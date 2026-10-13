"""Builders for HTTP API (payload v2) events, SQS events and Lambda contexts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# Mirrors infra/modules/data/main.tf - keep the two in sync.
TABLE_NAME = "tixlane-test"
TABLE_DEFINITION: dict[str, Any] = {
    "TableName": TABLE_NAME,
    "BillingMode": "PAY_PER_REQUEST",
    "AttributeDefinitions": [
        {"AttributeName": name, "AttributeType": "S"} for name in ("PK", "SK", "GSI1PK", "GSI1SK")
    ],
    "KeySchema": [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "GSI1",
            "KeySchema": [
                {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ],
}

ORGANISER_SUB = "0f0f0f0f-0000-4000-8000-000000000001"
OTHER_ORGANISER_SUB = "0f0f0f0f-0000-4000-8000-000000000002"
ATTENDEE_SUB = "0a0a0a0a-0000-4000-8000-000000000001"
OTHER_ATTENDEE_SUB = "0a0a0a0a-0000-4000-8000-000000000002"


def future(days: int = 30) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def event_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Synthwave Night",
        "venue": "Hall 4",
        "starts_at": future().isoformat(),
        "capacity": 10,
    }
    body.update(overrides)
    return body


def claims(sub: str, *groups: str, groups_format: str = "bracketed") -> dict[str, Any]:
    """Claims as the HTTP API JWT authorizer presents them (array claims flattened)."""
    value: Any = list(groups) if groups_format == "list" else "[" + " ".join(groups) + "]"
    return {"sub": sub, "token_use": "access", "client_id": "test-client", "cognito:groups": value}


def http_event(
    method: str,
    path: str,
    *,
    route: str,
    path_params: dict[str, str] | None = None,
    body: Any = None,
    raw_body: str | None = None,
    headers: dict[str, str] | None = None,
    jwt_claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_context: dict[str, Any] = {
        "accountId": "123456789012",
        "apiId": "testapi",
        "domainName": "testapi.execute-api.eu-central-1.amazonaws.com",
        "domainPrefix": "testapi",
        "http": {
            "method": method,
            "path": path,
            "protocol": "HTTP/1.1",
            "sourceIp": "203.0.113.10",
            "userAgent": "pytest",
        },
        "requestId": "req-test",
        "routeKey": f"{method} {route}",
        "stage": "$default",
        "time": "13/Oct/2026:12:00:00 +0000",
        "timeEpoch": 1791892800000,
    }
    if jwt_claims is not None:
        request_context["authorizer"] = {"jwt": {"claims": jwt_claims, "scopes": None}}
    if raw_body is None and body is not None:
        raw_body = json.dumps(body)
    return {
        "version": "2.0",
        "routeKey": f"{method} {route}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json", **(headers or {})},
        "requestContext": request_context,
        "pathParameters": path_params,
        "body": raw_body,
        "isBase64Encoded": False,
    }


def sqs_event(messages: list[dict[str, Any]], queue_arn: str) -> dict[str, Any]:
    """Turn ReceiveMessage results into the event Lambda's SQS poller would deliver."""
    return {
        "Records": [
            {
                "messageId": message["MessageId"],
                "receiptHandle": message.get("ReceiptHandle", "handle"),
                "body": message["Body"],
                "attributes": {
                    "ApproximateReceiveCount": "1",
                    "SentTimestamp": "1791892800000",
                    "SenderId": "AIDATESTSENDER",
                    "ApproximateFirstReceiveTimestamp": "1791892800000",
                },
                "messageAttributes": {},
                "md5OfBody": message.get("MD5OfBody", ""),
                "eventSource": "aws:sqs",
                "eventSourceARN": queue_arn,
                "awsRegion": "eu-central-1",
            }
            for message in messages
        ]
    }


@dataclass
class FakeLambdaContext:
    function_name: str = "tixlane-test"
    function_version: str = "$LATEST"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:eu-central-1:123456789012:function:tixlane-test"
    aws_request_id: str = "00000000-0000-4000-8000-000000000000"
    log_group_name: str = "/aws/lambda/tixlane-test"
    log_stream_name: str = "2026/10/13/[$LATEST]test"

    def get_remaining_time_in_millis(self) -> int:
        return 10_000


def invoke(handler: Any, event: dict[str, Any]) -> tuple[int, Any, dict[str, str]]:
    """Call an HTTP handler and return (status, parsed JSON body, headers)."""
    response = handler(event, FakeLambdaContext())
    body = json.loads(response["body"]) if response.get("body") else None
    return response["statusCode"], body, response.get("headers") or {}
