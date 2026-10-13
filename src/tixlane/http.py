"""Request parsing and JSON responses shared by the HTTP API handlers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from aws_lambda_powertools.event_handler import Response, content_types
from pydantic import BaseModel, ValidationError

from tixlane.errors import BadRequestError, DomainError
from tixlane.models import IDEMPOTENCY_KEY_PATTERN

_IDEMPOTENCY_KEY = re.compile(IDEMPOTENCY_KEY_PATTERN)


def json_response(
    status_code: int, body: Any, headers: Mapping[str, str] | None = None
) -> Response[str]:
    response_headers: dict[str, str | list[str]] = {**(headers or {})}
    return Response(
        status_code=status_code,
        content_type=content_types.APPLICATION_JSON,
        body=json.dumps(body, separators=(",", ":")),
        headers=response_headers,
    )


def error_response(exc: DomainError) -> Response[str]:
    return json_response(exc.status_code, exc.to_body(), exc.headers)


def parse_body[M: BaseModel](model: type[M], raw: str | None) -> M:
    data: object = {}
    if raw is not None and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BadRequestError("Request body must be valid JSON") from exc
    if not isinstance(data, dict):
        raise BadRequestError("Request body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = [
            {
                "loc": [str(part) for part in error["loc"]],
                "msg": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors(include_url=False)
        ]
        raise BadRequestError("Request validation failed", details=details) from exc


def idempotency_key(headers: Mapping[str, str] | None) -> str:
    lowered = {key.lower(): value for key, value in (headers or {}).items()}
    value = lowered.get("idempotency-key")
    if not value:
        raise BadRequestError("Idempotency-Key header is required")
    if not _IDEMPOTENCY_KEY.fullmatch(value):
        raise BadRequestError("Idempotency-Key must be 8-128 characters of [A-Za-z0-9_-]")
    return value
