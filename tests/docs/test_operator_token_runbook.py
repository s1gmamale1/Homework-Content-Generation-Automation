from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/runbooks/operator-token-rotation.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_in_order(document: str, anchors: list[str]) -> None:
    cursor = -1
    for anchor in anchors:
        position = document.find(anchor, cursor + 1)
        assert position >= 0, f"missing runbook contract anchor: {anchor!r}"
        assert position > cursor, f"out-of-order runbook contract anchor: {anchor!r}"
        cursor = position


def test_rotation_runbook_preserves_pause_and_credentials_in_safe_order() -> None:
    """Catches a rollout that restarts before draining or clears another owner's pause."""

    document = _text(RUNBOOK)
    _assert_in_order(
        document,
        [
            "SELECT api_paused_at, api_paused_reason",
            "pause_owned=false",
            "api_paused_reason = 'operator-auth-rotation'",
            "status = 'running'",
            "stored_vertex_keys",
            "hostname = 'Host-59'",
            "SHA-256",
            "delete-quarantine",
            "secrets.token_urlsafe(48)",
            "same strong token",
            "AUTH_TOKEN=123,<new>",
            "DO NOT restart or kill the head from automation",
            "operator restarts the head",
            "rolling worker restarts",
            "Post-rotation verification",
            "WHERE id = 1 AND api_paused_reason = 'operator-auth-rotation'",
            "Rollback",
            "never restore `123`",
        ],
    )


def test_rotation_runbook_rejects_unsafe_shortcuts() -> None:
    """Catches guidance that reopens weak auth or performs an unowned global unpause."""

    document = _text(RUNBOOK)
    lowered = document.casefold()
    assert "temporarily allow 123" not in lowered
    assert "restart head automatically" not in lowered
    assert not re.search(
        r"UPDATE\s+budget_state\s+SET\s+api_paused_at\s*=\s*NULL\s*,"
        r"\s*api_paused_reason\s*=\s*NULL\s*;",
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )


def test_rotation_runbook_attests_each_process_and_fences_offline_hosts() -> None:
    """Catches a hostname-only rollout that declares powered-off workers complete."""

    document = _text(RUNBOOK)
    for required in (
        "every online model-calling process",
        "code SHA",
        "token fingerprint",
        "version floor",
        "offline",
        "tombstone",
        "GEMINI_API_KEY",
        "six stored Vertex",
        "Host-59",
    ):
        assert required in document


def test_env_example_is_fail_closed_and_documents_explicit_local_dev() -> None:
    """Catches an example that silently teaches an empty or guessable production token."""

    document = _text(ROOT / ".env.example")
    assert "AUTH_TOKEN=<strong-shared-token>" in document
    assert "ALLOW_INSECURE_LOCAL_AUTH=false" in document
    assert "AUTH_TOKEN=\n" in document
    assert "ALLOW_INSECURE_LOCAL_AUTH=true" in document
    assert "SA-key routes remain closed" in document


def test_live_docs_no_longer_describe_empty_or_default_123_as_production_auth() -> None:
    """Catches stale operator guidance that contradicts fail-closed startup."""

    paths = [
        ROOT / "README.md",
        ROOT / "CLAUDE.md",
        ROOT / "docs/DEPLOY.md",
        ROOT / "docs/HOW_IT_WORKS.md",
        ROOT / "docs/CODE_MAP.md",
        ROOT / "docs/fleet/worker-pc-setup.md",
    ]
    joined = "\n".join(_text(path) for path in paths)
    stale_claims = (
        "Empty `AUTH_TOKEN` disables auth",
        "Empty disables auth",
        'Code default is the literal token `"123"`',
        'AUTH_TOKEN` | **strongly recommended** | `"123"`',
    )
    for claim in stale_claims:
        assert claim not in joined

    assert "operator-token-rotation.md" in joined
    assert "header-only" in joined
