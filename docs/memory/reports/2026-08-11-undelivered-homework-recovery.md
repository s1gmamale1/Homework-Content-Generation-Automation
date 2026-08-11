# Homework delivery audit — full corpus, both languages (2026-08-11)

**3,429 done jobs verified against live Notion by 14 parallel read-only audits (7 UZ + 7 RU,
one per grade G5–G11).** Supersedes the first version of this report, whose numbers came from
a broken crawl — see "What the first version got wrong" at the end.

## Headline

| | UZ | RU |
|---|---|---|
| done jobs audited | 2,131 | 1,298 |
| **phase defects** | **0** | **0** |
| genuinely undelivered | ~16 | ~82 |
| delivered but trashed | 36 | 0 |

**Every lesson in the corpus has a complete 11-phase generation.** ~134 lessons are missing
from Notion, and **60 of those are a single unmapped subject** (RU biology grade 6).

## Phase completeness — settled

38 UZ jobs carry fewer than 11 content phases (G7 ×11, G8 ×18, G9 ×9). Every one completed
**on or before 2026-06-19**, under the pre-worklog-0067 flow that ran ONE mini-game per
subject instead of four — the missing set is per-subject-constant (history/biology lost
jigsaw+sentence+tictactoe; kimyo/algebra lost jigsaw+memory-match+sentence). **Every one is
superseded by a later 11-phase re-run of the same lesson.**

RU has **no shortfalls at all** — all RU generation post-dates the flip.

Taking the latest done job per `toc_entry_id`, both languages are 100% at exactly 11.

## Delivery gaps by grade

### RU (1,298 jobs)

| grade | jobs | missing | detail |
|---|---|---|---|
| 5 | 200 | 28 → **~1 real** | 27 are repeated rubric rows (`Вспомните` ×9, `Подумайте…` ×12, `Текстовые задачи` ×6); one copy of each IS delivered |
| 6 | 175 | **60** + 24 rubric | biology never archived — unmapped; matematika's 24 are rubric rows |
| 7 | 179 | **0** | Notion holds **+2 duplicate pages** in Алгебра |
| 8 | 227 | **7** | 6 history (stale worker env) + 1 geometry topic |
| 9 | 217 | **11 + 2 partial** | 9 with no page; 2 pages truncated to 3/5 and 1/5 sections |
| 10 | 172 | **1** | `ПРОЕКТНАЯ РАБОТА` title collision, pre-#120 |
| 11 | 128 | **0** | 128/128, full 1:1 title identity |

### UZ (2,131 jobs)

| grade | jobs | missing | detail |
|---|---|---|---|
| 5 | 178 | 5 | `Matnli masalalar` ×7 title collision |
| 6 | 264 | 2 | same collision |
| 7 | 325 | **0** | — |
| 8 | 524 | 4 | `Tarixiy ma'lumotlar` non-lesson rows |
| 9 | 378 | 5 | 4× `Amaliy-tatbiqiy…` collision + 1 adabiyot |
| 10 | 251 | **0** | — |
| 11 | 211 | **36 TRASHED** | archived correctly 2026-07-21, then trashed as a unit |

## Remedy classes — NOT interchangeable

Using the wrong one causes damage.

| remedy | scope |
|---|---|
| **map key + re-archive** | 60 RU biology-6 |
| **plain re-archive** | 6 RU history-8, 11 RU history/biology-9, 1 RU algebra-10 |
| **backfill DB pointer ONLY** | RU biology-11 `d5e63646`, 3 RU matematika-5 — *re-archiving these duplicates* |
| **restore from Notion trash** | 36 UZ math-algebra-11 (+ their container) |
| **unrecoverable** | 1 RU geometry-8 topic (`Повторение курса 7-ого класса`) |
| **do not push** | ~51 RU rubric rows across G5/G6 |

## ⚠️ Recovery hazards — read before any push

1. **Re-archiving repeated-title rows creates DUPLICATES, not clobbers.** `find_or_create`
   (`page_creator.py:19`) reuses a child whose lowercased title matches, but #120's
   `resolve_lesson_title` now suffixes colliding titles (`· p.52 · e0751876`), so a re-push
   will NOT match the existing bare-title page — it creates a second one.
2. **Some lessons are delivered but the DB write-back failed.** RU biology-11 `d5e63646`'s
   page was created the same minute its job completed; 3 RU matematika-5 rows are live in
   Notion while their jobs still carry a skip reason. **Backfill the pointer, do NOT re-archive.**
3. **`NOTION_SUBJECT_PAGES` on the HEAD has no `ru:*|6` keys** (biology, history, matematika
   all absent). API-triggered re-archive runs on the head and will silently skip RU grade 6 —
   the exact subject holding the 60-lesson prize. The fleet workers carry a 273-key map;
   the head does not.
4. **10 RU geometry-8 pages are permanently DELETED** — `pages.retrieve` returns
   `ObjectNotFound`, not `in_trash` (verified against 4 clean control probes). 9 were
   duplicates of an already-delivered book.
5. **Partial pages exist.** Two RU history-9 lessons have pages with only 3/5 and 1/5
   sections. Page counts and title diffs both call them delivered; `notion_archived_at IS NULL`
   is what distinguishes them.
6. **NEVER `--force` a re-archive** before stamps are repaired — force clears and rewrites,
   destroying the surviving owner's content.

## Biology grade 6 — definitive: never written, not lost

Six independent proofs: all 60 jobs carry `notion_skip_reason = "no Notion page for
language=ru biology|6"`; all have NULL `notion_archived_at`; all TOC rows have NULL
`notion_homework_page_id` AND `notion_archived_job_id`; the Notion page has **no container**
(live or otherwise); a workspace-wide search for three distinctive lesson titles found nothing
homework-shaped; and `NOTION_SUBJECT_PAGES` has no `ru:biology|6` key.

**Nothing is in trash. Re-run the archiver.** Page id, harvested 2026-08-11 and still unmapped:

```json
"ru:biology|6": "39c99838-1c76-80b0-9e4e-f2a9881b2f19"
```

Also harvested, and present in the 273-key fleet map but NOT on the head:

```json
"ru:geografiya|5": "38c99838-1c76-80f7-beec-c67dcb072da1",
"ru:matematika|5": "2c999838-1c76-806f-9850-c07bb4dd7276",
"ru:matematika|6": "2c999838-1c76-80f2-933f-c88a8c2bd69f"
```

## Systemic defects found (beyond the delivery gaps)

- **Duplicate generation is expensive.** UZ G8: 524 jobs for 385 lessons — 139 redundant
  re-runs. `kimyo-g7-11` grade 8 ran **112 jobs for 44 lessons (2.5×)**; one lesson has 7 done
  jobs. Notion keeps one page per lesson, so re-runs overwrote each other — pure wasted spend.
  RU is nearly free of this (G5/G7/G9/G10/G11 are strictly one job per lesson).
- **Concurrent `find_or_create` race.** RU Алгебра 7 has 2 duplicated lesson pages, each pair
  created in the same minute (2026-06-30 13:45, 13:46). Read-then-create is not atomic.
- **Container-creation races too.** RU Геометрия 8 has THREE `Generated Homeworks` containers
  (1 + 1 + 55); UZ Биологиya 7 has two (1 + 35). Workspace-wide only these two pages are
  multi-container.
- **Cross-language misrouting (2, both duplicates not losses):** a `ru` biology-11 page sits in
  the **UZ** Biologiya container; a `uz` algebra-9 page sits in the **RU** Алгебра container.
  Both originals are intact in their correct tree.
- **Stale worker env.** The 6 RU history-8 failures all completed 2026-07-27 on exactly two
  worker processes (`Host-02:8424`, `Host-50:15356`) whose `.env` lacked `ru:history|8`, while
  sibling processes on the same hosts archived fine before and after.
- **Skip reasons can lie.** 11 RU history-9 jobs recorded `no Notion page for language=ru
  history|9`, yet `ru:history|9` IS mapped to IDs that exactly match the live pages. It was a
  transient push failure reporting itself as a config gap.
- **Invisibly un-archived jobs.** 22 RU matematika-6 jobs have `notion_archived_at` NULL AND
  `notion_skip_reason` NULL — the exact state `notion_archive.py:38-42` warns about.
- **Generation ran on an unaccepted TOC**: `6_sinf_matematika_darslik_2024_UZ.pdf` is
  `status='toc_review'` yet 22 jobs ran against it.
- **Possible content/scope defect (flagged, not proven):** RU algebra-10 job `f83ad37e` sits on
  TOC rows pp. 58–60 in the *ФУНКЦИИ* chapter but its generated case teaches trigonometric
  formulas, near-identical to its twin in the actual trigonometry chapter. Signature of a
  page-offset or extract-scope problem on generic repeated titles. **A lesson teaching the
  wrong chapter's material passes every check in this audit.**

## Known non-recoverable / do-not-deliver

- **english 8 (uz, 22 lessons)** — ROADMAP R27: the source PDF is truncated (104 pages, TOC to
  157). These packets were built from pages that do not exist. Re-ingest before delivering.
- **geometriya 8** has two byte-different PDFs with **100% identical TOC titles** (61 shared,
  0 unique either side). One is a duplicate upload; recovering both double-files the lessons.

## Column semantics (established by this audit)

- `toc_entries.notion_archived_job_id` records *"we wrote this once"*, **not** *"it is there
  now"* — it stays set for all 36 trashed UZ pages. It also has false negatives: UZ geometry
  and algebra grade 7 carry no stamps yet are 100% delivered; RU algebra-7 has it on only
  29 of 40 delivered rows.
- `toc_entries.notion_homework_page_id` points at the nested **`Homework` sub-page inside the
  lesson page**, not at the lesson page. Joining it against `get_child_pages("Generated
  Homeworks")` yields 0 matches and looks catastrophic. It is, however, the **reliable**
  signal: where it is NULL the lesson really is missing; where it is set the lesson is
  delivered and only the stamp is absent.

## Method, and what the first version got wrong

**Method:** 14 subagents, one per (language, grade), each running SELECTs against `edu_copy`
plus a live Notion crawl of `Lessons → N Grade → N - sinf | N - класс → subject →
Generated Homeworks`. Title diffs were normalised for numbering prefixes; agents were
instructed to report a truthful partial rather than guess, and to treat prior figures as
suspect.

**The first version of this report claimed 422 undelivered packets and asserted the DB's
delivery column was "PROVEN unreliable". Both were wrong.** The crawl behind them selected
the FIRST child page whose title contained "Generated" (`next(...)`). RU Геометрия 8 has three
such containers; the crawl counted 1 and reported "57 claimed vs 1 real", which then became
the evidence for a wholesale distrust of the DB. **Any Notion crawl must sum ALL matching
containers.** The same bug produced the UZ Биологиya 7 and Algebra 11 "proofs".

**Not established:** whether every page counted as delivered is *complete* — only the two RU
history-9 partials were caught, and only because their section depth was checked directly.
Counts elsewhere are page-level. A section-depth sweep across the corpus has not been run.
