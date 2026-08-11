# Undelivered homework — recovery inventory (2026-08-11)

**422 completed homework packets exist in `phase_outputs` but were never archived to
Notion.** All content is already generated and paid for; recovery is a push, not a
regeneration. RU 170 · UZ 252.

## ⚠️ The database's delivery record is not trustworthy

`toc_entries.notion_archived_job_id` is set by the archiver *before* the collision-era
`_write_leaf` could silently skip the actual write. A Notion crawl on 2026-08-11 proved
the divergence:

| | DB claims delivered | actually in Notion |
|---|---|---|
| Геометрия 8 (ru) | 57 | **1** |
| Биологиya 7 (uz) | 36 | **1** |
| Algebra 11 (uz) | 36 | **0** |

**So the 422 below is a FLOOR, not a total.** It counts only rows the DB itself admits
are unarchived. Lessons that are falsely stamped are additional and can only be found by
crawling Notion lesson-by-lesson, which has not been done.

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
