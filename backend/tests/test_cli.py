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
