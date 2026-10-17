"""Integration tests: real MCP client <-> real MCP server subprocess (stdio) <-> PostgreSQL."""

import os

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.mcp_client import EXPECTED_TOOLS, MODEL_VISIBLE_TOOLS, McpToolClient, McpUnavailableError
from app.models import ServiceRequest

pytestmark = pytest.mark.needs_db


@pytest.fixture
def db_url() -> str:
    return os.environ["DATABASE_URL"]


async def test_client_discovers_tools_over_mcp_protocol(db, db_url):
    async with McpToolClient(db_url) as client:
        assert client.connected
        assert set(client.tools) == EXPECTED_TOOLS


async def test_model_only_sees_allowlisted_tools(db, db_url):
    async with McpToolClient(db_url) as client:
        names = {spec["function"]["name"] for spec in client.openai_tool_specs()}
        assert names == MODEL_VISIBLE_TOOLS
        assert "create_service_request" not in names  # app-controlled, behind confirmation
        assert not any("status" in n and "update" in n for n in client.tools)  # no staff tool exists
        for spec in client.openai_tool_specs():
            assert spec["function"]["parameters"]["type"] == "object"


async def test_search_tools_return_grounded_results_with_sources(db, db_url):
    async with McpToolClient(db_url) as client:
        r = await client.call_tool("search_services", {"query": "upholstery repair"})
        assert r["ok"] and r["matches"][0]["source_url"] == "https://sportfitkw.com/service.html"

        r = await client.call_tool("search_knowledge_base", {"query": "warranty", "category": "faq"})
        assert r["ok"] and r["matches"][0]["id"] == "faq-warranty"

        r = await client.call_tool("search_services", {"query": "quantum teleportation lessons"})
        assert r["ok"] and r["matches"] == [] and "No verified" in r["message"]


async def test_tool_argument_validation(db, db_url):
    async with McpToolClient(db_url) as client:
        r = await client.call_tool("search_services", {"query": "   "})
        assert not r["ok"] and r["error"] == "invalid_query"
        r = await client.call_tool("search_knowledge_base", {"query": "x" * 500})
        assert not r["ok"] and r["error"] == "invalid_query"
        r = await client.call_tool("search_knowledge_base", {"query": "repair", "category": "secrets"})
        assert not r["ok"] and r["error"] == "invalid_category"


async def test_create_then_get_roundtrip_is_persisted_and_private_fields_hidden(db, db_url, valid_payload):
    async with McpToolClient(db_url) as client:
        created = await client.call_tool("create_service_request", valid_payload)
        assert created["ok"] and created["demo"] is True
        ref = created["request_reference"]

        # durable in PostgreSQL, readable from an independent session
        with session_scope() as s:
            row = s.scalar(select(ServiceRequest).where(ServiceRequest.request_reference == ref))
            assert row is not None and row.email == "customer@example.com"

        got = await client.call_tool("get_service_request", {"request_reference": ref})
        assert got["ok"] and got["found"] and got["request"]["status"] == "received"
        blob = str(got)
        for private in ("customer@example.com", "+965", "SN-12345", "Test Customer", "belt stops"):
            assert private not in blob


async def test_create_rejects_invalid_details_and_persists_nothing(db, db_url, valid_payload):
    async with McpToolClient(db_url) as client:
        valid_payload["email"] = "nope"
        valid_payload["problem_description"] = "short"
        r = await client.call_tool("create_service_request", valid_payload)
        assert not r["ok"] and r["error"] == "validation_error"
        assert {d["field"] for d in r["details"]} >= {"email", "problem_description"}
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(ServiceRequest)) == 0


async def test_unknown_reference_is_safe_not_found(db, db_url):
    async with McpToolClient(db_url) as client:
        r = await client.call_tool("get_service_request", {"request_reference": "SFD-AAAA-AAAA-AAAA"})
        assert r["ok"] and r["found"] is False
        r = await client.call_tool("get_service_request", {"request_reference": "x'; DROP TABLE service_requests;--"})
        assert r["ok"] and r["found"] is False


async def test_handoff_guidance_has_no_fake_integration(db, db_url):
    async with McpToolClient(db_url) as client:
        r = await client.call_tool("get_handoff_guidance")
        assert r["ok"] and "cannot place calls" in r["entries"][0]["content"]
        assert "No live handoff" in r["note"]


async def test_unknown_tool_and_disconnected_client_raise_cleanly(db, db_url):
    client = McpToolClient(db_url)
    with pytest.raises(McpUnavailableError):
        await client.call_tool("search_services", {"query": "repair"})  # not started
    async with client:
        with pytest.raises(McpUnavailableError):
            await client.call_tool("update_status", {"status": "completed"})  # no such tool
