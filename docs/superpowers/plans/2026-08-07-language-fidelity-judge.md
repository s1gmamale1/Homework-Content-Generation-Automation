# Language fidelity: source-absent fabrication is structurally un-regenerable — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A generated claim that the lesson source never mentions but that is *demonstrably false* (a wrong gloss, an ungrammatical model sentence, a wrong definition or unit) becomes a `major` failure the judge can regenerate on — while source *silence* alone stays `minor`; and the extract stops starving language lessons of the vocabulary and model sentences the generator is contractually told to build from.

**Architecture:** Two prompt-level limbs plus the deterministic plumbing that makes them take effect. (1) `phase_judge._FIDELITY_RULE` gains a third case — absent-AND-demonstrably-false → `major`, gated behind a substantiation requirement (state the correction or drop to `minor`). (2) `agent._CONTRACT_INSTRUCTIONS` gains two conditional headings — `## Vocabulary & set phrases` and `## Source sentences & passages` — in the contract's own existing "REQUIRED whenever … / OMIT if none" idiom, so no subject plumbing is needed. The extract prompt-cache key bumps `builtin:extract:v3` → `v4` (without it, every already-extracted section keeps serving a v3 extract forever and limb 2 never reaches a single real job), and `content_lint` learns the two new sections so the warn-only coverage lint can see vocabulary items.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy async / pytest + pytest-asyncio / uv. Real model calls over `transport=api` on gemini (plain `GEMINI_API_KEY`), extract role `gemini-3.5-flash-lite`, judge role `gemini-3.5-flash`.

---

## Approach & key decisions

- **The defect, stated precisely:** `_FIDELITY_RULE` defines its exempt zone by **source silence**, not by **unverifiability**. "Merely ABSENT … but not contradicted … is at most `minor` — never `major`" literally covers a claim the judge *knows* to be false, because a source that never mentions a word cannot contradict a wrong gloss of it. `_INSTRUCTIONS` §4 says `major` = "wrong … content"; the two collide and the more specific fidelity rule wins. Severity mapping (`phase_judge.py:31`) makes `minor` warn-only, so the wrongness is structurally un-regenerable.
- **Why the rule reads this way — and why we are NOT flipping it:** the text is the *output* of worklog 0159 (R25 limb 2, 2026-07-23), a measured win — flashcards reweighted major-rate fell in both counterbalanced arms (math 0.69→0.37 / 0.66→0.40; geo 0.89→0.71 / 0.63→0.31). Its per-claim safety probes were **p2 = absent-and-TRUE** (correctly never major) and **p4 = constructed-wrong-and-CONTRADICTING** (correctly major). **Absent-and-FALSE was never probed.** So this is a gap between two probes, not a reversal of a decision. A blanket "escalate absent claims" would carpet-bomb math's legitimately-invented practice numbers — rejected.
- **Chosen judge shape: truth-vs-silence split, subject-agnostic** (user-selected). Math's invented practice values, student names and hypothetical scenarios are not "demonstrably false", so they stay exempt by construction — no subject branch needed. Rejected: a `languages`-family-only relaxation (leaves the identical hole in the other 20 subjects and adds a second subject-conditional axis to a prompt R25 already wants to branch for CBP concealment).
- **Chosen extract shape: two conditional headings, subject-agnostic** (user-selected). Verified load-bearing facts: `_CONTRACT_INSTRUCTIONS` (`agent.py:2400-2418`) has exactly five headings and no vocabulary/passage inventory; `summarize_lesson` is entirely subject-blind (`rules=_NO_PREAMBLE`, no subject argument); yet the generation prompts *are* family-aware — `_FC_LANGUAGES` (`prompts.py:372`) mandates `vocabulary` cards (L2 word → L1 meaning) and example sentences, and `_CBP_LANGUAGES` forbids "authoring a fresh passage when the textbook has one". The system asks for source vocabulary from an extract with no vocabulary section. Rejected: threading `subject` into `summarize_lesson`/`summarize_lesson_vision` (first subject-varying extract prompt, touches the vision fallback and the pipeline extract branch, for no gain the conditional headings don't already give).
- **The cache bump is not optional.** `pipeline.py:1373` hardcodes `prompt_hash = "builtin:extract:v3"` and `phase_outputs.find_latest_extract` keys cross-job reuse on it. Changing the contract text without bumping the key means every already-extracted section (3,208 done lessons) silently serves a pre-change extract forever. Bumping to `v4` invalidates the cache — each re-launched section pays one fresh extract at ~57k input tokens on `gemini-3.5-flash-lite` ≈ **$0.006**, organically per-job. Accepted.
- **Accepted risk, measured not assumed — extract growth.** "List every such item; do not sample" has no ceiling, and the extract is injected as LESSON CONTEXT into all 11 content phases *and* every judge call — roughly 22 injections per job. A vocabulary-inventory-shaped section can therefore multiply per-job input tokens, not just the one-off re-extract. This is why the probe records `chars` per specimen and why one of the three is deliberately the 10-page `Vocabulary List` section — the worst realistic case. **Decision rule:** if that specimen's `after` extract exceeds ~12,000 characters (roughly 3× the current typical extract), add an explicit cap to the vocabulary heading before shipping and re-run; below that, accept the growth and record the measured delta in the worklog.
- **Out of scope, filed not fixed:** `_SOLVER_PHASES` (`pipeline.py:52`) covers only the 4 key-bearing phases — `flashcards`, `practice-sentence`, `practice-jigsaw`, `practice-memory-match`, where language content actually lives, get *no* independent correctness check. That is a per-job cost/architecture change (4 extra solver calls), not a prompt fix. Goes to WISHLIST as `language-drill-solver-gap-1` (Task 5).
- **Scope call — this is ONE plan, on an assumption.** The brief flagged a probable coupling with a separately-raised "language-fidelity gap". Nothing in this repo records that item — no spec, plan, branch, worklog or WISHLIST line — so it was raised in another session and its text was never supplied. This plan therefore covers the coupling **as the code shows it**: the judge-severity limb and the extract-starvation limb are the same defect seen from two ends, and shipping the judge alone would let it escalate fabrications the generator was never given the material to avoid. If the other item turns out to be about something else — the `languages`-family generation prompts, the L2 bridge, or output-language leakage — it is a separate plan and nothing here needs to change.
- **Honest caveat, addressed by Task 0:** every claim above is read from code, not measured — no language subject has ever been audited. The 24 done language packets (english 22, adabiyot 2) all date from 2026-06-17/24, i.e. before the coverage-contract extract (0119), before the 0159 re-anchor and before the 3.x models, and stored judge stats cannot measure fabrication anyway (english sits mid-pack at 14% `major_shipped`) because the judge is blind to it *by construction*. **Task 0 is a hard gate:** it probes the two load-bearing claims on the live system before a line of production code changes, and can kill limb 1.

---

## Global Constraints

- **Branch/worktree:** all work happens in the existing worktree `/Users/macmini5/Documents/HCGA-lang-fidelity` on branch `feat/language-fidelity-judge`, cut from `origin/Nggaev-v2` @ `2ebab53`. Never `cd` to the main checkout. Verify before every commit: `test "$(git branch --show-current)" = "feat/language-fidelity-judge" || exit 1`.
- **Stage only the files each task lists.** Never `git add -A` — other sessions commit to this branch's base.
- **Transport:** every real model call in this plan runs `transport="api"` on gemini. The cli path is retired operationally; do not benchmark against it.
- **Env trap (verified):** `/Users/macmini5/Documents/.env` EXISTS and would be picked up by `find_dotenv` walking up from the worktree, overriding the real config. Every probe/smoke command in this plan must source the main checkout's env explicitly:
  ```bash
  set -a; . /Users/macmini5/Documents/Homework-Content-Generation-Automation/.env; set +a
  export VAR_DIR=/Users/macmini5/Documents/Homework-Content-Generation-Automation/var
  ```
  (`VAR_DIR` matters because the book PDFs live in the main checkout's `var/books/`, not in this worktree.)
- **Money rule:** never mass-generate. Task 0 ≤ $2, Task 4 ≤ $2. Report actual spend from `agent_usages` in the worklog.
- **Baseline (already established in this worktree):** `uv run python -m pytest tests/ -q` → **2083 passed, 347 skipped, 0 failed**. Every task must leave the suite green.
- **Prompt-text tests are literal-assertion tests**, matching the existing style in `tests/services/test_phase_judge.py` and `tests/services/test_agent.py` — they pin the clause that must exist and the old wording that must be gone. The behavioral proof is Task 0/Task 4's real probes, not the unit tests.

---

### Task 0: Mechanism probe — measure before building (HARD GATE)

This task writes an instrument and runs it. It is not TDD; its deliverable is a measurement and a go/no-go decision. **Do not start Task 1 or Task 2 until the gate below is evaluated and recorded.**

**Files:**
- Create: `docs/research/2026-08-07-language-fidelity-probe.py`
- Create (generated): `docs/research/2026-08-07-language-fidelity-probe-data-before.json`
- Create: `docs/research/2026-08-07-language-fidelity-probe.md` (the written findings)

**Interfaces:**
- Consumes: `app.services.agent.summarize_lesson`, `app.services.agent.read_whole_book_text`, `app.services.phase_judge.judge`, `app.services.storage.book_pdf_path`, `app.db.SessionLocal`.
- Produces: `docs/research/2026-08-07-language-fidelity-probe-data-<label>.json` with keys `label`, `extract_probe` (per-section results) and `judge_probe` (per-arm-per-replay results, including the `regression:*` arms). Task 4 re-runs the same script with `--label after` and diffs the two files.

**Fixed specimens.** Every value below was read from `edu_copy` / the filesystem. The probe looks the TOC title and page range up **at runtime** rather than hardcoding them — an earlier draft of this plan carried invented page numbers, which is exactly the failure this repo's memory warns about.

| what | id | verified |
|---|---|---|
| english G8 book | `d463c690-08ce-4fd1-ba27-fa51f39961b5` | `var/books/<id>/source.pdf`, 15.7 MB |
| adabiyot G9 book | `e585a5f3-f8c4-4fc9-a68f-a7c0bc21f209` | `var/books/<id>/source.pdf`, 10.6 MB |
| extract sections | looked up by `section_title` at runtime | `Families` (pp.116-119), `Vocabulary List` (pp.127-136), `Alpomish` (pp.8-44); `section_number` is **empty** in the DB for all three |
| judge specimen — english | job `a334b3cf-e258-436d-89e1-dbe05741dd1a` ("Amazing Animals") | done; `flashcards` = **10 cards** (in G8's 8-10 band) with `judge_status='ok'`, and a `extract` row |
| regression specimen — math | job `a2b9f9f3-42e1-418c-8f01-c0299952aa04` (G11 "Prizmaning hajmi") | done; 12 cards (in G9-11 band), `judge_status='ok'` |
| regression specimen — geo | job `bfe00182-9460-470f-b7a4-1012be960c96` (G10 "Braziliya") | done; 12 cards, `judge_status='ok'` |

**Why not the obvious specimen.** Job `f10d2475` ("Families") was the first choice and is *unusable*: its deck has **7 cards** against G8's 8-10 band (a structural count the judge can major on its own), and its `nephew` card cannot be cleanly mutated — the Uzbek parenthetical sits on the same line as the English gloss, and the `hint` and `example` lines independently restate the correct meaning, so any wrong-gloss mutation leaves three self-contradictions inside one card. Worse, the source extract sorts `nephew` under "male (uncle, nephew, grandfather, grandson)", so a female gloss **contradicts the lesson context** — the arm would measure the contradiction clause that already works, not the absent-and-false hole. `a334b3cf` avoids all four problems.

**Arm design rule (load-bearing).** Every mutation replaces a **whole card block** (id/front/back/type/difficulty and every optional field) so no line of the original survives to contradict the injected claim, and each arm's verdict requires the judge's failure text to **cite the mutated card** — a bare `has_major` is not evidence, because the specimen may carry an unrelated major.

**Two known impurities, recorded rather than papered over.** (1) The `contradiction` arm's injected card is also inconsistent with surviving cards 1 and 3, which both restate the correct when/while rule — so a major there may fire on intra-deck inconsistency rather than on the source contradiction. That only adds firing channels to an arm that must fire anyway (it is a STOP-check), so it cannot push the gate in the dangerous direction; **record which channel the failure text actually cites.** (2) Replacing `card_8` drops the "turn on / turn off" concept, which the extract enumerates — see gate rule 3 for why that omission major must not be counted as the arm firing. (3) Bonus signal in the `control` arm: card_9's `explanation` claims rescue dogs find people under rubble, which the extract never says and which is true of rescue dogs generally — a naturally-occurring absent-but-TRUE claim. After the change it must still NOT be a major; if it becomes one, the exception is over-firing on exactly the class 0159 protected.

- [ ] **Step 1: Write the probe script**

Create `docs/research/2026-08-07-language-fidelity-probe.py`:

```python
"""Mechanism probe for the language-fidelity gap (plan 2026-08-07).

Two probes, both against the LIVE system over transport=api:

A. EXTRACT — does the current 5-heading coverage contract carry a language
   lesson's vocabulary and model sentences forward at all?
B. JUDGE  — does the current _FIDELITY_RULE cap an ABSENT-and-FALSE claim
   (a wrong gloss) at `minor`, while still majoring a CONTRADICTION?

Probe B also carries two UNMUTATED fact-dense decks (math + geography) as
regression arms: R25's regen tax lived there, so they answer the question the
english arms cannot -- does the new exception make the judge major things it
used to leave alone?

Run BEFORE the fix (`--label before`) and again after (`--label after`); the
two JSON files are the before/after evidence. Real model calls per run:
3 extracts + 15 judge calls, ~$0.35.

    set -a; . /path/to/main-checkout/.env; set +a
    export VAR_DIR=/path/to/main-checkout/var
    uv run python docs/research/2026-08-07-language-fidelity-probe.py --label before
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from app.db import SessionLocal
from app.services import agent, phase_judge, storage

EXTRACT_PROVIDER, EXTRACT_MODEL = "gemini", "gemini-3.5-flash-lite"
JUDGE_PROVIDER, JUDGE_MODEL = "gemini", "gemini-3.5-flash"
REPLAYS = 3

# (book_id, section_title, label) — the page range and section number are read
# from toc_entries at runtime; never hardcode them.
EXTRACT_SPECIMENS = [
    ("d463c690-08ce-4fd1-ba27-fa51f39961b5", "Families", "english-g8-families"),
    ("d463c690-08ce-4fd1-ba27-fa51f39961b5", "Vocabulary List", "english-g8-vocab-list"),
    ("e585a5f3-f8c4-4fc9-a68f-a7c0bc21f209", "Alpomish", "adabiyot-g9-alpomish"),
]

# 10 cards (in G8's 8-10 band), judge_status='ok' — no pre-existing major to
# confound the arms. Its extract lists 'duck' among the unit's animals with NO
# gloss, and states the when/while rule explicitly: one word the source is
# SILENT about, one rule it SPEAKS to. That pairing is what makes the two arms
# separable.
JUDGE_SPECIMEN = UUID("a334b3cf-e258-436d-89e1-dbe05741dd1a")

# Unmutated fact-dense decks. R25's regen tax lived here, so these answer the
# question the english arms cannot: does the new exception make the judge major
# things it used to leave alone? Both are judge_status='ok' and in-band today.
REGRESSION_SPECIMENS = [
    ("math-g11-prizma", UUID("a2b9f9f3-42e1-418c-8f01-c0299952aa04"), "math-algebra"),
    ("geo-g10-braziliya", UUID("bfe00182-9460-470f-b7a4-1012be960c96"), "geografiya"),
]

# Probe B arms: (arm, card_id, new_block, cite_needles). Each mutation replaces
# a WHOLE card block, so no original line survives to contradict the injected
# claim -- the failure mode that made the first specimen unusable.
#   control       — untouched deck.
#   absent_false  — 'duck' is NAMED in the source's animal list but never
#                   glossed, so a wrong gloss is absent-and-not-contradicted yet
#                   demonstrably false. THE CASE UNDER TEST.
#   contradiction — the source states 'when' precedes the past simple; this card
#                   asserts the opposite. Positive control: must already major.
JUDGE_ARMS = [
    ("control", None, None, ()),
    ("absent_false", "card_8", """**id:** card_8
**front:** duck
**back:** Yer ostida in qazib yashaydigan mayda kemiruvchi hayvon.
**type:** vocabulary
**difficulty:** easy
**example:** *The duck ran into its hole under the ground.*""", ("duck", "kemiruvchi")),
    ("contradiction", "card_2", """**id:** card_2
**front:** when
**back:** Uzoq davom etgan fon harakatidan oldin keladi; o'zidan keyin Past Continuous ishlatiladi.
**type:** grammar
**difficulty:** easy
**example:** *When the man was driving, a monkey jumped out of a tree.*""", ("card_2", "Past Continuous")),
]

_HEADING_RE = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]*(?P<h>[^\n#].*?)[ \t]*$")
_CARD_RE = re.compile(r"(?ms)^\*\*id:\*\* *(?P<id>card_\d+).*?(?=^\*\*id:\*\*|\Z)")


def _replace_card(md: str, card_id: str, new_block: str) -> str:
    """Swap a whole card block by id. Matching by id (not by quoting the card's
    text) keeps the probe robust against the typographic apostrophes and Uzbek
    parentheticals in real output."""
    for m in _CARD_RE.finditer(md):
        if m.group("id") == card_id:
            return md[:m.start()] + new_block.rstrip() + "\n\n" + md[m.end():]
    raise SystemExit(f"card {card_id} not found in specimen")


def _cites(warnings: list[str], needles: tuple[str, ...]) -> bool:
    """Heuristic only: did the judge's failure text mention the injected card?
    Recorded as a hint; the full `warnings` are stored so a human adjudicates."""
    blob = " ".join(warnings).lower()
    return any(n.lower() in blob for n in needles)


def _headings(md: str) -> list[str]:
    return [m.group("h").strip() for m in _HEADING_RE.finditer(md or "")]


async def _fetch_phase(job_id: UUID, phase_name: str) -> str:
    async with SessionLocal() as s:
        row = (await s.execute(text(
            "SELECT output_md FROM phase_outputs "
            "WHERE job_id = CAST(:j AS uuid) AND phase_name = :p AND status = 'done'"
        ), {"j": str(job_id), "p": phase_name})).scalar_one_or_none()
    if not row:
        raise SystemExit(f"specimen missing: {job_id} {phase_name}")
    return row


async def _toc_row(book_id: str, title: str) -> tuple[str, int, int]:
    async with SessionLocal() as s:
        row = (await s.execute(text(
            "SELECT COALESCE(section_number, ''), page_start, page_end FROM toc_entries "
            "WHERE book_id = CAST(:b AS uuid) AND section_title = :t LIMIT 1"
        ), {"b": book_id, "t": title})).first()
    if row is None:
        raise SystemExit(f"toc entry not found: {book_id} {title!r}")
    return row[0], row[1], row[2]


async def probe_extract() -> list[dict]:
    out = []
    for book_id, title, label in EXTRACT_SPECIMENS:
        number, ps, pe = await _toc_row(book_id, title)
        pdf = storage.book_pdf_path(UUID(book_id))
        if not pdf.exists():
            raise SystemExit(f"PDF missing for {label}: {pdf}")
        book_text = agent.read_whole_book_text(pdf)
        md, tin, tout = await agent.summarize_lesson(
            provider=EXTRACT_PROVIDER, model=EXTRACT_MODEL, book_text=book_text,
            section_title=title, section_number=number, page_start=ps, page_end=pe,
            homework_job_id=None, phase_output_id=None, transport="api",
        )
        heads = _headings(md)
        low = md.lower()
        out.append({
            "label": label, "headings": heads,
            "has_vocabulary_heading": any("vocabular" in h.lower() for h in heads),
            "has_passages_heading": any(
                "source sentence" in h.lower() or "passage" in h.lower() for h in heads),
            "gloss_arrow_lines": len(re.findall(r"(?m)^\s*[-*].*(—|->|→|:).+$", md)),
            "quoted_sentences": low.count('"') // 2,
            "chars": len(md), "tokens_in": tin, "tokens_out": tout,
            "extract_md": md,
        })
        print(f"[extract:{label}] headings={heads} chars={len(md)}")
    return out


async def _judge_replays(*, label: str, subject: str, output_md: str,
                         lesson_context: str, needles: tuple[str, ...]) -> list[dict]:
    out = []
    for i in range(REPLAYS):
        v = await phase_judge.judge(
            subject=subject, phase_name="flashcards", output_md=output_md,
            lesson_context=lesson_context, prior_outputs={},
            gen_provider="gemini", gen_model="gemini-3.6-flash",
            judge_provider=JUDGE_PROVIDER, judge_model=JUDGE_MODEL,
            transport="api", output_language="uz",
        )
        out.append({
            "arm": label, "replay": i, "available": v.available, "passed": v.passed,
            "has_major": v.has_major, "cites_mutation": _cites(v.warnings, needles),
            "warnings": v.warnings,
        })
        print(f"[judge:{label}#{i}] available={v.available} major={v.has_major} "
              f"cites={_cites(v.warnings, needles)} warnings={v.warnings}")
    return out


async def probe_judge() -> list[dict]:
    lesson_context = await _fetch_phase(JUDGE_SPECIMEN, "extract")
    base = await _fetch_phase(JUDGE_SPECIMEN, "flashcards")
    out = []
    for arm, card_id, new_block, needles in JUDGE_ARMS:
        output_md = base if card_id is None else _replace_card(base, card_id, new_block)
        out += await _judge_replays(label=arm, subject="english", output_md=output_md,
                                    lesson_context=lesson_context, needles=needles)
    # Regression arms: fact-dense decks, UNMUTATED. New majors here would mean
    # the exception reopened the tax 0159 closed.
    for label, job_id, subject in REGRESSION_SPECIMENS:
        out += await _judge_replays(
            label=f"regression:{label}", subject=subject,
            output_md=await _fetch_phase(job_id, "flashcards"),
            lesson_context=await _fetch_phase(job_id, "extract"), needles=(),
        )
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["before", "after"])
    args = ap.parse_args()
    data = {"label": args.label,
            "extract_probe": await probe_extract(),
            "judge_probe": await probe_judge()}
    dest = Path(__file__).with_name(
        f"2026-08-07-language-fidelity-probe-data-{args.label}.json")
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the probe**

```bash
cd /Users/macmini5/Documents/HCGA-lang-fidelity
set -a; . /Users/macmini5/Documents/Homework-Content-Generation-Automation/.env; set +a
export VAR_DIR=/Users/macmini5/Documents/Homework-Content-Generation-Automation/var
uv run python docs/research/2026-08-07-language-fidelity-probe.py --label before
```

Expected: 3 extract calls + 15 judge calls complete, one JSON file written. If the script exits with `card … not found in specimen`, read the real deck (`psql -U macmini5 -d edu_copy -tAc "SELECT output_md FROM phase_outputs WHERE job_id='a334b3cf-e258-436d-89e1-dbe05741dd1a' AND phase_name='flashcards';"`) and point the arm at a card id that exists — keep the whole-block replacement and the arm's meaning; do not fall back to a partial-line edit.

- [ ] **Step 3: Evaluate the gate and write the findings**

Read the JSON and record the verdict in `docs/research/2026-08-07-language-fidelity-probe.md` — the two questions, the raw per-arm counts, and the decision.

**Definition used by every rule below.** An arm "fires" when a replay has `has_major=True` **and** the failure text is about the injected card. `cites_mutation` is a keyword hint, not the decision — read the `warnings` strings yourself and judge whether the major names the mutation or something unrelated. A bare `has_major` is never sufficient evidence: a real deck can carry an unrelated major, and a gate that cannot tell the two apart is what made the first draft's specimen unusable.

**Gate rules (apply in this order — the confound check comes FIRST):**

1. **Confound check — `control` arm.** If the untouched deck majors in ≥2 of 3 replays, the specimen carries a pre-existing major. Record what it is. If that major is about a card an arm also mutates, the specimen is unusable: pick another `judge_status='ok'`, in-band english deck (query in Step 2's note) and re-run. Do not proceed on a confounded specimen.
2. **Instrument check — `contradiction` arm.** If it does not fire in ≥2 of 3 replays, the probe is not measuring what it claims. **STOP.** Do not proceed to any task. Report to the controller: the judge is not majoring even a plain source contradiction, which invalidates the plan's premise and needs its own diagnosis.
3. **Limb 1 (judge) — `absent_false` arm.**
   - Fires in ≥2 of 3 replays → **limb 1 is already handled by the live system. SKIP Task 1 entirely**, record it in the findings doc, and continue from Task 2. This is a real possible outcome and the honest one to accept.
   - Does not fire in ≥2 of 3 → **limb 1 CONFIRMED**; Task 1 proceeds.
   - Majors that fire on something OTHER than the duck card count as "does not fire" for this rule, and get written down separately.
   - **"Fires" here means specifically: a major asserting that the duck card's GLOSS IS FALSE** (a duck is not a rodent). It does **not** include a major complaining that the deck has *dropped* the "turn on / turn off" concept — the extract enumerates that phrasal verb, so removing its card is a legitimate omission the judge can major on today, under the unchanged rule, and such a major may well name the duck card as the thing that replaced it. Counting it would send the gate false-green into "SKIP Task 1" — the same failure direction as the first draft's contaminated arm, and the last remaining path into it. No clean victim card exists in this deck (all 10 map to extract-enumerated items), so this is resolved by definition, not by choosing a different card.
4. **Regression baseline — the two `regression:*` arms.** Record `has_major` per replay. This is the `before` half of the 0159-preservation measurement; Task 4 compares against it. No gate action here — it only becomes a gate in Task 4.
5. **Limb 2 (extract).** Record `has_vocabulary_heading` / `has_passages_heading` / `gloss_arrow_lines` / `chars` per specimen. Task 2 proceeds regardless (the heading is contractually absent — the probe measures how much vocabulary leaks through `## Concepts & terms` anyway, which is the baseline Task 4 must beat), but if all three specimens already carry a full glossed vocabulary inventory under the existing headings, say so plainly and flag it to the controller before Task 2.

- [ ] **Step 4: Commit**

```bash
test "$(git branch --show-current)" = "feat/language-fidelity-judge" || exit 1
git add docs/research/2026-08-07-language-fidelity-probe.py \
        docs/research/2026-08-07-language-fidelity-probe-data-before.json \
        docs/research/2026-08-07-language-fidelity-probe.md
git commit -m "research: mechanism probe for the language-fidelity gap (before)

Whole-card mutations on an in-band judge_status=ok specimen, verdicts keyed to
the failure text citing the injected card, plus unmutated math+geo regression
arms so the 0159 regen-tax question is measured rather than asserted."
```

---

### Task 1: Judge — absent AND demonstrably false is `major`

**Skip this task entirely if Task 0's gate rule 2 said so.**

**Files:**
- Modify: `app/services/phase_judge.py:77-89` (`_FIDELITY_RULE`)
- Test: `tests/services/test_phase_judge.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `phase_judge._FIDELITY_RULE` (module-level `str`) containing the literal substrings `"demonstrably FALSE"`, `"state the correction"` and `"drop it to `minor`"`. No signature changes anywhere — `judge()`, `_build_judge_prompt()`, `Failure`, `Verdict` and `JudgeOutcome` are untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_phase_judge.py`:

```python
def test_fidelity_rule_majors_absent_but_demonstrably_false():
    """Plan 2026-08-07: the exempt zone is UNVERIFIABILITY, not source silence.
    0159 correctly stopped absent-but-TRUE claims from majoring; its probes never
    covered absent-and-FALSE (a wrong gloss), which the old wording also swallowed."""
    rule = pj._FIDELITY_RULE
    # the 0159 anchor survives untouched
    assert "CONTRADICTS" in rule
    assert "merely ABSENT" in rule and "`minor`" in rule
    # the new third case
    assert "demonstrably FALSE" in rule
    low = rule.lower()
    assert "gloss" in low or "translation" in low     # names the language failure mode
    assert "unit" in low or "definition" in low       # and the non-language ones


def test_fidelity_rule_requires_a_stated_correction_before_majoring():
    """Anti-false-positive guard: the judge may only escalate a source-absent claim
    when it can state what the truth IS. Unable to state it => back to `minor`."""
    rule = pj._FIDELITY_RULE
    assert "state the correction" in rule
    assert "drop it to `minor`" in rule


def test_fidelity_rule_still_exempts_generated_teaching_values():
    """The R14/R25 regen-tax guard must survive verbatim: math's invented practice
    numbers, worked-example arithmetic and student names are never fidelity failures."""
    rule = pj._FIDELITY_RULE
    assert "practice-problem values" in rule
    assert "invented student names" in rule
    assert "are NOT fidelity violations" in rule


def test_judge_prompt_carries_the_absent_false_exception():
    p = pj._build_judge_prompt(contract="C", output_md="O")
    assert "demonstrably FALSE" in p
    assert "merely ABSENT" in p          # both cases reach the model in one prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/services/test_phase_judge.py -q -k "absent_false or demonstrably or stated_correction or teaching_values"
```

Expected: FAIL — `assert "demonstrably FALSE" in rule` (the current rule has no such clause). `test_fidelity_rule_still_exempts_generated_teaching_values` should PASS already; that is intentional — it is a regression pin, not a new behavior.

- [ ] **Step 3: Write the implementation**

In `app/services/phase_judge.py`, replace `_FIDELITY_RULE` (lines 77-89) with:

```python
_FIDELITY_RULE = (
    "\n\nSource-fidelity (CRITICAL): a LESSON CONTEXT section is provided below — the lesson "
    "the output was authored from. Treat it as ground truth for contradictions: raise a "
    "`major` failure for any factual claim ABOUT THE WORLD in the OUTPUT that CONTRADICTS "
    "the LESSON CONTEXT (a changed date, number, name, definition, rule, or causal claim). "
    "A world claim that is merely ABSENT from the LESSON CONTEXT but not contradicted by it "
    "(supporting context, standard curriculum facts) is at most `minor` — its absence alone is "
    "never `major`, never a reason to regenerate. "
    "EXCEPTION — absent AND demonstrably FALSE: source silence is not a licence to be wrong. "
    "When the OUTPUT states something the LESSON CONTEXT does not mention and you can show it "
    "is FALSE on its own terms — a wrong translation or gloss, a word that does not mean what "
    "the OUTPUT says it means, a sentence presented as a model that no native speaker would "
    "say or that is ungrammatical, a wrong definition, rule, formula or unit — that IS `major`. "
    "To raise one you MUST state the correction in `evidence`: quote the offending text, then "
    "give the correct form. If you cannot state the correct form, you have not shown it is "
    "false — drop it to `minor`. "
    "DO NOT flag numbers the OUTPUT generates for teaching — "
    "practice-problem values, worked-example arithmetic, invented student names, hypothetical "
    "scenarios — these are expected and are NOT fidelity violations. A hint list of candidate "
    "issues may appear below; verify each against the LESSON CONTEXT before trusting it, and "
    "drop any you cannot substantiate."
)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/services/test_phase_judge.py -q
uv run python -m pytest tests/ -q
```

Expected: `test_phase_judge.py` all green (including the pre-existing `test_fidelity_rule_downgrades_absence_to_minor` and `test_judge_prompt_carries_reanchored_rule`, which must still pass — the 0159 anchor is preserved verbatim). Full suite: 2083+ passed, 0 failed.

- [ ] **Step 5: Re-anchor the repo's own fidelity smoke, which is stale**

`scripts/smoke_judge_fidelity.py` dates from worklog 0079 and was never updated by 0159. Its case (a) asserts `has_major=True` for an **invented year absent from the source** — exactly the expectation 0159 reversed — so the script has been silently wrong for two weeks (it lives under `scripts/`, not `tests/`, so the suite never caught it). It also runs the retired cli transport. This change does **not** restore its old expectation: a plausible invented year is absent-but-not-*demonstrably-false*, so it stays `minor`.

Rewrite it to the three cases the rule now defines, over `transport="api"` (`phase_judge.judge(..., transport="api", judge_provider="gemini", judge_model="gemini-3.5-flash")`):

- **(a) absent but plausible** — the existing invented-`1991` case: expect `has_major=False`. Update the docstring and the pass/fail text to say the 0159 anchor is what is being proven, not an invented-fact catch.
- **(b) generated math values** — keep verbatim: expect `has_major=False`.
- **(c) absent AND demonstrably false** — new: a flashcard whose `back` glosses an English word with a meaning it does not have, against a lesson context that never glosses the word. Expect `has_major=True` and a failure whose text states the correct meaning (that is the substantiation requirement doing its job).

Run it and paste the output into the Task 4 findings doc:

```bash
set -a; . /Users/macmini5/Documents/Homework-Content-Generation-Automation/.env; set +a
uv run python -m scripts.smoke_judge_fidelity
```

Expected: `SMOKE PASS`. Cost: 3 judge calls on `gemini-3.5-flash`, well under $0.05.

- [ ] **Step 6: Commit**

```bash
test "$(git branch --show-current)" = "feat/language-fidelity-judge" || exit 1
git add app/services/phase_judge.py tests/services/test_phase_judge.py \
        scripts/smoke_judge_fidelity.py
git commit -m "feat(judge): absent AND demonstrably false is major, not minor

Source silence is not a licence to be wrong. The 0159 re-anchor defined the
exempt zone by source silence rather than unverifiability, so a wrong gloss of
a word the source never defines was capped at minor and could never trigger the
one regen. Adds a third case gated on stating the correction, so absence alone
and generated teaching values stay exempt.

Also re-anchors scripts/smoke_judge_fidelity.py, which still asserted the
pre-0159 expectation (invented year => major) and ran the retired cli transport."
```

---

### Task 2: Extract — carry vocabulary and model sentences forward

**Files:**
- Modify: `app/services/agent.py:2400-2418` (`_CONTRACT_INSTRUCTIONS`)
- Modify: `app/services/pipeline.py:1373` (`"builtin:extract:v3"` → `"builtin:extract:v4"`)
- Test: `tests/services/test_agent.py` (contract-header assertions, ~line 836)
- Test: `tests/services/test_pipeline_coverage.py:5-6` (cache-key pin)
- Test: `tests/repositories/test_extract_reuse_key.py:17` (cache-key pin)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `agent._CONTRACT_INSTRUCTIONS` containing the literal headings `## Vocabulary & set phrases` and `## Source sentences & passages` (both reach `_SUMMARIZE_LESSON_PROMPT` and `_SUMMARIZE_VISION_PROMPT` by concatenation, unchanged). The extract prompt-cache key string becomes `"builtin:extract:v4"`. Task 3 keys its `content_lint` needles off these exact heading words.

- [ ] **Step 1: Write the failing tests**

In `tests/services/test_agent.py`, replace the `_REQUIRED_HEADERS` list (line 838) and append two tests after `test_extract_prompts_specify_the_contract_headers`:

```python
_REQUIRED_HEADERS = [
    "## Concepts", "## Rules", "## Formulas", "## Worked-example types", "## Key facts",
    "## Vocabulary & set phrases", "## Source sentences & passages",
]


def test_extract_contract_requires_vocabulary_when_the_lesson_teaches_words():
    """Plan 2026-08-07: the five original headings carry no lexical inventory, so a
    language lesson's word list never reached the generator — which the flashcards
    contract nonetheless requires `vocabulary` cards from."""
    c = agent_module._CONTRACT_INSTRUCTIONS
    assert "## Vocabulary & set phrases" in c
    low = c.lower()
    assert "required whenever the lesson teaches words" in low
    assert "source's own gloss" in low or "source's own wording" in low


def test_extract_contract_quotes_model_sentences_verbatim():
    c = agent_module._CONTRACT_INSTRUCTIONS
    assert "## Source sentences & passages" in c
    low = c.lower()
    assert "verbatim" in low
    assert "never paraphrase" in low


def test_extract_contract_disambiguates_vocabulary_from_concepts():
    """Without this line the model splits the same items across two headings."""
    low = agent_module._CONTRACT_INSTRUCTIONS.lower()
    assert "must be able to use" in low
```

`tests/services/test_agent.py:37` already has `from app.services import agent as agent_module`, so `agent_module._CONTRACT_INSTRUCTIONS` resolves without a new import.

In `tests/services/test_pipeline_coverage.py`, the whole first test (lines 1-6) becomes — note the **function name** carries the version too, so rename it or the file ships a test called `..._is_v3` asserting v4:

```python
def test_extract_prompt_hash_is_v4():
    import inspect
    from app.services import pipeline
    src = inspect.getsource(pipeline)
    assert '"builtin:extract:v4"' in src
    assert '"builtin:extract:v3"' not in src
```

In `tests/repositories/test_extract_reuse_key.py`, change line 17 to:

```python
_PROMPT_HASH = "builtin:extract:v4"  # bumped with the vocabulary/passages contract headings
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/services/test_agent.py -q -k "extract_contract or extract_prompts"
uv run python -m pytest tests/services/test_pipeline_coverage.py -q
```

Expected: FAIL — `'## Vocabulary & set phrases' missing from extract prompt`, and `assert '"builtin:extract:v4"' in src`.

- [ ] **Step 3: Write the implementation**

In `app/services/agent.py`, replace `_CONTRACT_INSTRUCTIONS` with:

```python
_CONTRACT_INSTRUCTIONS = """Write the summary as an ENUMERATED COVERAGE CONTRACT so that \
every downstream generator can see the full inventory of what this lesson teaches. \
Begin with ONE short sentence naming the lesson (the gist). Then emit ONLY these \
section headings, using the EXACT English words below (do NOT translate the headings), \
with the ITEMS written in the lesson's language:

## Concepts & terms
## Rules & theorems
## Formulas
## Worked-example types
## Key facts
## Vocabulary & set phrases
## Source sentences & passages

Under each heading list one bullet ("- ") per item. OMIT a heading entirely if the \
lesson has no such items (e.g. a history lesson usually has no Formulas). \
"## Worked-example types" is REQUIRED whenever the lesson contains any worked example, \
sample problem, or solved exercise — list the TYPE of each (what the student must be able \
to solve), not the full worked solution. \
"## Vocabulary & set phrases" is REQUIRED whenever the lesson teaches words, phrases or set \
expressions the student must be able to USE (a language lesson's word list, a science \
lesson's new terms) — one bullet per item as `item — meaning`, taking the meaning from the \
source's own gloss or wording; never supply a meaning the source does not give. This differs \
from "## Concepts & terms", which carries the IDEAS the lesson explains. List every such item; \
do not sample. \
"## Source sentences & passages" is REQUIRED whenever the lesson presents model sentences, \
example dialogue, or a reading text the student learns from — quote them VERBATIM (up to 10 \
sentences; for a long reading text quote its key sentences), never paraphrase them, and never \
compose a sentence of your own here. \
Be complete but concise: capture every distinct \
teachable item, especially the problem/exercise types, and do not invent items absent \
from the source."""
```

In `app/services/pipeline.py:1373`, change:

```python
        prompt_hash = "builtin:extract:v4"
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/services/test_agent.py tests/services/test_pipeline_coverage.py tests/repositories/test_extract_reuse_key.py -q
uv run python -m pytest tests/ -q
```

Expected: all green. Note `tests/repositories/test_extract_reuse_key.py` is a real-DB test — it will report `skipped` without `RUN_DB_INTEGRATION=1`; that is the normal bar and is fine.

- [ ] **Step 5: Commit**

```bash
test "$(git branch --show-current)" = "feat/language-fidelity-judge" || exit 1
git add app/services/agent.py app/services/pipeline.py \
        tests/services/test_agent.py tests/services/test_pipeline_coverage.py \
        tests/repositories/test_extract_reuse_key.py
git commit -m "feat(extract): carry vocabulary and verbatim model sentences forward

The five-heading coverage contract has no lexical inventory and no source
passages, so a language lesson reached the generator with nothing to build
vocabulary cards or example sentences from but invention -- while the
languages-family flashcards contract explicitly requires vocabulary cards and
forbids authoring a fresh passage when the textbook has one. Two conditional
headings in the contract's existing OMIT-if-none idiom, so no subject plumbing.

Cache key bumped v3 -> v4: find_latest_extract keys cross-job reuse on it, so
without the bump every already-extracted section would serve a pre-change
extract forever and this change would reach no real job."
```

---

### Task 3: content_lint recognizes the two new contract sections

Without this, the new sections are invisible to `parse_extract_contract`, so `lint_coverage` cannot warn about a vocabulary item the packet dropped, and a vocabulary-only extract does not count as a parseable contract for Gate B (`agent.validate_extract_summary` → `content_lint.contract_has_items`).

**Files:**
- Modify: `app/services/content_lint.py:170-176` (`_CONTRACT_SECTION_NEEDLES`) and `:446` (`_COVERAGE_SECTIONS`)
- Test: `tests/services/test_content_lint.py`

**Interfaces:**
- Consumes: the exact heading words produced by Task 2.
- Produces: `parse_extract_contract` returns the canonical keys `"vocabulary"` and `"source_sentences"` alongside the existing five. `_COVERAGE_SECTIONS` gains `"vocabulary"` only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_content_lint.py`:

```python
_LANG_CONTRACT = """## Concepts & terms
- Adverbs of manner
## Vocabulary & set phrases
- nephew — the son of your brother or sister
- niece — the daughter of your brother or sister
## Source sentences & passages
- "Jana plays the piano really well."
"""


def test_parse_extract_contract_recognizes_vocabulary_and_source_sentences():
    parsed = cl.parse_extract_contract(_LANG_CONTRACT)
    assert len(parsed["vocabulary"]) == 2
    assert parsed["source_sentences"] == ['"Jana plays the piano really well."']
    assert parsed["concepts"] == ["Adverbs of manner"]


def test_vocabulary_only_extract_is_a_parseable_contract():
    """Gate B (agent.validate_extract_summary) passes any output that parses to >=1
    recognized section; a language lesson may legitimately be vocabulary-only."""
    assert cl.contract_has_items(
        "## Vocabulary & set phrases\n- nephew — the son of your brother or sister\n"
    ) is True


def test_coverage_lint_flags_a_dropped_vocabulary_item():
    packet = "Flash cards about nephews and adverbs."
    findings = cl.lint_coverage(_LANG_CONTRACT, packet)
    assert len(findings) == 1
    assert findings[0].code == "coverage_thin"
    assert "niece" in findings[0].message


def test_coverage_lint_ignores_source_sentences():
    """Verbatim source sentences are material to build FROM, not items the packet
    must echo -- counting them would flood coverage_thin with false warnings."""
    contract = '## Source sentences & passages\n- "Jana plays the piano really well."\n'
    assert cl.lint_coverage(contract, "A packet that quotes nothing verbatim.") == []
```

`tests/services/test_content_lint.py:5` already has `from app.services import content_lint as cl`, so no new import is needed.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m pytest tests/services/test_content_lint.py -q -k "vocabulary or source_sentences"
```

Expected: the first three FAIL with `KeyError: 'vocabulary'` (the needles do not exist yet). `test_coverage_lint_ignores_source_sentences` **passes already** — with no needle, the contract parses to `{}` and `lint_coverage` short-circuits to `[]` for the right answer by the wrong route. It is a pin against a future needle being added to `_COVERAGE_SECTIONS`, not a RED test; do not treat its early green as a problem.

- [ ] **Step 3: Write the implementation**

In `app/services/content_lint.py`, extend `_CONTRACT_SECTION_NEEDLES` (append at the end — order is specific-first and neither new needle can be shadowed by, or shadow, an existing one):

```python
_CONTRACT_SECTION_NEEDLES = [
    ("worked_example_types", ("worked", "example")),  # "Worked-example types"
    ("rules_theorems", ("rule", "theorem")),          # "Rules & theorems"
    ("key_facts", ("key fact",)),                     # "Key facts"
    ("concepts", ("concept", "term")),                # "Concepts & terms"
    ("formulas", ("formula",)),                       # "Formulas"
    ("source_sentences", ("source sentence", "passage")),  # "Source sentences & passages"
    ("vocabulary", ("vocabular", "set phrase")),      # "Vocabulary & set phrases"
]
```

Note the ordering within the two additions: `source_sentences` is listed **before** `vocabulary` for symmetry with the specific-first convention; neither needle appears in the other's heading, so first-hit-wins is unambiguous either way.

And extend `_COVERAGE_SECTIONS` (line 446) — `vocabulary` only:

```python
_COVERAGE_SECTIONS = ("concepts", "rules_theorems", "worked_example_types", "key_facts",
                      "vocabulary")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m pytest tests/services/test_content_lint.py -q
uv run python -m pytest tests/ -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
test "$(git branch --show-current)" = "feat/language-fidelity-judge" || exit 1
git add app/services/content_lint.py tests/services/test_content_lint.py
git commit -m "feat(lint): recognize the vocabulary and source-sentence contract sections

parse_extract_contract skipped the two new headings, so a dropped vocabulary
item raised no coverage_thin warning and a vocabulary-only extract did not
count as a parseable contract for Gate B. Source sentences stay out of the
coverage denominator -- they are material to build from, not items to echo."
```

---

### Task 4: Acceptance gate — re-run the probe, then a real language packet

**Files:**
- Modify (generated): `docs/research/2026-08-07-language-fidelity-probe-data-after.json`
- Modify: `docs/research/2026-08-07-language-fidelity-probe.md` (before/after section)

**Interfaces:**
- Consumes: the `--label before` JSON from Task 0 and every production change from Tasks 1-3.
- Produces: the before/after evidence the worklog cites.

- [ ] **Step 1: Re-run the probe against the changed system**

```bash
cd /Users/macmini5/Documents/HCGA-lang-fidelity
set -a; . /Users/macmini5/Documents/Homework-Content-Generation-Automation/.env; set +a
export VAR_DIR=/Users/macmini5/Documents/Homework-Content-Generation-Automation/var
uv run python docs/research/2026-08-07-language-fidelity-probe.py --label after
```

- [ ] **Step 2: Check the two acceptance criteria**

Compare `-before.json` and `-after.json`:

1. **Extract limb:** `has_vocabulary_heading` is `True` for the two english specimens in `after` (adabiyot G9 is a literature lesson and may legitimately have no vocabulary section — do not treat its absence as a failure), and `has_passages_heading` is `True` for at least the `english-g8-families` specimen (its source lesson contains a reading text). Record the per-specimen deltas.
2. **Judge limb** (skip if Task 1 was skipped): the `absent_false` arm **fires** in ≥2 of 3 replays — using Task 0's exact definition, i.e. a major asserting the duck card's **gloss is false**, NOT a major about the dropped "turn on / turn off" concept even when its text names the duck card — while the `control` arm does NOT gain a new major it did not have in `before`. A control that newly majors means the exception is over-firing — report it and stop rather than shipping.
3. **0159-preservation gate (the regen-tax check):** neither `regression:math-g11-prizma` nor `regression:geo-g10-braziliya` gains a major it did not have in `before` — 6 replays across two fact-dense subjects, unmutated. This is the only measurement in the plan that tests whether the "state the correction" brake actually holds against a judge willing to assert a correction it invented; the prompt-text unit tests cannot. **If either regression arm newly majors, do not ship.** Tighten the exception (e.g. require the contradicted claim to be one the OUTPUT presents as a definition or gloss, not any incidental statement) and re-run before proceeding.

If criterion 1 fails, the heading text is not landing — inspect the returned `extract_md` in the JSON, tighten the REQUIRED-whenever wording in `_CONTRACT_INSTRUCTIONS`, re-run. Do not proceed on a failed criterion.

- [ ] **Step 3: Generate one real language packet end-to-end**

One job, one lesson, over the transport production uses. Pick a not-yet-generated english G9 section (book `120888c6-8bbb-4a9f-adbb-6bce6fe250e3`) so no existing packet is disturbed:

```bash
psql -U macmini5 -d edu_copy -c "SELECT t.id, t.section_number, left(t.section_title,50) FROM toc_entries t LEFT JOIN homework_jobs j ON j.toc_entry_id = t.id WHERE t.book_id='120888c6-8bbb-4a9f-adbb-6bce6fe250e3' AND j.id IS NULL ORDER BY t.order_index LIMIT 10;"
```

**The user starts the server** (see the standing "own the process" rule) — ask, do not launch it yourself. Then launch the one job against it:

```bash
curl -s -X POST "http://127.0.0.1:8000/api/v1/books/120888c6-8bbb-4a9f-adbb-6bce6fe250e3/sections/<toc-entry-id>/generate" \
  -H "Authorization: Bearer $AUTH_TOKEN" -H "Content-Type: application/json" \
  -d '{"provider":"gemini","model":"gemini-3.6-flash","transport":"api","output_language":"uz"}'
```

Poll until `status=done`, then read the result:

```bash
psql -U macmini5 -d edu_copy -c "SELECT phase_name, judge_status, jsonb_array_length(COALESCE(validation_warnings,'[]'::jsonb)) AS warns FROM phase_outputs WHERE job_id='<new-job-id>' ORDER BY phase_order;"
```

Hand-read the `extract`, `flashcards` and `practice-sentence` outputs and record:
- does the extract carry a real glossed vocabulary list and verbatim source sentences?
- do the flashcards' `vocabulary` cards and the practice-sentence items now use source words with source meanings, rather than invented ones?
- did any phase regenerate on a fidelity major, and was that major correct?

- [ ] **Step 4: Report the spend**

```bash
psql -U macmini5 -d edu_copy -c "SELECT operation, count(*), sum(prompt_tokens) AS in_tok, sum(output_tokens) AS out_tok FROM agent_usages WHERE created_at > '<task-0-start-timestamp>' GROUP BY 1 ORDER BY 2 DESC;"
```

Convert with `app/services/pricing.py` rates and record the total in the findings doc. Budget: Task 0 + Task 4 ≤ $4 combined.

- [ ] **Step 5: Commit**

```bash
test "$(git branch --show-current)" = "feat/language-fidelity-judge" || exit 1
git add docs/research/2026-08-07-language-fidelity-probe-data-after.json \
        docs/research/2026-08-07-language-fidelity-probe.md
git commit -m "research: after-probe + live language packet — acceptance evidence"
```

---

### Task 5: Finish — worklog, backlog, plan move, de-stale the reference docs

**Files:**
- Modify: `docs/memory/MASTER_MEMORY.md` (new worklog entry)
- Modify: `docs/memory/INDEX.md` (new row)
- Modify: `docs/memory/WISHLIST.md` (new `language-drill-solver-gap-1` entry)
- Modify: `docs/memory/ROADMAP.md` (R25 cross-reference)
- Modify: `docs/HOW_IT_WORKS.md:368-370` (contract headings) and `:422-423` (fidelity rule)
- Modify: `docs/CODE_MAP.md:31` (`agent.py` contract headings), `:38` (`pipeline.py` cache key `v3`→`v4`), `:42` (`phase_judge.py` fidelity rule)
- Rename: `docs/superpowers/plans/2026-08-07-language-fidelity-judge.md` → `docs/superpowers/plans/shipped/`

- [ ] **Step 0: If Task 0's gate skipped Task 1, re-anchor the stale smoke here**

`scripts/smoke_judge_fidelity.py` is stale regardless of the gate outcome (its case (a) asserts the pre-0159 expectation and it runs the retired cli transport). If Task 1 ran, this was already done as Task 1 Step 5 — skip. If Task 1 was skipped, do Task 1 Step 5's rewrite now, minus case (c), and commit it separately:

```bash
test "$(git branch --show-current)" = "feat/language-fidelity-judge" || exit 1
git add scripts/smoke_judge_fidelity.py
git commit -m "fix(smoke): re-anchor the fidelity smoke to the 0159 rule and api transport"
```

- [ ] **Step 1: Re-check the worklog number before writing it**

```bash
tail -3 docs/memory/INDEX.md
git fetch origin && git log --oneline HEAD..origin/Nggaev-v2
```

The last entry at plan-writing time was **0163**, so this is **0164** — but another lane may have taken it. Use the real next number from the INDEX tail, and if `origin/Nggaev-v2` has moved, rebase onto it and re-run the full suite before continuing (CLAUDE.md finish rule).

- [ ] **Step 2: Write the worklog entry and INDEX row**

The entry must state: the two limbs; that limb 1 is a gap between 0159's two probes (absent-TRUE and contradicting-FALSE) rather than a reversal of 0159; the before/after probe numbers from Task 4; the cache-key bump and its per-lesson re-extract cost; the measured extract-length delta on the `Vocabulary List` specimen; the actual $ spend; the suite count; "no migration"; and — if Task 0's gate skipped Task 1 — say so plainly rather than describing work that did not happen.

It must also carry an explicit **post-deploy watch item**: after the fleet pulls and restarts, monitor the `flashcards` `major` / `major_shipped` rate for **math-algebra and geografiya** against the pre-change baseline (the R25 numbers, and the `judge_status` query used in this plan's exploration). The regression arms are 6 replays before and 6 after on two decks — enough to catch the exception grossly reopening R25's regen tax, **not** enough to catch a sub-threshold population-scale shift. 0159 itself measured across dozens of calls per arm. The live population is the backstop, and it only works if someone is told to look.

- [ ] **Step 3: File the out-of-scope gap on WISHLIST**

Append to `docs/memory/WISHLIST.md`:

```markdown
- `language-drill-solver-gap-1` — `pipeline._SOLVER_PHASES` covers only the four key-bearing phases (`memory-check`, `practice-error-detection`, `practice-rlc`, `boss-arena`), so `flashcards`, `practice-sentence`, `practice-jigsaw` and `practice-memory-match` — where a language packet's vocabulary, glosses and model sentences actually live — get no independent correctness check at all. The judge grades them against their own contract; the 2026-08-07 language-fidelity plan closed the *severity* hole (a demonstrably-false gloss can now major) but nothing re-derives a gloss independently the way the solver re-derives an answer key. Fix shape: a language-aware solver addendum plus those phases added to `_SOLVER_PHASES` — real per-job cost (+4 solver calls), so it needs its own gate.
```

- [ ] **Step 4: De-stale the reference docs**

- `docs/HOW_IT_WORKS.md:368-370` — the heading list must name all seven headings.
- `docs/HOW_IT_WORKS.md:422-423` — the fidelity-rule sentence currently ends at "never a reason to regenerate"; add the absent-and-false exception and its state-the-correction gate.
- `docs/CODE_MAP.md:31` — the `agent.py` bullet enumerates the five headings; make it seven.
- `docs/CODE_MAP.md:38` — the `pipeline.py` bullet says the cache key is `"builtin:extract:v3"` and explains the v2→v3 bump; update to `v4` and say what invalidating it costs.
- `docs/CODE_MAP.md:42` — the `phase_judge.py` bullet describes `_FIDELITY_RULE`; add the third case.
- `docs/CODE_MAP.md:46` — the `content_lint.py` bullet enumerates the same five headings for `parse_extract_contract` and says "Formulas excluded" for `lint_coverage`. Both go stale: seven headings, and the coverage exclusions become Formulas **and** source-sentences.

- [ ] **Step 5: Move the plan and commit**

```bash
test "$(git branch --show-current)" = "feat/language-fidelity-judge" || exit 1
git mv docs/superpowers/plans/2026-08-07-language-fidelity-judge.md \
       docs/superpowers/plans/shipped/2026-08-07-language-fidelity-judge.md
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/memory/WISHLIST.md \
        docs/memory/ROADMAP.md docs/HOW_IT_WORKS.md docs/CODE_MAP.md
git commit -m "docs: worklog <NNNN> + de-stale refs for the language-fidelity fix; plan -> shipped"
```

- [ ] **Step 6: Rebase check, then hand back**

```bash
git fetch origin
git log HEAD..origin/Nggaev-v2 --oneline
```

If the base has moved, rebase onto `origin/Nggaev-v2`, resolve conflicts, and re-run `uv run python -m pytest tests/ -q` before pushing. Then invoke `superpowers:finishing-a-development-branch` — the user decides push/PR/merge. Do not self-merge.

**Known merge interaction to raise at PR time:** PR #118 (`fix/content-json-gate-corrections`) is open and touches `app/services/prompts.py` (append-only, no overlap with this branch) and `app/services/pipeline.py` (overlap risk near the phase-execution block — this branch changes one line, `1373`). `practice-sentence` — a language-heavy phase — already has a structured JSON contract at `prompts/_general/structured/practice-sentence.md`, gated off by default via `settings.structured_output_enabled` (worklog 0162). When that flag is flipped on, the structured contract will need the same source-vocabulary discipline this plan gives the markdown path; note it in the PR body.
