"""Deterministic domain operations shared by the REST API and the MCP server.

Nothing here involves the LLM: validation, reference generation, persistence and status
changes are plain code, so a model can never decide that a write "succeeded".
"""

import secrets

from sqlalchemy import select
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
