import re

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db import session_scope
from app.models import STATUSES, ServiceRequest

pytestmark = pytest.mark.needs_db

REF = re.compile(r"^SFD-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def count_requests() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(ServiceRequest))


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" and body["checks"] == {"database": True, "mcp": True}
    assert body["llm_configured"] is False  # reported as a boolean only, never the token


def test_create_requires_explicit_confirmed_true(client, db, valid_payload):
    assert client.post("/api/service-requests", json={"details": valid_payload}).status_code == 422
    assert client.post("/api/service-requests", json={"details": valid_payload, "confirmed": False}).status_code == 422
    assert client.post("/api/service-requests", json={"details": valid_payload, "confirmed": "yes"}).status_code == 422
    assert count_requests() == 0


def test_create_rejects_invalid_and_forbidden_fields(client, db, valid_payload):
    bad = dict(valid_payload, email="nope", problem_description="short")
    assert client.post("/api/service-requests", json={"details": bad, "confirmed": True}).status_code == 422
    sneaky = dict(valid_payload, status="completed")
    assert client.post("/api/service-requests", json={"details": sneaky, "confirmed": True}).status_code == 422
    assert count_requests() == 0


def test_create_then_track_returns_restricted_view(client, db, valid_payload):
    r = client.post("/api/service-requests", json={"details": valid_payload, "confirmed": True})
    assert r.status_code == 201
    body = r.json()
    assert REF.match(body["request_reference"]) and body["demo"] is True
    assert "not sent to SportFit" in body["message"]
    with session_scope() as s:
        assert s.scalar(select(ServiceRequest.request_reference)) == body["request_reference"]

    t = client.get(f"/api/service-requests/{body['request_reference']}")
    assert t.status_code == 200
    data = t.json()
    assert set(data) == {
        "request_reference", "status", "status_label", "equipment_type", "created_at", "updated_at", "demo_notice",
    }
    assert data["status"] == "received" and "Demonstration data only" in data["demo_notice"]
    blob = t.text
    for private in ("customer@example.com", "+965", "SN-12345", "Test Customer", "Mahboula", "belt stops"):
        assert private not in blob


def test_unknown_and_malformed_references_look_identical(client, db):
    a = client.get("/api/service-requests/SFD-AAAA-AAAA-AAAA")
    b = client.get("/api/service-requests/not-a-reference")
    c = client.get("/api/service-requests/x'%20OR%201=1--")
    assert a.status_code == b.status_code == c.status_code == 404
    assert a.json() == b.json() == c.json()


def test_meta_statuses_come_from_the_backend(client):
    meta = client.get("/api/meta").json()
    assert tuple(meta["statuses"]) == STATUSES
    assert set(meta["status_labels"]) == set(STATUSES)
    assert "independent academic prototype" in meta["disclaimer"]
    assert len(meta["suggested_prompts"]) == 5


def test_knowledge_endpoint_has_sources_and_limitations(client):
    data = client.get("/api/knowledge/services").json()
    assert data["retrieved"] == "2026-10-17"
    assert {e["id"] for e in data["entries"]} >= {"svc-equipment-repair", "svc-upholstery-repair"}
    assert all(e["source_url"].startswith("https://sportfitkw.com/") for e in data["entries"])
    assert any(e["id"] == "lim-spare-parts" for e in data["limitations"])


def test_security_headers_and_no_store(client):
    r = client.get("/api/meta")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


def test_rate_limiting_returns_429(migrated_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = Settings(
        database_url=__import__("os").environ["DATABASE_URL"],
        rate_limit_tracking_per_minute=3,
    )
    with TestClient(create_app(settings, llm=None, start_mcp=False)) as c:
        codes = [c.get("/api/service-requests/SFD-AAAA-AAAA-AAAA").status_code for _ in range(5)]
    assert codes == [404, 404, 404, 429, 429]


def test_forwarded_header_ignored_unless_proxy_trusted(migrated_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = __import__("os").environ["DATABASE_URL"]
    ref = "/api/service-requests/SFD-AAAA-AAAA-AAAA"

    spoof = lambda i: {"X-Forwarded-For": f"10.0.0.{i}"}  # noqa: E731
    with TestClient(create_app(Settings(database_url=url, rate_limit_tracking_per_minute=2), llm=None, start_mcp=False)) as c:
        untrusted = [c.get(ref, headers=spoof(i)).status_code for i in range(4)]
    assert untrusted == [404, 404, 429, 429]  # rotating the header does not evade the limit

    with TestClient(
        create_app(Settings(database_url=url, rate_limit_tracking_per_minute=2, trust_proxy_headers=True), llm=None, start_mcp=False)
    ) as c:
        trusted = [c.get(ref, headers=spoof(i)).status_code for i in range(4)]
    assert trusted == [404, 404, 404, 404]  # behind a trusted proxy each real client is counted separately
