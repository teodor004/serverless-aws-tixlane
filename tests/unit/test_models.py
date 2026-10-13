from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tixlane.errors import NotFoundError
from tixlane.models import (
    MAX_CAPACITY,
    MAX_QUANTITY,
    EventCreate,
    ReservationCreate,
    ReservationMessage,
    parse_id,
)

from .factories import event_body


def test_event_create_normalises_start_to_utc() -> None:
    cet = timezone(timedelta(hours=2))
    starts = datetime.now(cet) + timedelta(days=3)
    event = EventCreate.model_validate(event_body(starts_at=starts.isoformat()))
    assert event.starts_at.tzinfo == UTC
    assert event.starts_at == starts


def test_event_create_strips_whitespace() -> None:
    event = EventCreate.model_validate(event_body(name="  Padded  "))
    assert event.name == "Padded"


@pytest.mark.parametrize(
    "overrides",
    [
        {"starts_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
        {"starts_at": "2030-01-01T20:00:00"},  # naive datetimes are ambiguous
        {"capacity": 0},
        {"capacity": MAX_CAPACITY + 1},
        {"name": ""},
        {"name": "x" * 121},
        {"unexpected": "field"},
    ],
)
def test_event_create_rejects_invalid_payloads(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EventCreate.model_validate(event_body(**overrides))


def test_reservation_create_defaults_to_one_seat() -> None:
    assert ReservationCreate.model_validate({}).quantity == 1


@pytest.mark.parametrize("quantity", [0, -1, MAX_QUANTITY + 1])
def test_reservation_create_bounds_quantity(quantity: int) -> None:
    with pytest.raises(ValidationError):
        ReservationCreate.model_validate({"quantity": quantity})


def test_reservation_message_round_trips_and_pins_version() -> None:
    message = ReservationMessage(reservation_id="r", event_id="e")
    assert ReservationMessage.model_validate_json(message.model_dump_json()) == message
    with pytest.raises(ValidationError):
        ReservationMessage.model_validate({"version": 2, "reservation_id": "r", "event_id": "e"})


def test_parse_id_normalises_uuid_forms() -> None:
    value = "6F1C2D2E6A514C4F9A431F0F6B7C9D10"
    assert parse_id(value) == "6f1c2d2e-6a51-4c4f-9a43-1f0f6b7c9d10"


@pytest.mark.parametrize("value", ["", "not-a-uuid", "../../etc/passwd", "EVENT#x"])
def test_parse_id_treats_garbage_as_not_found(value: str) -> None:
    with pytest.raises(NotFoundError):
        parse_id(value)
