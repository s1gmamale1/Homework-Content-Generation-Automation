# History Variant Label (R17) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the two history textbooks — Jahon tarixi (World) and O'zbekiston tarixi (Uzbekistan) — visually distinguishable in the Fleet/Library UI, which today shows both identically as "History · grade N".

**Architecture:** Backend detects the variant from the book filename; the frontend presents it. No DB migration, no generation impact (display-only).

**Tech Stack:** FastAPI + Pydantic v2 (`computed_field`), SQLAlchemy async, React + TypeScript.

---

## Approach & key decisions

- **Chosen:** a backend-derived, **non-persisted** `subject_variant` field (`"jahon" | "ozbekiston" | null`) computed from `original_filename`. One detection helper, unit-tested in pytest; the FE owns the display string. Caption reads `History · Jahon · grade 8`.
- **Rejected:** (a) FE-derives from filename — duplicates keyword/fold logic in TS and the batch card has no filename anyway; (b) persist the raw Notion title / split the `history` code — a DB migration + bigger blast radius, and it contradicts the deliberate "coarse single `history` subject" decision. (Persisting would also fix R16, but R16 is explicitly out of scope here.)
- **Load-bearing facts (verified against worktree @ origin/Nggaev-v2 14fd30e):**
  - `BookOut` (`app/schemas/book.py:10`) already carries `subject` + `original_filename`; a Pydantic `computed_field` can add `subject_variant` with **zero endpoint changes** (covers GET /books and /books/{id} → launcher cards).
  - `subjects.py` imports only stdlib → safe to import from `app/schemas/book.py` (no cycle).
  - The variant signal is in the filename (`jahon` / `ozbekiston`) — the **same folded-keyword basis** the archive split (`notion_archive._resolve_subject_page_id`) already uses, so UI label and Notion routing never disagree. Verified live: G7 national page is titled `Tarix Uzb` but its PDF is `7-sinf_Ozbekiston_tarixi_2022…pdf` → folds to `ozbekiston` → resolves correctly.
  - The batch payload (`app/api/v1/batch.py:_rollup_payload`) is built from the `batches` row, which has **no** filename → must plumb `original_filename` via `batches_repo.list_with_rollups` (join `books`) + per-endpoint book lookup.
  - FE render sites: `batch-funnel.tsx:20`, `launcher.tsx:466/487/679` (Preparing/Failed/Ready), `library.tsx` subtag. `BatchSummary` type at `types.ts:336`, `Book` at `types.ts:86`. No FE test runner → FE proof = `tsc --noEmit` + `npm run build`.
- **Out of scope (user's call):** R16 backend keyword robustness, the missing `history|5` config entry, and single-book detail/section/preview headers (those pages already show the filename).

---

### Task 1: Backend detection helper `history_variant`

**Files:**
- Modify: `app/services/subjects.py` (append at end of file, after `notion_keyword_pairs`)
- Test: `tests/services/test_history_variant.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_history_variant.py`:

```python
import pytest

from app.services.subjects import history_variant


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("8-sinf Jahon tarixi 2024 (@elekton_darslikbot).pdf", "jahon"),
        ("10 - sinf Jahon Tarixi.pdf", "jahon"),
        ("11-sinf-Jahon-tarixi.pdf", "jahon"),
        ("7-sinf_Ozbekiston_tarixi_2022_(elekton_darslikbot).pdf", "ozbekiston"),
        ("9-sinf O'zbekiston tarixi.pdf", "ozbekiston"),       # U+2019 right quote
        ("8-sinf O‘zbekiston tarixi 2023.pdf", "ozbekiston"),  # U+2018 left quote
        ("10 - sinf Oʻzbekiston tarixi.pdf", "ozbekiston"),    # U+02BB turned comma
        ("5-sinf Tarix (Qadimgi dunyo).pdf", None),            # combined, no split
        ("6-sinf Tarix Qadimgi Dunyo Tarixi.pdf", None),
    ],
)
def test_history_variant_history_subject(filename, expected):
    assert history_variant("history", filename) == expected


def test_history_variant_non_history_subject():
    assert history_variant("math-algebra", "8-sinf Algebra.pdf") is None
    # a non-history subject never splits, even if the filename has a keyword
    assert history_variant("biology", "jahon nimadir.pdf") is None


def test_history_variant_missing_filename():
    assert history_variant("history", None) is None
    assert history_variant("history", "") is None


def test_fold_agrees_with_archive_fold():
    """Drift guard: history_variant must fold apostrophe glyphs the SAME way the
    Notion archive split (notion_archive._fold) does, or the UI label and the
    archive routing would silently disagree. Fails loudly if either fold drops a
    glyph the other keeps."""
    from app.services.notion_archive import _fold as archive_fold

    for glyph in "'‘’ʻ`":
        name = f"7-sinf O{glyph}zbekiston tarixi.pdf"
        assert "ozbekiston" in archive_fold(name)        # archive would route it
        assert history_variant("history", name) == "ozbekiston"  # label agrees
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_history_variant.py -q`
Expected: FAIL — `ImportError: cannot import name 'history_variant'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/services/subjects.py`:

```python


# History is one app-subject ("history") but splits into two Notion pages /
# textbooks: Jahon tarixi (World) and O'zbekiston tarixi (national). The variant
# is recoverable from the book filename via the SAME folded-keyword basis the
# Notion archive split uses (notion_archive._resolve_subject_page_id), so the UI
# label and the archive routing never disagree. Display-only — not persisted.
_VARIANT_APOSTROPHES = "'‘’ʻ`"
# (folded-keyword, variant-key). A filename carries at most one in practice;
# order only decides a pathological both-match. jahon-first mirrors the majority
# of the per-grade NOTION_SUBJECT_PAGES dicts (archive routing).
_HISTORY_VARIANT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("jahon", "jahon"),
    ("ozbekiston", "ozbekiston"),
)


def history_variant(subject: str, filename: str | None) -> str | None:
    """For a history book, the variant key ("jahon"|"ozbekiston") derived from the
    filename, else None. None for non-history subjects, a combined Ancient-World
    book (Tarix qadimgi dunyo), or a missing/ambiguous filename."""
    if subject != "history" or not filename:
        return None
    folded = filename.lower().translate({ord(c): None for c in _VARIANT_APOSTROPHES})
    for keyword, variant in _HISTORY_VARIANT_KEYWORDS:
        if keyword in folded:
            return variant
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_history_variant.py -q`
Expected: PASS (12 cases).

- [ ] **Step 5: Commit**

```bash
git add app/services/subjects.py tests/services/test_history_variant.py
git commit -m "feat(subjects): history_variant() — derive Jahon/O'zbekiston from filename"
```

---

### Task 2: `subject_variant` computed field on `BookOut`

**Files:**
- Modify: `app/schemas/book.py`
- Test: `tests/schemas/test_book_out_variant.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/schemas/test_book_out_variant.py`:

```python
from uuid import uuid4

from app.schemas.book import BookOut


def test_bookout_subject_variant_ozbekiston():
    b = BookOut(
        id=uuid4(), subject="history", grade="8",
        original_filename="8-sinf O'zbekiston tarixi.pdf", status="toc_ready",
    )
    assert b.subject_variant == "ozbekiston"
    assert b.model_dump()["subject_variant"] == "ozbekiston"


def test_bookout_subject_variant_jahon():
    b = BookOut(
        id=uuid4(), subject="history", grade="8",
        original_filename="8-sinf Jahon tarixi.pdf", status="toc_ready",
    )
    assert b.subject_variant == "jahon"


def test_bookout_subject_variant_none_for_non_history():
    b = BookOut(
        id=uuid4(), subject="math-algebra", grade="8",
        original_filename="8-sinf Algebra.pdf", status="toc_ready",
    )
    assert b.subject_variant is None
    assert "subject_variant" in b.model_dump()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/schemas/test_book_out_variant.py -q`
Expected: FAIL — `AttributeError: 'BookOut' object has no attribute 'subject_variant'`.

- [ ] **Step 3: Write minimal implementation**

Edit `app/schemas/book.py`. Change the import line:

```python
from pydantic import BaseModel, ConfigDict, computed_field
```

Add the subjects import below the existing `from app.schemas.toc import TOCEntryOut`:

```python
from app.services import subjects
```

Add the computed field as the last member of `BookOut` (after `toc: ...`):

```python
    @computed_field  # type: ignore[prop-decorator]
    @property
    def subject_variant(self) -> Optional[str]:
        """"jahon"|"ozbekiston" for a history book (derived from the filename),
        else None — lets the FE distinguish the two history textbooks without a
        coarser subject code."""
        return subjects.history_variant(self.subject, self.original_filename)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/schemas/test_book_out_variant.py -q`
Expected: PASS (3 cases).

- [ ] **Step 5: Commit**

```bash
git add app/schemas/book.py tests/schemas/test_book_out_variant.py
git commit -m "feat(schemas): expose computed subject_variant on BookOut"
```

---

### Task 3: `subject_variant` in the batch payload

**Files:**
- Modify: `app/repositories/batches.py` (`list_with_rollups` — join the book filename)
- Modify: `app/api/v1/batch.py` (`_rollup_payload` + its three callers)
- Test: `tests/api/test_batch_payload_variant.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_batch_payload_variant.py`:

```python
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.batch import _rollup_payload


def _fake_batch(subject):
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), subject=subject, grade="8",
        provider="claude", model=None, transport="cli",
        extract_transport="inherit", judge_transport="inherit",
        extract_provider=None, extract_model=None,
        judge_provider=None, judge_model=None,
        created_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
    )


def test_rollup_payload_history_variant_jahon():
    p = _rollup_payload(_fake_batch("history"), {"done": 3}, "8-sinf Jahon tarixi.pdf")
    assert p["subject_variant"] == "jahon"


def test_rollup_payload_variant_none_without_filename():
    p = _rollup_payload(_fake_batch("history"), {"done": 1})
    assert p["subject_variant"] is None


def test_rollup_payload_non_history_variant_none():
    p = _rollup_payload(_fake_batch("math-algebra"), {"done": 1}, "8-sinf Algebra.pdf")
    assert p["subject_variant"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/api/test_batch_payload_variant.py -q`
Expected: FAIL — `KeyError: 'subject_variant'` (or `TypeError` on the 3rd positional arg).

- [ ] **Step 3: Write minimal implementation**

In `app/api/v1/batch.py`, add the import (with the other `app.services` imports):

```python
from app.services import subjects
```

Change `_rollup_payload`'s signature and insert the field next to `subject`:

```python
def _rollup_payload(batch, tally: dict[str, int], original_filename: str | None = None) -> dict:
    return {
        "batch_id": str(batch.id),
        "book_id": str(batch.book_id),
        "subject": batch.subject,
        "subject_variant": subjects.history_variant(batch.subject, original_filename),
        "grade": batch.grade,
        "provider": batch.provider,
        "model": batch.model,
        "transport": batch.transport,
        "extract_transport": batch.extract_transport,
        "judge_transport": batch.judge_transport,
        "extract_provider": batch.extract_provider,
        "extract_model": batch.extract_model,
        "judge_provider": batch.judge_provider,
        "judge_model": batch.judge_model,
        "rollup": tally,
        "lessons_covered": sum(tally.values()),
        "complete": (tally.get("pending", 0) + tally.get("running", 0)
                     + tally.get("cancelling", 0)) == 0 and sum(tally.values()) > 0,
        "created_at": batch.created_at.isoformat(),
    }
```

Update the three callers:

- `launch_batch` (the `book` is already in scope) — change the payload line to:
  ```python
      payload = _rollup_payload(batch, tally, book.original_filename)
  ```
- `list_batches` — change the comprehension to:
  ```python
      return {"batches": [_rollup_payload(r["batch"], r["rollup"], r.get("original_filename"))
                          for r in rows]}
  ```
- `get_batch` — fetch the book before building the payload:
  ```python
      tally = await batches_repo.rollup_for_batch(session, batch_id)
      book = await books_repo.get(session, batch.book_id)
      return _rollup_payload(batch, tally, book.original_filename if book else None)
  ```

In `app/repositories/batches.py`, add the Book import (after the existing model imports):

```python
from app.models.book import Book
```

Replace `list_with_rollups` body to join the filename:

```python
async def list_with_rollups(session: AsyncSession) -> list[dict]:
    """Every batch (newest first) + its computed rollup + the book filename
    (for subject-variant labeling)."""
    rows = (
        await session.execute(
            select(Batch, Book.original_filename)
            .join(Book, Book.id == Batch.book_id)
            .order_by(Batch.created_at.desc())
        )
    ).all()
    out = []
    for b, original_filename in rows:
        tally = await rollup_for_batch(session, b.id)
        out.append({"batch": b, "rollup": tally, "original_filename": original_filename})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/api/test_batch_payload_variant.py -q`
Expected: PASS (3 cases).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/batch.py app/repositories/batches.py tests/api/test_batch_payload_variant.py
git commit -m "feat(batch): include subject_variant in batch rollup payload"
```

---

### Task 4: Frontend types + label helper

**Files:**
- Modify: `web/src/lib/types.ts` (`Book` + `BatchSummary` interfaces)
- Modify: `web/src/lib/subjects.ts` (variant labels + composer)

- [ ] **Step 1: Extend the types**

In `web/src/lib/types.ts`, add to the `Book` interface (after `original_filename`):

```typescript
  subject_variant?: string | null;
```

And to the `BatchSummary` interface (after `subject`):

```typescript
  subject_variant?: string | null;
```

- [ ] **Step 2: Add the label helper**

In `web/src/lib/subjects.ts`, append after `subjectLabel`:

```typescript
export const VARIANT_LABELS: Record<string, string> = {
  jahon: "Jahon",
  ozbekiston: "O'zbekiston",
};

/** "History · O'zbekiston" when a history variant is present, else "History". */
export function subjectLabelWithVariant(
  subject: string,
  variant?: string | null,
): string {
  const base = subjectLabel(subject);
  const v = variant ? VARIANT_LABELS[variant] ?? variant : null;
  return v ? `${base} · ${v}` : base;
}
```

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: PASS (no errors). The new exports are unused until Task 5 — that is fine for `tsc`.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/subjects.ts
git commit -m "feat(web): subject_variant types + subjectLabelWithVariant helper"
```

---

### Task 5: Apply the variant label in the card views

**Files:**
- Modify: `web/src/components/fleet/batch-funnel.tsx`
- Modify: `web/src/components/fleet/launcher.tsx`
- Modify: `web/src/routes/library.tsx`

- [ ] **Step 1: Batch funnel card**

In `web/src/components/fleet/batch-funnel.tsx`, change the import on line 4:

```typescript
import { subjectLabelWithVariant } from "@/lib/subjects";
```

Replace line 20 (`{subjectLabel(batch.subject)}`) with:

```typescript
            {subjectLabelWithVariant(batch.subject, batch.subject_variant)}
```

- [ ] **Step 2: Launcher cards (all three)**

In `web/src/components/fleet/launcher.tsx`, replace each of the three occurrences of `{subjectLabel(book.subject)}` (PreparingCard ~466, FailedCard ~487, ReadyCard ~679) with:

```typescript
          {subjectLabelWithVariant(book.subject, book.subject_variant)}
```

(Leave the adjacent `<GradeChip grade={book.grade} />` untouched.) Then fix the import from `@/lib/subjects`: all three `subjectLabel` call sites are now gone, so **swap** `subjectLabel` → `subjectLabelWithVariant` in that import (keep any other named imports like `accentOf`). Leaving an unused `subjectLabel` import would fail `tsc` under `noUnusedLocals`.

- [ ] **Step 3: Library card subtag**

In `web/src/routes/library.tsx`, ensure `subjectLabelWithVariant` is imported from `@/lib/subjects`, then replace the subtag render `{subjectLabel(book.subject)}` with:

```typescript
                {subjectLabelWithVariant(book.subject, book.subject_variant)}
```

(If `subjectLabel` is still used elsewhere in the file, keep both imports; otherwise replace the import.)

- [ ] **Step 4: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: PASS — `tsc` clean, Vite build writes `web/dist/`.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/fleet/batch-funnel.tsx web/src/components/fleet/launcher.tsx web/src/routes/library.tsx
git commit -m "feat(web): show Jahon/O'zbekiston variant in fleet + library cards"
```

---

## Acceptance

Display-only change — **no generation impact**, so the CLI-smoke acceptance gate does **not** apply. Proof is:

- [ ] Full backend suite green: `uv run python -m pytest tests/ -q`
- [ ] FE typecheck + build clean: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
- [ ] Manual sanity (optional): a history book with `Jahon` in its filename renders `History · Jahon`; an `O'zbekiston` one renders `History · O'zbekiston`; a non-history book is unchanged.

## Finish (do not defer)

- [ ] `finishing-a-development-branch` — push `feat/history-variant-label`, open PR to `Nggaev-v2` (user decides merge).
- [ ] Worklog **0073** in `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md` (verify 0073 is still the next free number at finish time — other sessions may have advanced).
- [ ] Close **R17** in `docs/memory/ROADMAP.md` (note R16 — backend keyword robustness — remains open, as does the deferred `history|5` config entry).
- [ ] `git mv docs/superpowers/plans/2026-06-17-history-variant-label.md docs/superpowers/plans/shipped/`.
