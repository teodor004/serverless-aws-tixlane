"""P3: poison message -> 3 receives -> DLQ -> DLQ-depth alarm."""

from __future__ import annotations

import os

import pytest

import e2e

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# EMULATOR_GAPS G-05: set TIXLANE_EMULATOR_ALARMS=0 if the emulator never evaluates alarms;
# the test then checks the alarm definition instead of its state.
EMULATOR_EVALUATES_ALARMS = os.environ.get("TIXLANE_EMULATOR_ALARMS", "1") == "1"


def test_poison_message_reaches_dlq_and_raises_alarm(stack: e2e.Stack) -> None:
    result = e2e.dlq_drill(
        stack,
        dlq_timeout=4 * stack.visibility_timeout + 30,
        alarm_timeout=180 if EMULATOR_EVALUATES_ALARMS else 0,
        purge_after=True,
    )
    assert result.dlq_seen_at is not None, "poison message never reached the DLQ"

    if EMULATOR_EVALUATES_ALARMS:
        assert result.alarm_state == "ALARM"
        return

    [alarm] = stack.client("cloudwatch").describe_alarms(AlarmNames=[stack.dlq_alarm_name])[
        "MetricAlarms"
    ]
    assert alarm["Namespace"] == "AWS/SQS"
    assert alarm["MetricName"] == "ApproximateNumberOfMessagesVisible"
    assert alarm["Threshold"] == 0
    assert alarm["ComparisonOperator"] == "GreaterThanThreshold"
    assert {"Name": "QueueName", "Value": stack.dlq_url.rsplit("/", 1)[-1]} in alarm["Dimensions"]
    assert alarm["AlarmActions"], "alarm must notify ops-alerts"
