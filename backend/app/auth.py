"""Staff authentication: argon2 password check, server-side sessions, CSRF.

Design notes (explainable in class):
- One staff account, configured by environment variables. The password is never stored or logged; only an
  argon2 hash is configured.
- The browser holds an opaque random token in an HttpOnly cookie. The database stores only an HMAC of it
  (keyed with SESSION_SECRET), so logout and expiry are enforced on the server.
- State-changing staff requests must also send the per-session CSRF token in an X-CSRF-Token header.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from sqlalchemy import delete, select

from app.config import Settings
from app.db import session_scope
from app.models import StaffSession

log = logging.getLogger("sportfit.auth")

COOKIE_NAME = "sf_staff"
MIN_SECRET_LENGTH = 32

_hasher = PasswordHasher()
# Verified against when the username is wrong, so wrong-user and wrong-password take similar time.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def staff_login_enabled(settings: Settings) -> bool:
    return (
        settings.staff_password_hash.startswith("$argon2")
        and len(settings.session_secret) >= MIN_SECRET_LENGTH
        and bool(settings.staff_username)
    )


def check_credentials(settings: Settings, username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username.encode(), settings.staff_username.encode())
    target = settings.staff_password_hash if user_ok else _DUMMY_HASH
    try:
        pw_ok = _hasher.verify(target, password)
    except (VerificationError, InvalidHashError):
        pw_ok = False
    return user_ok and pw_ok


def _token_hash(settings: Settings, token: str) -> str:
    return hmac.new(settings.session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StaffContext:
    username: str
    csrf_token: str
    expires_at: datetime


def create_session(settings: Settings, username: str) -> tuple[str, StaffContext]:
    """Returns (cookie token, context). Expired sessions are purged on every login."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires = _now() + timedelta(hours=settings.staff_session_hours)
    with session_scope() as s:
        s.execute(delete(StaffSession).where(StaffSession.expires_at < _now()))
        s.add(
            StaffSession(
                token_hash=_token_hash(settings, token), csrf_token=csrf, username=username, expires_at=expires
            )
        )
    return token, StaffContext(username, csrf, expires)


def load_session(settings: Settings, token: str | None) -> StaffContext | None:
    if not token or not staff_login_enabled(settings):
        return None
    with session_scope() as s:
        row = s.scalar(select(StaffSession).where(StaffSession.token_hash == _token_hash(settings, token)))
        if row is None:
            return None
        if row.expires_at <= _now():
            s.delete(row)
            return None
        return StaffContext(row.username, row.csrf_token, row.expires_at)


def destroy_session(settings: Settings, token: str | None) -> None:
    if not token or not settings.session_secret:
        return
    with session_scope() as s:
        s.execute(delete(StaffSession).where(StaffSession.token_hash == _token_hash(settings, token)))


# ---- FastAPI dependencies


def require_staff(request: Request) -> StaffContext:
    """401 unless the request carries a valid, unexpired staff session."""
    ctx = load_session(request.app.state.settings, request.cookies.get(COOKIE_NAME))
    if ctx is None:
        raise HTTPException(status_code=401, detail="Staff login required.", headers={"Cache-Control": "no-store"})
    request.state.staff = ctx
    return ctx


def require_staff_csrf(request: Request) -> StaffContext:
    """For POST/PATCH: valid session (401) AND matching CSRF header (403)."""
    ctx = require_staff(request)
    sent = request.headers.get("x-csrf-token", "")
    if not sent or not hmac.compare_digest(sent.encode(), ctx.csrf_token.encode()):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")
    return ctx
