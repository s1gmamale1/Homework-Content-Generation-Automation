# Plan — Extract robustness: scanned PDFs (vision) + oversize text books (subset)

Branch `feat/extract-robustness` (off `origin/Nggaev-v2` @ `4d9ffc9`). One PR, two features
shipped **one at a time**: scanned-vision FIRST (Tasks 1–3), then oversize-subset (Tasks 4–5),
then finish (Task 6). Backend-only.

## Approach & key decisions

**Problem.** The extract branch (`pipeline.py:732–760`) reads the *whole* book's text and the
model finds the lesson **by title** (page numbers are only a hint — so the normal path is
offset-immune). Two book classes break it, both **common** in the campaign corpus:
- **Scanned/image-only** (e.g. algebra-g9 `a0173601`, ~49 chars/page): no text layer → Gate A
  (`validate_extract_text`) fails → today `raise` → hard fail.
- **Oversize text** (e.g. adabiyot `e585a5f3`, 665K > `extract_max_text_chars=600_000`):
  `extract_text_is_oversize` → today `raise` → hard fail.

**Chosen approach — a scoped fallback at the two existing raise-points.** The normal whole-text
path is **100% unchanged**; only books that hit a raise take a fallback:
- **Oversize → subset the lesson's pages as TEXT inline** (cheap; keeps `transport=api`). Reuses
  `summarize_lesson` unchanged, just with subset text instead of whole-book text.
- **Scanned → vision-attach a page-window PDF, forced `transport=cli`.** `api_transport.generate`
  raises `NotImplementedError("api transport is text-only")` on attachments (`api_transport.py:32`),
  so vision **must** be cli. A vision-capable extract provider (default gemini reads PDFs natively).

**Key decisions (load-bearing, verified against code):**
1. **Gate A is the scanned detector** — `validate_extract_text` already returns a reason for
   no-text-layer PDFs (`agent.py:928`). No new `too_sparse` heuristic needed. Dispatch =
   `validate_extract_text(book_text) is not None` → scanned branch.
2. **Page numbers can be off-by-N** (printed TOC vs physical order; front-matter offset).
   `page_start`/`page_end` are `nullable=True` (`toc_entry.py:24`). Scanned PDFs have **no text to
   validate the slice** → silent wrong-lesson risk. Mitigation: **generous window + title-find** —
   attach `[ps−W .. pe+W]` (W=`extract_window_pages`, default 5; capped at
   `extract_window_max_pages`, default 25) and instruct the model to locate the lesson **by title**
   within the attached pages (same title-first trick as the whole-text path). Rejected: tight ±1
   (silent miss on any real front-matter); offset-calibration (needs a text layer the scanned book
   lacks — can't serve its own case).
3. **Missing `page_start`/`page_end` → fail loud** (no window to scope) — no worse than today's
   hard fail.
4. **Reuse, don't rebuild.** `_subset_pdf` (`agent.py:1330`, page-range temp-PDF writer) and the
   vision-attach/​spawn/​usage/​cleanup mechanics of the **legacy** `extract_lesson_context`
   (`agent.py:1387`, dead since worklog 0035) are exactly the scanned machinery — extend/adapt
   rather than write fresh.
5. **No cross-provider failover on the scanned path** — vision needs a vision-capable provider;
   failing over to kimi/codex would just fail. Scanned = single call to `extract_provider`, Gate B
   check, raise on failure. Oversize keeps the existing `_run_with_failover` wrapper (text path).
6. **Ordering inside the branch:** oversize-check → Gate-A-scanned-check → normal. Oversize implies
   a large text layer, so the two fallbacks are mutually exclusive in practice.

**New config** (`app/config.py`, after `extract_max_text_chars`):
```python
extract_window_pages: int = 5        # ± margin around printed page range for scoped extract
extract_window_max_pages: int = 25   # hard cap on a scoped window (size/cost guard)
```

---

## Task 1 — windowed page-subset PDF (`_subset_pdf` margin/cap)  [scanned]

**File:** `app/services/agent.py` (extend `_subset_pdf`, ~1330). **Test:** `tests/services/test_extract_subset.py` (new).

Extend the signature, default-preserving legacy callers:
```python
def _subset_pdf(
    pdf_path: Path, page_start: Optional[int], page_end: Optional[int],
    *, margin: int = 0, max_pages: Optional[int] = None,
) -> Optional[Path]:
```
- Apply `margin`: `start = page_start - margin`, `end = page_end + margin` (then clamp to
  `[1..n]` as today via `start_idx`/`end_idx`).
- After clamping, if `max_pages` and `(end_idx - start_idx + 1) > max_pages`, **center** the cap
  on the original `[page_start..page_end]` and trim the window to `max_pages`.
- Unchanged guards: falsy/invalid pages → `None`; empty result → `None`; exception → `None`+warn.

**TDD (write first, must fail):**
1. `test_subset_window_adds_margin` — a 40-page PDF, `_subset_pdf(p, 20, 22, margin=5)` → temp PDF
   with **13** pages (15..27, 1-based inclusive). (Build a tiny in-memory PDF via `pypdf.PdfWriter`
   with blank pages in a fixture.)
2. `test_subset_window_clamps_to_bounds` — `_subset_pdf(p, 2, 3, margin=5)` on a 6-page PDF → pages
   1..6 (no negative/overflow).
3. `test_subset_window_respects_max_pages` — `_subset_pdf(p, 20, 22, margin=20, max_pages=11)` →
   exactly 11 pages, centered on 20..22.
4. `test_subset_legacy_callsite_unchanged` — `_subset_pdf(p, 10, 12)` (no kwargs) → 3 pages
   (10..12), proving default behavior is byte-for-byte the legacy path.

**Commands:** `uv run python -m pytest tests/services/test_extract_subset.py -q`
**Commit:** `feat(extract): windowed page-subset PDF (margin + max_pages cap)`

---

## Task 2 — forced-cli vision extract (`summarize_lesson_vision`)  [scanned]

**File:** `app/services/agent.py` (new fn near `summarize_lesson`, ~1627). **Test:** `tests/services/test_summarize_lesson_vision.py` (new).

New fn, **same return shape** as `summarize_lesson` `(text, prompt_tokens, output_tokens)`:
```python
async def summarize_lesson_vision(
    *, provider: str, model: Optional[str], pdf_path: Path,
    section_title: str, section_number: str, page_start: int, page_end: int,
    homework_job_id: UUID, phase_output_id: UUID,
) -> tuple[str, int, int]:
```
Behavior:
- Build window PDF: `_subset_pdf(pdf_path, page_start, page_end, margin=settings.extract_window_pages,
  max_pages=settings.extract_window_max_pages)`. If `None` → `raise RuntimeError("lesson.extract
  (vision): cannot scope page range …")` (fail loud per decision 3).
- Title-find prompt (new `_SUMMARIZE_VISION_PROMPT`): "*The attached PDF pages contain the lesson
  titled "{title}" (section {number}, printed around pages {ps}-{pe} — page numbers are a hint,
  find it by its TITLE). Summarize only that lesson … {rules}*".
- `prompt = _build_master_prompt(..., attachment_preamble=prov.format_attachments([window_pdf]))`.
- **`transport="cli"` hardcoded** at the `_spawn` call (never `api` — attachments). `auth_mode="cli"`
  in `_record_usage`.
- `finally:` unlink the temp window PDF (never `pdf_path`).
- Record usage (operation `"lesson.extract"`), raise on `rc != 0`, return `(text, pt, ot)`.
  Model itself stays `_resolve_model(provider, model)` (extract pin).

**TDD (mock `agent._spawn`):**
1. `test_vision_forces_cli_transport` — monkeypatch `_spawn` to capture kwargs; assert the call's
   `transport == "cli"` even though we pass nothing/​api-ish.
2. `test_vision_attaches_window_pdf` — assert `_spawn` got `attachments=[<a real temp .pdf>]` whose
   page count matches the windowed range; assert the temp file is **unlinked** after.
3. `test_vision_fails_loud_without_pages` — `page_start=None` → `_subset_pdf` returns None →
   `RuntimeError`, `_spawn` never called.
4. `test_vision_returns_token_shape` — `_spawn` returns `(0, "real summary text…", usage, "")` →
   fn returns `("real summary text…", pt, ot)`.

**Commands:** `uv run python -m pytest tests/services/test_summarize_lesson_vision.py -q`
**Commit:** `feat(extract): forced-cli vision extract for scanned PDFs`

---

## Task 3 — dispatch scanned books to the vision fallback  [scanned — feature complete]

**File:** `app/services/pipeline.py` (extract branch, 732–760). **Test:** `tests/services/test_pipeline_extract_dispatch.py` (new, async).

Replace the current Gate-A `raise` (lines 738–740) with a dispatch. Keep oversize raise as-is for
now (Task 5 replaces it). New shape:
```python
book_text = await asyncio.to_thread(agent.read_whole_book_text, pdf_path)
if agent.extract_text_is_oversize(book_text):
    raise RuntimeError("lesson.extract: book too large …")   # Task 5 replaces
elif agent.validate_extract_text(book_text) is not None:      # scanned / no text layer
    ps, pe = section["page_start"], section["page_end"]
    if not ps or not pe:
        raise RuntimeError(f"lesson.extract: {agent.validate_extract_text(book_text)} "
                           f"and no page range to scope a vision extract")
    out, tin, tout = await agent.summarize_lesson_vision(
        provider=extract_provider, model=extract_model, pdf_path=pdf_path,
        section_title=section["title"], section_number=section["number"],
        page_start=ps, page_end=pe, homework_job_id=job_id, phase_output_id=po_id,
    )
    reason = agent.validate_extract_summary(out)
    if reason is not None:
        raise failure_classifier.ExtractRefusal(f"lesson.extract Gate B (vision): {reason}")
    output_md, tin, tout, produced_by = out, tin, tout, extract_provider
    parsed_struct = None
else:
    # … existing whole-text path UNCHANGED (Gate A pass → _run_with_failover(summarize_lesson)) …
```
(Refactor minimally: lift the existing whole-text block into the `else`. Do **not** alter it.)

**TDD (async, monkeypatch `agent.read_whole_book_text`, `agent.summarize_lesson_vision`,
`agent.summarize_lesson`):**
1. `test_scanned_book_routes_to_vision` — `read_whole_book_text` → `"x"*40` (Gate A fails),
   section has pages → asserts `summarize_lesson_vision` called once, `summarize_lesson` **not**
   called.
2. `test_scanned_book_no_pages_fails_loud` — Gate A fails + `page_start=None` → `RuntimeError`,
   vision **not** called.
3. `test_normal_book_unchanged` — `read_whole_book_text` → long clean text (Gate A passes) →
   `summarize_lesson` path taken, `summarize_lesson_vision` **not** called.

**Commands:** `uv run python -m pytest tests/services/test_pipeline_extract_dispatch.py -q`
**Commit:** `feat(extract): route scanned/no-text-layer PDFs to vision extract`

> **Controller checkpoint after Task 3:** scanned feature is functionally complete. Read the full
> diff + re-run all three new test files before proceeding to oversize.

---

## Task 4 — `read_page_range_text` helper  [oversize]

**File:** `app/services/agent.py` (new fn near `read_whole_book_text`, ~1021). **Test:** add to `tests/services/test_extract_subset.py`.

```python
def read_page_range_text(pdf_path: Path, page_start: int, page_end: int, *, margin: int = 0) -> str:
    """Glyph-decoded text for printed pages [page_start-margin .. page_end+margin], clamped to the
    PDF, budgeted to extract_max_text_chars. '' if no text. Built on _read_pdf_pages."""
```
- Clamp `[ps-margin .. pe+margin]` to `[1..n]`; `_read_pdf_pages(reader, range(...),
  budget=settings.extract_max_text_chars, already=set(), pdf_name=…)`; `"".join(chunks).strip()`.

**TDD:** build a PDF whose pages carry distinct text (`f"PAGE {i} BODY"`):
1. `test_page_range_text_reads_window` — pages 10..12 margin 1 → text contains PAGE 9..13 markers,
   excludes PAGE 8 / PAGE 14.
2. `test_page_range_text_clamps` — pages 1..2 margin 5 → no error, starts at PAGE 1.
3. `test_page_range_text_empty_on_imageonly` — a blank/no-text PDF → `""`.

**Commands:** `uv run python -m pytest tests/services/test_extract_subset.py -q`
**Commit:** `feat(extract): read_page_range_text (windowed text slice)`

---

## Task 5 — dispatch oversize books to the subset-text fallback  [oversize — feature complete]

**File:** `app/services/pipeline.py` (the oversize branch from Task 3). **Test:** extend `tests/services/test_pipeline_extract_dispatch.py`.

Replace the oversize `raise` with: scope to the lesson's pages as text, then run the **existing**
`_run_with_failover(summarize_lesson)` path on the subset (not the whole book):
```python
if agent.extract_text_is_oversize(book_text):
    ps, pe = section["page_start"], section["page_end"]
    if not ps or not pe:
        raise RuntimeError("lesson.extract: book too large and no page range to scope")
    book_text = await asyncio.to_thread(
        agent.read_page_range_text, pdf_path, ps, pe, margin=settings.extract_window_pages)
    if agent.extract_text_is_oversize(book_text):
        raise RuntimeError("lesson.extract: lesson page-subset still too large")
    if agent.validate_extract_text(book_text) is not None:
        # subset has no text layer → treat as scanned
        … (fall through to vision branch) …
    # else: fall through to the normal whole-text path below, now with subset book_text
```
Cleanest structure: compute `book_text` (whole → subset if oversize) **first**, then run the
single Gate-A dispatch (scanned-vision vs normal) on the final `book_text`. Re-order the branch so
oversize only *rewrites* `book_text`, and the scanned-vs-normal decision happens once afterward.

**TDD (extend):**
1. `test_oversize_book_subsets_text` — `read_whole_book_text` → `"a"*700_000` (oversize),
   `read_page_range_text` → long clean text → `summarize_lesson` called with the **subset** text;
   no vision.
2. `test_oversize_no_pages_fails_loud` — oversize + `page_start=None` → `RuntimeError`.
3. `test_oversize_subset_still_oversize_fails` — `read_page_range_text` → `"a"*700_000` →
   `RuntimeError("…still too large")`.

**Commands:** `uv run python -m pytest tests/services/test_pipeline_extract_dispatch.py -q`
**Commit:** `feat(extract): scope oversize text books to the lesson page range`

---

## Task 6 — acceptance smoke + finish

**Acceptance gate (real CLI, in-process, no server):** a small script/async-repl that calls the
pipeline extract step against two **real** books in the live DB:
- scanned: algebra-g9 `a0173601` (verify the id exists at smoke time; else pick any scanned book).
- oversize: adabiyot `e585a5f3` (665K chars).
Prove: scanned → a real lesson summary via a **cli** `agent_usages` row (vision); oversize → a real
summary from a subset. Capture the summaries + token rows. **Fact over theory — actually run gemini.**

**Finish (same commit set, do not defer):**
- Full suite: `uv run python -m pytest tests/ -q` (green; note pre-existing RUN_DB_INTEGRATION skips).
- `docs/memory/MASTER_MEMORY.md` worklog **0069** + `docs/memory/INDEX.md` row.
- Close the two WISHLIST items (`fetch-1(b)` oversize-subset + the scanned/image-only item) — move to
  worklog; update `docs/memory/ROADMAP.md` if tracked there.
- `git mv docs/superpowers/plans/2026-06-17-extract-robustness-scanned-oversize.md
  docs/superpowers/plans/shipped/`.
- De-stale reference docs if the extract behavior is described in `docs/HOW_IT_WORKS.md` /
  `docs/CODE_MAP.md` (per the update-live-system-reference-docs rule).
- `finishing-a-development-branch` → open PR to `Nggaev-v2` (user decides merge).

**Commit:** `docs(memory): worklog 0069 — scanned-vision + oversize-subset extract; ship plan`

---

## Self-review

- **Coverage:** every new unit (`_subset_pdf` window, `summarize_lesson_vision`,
  `read_page_range_text`, both dispatch arms) has failing-first tests; acceptance proves real model
  behavior (Task 6).
- **Type consistency:** `summarize_lesson_vision` mirrors `summarize_lesson`'s `(str,int,int)`;
  `read_page_range_text` returns `str` like `read_whole_book_text`; new settings are `int`.
- **Scope discipline:** stage only the files each task lists; never `git add -A`. Normal extract
  path untouched (asserted by `test_normal_book_unchanged` / `test_subset_legacy_callsite_unchanged`).
- **No regressions to the api path:** scanned forces cli by construction; oversize-subset stays text
  → `transport=api` still works for oversize.
