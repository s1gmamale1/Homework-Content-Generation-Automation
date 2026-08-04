"""Acceptance smoke for the prompt-optimization port: render 3 high-delta phases'
prompts and do ONE real api generation each, eyeballing that the ported rules hold
(grade-sized deck, no leftover tokens, no SVG/JSON leakage).
Run: uv run python -m scripts.smoke_prompt_port
Minimal per the no-mass-gen rule: 3 single-phase calls on a tiny synthetic lesson."""
import asyncio
import re

from app.services import agent
from app.services.prompts import get_prompt

SUBJECT = "matematika"
LESSON = (
    "Grade 7 mathematics — Linear equations in one variable. A linear equation has "
    "the form ax + b = c. To solve, isolate x by doing the same operation to both "
    "sides: subtract b, then divide by a. Example: 3x + 7 = 22 -> 3x = 15 -> x = 5. "
    "Common mistake: dividing before subtracting, or moving a term without flipping "
    "its sign. Always check by substituting the answer back into the original."
)
PHASES = ["flashcards", "case-based-preview", "boss-arena"]


async def _main():
    for phase in PHASES:
        prompt = get_prompt(SUBJECT, phase)
        assert "{{" not in prompt, f"{phase}: prompt has unresolved tokens"
        text, tin, tout = await agent.run_phase_prompt(
            provider="gemini", model="gemini-3.5-flash",
            phase_prompt=prompt, lesson_context=LESSON, prior_outputs={},
            difficulty=None, phase_name=phase, transport="api",
        )
        low = text.lower()
        assert text.strip(), f"{phase}: empty output"
        assert "<svg" not in low, f"{phase}: raw SVG leaked into output"
        print(f"\n{'='*70}\n### {phase}  (in={tin} out={tout})\n{'='*70}")
        print(text[:1400])
    print("\nSMOKE PASS: 3 phases generated through the ported prompts; no token/SVG leakage.")


if __name__ == "__main__":
    asyncio.run(_main())
