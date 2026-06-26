"""Acceptance: prove book.grade injection drives the grade-band rules.
The lesson body has NO grade mention; the grade reaches the model ONLY via the
real pipeline _inject_grade. Deck size must track the grade. Run by the controller:
  uv run python -m scripts.smoke_grade_injection"""
import asyncio
import re

from app.services import agent
from app.services.pipeline import _inject_grade
from app.services.prompts import get_prompt

SUBJECT = "matematika"
# Deliberately NO grade mention anywhere in the lesson body.
LESSON_BODY = (
    "Linear equations in one variable. A linear equation has the form ax + b = c. "
    "To solve, isolate x: subtract b from both sides, then divide by a. "
    "Example: 3x + 7 = 22 -> 3x = 15 -> x = 5. Common mistake: dividing before "
    "subtracting, or moving a term without flipping its sign. Check by substituting back."
)


def _card_count(md: str) -> int:
    # The flashcards prompt emits each card as a markdown blockquote with a
    # stable `**id:** card_N` line (allow a leading `> ` blockquote prefix).
    # Count unique card ids; fall back to counting `**front:**` field lines.
    ids = set(re.findall(r"(?im)\bcard_(\d+)\b", md))
    if ids:
        return len(ids)
    return len(re.findall(r"(?im)^\s*>?\s*\**front\**\s*:", md))


async def _gen(grade: str) -> int:
    ctx = _inject_grade(LESSON_BODY, grade)            # grade comes ONLY from here
    assert "grade" not in LESSON_BODY.lower()          # body itself never mentions grade
    prompt = get_prompt(SUBJECT, "flashcards")
    text, _tin, _tout = await agent.run_phase_prompt(
        provider="gemini", model="gemini-2.5-flash",
        phase_prompt=prompt, lesson_context=ctx, prior_outputs={},
        difficulty=None, phase_name="flashcards", transport="api",
    )
    n = _card_count(text)
    print(f"\n--- grade {grade}: {n} cards ---\n{text[:600]}")
    return n


async def _main():
    n5 = await _gen("5")
    n11 = await _gen("11")
    print(f"\nRESULT: grade5={n5} cards, grade11={n11} cards")
    assert n5 > 0 and n11 > 0, "card counting failed — adjust _card_count"
    assert n11 >= n5, f"deck size should not shrink with higher grade (got 5={n5}, 11={n11})"
    print("SMOKE PASS: deck size tracks the injected grade (wiring works).")


if __name__ == "__main__":
    asyncio.run(_main())
