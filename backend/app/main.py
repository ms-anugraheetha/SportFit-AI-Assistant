"""FastAPI application: public API (chat, requests, tracking, knowledge, health).

Staff endpoints live in staff_api.py (session + CSRF protected). Nothing here lets the model touch the
database directly: writes go through deterministic service functions or the allowlisted MCP tools.
"""

import logging
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from app import knowledge, services
from app.config import Settings, get_settings
from app.conversation import ConversationStore
from app.db import session_scope
from app.llm import ChatLLM, HFChatClient
from app.mcp_client import ManagedMcpClient, McpUnavailableError
from app.models import STATUSES
from app.orchestrator import ChatOrchestrator
from app.staff_api import register_staff_routes
from app.static_site import register_static_site
from app.schemas import STATUS_LABELS, PublicTracking, ServiceRequestCreate

log = logging.getLogger("sportfit.api")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DISCLAIMER = (
    "SportFit AI Assistant is an independent academic prototype. It is not affiliated with, endorsed by, "
    "or connected to SportFit, and requests created here are demonstration data only."
)


# ----------------------------------------------------------------------------- schemas


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def _clean_message(cls, v: str) -> str:
        v = _CONTROL_CHARS.sub("", v).strip()
        if not v:
            raise ValueError("message must not be empty")
        return v


class SourceRef(BaseModel):
    title: str
    url: str


class ChatResponse(BaseModel):
    conversation_id: str
    state: str
    reply: str
    suggested_actions: list[str] = []
    request_reference: str | None = None
    sources: list[SourceRef] = []
    safety_notice: list[str] = []
    degraded: bool = False


class CreateRequestBody(BaseModel):
    """Direct REST creation. ``confirmed`` must be literally true: the client asserts it showed the
    customer a summary and got explicit confirmation."""

    model_config = ConfigDict(extra="forbid")

    details: ServiceRequestCreate
    confirmed: Literal[True]


class CreatedResponse(BaseModel):
    request_reference: str
    status: str
    demo: bool = True
    message: str


# ----------------------------------------------------------------------------- abuse protection


class RateLimiter:
    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self._hits) > 5000:
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                del self._hits[k]
        return True

    def reset(self) -> None:
        self._hits.clear()


class PayloadTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Reject request bodies over a limit, including chunked uploads that send no Content-Length."""

    def __init__(self, app: Any, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope["headers"]).get(b"content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Any:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise PayloadTooLarge()
            return message

        await self.app(scope, limited_receive, send)


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def limited(name: str) -> Any:
    def dependency(request: Request) -> None:
        limiter: RateLimiter = request.app.state.limiters[name]
        settings: Settings = request.app.state.settings
        if not limiter.allow(client_ip(request, settings.trust_proxy_headers)):
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down.", headers={"Retry-After": "60"})

    return dependency


# ----------------------------------------------------------------------------- app factory


def create_app(
    settings: Settings | None = None,
    llm: ChatLLM | None | Literal["auto"] = "auto",
    start_mcp: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    if llm == "auto":
        llm = (
            HFChatClient(settings.hf_token, settings.hf_model, settings.hf_base_url, settings.llm_timeout_seconds)
            if settings.hf_token
            else None
        )

    mcp = ManagedMcpClient(settings.database_url, settings.tool_timeout_seconds)
    store = ConversationStore(settings.conversation_ttl_seconds)
    orchestrator = ChatOrchestrator(mcp, llm, store, settings.max_tool_iterations)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_mcp and settings.database_url:
            try:
                await mcp.start()
            except McpUnavailableError:
                log.warning("MCP server not ready at startup; the supervisor keeps retrying")
        yield
        await mcp.stop()

    app = FastAPI(
        title="SportFit AI Assistant (independent academic prototype)",
        version="0.3.0",
        lifespan=lifespan,
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.mcp = mcp
    app.state.store = store
    app.state.orchestrator = orchestrator
    app.state.limiters = {
        "chat": RateLimiter(settings.rate_limit_chat_per_minute),
        "tracking": RateLimiter(settings.rate_limit_tracking_per_minute),
        "create": RateLimiter(settings.rate_limit_create_per_minute),
        "login": RateLimiter(settings.rate_limit_login_per_minute),
    }

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(PayloadTooLarge)
    async def _too_large(_: Request, __: PayloadTooLarge) -> JSONResponse:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Type and route only: never the request body, which may contain personal details.
        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    # ---- health

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        checks = {"database": False, "mcp": mcp.connected}
        try:
            with session_scope() as s:
                s.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:
            pass
        ok = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ok else "not_ready", "checks": checks, "llm_configured": llm is not None},
            status_code=200 if ok else 503,
        )

    # ---- public API

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        """Statuses and labels come from the backend so the UI never hardcodes them."""
        return {
            "statuses": list(STATUSES),
            "status_labels": STATUS_LABELS,
            "disclaimer": DISCLAIMER,
            "suggested_prompts": [
                "What repair services are available?",
                "I need help with a treadmill.",
                "Submit a repair request.",
                "Track my demo request.",
                "I have a safety concern.",
            ],
        }

    @app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(limited("chat"))])
    async def chat(body: ChatRequest) -> ChatResponse:
        result = await orchestrator.handle(body.conversation_id, body.message)
        return ChatResponse(
            conversation_id=result.conversation_id,
            state=result.state.value,
            reply=result.reply,
            suggested_actions=result.suggested_actions,
            request_reference=result.request_reference,
            sources=[SourceRef(**s) for s in result.sources],
            safety_notice=result.safety_notice,
            degraded=result.degraded,
        )

    @app.post(
        "/api/service-requests",
        response_model=CreatedResponse,
        status_code=201,
        dependencies=[Depends(limited("create"))],
    )
    def create_request(body: CreateRequestBody) -> CreatedResponse:
        with session_scope() as session:
            reference = services.create_service_request(session, body.details)
        # session_scope committed above, so the reference is only returned for a durable record
        return CreatedResponse(
            request_reference=reference,
            status="received",
            message="Recorded in the academic demonstration database only. It was not sent to SportFit.",
        )

    @app.get(
        "/api/service-requests/{request_reference}",
        response_model=PublicTracking,
        dependencies=[Depends(limited("tracking"))],
    )
    def track_request(request_reference: str) -> PublicTracking:
        with session_scope() as session:
            tracking = services.get_public_tracking(session, request_reference)
        if tracking is None:  # unknown and malformed references look identical
            raise HTTPException(status_code=404, detail="No demonstration request was found for that reference.")
        return tracking

    @app.get("/api/knowledge/services")
    def knowledge_services() -> dict[str, Any]:
        return {
            "entries": knowledge.entries_in("services"),
            "limitations": knowledge.entries_in("limitations"),
            "retrieved": knowledge.load_kb()["meta"]["retrieved"],
            "disclaimer": DISCLAIMER,
        }

    register_staff_routes(app, limited)
    register_static_site(app)  # no-op unless the frontend has been built into backend/static
    return app


app = create_app()
