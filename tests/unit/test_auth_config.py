import base64
import json

import pytest

from tixlane import container
from tixlane.auth import (
    ATTENDEES,
    ORGANISERS,
    Principal,
    parse_groups,
    principal_from_event,
    require_any_group,
)
from tixlane.config import AuthMode, Settings
from tixlane.errors import ForbiddenError, UnauthorizedError

from .factories import ATTENDEE_SUB, claims, http_event


def _settings(auth_mode: AuthMode = AuthMode.APIGW) -> Settings:
    return Settings(
        env="local",
        table_name="t",
        queue_url="",
        bucket_name="",
        ticket_topic_arn="",
        auth_mode=auth_mode,
        aws_endpoint_url="http://localhost:4566",
        idempotency_ttl_seconds=60,
    )


def _jwt(payload: object) -> str:
    def encode(part: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")

    return f"{encode({'alg': 'RS256'})}.{encode(payload)}.signature"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, set()),
        (42, set()),
        ("", set()),
        ("[]", set()),
        ("[organisers]", {"organisers"}),
        ("[organisers attendees]", {"organisers", "attendees"}),
        ('["organisers","attendees"]', {"organisers", "attendees"}),
        ('["unterminated', {'"unterminated'}),
        ("organisers,attendees", {"organisers", "attendees"}),
        (["attendees", ""], {"attendees"}),
    ],
)
def test_parse_groups_accepts_every_claim_shape(raw: object, expected: set[str]) -> None:
    assert parse_groups(raw) == expected


@pytest.mark.parametrize("groups_format", ["bracketed", "list"])
def test_principal_from_authorizer_claims(groups_format: str) -> None:
    event = http_event(
        "GET",
        "/x",
        route="/x",
        jwt_claims=claims(ATTENDEE_SUB, ATTENDEES, ORGANISERS, groups_format=groups_format),
    )
    principal = principal_from_event(event, _settings())
    assert principal == Principal(sub=ATTENDEE_SUB, groups=frozenset({ATTENDEES, ORGANISERS}))


def test_missing_claims_are_unauthorized_in_apigw_mode() -> None:
    token = _jwt({"sub": "s"})
    event = http_event("GET", "/x", route="/x", headers={"authorization": f"Bearer {token}"})
    with pytest.raises(UnauthorizedError):
        principal_from_event(event, _settings())


@pytest.mark.parametrize(
    "bad_claims",
    [
        {"token_use": "access", "cognito:groups": "[attendees]"},
        {"sub": "", "token_use": "access"},
        {"sub": "s", "token_use": "id"},
    ],
)
def test_claims_must_carry_a_subject_and_be_an_access_token(bad_claims: dict[str, str]) -> None:
    event = http_event("GET", "/x", route="/x", jwt_claims=bad_claims)
    with pytest.raises(UnauthorizedError):
        principal_from_event(event, _settings())


def test_emulator_mode_decodes_bearer_token_without_verification() -> None:
    token = _jwt({"sub": ATTENDEE_SUB, "token_use": "access", "cognito:groups": [ATTENDEES]})
    event = http_event("GET", "/x", route="/x", headers={"Authorization": f"Bearer {token}"})
    principal = principal_from_event(event, _settings(AuthMode.EMULATOR_UNVERIFIED))
    assert principal.groups == {ATTENDEES}


@pytest.mark.parametrize(
    "authorization",
    ["", "Basic abc", "Bearer not-a-jwt", "Bearer a.!!!.c", f"Bearer {_jwt(['not', 'a', 'dict'])}"],
)
def test_emulator_mode_rejects_malformed_tokens(authorization: str) -> None:
    event = http_event("GET", "/x", route="/x", headers={"authorization": authorization})
    with pytest.raises(UnauthorizedError):
        principal_from_event(event, _settings(AuthMode.EMULATOR_UNVERIFIED))


def test_require_any_group() -> None:
    principal = Principal(sub="s", groups=frozenset({ATTENDEES}))
    require_any_group(principal, ATTENDEES, ORGANISERS)
    with pytest.raises(ForbiddenError):
        require_any_group(principal, ORGANISERS)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", "tixlane-x")
    for name in ("TIXLANE_ENV", "TIXLANE_AUTH_MODE", "TIXLANE_AWS_ENDPOINT_URL", "QUEUE_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert settings.auth_mode is AuthMode.APIGW
    assert settings.aws_endpoint_url is None
    assert settings.idempotency_ttl_seconds == 86400


@pytest.mark.parametrize(
    ("env", "endpoint"),
    [("prod", "http://localhost:4566"), ("local", ""), ("", "http://localhost:4566")],
)
def test_unverified_auth_is_refused_outside_the_emulator(
    monkeypatch: pytest.MonkeyPatch, env: str, endpoint: str
) -> None:
    monkeypatch.setenv("TABLE_NAME", "tixlane-x")
    monkeypatch.setenv("TIXLANE_ENV", env)
    monkeypatch.setenv("TIXLANE_AUTH_MODE", "emulator-unverified")
    monkeypatch.setenv("TIXLANE_AWS_ENDPOINT_URL", endpoint)
    with pytest.raises(RuntimeError, match="emulator-unverified"):
        Settings.from_env()


def test_container_points_clients_at_the_emulator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", "tixlane-local")
    monkeypatch.setenv("TIXLANE_ENV", "local")
    monkeypatch.setenv("TIXLANE_AWS_ENDPOINT_URL", "http://localhost:4566")
    container.reset()
    try:
        s3 = container.s3()
        assert s3.meta.endpoint_url == "http://localhost:4566"
        assert s3.meta.config.s3 == {"addressing_style": "path"}
        assert container.sqs().meta.endpoint_url == "http://localhost:4566"
    finally:
        container.reset()
