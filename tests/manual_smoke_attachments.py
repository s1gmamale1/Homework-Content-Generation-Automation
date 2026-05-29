"""Manual smoke test: prove each CLI provider actually ingests an attachment.

Run with:

    uv run python tests/manual_smoke_attachments.py

Skips persistence (monkeypatches ``_record_usage``) and DB context. Calls
``agent.run_phase`` once per provider with a tiny text fixture and asserts
the answer mentions the answer-token from the file (``Olympus Mons``).

Not part of the pytest suite because it spawns real CLIs (slow + needs
auth + costs API credits). Kept for one-shot post-fix verification.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# Make sure the project root is importable when invoked via ``uv run``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import agent as agent_module
from app.services.agent import run_phase


PROBE_BODY = (
    "The capital of Mars is Olympus Mons. "
    "The capital of Venus is Aphrodite Terra."
)
QUESTION = (
    "Read the attached file and answer in ONE short sentence: "
    "what is the capital of Mars per the file? Reply with just the city name."
)
EXPECTED_TOKEN = "olympus mons"


async def _patch_record(**_kwargs: object) -> None:
    return None


async def smoke_one(provider_name: str, probe: Path) -> tuple[bool, str]:
    """Returns ``(ok, snippet)`` — ``ok`` iff the answer mentions the token."""
    print(f"\n=== {provider_name} ===")
    try:
        result = await run_phase(
            provider=provider_name,
            model=None,
            phase_prompt=QUESTION,
            phase_name="smoke-attach",
            homework_job_id=None,
            phase_output_id=None,
            attachments=[probe],
            schema=None,
        )
    except Exception as exc:
        snippet = f"<{type(exc).__name__}: {exc}>"
        print(f"  EXCEPTION: {snippet}")
        return False, snippet

    text = (result.text or "").strip()
    print(f"  text: {text[:200]!r}")
    ok = EXPECTED_TOKEN in text.lower()
    print(f"  contains '{EXPECTED_TOKEN}': {ok}")
    return ok, text[:200]


async def main() -> int:
    # Monkeypatch _record_usage so we don't need a Postgres session.
    agent_module._record_usage = _patch_record  # type: ignore[assignment]

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.txt"
        probe.write_text(PROBE_BODY, encoding="utf-8")
        print(f"probe: {probe}")

        results: dict[str, tuple[bool, str]] = {}
        for prov in ("claude", "gemini", "codex", "kimi"):
            results[prov] = await smoke_one(prov, probe)

    print("\n=== SUMMARY ===")
    failed = 0
    for name, (ok, snippet) in results.items():
        marker = "OK " if ok else "FAIL"
        print(f"  [{marker}] {name}: {snippet[:120]!r}")
        if not ok:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
