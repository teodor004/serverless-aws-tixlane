"""Use cases behind the HTTP routes and the SQS ticket worker."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from aws_lambda_powertools import Logger
from botocore.exceptions import BotoCoreError, ClientError

from tixlane import keys
from tixlane.auth import ORGANISERS, Principal
from tixlane.errors import EnqueueFailedError, NotFoundError, PoisonMessageError
from tixlane.models import (
    Event,
    EventCreate,
    Reservation,
    ReservationCreate,
    ReservationMessage,
    ReservationStatus,
    Ticket,
    new_id,
)
from tixlane.repository import Repository, ReservationOutcome

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_sns import SNSClient
    from mypy_boto3_sqs import SQSClient

logger = Logger(child=True)

Clock = Callable[[], datetime]

# Fixed namespace so ticket codes are a pure function of (reservation_id, seat): re-delivered
# messages rewrite byte-identical artefacts.
TICKET_NAMESPACE = uuid.UUID("6f1c2d2e-6a51-4c4f-9a43-1f0f6b7c9d10")


def request_fingerprint(event_id: str, payload: ReservationCreate) -> str:
    canonical = json.dumps(
        {"event_id": event_id, **payload.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class EventService:
    def __init__(self, repository: Repository, *, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    def create(self, organiser: Principal, payload: EventCreate) -> Event:
        event = Event(
            event_id=new_id(),
            name=payload.name,
            venue=payload.venue,
            starts_at=payload.starts_at,
            capacity=payload.capacity,
            remaining=payload.capacity,
            organiser_id=organiser.sub,
            created_at=self._clock(),
        )
        self._repository.put_event(event)
        return event

    def get(self, event_id: str) -> Event:
        event = self._repository.get_event(event_id)
        if event is None:
            raise NotFoundError("Event not found")
        return event


class ReservationQueue(Protocol):
    def send(self, message: ReservationMessage) -> None: ...


class SqsReservationQueue:
    def __init__(self, client: SQSClient, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    def send(self, message: ReservationMessage) -> None:
        try:
            self._client.send_message(
                QueueUrl=self._queue_url, MessageBody=message.model_dump_json()
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception(
                "failed to enqueue reservation", extra={"reservation_id": message.reservation_id}
            )
            raise EnqueueFailedError(
                "Reservation saved but not yet queued; retry with the same Idempotency-Key"
            ) from exc


class ReservationService:
    def __init__(
        self,
        repository: Repository,
        queue: ReservationQueue,
        *,
        clock: Clock,
        idempotency_ttl_seconds: int,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._clock = clock
        self._ttl = idempotency_ttl_seconds

    def reserve(
        self,
        attendee: Principal,
        event_id: str,
        payload: ReservationCreate,
        idempotency_key: str,
    ) -> ReservationOutcome:
        now = self._clock()
        candidate = Reservation(
            reservation_id=new_id(),
            event_id=event_id,
            attendee_id=attendee.sub,
            quantity=payload.quantity,
            status=ReservationStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        outcome = self._repository.create_reservation(
            candidate,
            idempotency_key=idempotency_key,
            request_hash=request_fingerprint(event_id, payload),
            expires_at=int(now.timestamp()) + self._ttl,
        )
        if outcome.reservation.status is ReservationStatus.PENDING:
            # New reservation, or a replay whose first enqueue may have failed. The worker is
            # idempotent, so re-sending on replay is what heals the DB-commit/SQS-send gap.
            self._queue.send(
                ReservationMessage(
                    reservation_id=outcome.reservation.reservation_id,
                    event_id=outcome.reservation.event_id,
                )
            )
        return outcome

    def get_for(self, principal: Principal, reservation_id: str) -> Reservation:
        """Owners see their reservation; organisers see reservations for their own events."""
        reservation = self._repository.get_reservation(reservation_id)
        if reservation is not None:
            if reservation.attendee_id == principal.sub:
                return reservation
            if ORGANISERS in principal.groups:
                event = self._repository.get_event(reservation.event_id)
                if event is not None and event.organiser_id == principal.sub:
                    return reservation
        # Same answer for "missing" and "not yours" so IDs cannot be probed.
        raise NotFoundError("Reservation not found")


@dataclass(frozen=True, slots=True)
class IssueResult:
    reservation: Reservation
    newly_confirmed: bool
    notified: bool


def build_ticket(reservation: Reservation, event: Event, *, issued_at: datetime) -> Ticket:
    return Ticket(
        reservation_id=reservation.reservation_id,
        event_id=event.event_id,
        event_name=event.name,
        venue=event.venue,
        starts_at=event.starts_at,
        attendee_id=reservation.attendee_id,
        quantity=reservation.quantity,
        ticket_codes=[
            str(uuid.uuid5(TICKET_NAMESPACE, f"{reservation.reservation_id}:{seat}"))
            for seat in range(reservation.quantity)
        ],
        issued_at=issued_at,
    )


class TicketIssuer:
    """PENDING -> write artefact -> CONFIRMED -> publish -> notified.

    Every step is safe to repeat, so SQS at-least-once delivery cannot double-confirm. The
    SNS publish is at-least-once: a crash between publish and ``mark_notified`` re-publishes,
    and subscribers deduplicate on ``reservation_id`` (ADR-0006).
    """

    def __init__(
        self,
        repository: Repository,
        s3: S3Client,
        sns: SNSClient,
        *,
        bucket: str,
        topic_arn: str,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._s3 = s3
        self._sns = sns
        self._bucket = bucket
        self._topic_arn = topic_arn
        self._clock = clock

    def issue(self, message: ReservationMessage) -> IssueResult:
        reservation = self._repository.get_reservation(message.reservation_id)
        if reservation is None or reservation.event_id != message.event_id:
            msg = f"no reservation {message.reservation_id} for event {message.event_id}"
            raise PoisonMessageError(msg)
        newly_confirmed = False
        if reservation.status is ReservationStatus.PENDING:
            reservation, newly_confirmed = self._confirm(reservation)
        notified = False
        if reservation.notified_at is None:
            self._publish(reservation)
            notified = self._repository.mark_notified(reservation.reservation_id, self._clock())
        return IssueResult(
            reservation=reservation, newly_confirmed=newly_confirmed, notified=notified
        )

    def _confirm(self, reservation: Reservation) -> tuple[Reservation, bool]:
        event = self._repository.get_event(reservation.event_id)
        if event is None:
            msg = f"event of reservation {reservation.reservation_id} is gone"
            raise PoisonMessageError(msg)
        now = self._clock()
        object_key = keys.ticket_object_key(reservation.reservation_id)
        ticket = build_ticket(reservation, event, issued_at=now)
        # Artefact first, so CONFIRMED always implies the ticket exists in S3.
        self._s3.put_object(
            Bucket=self._bucket,
            Key=object_key,
            Body=ticket.model_dump_json().encode(),
            ContentType="application/json",
        )
        transitioned = self._repository.confirm_reservation(
            reservation.reservation_id, ticket_key=object_key, now=now
        )
        current = self._repository.get_reservation(reservation.reservation_id)
        if current is None or current.status is not ReservationStatus.CONFIRMED:
            msg = f"reservation {reservation.reservation_id} did not reach CONFIRMED"
            raise RuntimeError(msg)
        return current, transitioned

    def _publish(self, reservation: Reservation) -> None:
        body = {
            "type": "ticket-issued",
            "version": 1,
            "reservation_id": reservation.reservation_id,
            "event_id": reservation.event_id,
            "attendee_id": reservation.attendee_id,
            "quantity": reservation.quantity,
            "ticket_key": reservation.ticket_key,
        }
        self._sns.publish(
            TopicArn=self._topic_arn,
            Message=json.dumps(body, separators=(",", ":")),
            MessageAttributes={
                "type": {"DataType": "String", "StringValue": "ticket-issued"},
                "event_id": {"DataType": "String", "StringValue": reservation.event_id},
            },
        )
