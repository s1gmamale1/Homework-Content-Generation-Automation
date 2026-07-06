"""Phase-0 coverage audit for the extract COVERAGE-CONTRACT lane.

Read-only against edu_copy. For each sampled done lesson:
  source (pdftotext of the printed page window)  ->  enumerate core teachable items
  extract (phase_outputs 'extract')              ->  is each item captured?
  packet  (all other phase_outputs)              ->  is each item taught/tested?

One bounded gemini-api call per lesson (gemini-3.1-pro-preview). Prints token
totals for the money-rule log. Writes results JSON to the scratchpad.

Run:  uv run python <path>/coverage_audit.py
"""
import asyncio, json, os, re, subprocess, sys
from pathlib import Path

import app.config  # noqa: F401  -- triggers load_dotenv so os.environ has creds
import asyncpg
from app.services import api_transport

DSN = "postgresql://edu:edu@127.0.0.1:5432/edu_copy"
BOOKS = Path("var/books")
AUDIT_MODEL = "gemini-3.1-pro-preview"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("coverage_audit_results.json")

# job_id, subject, sec-label, book_id, printed page_start, page_end, note
SAMPLE = [
    ("1e8fc0c2-86f0-4496-ab40-0aa86df1f832", "math-algebra", "§5", "50b71915-9d70-49b7-9fd3-151d102fadbe", 27, 29, "COMPACT / Gate-B FP case"),
    ("3179e47d-5549-4036-b3b0-714ff5c1d109", "math-algebra", "§2", "50b71915-9d70-49b7-9fd3-151d102fadbe", 12, 17, "dense 6pp"),
    ("19f32884-504b-4d39-92e7-45f0a9c61ce5", "geometriya-g7-11", "1-mavzu", "860e86aa-a68b-4ca1-b271-5f5cde0e1d6e", 5, 7, "theorems"),
    ("5df1dd08-37dd-4e77-ab46-c0f6ee1a6f33", "geometriya-g7-11", "5-6-mavzu", "860e86aa-a68b-4ca1-b271-5f5cde0e1d6e", 16, 18, "multi-topic rhombus+square"),
    ("08b02e07-...", "kimyo-g7-11", "13-§", "d87e4f5c", 51, 62, "big 12pp"),   # book id filled below
    ("04aa4527-...", "kimyo-g7-11", "16-§", "d87e4f5c", 71, 73, "compact concept"),
    ("6d3bf652-...", "history", "18 Crusades", "41aec815", 106, 115, "10pp names/dates/events"),
    ("7bd23497-...", "history", "10 Saljuqiylar", "41aec815", 62, 65, "compact history"),
    ("1a4f4fa2-...", "biology", "6 Fungi", "9c0e5362", 19, 24, "classification-heavy"),
    ("26942816-...", "english", "Irregular verbs", "d463c690", 158, 160, "reference-list edge case"),
]

AUDIT_PROMPT = """You are auditing homework-generation COVERAGE for one textbook lesson.

You are given three inputs:
1. SOURCE — the raw textbook text of the lesson (locate the lesson titled "{title}", section {sec}; ignore unrelated pages in the window).
2. EXTRACT — the automated summary the pipeline produced from the source (this is what every downstream generator + grader actually reads; they never see the raw source).
3. PACKET — the concatenated homework phases generated for students (flashcards, memory check, practice games, boss quiz, reflection).

TASK:
A. From the SOURCE ALONE, enumerate the lesson's CORE teachable items — the things a student is expected to learn/recall/apply from THIS lesson. For each item give:
   - "label": short name (in the lesson's language is fine)
   - "type": one of concept, term, rule_or_theorem, formula, worked_example_type, procedure, key_fact
   - "central": true if it is a primary teaching point of the lesson, false if secondary/supporting.
B. For each item decide:
   - "in_extract": true if the EXTRACT captures it (mentions/states it such that a downstream generator could teach it), else false.
   - "in_packet": true if ANY packet phase actually teaches, tests, or uses it, else false.

Be strict and evidence-based. Do NOT invent items not grounded in the source. Do NOT count an item as in_extract/in_packet on a vague thematic mention — it must be genuinely present.

Return ONLY a fenced ```json block, an object:
{{"items": [{{"label": "...", "type": "...", "central": true, "in_extract": true, "in_packet": true}}, ...],
  "notes": "one sentence: what class of items (if any) is being lost, and whether the loss is mainly at the extract or in the phases"}}

===== SOURCE =====
{source}
===== END SOURCE =====

===== EXTRACT =====
{extract}
===== END EXTRACT =====

===== PACKET =====
{packet}
===== END PACKET ====="""


def pdftext(book_id: str, ps: int, pe: int) -> str:
    d = next((p for p in BOOKS.glob(f"{book_id}*") if (p / "source.pdf").exists()), None)
    if not d:
        return ""
    f, l = max(1, ps - 3), pe + 3
    out = subprocess.run(
        ["pdftotext", "-f", str(f), "-l", str(l), "-layout", str(d / "source.pdf"), "-"],
        capture_output=True, text=True,
    )
    return out.stdout.strip()


def extract_json(text: str) -> dict:
    m = re.search(r"```json\s*(.+?)```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise ValueError("no json in response")
    return json.loads(m.group(1))


async def load_lesson(conn, job_id: str):
    row = await conn.fetchrow(
        """select j.id, j.subject, t.section_number sec,
                  coalesce(t.section_title,t.chapter_title) title,
                  t.page_start, t.page_end, j.book_id
             from homework_jobs j join toc_entries t on t.id=j.toc_entry_id
            where j.id=$1""", job_id)
    phases = await conn.fetch(
        "select phase_name, output_md from phase_outputs where job_id=$1 and status='done'", job_id)
    extract = next((p["output_md"] for p in phases if p["phase_name"] == "extract"), "") or ""
    packet = "\n\n".join(
        f"### PHASE: {p['phase_name']}\n{p['output_md']}"
        for p in phases if p["phase_name"] != "extract" and p["output_md"])
    return dict(row), extract, packet


async def main():
    conn = await asyncpg.connect(DSN)
    # resolve the truncated job ids by prefix
    results = []
    tin_tot = tout_tot = 0
    for jid_pfx, subject, sec, book_pfx, ps, pe, note in SAMPLE:
        jid = await conn.fetchval(
            "select id from homework_jobs where id::text like $1 and status='done' limit 1",
            jid_pfx.split("-")[0] + "%")
        if not jid:
            print(f"SKIP {jid_pfx} — no done job", file=sys.stderr); continue
        meta, extract, packet = await load_lesson(conn, jid)
        source = pdftext(str(meta["book_id"]), ps, pe)
        if not source:
            print(f"SKIP {jid} — no source text", file=sys.stderr); continue
        prompt = AUDIT_PROMPT.format(
            title=meta["title"], sec=meta["sec"],
            source=source[:120_000], extract=extract, packet=packet[:120_000])
        rc, text, usage, err = await api_transport.generate(
            provider="gemini", model=AUDIT_MODEL, prompt=prompt, attachments=[])
        if rc != 0 or not text:
            print(f"FAIL {jid} rc={rc} err={err[:200]}", file=sys.stderr); continue
        tin_tot += usage.get("prompt_tokens") or 0
        tout_tot += usage.get("output_tokens") or 0
        try:
            parsed = extract_json(text)
        except Exception as e:
            print(f"PARSEFAIL {jid}: {e}\n{text[:400]}", file=sys.stderr); continue
        items = parsed.get("items", [])
        results.append({
            "job": str(jid), "subject": subject, "sec": sec, "note": note,
            "title": meta["title"], "pages": f"{ps}-{pe}",
            "extract_chars": len(extract.strip()), "n_items": len(items),
            "items": items, "notes": parsed.get("notes", ""),
        })
        cov_p = sum(i["in_packet"] for i in items) / len(items) if items else 0
        print(f"OK {subject:16} {sec:14} items={len(items):2} cov_packet={cov_p:.0%} extract={len(extract.strip())}c", file=sys.stderr)
    await conn.close()
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n=== tokens: in={tin_tot:,} out={tout_tot:,} over {len(results)} lessons on {AUDIT_MODEL} ===", file=sys.stderr)
    print(f"wrote {OUT}", file=sys.stderr)


asyncio.run(main())
