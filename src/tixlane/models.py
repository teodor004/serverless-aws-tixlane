"""Pydantic models for API payloads, stored entities and queue messages."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

from tixlane.errors import NotFoundError

MAX_CAPACITY = 10_000
MAX_QUANTITY = 10
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_-]{8,128}$"

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


def new_id() -> str:
    return str(uuid.uuid4())


def parse_id(value: str) -> str:
    """Normalise a path identifier; anything that is not a UUID cannot exist, so it is a 404."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise NotFoundError("Resource not found") from exc


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventCreate(_Model):
    name: Name
    venue: Name
    starts_at: AwareDatetime
    capacity: int = Field(ge=1, le=MAX_CAPACITY)

    @field_validator("starts_at")
    @classmethod
    def _must_be_future(cls, value: datetime) -> datetime:
        if value <= datetime.now(UTC):
            raise ValueError("starts_at must be in the future")
        return value.astimezone(UTC)


class Event(_Model):
    event_id: str
    name: str
    venue: str
    starts_at: datetime
    capacity: int
    remaining: int
    organiser_id: str
    created_at: datetime


class ReservationCreate(_Model):
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY)


class ReservationStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"


class Reservation(_Model):
    reservation_id: str
    event_id: str
    attendee_id: str
    quantity: int
    status: ReservationStatus
    created_at: datetime
    updated_at: datetime
    ticket_key: str | None = None
    confirmed_at: datetime | None = None
    notified_at: datetime | None = None


class ReservationMessage(_Model):
    """Body of the SQS message the API sends to the ticket worker."""

    version: Literal[1] = 1
    reservation_id: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=64)


class Ticket(_Model):
    """Shape of the tickets/{reservation_id}.json artefact written to S3."""

    reservation_id: str
    event_id: str
    event_name: str
    venue: str
    starts_at: datetime
    attendee_id: str
    quantity: int
    ticket_codes: list[str]
    issued_at: datetime
