from __future__ import annotations

import re
from pathlib import Path

from app.services import operator_auth


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
        "rotation_version_floors",
        "final_floor = max(prior_floor or 0, target_code_version)",
        "min_worker_version IS NOT DISTINCT FROM :prior_floor",
        "min_worker_version_stamped_by IS NOT DISTINCT FROM :prior_floor_stamped_by",
        "api_paused_reason IS NOT DISTINCT FROM :observed_pause_reason",
        "one drain request per online process ID",
        "post-done Notion archival",
        "OS/process supervisor reports zero worker processes",
        "SELECT count(*) AS active_credential_slots",
    ):
        assert required in document


def test_rotation_inventories_every_effective_version_and_host_override() -> None:
    """Catches sizing the fence from only the intended deployment version."""

    document = re.sub(r"\s+", " ", _text(RUNBOOK))
    for required in (
        "every registry row",
        "online, retained, and offline-known",
        "capabilities->>'code_version'",
        "WORKER_CODE_VERSION",
        "unexpected or ahead override",
        "unreadable override",
        "abort the rotation",
        "unconditional ABORT",
        "Bring it reachable and verify",
        "separately authorized decommission procedure outside this rotation",
        "restart this runbook from preflight",
        "Preserve existing tombstones, but never count them as rotation proof",
        "2_147_483_647",
    ):
        assert required in document
    assert "already be **tombstoned/parked before proceeding**" not in document


def test_offline_host_rule_has_no_waiver_or_tombstone_escape() -> None:
    """Catches reintroducing a route around reachability/env verification."""

    document = re.sub(r"\s+", " ", _text(RUNBOOK))
    offline_rule = document.split(
        "A known offline/unreachable host", 1
    )[1].split("Calculate the two checked values", 1)[0].casefold()
    for forbidden in (
        "unless",
        "exception",
        "waiv",
        "proceed if tombstoned",
        "exact-binary",
        "network/db isolation",
        "head-side mechanism",
        "disabled supervisor",
    ):
        assert forbidden not in offline_rule


def test_final_reopen_never_drops_below_the_deployed_target() -> None:
    """Catches restoring a stale prior floor after new code is deployed."""

    document = _text(RUNBOOK)
    assert "final_floor = max(prior_floor or 0, target_code_version)" in document
    assert "SET min_worker_version = :prior_floor" not in document
    sql_blocks = re.findall(r"```sql\s*(.*?)```", document, re.DOTALL)
    reopen_blocks = [
        block
        for block in sql_blocks
        if "min_worker_version = :final_floor" in block
    ]
    assert len(reopen_blocks) == 2
    for block in reopen_blocks:
        assert (
            "min_worker_version_stamped_by = 'operator-auth-rotation-final'"
            in block
        )
        assert "min_worker_version_stamped_at = now()" in block
        assert "min_worker_version = :temporary_floor" in block


def test_final_floor_arithmetic_never_restores_the_lower_prior_floor() -> None:
    """The concrete 953→1000 cutover finishes at the target arithmetic."""

    final_floor, _ = operator_auth.rotation_version_floors(
        prior_floor=953,
        target_code_version=1000,
        reported_code_versions=(954, 1000),
        configured_overrides=(),
    )

    assert final_floor == 1000


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


def test_rollback_never_selects_unhardened_code() -> None:
    """Catches rollback guidance that reintroduces weak auth or vault access."""

    document = re.sub(r"\s+", " ", _text(RUNBOOK))
    lowered = document.casefold()
    assert "roll back to old code" not in lowered
    assert "designated_hardened_rollback_ref" in document
    assert "Tasks 1–6" in document
    assert "sealed strong replacement" in document
    assert "current hardened code" in document


def test_rotation_requires_every_known_host_reachable_through_final_reopen() -> None:
    """Catches treating unreachable hosts as rollout-complete."""

    document = re.sub(r"\s+", " ", _text(RUNBOOK))
    for required in (
        "every online model-calling process",
        "code SHA",
        "token fingerprint",
        "auth_token_fingerprint",
        "version floor",
        "Every known host must still be reachable and attested",
        "If any known host becomes unreachable",
        "restart from preflight",
        "separate decommission",
        "Preserve existing tombstones but do not count them as evidence",
        "GEMINI_API_KEY",
        "six stored Vertex",
        "Host-59",
    ):
        assert required in document
    for forbidden in (
        "offline v954",
        "offline fences",
        "offline stragglers",
        "offline pre-target",
    ):
        assert forbidden not in document


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
