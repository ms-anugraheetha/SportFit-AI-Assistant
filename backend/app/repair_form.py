"""The guided repair-request form: fields, questions, per-field validation and the summary text.

Validation reuses ServiceRequestCreate, so the chat flow and the REST/MCP paths can never disagree
about what a valid value is. Nothing in this module involves the language model.
"""

import re
from typing import Any

from pydantic import ValidationError

from app.schemas import ServiceRequestCreate

REQUIRED_FIELDS = (
    "customer_name",
    "email",
    "service_area",
    "equipment_type",
    "problem_description",
    "preferred_contact_method",
)
OPTIONAL_FIELDS = (
    "phone",
    "equipment_brand",
    "equipment_model",
    "serial_number",
    "preferred_appointment_time",
)
ORDER = REQUIRED_FIELDS + OPTIONAL_FIELDS

LABELS = {
    "customer_name": "Name",
    "email": "Email",
    "service_area": "General area",
    "equipment_type": "Equipment type",
    "problem_description": "Problem",
    "preferred_contact_method": "Preferred contact",
    "phone": "Phone",
    "equipment_brand": "Brand",
    "equipment_model": "Model",
    "serial_number": "Serial number",
    "preferred_appointment_time": "Preferred appointment time",
}

QUESTIONS = {
    "customer_name": "What is your name?",
    "email": "What email address can be used for this demonstration request?",
    "service_area": "Which general area are you in (a neighbourhood or city is enough - no full street address needed)?",
    "equipment_type": "What type of equipment needs repair (for example treadmill, elliptical or a weight machine)?",
    "problem_description": "Please describe the problem in a sentence or two: what happens, and when did it start? (at least 10 characters)",
    "preferred_contact_method": "How would you prefer to be contacted: email, phone or WhatsApp?",
    "phone": "What phone number should be used? (digits, optionally starting with +)",
    "equipment_brand": "Optional - what brand is it? (type \"skip\" to skip this one, or \"skip all\" to skip the remaining optional questions)",
    "equipment_model": "Optional - what is the model? (\"skip\" or \"skip all\")",
    "serial_number": "Optional - do you have the serial number? (\"skip\" or \"skip all\")",
    "preferred_appointment_time": "Optional - any preferred appointment time? (\"skip\" to finish)",
}

FRIENDLY_ERRORS = {
    "customer_name": "Please enter your name (2-100 characters).",
    "email": "That doesn't look like a valid email address. Please check it and try again.",
    "service_area": "Please enter your general area (2-100 characters).",
    "equipment_type": "Please say what type of equipment it is (2-60 characters).",
    "problem_description": "Please describe the problem in at least 10 characters (maximum 1000).",
    "preferred_contact_method": "Please choose one: email, phone or WhatsApp.",
    "phone": "That phone number doesn't look right. Use 7-20 characters: digits, spaces, ( ) or -, optionally starting with +.",
    "equipment_brand": "The brand must be 60 characters or fewer.",
    "equipment_model": "The model must be 60 characters or fewer.",
    "serial_number": "Serial numbers may only contain letters, digits, spaces and - _ / . (maximum 64 characters).",
    "preferred_appointment_time": "Please keep this under 100 characters.",
}

# words a customer may use after "edit ..." to pick a field
ALIASES = {
    "name": "customer_name",
    "email": "email",
    "mail": "email",
    "area": "service_area",
    "location": "service_area",
    "city": "service_area",
    "type": "equipment_type",
    "equipment": "equipment_type",
    "problem": "problem_description",
    "issue": "problem_description",
    "description": "problem_description",
    "contact": "preferred_contact_method",
    "phone": "phone",
    "number": "phone",
    "brand": "equipment_brand",
    "model": "equipment_model",
    "serial": "serial_number",
    "time": "preferred_appointment_time",
    "appointment": "preferred_appointment_time",
}

# Valid stand-in values so one field can be validated on its own with the real model.
_SAMPLE: dict[str, Any] = {
    "customer_name": "Sample Name",
    "email": "sample@example.com",
    "service_area": "Sample Area",
    "equipment_type": "Treadmill",
    "problem_description": "A sample problem description.",
    "preferred_contact_method": "email",
    # present so choosing phone/WhatsApp as the contact method passes the cross-field rule when the
    # contact method itself is validated; the real phone number is asked for separately
    "phone": "+965 5000 0000",
}


def parse_contact_method(raw: str) -> str | None:
    n = raw.lower()
    if "whatsapp" in n or "whats app" in n:
        return "whatsapp"
    if re.search(r"\b(phone|call|mobile|telephone|ring)\b", n):
        return "phone"
    if re.search(r"e-?mail|\bmail\b", n):
        return "email"
    return None


def validate_field(field: str, raw: str) -> tuple[str | None, str | None]:
    """Return (clean_value, None) or (None, friendly_error) for one field."""
    value: Any = raw
    if field == "preferred_contact_method":
        value = parse_contact_method(raw)
        if value is None:
            return None, FRIENDLY_ERRORS[field]
    data = dict(_SAMPLE)
    data[field] = value
    try:
        model = ServiceRequestCreate.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            if err["loc"] and err["loc"][0] == field:
                return None, FRIENDLY_ERRORS[field]
        return None, "That value isn't valid."
    clean = getattr(model, field)
    if clean is None:
        return None, FRIENDLY_ERRORS[field]
    return str(clean), None


def phone_required(draft: dict[str, str]) -> bool:
    return draft.get("preferred_contact_method") in ("phone", "whatsapp")


def next_field(draft: dict[str, str], skipped: set[str]) -> str | None:
    for f in ORDER:
        if f in draft:
            continue
        if f in skipped and not (f == "phone" and phone_required(draft)):
            continue
        return f
    return None


def is_optional(field: str, draft: dict[str, str]) -> bool:
    return field in OPTIONAL_FIELDS and not (field == "phone" and phone_required(draft))


def build_summary(draft: dict[str, str]) -> str:
    def line(label: str, value: str | None) -> str:
        return f"• {label}: {value if value else '-'}"

    equipment = draft.get("equipment_type", "-")
    extra = " ".join(x for x in (draft.get("equipment_brand"), draft.get("equipment_model")) if x)
    if extra:
        equipment = f"{equipment} ({extra})"
    lines = [
        "Please review your demonstration request:",
        line("Name", draft.get("customer_name")),
        line("Email", draft.get("email")),
        line("Phone", draft.get("phone")),
        line("General area", draft.get("service_area")),
        line("Equipment", equipment),
        line("Serial number", draft.get("serial_number")),
        line("Problem", draft.get("problem_description")),
        line("Preferred contact", draft.get("preferred_contact_method")),
        line("Preferred appointment time", draft.get("preferred_appointment_time")),
        "",
        "Nothing has been saved yet. If you confirm, this is recorded ONLY in this academic demonstration "
        "database - it is NOT sent to SportFit.",
        'Reply "confirm" to save it, "cancel" to discard it, or "edit <field>" (for example "edit email") to change something.',
    ]
    return "\n".join(lines)
