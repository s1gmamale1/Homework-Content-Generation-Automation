"""Validate + extract identity from a GCP service-account key JSON. Pure; no I/O."""
from __future__ import annotations

import json


class InvalidServiceAccountKey(ValueError):
    """The uploaded bytes are not a usable GCP service-account key."""


def parse_and_validate_sa_key(body: bytes) -> tuple[str, str]:
    """Return (project_id, client_email) for a valid SA key, else raise.

    A valid key is JSON with type=="service_account" and non-empty
    project_id, client_email and private_key fields."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidServiceAccountKey(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidServiceAccountKey("JSON is not an object")
    if data.get("type") != "service_account":
        raise InvalidServiceAccountKey("type is not 'service_account'")
    project_id = data.get("project_id")
    client_email = data.get("client_email")
    private_key = data.get("private_key")
    if not project_id:
        raise InvalidServiceAccountKey("missing project_id")
    if not client_email:
        raise InvalidServiceAccountKey("missing client_email")
    if not private_key:
        raise InvalidServiceAccountKey("missing private_key")
    return str(project_id), str(client_email)
