"""Staff-only API: login/logout, request list, detail, status update.

Every route except login requires a valid server-side session (401). Mutating routes also require the CSRF
header (403). Customer-facing endpoints never share these code paths, and the model has no tool that
reaches any of this.
"""

import logging
import math
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app import auth, services
from app.auth import StaffContext
from app.db import session_scope
from app.schemas import (
    Status,
    StaffRequestFull,
    StaffRequestPage,
    StaffRequestSummary,
    StaffSummary,
    StatusHistoryItem,
    StatusUpdate,
)

log = logging.getLogger("sportfit.staff")


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionInfo(BaseModel):
    username: str
    csrf_token: str
    expires_at: datetime


def _info(ctx: StaffContext) -> SessionInfo:
    return SessionInfo(username=ctx.username, csrf_token=ctx.csrf_token, expires_at=ctx.expires_at)


def _full(row: Any, history: list[Any]) -> StaffRequestFull:
    return StaffRequestFull(
        **{k: getattr(row, k) for k in StaffRequestFull.model_fields if k != "history"},
        history=[StatusHistoryItem.model_validate(h) for h in history],
    )


def register_staff_routes(app: FastAPI, limited: Any) -> None:
    Staff = Annotated[StaffContext, Depends(auth.require_staff)]
    StaffCsrf = Annotated[StaffContext, Depends(auth.require_staff_csrf)]

    @app.post("/api/staff/login", response_model=SessionInfo, dependencies=[Depends(limited("login"))])
    def login(body: LoginBody, request: Request, response: Response) -> SessionInfo:
        settings = request.app.state.settings
        if not auth.staff_login_enabled(settings):
            # Misconfigured server: say so without revealing which setting is missing.
            raise HTTPException(status_code=503, detail="Staff login is not configured on this server.")
        if not auth.check_credentials(settings, body.username, body.password):
            log.warning("failed staff login")  # never log the username or password that was tried
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        token, ctx = auth.create_session(settings, settings.staff_username)
        response.set_cookie(
            auth.COOKIE_NAME,
            token,
            max_age=settings.staff_session_hours * 3600,
            httponly=True,
            secure=settings.app_env == "production",
            samesite="lax",
            path="/",
        )
        return _info(ctx)

    @app.post("/api/staff/logout", status_code=204)
    def logout(request: Request, ctx: StaffCsrf) -> Response:
        auth.destroy_session(request.app.state.settings, request.cookies.get(auth.COOKIE_NAME))
        response = Response(status_code=204)
        response.delete_cookie(auth.COOKIE_NAME, path="/")
        return response

    @app.get("/api/staff/me", response_model=SessionInfo)
    def me(ctx: Staff) -> SessionInfo:
        return _info(ctx)

    @app.get("/api/staff/summary", response_model=StaffSummary)
    def summary(ctx: Staff) -> StaffSummary:
        with session_scope() as s:
            counts = services.status_counts(s)
        return StaffSummary(total=sum(counts.values()), counts=counts)

    @app.get("/api/staff/service-requests", response_model=StaffRequestPage)
    def list_requests(
        ctx: Staff,
        q: Annotated[str | None, Query(max_length=100)] = None,
        status: Annotated[Status | None, Query()] = None,
        sort: Annotated[Literal["created_at", "updated_at", "status", "customer_name", "equipment_type"], Query()] = "created_at",
        order: Annotated[Literal["asc", "desc"], Query()] = "desc",
        page: Annotated[int, Query(ge=1, le=10_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> StaffRequestPage:
        with session_scope() as s:
            rows, total = services.list_requests(
                s, q=q, status=status, sort=sort, order=order, page=page, page_size=page_size
            )
            items = [
                StaffRequestSummary(
                    request_reference=r.request_reference,
                    created_at=r.created_at,
                    customer_name=r.customer_name,
                    equipment_type=r.equipment_type,
                    problem_summary=services.problem_summary(r.problem_description),
                    status=r.status,
                )
                for r in rows
            ]
        return StaffRequestPage(
            items=items, page=page, page_size=page_size, total=total, total_pages=max(1, math.ceil(total / page_size))
        )

    @app.get("/api/staff/service-requests/{request_reference}", response_model=StaffRequestFull)
    def detail(request_reference: str, ctx: Staff) -> StaffRequestFull:
        with session_scope() as s:
            found = services.get_request_with_history(s, request_reference)
            if found is None:
                raise HTTPException(status_code=404, detail="Request not found.")
            return _full(*found)

    @app.patch("/api/staff/service-requests/{request_reference}/status", response_model=StaffRequestFull)
    def change_status(request_reference: str, body: StatusUpdate, ctx: StaffCsrf) -> StaffRequestFull:
        with session_scope() as s:
            try:
                services.update_status(s, request_reference, body.status, ctx.username)
            except services.RequestNotFoundError:
                raise HTTPException(status_code=404, detail="Request not found.")
            found = services.get_request_with_history(s, request_reference)
            assert found is not None
            return _full(*found)
