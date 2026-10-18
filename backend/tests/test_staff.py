"""Staff authentication, authorization and dashboard API (real PostgreSQL, real migrations)."""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import hash_password
from app.config import Settings
from app.db import get_engine, session_scope
from app.main import create_app

pytestmark = pytest.mark.needs_db

PASSWORD = "correct horse battery staple"
SECRET = "x" * 48


def make_settings(**over) -> Settings:
    base = dict(
        database_url=os.environ.get("DATABASE_URL", ""),
        staff_username="staff",
        staff_password_hash=hash_password(PASSWORD),
        session_secret=SECRET,
        rate_limit_chat_per_minute=10_000,
        rate_limit_tracking_per_minute=10_000,
        rate_limit_create_per_minute=10_000,
        rate_limit_login_per_minute=10_000,
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture(scope="module")
def staff_client(migrated_db):
    with TestClient(create_app(make_settings(), llm=None, start_mcp=False)) as c:
        yield c


@pytest.fixture
def clean(migrated_db):
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE service_requests, status_history, staff_sessions RESTART IDENTITY CASCADE"))


def seed(n=3, **over):
    from app import services
    from app.schemas import ServiceRequestCreate
    from tests.conftest import VALID_PAYLOAD

    refs = []
    with session_scope() as s:
        for i in range(n):
            payload = dict(VALID_PAYLOAD, customer_name=f"Customer {i}", **over)
            refs.append(services.create_service_request(s, ServiceRequestCreate(**payload)))
    return refs


def login(client, password=PASSWORD, username="staff"):
    return client.post("/api/staff/login", json={"username": username, "password": password})


def authed(client):
    """Fresh logged-in client state; returns CSRF headers."""
    client.cookies.clear()
    r = login(client)
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


# ----------------------------------------------------------------------------- login


def test_login_success_sets_hardened_cookie(staff_client, clean):
    staff_client.cookies.clear()
    r = login(staff_client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "sf_staff=" in cookie
    assert "password" not in r.text.lower()
    assert staff_client.get("/api/staff/me").json()["username"] == "staff"


def test_login_rejects_bad_password_and_bad_user_identically(staff_client, clean):
    staff_client.cookies.clear()
    a = login(staff_client, password="wrong password!!")
    b = login(staff_client, username="nobody")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()
    assert "set-cookie" not in a.headers


def test_login_rejects_extra_fields_and_empty_values(staff_client):
    assert staff_client.post("/api/staff/login", json={"username": "staff", "password": "x", "admin": True}).status_code == 422
    assert staff_client.post("/api/staff/login", json={"username": "", "password": ""}).status_code == 422


def test_cookie_is_secure_in_production(migrated_db, clean):
    app = create_app(make_settings(app_env="production"), llm=None, start_mcp=False)
    with TestClient(app) as c:
        assert "secure" in login(c).headers["set-cookie"].lower()


def test_login_disabled_when_not_configured(migrated_db):
    for over in ({"staff_password_hash": ""}, {"session_secret": "short"}):
        with TestClient(create_app(make_settings(**over), llm=None, start_mcp=False)) as c:
            assert login(c).status_code == 503


def test_login_is_rate_limited(migrated_db, clean):
    with TestClient(create_app(make_settings(rate_limit_login_per_minute=3), llm=None, start_mcp=False)) as c:
        codes = [login(c, password="bad bad bad bad").status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_malformed_hash_in_config_never_logs_in(migrated_db):
    s = make_settings(staff_password_hash="$argon2id$garbage")
    with TestClient(create_app(s, llm=None, start_mcp=False)) as c:
        assert login(c).status_code == 401


# ----------------------------------------------------------------------------- 401 / 403


PROTECTED_GET = [
    "/api/staff/me",
    "/api/staff/summary",
    "/api/staff/service-requests",
    "/api/staff/service-requests/SFD-AAAA-BBBB-CCCC",
]


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_staff_get_endpoints_require_login(staff_client, path):
    staff_client.cookies.clear()
    assert staff_client.get(path).status_code == 401


def test_status_update_and_logout_require_login(staff_client):
    staff_client.cookies.clear()
    assert staff_client.patch("/api/staff/service-requests/SFD-AAAA-BBBB-CCCC/status", json={"status": "completed"}).status_code == 401
    assert staff_client.post("/api/staff/logout").status_code == 401


def test_forged_and_tampered_cookies_are_rejected(staff_client, clean):
    staff_client.cookies.clear()
    staff_client.cookies.set("sf_staff", "a" * 43)
    assert staff_client.get("/api/staff/me").status_code == 401
    authed(staff_client)
    real = staff_client.cookies.get("sf_staff")
    staff_client.cookies.set("sf_staff", real[:-1] + ("A" if real[-1] != "A" else "B"))
    assert staff_client.get("/api/staff/me").status_code == 401


def test_csrf_required_for_mutations(staff_client, clean):
    (ref,) = seed(1)
    headers = authed(staff_client)
    path = f"/api/staff/service-requests/{ref}/status"
    assert staff_client.patch(path, json={"status": "in_progress"}).status_code == 403
    assert staff_client.patch(path, json={"status": "in_progress"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert staff_client.post("/api/staff/logout").status_code == 403
    assert staff_client.get(f"/api/staff/service-requests/{ref}").json()["status"] == "received"  # unchanged
    assert staff_client.patch(path, json={"status": "in_progress"}, headers=headers).status_code == 200


def test_csrf_token_from_another_session_is_rejected(staff_client, clean):
    (ref,) = seed(1)
    other = authed(staff_client)
    authed(staff_client)  # new session replaces the cookie
    r = staff_client.patch(f"/api/staff/service-requests/{ref}/status", json={"status": "completed"}, headers=other)
    assert r.status_code == 403


# ----------------------------------------------------------------------------- logout / expiry


def test_logout_invalidates_session_server_side(staff_client, clean):
    headers = authed(staff_client)
    stolen = staff_client.cookies.get("sf_staff")
    assert staff_client.post("/api/staff/logout", headers=headers).status_code == 204
    staff_client.cookies.clear()
    staff_client.cookies.set("sf_staff", stolen)  # replay the old cookie
    assert staff_client.get("/api/staff/me").status_code == 401
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM staff_sessions")).scalar() == 0


def test_expired_session_is_rejected_and_removed(staff_client, clean):
    authed(staff_client)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE staff_sessions SET expires_at = now() - interval '1 minute'"))
    assert staff_client.get("/api/staff/me").status_code == 401
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM staff_sessions")).scalar() == 0


def test_session_token_is_not_stored_in_plaintext(staff_client, clean):
    authed(staff_client)
    token = staff_client.cookies.get("sf_staff")
    with get_engine().connect() as conn:
        stored = conn.execute(text("SELECT token_hash FROM staff_sessions")).scalar()
    assert stored != token and token not in stored


# ----------------------------------------------------------------------------- dashboard data


def test_list_search_filter_sort_paginate(staff_client, clean):
    refs = seed(5)
    seed(2, equipment_type="Rowing Machine", problem_description="Chain slips under load 100%_wow")
    headers = authed(staff_client)
    staff_client.patch(f"/api/staff/service-requests/{refs[0]}/status", json={"status": "completed"}, headers=headers)

    page = staff_client.get("/api/staff/service-requests", params={"page_size": 3}).json()
    assert page["total"] == 7 and page["total_pages"] == 3 and len(page["items"]) == 3
    assert set(page["items"][0]) == {"request_reference", "created_at", "customer_name", "equipment_type", "problem_summary", "status"}

    only_done = staff_client.get("/api/staff/service-requests", params={"status": "completed"}).json()
    assert only_done["total"] == 1 and only_done["items"][0]["request_reference"] == refs[0]

    rowing = staff_client.get("/api/staff/service-requests", params={"q": "rowing"}).json()
    assert rowing["total"] == 2
    by_ref = staff_client.get("/api/staff/service-requests", params={"q": refs[1].lower()}).json()
    assert [i["request_reference"] for i in by_ref["items"]] == [refs[1]]

    # LIKE wildcards are literal: '%_' matches only the one text containing it, '%' alone does not match all
    assert staff_client.get("/api/staff/service-requests", params={"q": "100%_wow"}).json()["total"] == 2
    assert staff_client.get("/api/staff/service-requests", params={"q": "%zzz"}).json()["total"] == 0

    asc = staff_client.get("/api/staff/service-requests", params={"sort": "customer_name", "order": "asc"}).json()
    names = [i["customer_name"] for i in asc["items"]]
    assert names == sorted(names)

    seen = set()
    for p in (1, 2, 3):
        seen |= {i["request_reference"] for i in staff_client.get("/api/staff/service-requests", params={"page_size": 3, "page": p}).json()["items"]}
    assert len(seen) == 7  # pagination neither loses nor repeats rows

    empty = staff_client.get("/api/staff/service-requests", params={"q": "no such thing"}).json()
    assert empty["total"] == 0 and empty["items"] == [] and empty["total_pages"] == 1


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "email; DROP TABLE service_requests"},
        {"sort": "password"},
        {"order": "sideways"},
        {"status": "hacked"},
        {"page": 0},
        {"page_size": 500},
        {"q": "x" * 101},
    ],
)
def test_list_rejects_bad_parameters(staff_client, clean, params):
    authed(staff_client)
    assert staff_client.get("/api/staff/service-requests", params=params).status_code == 422
    with get_engine().connect() as conn:  # table still there
        conn.execute(text("SELECT 1 FROM service_requests"))


def test_sql_injection_in_search_is_inert(staff_client, clean):
    seed(2)
    authed(staff_client)
    r = staff_client.get("/api/staff/service-requests", params={"q": "'; DROP TABLE service_requests; --"})
    assert r.status_code == 200 and r.json()["total"] == 0
    assert staff_client.get("/api/staff/summary").json()["total"] == 2


def test_summary_counts(staff_client, clean):
    a, b, c = seed(3)
    headers = authed(staff_client)
    staff_client.patch(f"/api/staff/service-requests/{a}/status", json={"status": "completed"}, headers=headers)
    staff_client.patch(f"/api/staff/service-requests/{b}/status", json={"status": "in_progress"}, headers=headers)
    s = staff_client.get("/api/staff/summary").json()
    assert s["total"] == 3
    assert s["counts"] == {"received": 1, "under_review": 0, "in_progress": 1, "completed": 1, "cancelled": 0}


def test_detail_has_full_data_and_history(staff_client, clean):
    (ref,) = seed(1)
    headers = authed(staff_client)
    staff_client.patch(f"/api/staff/service-requests/{ref}/status", json={"status": "under_review"}, headers=headers)
    staff_client.patch(f"/api/staff/service-requests/{ref}/status", json={"status": "in_progress"}, headers=headers)
    d = staff_client.get(f"/api/staff/service-requests/{ref.lower()}").json()  # lower case is normalised
    assert d["email"] == "customer@example.com" and d["serial_number"] == "SN-12345"
    assert [(h["old_status"], h["new_status"], h["changed_by"]) for h in d["history"]] == [
        ("received", "under_review", "staff"),
        ("under_review", "in_progress", "staff"),
    ]
    assert "id" not in d


def test_unknown_and_malformed_references_return_404(staff_client, clean):
    headers = authed(staff_client)
    for ref in ("SFD-AAAA-BBBB-CCCC", "not-a-reference", "1"):
        assert staff_client.get(f"/api/staff/service-requests/{ref}").status_code == 404
        assert staff_client.patch(f"/api/staff/service-requests/{ref}/status", json={"status": "completed"}, headers=headers).status_code == 404


def test_status_update_validation_and_no_other_fields_change(staff_client, clean):
    (ref,) = seed(1)
    headers = authed(staff_client)
    path = f"/api/staff/service-requests/{ref}/status"
    before = staff_client.get(f"/api/staff/service-requests/{ref}").json()
    assert staff_client.patch(path, json={"status": "bogus"}, headers=headers).status_code == 422
    assert staff_client.patch(path, json={"status": "completed", "email": "evil@example.com"}, headers=headers).status_code == 422
    assert staff_client.patch(path, json={}, headers=headers).status_code == 422
    after = staff_client.get(f"/api/staff/service-requests/{ref}").json()
    assert after == before
    ok = staff_client.patch(path, json={"status": "completed"}, headers=headers).json()
    assert ok["status"] == "completed" and ok["email"] == before["email"] and ok["customer_name"] == before["customer_name"]


def test_same_status_update_adds_no_history(staff_client, clean):
    (ref,) = seed(1)
    headers = authed(staff_client)
    r = staff_client.patch(f"/api/staff/service-requests/{ref}/status", json={"status": "received"}, headers=headers)
    assert r.status_code == 200 and r.json()["history"] == []


def test_customer_tracking_reflects_staff_update_without_private_data(staff_client, clean):
    (ref,) = seed(1)
    headers = authed(staff_client)
    staff_client.patch(f"/api/staff/service-requests/{ref}/status", json={"status": "completed"}, headers=headers)
    public = TestClient(staff_client.app)  # no staff cookie
    r = public.get(f"/api/service-requests/{ref}")
    assert r.json()["status"] == "completed"
    assert "email" not in r.text and "customer@example.com" not in r.text
    assert public.get("/api/staff/service-requests").status_code == 401


def test_staff_responses_are_not_cacheable(staff_client, clean):
    authed(staff_client)
    assert staff_client.get("/api/staff/summary").headers["cache-control"] == "no-store"
    staff_client.cookies.clear()
    assert staff_client.get("/api/staff/summary").headers["cache-control"] == "no-store"


def test_chat_api_cannot_reach_staff_data(client, clean):
    """The customer chat has no path to staff data: a status-change request in chat is refused honestly."""
    (ref,) = seed(1)
    r = client.post("/api/chat", json={"message": f"Mark request {ref} as completed"}).json()
    assert "completed" not in r["reply"].lower() or "cannot" in r["reply"].lower() or "can't" in r["reply"].lower()
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT status FROM service_requests")).scalar() == "received"
