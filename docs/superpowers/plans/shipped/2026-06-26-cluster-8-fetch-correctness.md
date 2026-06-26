# Plan — Cluster 8 (slice): Notion-fetch correctness — `fetch-2` + `r13-fetch-1`

**Scope (user-locked 2026-06-26):** ship ONLY the two clean fetch-lane items of Cluster 8.
The two expensive items — `fetch-1` (>50 MB giants) and `extract-1`/R10 (glyph-loss TOC) —
are **deferred to the operator escape hatch** (manual shrink / re-source a cleaner-font PDF)
until the definitive campaign subject list proves a *required* textbook actually hits one of
them. Both are flagged in the backlog as not-clean drop-ins (a brainstorm + a maybe-ineffective
fix), so they are out of this plan by decision, not omission.

**Worklog ID:** **0085** (verify at finish: 0084 is the last block, 0082 is reserved for C6).
**Branch:** `cluster-8-book-ingestion` (worktree `../HCGA-c8-book-ingestion`, cut off `origin/Nggaev-v2` @ `95a95d5`).
**Commit prefix:** `c8:`.

## Approach & key decisions

- **Two independent, low-risk fixes, each its own task + commit, TDD-first.** Both live entirely
  in the fetch lane (`app/services/notion_fetch.py`, `app/services/book_fetch.py`) — **disjoint
  from every other cluster's files and from the in-flight `launcher-capability-gate` worktree.**
  **No migration, no schema change, no API change, no generation/LLM path** → unit tests are the
  proof; **no CLI smoke required** (nothing reaches a provider).
- **`fetch-2` (textbook-over-workbook):** today `_first_pdf_block` (`notion_fetch.py:41`) returns
  the **first PDF in page order**, so a subject page that lists `ish daftari` (workbook) before
  `darslik` (textbook) makes the *workbook* the book for the whole batch — a silent, whole-subject
  content-quality failure under mass-gen. Fix = rank candidate PDF blocks by filename and pick the
  best: **textbook (rank 0) ▸ neutral (rank 1) ▸ workbook (rank 2)**, ties broken by page order.
  **Preference, not exclusion** — a page with *only* a workbook still returns it (better than
  refusing to ingest). Markers matched on the existing `_fold`-normalized, lower-cased filename.
  The function's other caller (`list_subjects` → `has_textbook = ... is not None`,
  `notion_fetch.py:89`) only tests presence, so its behavior is unchanged.
- **`r13-fetch-1` (per-book download dedup):** `ensure_book_pdf_sync` (`book_fetch.py:44`) is a
  **sync** function the async pipeline calls via `asyncio.to_thread`. When N lessons of one book
  run concurrently (N threads), each sees the PDF missing and **each downloads the full file**.
  Fix = a per-`book_id` **`threading.Lock`** (a module dict guarded by a master lock) so the first
  thread fetches and the rest wait, then take the cached fast path. **Thread-lock, not asyncio-lock**,
  because the function runs in a worker thread, not the event loop. **Lock-free fast path preserved**
  for the overwhelmingly common already-cached case (no contention cost on the hot path). Per-process
  scope is correct and sufficient — the WISHLIST item is explicitly "in one worker"; cross-PC dedup
  is a non-goal. All existing behavior (the head-is-canonical rule, the `r13-integrity-1` wrong-size
  re-fetch, raise-if-missing-with-no-head) is preserved exactly.
- **Verified load-bearing facts (read against tip `95a95d5`):**
  - `_first_pdf_block` returns first-in-order; the existing test `test_first_pdf_block_picks_first_pdf_in_order`
    (`tests/services/test_notion_fetch.py:31`) **asserts the workbook is picked** — it encodes the
    old wrong behavior and **must be updated** to assert the textbook (this is the fix's demonstration).
  - `_fold` (`notion_fetch.py:21`) lower-cases + strips apostrophes; reuse it for marker matching.
  - `download_textbook` (`notion_fetch.py:105`) reads the chosen block's filename via
    `payload.get("name")` where `payload = block[block["type"]]` — same shape `_pdf_name` will read.
  - `ensure_book_pdf_sync` fast-path / corrupt-cache / fetch structure at `book_fetch.py:57-96`;
    `_fetch_to_temp` (`:28`) writes `tmp` then the caller `os.replace(tmp, path)` (`:95`) atomically.
  - No migration in this slice → `alembic heads` stays single-head (unchanged).

---

## Task 1 — `fetch-2`: prefer textbook (darslik) over workbook (ish daftari)

**RED** — edit `tests/services/test_notion_fetch.py`:
- **Update** `test_first_pdf_block_picks_first_pdf_in_order` → rename to
  `test_first_pdf_block_prefers_textbook_over_workbook` and flip the assertion: with the same
  `[paragraph, file:ish_daftari.pdf, pdf(unnamed)]` blocks, assert it now returns `blocks[2]`
  (the neutral PDF beats the workbook), **not** `blocks[1]`.
- Add `test_first_pdf_block_prefers_darslik_by_name`: blocks =
  `[pdf darslik first?]` — put a `file` named `"ish_daftari.pdf"` (url) FIRST and a `file` named
  `"8-sinf_algebra_darslik.pdf"` (url) SECOND; assert the darslik (second) is returned despite
  page order.
- Add `test_first_pdf_block_falls_back_to_workbook_when_only_one`: a single
  `file` named `"ish_daftari.pdf"` → still returned (preference, not exclusion).
- Add `test_first_pdf_block_textbook_beats_workbook_regardless_of_order`: darslik FIRST, workbook
  SECOND → darslik returned (proves ranking, not just order-flip).
- Add `test_first_pdf_block_ties_break_by_page_order`: two neutral PDFs → first one returned.
- Keep `test_first_pdf_block_none_when_absent` and `test_first_pdf_block_skips_non_pdf_file`
  passing (cover.png skipped; the pdf block is neutral rank → returned).

Run → the updated/added tests FAIL (current code returns first-in-order).

**GREEN** — in `app/services/notion_fetch.py`, replace `_first_pdf_block` and add two pure helpers
above it:
```python
_TEXTBOOK_MARKERS = ("darslik", "textbook")
_WORKBOOK_MARKERS = ("ish daftari", "ishchi daftar", "workbook", "daftar")


def _pdf_name(block: dict) -> str:
    """Folded, lower-cased filename of a pdf/file block (``""`` when unnamed)."""
    payload = block.get(block.get("type"), {})
    return _fold(payload.get("name") or "")


def _pdf_rank(name: str) -> int:
    """Selection rank for a PDF filename — LOWER is preferred. A `darslik`
    (textbook) beats a neutral PDF beats an `ish daftari` (workbook), so a
    workbook listed first no longer becomes the batch's 'textbook' (fetch-2)."""
    if any(m in name for m in _TEXTBOOK_MARKERS):
        return 0
    if any(m in name for m in _WORKBOOK_MARKERS):
        return 2
    return 1


def _first_pdf_block(blocks: list[dict]) -> dict | None:
    """Best textbook PDF block, else None. Prefers a `darslik` over an `ish
    daftari` when a subject page attaches both; ties broken by page order. A
    `pdf` block is inherently a PDF; a `file` block needs a `.pdf` filename (a
    page may also attach a cover image / .docx, which must NOT be the textbook)."""
    candidates: list[tuple[int, int, dict]] = []
    for i, b in enumerate(blocks):
        t = b.get("type")
        if not _url_from_block(b):
            continue
        if t == "pdf":
            candidates.append((_pdf_rank(_pdf_name(b)), i, b))
        elif t == "file":
            name = (b.get("file", {}).get("name") or "").lower()
            if name.endswith(".pdf"):
                candidates.append((_pdf_rank(_pdf_name(b)), i, b))
    if not candidates:
        return None
    return min(candidates, key=lambda c: (c[0], c[1]))[2]
```
(`_pdf_rank`/`_pdf_name` are defined ABOVE `_first_pdf_block`; `_url_from_block` and `_fold`
already exist earlier in the module — no reordering of those needed.)

**Commands:** `uv run python -m pytest tests/services/test_notion_fetch.py -q`
**Commit:** `c8: prefer textbook (darslik) over workbook in Notion fetch (fetch-2)`

---

## Task 2 — `r13-fetch-1`: per-book_id lock so concurrent fetches download once

**RED** — add `tests/services/test_book_fetch_dedup.py`:
- `test_concurrent_same_book_fetches_download_once`:
  - Point storage at a tmp dir: `monkeypatch.setattr(settings, "var_dir", str(tmp_path))` (so
    `storage.book_pdf_path(book_id)` resolves under `tmp_path`) and
    `monkeypatch.setattr(settings, "fleet_head_url", "http://head.local")` (so the fetch path is taken).
  - Replace the network with a counting stub that ACTUALLY writes the temp file (so the second
    waiter sees a cached file): `calls = []; def fake_fetch(url, headers, tmp): calls.append(1); time.sleep(0.05); Path(tmp).write_bytes(b"%PDF-1.4 x")` then
    `monkeypatch.setattr(book_fetch, "_fetch_to_temp", fake_fetch)`.
  - Launch e.g. 5 threads on a `threading.Barrier(5)` that all call
    `book_fetch.ensure_book_pdf_sync(book_id)` for the SAME `book_id`; join.
  - Assert `len(calls) == 1` (single download) AND every thread returned the same existing path
    with non-empty bytes.
- `test_distinct_books_each_fetch`: two different `book_id`s concurrently → `len(calls) == 2`
  (the lock is per-book, not global — it must not serialize unrelated books).

RED-prove: this test must FAIL on the current code (each thread downloads → `len(calls) == 5`),
confirming the test bites before the lock exists. (Run it against HEAD before writing GREEN.)

**GREEN** — in `app/services/book_fetch.py`:
- Add at module level (after imports):
  ```python
  import threading

  _book_locks: dict[str, threading.Lock] = {}
  _book_locks_guard = threading.Lock()


  def _lock_for(book_id: UUID | str) -> threading.Lock:
      """Per-book_id lock so concurrent same-book fetches in one worker download
      ONCE — the first thread fetches, the rest wait then hit the cached fast path
      (r13-fetch-1). Per-process by design; cross-PC dedup is a non-goal."""
      key = str(book_id)
      with _book_locks_guard:
          lk = _book_locks.get(key)
          if lk is None:
              lk = _book_locks[key] = threading.Lock()
          return lk


  def _cached_ok(path: Path, expected_size: int | None, head: str) -> bool:
      """True when the on-disk PDF can be returned as-is. A wrong-size cache is
      'not ok' ONLY when a head is configured to re-fetch from (on the head the
      file is canonical — there's nowhere to re-pull, so it stays ok)."""
      if not path.exists():
          return False
      if expected_size and head and path.stat().st_size != expected_size:
          return False
      return True
  ```
- Rewrite the body of `ensure_book_pdf_sync` to: lock-free fast path → per-book lock → re-check →
  (unlink wrong-size) → raise-if-no-head → fetch. The fetch/promote block (the existing
  `tmp = ...` through `os.replace(tmp, path); return path`, `book_fetch.py:71-96`) moves
  **verbatim** inside the `with _lock_for(book_id):` block after the re-check:
  ```python
  def ensure_book_pdf_sync(book_id: UUID | str, expected_size: int | None = None) -> Path:
      """<keep the existing docstring verbatim>"""
      path = storage.book_pdf_path(book_id)
      head = settings.fleet_head_url.strip()
      # Lock-free fast path: already cached & valid (the common case — no lock).
      if _cached_ok(path, expected_size, head):
          return path
      # Serialize concurrent fetches of the SAME book so N lessons don't each
      # download the full PDF — first fetches, the rest wait then hit cache.
      with _lock_for(book_id):
          if _cached_ok(path, expected_size, head):
              return path
          # wrong-size cache with a head to re-fetch from → drop it (r13-integrity-1)
          if path.exists() and expected_size and head and path.stat().st_size != expected_size:
              path.unlink(missing_ok=True)
          if not head:
              raise RuntimeError(f"Book PDF missing on disk: {path}")
          # <existing fetch+promote block, moved here verbatim>
          ...
          os.replace(tmp, path)
          return path
  ```
  The `if path.exists(): ... else: return path` head of the old body is replaced by the
  `_cached_ok` fast path + re-check; **no other line of the fetch/promote logic changes.**

**Commands:** `uv run python -m pytest tests/services/test_book_fetch_dedup.py -q`
then the full fetch lane: `uv run python -m pytest tests/services/test_notion_fetch.py tests/services/test_book_fetch_dedup.py -q`
**Commit:** `c8: per-book_id lock to de-dupe concurrent same-book fetches (r13-fetch-1)`

---

## Task 3 — full suite + acceptance

- **Full suite green:** `uv run python -m pytest tests/ -q`
  (the 5 notion-router 503s are pre-existing/env — confirm the count is unchanged, not a regression).
- **Acceptance (no CLI smoke — nothing reaches a provider):** the unit tests ARE the proof —
  `fetch-2` is pure selection, `r13-fetch-1` is download plumbing, neither touches generation.
  The dedup test's RED-prove (5 downloads → 1) is the load-bearing acceptance for r13.

---

## Finish (controller)

- Full suite green (Task 3).
- **Rebase-check before PR:** `git fetch origin` → `git log HEAD..origin/Nggaev-v2`; if the base
  moved, rebase onto `origin/Nggaev-v2`, re-run the suite. (Expect a trivial append conflict in
  `MASTER_MEMORY.md`/`INDEX.md`/`WISHLIST.md` if another cluster merged first — keep both blocks.)
- **Worklog** `## [0085]` block in `docs/memory/MASTER_MEMORY.md` + an `INDEX.md` row (verify 0085
  is still free against the live tip first; renumber if a parallel cluster took it).
- **WISHLIST closes:** mark `fetch-2` ✅ SHIPPED and `r13-fetch-1` ✅ SHIPPED (redundant-download
  half) with the commit refs; leave `fetch-1` and `extract-1`/R10 OPEN with a one-line note that
  they're deferred to the operator workaround pending the campaign subject list.
- **`git mv`** this plan into `docs/superpowers/plans/shipped/`.
- **Reference-doc de-stale:** check `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` for any line
  describing Notion PDF selection ("first PDF") and update to "prefers textbook over workbook";
  add the per-book fetch-lock note if the fetch path is documented. (No README/DEPLOY/DATABASE
  change — no env var, schema, or deploy change.)
- **Stage only this slice's files** (`notion_fetch.py`, `book_fetch.py`, the two test files, the
  plan, the memory/doc files) — never `git add -A`; the `launcher-capability-gate` worktree shares
  this branch's base.
- PR titled `[cluster-8] fetch correctness — fetch-2 + r13-fetch-1` to the gatekeeper. **No self-merge.**

## Out of scope (deferred by user decision, 2026-06-26)

`fetch-1` (>50 MB giants → auto-shrink/subset) and `extract-1`/R10 (glyph-loss `/Gxx` TOC) — both
have working operator escape hatches and are flagged not-clean in the backlog. Revisit only when
the definitive campaign subject list confirms a *required* textbook hits one of them.
