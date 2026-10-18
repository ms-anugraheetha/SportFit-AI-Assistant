"""Pydantic schemas: server-side validation and the public/staff response shapes."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

ContactMethod = Literal["email", "phone", "whatsapp"]
Status = Literal["received", "under_review", "in_progress", "completed", "cancelled"]

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-]{5,18}[0-9]$")
_SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-_/.]{0,63}$")

REFERENCE_RE = re.compile(r"^SFD-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")

STATUS_LABELS: dict[str, str] = {
    "received": "Received",
    "under_review": "Under review",
    "in_progress": "In progress",
    "completed": "Completed",
    "cancelled": "Cancelled",
}


def normalize_reference(raw: str) -> str | None:
    """Return a canonical reference or None if the input cannot be a valid one."""
    candidate = raw.strip().upper()
    return candidate if REFERENCE_RE.match(candidate) else None


def _clean(value: object) -> object:
    if isinstance(value, str):
        value = _CONTROL_CHARS.sub("", value).strip()
        return value or None
    return value


class ServiceRequestCreate(BaseModel):
    """Everything the customer provides. Validated again on the backend regardless of the UI."""

    model_config = ConfigDict(extra="forbid")

    customer_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)
    service_area: str = Field(min_length=2, max_length=100)
    equipment_type: str = Field(min_length=2, max_length=60)
    equipment_brand: str | None = Field(default=None, max_length=60)
    equipment_model: str | None = Field(default=None, max_length=60)
    serial_number: str | None = Field(default=None, max_length=64)
    problem_description: str = Field(min_length=10, max_length=1000)
    preferred_contact_method: ContactMethod
    preferred_appointment_time: str | None = Field(default=None, max_length=100)

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return _clean(v)

    @field_validator("phone")
    @classmethod
    def _phone_format(cls, v: str | None) -> str | None:
        if v is not None and not _PHONE_RE.match(v):
            raise ValueError("phone number format is not valid")
        return v

    @field_validator("serial_number")
    @classmethod
    def _serial_format(cls, v: str | None) -> str | None:
        if v is not None and not _SERIAL_RE.match(v):
            raise ValueError("serial number may only contain letters, digits, spaces and - _ / .")
        return v

    @model_validator(mode="after")
    def _phone_needed_for_phone_contact(self) -> "ServiceRequestCreate":
        if self.preferred_contact_method in ("phone", "whatsapp") and not self.phone:
            raise ValueError("a phone number is required when the preferred contact is phone or WhatsApp")
        return self


# ---- Public tracking (deliberately minimal: no contact details, serial number or free text) ----


class PublicTracking(BaseModel):
    request_reference: str
    status: Status
    status_label: str
    equipment_type: str
    created_at: datetime
    updated_at: datetime
    demo_notice: str = (
        "Demonstration data only. This is an independent academic prototype and is not "
        "connected to SportFit's real service system."
    )


# ---- Staff views ----


class StaffRequestSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_reference: str
    created_at: datetime
    customer_name: str
    equipment_type: str
    problem_summary: str
    status: Status


class StaffRequestDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_reference: str
    customer_name: str
    email: str
    phone: str | None
    service_area: str
    equipment_type: str
    equipment_brand: str | None
    equipment_model: str | None
    serial_number: str | None
    problem_description: str
    preferred_contact_method: ContactMethod
    preferred_appointment_time: str | None
    status: Status
    created_at: datetime
    updated_at: datetime


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a status update can never carry other fields

    status: Status


class StatusHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    old_status: Status
    new_status: Status
    changed_by: str
    changed_at: datetime


class StaffRequestFull(StaffRequestDetail):
    history: list[StatusHistoryItem] = []


class StaffRequestPage(BaseModel):
    items: list[StaffRequestSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class StaffSummary(BaseModel):
    total: int
    counts: dict[str, int]
