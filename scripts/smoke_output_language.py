"""Real-call acceptance smoke: output_language medium-of-instruction switch.

Proves that the medium switch actually changes the generated language end-to-end,
that UZ is unchanged vs today's behaviour, and that an L2 class (english subject)
is unaffected by the medium override (stays Uzbek-bridged regardless of
output_language="ru").

Four single-phase calls on a tiny synthetic lesson — minimal tokens.
Run: uv run python -m scripts.smoke_output_language

Provider: claude (the CLI that authenticates headless on this machine).
Requires DATABASE_URL in env (or .env) — DB writes are best-effort / swallowed,
so a dummy URL is fine: DATABASE_URL=postgresql+asyncpg://x:x@localhost/x

Exit 0 if at least one run succeeded; non-zero only if ALL runs failed.
"""
from __future__ import annotations

import asyncio
import sys
import unicodedata

from app.config import settings  # noqa: F401 — triggers load_dotenv
from app.services import agent
from app.services.prompts import get_prompt

# ── constants ─────────────────────────────────────────────────────────────────

PROVIDER = "claude"
MODEL = None  # let the provider pick its default

# One short lesson — no grade mention (minimise tokens, maximise signal clarity)
LESSON = (
    "Linear equations in one variable. "
    "A linear equation has the form ax + b = c. "
    "To solve, isolate x by subtracting b then dividing by a. "
    "Example: 3x + 7 = 22 → x = 5."
)

RUNS = [
    # (subject, output_language, description, expect_cyrillic)
    ("matematika", "uz",  "non-L2, medium=uz  → Uzbek (Latin)",   False),
    ("matematika", "en",  "non-L2, medium=en  → English",          False),
    ("matematika", "ru",  "non-L2, medium=ru  → Russian (Cyrillic)", True),
    ("english",    "ru",  "L2-english, medium=ru → stays UZ-bridged (no Cyrillic)", False),
]

# ── helpers ───────────────────────────────────────────────────────────────────


def _count_cyrillic(text: str) -> int:
    return sum(1 for ch in text if _is_cyrillic_char(ch))


def _is_cyrillic_char(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def _count_alpha_cyrillic(text: str) -> int:
    return sum(1 for ch in text if _is_cyrillic_char(ch))


def _count_latin_alpha(text: str) -> int:
    return sum(1 for ch in text
               if ch.isalpha() and not _is_cyrillic_char(ch)
               and not ("؀" <= ch <= "ۿ"))


def _verdict(text: str, expect_cyrillic: bool, description: str) -> str:
    cyrillic = _count_alpha_cyrillic(text)
    latin = _count_latin_alpha(text)
    total_alpha = cyrillic + latin or 1  # avoid /0
    cyr_pct = 100 * cyrillic / total_alpha
    lat_pct = 100 * latin / total_alpha

    if expect_cyrillic:
        ok = cyrillic > 50  # at least 50 Cyrillic chars expected
        heuristic = f"Cyrillic={cyrillic} ({cyr_pct:.0f}%), Latin={latin} ({lat_pct:.0f}%)"
        outcome = "PASS (Cyrillic present)" if ok else "FAIL (no/few Cyrillic chars)"
    else:
        # For UZ (Latin), EN, and the L2 bypass: expect mostly Latin/Uzbek, little Cyrillic
        # The L2 english subject should have English target words (Latin) with Uzbek bridge.
        ok = cyrillic < 30  # fewer than 30 Cyrillic chars expected
        heuristic = f"Cyrillic={cyrillic} ({cyr_pct:.0f}%), Latin={latin} ({lat_pct:.0f}%)"
        outcome = "PASS (no/few Cyrillic)" if ok else "FAIL (unexpected Cyrillic chars)"

    return f"  [{outcome}] {description}\n  heuristic: {heuristic}"


# ── main ──────────────────────────────────────────────────────────────────────


async def _main() -> int:
    successes = 0
    failures = 0

    for subject, lang, description, expect_cyrillic in RUNS:
        print(f"\n{'='*72}")
        print(f"RUN: subject={subject!r}, output_language={lang!r}")
        print(f"     {description}")
        print("─" * 72)
        try:
            prompt = get_prompt(subject, "flashcards", output_language=lang)
            text, tin, tout = await agent.run_phase_prompt(
                provider=PROVIDER,
                model=MODEL,
                phase_prompt=prompt,
                lesson_context=LESSON,
                prior_outputs={},
                difficulty=None,
                phase_name="flashcards",
                transport="cli",
            )
            verdict = _verdict(text, expect_cyrillic, description)
            print(verdict)
            print(f"  tokens: in={tin} out={tout}")
            print(f"  first 300 chars: {text[:300]!r}")
            if "FAIL" in verdict:
                print("  *** HEURISTIC MISMATCH — review output above ***")
                failures += 1
            else:
                successes += 1
        except Exception as exc:
            print(f"  ERROR: {exc!r}")
            print("  (continuing to next run)")
            failures += 1

    print(f"\n{'='*72}")
    print(f"SUMMARY: {successes} passed, {failures} failed "
          f"(out of {len(RUNS)} runs)")
    if successes == 0:
        print("ALL RUNS FAILED — check provider auth/CLI.")
        return 1
    elif failures > 0:
        print("SOME RUNS FAILED — review heuristic mismatches above.")
        return 1
    else:
        print("SMOKE PASS: all four runs produced expected language signals.")
        return 0


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
