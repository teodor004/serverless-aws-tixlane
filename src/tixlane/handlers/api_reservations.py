"""Attendee routes: POST /events/{id}/reservations, GET /reservations/{id}."""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

from tixlane import container
from tixlane.auth import ATTENDEES, ORGANISERS, principal_from_event, require_any_group
from tixlane.errors import DomainError, SoldOutError
from tixlane.http import error_response, idempotency_key, json_response, parse_body
from tixlane.models import ReservationCreate, parse_id

logger = Logger()
tracer = Tracer()
metrics = Metrics()
app = APIGatewayHttpResolver()


def _domain_error(exc: DomainError) -> Response[str]:
    if isinstance(exc, SoldOutError):
        metrics.add_metric(name="SoldOut", unit=MetricUnit.Count, value=1)
    logger.info("request rejected", extra={"code": exc.code, "status": exc.status_code})
    return error_response(exc)


# Registered by call, not decorator: Powertools' decorator is untyped under mypy --strict.
app.exception_handler(DomainError)(_domain_error)


@app.post("/events/<event_id>/reservations")
@tracer.capture_method
def create_reservation(event_id: str) -> Response[str]:
    principal = principal_from_event(app.current_event.raw_event, container.settings())
    require_any_group(principal, ATTENDEES)
    target = parse_id(event_id)
    key = idempotency_key(app.current_event.headers)
    payload = parse_body(ReservationCreate, app.current_event.decoded_body)
    outcome = container.reservation_service().reserve(principal, target, payload, key)
    reservation = outcome.reservation
    headers = {"Location": f"/reservations/{reservation.reservation_id}"}
    if outcome.replayed:
        headers["Idempotent-Replayed"] = "true"
    else:
        metrics.add_metric(name="ReservationsCreated", unit=MetricUnit.Count, value=1)
    logger.info(
        "reservation accepted",
        extra={
            "reservation_id": reservation.reservation_id,
            "event_id": reservation.event_id,
            "replayed": outcome.replayed,
        },
    )
    # 202: the reservation exists (PENDING) but confirmation is asynchronous.
    return json_response(202, reservation.model_dump(mode="json"), headers)


@app.get("/reservations/<reservation_id>")
@tracer.capture_method
def get_reservation(reservation_id: str) -> Response[str]:
    principal = principal_from_event(app.current_event.raw_event, container.settings())
    require_any_group(principal, ORGANISERS, ATTENDEES)
    reservation = container.reservation_service().get_for(principal, parse_id(reservation_id))
    return json_response(200, reservation.model_dump(mode="json"))


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    return app.resolve(event, context)
