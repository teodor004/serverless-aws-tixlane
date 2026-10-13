"""Domain errors and their HTTP mapping."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar


class DomainError(Exception):
    """An expected failure that maps onto a JSON HTTP error response."""

    status_code: ClassVar[int] = 500
    code: ClassVar[str] = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.headers: dict[str, str] = dict(headers or {})

    def to_body(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


class BadRequestError(DomainError):
    status_code = 400
    code = "bad_request"


class UnauthorizedError(DomainError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(DomainError):
    status_code = 403
    code = "forbidden"


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class SoldOutError(DomainError):
    status_code = 409
    code = "sold_out"


class IdempotencyConflictError(DomainError):
    status_code = 422
    code = "idempotency_key_reused"


class _RetryableError(DomainError):
    status_code = 503

    def __init__(self, message: str) -> None:
        super().__init__(message, headers={"Retry-After": "1"})


class ServiceBusyError(_RetryableError):
    code = "service_busy"


class EnqueueFailedError(_RetryableError):
    code = "enqueue_failed"


class PoisonMessageError(Exception):
    """A queue message that can never succeed; SQS redrive moves it to the DLQ."""
