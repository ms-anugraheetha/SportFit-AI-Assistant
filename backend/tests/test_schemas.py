import pytest
from pydantic import ValidationError

from app.schemas import ServiceRequestCreate, StatusUpdate, normalize_reference


def test_valid_payload_accepted(valid_payload):
    req = ServiceRequestCreate(**valid_payload)
    assert req.email == "customer@example.com"


def test_optional_fields_may_be_omitted():
    req = ServiceRequestCreate(
        customer_name="Aa Bb",
        email="a@example.com",
        service_area="Salmiya",
        equipment_type="Bike",
        problem_description="Pedals make a grinding noise.",
        preferred_contact_method="email",
    )
    assert req.phone is None and req.serial_number is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("customer_name", "A"),
        ("email", "not-an-email"),
        ("service_area", ""),
        ("equipment_type", " "),
        ("problem_description", "too short"),
        ("problem_description", "x" * 1001),
        ("customer_name", "n" * 101),
        ("phone", "abc"),
        ("serial_number", "bad;serial'--"),
        ("preferred_contact_method", "carrier-pigeon"),
    ],
)
def test_invalid_values_rejected(valid_payload, field, value):
    valid_payload[field] = value
    with pytest.raises(ValidationError):
        ServiceRequestCreate(**valid_payload)


def test_required_fields_missing_rejected(valid_payload):
    del valid_payload["problem_description"]
    with pytest.raises(ValidationError):
        ServiceRequestCreate(**valid_payload)


def test_phone_required_for_phone_contact(valid_payload):
    valid_payload["phone"] = None
    valid_payload["preferred_contact_method"] = "whatsapp"
    with pytest.raises(ValidationError, match="phone number is required"):
        ServiceRequestCreate(**valid_payload)


def test_unknown_fields_forbidden(valid_payload):
    valid_payload["status"] = "completed"  # a customer must never be able to set status
    with pytest.raises(ValidationError):
        ServiceRequestCreate(**valid_payload)


def test_control_characters_stripped(valid_payload):
    valid_payload["customer_name"] = "Test\x00 Customer\x07"
    assert ServiceRequestCreate(**valid_payload).customer_name == "Test Customer"


def test_status_update_allowlist():
    assert StatusUpdate(status="in_progress").status == "in_progress"
    with pytest.raises(ValidationError):
        StatusUpdate(status="deleted")
    with pytest.raises(ValidationError):
        StatusUpdate(status="completed", customer_name="x")  # cannot carry other fields


def test_normalize_reference():
    assert normalize_reference(" sfd-abcd-2345-wxyz ") == "SFD-ABCD-2345-WXYZ"
    assert normalize_reference("1; DROP TABLE service_requests") is None
    assert normalize_reference("SFD-ABC-2345-WXYZ") is None
