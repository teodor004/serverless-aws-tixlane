"""P3: Cognito sign-in -> token -> API -> async confirmation within 30 s -> S3 artefact."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import e2e

pytestmark = pytest.mark.integration

CONFIRMATION_DEADLINE_SECONDS = 30


def _key() -> str:
    return f"itest-{uuid.uuid4().hex}"


def test_reservation_is_confirmed_and_ticket_issued(
    stack: e2e.Stack, api: e2e.Api, organiser: e2e.TestUser, attendee: e2e.TestUser
) -> None:
    event = e2e.expect(api.create_event(organiser.token, capacity=5), 201, "create event")
    seen = e2e.expect(api.get_event(attendee.token, event["event_id"]), 200, "get event")
    assert seen["remaining"] == 5

    key = _key()
    created = e2e.expect(api.reserve(attendee.token, event["event_id"], key, 2), 202, "reserve")
    assert created["status"] == "PENDING"

    replay = api.reserve(attendee.token, event["event_id"], key, 2)
    assert replay.status == 202
    assert replay.body["reservation_id"] == created["reservation_id"]
    assert replay.headers.get("idempotent-replayed") == "true"

    confirmed, seconds = e2e.wait_for_status(
        api,
        attendee.token,
        created["reservation_id"],
        "CONFIRMED",
        timeout=CONFIRMATION_DEADLINE_SECONDS,
    )
    assert seconds <= CONFIRMATION_DEADLINE_SECONDS

    obj = stack.client("s3").get_object(Bucket=stack.bucket_name, Key=confirmed["ticket_key"])
    ticket = json.loads(obj["Body"].read())
    assert ticket["reservation_id"] == created["reservation_id"]
    assert len(ticket["ticket_codes"]) == 2

    after = e2e.expect(api.get_event(attendee.token, event["event_id"]), 200, "get event")
    assert after["remaining"] == 3


def test_requests_without_a_token_are_rejected(api: e2e.Api) -> None:
    assert api.call("GET", f"/events/{uuid.uuid4()}").status == 401


def test_group_rules_are_enforced(
    api: e2e.Api, users: dict[str, e2e.TestUser], organiser: e2e.TestUser, attendee: e2e.TestUser
) -> None:
    assert api.create_event(attendee.token, capacity=1).status == 403
    event = e2e.expect(api.create_event(organiser.token, capacity=3), 201, "create event")
    assert api.reserve(organiser.token, event["event_id"], _key()).status == 403

    created = e2e.expect(api.reserve(attendee.token, event["event_id"], _key()), 202, "reserve")
    rid = created["reservation_id"]
    assert api.get_reservation(organiser.token, rid).status == 200
    assert api.get_reservation(users["other_organiser"].token, rid).status == 404
    assert api.get_reservation(users["other_attendee"].token, rid).status == 404


def test_oversell_is_impossible_over_http(
    api: e2e.Api, organiser: e2e.TestUser, attendee: e2e.TestUser
) -> None:
    """Same invariant as the Moto test, without the lock: real server-side concurrency."""
    seats, contenders = 5, 20
    event = e2e.expect(api.create_event(organiser.token, capacity=seats), 201, "create event")

    with ThreadPoolExecutor(max_workers=contenders) as pool:
        statuses = list(
            pool.map(
                lambda _: api.reserve(attendee.token, event["event_id"], _key()).status,
                range(contenders),
            )
        )

    # 503 service_busy is a legitimate answer under contention; it never consumes a seat.
    assert statuses.count(202) == seats
    assert set(statuses) <= {202, 409, 503}
    after = e2e.expect(api.get_event(attendee.token, event["event_id"]), 200, "get event")
    assert after["remaining"] == 0
