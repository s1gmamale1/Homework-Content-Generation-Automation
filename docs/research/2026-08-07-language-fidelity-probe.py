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
import os
import re
import sys
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

# Repo root on sys.path so the script runs from any cwd (same shim as the
# sibling research script 2026-07-20-teaching-audit-drill-density.py).
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

# Load env from an EXPLICIT file before app.config builds its Settings.
# Default is the repo's own .env; set HCGA_ENV_FILE when running from a git
# worktree, which has no .env of its own -- the app's find_dotenv would then
# walk UP the tree and silently bind to a different .env with a different
# DATABASE_URL. Do not source the .env with `set -a` in a shell instead: it
# strips the quoting from JSON-valued vars (NOTION_SUBJECT_PAGES) and pydantic
# then fails to parse them.
_ENV_FILE = Path(os.environ.get("HCGA_ENV_FILE") or (_ROOT / ".env"))
if _ENV_FILE.is_file():
    load_dotenv(_ENV_FILE, override=True)

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.services import agent, phase_judge, storage  # noqa: E402

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
