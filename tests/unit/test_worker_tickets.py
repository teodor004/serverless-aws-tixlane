from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from aws_lambda_powertools.utilities.batch.exceptions import BatchProcessingError
from botocore.exceptions import ClientError

from tixlane import container
from tixlane.auth import Principal
from tixlane.errors import PoisonMessageError
from tixlane.handlers import worker_tickets
from tixlane.models import Event, Reservation, ReservationCreate, ReservationMessage, new_id
from tixlane.repository import Repository
from tixlane.services import TICKET_NAMESPACE, TicketIssuer

from .conftest import AwsResources
from .factories import FakeLambdaContext, sqs_event


def _pending(attendee: Principal, event: Event, quantity: int = 2) -> Reservation:
    outcome = container.reservation_service().reserve(
        attendee, event.event_id, ReservationCreate(quantity=quantity), f"worker-{new_id()[:8]}"
    )
    return outcome.reservation


def _handle(aws: AwsResources, messages: list[dict[str, Any]]) -> Any:
    return worker_tickets.lambda_handler(sqs_event(messages, aws.queue_arn), FakeLambdaContext())


def _raw(body: str, message_id: str | None = None) -> dict[str, Any]:
    return {"MessageId": message_id or new_id(), "Body": body}


def test_worker_confirms_writes_ticket_and_publishes(
    aws: AwsResources, attendee: Principal, make_event: Callable[..., Event]
) -> None:
    event = make_event(name="Late Show")
    reservation = _pending(attendee, event, quantity=2)

    assert _handle(aws, aws.drain()) == {"batchItemFailures": []}

    stored = container.repository().get_reservation(reservation.reservation_id)
    assert stored is not None
    assert stored.status == "CONFIRMED"
    assert stored.ticket_key == f"tickets/{reservation.reservation_id}.json"
    assert stored.confirmed_at is not None
    assert stored.notified_at is not None

    obj = boto3.client("s3").get_object(Bucket=aws.bucket_name, Key=stored.ticket_key)
    assert obj["ContentType"] == "application/json"
    ticket = json.loads(obj["Body"].read())
    assert ticket["event_name"] == "Late Show"
    assert ticket["attendee_id"] == attendee.sub
    assert len(set(ticket["ticket_codes"])) == 2

    [notification] = aws.published()
    assert notification == {
        "type": "ticket-issued",
        "version": 1,
        "reservation_id": reservation.reservation_id,
        "event_id": event.event_id,
        "attendee_id": attendee.sub,
        "quantity": 2,
        "ticket_key": stored.ticket_key,
    }


def test_redelivery_is_a_no_op(
    aws: AwsResources, attendee: Principal, make_event: Callable[..., Event]
) -> None:
    _pending(attendee, make_event())
    messages = aws.drain()
    _handle(aws, messages)
    assert _handle(aws, messages) == {"batchItemFailures": []}
    assert len(aws.published()) == 1


def test_ticket_codes_are_deterministic(
    aws: AwsResources, attendee: Principal, make_event: Callable[..., Event]
) -> None:
    reservation = _pending(attendee, make_event(), quantity=3)
    _handle(aws, aws.drain())
    obj = boto3.client("s3").get_object(
        Bucket=aws.bucket_name, Key=f"tickets/{reservation.reservation_id}.json"
    )
    codes = json.loads(obj["Body"].read())["ticket_codes"]
    assert codes == [
        str(uuid.uuid5(TICKET_NAMESPACE, f"{reservation.reservation_id}:{seat}"))
        for seat in range(3)
    ]


def test_poison_alone_fails_the_invocation(aws: AwsResources) -> None:
    # A batch where every record fails raises, so SQS re-drives it; after 3 receives the
    # queue's redrive policy moves the message to the DLQ.
    with pytest.raises(BatchProcessingError):
        _handle(aws, [_raw("this is not json")])


@pytest.mark.parametrize(
    "body",
    [
        "this is not json",
        json.dumps({"version": 1, "reservation_id": "r"}),
        json.dumps({"version": 1, "reservation_id": "missing", "event_id": "missing"}),
    ],
)
def test_poison_records_are_reported_individually(
    aws: AwsResources, attendee: Principal, make_event: Callable[..., Event], body: str
) -> None:
    _pending(attendee, make_event())
    good = aws.drain()
    poison = _raw(body, message_id="poison-1")
    assert _handle(aws, [*good, poison]) == {"batchItemFailures": [{"itemIdentifier": "poison-1"}]}


def test_message_for_wrong_event_is_poison(
    aws: AwsResources, attendee: Principal, make_event: Callable[..., Event]
) -> None:
    reservation = _pending(attendee, make_event())
    aws.drain()
    message = ReservationMessage(reservation_id=reservation.reservation_id, event_id=new_id())
    with pytest.raises(PoisonMessageError):
        container.ticket_issuer().issue(message)


class _FailOncePublisher:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.failed = False

    def publish(self, **kwargs: Any) -> Any:
        if not self.failed:
            self.failed = True
            raise ClientError({"Error": {"Code": "InternalError", "Message": "boom"}}, "Publish")
        return self._inner.publish(**kwargs)


def _issuer(aws: AwsResources, repository: Repository, sns: Any) -> TicketIssuer:
    return TicketIssuer(
        repository,
        boto3.client("s3"),
        sns,
        bucket=aws.bucket_name,
        topic_arn=aws.topic_arn,
        clock=lambda: datetime.now(UTC),
    )


def test_publish_failure_is_retried_without_reconfirming(
    aws: AwsResources, attendee: Principal, make_event: Callable[..., Event]
) -> None:
    reservation = _pending(attendee, make_event())
    aws.drain()
    message = ReservationMessage(
        reservation_id=reservation.reservation_id, event_id=reservation.event_id
    )
    issuer = _issuer(aws, container.repository(), _FailOncePublisher(boto3.client("sns")))

    with pytest.raises(ClientError):
        issuer.issue(message)
    half_done = container.repository().get_reservation(reservation.reservation_id)
    assert half_done is not None
    assert half_done.status == "CONFIRMED"
    assert half_done.notified_at is None

    result = issuer.issue(message)
    assert not result.newly_confirmed
    assert result.notified
    assert len(aws.published()) == 1


class _StaleRepository(Repository):
    """Returns the PENDING snapshot once, as if a concurrent delivery confirmed in between."""

    def __init__(self, inner: Repository, stale: Reservation) -> None:
        self.__dict__.update(inner.__dict__)
        self._stale: Reservation | None = stale

    def get_reservation(self, reservation_id: str) -> Reservation | None:
        if self._stale is not None:
            stale, self._stale = self._stale, None
            return stale
        return super().get_reservation(reservation_id)


def test_losing_a_confirmation_race_still_converges(
    aws: AwsResources, attendee: Principal, make_event: Callable[..., Event]
) -> None:
    reservation = _pending(attendee, make_event())
    aws.drain()
    repo = container.repository()
    repo.confirm_reservation(
        reservation.reservation_id, ticket_key="tickets/other.json", now=datetime.now(UTC)
    )
    issuer = _issuer(aws, _StaleRepository(repo, reservation), boto3.client("sns"))
    result = issuer.issue(
        ReservationMessage(reservation_id=reservation.reservation_id, event_id=reservation.event_id)
    )
    assert not result.newly_confirmed
    assert result.reservation.ticket_key == "tickets/other.json"
    assert result.notified


def test_reservation_whose_event_vanished_is_poison(
    aws: AwsResources, attendee: Principal, make_event: Callable[..., Event]
) -> None:
    event = make_event()
    reservation = _pending(attendee, event)
    aws.drain()
    boto3.client("dynamodb").delete_item(
        TableName=aws.table_name,
        Key={"PK": {"S": f"EVENT#{event.event_id}"}, "SK": {"S": "META"}},
    )
    with pytest.raises(PoisonMessageError):
        container.ticket_issuer().issue(
            ReservationMessage(reservation_id=reservation.reservation_id, event_id=event.event_id)
        )
