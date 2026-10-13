"""DynamoDB access for the single TixLane table.

Overselling is prevented by one TransactWriteItems call that (1) decrements ``remaining``
only while ``remaining >= quantity``, (2) creates the PENDING reservation and (3) claims the
caller's idempotency key. Either all three happen or none do. See ADR-0003 and ADR-0004.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError
from pydantic import BaseModel

from tixlane import keys
from tixlane.errors import IdempotencyConflictError, NotFoundError, ServiceBusyError, SoldOutError
from tixlane.models import Event, Reservation, ReservationStatus

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

logger = Logger(child=True)

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


@dataclass(frozen=True, slots=True)
class ReservationOutcome:
    reservation: Reservation
    replayed: bool


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    reservation_id: str
    request_hash: str


class Repository:
    def __init__(
        self,
        client: DynamoDBClient,
        table_name: str,
        *,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._table = table_name
        self._max_attempts = max_attempts
        self._sleep = sleep

    # -- events ---------------------------------------------------------------------------

    def put_event(self, event: Event) -> None:
        self._client.put_item(
            TableName=self._table,
            Item=_to_item(
                {"PK": keys.event_pk(event.event_id), "SK": keys.META, "entity": "Event"}
                | event.model_dump(mode="json")
            ),
            ConditionExpression="attribute_not_exists(PK)",
        )

    def get_event(self, event_id: str) -> Event | None:
        item = self._get(keys.event_pk(event_id))
        return None if item is None else _model(Event, item)

    # -- reservations ---------------------------------------------------------------------

    def get_reservation(self, reservation_id: str) -> Reservation | None:
        item = self._get(keys.reservation_pk(reservation_id))
        return None if item is None else _model(Reservation, item)

    def list_reservations_for_event(self, event_id: str, *, limit: int = 50) -> list[Reservation]:
        response = self._client.query(
            TableName=self._table,
            IndexName=keys.GSI1,
            KeyConditionExpression="GSI1PK = :pk AND begins_with(GSI1SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": {"S": keys.event_pk(event_id)},
                ":prefix": {"S": keys.RESERVATION_PREFIX},
            },
            Limit=limit,
        )
        return [_model(Reservation, item) for item in response.get("Items", [])]

    def create_reservation(
        self,
        reservation: Reservation,
        *,
        idempotency_key: str,
        request_hash: str,
        expires_at: int,
    ) -> ReservationOutcome:
        data = reservation.model_dump(mode="json")
        reservation_item = _to_item(
            {
                "PK": keys.reservation_pk(reservation.reservation_id),
                "SK": keys.META,
                "entity": "Reservation",
                "GSI1PK": keys.event_pk(reservation.event_id),
                "GSI1SK": keys.reservation_gsi1_sk(data["created_at"], reservation.reservation_id),
            }
            | data
        )
        idempotency_item = _to_item(
            {
                "PK": keys.idempotency_pk(reservation.attendee_id, idempotency_key),
                "SK": keys.META,
                "entity": "Idempotency",
                "reservation_id": reservation.reservation_id,
                "request_hash": request_hash,
                "expires_at": expires_at,
            }
        )
        quantity = {":q": {"N": str(reservation.quantity)}}
        for attempt in range(self._max_attempts):
            try:
                self._client.transact_write_items(
                    TransactItems=[
                        {
                            "Update": {
                                "TableName": self._table,
                                "Key": _key(keys.event_pk(reservation.event_id)),
                                "UpdateExpression": "SET remaining = remaining - :q",
                                "ConditionExpression": "attribute_exists(PK) AND remaining >= :q",
                                "ExpressionAttributeValues": quantity,
                            }
                        },
                        {
                            "Put": {
                                "TableName": self._table,
                                "Item": reservation_item,
                                "ConditionExpression": "attribute_not_exists(PK)",
                            }
                        },
                        {
                            "Put": {
                                "TableName": self._table,
                                "Item": idempotency_item,
                                "ConditionExpression": "attribute_not_exists(PK)",
                            }
                        },
                    ]
                )
            except ClientError as exc:
                if _error_code(exc) != "TransactionCanceledException":
                    raise
                outcome = self._resolve_cancellation(
                    reservation, idempotency_key, request_hash, exc
                )
                if outcome is not None:
                    return outcome
                self._sleep(_backoff(attempt))
            else:
                return ReservationOutcome(reservation=reservation, replayed=False)
        raise ServiceBusyError(
            "Reservation could not complete under contention; retry with the same Idempotency-Key"
        )

    def confirm_reservation(self, reservation_id: str, *, ticket_key: str, now: datetime) -> bool:
        """PENDING -> CONFIRMED. Returns False when another delivery already confirmed it."""
        return self._conditional_update(
            reservation_id,
            update=(
                "SET #status = :confirmed, ticket_key = :key,"
                " confirmed_at = :now, updated_at = :now"
            ),
            condition="#status = :pending",
            values={
                ":confirmed": {"S": ReservationStatus.CONFIRMED.value},
                ":pending": {"S": ReservationStatus.PENDING.value},
                ":key": {"S": ticket_key},
                ":now": {"S": now.isoformat()},
            },
        )

    def mark_notified(self, reservation_id: str, now: datetime) -> bool:
        return self._conditional_update(
            reservation_id,
            update="SET notified_at = :now, updated_at = :now",
            condition="#status = :confirmed AND attribute_not_exists(notified_at)",
            values={
                ":confirmed": {"S": ReservationStatus.CONFIRMED.value},
                ":now": {"S": now.isoformat()},
            },
        )

    # -- internals ------------------------------------------------------------------------

    def _resolve_cancellation(
        self,
        reservation: Reservation,
        idempotency_key: str,
        request_hash: str,
        exc: ClientError,
    ) -> ReservationOutcome | None:
        """Work out why the transaction was cancelled, using consistent reads.

        Returns the outcome for a replay, raises for a terminal failure, or returns None for a
        transient conflict that should be retried. Reads are used instead of trusting
        CancellationReasons alone so the logic is identical on DynamoDB, Moto and emulators.
        """
        response = cast("Mapping[str, Any]", exc.response)
        reasons = [reason.get("Code", "None") for reason in response.get("CancellationReasons", [])]
        logger.info(
            "reservation transaction cancelled",
            extra={"event_id": reservation.event_id, "reasons": reasons},
        )
        record = self._get_idempotency(reservation.attendee_id, idempotency_key)
        if record is not None:
            if record.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "Idempotency-Key was already used with a different request"
                )
            existing = self.get_reservation(record.reservation_id)
            if existing is None:  # pragma: no cover - written atomically with the record
                msg = f"idempotency record points at missing reservation {record.reservation_id}"
                raise RuntimeError(msg)
            return ReservationOutcome(reservation=existing, replayed=True)
        event = self.get_event(reservation.event_id)
        if event is None:
            raise NotFoundError("Event not found")
        if event.remaining < reservation.quantity:
            raise SoldOutError("Not enough seats remaining")
        return None

    def _get_idempotency(self, attendee_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        item = self._get(keys.idempotency_pk(attendee_id, idempotency_key))
        if item is None:
            return None
        data = _from_item(item)
        return IdempotencyRecord(
            reservation_id=str(data["reservation_id"]), request_hash=str(data["request_hash"])
        )

    def _get(self, pk: str) -> dict[str, Any] | None:
        response = self._client.get_item(TableName=self._table, Key=_key(pk), ConsistentRead=True)
        item = response.get("Item")
        return None if item is None else dict(item)

    def _conditional_update(
        self, reservation_id: str, *, update: str, condition: str, values: dict[str, Any]
    ) -> bool:
        try:
            self._client.update_item(
                TableName=self._table,
                Key=_key(keys.reservation_pk(reservation_id)),
                UpdateExpression=update,
                ConditionExpression=f"attribute_exists(PK) AND {condition}",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if _error_code(exc) == "ConditionalCheckFailedException":
                return False
            raise
        return True


def _key(pk: str) -> dict[str, Any]:
    return {"PK": {"S": pk}, "SK": {"S": keys.META}}


def _to_item(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: _serializer.serialize(value) for name, value in values.items() if value is not None
    }


def _from_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {name: _deserializer.deserialize(value) for name, value in item.items()}


def _model[M: BaseModel](model: type[M], item: Mapping[str, Any]) -> M:
    data = _from_item(item)
    return model.model_validate({name: data[name] for name in model.model_fields if name in data})


def _error_code(exc: ClientError) -> str:
    response = cast("Mapping[str, Any]", exc.response)
    return str(response.get("Error", {}).get("Code", ""))


def _backoff(attempt: int) -> float:
    return min(0.05 * 2.0**attempt, 0.4) * random.uniform(0.5, 1.0)  # noqa: S311 - jitter, not crypto
