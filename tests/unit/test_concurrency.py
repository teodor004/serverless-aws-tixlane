"""P1 acceptance: 50 concurrent reservations for 10 seats yield exactly 10 confirmable ones."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import boto3
import pytest
from moto.dynamodb.models import DynamoDBBackend

from tixlane import container
from tixlane.auth import ATTENDEES, Principal
from tixlane.errors import SoldOutError
from tixlane.handlers import worker_tickets
from tixlane.models import Event, ReservationCreate, ReservationStatus

from .conftest import AwsResources
from .factories import FakeLambdaContext, sqs_event

SEATS = 10
CONTENDERS = 50


@pytest.fixture
def serialized_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give Moto DynamoDB's isolation guarantees.

    Moto's in-memory backend makes no thread-safety promise, whereas DynamoDB documents that
    TransactWriteItems is serializable and single-item reads are atomic. Moto even mutates on
    reads (a get_item for a missing key inserts into the table's defaultdict) while a
    concurrent transaction deep-copies the table for rollback. One re-entrant lock around
    every backend operation models DynamoDB's guarantees, so this test exercises *our*
    condition expressions rather than Moto's internals. Re-entrant because Moto's
    transact_write_items calls update_item/put_item internally. The same scenario runs
    unlocked against a real server in
    tests/integration/test_reservation_flow.py::test_oversell_is_impossible_over_http.
    """
    lock = threading.RLock()

    def serialize(name: str) -> None:
        original = getattr(DynamoDBBackend, name)

        def serialized(self: DynamoDBBackend, *args: Any, **kwargs: Any) -> Any:
            with lock:
                return original(self, *args, **kwargs)

        monkeypatch.setattr(DynamoDBBackend, name, serialized)

    for name in ("transact_write_items", "get_item", "put_item", "update_item", "query"):
        serialize(name)


def test_fifty_concurrent_reservations_for_ten_seats(
    aws: AwsResources,
    serialized_transactions: None,
    make_event: Callable[..., Event],
) -> None:
    event = make_event(capacity=SEATS)
    service = container.reservation_service()
    start = threading.Barrier(CONTENDERS, timeout=10)

    # Service layer, not lambda_handler: Powertools' resolver keeps per-request state on a
    # module-level object, and a real Lambda never runs two invocations in one process.
    def contend(i: int) -> str | None:
        attendee = Principal(sub=f"attendee-{i:02d}", groups=frozenset({ATTENDEES}))
        start.wait()
        try:
            outcome = service.reserve(
                attendee, event.event_id, ReservationCreate(quantity=1), f"contend-{i:04d}"
            )
        except SoldOutError:
            return None
        return outcome.reservation.reservation_id

    with ThreadPoolExecutor(max_workers=CONTENDERS) as pool:
        results = list(pool.map(contend, range(CONTENDERS)))

    winners = [reservation_id for reservation_id in results if reservation_id is not None]
    assert len(winners) == SEATS
    assert len(set(winners)) == SEATS
    assert results.count(None) == CONTENDERS - SEATS
    assert container.event_service().get(event.event_id).remaining == 0

    # Exactly one message per winner, and the worker confirms every one of them.
    messages = aws.drain()
    assert sorted(json.loads(m["Body"])["reservation_id"] for m in messages) == sorted(winners)
    response = worker_tickets.lambda_handler(
        sqs_event(messages, aws.queue_arn), FakeLambdaContext()
    )
    assert response == {"batchItemFailures": []}

    s3 = boto3.client("s3")
    repo = container.repository()
    for reservation_id in winners:
        reservation = repo.get_reservation(reservation_id)
        assert reservation is not None
        assert reservation.status is ReservationStatus.CONFIRMED
        s3.head_object(Bucket=aws.bucket_name, Key=f"tickets/{reservation_id}.json")
    assert len(aws.published()) == SEATS
