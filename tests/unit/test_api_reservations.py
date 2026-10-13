from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from botocore.exceptions import ClientError

from tixlane import container
from tixlane.auth import ATTENDEES, ORGANISERS
from tixlane.handlers import api_reservations
from tixlane.models import Event, new_id

from .conftest import AwsResources
from .factories import (
    ATTENDEE_SUB,
    ORGANISER_SUB,
    OTHER_ATTENDEE_SUB,
    OTHER_ORGANISER_SUB,
    claims,
    http_event,
    invoke,
)

ATTENDEE = claims(ATTENDEE_SUB, ATTENDEES)
OTHER_ATTENDEE = claims(OTHER_ATTENDEE_SUB, ATTENDEES)
ORGANISER = claims(ORGANISER_SUB, ORGANISERS)
OTHER_ORGANISER = claims(OTHER_ORGANISER_SUB, ORGANISERS)

Result = tuple[int, Any, dict[str, str]]


def _reserve(
    event_id: str,
    *,
    key: str | None = "idem-key-0001",
    quantity: int | None = 1,
    jwt_claims: dict[str, Any] | None = ATTENDEE,
) -> Result:
    headers = {} if key is None else {"idempotency-key": key}
    body = None if quantity is None else {"quantity": quantity}
    event = http_event(
        "POST",
        f"/events/{event_id}/reservations",
        route="/events/{id}/reservations",
        path_params={"id": event_id},
        body=body,
        headers=headers,
        jwt_claims=jwt_claims,
    )
    return invoke(api_reservations.lambda_handler, event)


def _get(reservation_id: str, jwt_claims: dict[str, Any] | None = ATTENDEE) -> Result:
    event = http_event(
        "GET",
        f"/reservations/{reservation_id}",
        route="/reservations/{id}",
        path_params={"id": reservation_id},
        jwt_claims=jwt_claims,
    )
    return invoke(api_reservations.lambda_handler, event)


def test_reservation_is_accepted_pending_and_enqueued(
    aws: AwsResources, make_event: Callable[..., Event]
) -> None:
    event = make_event(capacity=4)
    status, body, headers = _reserve(event.event_id, quantity=3)
    assert status == 202
    assert body["status"] == "PENDING"
    assert body["quantity"] == 3
    assert body["attendee_id"] == ATTENDEE_SUB
    assert headers["Location"] == f"/reservations/{body['reservation_id']}"
    assert "Idempotent-Replayed" not in headers

    messages = aws.drain()
    assert [json.loads(m["Body"]) for m in messages] == [
        {"version": 1, "reservation_id": body["reservation_id"], "event_id": event.event_id}
    ]
    assert container.event_service().get(event.event_id).remaining == 1


def test_empty_body_reserves_one_seat(make_event: Callable[..., Event]) -> None:
    event = make_event()
    status, body, _ = _reserve(event.event_id, quantity=None)
    assert status == 202
    assert body["quantity"] == 1


def test_replay_returns_original_and_re_enqueues_while_pending(
    aws: AwsResources, make_event: Callable[..., Event]
) -> None:
    event = make_event(capacity=5)
    _, first, _ = _reserve(event.event_id)
    status, second, headers = _reserve(event.event_id)
    assert status == 202
    assert second["reservation_id"] == first["reservation_id"]
    assert headers["Idempotent-Replayed"] == "true"
    assert container.event_service().get(event.event_id).remaining == 4
    # Still PENDING, so the replay re-sends: this is what heals a lost first enqueue.
    assert len(aws.drain()) == 2


def test_replay_after_confirmation_does_not_re_enqueue(
    aws: AwsResources, make_event: Callable[..., Event]
) -> None:
    event = make_event()
    _, first, _ = _reserve(event.event_id)
    aws.drain()
    container.repository().confirm_reservation(
        first["reservation_id"], ticket_key="tickets/x.json", now=container.utc_now()
    )
    status, second, headers = _reserve(event.event_id)
    assert status == 202
    assert second["status"] == "CONFIRMED"
    assert headers["Idempotent-Replayed"] == "true"
    assert aws.drain() == []


def test_reusing_a_key_for_a_different_request_is_rejected(
    make_event: Callable[..., Event],
) -> None:
    event = make_event()
    _reserve(event.event_id, quantity=1)
    status, body, _ = _reserve(event.event_id, quantity=2)
    assert status == 422
    assert body["error"]["code"] == "idempotency_key_reused"


@pytest.mark.parametrize(("key", "message"), [(None, "required"), ("short", "8-128")])
def test_idempotency_key_header_is_required_and_validated(
    make_event: Callable[..., Event], key: str | None, message: str
) -> None:
    event = make_event()
    status, body, _ = _reserve(event.event_id, key=key)
    assert status == 400
    assert message in body["error"]["message"]


def test_sold_out_is_a_conflict(make_event: Callable[..., Event]) -> None:
    event = make_event(capacity=1)
    _reserve(event.event_id, key="first-key-01")
    status, body, _ = _reserve(event.event_id, key="second-key-01")
    assert status == 409
    assert body["error"]["code"] == "sold_out"


def test_unknown_event_is_not_found(aws: AwsResources) -> None:
    status, _, _ = _reserve(new_id())
    assert status == 404


def test_organisers_cannot_reserve(make_event: Callable[..., Event]) -> None:
    event = make_event()
    status, _, _ = _reserve(event.event_id, jwt_claims=ORGANISER)
    assert status == 403


def test_enqueue_failure_is_retryable_and_heals_on_replay(
    aws: AwsResources, make_event: Callable[..., Event], monkeypatch: pytest.MonkeyPatch
) -> None:
    event = make_event()
    sqs = container.sqs()
    real_send = sqs.send_message
    calls = {"n": 0}

    def flaky_send(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            error = {"Error": {"Code": "InternalError", "Message": "boom"}}
            raise ClientError(error, "SendMessage")
        return real_send(**kwargs)

    monkeypatch.setattr(sqs, "send_message", flaky_send)

    status, body, headers = _reserve(event.event_id)
    assert status == 503
    assert body["error"]["code"] == "enqueue_failed"
    assert headers["Retry-After"] == "1"
    assert container.event_service().get(event.event_id).remaining == 9  # seat is held

    status, body, _ = _reserve(event.event_id)
    assert status == 202
    assert len(aws.drain()) == 1


def test_owner_reads_own_reservation(make_event: Callable[..., Event]) -> None:
    event = make_event()
    _, created, _ = _reserve(event.event_id)
    status, body, _ = _get(created["reservation_id"])
    assert status == 200
    assert body == created


def test_other_attendees_get_not_found(make_event: Callable[..., Event]) -> None:
    event = make_event()
    _, created, _ = _reserve(event.event_id)
    status, _, _ = _get(created["reservation_id"], jwt_claims=OTHER_ATTENDEE)
    assert status == 404


def test_event_organiser_can_read_but_other_organisers_cannot(
    make_event: Callable[..., Event],
) -> None:
    event = make_event()
    _, created, _ = _reserve(event.event_id)
    assert _get(created["reservation_id"], jwt_claims=ORGANISER)[0] == 200
    assert _get(created["reservation_id"], jwt_claims=OTHER_ORGANISER)[0] == 404


def test_missing_reservation_is_not_found(aws: AwsResources) -> None:
    assert _get(new_id())[0] == 404
