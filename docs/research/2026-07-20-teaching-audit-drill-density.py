"""Drill-density measurement for R24 (2026-07-20). No model calls, read-only, $0.

Re-run:  uv run python docs/research/2026-07-20-teaching-audit-drill-density.py
Output:  docs/research/2026-07-20-teaching-audit-drill-density-data.json (one row per packet)

Measures whether the packet's drill budget scales with a lesson's factual load.
Result at time of writing (1,334 done uz packets): it does NOT — drill items are
~flat (177 short -> 184 long, +4%) while facts nearly double (25 -> 49), so
items/fact halves. Long history is the extreme (81.6 facts, 2.24 items/fact).

Hypothesis: the packet's drill budget is ~fixed regardless of lesson size, so
drill-items-per-fact collapses as a lesson's discrete-fact count grows.
Facts are proxied by countable surface markers in the EXTRACT (the lesson's own
factual summary): years, numbers, and capitalised proper nouns.
Drill items are proxied by countable question/card markers in the student-facing
phases (flashcards, memory-check, practices, boss-arena).

NOTE: the corpus takes EVERY done job, not the latest per toc_entry (unlike the
batch rollup's DISTINCT ON), so a re-generated lesson is weighted more than once.
Deliberate — each generated packet is an independent observation of the drill
budget — but it means n is packets, not distinct lessons.
"""
import asyncio, json, pathlib, re, sys
from collections import defaultdict
from dotenv import load_dotenv
_ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root, so any cwd works
load_dotenv(_ROOT / ".env", override=True)  # override: read-only research script must
# hit the repo's configured DB, not whatever DATABASE_URL happens to be ambient (a
# nested worktree's upward .env walk resolves elsewhere). Diverges from the app's
# load_dotenv(override=False) convention deliberately.
sys.path.insert(0, str(_ROOT))
from sqlalchemy import text
from app.db import SessionLocal

# MUST match flows._BASE_PHASES + flows._GAMES + boss-arena exactly — a name that
# doesn't exist is silently skipped by phases.get(p, "") and under-counts every
# packet. (Gate review 2026-07-20 caught "memory-match"/"tictactoe" here; the real
# names carry the `practice-` prefix, so 2 of 9 drill phases were excluded.)
DRILL_PHASES = ("flashcards", "memory-check", "practice-rlc", "practice-jigsaw",
                "practice-error-detection", "practice-sentence", "boss-arena",
                "practice-memory-match", "practice-tictactoe")

YEAR  = re.compile(r"\b1[0-9]{3}\b|\b20[0-2][0-9]\b")
NUM   = re.compile(r"\b\d{1,3}(?:[ ,]\d{3})+\b|\b\d+(?:[.,]\d+)?\s?(?:%|ming|million|mln|km|kg)\b", re.I)
PROP  = re.compile(r"\b[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ’'`ʻ]{3,}\b")
ITEM  = re.compile(r"^\s*(?:[-*]\s|\d+[.)]\s|#{2,4}\s)", re.M)   # list/heading items
QMARK = re.compile(r"\?")

async def main():
    async with SessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT j.id::text, b.subject, b.grade, t.section_title,
                   (t.page_end-t.page_start) AS span
            FROM homework_jobs j JOIN toc_entries t ON t.id=j.toc_entry_id
            JOIN books b ON b.id=j.book_id
            WHERE j.status='done' AND j.output_language='uz'
              AND t.page_start IS NOT NULL AND t.page_end IS NOT NULL
        """))).all()
        jobs = {r[0]: r for r in rows}
        ph = (await s.execute(text(
            "SELECT job_id::text, phase_name, output_md FROM phase_outputs "
            "WHERE status='done' AND job_id::text = ANY(:ids)"),
            {"ids": list(jobs)})).all()

    per = defaultdict(dict)
    for jid, name, md in ph:
        per[jid][name] = md or ""

    recs = []
    for jid, phases in per.items():
        if "extract" not in phases:
            continue
        ex = phases["extract"]
        facts = len(YEAR.findall(ex)) + len(NUM.findall(ex)) + len(set(PROP.findall(ex)))
        drill_md = "\n".join(phases.get(p, "") for p in DRILL_PHASES)
        if not drill_md.strip() or facts < 5:
            continue
        items = len(ITEM.findall(drill_md)) + len(QMARK.findall(drill_md))
        _, subj, grade, title, span = jobs[jid]
        recs.append({"job_id": jid, "subject": subj, "grade": grade, "span": span,
                     "facts": facts, "items": items, "ratio": items / facts})

    print(f"packets analysed: {len(recs)}  (no model calls, $0)\n")

    def band(r):
        return "short (0-3pp)" if r["span"] <= 3 else "mid (4-6pp)" if r["span"] <= 6 else "long (7+pp)"
    for name in ("short (0-3pp)", "mid (4-6pp)", "long (7+pp)"):
        g = [r for r in recs if band(r) == name]
        if not g: continue
        f = sum(x["facts"] for x in g)/len(g); i = sum(x["items"] for x in g)/len(g)
        rr = sorted(x["ratio"] for x in g)[len(g)//2]
        print(f"  {name:<14} n={len(g):<4} avg facts={f:>6.1f}  avg drill items={i:>6.1f}  MEDIAN items/fact={rr:.2f}")

    print("\n  by subject (long lessons only, 7+pp):")
    bysub = defaultdict(list)
    for r in recs:
        if r["span"] >= 7: bysub[r["subject"]].append(r)
    for sub, g in sorted(bysub.items(), key=lambda kv: -len(kv[1])):
        if len(g) < 3: continue
        f = sum(x["facts"] for x in g)/len(g); i = sum(x["items"] for x in g)/len(g)
        rr = sorted(x["ratio"] for x in g)[len(g)//2]
        print(f"    {sub:<18} n={len(g):<4} avg facts={f:>6.1f}  avg items={i:>6.1f}  median items/fact={rr:.2f}")
    json.dump(recs, open(_ROOT / "docs/research/2026-07-20-teaching-audit-drill-density-data.json", "w"), indent=1)
    print("\n  raw -> docs/research/2026-07-20-teaching-audit-drill-density-data.json")
asyncio.run(main())
