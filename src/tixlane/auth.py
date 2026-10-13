"""Caller identity and group enforcement from Cognito JWT claims."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tixlane.config import AuthMode, Settings
from tixlane.errors import ForbiddenError, UnauthorizedError

ORGANISERS = "organisers"
ATTENDEES = "attendees"

_JWS_COMPACT_SEGMENTS = 3


@dataclass(frozen=True, slots=True)
class Principal:
    sub: str
    groups: frozenset[str]


def parse_groups(raw: object) -> frozenset[str]:
    """Normalise ``cognito:groups``.

    The HTTP API JWT authorizer flattens array claims into a string such as
    ``"[organisers attendees]"`` (V-027 in docs/VERIFICATION_LOG.md); a raw token carries a
    JSON list. Accept both, plus a JSON-encoded list string.
    """
    if isinstance(raw, list):
        return frozenset(str(group) for group in raw if str(group))
    if not isinstance(raw, str):
        return frozenset()
    text = raw.strip()
    if text.startswith("[") and '"' in text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return parse_groups(parsed)
    text = text.removeprefix("[").removesuffix("]")
    return frozenset(part for part in re.split(r"[\s,]+", text) if part)


def principal_from_event(event: Mapping[str, Any], settings: Settings) -> Principal:
    claims = _authorizer_claims(event)
    if claims is None and settings.auth_mode is AuthMode.EMULATOR_UNVERIFIED:
        claims = _unverified_bearer_claims(event)
    if not claims:
        raise UnauthorizedError("Missing or invalid credentials")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise UnauthorizedError("Token has no subject")
    # Only Cognito access tokens are accepted; ID tokens would also pass the JWT authorizer.
    if claims.get("token_use", "access") != "access":
        raise UnauthorizedError("An access token is required")
    return Principal(sub=sub, groups=parse_groups(claims.get("cognito:groups")))


def require_any_group(principal: Principal, *groups: str) -> None:
    if principal.groups.isdisjoint(groups):
        raise ForbiddenError(f"Requires membership of: {', '.join(sorted(groups))}")


def _authorizer_claims(event: Mapping[str, Any]) -> dict[str, Any] | None:
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims")
    return claims if isinstance(claims, dict) and claims else None


def _unverified_bearer_claims(event: Mapping[str, Any]) -> dict[str, Any] | None:
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    scheme, _, token = str(headers.get("authorization", "")).partition(" ")
    segments = token.split(".")
    if scheme.lower() != "bearer" or len(segments) != _JWS_COMPACT_SEGMENTS:
        return None
    payload = segments[1]
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (binascii.Error, ValueError):
        return None
    return claims if isinstance(claims, dict) else None
