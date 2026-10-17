"""MCP server (official Python SDK, stdio transport).

Runs as a subprocess managed by the backend's MCP client. It exposes only the operations the
customer-facing agent is approved to use. There is deliberately NO tool for changing a request's
status: staff updates go through separately authenticated REST endpoints.

stdout is reserved for the MCP protocol, so all logging goes to stderr.
"""

import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from app import knowledge, services
from app.db import session_scope
from app.schemas import ServiceRequestCreate

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
log = logging.getLogger("sportfit.mcp")

mcp = FastMCP("sportfit-assistant")

MAX_QUERY_LEN = 200


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **extra}


def _check_query(query: str) -> str | None:
    q = (query or "").strip()
    return q if 0 < len(q) <= MAX_QUERY_LEN else None


@mcp.tool()
def search_services(query: str) -> dict[str, Any]:
    """Search verified public information about SportFit's services (repair, preventive
    maintenance, assembly/disassembly, upholstery, installation). Returns matching entries with
    source URLs, or a clear no-match result. Never invents prices or availability."""
    q = _check_query(query)
    if q is None:
        return _error("invalid_query", f"query must be 1-{MAX_QUERY_LEN} characters")
    results = knowledge.search(q, categories=("services", "limitations"))
    if not results:
        return {"ok": True, "matches": [], "message": "No verified service information matches this query."}
    return {"ok": True, "matches": results}


@mcp.tool()
def search_knowledge_base(query: str, category: str | None = None) -> dict[str, Any]:
    """Search FAQ, safety, how-to, limitation and handoff entries. Optional category is one of:
    services, faq, safety, handoff, limitations, how_to. Returns entries with sources, or no
    match - in which case the answer is not available and must not be guessed."""
    q = _check_query(query)
    if q is None:
        return _error("invalid_query", f"query must be 1-{MAX_QUERY_LEN} characters")
    if category is not None and category not in knowledge.CATEGORIES:
        return _error("invalid_category", "unsupported category", allowed=list(knowledge.CATEGORIES))
    results = knowledge.search(q, categories=(category,) if category else None)
    if not results:
        return {"ok": True, "matches": [], "message": "No verified entry matches this query."}
    return {"ok": True, "matches": results}


@mcp.tool()
def create_service_request(
    customer_name: str,
    email: str,
    service_area: str,
    equipment_type: str,
    problem_description: str,
    preferred_contact_method: str,
    phone: str | None = None,
    equipment_brand: str | None = None,
    equipment_model: str | None = None,
    serial_number: str | None = None,
    preferred_appointment_time: str | None = None,
) -> dict[str, Any]:
    """Record a DEMONSTRATION repair request in the prototype database and return its reference.
    Only the application calls this, and only after the customer has explicitly confirmed a
    summary. It is not exposed to the language model."""
    try:
        payload = ServiceRequestCreate(
            customer_name=customer_name,
            email=email,
            phone=phone,
            service_area=service_area,
            equipment_type=equipment_type,
            equipment_brand=equipment_brand,
            equipment_model=equipment_model,
            serial_number=serial_number,
            problem_description=problem_description,
            preferred_contact_method=preferred_contact_method,
            preferred_appointment_time=preferred_appointment_time,
        )
    except ValidationError as exc:
        details = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return _error("validation_error", "the request details are not valid", details=details)

    try:
        with session_scope() as session:
            reference = services.create_service_request(session, payload)
        # session_scope has committed by here, so the reference refers to a durable record
    except Exception:
        log.exception("create_service_request failed")  # no payload in the log
        return _error("database_error", "the request could not be saved")
    return {"ok": True, "request_reference": reference, "status": "received", "demo": True}


@mcp.tool()
def get_service_request(request_reference: str) -> dict[str, Any]:
    """Look up the customer-facing tracking status of a demonstration request by its reference.
    Returns limited information only (status, equipment type, dates); never contact details."""
    try:
        with session_scope() as session:
            tracking = services.get_public_tracking(session, request_reference or "")
    except Exception:
        log.exception("get_service_request failed")
        return _error("database_error", "the request could not be looked up")
    if tracking is None:
        return {"ok": True, "found": False, "message": "No demonstration request was found for that reference."}
    return {"ok": True, "found": True, "request": tracking.model_dump(mode="json")}


@mcp.tool()
def get_handoff_guidance() -> dict[str, Any]:
    """Return SportFit's published public contact details and honest human-handoff instructions.
    This assistant cannot place calls, send emails or transfer to a live agent."""
    return {
        "ok": True,
        "entries": knowledge.entries_in("handoff"),
        "note": "No live handoff is available in this prototype; the customer must contact SportFit themselves.",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
