"""Pure policy helpers for operator bearer authentication.

Configuration strength is enforced explicitly at process startup, not while
this module is imported.  Request dependencies deliberately keep accepting
short injected test tokens once a process has passed its startup gate.
"""

from __future__ import annotations

import hashlib
import hmac
import string
import unicodedata
from collections.abc import Iterable
from typing import Literal


MIN_TOKEN_LENGTH = 32
MIN_DISTINCT_CHARACTERS = 8
MAX_DATABASE_INTEGER = 2_147_483_647
_TOKEN_ALPHABET = frozenset(string.ascii_letters + string.digits + "_-")
_DENYLIST = frozenset(
    {
        "123",
        "password",
        "changeme",
        "change-me",
        "secret",
        "admin",
        "test",
        "dev",
        "development",
    }
)
_FINGERPRINT_DOMAIN = b"hcga.operator-auth-token-set.v1\x00"


class OperatorAuthConfigurationError(RuntimeError):
    """Operator auth cannot safely start; never carries token material."""


def parse_strong_tokens(raw: str) -> tuple[str, ...]:
    """Parse an exact comma-delimited operator-token configuration.

    An empty string is represented as no configured tokens so the separate
    startup-mode decision can allow an explicit local-development mode.
    Every non-empty member must independently satisfy the strength floor.
    """

    if raw == "":
        return ()

    parts = raw.split(",")
    parsed: list[str] = []
    for index, part in enumerate(parts, start=1):
        token = part.strip()
        invalid = (
            not token
            or token != part
            or len(token) < MIN_TOKEN_LENGTH
            or not token.isascii()
            or any(character not in _TOKEN_ALPHABET for character in token)
            or any(
                character.isspace()
                or unicodedata.category(character).startswith("C")
                for character in token
            )
            or len(set(token)) < MIN_DISTINCT_CHARACTERS
            or token.casefold() in _DENYLIST
        )
        if invalid:
            raise OperatorAuthConfigurationError(
                f"AUTH_TOKEN member {index} is structurally weak or malformed"
            )
        if token in parsed:
            raise OperatorAuthConfigurationError(
                f"AUTH_TOKEN member {index} duplicates an earlier member"
            )
        parsed.append(token)
    return tuple(parsed)


def require_startup_auth(
    raw: str, *, allow_insecure_local: bool
) -> Literal["token", "local-dev"]:
    """Validate startup configuration and return its explicit auth mode."""

    tokens = parse_strong_tokens(raw)
    if tokens:
        return "token"
    if allow_insecure_local:
        return "local-dev"
    raise OperatorAuthConfigurationError(
        "AUTH_TOKEN is required unless ALLOW_INSECURE_LOCAL_AUTH=true"
    )


def runtime_token_set_fingerprint(
    raw: str, *, allow_insecure_local: bool
) -> str | None:
    """Return non-disclosing runtime evidence for the configured token set.

    Token members are sorted before hashing so a strong-to-strong rotation
    overlap has one stable set identity regardless of comma order. The domain
    prefix prevents this digest from being confused with a vault/file hash.
    Invalid startup configuration returns ``None`` because capability blobs are
    created at import time; the executable startup gate remains the authority
    that refuses the process. Explicit anonymous development is distinguishable
    from invalid/missing production configuration without pretending it has a
    token fingerprint.
    """

    try:
        mode = require_startup_auth(
            raw, allow_insecure_local=allow_insecure_local
        )
    except OperatorAuthConfigurationError:
        return None
    if mode == "local-dev":
        return "local-dev"
    members = sorted(parse_strong_tokens(raw))
    canonical = b"\x00".join(member.encode("ascii") for member in members)
    return "sha256:" + hashlib.sha256(
        _FINGERPRINT_DOMAIN + canonical
    ).hexdigest()


def rotation_version_floors(
    *,
    prior_floor: int | None,
    target_code_version: int,
    reported_code_versions: Iterable[int],
    configured_overrides: Iterable[int],
) -> tuple[int, int]:
    """Return ``(final_floor, temporary_floor)`` for an auth hard cut.

    The final floor never drops below the deployed target. The temporary
    all-claim fence strictly exceeds every version the preflight proved could
    start, including ``WORKER_CODE_VERSION`` overrides. PostgreSQL ``Integer``
    is signed 32-bit, so an unbounded/invalid value or a maximum value that
    cannot be exceeded aborts instead of wrapping or weakening the fence.
    """

    def checked(value: object, *, field: str) -> int:
        if (
            type(value) is not int
            or value < 0
            or value > MAX_DATABASE_INTEGER
        ):
            raise OperatorAuthConfigurationError(
                f"{field} is not a bounded PostgreSQL integer version"
            )
        return value

    prior = (
        0
        if prior_floor is None
        else checked(prior_floor, field="prior_floor")
    )
    target = checked(target_code_version, field="target_code_version")
    reported = [
        checked(value, field="reported_code_version")
        for value in reported_code_versions
    ]
    overrides = [
        checked(value, field="configured_override")
        for value in configured_overrides
    ]
    final_floor = max(prior, target)
    highest_known = max([final_floor, *reported, *overrides])
    if highest_known == MAX_DATABASE_INTEGER:
        raise OperatorAuthConfigurationError(
            "known code version leaves no representable temporary fence"
        )
    return final_floor, highest_known + 1


def constant_time_token_match(
    provided: str, candidates: Iterable[str]
) -> bool:
    """Compare an exact presented token against every candidate.

    Bytes make ordinary non-ASCII input a safe mismatch against the configured
    ASCII token set.  Ill-formed Unicode (for example, an injected lone
    surrogate) cannot be UTF-8 encoded and is also an authentication miss,
    never an exception that escapes as a 500.
    """

    try:
        provided_bytes = provided.encode("utf-8")
    except (UnicodeEncodeError, AttributeError):
        return False

    matched = False
    for candidate in candidates:
        try:
            candidate_bytes = candidate.encode("utf-8")
        except (UnicodeEncodeError, AttributeError):
            # Startup policy makes live candidates ASCII.  Keep one digest
            # operation per injected candidate while forcing malformed test
            # configuration to miss.
            candidate_bytes = b"\x00invalid-candidate"
        candidate_matches = hmac.compare_digest(
            provided_bytes, candidate_bytes
        )
        matched = candidate_matches or matched
    return matched
