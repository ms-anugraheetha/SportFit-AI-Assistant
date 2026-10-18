"""Test fixtures. Database tests need PostgreSQL: set TEST_DATABASE_URL to run them.

Example: TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/sportfit_test
The schema is created by running the real Alembic migrations, so the migrations are tested too.
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")

if TEST_DB_URL:
    # Must happen before any app module reads settings.
    os.environ["DATABASE_URL"] = TEST_DB_URL
    os.environ.setdefault("APP_ENV", "test")


def pytest_collection_modifyitems(config, items):
    skip_db = pytest.mark.skip(reason="TEST_DATABASE_URL not set (needs PostgreSQL)")
    skip_hf = pytest.mark.skip(reason="HF_TOKEN not set (needs real Hugging Face credentials)")
    for item in items:
        if "needs_db" in item.keywords and not TEST_DB_URL:
            item.add_marker(skip_db)
        if "needs_hf" in item.keywords and not os.environ.get("HF_TOKEN"):
            item.add_marker(skip_hf)


@pytest.fixture(scope="session")
def migrated_db():
    from alembic import command
    from alembic.config import Config

    from app.db import reset_engine_cache

    reset_engine_cache()
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.downgrade(cfg, "base")  # start from a clean schema
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture
def db(migrated_db):
    from app.db import get_engine

    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE service_requests, status_history, staff_sessions RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture(scope="module")
def client(migrated_db):
    """A running app (real MCP subprocess, real PostgreSQL, no language model)."""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        database_url=os.environ["DATABASE_URL"],
        rate_limit_chat_per_minute=10_000,
        rate_limit_tracking_per_minute=10_000,
        rate_limit_create_per_minute=10_000,
    )
    app = create_app(settings, llm=None)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def set_llm(client):
    """Swap the model used by the running app for the duration of one test."""
    orchestrator = client.app.state.orchestrator
    original = orchestrator.llm

    def _set(llm):
        orchestrator.llm = llm

    yield _set
    orchestrator.llm = original


@pytest.fixture
def mcp_calls(client, monkeypatch):
    """Record the names of every MCP tool call the app makes."""
    mcp = client.app.state.mcp
    real = mcp.call_tool
    calls: list[str] = []

    async def spy(name, arguments=None, retry=True):
        calls.append(name)
        return await real(name, arguments, retry)

    monkeypatch.setattr(mcp, "call_tool", spy)
    return calls


VALID_PAYLOAD = {
    "customer_name": "Test Customer",
    "email": "customer@example.com",
    "phone": "+965 5000 0000",
    "service_area": "Mahboula",
    "equipment_type": "Treadmill",
    "equipment_brand": "ExampleBrand",
    "equipment_model": "T-100",
    "serial_number": "SN-12345",
    "problem_description": "The belt stops moving after a few minutes of use.",
    "preferred_contact_method": "email",
    "preferred_appointment_time": "Weekday mornings",
}


@pytest.fixture
def valid_payload() -> dict:
    return dict(VALID_PAYLOAD)
