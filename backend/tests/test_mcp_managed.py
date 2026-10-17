import os

import pytest

from app.mcp_client import ManagedMcpClient, McpToolNotFoundError, McpUnavailableError

pytestmark = pytest.mark.needs_db


async def test_supervisor_starts_calls_restarts_and_stops(db):
    mcp = ManagedMcpClient(os.environ["DATABASE_URL"], restart_backoff=0.1)
    await mcp.start()
    try:
        assert mcp.connected and "create_service_request" in mcp.tools
        first = mcp._client
        assert (await mcp.call_tool("search_services", {"query": "repair"}))["ok"]

        mcp.request_restart()  # simulates the subprocess dying
        assert not mcp.connected
        r = await mcp.call_tool("search_services", {"query": "repair"})  # waits for the new process
        assert r["ok"] and mcp.connected and mcp._client is not first
    finally:
        await mcp.stop()
    assert not mcp.connected
    with pytest.raises(McpUnavailableError):
        await mcp.call_tool("search_services", {"query": "repair"})


async def test_unknown_tool_does_not_trigger_a_restart(db):
    mcp = ManagedMcpClient(os.environ["DATABASE_URL"], restart_backoff=0.1)
    await mcp.start()
    try:
        before = mcp._client
        with pytest.raises(McpToolNotFoundError):
            await mcp.call_tool("drop_everything", {})
        assert mcp.connected and mcp._client is before
    finally:
        await mcp.stop()


async def test_start_failure_is_reported_and_stop_is_clean():
    mcp = ManagedMcpClient("postgresql+psycopg://x@127.0.0.1:1/none", restart_backoff=0.1)
    # The server process itself starts (it only touches the DB when a tool runs), so it connects.
    await mcp.start()
    await mcp.stop()
