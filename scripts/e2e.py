"""End-to-end harness shared by tests/integration, the Makefile and the GitHub workflows.

Sub-commands
  smoke         sign in -> create event -> reserve (+ replay) -> CONFIRMED -> S3 artefact
  prepare-load  users + a large event for the k6 spike; writes a k6 env file
  cleanup       delete the Cognito users that smoke / prepare-load created
  dlq-drill     poison message -> DLQ -> DLQ-depth alarm in ALARM
  snapshot      read-only evidence: alarm states, key metrics, function concurrency

Targets: with --endpoint-url the harness talks to the emulator using dummy credentials.
Without it, it targets real AWS, which is refused unless --allow-real-aws is given AND the
process runs inside GitHub Actions (CLAUDE.md: real AWS only via approved workflows). The
Claude Code PreToolUse hook additionally blocks --allow-real-aws on workstations.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3

AWS_HOST_SUFFIXES = ("amazonaws.com", "amazonaws.com.cn", "aws.amazon.com", "on.aws")
EMULATOR_CREDENTIALS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
# Symbols accepted by the Cognito password policy.
COGNITO_SYMBOLS = "!#%*+-=?@^_~"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def in_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def mask(value: str) -> None:
    """Keep tokens out of GitHub Actions logs."""
    if in_github_actions():
        print(f"::add-mask::{value}", flush=True)


def is_aws_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.endswith(AWS_HOST_SUFFIXES)


# --------------------------------------------------------------------------------------------
# Stack outputs
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stack:
    region: str
    api_url: str
    user_pool_id: str
    client_id: str
    table_name: str
    queue_url: str
    dlq_url: str
    bucket_name: str
    dlq_alarm_name: str
    visibility_timeout: int
    alarm_names: list[str] = field(default_factory=list)
    function_names: dict[str, str] = field(default_factory=dict)
    endpoint_url: str | None = None

    @classmethod
    def from_outputs(
        cls, path: Path, *, endpoint_url: str | None, api_url: str | None = None
    ) -> Stack:
        raw = json.loads(path.read_text())

        def value(name: str) -> Any:
            return raw[name]["value"]

        return cls(
            region=value("region"),
            api_url=(api_url or value("api_endpoint")).rstrip("/"),
            user_pool_id=value("user_pool_id"),
            client_id=value("user_pool_client_id"),
            table_name=value("table_name"),
            queue_url=value("queue_url"),
            dlq_url=value("dlq_url"),
            bucket_name=value("bucket_name"),
            dlq_alarm_name=value("dlq_alarm_name"),
            visibility_timeout=int(value("queue_visibility_timeout_seconds")),
            alarm_names=list(value("alarm_names")),
            function_names=dict(value("function_names")),
            endpoint_url=endpoint_url,
        )

    @property
    def is_emulator(self) -> bool:
        return self.endpoint_url is not None

    def client(self, service: str) -> Any:
        if self.endpoint_url:
            return boto3.client(
                service,
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                **EMULATOR_CREDENTIALS,
            )
        return boto3.client(service, region_name=self.region)


def ensure_target_allowed(stack: Stack, *, allow_real_aws: bool) -> None:
    if stack.endpoint_url is not None:
        for url in (stack.endpoint_url, stack.api_url):
            if is_aws_url(url):
                msg = (
                    f"emulator mode, but {url} is an AWS endpoint. Set TIXLANE_API_URL / "
                    "--api-url to the emulator's invoke URL (EMULATOR_GAPS G-03)."
                )
                raise SystemExit(msg)
        return
    if not (allow_real_aws and in_github_actions()):
        msg = (
            "Refusing to call real AWS: only the approval-gated GitHub workflows may do that "
            "(--allow-real-aws inside GitHub Actions). Use --endpoint-url for the emulator."
        )
        raise SystemExit(msg)


# --------------------------------------------------------------------------------------------
# HTTP API client
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: dict[str, str]
    body: Any


class Api:
    def __init__(self, base_url: str, *, timeout: float = 15.0) -> None:
        if urlparse(base_url).scheme not in {"http", "https"}:
            msg = f"unsupported API URL {base_url!r}"
            raise ValueError(msg)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> ApiResponse:
        request_headers = {"content-type": "application/json", **(headers or {})}
        if token:
            request_headers["authorization"] = f"Bearer {token}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=request_headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, raw_headers, raw = response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            status, raw_headers, raw = error.code, error.headers, error.read()
        try:
            parsed: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw.decode(errors="replace")
        return ApiResponse(status, {k.lower(): v for k, v in raw_headers.items()}, parsed)

    def create_event(self, token: str, *, capacity: int, name: str = "E2E gig") -> ApiResponse:
        starts_at = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        body = {"name": name, "venue": "E2E Hall", "starts_at": starts_at, "capacity": capacity}
        return self.call("POST", "/events", token=token, body=body)

    def get_event(self, token: str, event_id: str) -> ApiResponse:
        return self.call("GET", f"/events/{event_id}", token=token)

    def reserve(self, token: str, event_id: str, key: str, quantity: int = 1) -> ApiResponse:
        return self.call(
            "POST",
            f"/events/{event_id}/reservations",
            token=token,
            body={"quantity": quantity},
            headers={"idempotency-key": key},
        )

    def get_reservation(self, token: str, reservation_id: str) -> ApiResponse:
        return self.call("GET", f"/reservations/{reservation_id}", token=token)


def expect(response: ApiResponse, status: int, what: str) -> Any:
    if response.status != status:
        msg = f"{what}: expected HTTP {status}, got {response.status}: {response.body}"
        raise AssertionError(msg)
    return response.body


def wait_for_status(
    api: Api, token: str, reservation_id: str, status: str, *, timeout: float = 30.0
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    while True:
        body = expect(api.get_reservation(token, reservation_id), 200, "GET reservation")
        elapsed = time.monotonic() - started
        if body["status"] == status:
            return body, elapsed
        if elapsed > timeout:
            msg = f"reservation {reservation_id} still {body['status']} after {timeout:.0f}s"
            raise AssertionError(msg)
        time.sleep(0.5)


# --------------------------------------------------------------------------------------------
# Cognito test users
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TestUser:
    username: str
    group: str
    token: str = field(repr=False)


def strong_password(length: int = 24) -> str:
    classes = [string.ascii_lowercase, string.ascii_uppercase, string.digits, COGNITO_SYMBOLS]
    alphabet = "".join(classes)
    chars = [secrets.choice(chars) for chars in classes]
    chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def create_user(stack: Stack, group: str) -> TestUser:
    cognito = stack.client("cognito-idp")
    username = f"e2e-{group}-{uuid.uuid4().hex[:12]}@example.com"
    password = strong_password()
    mask(password)
    cognito.admin_create_user(
        UserPoolId=stack.user_pool_id,
        Username=username,
        MessageAction="SUPPRESS",
        UserAttributes=[
            {"Name": "email", "Value": username},
            {"Name": "email_verified", "Value": "true"},
        ],
    )
    cognito.admin_set_user_password(
        UserPoolId=stack.user_pool_id, Username=username, Password=password, Permanent=True
    )
    cognito.admin_add_user_to_group(
        UserPoolId=stack.user_pool_id, Username=username, GroupName=group
    )
    # Sign in after joining the group so the access token carries cognito:groups.
    auth = cognito.initiate_auth(
        ClientId=stack.client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    token = auth["AuthenticationResult"]["AccessToken"]
    mask(token)
    return TestUser(username=username, group=group, token=token)


def delete_users(stack: Stack, usernames: list[str]) -> None:
    cognito = stack.client("cognito-idp")
    for username in usernames:
        try:
            cognito.admin_delete_user(UserPoolId=stack.user_pool_id, Username=username)
        except cognito.exceptions.UserNotFoundException:
            continue


def record_users(path: Path, users: list[TestUser]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([user.username for user in users], indent=2) + "\n")


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))


# --------------------------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------------------------


def smoke(stack: Stack, evidence_dir: Path, k6_env_file: Path | None) -> dict[str, Any]:
    api = Api(stack.api_url)
    started_at = utc_now()
    organiser = create_user(stack, "organisers")
    attendee = create_user(stack, "attendees")
    record_users(evidence_dir / "users.json", [organiser, attendee])

    event = expect(api.create_event(organiser.token, capacity=5), 201, "POST /events")
    expect(api.get_event(attendee.token, event["event_id"]), 200, "GET /events/{id}")
    key = f"smoke-{uuid.uuid4().hex}"
    created = expect(api.reserve(attendee.token, event["event_id"], key, 2), 202, "reserve")
    replay = api.reserve(attendee.token, event["event_id"], key, 2)
    expect(replay, 202, "replay")
    if replay.body["reservation_id"] != created["reservation_id"]:
        raise AssertionError("idempotent replay returned a different reservation")
    confirmed, seconds = wait_for_status(
        api, attendee.token, created["reservation_id"], "CONFIRMED", timeout=30
    )
    ticket_object = stack.client("s3").get_object(
        Bucket=stack.bucket_name, Key=confirmed["ticket_key"]
    )
    ticket = json.loads(ticket_object["Body"].read())
    unauthenticated = api.call("GET", f"/events/{event['event_id']}")

    result = {
        "scenario": "smoke",
        "target": "emulator" if stack.is_emulator else "aws",
        "started_at": started_at,
        "finished_at": utc_now(),
        "event_id": event["event_id"],
        "reservation_id": created["reservation_id"],
        "confirmation_seconds": round(seconds, 2),
        "ticket_key": confirmed["ticket_key"],
        "ticket_codes": len(ticket["ticket_codes"]),
        "checks": {
            "create_event_201": True,
            "reserve_202": True,
            "replay_same_reservation": True,
            "replay_header": replay.headers.get("idempotent-replayed") == "true",
            "confirmed_within_30s": seconds <= 30,
            "artefact_matches": ticket["reservation_id"] == created["reservation_id"],
            "unauthenticated_401": unauthenticated.status == 401,
        },
    }
    result["passed"] = all(result["checks"].values())
    write_json(evidence_dir / "smoke.json", result)
    if k6_env_file:
        write_env_file(
            k6_env_file,
            {
                "API_URL": stack.api_url,
                "ORGANISER_TOKEN": organiser.token,
                "ATTENDEE_TOKEN": attendee.token,
            },
        )
    return result


def prepare_load(
    stack: Stack, evidence_dir: Path, k6_env_file: Path, capacity: int
) -> dict[str, Any]:
    api = Api(stack.api_url)
    organiser = create_user(stack, "organisers")
    attendee = create_user(stack, "attendees")
    record_users(evidence_dir / "users.json", [organiser, attendee])
    event = expect(
        api.create_event(organiser.token, capacity=capacity, name="k6 spike"), 201, "POST /events"
    )
    write_env_file(
        k6_env_file,
        {"API_URL": stack.api_url, "ATTENDEE_TOKEN": attendee.token, "EVENT_ID": event["event_id"]},
    )
    return {"scenario": "prepare-load", "event_id": event["event_id"], "capacity": capacity}


@dataclass
class DrillResult:
    marker: str
    sent_at: str
    dlq_seen_at: str | None = None
    dlq_seconds: float | None = None
    alarm_state: str | None = None
    alarm_at: str | None = None
    alarm_seconds: float | None = None
    purged: bool = False


def dlq_drill(
    stack: Stack, *, dlq_timeout: float, alarm_timeout: float, purge_after: bool
) -> DrillResult:
    sqs = stack.client("sqs")
    cloudwatch = stack.client("cloudwatch")
    marker = f"tixlane-dlq-drill-{uuid.uuid4().hex}"
    result = DrillResult(marker=marker, sent_at=utc_now())
    started = time.monotonic()
    sqs.send_message(QueueUrl=stack.queue_url, MessageBody=f"poison:{marker}")

    deadline = started + dlq_timeout
    while time.monotonic() < deadline and result.dlq_seen_at is None:
        # VisibilityTimeout=0: peeking must not hide the message from the alarm metric.
        messages = sqs.receive_message(
            QueueUrl=stack.dlq_url, MaxNumberOfMessages=10, WaitTimeSeconds=5, VisibilityTimeout=0
        ).get("Messages", [])
        if any(marker in message["Body"] for message in messages):
            result.dlq_seen_at = utc_now()
            result.dlq_seconds = round(time.monotonic() - started, 1)

    if result.dlq_seen_at is not None:
        deadline = time.monotonic() + alarm_timeout
        while time.monotonic() < deadline:
            alarm = cloudwatch.describe_alarms(AlarmNames=[stack.dlq_alarm_name])["MetricAlarms"][0]
            result.alarm_state = alarm["StateValue"]
            if result.alarm_state == "ALARM":
                result.alarm_at = utc_now()
                result.alarm_seconds = round(time.monotonic() - started, 1)
                break
            time.sleep(10)

    if purge_after:
        sqs.purge_queue(QueueUrl=stack.dlq_url)
        result.purged = True
    return result


def snapshot(stack: Stack, evidence_dir: Path, hours: int) -> dict[str, Any]:
    """Read-only. Every call here is a Describe/Get/List."""
    cloudwatch = stack.client("cloudwatch")
    lambda_client = stack.client("lambda")
    end = datetime.now(UTC)
    start = end - timedelta(hours=hours)

    alarms = cloudwatch.describe_alarms(AlarmNames=stack.alarm_names)["MetricAlarms"]
    queries = []
    for index, (key, name) in enumerate(sorted(stack.function_names.items())):
        for metric, stat in (("Invocations", "Sum"), ("Errors", "Sum"), ("Throttles", "Sum"),
                             ("Duration", "p95")):  # fmt: skip
            queries.append(
                {
                    "Id": f"m{index}{metric.lower()}",
                    "Label": f"{key} {metric} {stat}",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/Lambda",
                            "MetricName": metric,
                            "Dimensions": [{"Name": "FunctionName", "Value": name}],
                        },
                        "Period": 300,
                        "Stat": stat,
                    },
                }
            )
    metrics = cloudwatch.get_metric_data(MetricDataQueries=queries, StartTime=start, EndTime=end)
    concurrency = {
        key: lambda_client.get_function_concurrency(FunctionName=name).get(
            "ReservedConcurrentExecutions"
        )
        for key, name in stack.function_names.items()
    }
    result = {
        "scenario": "snapshot",
        "taken_at": utc_now(),
        "window_hours": hours,
        "alarms": [
            {
                "name": alarm["AlarmName"],
                "state": alarm["StateValue"],
                "reason": alarm.get("StateReason"),
                "updated": str(alarm.get("StateUpdatedTimestamp")),
            }
            for alarm in alarms
        ],
        "lambda_metrics": {
            series["Label"]: {"total": sum(series["Values"]), "points": len(series["Values"])}
            for series in metrics["MetricDataResults"]
        },
        "reserved_concurrency": concurrency,
    }
    write_json(evidence_dir / "snapshot.json", result)
    return result


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--outputs", type=Path, required=True, help="`tofu output -json` file")
    parser.add_argument("--endpoint-url", help="emulator endpoint; omit only inside the workflows")
    parser.add_argument("--api-url", help="override the API invoke URL (EMULATOR_GAPS G-03)")
    parser.add_argument("--allow-real-aws", action="store_true", help="GitHub Actions only")
    parser.add_argument("--evidence-dir", type=Path, default=Path("build/evidence"))
    sub = parser.add_subparsers(dest="command", required=True)

    smoke_cmd = sub.add_parser("smoke")
    smoke_cmd.add_argument("--k6-env-file", type=Path)

    load_cmd = sub.add_parser("prepare-load")
    load_cmd.add_argument("--k6-env-file", type=Path, required=True)
    load_cmd.add_argument("--capacity", type=int, default=5000)

    cleanup_cmd = sub.add_parser("cleanup")
    cleanup_cmd.add_argument("--users-file", type=Path, required=True)

    drill_cmd = sub.add_parser("dlq-drill")
    drill_cmd.add_argument("--dlq-timeout", type=float, help="default: 4x visibility timeout")
    drill_cmd.add_argument("--alarm-timeout", type=float, default=300)
    drill_cmd.add_argument("--purge-after", action="store_true")

    snapshot_cmd = sub.add_parser("snapshot")
    snapshot_cmd.add_argument("--hours", type=int, default=6)

    args = parser.parse_args(argv)
    stack = Stack.from_outputs(args.outputs, endpoint_url=args.endpoint_url, api_url=args.api_url)
    ensure_target_allowed(stack, allow_real_aws=args.allow_real_aws)

    if args.command == "smoke":
        result: Any = smoke(stack, args.evidence_dir, args.k6_env_file)
    elif args.command == "prepare-load":
        result = prepare_load(stack, args.evidence_dir, args.k6_env_file, args.capacity)
    elif args.command == "cleanup":
        usernames = json.loads(args.users_file.read_text()) if args.users_file.exists() else []
        delete_users(stack, usernames)
        result = {"scenario": "cleanup", "deleted": len(usernames)}
    elif args.command == "dlq-drill":
        drill = dlq_drill(
            stack,
            dlq_timeout=args.dlq_timeout or 4 * stack.visibility_timeout + 30,
            alarm_timeout=args.alarm_timeout,
            purge_after=args.purge_after,
        )
        result = asdict(drill)
        write_json(args.evidence_dir / "dlq-drill.json", result)
    else:
        result = snapshot(stack, args.evidence_dir, args.hours)

    print(json.dumps(result, indent=2, default=str))
    passed = result.get("passed", True)
    if args.command == "dlq-drill":
        passed = result["dlq_seen_at"] is not None and result["alarm_state"] == "ALARM"
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
