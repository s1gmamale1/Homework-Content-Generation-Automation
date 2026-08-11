# Undelivered homework — recovery inventory (2026-08-11)

> **⚠️ SUPERSEDED IN PART — corrected 2026-08-11 after a 7-grade UZ audit.**
> The original version of this report claimed 422 undelivered packets and asserted that
> `toc_entries.notion_archived_job_id` was proven unreliable. **Both claims were wrong,
> and the error was mine**: the crawl that produced them selected the FIRST Notion page
> whose title contained "Generated" (`next(...)`). Several subject pages hold MULTIPLE
> `Generated Homeworks` containers, so the crawl counted one of them and missed the rest.
>
> RU Геометрия 8 was the headline "proof": crawl said 1 page, DB said 57. The subject page
> actually has **three** containers holding 1 + 1 + 55 = **57**. The DB was correct.
> The same bug produced the Биологиya 7 (1 vs 36 — real total 1 + 35) and Algebra 11
> "proofs". **Any future crawl must sum ALL matching containers, never take the first.**
>
> See "Audited truth (UZ)" below. The RU figures further down were produced by the same
> broken method and are **UNVERIFIED** — treat them as suspect until an RU audit runs.

## Audited truth — UZ, all grades, 2026-08-11

Seven parallel read-only audits (DB + live Notion), **2,131 done UZ jobs**.

### Phase completeness: no defects

38 jobs carry fewer than 11 content phases (G7 ×11, G8 ×18, G9 ×9). **Every one is a
pre-worklog-0067 job** — completed on or before 2026-06-19, when the flow ran ONE
mini-game per subject (`SUBJECT_GAME`) instead of all four; the missing set is
per-subject-constant (history/biology lost jigsaw+sentence+tictactoe, kimyo/algebra lost
jigsaw+memory-match+sentence). **Every one is superseded by a later 11-phase re-run of the
same lesson.** Net: **zero UZ lessons lack a complete 11-phase generation.**

### Delivery: 52 real gaps, not 252

| grade | done jobs | all-11 (latest per lesson) | genuinely missing from Notion |
|---|---|---|---|
| 5 | 178 | yes | 5 — `Matnli masalalar` ×7 title collision |
| 6 | 264 | yes | 2 — same collision |
| 7 | 325 | yes | 0 |
| 8 | 524 | yes | 4 — `Tarixiy ma'lumotlar` non-lesson TOC rows |
| 9 | 378 | yes | 5 — 4× `Amaliy-tatbiqiy…` collision + 1 adabiyot |
| 10 | 251 | yes | 0 |
| 11 | 211 | yes | **36 TRASHED, recoverable by restore** |

**~16 undelivered + 36 trashed.** The dominant cause is **duplicate lesson titles inside one
textbook** collapsing onto a single Notion page — the pre-#120 collision, fixed for new
archives on 2026-08-06.

### Grade-11 math: deleted, not missing

All 36 `math-algebra|11` pages plus their `Generated Homeworks` container return
`in_trash=true, archived=true` (verified per-page). Mapping key exists and is correct;
ancestry is correct. Archived 2026-07-21, then trashed as a unit. **Restore from Notion
trash recovers all 36 with no regeneration.** Actor and time unknown from a read-only API.

### What the delivery column actually means

`notion_archived_job_id` is set for all 211 grade-11 jobs **including the 36 trashed ones**.
It records *"we wrote this once"*, NOT *"it is there now"*. That is a real limitation — but
it is not the wholesale unreliability originally claimed here. For grades 5, 6, 8 and 10 it
agreed with Notion exactly. Known false-negatives exist (UZ geometry/algebra grade 7 carry
no stamps yet are 100% delivered; two grade-9 kimyo jobs likewise).

### Other findings worth acting on

- **Duplicate generation is expensive.** G8: 524 jobs for 385 lessons (139 redundant).
  Kimyo grade 8 ran **112 jobs for 44 lessons (2.5x)**; one lesson has 7 done jobs. Notion
  keeps one page per lesson, so re-runs overwrote each other — pure wasted spend.
- **The archiver handles duplicate titles inconsistently**: G9 geometry delivered all 8
  same-titled pages correctly; G9 algebra collapsed 5 into 1.
- **Cross-language misrouting**: a `ru` biology-11 job was archived into the **UZ**
  Biologiya container despite `ru:biology|11` existing and pointing elsewhere.
- **Generation ran on an unaccepted TOC**: `6_sinf_matematika_darslik_2024_UZ.pdf` is
  `status='toc_review'` yet 22 jobs ran against it.
- **`notion_homework_page_id` points at the nested `Homework` sub-page**, not the lesson
  page. Joining it against `get_child_pages("Generated Homeworks")` yields 0 matches and
  looks catastrophic. It is not.

---

# ORIGINAL (UNVERIFIED) — RU figures below were produced by the buggy crawl

## Inventory (DB view: a `done` job exists, no archive stamp)

`page?` = whether `toc_entries.notion_homework_page_id` is already set.

| lang | subject | grade | n | page? | generated |
|---|---|---|---|---|---|
| ru | biology | 6 | 60 | NO | 2026-07-29…30 |
| ru | matematika | 5 | 31 | NO | 2026-08-05 |
| ru | matematika | 6 | 24 | NO | 2026-08-05 |
| ru | geometriya-g7-11 | 8 | 13 | yes | 2026-06-30…07-03 |
| ru | math-algebra | 7 | 11 | yes | 2026-06-30 |
| ru | history | 9 | 11 | NO | 2026-07-27 |
| ru | math-algebra | 8 | 10 | yes | 2026-07-01…03 |
| ru | history | 8 | 6 | NO | 2026-07-27 |
| ru | biology | 9 | 2 | NO | 2026-07-29…30 |
| ru | math-algebra | 10 | 1 | NO | 2026-07-24 |
| ru | biology | 11 | 1 | NO | 2026-07-29 |
| uz | geometriya-g7-11 | 8 | 79 | yes | 2026-06-26…07-06 |
| uz | math-algebra | 7 | 51 | yes | 2026-06-06…07-07 |
| uz | geometriya-g7-11 | 9 | 40 | yes | 2026-07-07 |
| uz | geometriya-g7-11 | 7 | 27 | yes | 2026-06-27…07-07 |
| uz | english | 8 | 22 | yes | 2026-06-24 |
| uz | math-algebra | 9 | 9 | yes | 2026-06-18…07-07 |
| uz | geometriya-g7-11 | 8 | 5 | NO | 2026-06-27 |
| uz | matematika | 5 | 5 | NO | 2026-07-21 |
| uz | math-algebra | 9 | 4 | NO | 2026-07-07 |
| uz | matematika | 6 | 2 | NO | 2026-07-08 |
| uz | kimyo-g7-11 | 9 | 2+2 | NO/yes | 2026-06-05…07-20 |
| uz | geometriya-g7-11 | 10 | 2 | yes | 2026-07-03…08 |
| uz | adabiyot | 9 | 1+1 | yes/NO | 2026-06-18 |

**Geometry is the dominant failure** — 166 of 422 across both languages.

## What each group needs

**A. Blocked on a missing `NOTION_SUBJECT_PAGES` key (115 ru lessons).**
Page IDs harvested from Notion 2026-08-11 — these pages exist, they were simply never
mapped:

```json
"ru:geografiya|5": "38c99838-1c76-80f7-beec-c67dcb072da1",
"ru:matematika|5": "2c999838-1c76-806f-9850-c07bb4dd7276",
"ru:matematika|6": "2c999838-1c76-80f2-933f-c88a8c2bd69f",
"ru:biology|6":    "39c99838-1c76-80b0-9e4e-f2a9881b2f19"
```

As of 2026-08-11 the canonical fleet map (273 keys, all 14 workers) contains
`ru:geografiya|5`, `ru:matematika|5`, `ru:matematika|6` but **NOT `ru:biology|6`** —
so biology 6's 60 lessons stay blocked until that key is appended. `ru:geografiya|6` is
also absent, but no Russian grade-6 geography textbook exists, so nothing is blocked by it.

A missing key is **not** an error: `notion_archive._resolve_subject_page_id` returns
`None`, the caller records `notion_skip_reason` and returns. Silent by design — this is
how biology 6 lost 60 lessons unnoticed.

**B. Page exists, content skipped (collision damage).**
Needs the two-gesture repair in `scripts/repair_notion_collisions.py`:
`--apply --expect-plan-hash=… --manifest-out=…` then `--refresh-notion --plan-file=…`.
The rewrite is PROHIBITED before the clear and REQUIRED after it. **All archivers must be
drained for both gestures** — the script's own banner warns a concurrent push trips the
state-drift abort.

**C. Never pushed, mapping present.** Plain archive retry; likely the old
ConnectError/RemoteProtocolError failures.

## Known non-recoverable

- **english 8 (uz, 22)** — R27: the source PDF is truncated (104 pages, TOC to 157).
  These packets were built from pages that do not exist. Re-ingest before delivering.
- **geometriya 8** has two byte-different PDFs with **100% identical TOC titles**
  (61 shared, 0 unique either side). One is a duplicate upload. Recovering both would
  double-file the same lessons.

## Method and limits

- Source: `edu_copy` (production), plus a live Notion crawl of `Lessons → N Grade →
  N - класс / N - sinf → subject → Generated Homeworks`.
- Full row-level list (lang, subject, grade, `toc_entry_id`, `job_id`, page-state) was
  generated alongside this report; regenerate with the query in worklog history rather
  than trusting a stale copy.
- Counts here include every `done` job without a stamp, **not** only LESSON-classified
  TOC rows — earlier figures in conversation (~230) used the narrower LESSON filter.
- Not established: how many *falsely stamped* lessons exist. Requires per-lesson Notion
  verification.
