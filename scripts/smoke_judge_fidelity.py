"""Acceptance smoke: three-sided fidelity judge.

Re-anchored 2026-08-07. The original version (worklog 0079) asserted that an
invented YEAR absent from the lesson context must be `major` — the expectation
worklog 0159 deliberately REVERSED, and it also ran the retired cli transport.
It sat under scripts/ rather than tests/, so the suite never caught the drift.

Proves the judge's three fidelity cases behave as the rule now defines them:
  (a) absent but PLAUSIBLE (an invented year the source does not mention)
      -> NOT major. This is the 0159 re-anchor: absence alone is at most minor,
      which is what killed the 56-59% false-major tax on fact-dense subjects.
  (b) generated TEACHING values (math worked-example numbers, invented names)
      -> NOT major. The R14/R25 regen-tax guard.
  (c) absent AND DEMONSTRABLY FALSE (a wrong gloss of a word the source names
      but never defines) -> major. Measured 2026-08-07: the judge already does
      this under the unchanged rule, because `_INSTRUCTIONS` §4 ("wrong
      content") dominates `_FIDELITY_RULE`'s absence exemption in practice.
      Evidence: docs/research/2026-08-07-language-fidelity-probe.md (3/3).

Runs over transport=api on gemini (the transport production uses; cli is
retired). Costs 3 judge calls, well under $0.05. No server, no DB — in-process.

Run:  uv run python -m scripts.smoke_judge_fidelity   (from the repo root)
Exit 0 + "SMOKE PASS" on success; exit 1 if any assertion fails.
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load env from an EXPLICIT file before app.config builds its Settings. Default
# is the repo's own .env; set HCGA_ENV_FILE when running from a git worktree,
# which has no .env of its own -- app.config's find_dotenv would then walk UP
# the tree and bind to a different .env, and the judge fails as "unavailable"
# with no credential rather than saying so.
_ENV_FILE = Path(os.environ.get("HCGA_ENV_FILE")
                 or (Path(__file__).resolve().parents[1] / ".env"))
if _ENV_FILE.is_file():
    load_dotenv(_ENV_FILE, override=True)

from app.services import phase_judge  # noqa: E402


# ─── shared setup ────────────────────────────────────────────────────────────
# Use "flashcards" — a real phase name in prompts/_general/flashcards.md.
# The judge loads its contract via get_prompt(subject, phase_name).
SUBJECT = "history"        # generic enough for a history-ish case
MATH_SUBJECT = "mathematics"   # used for the math case
LANG_SUBJECT = "english"       # used for the wrong-gloss case

# transport=api on gemini: the transport production actually uses. The cli path
# is retired operationally (CLAUDE.md standing decision 2026-07-01).
JUDGE_PROVIDER, JUDGE_MODEL, TRANSPORT = "gemini", "gemini-3.5-flash", "api"


# ─── Case (a) — invented fact: output asserts "1991", lesson says nothing ────
# Two defects in the original fixture, both found 2026-08-07 by running it:
#  1. It ended with "No specific dates are recorded in primary sources for these
#     events", which made the invented year a CONTRADICTION of the context, not
#     an absence — the judge was right to major it.
#  2. It dated an alliance between "kingdoms" to 1991, which the judge flagged as
#     ANACHRONISTIC — i.e. demonstrably wrong, not merely absent. An absent claim
#     has to be PLAUSIBLE to test the 0159 exemption, so the setting is now modern.
HISTORY_LESSON_CONTEXT = """\
This lesson covers the formation of a regional trade agreement between two
neighbouring modern states in the late twentieth century. The agreement was
established after a series of diplomatic meetings. The two states exchanged
ambassadors and agreed on shared border management and mutual trade rights.
"""

HISTORY_OUTPUT_MD = """\
# Flesh-kartlar — Savdo kelishuvi

**id:** card_1
**front:** Savdo kelishuvi qachon imzolangan?
**back:** Kelishuv **1994-yilda** imzolangan va hamkorlikning yangi davrini boshlagan.
**type:** question_answer
**difficulty:** easy

**id:** card_2
**front:** Elchilar nima haqida kelishib olishgan?
**back:** Umumiy chegara boshqaruvi va o'zaro savdo huquqlari haqida.
**type:** question_answer
**difficulty:** easy

**id:** card_3
**front:** Uchrashuvlar qanday boshlangan?
**back:** Ikki davlat o'rtasidagi diplomatik muzokaralar orqali.
**type:** question_answer
**difficulty:** easy

**id:** card_4
**front:** Diplomatik munosabat
**back:** Davlatlar o'rtasidagi rasmiy aloqalarni yuritish tartibi.
**type:** definition
**difficulty:** medium

**id:** card_5
**front:** Chegara boshqaruvi
**back:** Qo'shni davlatlar chegarani birgalikda nazorat qilish tartibi.
**type:** definition
**difficulty:** medium

**id:** card_6
**front:** O'zaro savdo huquqlari
**back:** Tomonlarning bir-biri bilan erkin savdo qilish huquqlari.
**type:** definition
**difficulty:** medium
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


# ─── Case (c) — absent AND demonstrably false: a wrong gloss ────────────────
# The lesson names 'duck' in a word list and never defines it, so a wrong gloss
# is absent-and-not-contradicted — yet it is plainly false. Distilled from the
# 2026-08-07 probe specimen (english G8 "Amazing Animals"), which measured this
# as `major` 3/3 under the unchanged rule.
LANG_LESSON_CONTEXT = """\
Unit 16 "Amazing Animals" teaches English through the theme of animals.

**Vocabulary — Animals:** bear, chicken, duck, elephant, insect, lion, monkey,
mouse, rabbit, rat. Students sort these into wild animals, farm animals, or
both, and identify which can be kept as pets and which can help people.

**Grammar — Past simple and past continuous:** the past continuous describes a
longer background action and the past simple a shorter action that interrupts
it. The connectors when (before past simple) and while (before past continuous)
are practised.
"""

LANG_OUTPUT_MD = """\
# Flesh-kartlar — Hayvonlar

**id:** card_1
**front:** while
**back:** Uzoq davom etgan fon harakatidan oldin keladi.
**type:** grammar
**difficulty:** easy
**example:** *While the man was driving, a monkey jumped out of a tree.*

**id:** card_2
**front:** duck
**back:** Yer ostida in qazib yashaydigan mayda kemiruvchi hayvon.
**type:** vocabulary
**difficulty:** easy
**example:** *The duck ran into its hole under the ground.*

**id:** card_3
**front:** rabbit
**back:** Uzun quloqli, tez yuguradigan mayda hayvon.
**type:** vocabulary
**difficulty:** easy
**example:** *The rabbit ran across the field.*
"""


def _majors_about(warnings: list[str], needles: tuple[str, ...]) -> list[str]:
    """Majors whose text mentions any needle.

    A bare ``has_major`` is NOT evidence about fidelity. These fixtures are
    small and hand-written, so the judge legitimately majors them for unrelated
    contract reasons — deck size below the grade band, missing card fields,
    English student-facing text. Keying each case to the fidelity-specific
    failure is what makes the smoke measure fidelity instead of fixture hygiene.
    (Learned the hard way: the same confound invalidated the first version of
    the 2026-08-07 probe's absent_false arm.)

    Matches requirement AND evidence: the judge often states the rule generically
    ("definitions must represent the terms correctly") and names the offending
    item only in the evidence, so a requirement-only match false-negatives. The
    cost is that a needle appearing in quoted output can false-positive, which is
    why each case's fixture is kept minimal and its needles specific.
    """
    out = []
    for w in warnings:
        if w.startswith("[major]") and any(n in w.lower() for n in needles):
            out.append(w)
    return out


# Fidelity-specific needles per case.
_YEAR_NEEDLES = ("1991", "year", "date", "signed in")
_INVENTION_NEEDLES = ("invent", "fabricat", "not present in the lesson",
                      "not in the lesson context", "not found in the source",
                      "worked-example", "made up")
_GLOSS_NEEDLES = ("duck", "bird", "rodent")


async def main() -> int:
    exit_code = 0

    # ── Case (a) ─────────────────────────────────────────────────────────────
    print("=" * 60)
    print("Case (a): absent but PLAUSIBLE — must NOT regenerate (0159 re-anchor)")
    print("  lesson_context: trade alliance, NO year mentioned")
    print("  output_md: claims 'signed in 1991'")
    print("  expect: has_major=False (absence alone is at most minor)")
    print("=" * 60)

    outcome_a = await phase_judge.judge(
        subject=SUBJECT,
        phase_name="flashcards",
        output_md=HISTORY_OUTPUT_MD,
        lesson_context=HISTORY_LESSON_CONTEXT,
        prior_outputs={},
        gen_provider="gemini",
        gen_model="gemini-3.6-flash",
        judge_provider=JUDGE_PROVIDER,
        judge_model=JUDGE_MODEL,
        transport=TRANSPORT,
    )

    print(f"  available : {outcome_a.available}")
    print(f"  passed    : {outcome_a.passed}")
    print(f"  has_major : {outcome_a.has_major}")
    print(f"  warnings  : {outcome_a.warnings}")

    if not outcome_a.available:
        print("\n[CASE (a)] BLOCKED — judge unavailable")
        return 1

    hits_a = _majors_about(outcome_a.warnings, _YEAR_NEEDLES)
    if hits_a:
        print("\n[CASE (a)] FAIL — a year merely ABSENT from the source was escalated "
              "to major. That is the pre-0159 behaviour whose false-major tax measured "
              "56-59% on fact-dense subjects; offending majors: " + str(hits_a))
        exit_code = 1
    else:
        print("\n[CASE (a)] PASS — no fidelity major about the absent year "
              f"(unrelated majors, if any, ignored: {outcome_a.has_major})")

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
        gen_provider="gemini",
        gen_model="gemini-3.6-flash",
        judge_provider=JUDGE_PROVIDER,
        judge_model=JUDGE_MODEL,
        transport=TRANSPORT,
    )

    print(f"  available : {outcome_b.available}")
    print(f"  passed    : {outcome_b.passed}")
    print(f"  has_major : {outcome_b.has_major}")
    print(f"  warnings  : {outcome_b.warnings}")

    if not outcome_b.available:
        print("\n[CASE (b)] BLOCKED — judge unavailable (CLI error)")
        return 1

    hits_b = _majors_about(outcome_b.warnings, _INVENTION_NEEDLES)
    if hits_b:
        print("\n[CASE (b)] FAIL — judge treated generated teaching values as invented "
              "facts; that is the R14/R25 regen tax; offending majors: " + str(hits_b))
        exit_code = 1
    else:
        print("\n[CASE (b)] PASS — no fidelity major about the worked-example numbers "
              f"(unrelated majors, if any, ignored: {outcome_b.has_major})")

    # ── Case (c) ─────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("Case (c): absent AND demonstrably FALSE — must regenerate")
    print("  lesson_context: names 'duck' in a word list, never defines it")
    print("  output_md: glosses duck as a burrowing rodent")
    print("  expect: has_major=True, failure says the gloss is wrong")
    print("=" * 60)

    outcome_c = await phase_judge.judge(
        subject=LANG_SUBJECT,
        phase_name="flashcards",
        output_md=LANG_OUTPUT_MD,
        lesson_context=LANG_LESSON_CONTEXT,
        prior_outputs={},
        gen_provider="gemini",
        gen_model="gemini-3.6-flash",
        judge_provider=JUDGE_PROVIDER,
        judge_model=JUDGE_MODEL,
        transport=TRANSPORT,
    )

    print(f"  available : {outcome_c.available}")
    print(f"  passed    : {outcome_c.passed}")
    print(f"  has_major : {outcome_c.has_major}")
    print(f"  warnings  : {outcome_c.warnings}")

    if not outcome_c.available:
        print("\n[CASE (c)] BLOCKED — judge unavailable")
        return 1

    hits_c = _majors_about(outcome_c.warnings, _GLOSS_NEEDLES)
    if not hits_c:
        print("\n[CASE (c)] FAIL — no major names the false duck gloss, so a "
              "demonstrably wrong meaning can never regenerate. Source silence is not "
              "a licence to be wrong; failures: " + str(outcome_c.warnings))
        exit_code = 1
    else:
        print("\n[CASE (c)] PASS — a major names the false gloss: " + hits_c[0][:110])

    print()
    if exit_code == 0:
        print("SMOKE PASS — absence spared; teaching values spared; false gloss caught")
    else:
        print("SMOKE FAIL — see above for details")

    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
