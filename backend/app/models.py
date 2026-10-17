"""SQLAlchemy models. Schema changes go through Alembic migrations."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# The single source of truth for statuses; the frontend reads them from the API.
STATUSES = ("received", "under_review", "in_progress", "completed", "cancelled")
CONTACT_METHODS = ("email", "phone", "whatsapp")


class Base(DeclarativeBase):
    pass


class ServiceRequest(Base):
    __tablename__ = "service_requests"

    id: Mapped[int] = mapped_column(primary_key=True)  # internal only, never exposed
    request_reference: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    customer_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(20))
    service_area: Mapped[str] = mapped_column(String(100))

    equipment_type: Mapped[str] = mapped_column(String(60))
    equipment_brand: Mapped[str | None] = mapped_column(String(60))
    equipment_model: Mapped[str | None] = mapped_column(String(60))
    serial_number: Mapped[str | None] = mapped_column(String(64))

    problem_description: Mapped[str] = mapped_column(Text)
    preferred_contact_method: Mapped[str] = mapped_column(String(16))
    preferred_appointment_time: Mapped[str | None] = mapped_column(String(100))

    status: Mapped[str] = mapped_column(String(20), default="received", server_default="received")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('received','under_review','in_progress','completed','cancelled')",
            name="ck_service_requests_status",
        ),
        CheckConstraint(
            "preferred_contact_method IN ('email','phone','whatsapp')",
            name="ck_service_requests_contact_method",
        ),
        CheckConstraint(
            "char_length(problem_description) <= 1000", name="ck_service_requests_problem_len"
        ),
        Index("ix_service_requests_status_created", "status", "created_at"),
        Index("ix_service_requests_created_at", "created_at"),
    )


class StatusHistory(Base):
    """Audit trail of status changes made by staff."""

    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("service_requests.id", ondelete="CASCADE"), index=True
    )
    old_status: Mapped[str] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20))
    changed_by: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
