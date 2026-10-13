"""SQS consumer: confirms reservations, writes the ticket artefact, publishes ticket-issued.

Failures are reported per record (ReportBatchItemFailures) so one poison message does not
re-drive its whole batch. A record that keeps failing is moved to the DLQ by the queue's
redrive policy after 3 receives, and the DLQ-depth alarm fires.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.batch import (
    BatchProcessor,
    EventType,
    process_partial_response,
)
from aws_lambda_powertools.utilities.batch.types import PartialItemFailureResponse
from aws_lambda_powertools.utilities.data_classes.sqs_event import SQSRecord
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import ValidationError

from tixlane import container
from tixlane.errors import PoisonMessageError
from tixlane.models import ReservationMessage

logger = Logger()
tracer = Tracer()
metrics = Metrics()
processor = BatchProcessor(event_type=EventType.SQS)


@tracer.capture_method
def record_handler(record: SQSRecord) -> None:
    try:
        message = ReservationMessage.model_validate_json(record.body)
    except ValidationError as exc:
        logger.exception("poison message: invalid body", extra={"message_id": record.message_id})
        msg = f"message {record.message_id} is not a valid ReservationMessage"
        raise PoisonMessageError(msg) from exc
    result = container.ticket_issuer().issue(message)
    if result.newly_confirmed:
        metrics.add_metric(name="TicketsIssued", unit=MetricUnit.Count, value=1)
    logger.info(
        "reservation processed",
        extra={
            "reservation_id": message.reservation_id,
            "newly_confirmed": result.newly_confirmed,
            "notified": result.notified,
            "receive_count": record.attributes.approximate_receive_count,
        },
    )


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> PartialItemFailureResponse:
    return process_partial_response(
        event=event, record_handler=record_handler, processor=processor, context=context
    )
