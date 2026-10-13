"""Single-table key layout. See docs/adr/0002-single-table-dynamodb.md."""

from __future__ import annotations

META = "META"
GSI1 = "GSI1"
RESERVATION_PREFIX = "RES#"


def event_pk(event_id: str) -> str:
    return f"EVENT#{event_id}"


def reservation_pk(reservation_id: str) -> str:
    return f"{RESERVATION_PREFIX}{reservation_id}"


def idempotency_pk(attendee_id: str, idempotency_key: str) -> str:
    # Scoped per caller so two attendees can never collide on the same client-chosen key.
    return f"IDEMP#{attendee_id}#{idempotency_key}"


def reservation_gsi1_sk(created_at_iso: str, reservation_id: str) -> str:
    return f"{RESERVATION_PREFIX}{created_at_iso}#{reservation_id}"


def ticket_object_key(reservation_id: str) -> str:
    return f"tickets/{reservation_id}.json"
