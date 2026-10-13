"""Organiser event routes: POST /events, GET /events/{id}."""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

from tixlane import container
from tixlane.auth import ATTENDEES, ORGANISERS, principal_from_event, require_any_group
from tixlane.errors import DomainError
from tixlane.http import error_response, json_response, parse_body
from tixlane.models import EventCreate, parse_id

logger = Logger()
tracer = Tracer()
metrics = Metrics()
app = APIGatewayHttpResolver()


def _domain_error(exc: DomainError) -> Response[str]:
    logger.info("request rejected", extra={"code": exc.code, "status": exc.status_code})
    return error_response(exc)


# Registered by call, not decorator: Powertools' decorator is untyped under mypy --strict.
app.exception_handler(DomainError)(_domain_error)


# Unexpected exceptions are deliberately not handled: they fail the invocation, which counts
# towards both the Lambda Errors alarm and the API 5xx-rate alarm.


@app.post("/events")
@tracer.capture_method
def create_event() -> Response[str]:
    principal = principal_from_event(app.current_event.raw_event, container.settings())
    require_any_group(principal, ORGANISERS)
    payload = parse_body(EventCreate, app.current_event.decoded_body)
    event = container.event_service().create(principal, payload)
    metrics.add_metric(name="EventsCreated", unit=MetricUnit.Count, value=1)
    logger.info("event created", extra={"event_id": event.event_id, "capacity": event.capacity})
    location = {"Location": f"/events/{event.event_id}"}
    return json_response(201, event.model_dump(mode="json"), location)


@app.get("/events/<event_id>")
@tracer.capture_method
def get_event(event_id: str) -> Response[str]:
    principal = principal_from_event(app.current_event.raw_event, container.settings())
    require_any_group(principal, ORGANISERS, ATTENDEES)
    event = container.event_service().get(parse_id(event_id))
    return json_response(200, event.model_dump(mode="json"))


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    return app.resolve(event, context)
