from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/runbooks/operator-token-rotation.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_in_order(document: str, anchors: list[str]) -> None:
    document = re.sub(r"\s+", " ", document)
    cursor = -1
    for anchor in anchors:
        anchor = re.sub(r"\s+", " ", anchor)
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
            "prior_floor_stamped_by",
            "temporary_floor",
            "pause_owned=false",
            "api_paused_reason = 'operator-auth-rotation'",
            "Drain and stop every online worker process",
            "status IN ('running', 'cancelling')",
            "FROM credential_slots",
            "WORKER_CONCURRENCY=0",
            "stored_vertex_keys",
            "hostname = 'Host-59'",
            "snapshot_uuid_inventory",
            "secrets.token_urlsafe(48)",
            "runtime_token_set_fingerprint",
            "same strong token",
            "AUTH_TOKEN=123,<new>",
            "DO NOT restart or kill the head from automation",
            "operator restarts the head",
            "rolling worker restarts",
            "Post-rotation verification",
            "auth_token_fingerprint",
            "Final owner-scoped reopen",
            "api_paused_reason = 'operator-auth-rotation'",
            "min_worker_version = :temporary_floor",
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
    sql_blocks = re.findall(r"```sql\s*(.*?)```", document, re.DOTALL)
    clearing_blocks = [
        block for block in sql_blocks if re.search(r"api_paused_at\s*=\s*NULL", block)
    ]
    assert len(clearing_blocks) == 1
    clearing = clearing_blocks[0]
    assert "BEGIN;" in clearing and "COMMIT;" in clearing
    assert "WHERE id = 1" in clearing
    assert "api_paused_reason = 'operator-auth-rotation'" in clearing
    assert "min_worker_version = :temporary_floor" in clearing
    assert "min_worker_version_stamped_by = 'operator-auth-rotation'" in clearing


def test_rotation_runbook_uses_version_floor_as_the_all_claim_fence() -> None:
    """Catches treating the API-only budget pause as a global claim barrier."""

    document = re.sub(r"\s+", " ", _text(RUNBOOK))
    for required in (
        "API pause is not a global claim fence",
        "max(prior_floor or 0, target_code_version) + 1",
        "min_worker_version IS NOT DISTINCT FROM :prior_floor",
        "min_worker_version_stamped_by IS NOT DISTINCT FROM :prior_floor_stamped_by",
        "api_paused_reason IS NOT DISTINCT FROM :observed_pause_reason",
        "one drain request per online process ID",
        "post-done Notion archival",
        "OS/process supervisor reports zero worker processes",
        "SELECT count(*) AS active_credential_slots",
    ):
        assert required in document


def test_foreign_pause_keeps_temporary_floor_until_explicit_handoff() -> None:
    """Catches silently restoring the claim floor while another owner remains paused."""

    document = re.sub(r"\s+", " ", _text(RUNBOOK))
    for required in (
        "pause_owned=false",
        "do not restore or lower the temporary floor",
        "explicitly accept the fence handoff",
        "foreign_pause_reason",
        "foreign_pause_at",
        "min_worker_version_stamped_by = 'operator-auth-rotation'",
    ):
        assert required in document


def test_vault_snapshot_uses_only_production_vault_api() -> None:
    """Catches a runbook bypassing held-handle validation with pathlib reads."""

    document = _text(RUNBOOK)
    vault_section = document.split("## 4. Snapshot", 1)[1].split("\n## ", 1)[0]
    assert "sa_key_vault.harden_vault()" in vault_section
    assert "sa_key_vault.snapshot_uuid_inventory()" in vault_section
    assert ".read_bytes(" not in vault_section
    assert ".iterdir(" not in vault_section
    assert "Path(" not in vault_section


def test_rotation_requires_runtime_fingerprint_evidence_before_reopen() -> None:
    """Catches trusting staged env files instead of restarted process state."""

    document = re.sub(r"\s+", " ", _text(RUNBOOK))
    for required in (
        "runtime_token_set_fingerprint",
        "expected_auth_fingerprint",
        "head accepts the staged token",
        "auth_token_fingerprint",
        "every restarted online model-calling process",
        "before lowering the temporary floor",
    ):
        assert required in document


def test_rotation_runbook_attests_each_process_and_fences_offline_hosts() -> None:
    """Catches a hostname-only rollout that declares powered-off workers complete."""

    document = re.sub(r"\s+", " ", _text(RUNBOOK))
    for required in (
        "every online model-calling process",
        "code SHA",
        "token fingerprint",
        "auth_token_fingerprint",
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
