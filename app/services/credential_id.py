"""Pure per-provider credential fingerprinting for the fleet-wide api
concurrency limiter (BE-16). No DB, no wiring — see Tasks 3-5 for that.

``credential_for`` never touches ``os.environ`` itself; callers pass an
explicit env mapping (production passes ``os.environ``) so this stays a
pure function and tests stay hermetic.

gemini's branch order MUST mirror ``api_transport._gemini_client``
(``app/services/api_transport.py:63-69``) exactly: that function checks
``GEMINI_API_KEY`` FIRST and only falls to the Vertex SA pair
(``GOOGLE_APPLICATION_CREDENTIALS`` + ``GOOGLE_CLOUD_PROJECT``) when no key
is set. A host with both a leftover key and a Vertex SA assignment bills
via the key — so the fingerprint must count what actually bills, not just
whatever credential happens to be configured. If this drifts out of sync
with ``_gemini_client``, the limiter will fence the wrong credential slot.
"""
from __future__ import annotations

import hashlib
from typing import Mapping

_KEY_ENV_VAR = {
    "claude": "ANTHROPIC_API_KEY",
    "clodex": "CLODEX_API_KEY",
}


def _fp(provider: str, key: str) -> str:
    return f"{provider}:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


def gemini_project_credential(project_id: str) -> str:
    """The one canonical `gemini:{project_id}` credential string for a
    Vertex-SA-backed gemini credential. ``credential_for``'s own Vertex-pair
    branch (below) and the sa-keys API's per-project slots_in_use/
    effective_limit lookups (Task 6) both MUST build this string through
    here — never a second inline f-string — or the two sites can drift out
    of sync and the API would report visibility for a credential the
    limiter never actually keys slots under."""
    return f"gemini:{project_id}"


def credential_for(provider: str, env: Mapping[str, str]) -> str | None:
    """Return a stable, non-reversible fingerprint identifying the
    credential ``provider`` would actually bill against, given ``env``, or
    ``None`` if no usable credential is present (the limiter skips it).

    gemini: ``GEMINI_API_KEY`` checked first (parity with
    ``api_transport._gemini_client``, api_transport.py:63-69) ->
    ``gemini:{sha256(key)[:16]}``; else the Vertex pair
    (``GOOGLE_APPLICATION_CREDENTIALS`` + ``GOOGLE_CLOUD_PROJECT``) ->
    ``gemini:{GOOGLE_CLOUD_PROJECT}``; else None.
    claude / clodex: ``{provider}:{sha256(key)[:16]}`` from
    ``ANTHROPIC_API_KEY`` / ``CLODEX_API_KEY``; missing key -> None.
    Any other provider -> None.
    """
    if provider == "gemini":
        key = env.get("GEMINI_API_KEY")
        if key:
            return _fp("gemini", key)
        proj = env.get("GOOGLE_CLOUD_PROJECT")
        if env.get("GOOGLE_APPLICATION_CREDENTIALS") and proj:
            return gemini_project_credential(proj)
        return None

    env_var = _KEY_ENV_VAR.get(provider)
    if env_var is None:
        return None
    key = env.get(env_var)
    if not key:
        return None
    return _fp(provider, key)
