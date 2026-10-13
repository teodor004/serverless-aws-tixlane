from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError

from tixlane import container
from tixlane.auth import Principal
from tixlane.errors import IdempotencyConflictError, NotFoundError, ServiceBusyError, SoldOutError
from tixlane.models import Event, Reservation, ReservationStatus, new_id
from tixlane.repository import Repository, ReservationOutcome

from .conftest import AwsResources


def _reservation(event_id: str, attendee: Principal, quantity: int = 1) -> Reservation:
    now = datetime.now(UTC)
    return Reservation(
        reservation_id=new_id(),
        event_id=event_id,
        attendee_id=attendee.sub,
        quantity=quantity,
        status=ReservationStatus.PENDING,
        created_at=now,
        updated_at=now,
    )


def _create(
    repo: Repository, reservation: Reservation, key: str = "key-00000001", rhash: str = "h"
) -> ReservationOutcome:
    return repo.create_reservation(
        reservation, idempotency_key=key, request_hash=rhash, expires_at=2_000_000_000
    )


class CancellingClient:
    """Moto client whose first ``failures`` transactions are cancelled by a write conflict."""

    def __init__(self, inner: Any, failures: int) -> None:
        self._inner = inner
        self.failures = failures

    def transact_write_items(self, **kwargs: Any) -> Any:
        if self.failures > 0:
            self.failures -= 1
            raise ClientError(
                {
                    "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
                    "CancellationReasons": [
                        {"Code": "TransactionConflict"},
                        {"Code": "None"},
                        {"Code": "None"},
                    ],
                },
                "TransactWriteItems",
            )
        return self._inner.transact_write_items(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_event_round_trip(make_event: Callable[..., Event]) -> None:
    event = make_event(capacity=7)
    assert container.repository().get_event(event.event_id) == event


def test_put_event_refuses_to_overwrite(make_event: Callable[..., Event]) -> None:
    event = make_event()
    with pytest.raises(ClientError) as raised:
        container.repository().put_event(event)
    assert raised.value.response["Error"]["Code"] == "ConditionalCheckFailedException"


def test_missing_items_return_none(aws: AwsResources) -> None:
    repo = container.repository()
    assert repo.get_event(new_id()) is None
    assert repo.get_reservation(new_id()) is None


def test_create_reservation_decrements_capacity_atomically(
    make_event: Callable[..., Event], attendee: Principal
) -> None:
    repo = container.repository()
    event = make_event(capacity=5)
    outcome = _create(repo, _reservation(event.event_id, attendee, quantity=3))
    assert not outcome.replayed
    assert repo.get_reservation(outcome.reservation.reservation_id) == outcome.reservation
    stored = repo.get_event(event.event_id)
    assert stored is not None
    assert stored.remaining == 2


def test_sold_out_leaves_no_partial_writes(
    make_event: Callable[..., Event], attendee: Principal
) -> None:
    repo = container.repository()
    event = make_event(capacity=2)
    candidate = _reservation(event.event_id, attendee, quantity=3)
    with pytest.raises(SoldOutError):
        _create(repo, candidate)
    assert repo.get_reservation(candidate.reservation_id) is None
    stored = repo.get_event(event.event_id)
    assert stored is not None
    assert stored.remaining == 2
    # The idempotency key was not consumed by the failed attempt.
    retry = _create(repo, _reservation(event.event_id, attendee, quantity=2))
    assert not retry.replayed


def test_unknown_event_is_not_found(aws: AwsResources, attendee: Principal) -> None:
    with pytest.raises(NotFoundError):
        _create(container.repository(), _reservation(new_id(), attendee))


def test_same_key_same_request_replays_original(
    make_event: Callable[..., Event], attendee: Principal
) -> None:
    repo = container.repository()
    event = make_event(capacity=5)
    first = _create(repo, _reservation(event.event_id, attendee))
    second = _create(repo, _reservation(event.event_id, attendee))
    assert second.replayed
    assert second.reservation == first.reservation
    stored = repo.get_event(event.event_id)
    assert stored is not None
    assert stored.remaining == 4


def test_same_key_different_request_is_a_conflict(
    make_event: Callable[..., Event], attendee: Principal
) -> None:
    repo = container.repository()
    event = make_event()
    _create(repo, _reservation(event.event_id, attendee), rhash="first")
    with pytest.raises(IdempotencyConflictError):
        _create(repo, _reservation(event.event_id, attendee), rhash="second")


def test_idempotency_keys_are_scoped_per_attendee(
    make_event: Callable[..., Event], attendee: Principal, other_attendee: Principal
) -> None:
    repo = container.repository()
    event = make_event()
    mine = _create(repo, _reservation(event.event_id, attendee))
    theirs = _create(repo, _reservation(event.event_id, other_attendee))
    assert not theirs.replayed
    assert theirs.reservation.reservation_id != mine.reservation.reservation_id


def test_transaction_conflicts_are_retried_with_backoff(
    make_event: Callable[..., Event], attendee: Principal
) -> None:
    event = make_event()
    sleeps: list[float] = []
    repo = Repository(
        CancellingClient(boto3.client("dynamodb"), failures=2),
        container.settings().table_name,
        sleep=sleeps.append,
    )
    outcome = _create(repo, _reservation(event.event_id, attendee))
    assert not outcome.replayed
    assert len(sleeps) == 2
    assert all(0 < delay <= 0.4 for delay in sleeps)


def test_persistent_conflict_surfaces_as_busy(
    make_event: Callable[..., Event], attendee: Principal
) -> None:
    event = make_event()
    repo = Repository(
        CancellingClient(boto3.client("dynamodb"), failures=99),
        container.settings().table_name,
        max_attempts=3,
        sleep=lambda _: None,
    )
    with pytest.raises(ServiceBusyError):
        _create(repo, _reservation(event.event_id, attendee))


def test_other_client_errors_propagate(aws: AwsResources, attendee: Principal) -> None:
    repo = Repository(boto3.client("dynamodb"), "no-such-table")
    with pytest.raises(ClientError) as raised:
        _create(repo, _reservation(new_id(), attendee))
    assert raised.value.response["Error"]["Code"] != "TransactionCanceledException"


def test_confirm_and_notify_are_one_shot_transitions(
    make_event: Callable[..., Event], attendee: Principal
) -> None:
    repo = container.repository()
    event = make_event()
    reservation = _create(repo, _reservation(event.event_id, attendee)).reservation
    now = datetime.now(UTC)
    rid = reservation.reservation_id

    assert not repo.mark_notified(rid, now), "cannot notify before confirmation"
    assert repo.confirm_reservation(rid, ticket_key="tickets/x.json", now=now)
    assert not repo.confirm_reservation(rid, ticket_key="tickets/x.json", now=now)
    assert repo.mark_notified(rid, now)
    assert not repo.mark_notified(rid, now)

    stored = repo.get_reservation(rid)
    assert stored is not None
    assert stored.status is ReservationStatus.CONFIRMED
    assert stored.ticket_key == "tickets/x.json"
    assert stored.notified_at is not None
    assert not repo.confirm_reservation(new_id(), ticket_key="k", now=now)


def test_conditional_update_propagates_unexpected_errors(aws: AwsResources) -> None:
    repo = Repository(boto3.client("dynamodb"), "no-such-table")
    with pytest.raises(ClientError):
        repo.confirm_reservation(new_id(), ticket_key="k", now=datetime.now(UTC))


def test_gsi1_lists_reservations_for_an_event(
    make_event: Callable[..., Event], attendee: Principal, other_attendee: Principal
) -> None:
    repo = container.repository()
    event = make_event()
    unrelated = make_event()
    ids = {
        _create(repo, _reservation(event.event_id, who)).reservation.reservation_id
        for who in (attendee, other_attendee)
    }
    _create(repo, _reservation(unrelated.event_id, attendee), key="key-00000002")
    listed = repo.list_reservations_for_event(event.event_id)
    assert {reservation.reservation_id for reservation in listed} == ids
