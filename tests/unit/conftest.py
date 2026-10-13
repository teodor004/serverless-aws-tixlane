"""Moto-backed AWS resources mirroring infra/modules/{data,messaging,storage}."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import boto3
import pytest
from moto import mock_aws

from tixlane import container
from tixlane.auth import ATTENDEES, ORGANISERS, Principal
from tixlane.models import Event, EventCreate

from .factories import (
    ATTENDEE_SUB,
    ORGANISER_SUB,
    OTHER_ATTENDEE_SUB,
    TABLE_DEFINITION,
    TABLE_NAME,
    future,
)

REGION = "eu-central-1"


@dataclass(frozen=True)
class AwsResources:
    table_name: str
    queue_url: str
    queue_arn: str
    dlq_url: str
    bucket_name: str
    topic_arn: str
    sns_capture_url: str

    def drain(self, queue_url: str | None = None) -> list[dict[str, Any]]:
        sqs = boto3.client("sqs", region_name=REGION)
        messages: list[dict[str, Any]] = []
        while True:
            batch = sqs.receive_message(
                QueueUrl=queue_url or self.queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0
            ).get("Messages", [])
            if not batch:
                return messages
            messages.extend(batch)

    def published(self) -> list[dict[str, Any]]:
        return [json.loads(message["Body"]) for message in self.drain(self.sns_capture_url)]


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[AwsResources]:
    monkeypatch.setenv("TIXLANE_ENV", "test")
    monkeypatch.setenv("TIXLANE_AUTH_MODE", "apigw")
    monkeypatch.delenv("TIXLANE_AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        boto3.client("dynamodb", region_name=REGION).create_table(**TABLE_DEFINITION)

        sqs = boto3.client("sqs", region_name=REGION)
        dlq_url = sqs.create_queue(QueueName="tixlane-test-tickets-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]
        queue_url = sqs.create_queue(
            QueueName="tixlane-test-tickets",
            Attributes={
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"}
                )
            },
        )["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]

        bucket = "tixlane-test-artefacts"
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION}
        )

        sns = boto3.client("sns", region_name=REGION)
        topic_arn = sns.create_topic(Name="tixlane-test-ticket-issued")["TopicArn"]
        capture_url = sqs.create_queue(QueueName="tixlane-test-sns-capture")["QueueUrl"]
        capture_arn = sqs.get_queue_attributes(QueueUrl=capture_url, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]
        sns.subscribe(
            TopicArn=topic_arn,
            Protocol="sqs",
            Endpoint=capture_arn,
            Attributes={"RawMessageDelivery": "true"},
        )

        monkeypatch.setenv("TABLE_NAME", TABLE_NAME)
        monkeypatch.setenv("QUEUE_URL", queue_url)
        monkeypatch.setenv("BUCKET_NAME", bucket)
        monkeypatch.setenv("TICKET_TOPIC_ARN", topic_arn)
        container.reset()
        yield AwsResources(
            table_name=TABLE_NAME,
            queue_url=queue_url,
            queue_arn=queue_arn,
            dlq_url=dlq_url,
            bucket_name=bucket,
            topic_arn=topic_arn,
            sns_capture_url=capture_url,
        )
    container.reset()


@pytest.fixture
def organiser() -> Principal:
    return Principal(sub=ORGANISER_SUB, groups=frozenset({ORGANISERS}))


@pytest.fixture
def attendee() -> Principal:
    return Principal(sub=ATTENDEE_SUB, groups=frozenset({ATTENDEES}))


@pytest.fixture
def other_attendee() -> Principal:
    return Principal(sub=OTHER_ATTENDEE_SUB, groups=frozenset({ATTENDEES}))


@pytest.fixture
def make_event(aws: AwsResources, organiser: Principal) -> Callable[..., Event]:
    def _make(capacity: int = 10, name: str = "Synthwave Night") -> Event:
        payload = EventCreate(name=name, venue="Hall 4", starts_at=future(), capacity=capacity)
        return container.event_service().create(organiser, payload)

    return _make
