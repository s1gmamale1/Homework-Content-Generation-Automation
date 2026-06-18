"""Acceptance smoke: two-sided real-CLI fidelity judge.

Proves the reframed judge (Tasks A1+A2) correctly:
  (a) catches an invented fact (a year not present in the lesson context)
  (b) does NOT regen-tax math worked-examples (generated exercise numbers)

Uses the REAL claude CLI judge (cli transport = $0 marginal, no API billing).
No server, no DB — in-process only.

Run:  uv run python -m scripts.smoke_judge_fidelity   (from the repo root)
Exit 0 + "SMOKE PASS" on success; exit 1 if either assertion fails.
"""
import asyncio
import sys

from app.services import phase_judge


# ─── shared setup ────────────────────────────────────────────────────────────
# Use "flashcards" — a real phase name in prompts/_general/flashcards.md.
# The judge loads its contract via get_prompt(subject, phase_name).
SUBJECT = "history"        # generic enough for a history-ish case
MATH_SUBJECT = "mathematics"   # used for the math case


# ─── Case (a) — invented fact: output asserts "1991", lesson says nothing ────
HISTORY_LESSON_CONTEXT = """\
This lesson covers the formation of a trade alliance between two neighbouring
kingdoms. The alliance was established after a series of diplomatic meetings.
No specific dates are recorded in primary sources for these events.
The kingdoms exchanged ambassadors and agreed on shared border management.
"""

HISTORY_OUTPUT_MD = """\
# Flashcards — Trade Alliance

**Card 1**
Q: When was the trade alliance officially signed?
A: The alliance was signed in **1991**, marking a new era of cooperation.

**Card 2**
Q: What did the ambassadors agree on?
A: Shared border management and mutual trade rights.

**Card 3**
Q: How were the meetings initiated?
A: Through a series of diplomatic exchanges between the two kingdoms.
"""


# ─── Case (b) — math worked-example: generated numbers, method-only context ─
# The lesson context describes ONLY the method — no specific numbers.
# The flashcard output contains worked-example numbers (5, 7, 14…) in the
# example/explanation fields. The judge must NOT flag these as invented-fact
# fidelity violations — they are generated teaching numbers, not world claims.
# The output is deliberately well-formed (Uzbek, proper fields, 5 cards) so
# any has_major=True result would specifically be about the math numbers.
MATH_LESSON_CONTEXT = """\
Ushbu dars bir o'zgaruvchili chiziqli tenglamalarni yechishni o'rgatadi.
Usul: tenglamaning har ikkala tomoniga teskari amalni qo'llab,
o'zgaruvchini ajratib olish. Har doim javobni asl tenglamaga qo'yib tekshirish kerak.
Misol: ax + b = c ko'rinishidagi tenglamani yechishda avval b ni olib tashlang,
so'ngra a ga bo'ling.
"""

MATH_OUTPUT_MD = """\
# Flesh-kartlar — Chiziqli tenglamalar

**id:** card_1
**front:** Chiziqli tenglama
**back:** ax + b = c ko'rinishidagi tenglama, bunda x — noma'lum.
**type:** definition
**difficulty:** easy
**example:** 3x + 7 = 22

**id:** card_2
**front:** Teskari amal
**back:** O'zgaruvchini ajratish uchun har ikkala tomonga teskari amalni qo'llash.
**type:** term_to_meaning
**difficulty:** easy
**explanation:** Misol: 3x + 7 = 22 → har ikkala tomandan 7 ayiramiz → 3x = 15.

**id:** card_3
**front:** Tenglamani yechish tartibi
**back:** 1) b ni olib tashla; 2) a ga bo'l; 3) javobni tekshir.
**type:** process_step
**difficulty:** medium
**example:** 2x − 4 = 10 → 2x = 14 → x = 7. Tekshirish: 2(7) − 4 = 10 ✓

**id:** card_4
**front:** Tekshirish qoidasi
**back:** Topilgan x qiymatini asl tenglamaga qo'yib, tenglik o'rinli ekanini ko'rish.
**type:** process_step
**difficulty:** easy
**misconception:** Ko'pchilik javob topilgandan keyin tekshirishni o'tkazib yuboradi.

**id:** card_5
**front:** Teskari ko'paytirish amali
**back:** ax = c bo'lsa, x = c ÷ a.
**type:** process_step
**difficulty:** medium
**example:** 3x = 15 → x = 15 ÷ 3 = 5
"""


async def main() -> int:
    exit_code = 0

    # ── Case (a) ─────────────────────────────────────────────────────────────
    print("=" * 60)
    print("Case (a): invented-fact detection")
    print("  lesson_context: trade alliance, NO year mentioned")
    print("  output_md: claims 'signed in 1991'")
    print("  expect: has_major=True, failure cites the date")
    print("=" * 60)

    outcome_a = await phase_judge.judge(
        subject=SUBJECT,
        phase_name="flashcards",
        output_md=HISTORY_OUTPUT_MD,
        lesson_context=HISTORY_LESSON_CONTEXT,
        prior_outputs={},
        gen_provider="claude",
        gen_model=None,
        judge_provider="claude",
        judge_model=None,
        transport="cli",
    )

    print(f"  available : {outcome_a.available}")
    print(f"  passed    : {outcome_a.passed}")
    print(f"  has_major : {outcome_a.has_major}")
    print(f"  warnings  : {outcome_a.warnings}")

    if not outcome_a.available:
        print("\n[CASE (a)] BLOCKED — judge unavailable (CLI error)")
        return 1

    if not outcome_a.has_major:
        print("\n[CASE (a)] FAIL — expected has_major=True but got False")
        exit_code = 1
    else:
        # Confirm failures mention the year or the date claim
        year_cited = any("1991" in w or "year" in w.lower() or "date" in w.lower()
                         or "signed" in w.lower() for w in outcome_a.warnings)
        if not year_cited:
            print("\n[CASE (a)] FAIL — has_major=True but no failure mentions the "
                  "invented date/year; failures: " + str(outcome_a.warnings))
            exit_code = 1
        else:
            print("\n[CASE (a)] PASS — has_major=True and failure cites invented date/year")

    # ── Case (b) ─────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("Case (b): math worked-example — should NOT regen-tax")
    print("  lesson_context: Uzbek, describes the METHOD only, no specific numbers")
    print("  output_md: proper 5-card Uzbek flashcards with worked-example numbers")
    print("  expect: has_major=False (passed=True or only minor warnings)")
    print("=" * 60)

    outcome_b = await phase_judge.judge(
        subject=MATH_SUBJECT,
        phase_name="flashcards",
        output_md=MATH_OUTPUT_MD,
        lesson_context=MATH_LESSON_CONTEXT,
        prior_outputs={},
        gen_provider="claude",
        gen_model=None,
        judge_provider="claude",
        judge_model=None,
        transport="cli",
    )

    print(f"  available : {outcome_b.available}")
    print(f"  passed    : {outcome_b.passed}")
    print(f"  has_major : {outcome_b.has_major}")
    print(f"  warnings  : {outcome_b.warnings}")

    if not outcome_b.available:
        print("\n[CASE (b)] BLOCKED — judge unavailable (CLI error)")
        return 1

    if outcome_b.has_major:
        print("\n[CASE (b)] FAIL — judge OVER-FLAGGED math worked-examples as major; "
              "gating deferred to R20 (deterministic signal is warning-only)")
        exit_code = 1
    else:
        print("\n[CASE (b)] PASS — has_major=False; math worked-examples not regen-taxed")

    print()
    if exit_code == 0:
        print("SMOKE PASS — invented fact caught; math worked-examples spared")
    else:
        print("SMOKE FAIL — see above for details")

    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
