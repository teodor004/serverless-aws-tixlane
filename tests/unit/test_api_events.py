from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tixlane.auth import ATTENDEES, ORGANISERS
from tixlane.handlers import api_events
from tixlane.models import Event, new_id

from .conftest import AwsResources
from .factories import ATTENDEE_SUB, ORGANISER_SUB, claims, event_body, http_event, invoke

ORGANISER = claims(ORGANISER_SUB, ORGANISERS)
ATTENDEE = claims(ATTENDEE_SUB, ATTENDEES)


Result = tuple[int, Any, dict[str, str]]


def _post(
    body: object = None, *, jwt_claims: dict[str, Any] | None = ORGANISER, raw: str | None = None
) -> Result:
    event = http_event(
        "POST", "/events", route="/events", body=body, raw_body=raw, jwt_claims=jwt_claims
    )
    return invoke(api_events.lambda_handler, event)


def _get(event_id: str, jwt_claims: dict[str, Any] | None = ATTENDEE) -> Result:
    return invoke(
        api_events.lambda_handler,
        http_event(
            "GET",
            f"/events/{event_id}",
            route="/events/{id}",
            path_params={"id": event_id},
            jwt_claims=jwt_claims,
        ),
    )


def test_organiser_creates_event(aws: AwsResources) -> None:
    status, body, headers = _post(event_body(capacity=25))
    assert status == 201
    assert body["capacity"] == body["remaining"] == 25
    assert body["organiser_id"] == ORGANISER_SUB
    assert headers["Location"] == f"/events/{body['event_id']}"


def test_attendee_cannot_create_event(aws: AwsResources) -> None:
    status, body, _ = _post(event_body(), jwt_claims=ATTENDEE)
    assert status == 403
    assert body["error"]["code"] == "forbidden"


def test_unauthenticated_request_is_rejected(aws: AwsResources) -> None:
    status, body, _ = _post(event_body(), jwt_claims=None)
    assert status == 401
    assert body["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("{not json", "Request body must be valid JSON"),
        ("[1, 2]", "Request body must be a JSON object"),
        ("", "Request validation failed"),
    ],
)
def test_malformed_bodies_are_bad_requests(aws: AwsResources, raw: str, message: str) -> None:
    status, body, _ = _post(raw=raw)
    assert status == 400
    assert body["error"]["message"] == message


def test_validation_errors_list_offending_fields(aws: AwsResources) -> None:
    status, body, _ = _post(event_body(capacity=0, extra="nope"))
    assert status == 400
    locations = {tuple(detail["loc"]) for detail in body["error"]["details"]}
    assert locations == {("capacity",), ("extra",)}


def test_any_member_can_read_an_event(make_event: Callable[..., Event]) -> None:
    event = make_event()
    for who in (ATTENDEE, ORGANISER):
        status, body, _ = _get(event.event_id, jwt_claims=who)
        assert status == 200
        assert body["event_id"] == event.event_id


def test_unknown_and_malformed_ids_are_not_found(aws: AwsResources) -> None:
    for event_id in (new_id(), "not-a-uuid"):
        status, body, _ = _get(event_id)
        assert status == 404
        assert body["error"]["code"] == "not_found"


def test_users_outside_both_groups_are_forbidden(make_event: Callable[..., Event]) -> None:
    event = make_event()
    status, _, _ = _get(event.event_id, jwt_claims=claims(ATTENDEE_SUB))
    assert status == 403
