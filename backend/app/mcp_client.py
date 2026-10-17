"""MCP client: starts the MCP server as a stdio subprocess and talks to it over the MCP protocol."""

import asyncio
import json
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
            raise McpUnavailableError(f"unknown tool: {name}")
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
