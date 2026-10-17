"""MCP client: starts the MCP server as a stdio subprocess and talks to it over the MCP protocol."""

import asyncio
import json
import logging
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Tools the language model may be offered. create_service_request is intentionally absent:
# the application calls it itself, after explicit customer confirmation.
MODEL_VISIBLE_TOOLS = frozenset(
    {"search_services", "search_knowledge_base", "get_service_request", "get_handoff_guidance"}
)
EXPECTED_TOOLS = MODEL_VISIBLE_TOOLS | {"create_service_request"}


class McpUnavailableError(RuntimeError):
    """The MCP server process is not running or did not answer in time."""


class McpToolNotFoundError(McpUnavailableError):
    """The requested tool does not exist on the server (a caller bug, not a process failure)."""


class McpToolClient:
    def __init__(self, database_url: str, timeout: float = 15.0) -> None:
        self._database_url = database_url
        self._timeout = timeout
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self.tools: dict[str, Any] = {}

    async def start(self) -> None:
        # Least privilege: the subprocess gets the database URL and nothing else. In particular
        # it never sees HF_TOKEN or staff secrets.
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env={"DATABASE_URL": self._database_url, "PYTHONPATH": str(BACKEND_DIR), "PYTHONUNBUFFERED": "1"},
            cwd=str(BACKEND_DIR),
        )
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=self._timeout)
            listed = await asyncio.wait_for(session.list_tools(), timeout=self._timeout)
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._session = stack, session
        self.tools = {t.name: t for t in listed.tools}
        missing = EXPECTED_TOOLS - set(self.tools)
        if missing:
            await self.stop()
            raise McpUnavailableError(f"MCP server is missing tools: {sorted(missing)}")

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = self._session = None

    async def __aenter__(self) -> "McpToolClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    @property
    def connected(self) -> bool:
        return self._session is not None

    def openai_tool_specs(self) -> list[dict[str, Any]]:
        """Tool definitions discovered over MCP, converted to the OpenAI-compatible format, filtered
        to the allowlist of model-visible tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema,
                },
            }
            for name, t in self.tools.items()
            if name in MODEL_VISIBLE_TOOLS
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call an MCP tool and return its structured result. Raises McpUnavailableError on
        process/transport failure or timeout; tool-level problems come back as ``{"ok": False}``."""
        if self._session is None:
            raise McpUnavailableError("MCP client is not connected")
        if name not in self.tools:
            raise McpToolNotFoundError(f"unknown tool: {name}")
        try:
            result = await asyncio.wait_for(self._session.call_tool(name, arguments or {}), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            raise McpUnavailableError(f"tool {name} timed out") from exc
        except Exception as exc:  # broken pipe, closed stream, protocol error
            raise McpUnavailableError(f"tool {name} failed: {type(exc).__name__}") from exc

        if result.isError:
            return {"ok": False, "error": "tool_error", "message": "the tool reported an error"}
        if result.structuredContent is not None:
            payload = result.structuredContent
            # FastMCP wraps non-object returns as {"result": ...}; ours are always objects
            return payload if isinstance(payload, dict) else {"ok": False, "error": "bad_output"}
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    break
                if isinstance(parsed, dict):
                    return parsed
        return {"ok": False, "error": "bad_output", "message": "the tool returned an unreadable result"}


log = logging.getLogger("sportfit.mcp_client")


class ManagedMcpClient:
    """Keeps one MCP server subprocess alive for the whole app and restarts it if it dies.

    anyio requires a stdio session to be opened and closed by the *same task*. Request handlers run
    in different tasks, so a single supervisor task owns the subprocess; handlers only call tools.
    """

    def __init__(self, database_url: str, timeout: float = 15.0, restart_backoff: float = 2.0) -> None:
        self._database_url = database_url
        self._timeout = timeout
        self._backoff = restart_backoff
        self._task: asyncio.Task[None] | None = None
        self._client: McpToolClient | None = None
        self._ready = asyncio.Event()
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._ready.is_set() and self._client is not None

    @property
    def tools(self) -> dict[str, Any]:
        return self._client.tools if self._client else {}

    def openai_tool_specs(self) -> list[dict[str, Any]]:
        return self._client.openai_tool_specs() if self._client else []

    async def start(self, wait: float = 20.0) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="mcp-supervisor")
        try:
            await asyncio.wait_for(self._ready.wait(), wait)
        except asyncio.TimeoutError as exc:
            raise McpUnavailableError("MCP server did not start in time") from exc

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task
            self._task = None

    def request_restart(self, failed: McpToolClient | None = None) -> None:
        if failed is not None and failed is not self._client:
            return  # someone already replaced it
        self._ready.clear()
        self._wake.set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            client = McpToolClient(self._database_url, self._timeout)
            try:
                await client.start()
            except Exception as exc:  # start-up failure: retry after a pause
                log.warning("MCP server failed to start: %s", type(exc).__name__)
                await self._sleep(self._backoff)
                continue
            self._client = client
            self._wake.clear()
            self._ready.set()
            await self._wake.wait()
            self._ready.clear()
            self._client = None
            try:
                await client.stop()
            except Exception:
                log.warning("MCP server did not shut down cleanly")
            if not self._stop.is_set():
                await self._sleep(self._backoff)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, retry: bool = True
    ) -> dict[str, Any]:
        """Call a tool. On a process failure the server is restarted; the call is retried once only
        if ``retry`` is true. Never retry non-idempotent calls (creating a request)."""
        if not self._ready.is_set():
            if self._task is None or self._stop.is_set():
                raise McpUnavailableError("MCP server is not running")
            try:  # a restart may be in progress: give it a few seconds
                await asyncio.wait_for(self._ready.wait(), 5.0)
            except asyncio.TimeoutError as exc:
                raise McpUnavailableError("MCP server is not running") from exc
        client = self._client
        if client is None:
            raise McpUnavailableError("MCP server is not running")
        try:
            return await client.call_tool(name, arguments)
        except McpToolNotFoundError:
            raise
        except McpUnavailableError:
            self.request_restart(client)
            if not retry:
                raise
        try:
            await asyncio.wait_for(self._ready.wait(), 10.0)
        except asyncio.TimeoutError as exc:
            raise McpUnavailableError("MCP server did not come back") from exc
        assert self._client is not None
        return await self._client.call_tool(name, arguments)
