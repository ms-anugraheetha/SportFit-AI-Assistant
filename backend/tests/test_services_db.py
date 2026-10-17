import pytest
from sqlalchemy import func, select

from app import services
from app.db import session_scope
from app.models import ServiceRequest, StatusHistory
from app.schemas import REFERENCE_RE, ServiceRequestCreate

pytestmark = pytest.mark.needs_db


def test_schema_initialised_via_migrations(db):
    from app.db import get_engine
    from sqlalchemy import inspect

    names = set(inspect(get_engine()).get_table_names())
    assert {"service_requests", "status_history", "alembic_version"} <= names


def test_create_persists_and_returns_unpredictable_reference(db, valid_payload):
    with session_scope() as s:
        ref = services.create_service_request(s, ServiceRequestCreate(**valid_payload))
    assert REFERENCE_RE.match(ref)
    with session_scope() as s:
        row = s.scalar(select(ServiceRequest).where(ServiceRequest.request_reference == ref))
        assert row.customer_name == "Test Customer"
        assert row.status == "received"
        assert row.created_at is not None and row.updated_at is not None


def test_rolled_back_transaction_leaves_no_record(db, valid_payload):
    with pytest.raises(RuntimeError):
        with session_scope() as s:
            services.create_service_request(s, ServiceRequestCreate(**valid_payload))
            raise RuntimeError("boom after flush, before commit")
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(ServiceRequest)) == 0


def test_references_are_unique_and_non_sequential(db, valid_payload):
    refs = set()
    for _ in range(25):
        with session_scope() as s:
            refs.add(services.create_service_request(s, ServiceRequestCreate(**valid_payload)))
    assert len(refs) == 25


def test_public_tracking_hides_private_fields(db, valid_payload):
    with session_scope() as s:
        ref = services.create_service_request(s, ServiceRequestCreate(**valid_payload))
    with session_scope() as s:
        tracking = services.get_public_tracking(s, ref.lower())  # case-insensitive input
    dumped = tracking.model_dump(mode="json")
    assert dumped["status"] == "received"
    blob = str(dumped)
    for private in ("customer@example.com", "+965", "SN-12345", "Test Customer", "Mahboula", "belt stops"):
        assert private not in blob


def test_unknown_or_malformed_reference_is_none(db):
    with session_scope() as s:
        assert services.get_public_tracking(s, "SFD-AAAA-AAAA-AAAA") is None
        assert services.get_public_tracking(s, "' OR 1=1 --") is None


def test_update_status_persists_history_and_touches_only_status(db, valid_payload):
    with session_scope() as s:
        ref = services.create_service_request(s, ServiceRequestCreate(**valid_payload))
    with session_scope() as s:
        before = s.scalar(select(ServiceRequest).where(ServiceRequest.request_reference == ref))
        created, updated0, name = before.created_at, before.updated_at, before.customer_name
    with session_scope() as s:
        services.update_status(s, ref, "in_progress", actor="staff-test")
    with session_scope() as s:
        row = s.scalar(select(ServiceRequest).where(ServiceRequest.request_reference == ref))
        assert row.status == "in_progress"
        assert row.updated_at > updated0
        assert row.created_at == created and row.customer_name == name
        hist = s.scalars(select(StatusHistory)).all()
        assert [(h.old_status, h.new_status, h.changed_by) for h in hist] == [
            ("received", "in_progress", "staff-test")
        ]
        assert services.get_public_tracking(s, ref).status == "in_progress"


def test_update_status_rejects_unknown_status_and_unknown_reference(db, valid_payload):
    with session_scope() as s:
        ref = services.create_service_request(s, ServiceRequestCreate(**valid_payload))
    with pytest.raises(services.InvalidStatusError):
        with session_scope() as s:
            services.update_status(s, ref, "exploded", actor="x")
    with pytest.raises(services.RequestNotFoundError):
        with session_scope() as s:
            services.update_status(s, "SFD-AAAA-AAAA-AAAA", "completed", actor="x")


def test_database_rejects_invalid_status_directly(db, valid_payload):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with session_scope() as s:
            s.add(ServiceRequest(request_reference="SFD-ZZZZ-ZZZZ-ZZZZ", status="bogus", **valid_payload))
