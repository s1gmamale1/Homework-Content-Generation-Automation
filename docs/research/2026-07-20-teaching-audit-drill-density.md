# Long fact-dense lessons don't teach: the fixed drill budget (2026-07-20)

Backing evidence for **ROADMAP R24**. Two methodologically independent lines of
evidence — a simulated-student exam and a deterministic markdown count — agree
that long, fact-dense lessons produce packets that *cover* their material without
teaching it.

- Re-run the deterministic half: `uv run python docs/research/2026-07-20-teaching-audit-drill-density.py`
  (no model calls, $0) → `docs/research/2026-07-20-teaching-audit-drill-density-data.json`
- Audit half: `uv run python scripts/teaching_audit.py --job <id>` (~$0.20/packet, worklog 0148/0152)

## 1. The audit finding (simulated student, ~$5.40 total)

The teaching audit derives an exam from the **textbook pages only**, then has a
closed-book simulated student sit it before and after "studying" nothing but the
packet. An objective counts as *learned* at ≥75% post-score (1.5 of 2).

| Group | packets | objectives | learned/known | hard-fails (≤0.5/2) | not_taught |
|---|---|---|---|---|---|
| History **long** (7–15pp) | 6 | 31 | **10 (32%)** | 9 | 4 |
| History **short** (0–3pp), *same books/grades/authors* | 4 | 18 | **14 (78%)** | **0** | 1 |
| Non-history **long** (9–13pp) | 4 | 20 | 14 (70%) | 2 | 1 |

The short-history control is the load-bearing comparison: same books, same
authors, same grades — **span was the only variable changed**.

**Worst case is reproducible, not sampling noise.** G8 *"Muhammad Xorazmshohning
mamlakat mudofaasiga oid tadbirlari va buning oqibati"* (15pp) audited **three
independent times → 0 of 17 objectives learned (0%)**, 3 hard-fails every run,
every post-score between 0 and 1 of 2. The examiner derived a slightly different
objective set each run (6/5/6) and the verdict never moved. Nearly every
objective carried `coverage=taught` — the packet *does* cover the lesson.

**This class is structurally invisible to the LLM judge**, which grades a packet
against its own prompt and extract and therefore cannot observe that the packet
under-teaches its source lesson. Only an exam derived from the textbook can.

## 2. The mechanism (deterministic, 1,334 packets, $0)

The flow is a **fixed 11-phase set regardless of lesson size** (`flows.flow_for`:
`_BASE_PHASES` + `_GAMES` + boss-arena + reflection), so the drill budget does not
scale with factual load. Counting discrete facts in each packet's `extract`
(years + numbers + distinct proper nouns) against drill items in the
student-facing phases (list/heading items + question marks):

| Lesson length | n | avg facts | avg drill items | median items/fact |
|---|---|---|---|---|
| short (0–3pp) | 804 | 25.3 | 177.0 | **8.05** |
| mid (4–6pp) | 408 | 44.2 | 180.0 | **4.83** |
| long (7+pp) | 122 | 48.6 | 184.4 | **4.75** |

Drill items move **+4%** (177 → 184) while facts nearly **double** (25 → 49).
Practice volume is effectively constant; the material it must cover is not.

By subject, long lessons only — this is why history is the visible casualty:

| Subject (7+pp) | n | avg facts | avg items | median items/fact |
|---|---|---|---|---|
| **history** | 50 | **81.6** | 175.5 | **2.24** |
| biology | 7 | 38.1 | 178.0 | 4.82 |
| geometry | 23 | 25.2 | 168.3 | 7.75 |
| chemistry | 9 | 20.1 | 218.4 | 10.96 |
| algebra | 17 | 23.8 | 195.5 | 16.07 |
| maths (g6) | 6 | 19.8 | 144.7 | 10.62 |
| physics | 4 | 20.2 | 168.5 | 9.10 |

History isn't special *as history*; it is special as the most fact-dense subject
in the corpus — 3–4× the discrete facts of any other — so the fixed budget divides
furthest. The deterministic side covers **50 long-history packets** against the
audit's 6, retiring the audit's single-corpus sampling limit.

## 3. Limits of this evidence (read before acting)

- **The "facts" proxy favours history by construction.** It counts years, numbers
  and proper nouns, which under-counts method-based subjects whose learnable
  content is procedures. The **length trend is solid**; the **subject ranking is
  softer than the table implies**.
- **Literature (`adabiyot`) is untested** — the other genuinely fact-dense subject,
  with too few completed jobs to appear (n<3) and never audited.
- **Half the audit's `not_learnable` calls sat at 1.0–1.4/2**, just under the bar,
  and such borderline calls flip between runs (established by a 3× repeat of a
  clean short-history packet: 3/3 clean). The finding rests on the **hard-fails
  (0–0.5/2, 9 of them in long history) and the 0/17 repeat**, which noise cannot
  explain.
- **The mechanism is measured; the remedy is not determined.** See R24 for the
  three candidate fix shapes and the open product question about whether the
  audit's contract ("student learns the lesson from the packet alone") is the
  right bar for homework that supplements a taught lesson.
- **The audit's own per-packet raw data was lost** (written to `/tmp`, since
  cleared) — the tables above are transcribed from the run output. This document
  and the committed dataset exist precisely so that never recurs; the deterministic
  half is fully re-runnable.

## 4. Method notes

- Corpus: all `status='done'`, `output_language='uz'` jobs with a TOC page range.
- "Facts" and "drill items" are **surface proxies**, chosen for determinism and
  re-runnability, not semantic precision. They are comparable *across* lessons
  because the same regexes apply everywhere; they are not absolute counts.
- Packets with fewer than 5 detected facts, or no drill phases, are excluded.
- Span is `page_end - page_start` from the TOC (printed page numbers).
- **The corpus grows as generation continues, and the result is stable.** The tables
  above are the 1,334-packet snapshot; an immediate re-run at 1,362 packets gave
  short 8.04 vs 8.05, long 4.82 vs 4.75, and long-history **2.24 items/fact —
  identical**. Expect the committed dataset to lag the live DB; re-run the script
  rather than trusting the JSON's row count.
