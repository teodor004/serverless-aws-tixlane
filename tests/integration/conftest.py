"""Integration fixtures. These tests only ever run against the local emulator.

Run with `make up && make local-apply && make itest`. The stack is read from the
`tofu output -json` file written by `make local-apply`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import e2e  # noqa: E402 - scripts/ is not a package


@pytest.fixture(scope="session")
def stack() -> e2e.Stack:
    outputs = Path(os.environ.get("TIXLANE_OUTPUTS", ROOT / "build" / "local-outputs.json"))
    if not outputs.exists():
        pytest.fail(f"{outputs} not found - run `make local-apply` first")
    stack = e2e.Stack.from_outputs(
        outputs,
        endpoint_url=os.environ.get("TIXLANE_ENDPOINT_URL", "http://localhost:4566"),
        api_url=os.environ.get("TIXLANE_API_URL"),
    )
    # Raises if any URL points at AWS: the suite must never leave the emulator.
    e2e.ensure_target_allowed(stack, allow_real_aws=False)
    return stack


@pytest.fixture(scope="session")
def api(stack: e2e.Stack) -> e2e.Api:
    return e2e.Api(stack.api_url)


@pytest.fixture(scope="session")
def users(stack: e2e.Stack) -> Iterator[dict[str, e2e.TestUser]]:
    created = {
        "organiser": e2e.create_user(stack, "organisers"),
        "other_organiser": e2e.create_user(stack, "organisers"),
        "attendee": e2e.create_user(stack, "attendees"),
        "other_attendee": e2e.create_user(stack, "attendees"),
    }
    yield created
    e2e.delete_users(stack, [user.username for user in created.values()])


@pytest.fixture
def organiser(users: dict[str, e2e.TestUser]) -> e2e.TestUser:
    return users["organiser"]


@pytest.fixture
def attendee(users: dict[str, e2e.TestUser]) -> e2e.TestUser:
    return users["attendee"]
