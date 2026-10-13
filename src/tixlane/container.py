"""Lazily built singletons, reused across warm invocations of one execution environment."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import cache
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

from tixlane.config import Settings
from tixlane.repository import Repository
from tixlane.services import EventService, ReservationService, SqsReservationQueue, TicketIssuer

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_sns import SNSClient
    from mypy_boto3_sqs import SQSClient

_BOTO_CONFIG = Config(
    connect_timeout=2,
    read_timeout=5,
    retries={"mode": "standard", "max_attempts": 3},
)


def utc_now() -> datetime:
    return datetime.now(UTC)


@cache
def settings() -> Settings:
    return Settings.from_env()


@cache
def dynamodb() -> DynamoDBClient:
    return boto3.client("dynamodb", endpoint_url=settings().aws_endpoint_url, config=_BOTO_CONFIG)


@cache
def sqs() -> SQSClient:
    return boto3.client("sqs", endpoint_url=settings().aws_endpoint_url, config=_BOTO_CONFIG)


@cache
def sns() -> SNSClient:
    return boto3.client("sns", endpoint_url=settings().aws_endpoint_url, config=_BOTO_CONFIG)


@cache
def s3() -> S3Client:
    endpoint = settings().aws_endpoint_url
    # Emulators serve S3 on a single host, so virtual-hosted bucket names would not resolve.
    config = _BOTO_CONFIG
    if endpoint:
        config = config.merge(Config(s3={"addressing_style": "path"}))
    return boto3.client("s3", endpoint_url=endpoint, config=config)


@cache
def repository() -> Repository:
    return Repository(dynamodb(), settings().table_name)


@cache
def event_service() -> EventService:
    return EventService(repository(), clock=utc_now)


@cache
def reservation_service() -> ReservationService:
    return ReservationService(
        repository(),
        SqsReservationQueue(sqs(), settings().queue_url),
        clock=utc_now,
        idempotency_ttl_seconds=settings().idempotency_ttl_seconds,
    )


@cache
def ticket_issuer() -> TicketIssuer:
    return TicketIssuer(
        repository(),
        s3(),
        sns(),
        bucket=settings().bucket_name,
        topic_arn=settings().ticket_topic_arn,
        clock=utc_now,
    )


def reset() -> None:
    """Drop every cached object (tests re-point clients at a fresh Moto backend)."""
    for factory in (
        settings,
        dynamodb,
        sqs,
        sns,
        s3,
        repository,
        event_service,
        reservation_service,
        ticket_issuer,
    ):
        factory.cache_clear()
