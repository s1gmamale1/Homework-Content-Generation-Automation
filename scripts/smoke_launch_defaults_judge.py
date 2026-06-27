"""Acceptance smoke for global-launch-defaults feature.

Proves that setting judge_provider/judge_model in the launch_defaults singleton
(what the new /settings PUT does) drives the ACTUAL judge call — the real model
ends up in agent_usages.model_name, not the old seed (gemini-2.5-flash).

Run:
    createdb -U macmini5 edu_gld_smoke
    export DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_smoke
    export RUN_DB_INTEGRATION=1
    uv run --extra dev alembic upgrade head
    uv run --extra dev python -m scripts.smoke_launch_defaults_judge
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models.agent_usage import AgentUsage
from app.repositories import launch_defaults as ld_repo
from app.services import phase_judge
from app.services.agent_models import resolve_role_selection


async def main() -> int:
    """Returns 0 on PASS, 1 on FAIL."""

    # ── Step 1: PUT judge defaults → claude/claude-opus-4-7 ─────────────────
    print("Step 1: Writing launch_defaults: judge_provider=claude, judge_model=claude-opus-4-7")
    async with SessionLocal() as session:
        await ld_repo.update(session, {
            "judge_provider": "claude",
            "judge_model": "claude-opus-4-7",
        })
        await session.commit()
        ld = await ld_repo.get(session)
        print(f"         -> DB row: judge_provider={ld.judge_provider!r}  "
              f"judge_model={ld.judge_model!r}")

    # ── Step 2: Resolve as the launch endpoint does ──────────────────────────
    print("\nStep 2: Resolving (explicit=None/None, defaults from DB row)")
    jp, jm = resolve_role_selection(
        explicit_provider=None,
        explicit_model=None,
        default_provider=ld.judge_provider,
        default_model_=ld.judge_model,
    )
    print(f"         -> resolved: jp={jp!r}  jm={jm!r}")
    assert (jp, jm) == ("claude", "claude-opus-4-7"), (
        f"FAIL: expected ('claude', 'claude-opus-4-7'), got {(jp, jm)!r}"
    )
    print("         ASSERT PASS: resolve_role_selection returns the DB-set pair.")

    # ── Step 3: Real judge call ──────────────────────────────────────────────
    # Subject: "biology" — exists in registry; "flashcards" prompt is at
    # prompts/_general/flashcards.md and is a real content phase.
    print("\nStep 3: Making REAL judge call (claude cli) — may take 20-60s …")
    call_start = datetime.now(tz=timezone.utc)
    outcome = await phase_judge.judge(
        subject="biology",
        phase_name="flashcards",
        output_md=(
            "# Flashcards\n\n"
            "**Q:** What organelle is known as the powerhouse of the cell?  \n"
            "**A:** The mitochondria.\n\n"
            "**Q:** What is photosynthesis?  \n"
            "**A:** The process plants use to convert sunlight, water, and CO₂ into glucose.\n"
        ),
        lesson_context=(
            "This lesson introduces cell biology basics: the mitochondria as the site of "
            "cellular respiration, chloroplasts as the site of photosynthesis, and the cell "
            "membrane as the selective barrier."
        ),
        prior_outputs={},
        gen_provider="gemini",
        gen_model="gemini-2.5-flash",
        judge_provider=jp,
        judge_model=jm,
        transport="cli",
        homework_job_id=None,   # nullable FK — smoke has no real job row
        phase_output_id=None,
    )
    print(f"         -> outcome: available={outcome.available}  passed={outcome.passed}  "
          f"refused={outcome.refused}  warnings={outcome.warnings}")

    if not outcome.available:
        # Distinguish env failure from code bug
        w = " | ".join(outcome.warnings)
        print(f"\nFAIL (ENV): judge call returned unavailable. warnings: {w}")
        print("This may be a claude CLI environment issue (session limit, network), "
              "not a feature defect.")
        return 1

    # ── Step 4: Verify agent_usages row ─────────────────────────────────────
    print("\nStep 4: Querying agent_usages for the judge row written by this call …")
    async with SessionLocal() as session:
        result = await session.execute(
            select(AgentUsage)
            .where(AgentUsage.operation.like("judge:%"))
            .where(AgentUsage.created_at >= call_start)
            .order_by(AgentUsage.created_at.desc())
            .limit(5)
        )
        rows = result.scalars().all()

    if not rows:
        print("\nFAIL: No agent_usages rows found with operation LIKE 'judge:%' after the call.")
        return 1

    row = rows[0]
    print(f"\n         agent_usages row:")
    print(f"           provider   = {row.provider!r}")
    print(f"           model_name = {row.model_name!r}")
    print(f"           auth_mode  = {row.auth_mode!r}")
    print(f"           success    = {row.success!r}")
    print(f"           operation  = {row.operation!r}")

    # Hard assertions
    errors = []
    if row.model_name != "claude-opus-4-7":
        errors.append(
            f"model_name expected 'claude-opus-4-7', got {row.model_name!r}"
        )
    if row.provider != "claude":
        errors.append(f"provider expected 'claude', got {row.provider!r}")
    if not row.success:
        errors.append("success is False — judge call reported a failure")
    if row.model_name == "gemini-2.5-flash":
        errors.append("model_name is still 'gemini-2.5-flash' — DB update did NOT drive it")

    if errors:
        print("\nFAIL:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\n══════════════════════════════════════════════════════════")
    print("  SMOKE PASS")
    print(f"  Real claude call executed: provider={row.provider!r}  "
          f"model_name={row.model_name!r}  success={row.success!r}")
    print("  DB PUT (judge_provider=claude, judge_model=claude-opus-4-7)")
    print("  correctly drove the judge — NOT the old gemini-2.5-flash seed.")
    print("══════════════════════════════════════════════════════════")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
