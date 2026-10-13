"""Runtime configuration read from the Lambda environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class AuthMode(StrEnum):
    # Claims come from the API Gateway JWT authorizer, which has already verified the token.
    APIGW = "apigw"
    # Emulator-only fallback for gap G-01 (docs/EMULATOR_GAPS.md): decode the bearer token
    # WITHOUT verifying its signature. Refused unless TIXLANE_ENV=local and an emulator
    # endpoint is configured.
    EMULATOR_UNVERIFIED = "emulator-unverified"


@dataclass(frozen=True, slots=True)
class Settings:
    env: str
    table_name: str
    queue_url: str
    bucket_name: str
    ticket_topic_arn: str
    auth_mode: AuthMode
    aws_endpoint_url: str | None
    idempotency_ttl_seconds: int

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get("TIXLANE_ENV", "")
        auth_mode = AuthMode(os.environ.get("TIXLANE_AUTH_MODE", AuthMode.APIGW.value))
        endpoint = os.environ.get("TIXLANE_AWS_ENDPOINT_URL") or None
        if auth_mode is AuthMode.EMULATOR_UNVERIFIED and (env != "local" or endpoint is None):
            msg = (
                "TIXLANE_AUTH_MODE=emulator-unverified requires TIXLANE_ENV=local "
                "and TIXLANE_AWS_ENDPOINT_URL to be set"
            )
            raise RuntimeError(msg)
        return cls(
            env=env,
            table_name=os.environ["TABLE_NAME"],
            queue_url=os.environ.get("QUEUE_URL", ""),
            bucket_name=os.environ.get("BUCKET_NAME", ""),
            ticket_topic_arn=os.environ.get("TICKET_TOPIC_ARN", ""),
            auth_mode=auth_mode,
            aws_endpoint_url=endpoint,
            idempotency_ttl_seconds=int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", "86400")),
        )
