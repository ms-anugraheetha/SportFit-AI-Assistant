import subprocess
import sys
from pathlib import Path

from argon2 import PasswordHasher

from app.auth import hash_password

BACKEND = Path(__file__).resolve().parent.parent


def test_hash_password_produces_verifiable_argon2():
    h = hash_password("a long enough password")
    assert h.startswith("$argon2id$")
    assert PasswordHasher().verify(h, "a long enough password")
    assert hash_password("a long enough password") != h  # salted


def test_gen_secret_is_long_and_random():
    out = [subprocess.run([sys.executable, "-m", "app.cli", "gen-secret"], cwd=BACKEND, capture_output=True, text=True).stdout.strip() for _ in range(2)]
    assert all(len(o) >= 48 for o in out) and out[0] != out[1]


import os

import pytest


@pytest.mark.needs_db
def test_clear_demo_data_requires_flag_and_clears(migrated_db):
    from sqlalchemy import text

    from app.db import get_engine, session_scope
    from app.schemas import ServiceRequestCreate
    from app import services
    from tests.conftest import VALID_PAYLOAD

    with session_scope() as s:
        services.create_service_request(s, ServiceRequestCreate(**VALID_PAYLOAD))
    env = dict(os.environ)
    refused = subprocess.run([sys.executable, "-m", "app.cli", "clear-demo-data"], cwd=BACKEND, capture_output=True, text=True, env=env)
    assert refused.returncode == 1
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM service_requests")).scalar() == 1
    done = subprocess.run([sys.executable, "-m", "app.cli", "clear-demo-data", "--yes"], cwd=BACKEND, capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM service_requests")).scalar() == 0
