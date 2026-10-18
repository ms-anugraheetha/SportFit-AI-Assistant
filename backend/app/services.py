"""Deterministic domain operations shared by the REST API and the MCP server.

Nothing here involves the LLM: validation, reference generation, persistence and status
changes are plain code, so a model can never decide that a write "succeeded".
"""

import secrets

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import STATUSES, ServiceRequest, StatusHistory
from app.schemas import (
    STATUS_LABELS,
    PublicTracking,
    ServiceRequestCreate,
    normalize_reference,
)

# No 0/O/1/I/L so references are easy to read out loud. 30^12 ~ 5e17 combinations.
_REF_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


class InvalidStatusError(ValueError):
    pass


class RequestNotFoundError(LookupError):
    pass


def generate_reference() -> str:
    groups = ("".join(secrets.choice(_REF_ALPHABET) for _ in range(4)) for _ in range(3))
    return "SFD-" + "-".join(groups)


def create_service_request(session: Session, payload: ServiceRequestCreate) -> str:
    """Insert a request and return its public reference.

    The reference is returned only after the INSERT has been flushed successfully; the caller's
    transaction (``session_scope``) commits afterwards. Collisions are retried with a fresh value.
    """
    for _ in range(5):
        reference = generate_reference()
        row = ServiceRequest(request_reference=reference, status="received", **payload.model_dump())
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:
            continue  # reference collision (astronomically unlikely) - try again
        return reference
    raise RuntimeError("could not allocate a unique request reference")


def get_public_tracking(session: Session, raw_reference: str) -> PublicTracking | None:
    reference = normalize_reference(raw_reference)
    if reference is None:
        return None
    row = session.scalar(select(ServiceRequest).where(ServiceRequest.request_reference == reference))
    if row is None:
        return None
    return PublicTracking(
        request_reference=row.request_reference,
        status=row.status,
        status_label=STATUS_LABELS[row.status],
        equipment_type=row.equipment_type,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def update_status(session: Session, raw_reference: str, new_status: str, actor: str) -> ServiceRequest:
    """Staff-only status change. Touches the status column (and history) and nothing else."""
    if new_status not in STATUSES:
        raise InvalidStatusError(new_status)
    reference = normalize_reference(raw_reference)
    row = (
        session.scalar(
            select(ServiceRequest).where(ServiceRequest.request_reference == reference).with_for_update()
        )
        if reference
        else None
    )
    if row is None:
        raise RequestNotFoundError(raw_reference)
    if row.status != new_status:
        session.add(StatusHistory(request_id=row.id, old_status=row.status, new_status=new_status, changed_by=actor))
        row.status = new_status  # updated_at is refreshed by the column's onupdate
        session.flush()
    return row


# ---- Staff dashboard queries ----

SORT_COLUMNS = {
    "created_at": ServiceRequest.created_at,
    "updated_at": ServiceRequest.updated_at,
    "status": ServiceRequest.status,
    "customer_name": ServiceRequest.customer_name,
    "equipment_type": ServiceRequest.equipment_type,
}
SUMMARY_LENGTH = 100


def _like_pattern(term: str) -> str:
    """Escape LIKE wildcards so a search for '%' or '_' matches literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def problem_summary(text: str) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= SUMMARY_LENGTH else one_line[: SUMMARY_LENGTH - 1].rstrip() + "\u2026"


def list_requests(
    session: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 10,
) -> tuple[list[ServiceRequest], int]:
    """Search / filter / sort / paginate. ``sort`` is whitelisted; every value is bound as a parameter."""
    if sort not in SORT_COLUMNS:
        raise ValueError("unsupported sort column")
    if status is not None and status not in STATUSES:
        raise InvalidStatusError(status)
    stmt = select(ServiceRequest)
    if status:
        stmt = stmt.where(ServiceRequest.status == status)
    if q and q.strip():
        pattern = _like_pattern(q.strip())
        stmt = stmt.where(
            or_(
                *(
                    col.ilike(pattern, escape="\\")
                    for col in (
                        ServiceRequest.request_reference,
                        ServiceRequest.customer_name,
                        ServiceRequest.email,
                        ServiceRequest.equipment_type,
                        ServiceRequest.equipment_brand,
                        ServiceRequest.problem_description,
                    )
                )
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = SORT_COLUMNS[sort]
    primary = col.asc() if order == "asc" else col.desc()
    rows = session.scalars(
        stmt.order_by(primary, ServiceRequest.id.desc()).limit(page_size).offset((page - 1) * page_size)
    ).all()
    return list(rows), total


def status_counts(session: Session) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for status, n in session.execute(select(ServiceRequest.status, func.count()).group_by(ServiceRequest.status)):
        counts[status] = n
    return counts


def get_request_with_history(session: Session, raw_reference: str) -> tuple[ServiceRequest, list[StatusHistory]] | None:
    reference = normalize_reference(raw_reference)
    row = (
        session.scalar(select(ServiceRequest).where(ServiceRequest.request_reference == reference))
        if reference
        else None
    )
    if row is None:
        return None
    history = session.scalars(
        select(StatusHistory).where(StatusHistory.request_id == row.id).order_by(StatusHistory.changed_at, StatusHistory.id)
    ).all()
    return row, list(history)
