# Post-TOC-extract vision validator (soft-gate)

## Approach & key decisions

After `extract_toc` produces entries, run **one Gemini-2.5-flash vision call** that
checks the extracted lesson list against the **printed contents page image** of the
book, before the book is allowed into generation. Verdict gates the book status:

- **Chosen: soft-gate + vision window.** On a `mismatch` verdict the book goes to a
  new **`toc_review`** status (held out of generation; operator clicks **Accept anyway**
  or **Retry**). On `verified` or `skipped` → `toc_ready` as today. Vision window =
  the **rendered** contents page, so the check is independent of the text-layer bugs
  that produce bad TOCs (broken-font `/Gxx` glyph-garbage, hallucinated sections) —
  the one failure class a text re-read cannot catch (WISHLIST `extract-1`).
- **Rejected — advisory badge:** doesn't stop a poisoned TOC reaching generation.
  **Rejected — hard-fail:** a flaky LLM false-positive would permanently block a good
  book with no recovery short of re-extract.
- **The gate is free:** generation already requires `book.status == "toc_ready"`
  (`batch.py:105`, `jobs.py:130`), so `toc_review` blocks generation with no new check.
- **Never blocks on the validator's own failure:** spawn/parse error or an unbuildable
  window → `skipped` → `toc_ready` (mirrors judge-unavailable). One cheap one-time call
  per upload; behind `settings.toc_validation_enabled` (default on).

**Load-bearing facts verified against code (2026-06-30):**
- `toc_extractor.run` (`toc_extractor.py:58`) has `extracted.entries` in memory before
  the `toc_ready` flip (`:93`) — the validator slots in there.
- `agent._toc_source_pdf(pdf, front, back)` (`agent.py:1563`) already builds the
  front+back page window; `api_transport._gemini` already PDF-vision-attaches via
  `types.Part.from_bytes` (`api_transport.py:104`). `gemini-2.5-flash` ∈ manifest.
- Status is free-form `String(32)` (`book.py:29`); adding `toc_review` needs no enum
  migration. `_book_out_with_toc` only attaches `toc` for `toc_ready` (`books.py:447`)
  — must extend to `toc_review` so the operator can review the entries.
- Migration head = `0040_books_source_language`; next = `0041`.
- `launch_defaults.toc_transport` (`launch_defaults.py:28`) governs TOC-call transport;
  the validator follows it. Validator provider/model pinned-but-configurable like
  extract (`settings.toc_validation_provider`/`_model`, default `gemini`/`gemini-2.5-flash`).

**Global constraints:**
- Validator must NEVER raise into `toc_extractor.run` — any failure → `skipped` result.
- Persist entries on `toc_review` too (operator reviews them); only the *status* differs.
- Stage only each task's listed files. Commit per task. TDD per task.
- DB-integration tests need `RUN_DB_INTEGRATION=1` + a scratch DB (`createdb -O edu …`).

---

## Task 1 — `books.toc_validation` + `toc_validation_detail` columns + migration 0041

- **Model** `app/models/book.py`: add
  `toc_validation: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)`
  and `toc_validation_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)`
  (Text already imported). Add a `CheckConstraint("toc_validation IS NULL OR toc_validation IN ('verified','mismatch','skipped')", name="ck_books_toc_validation")` to `__table_args__`.
- **Migration** `alembic/versions/0041_books_toc_validation.py` (down_revision `0040_books_source_language`):
  `add_column` both (nullable, no server_default — existing books stay NULL = "not validated") + `create_check_constraint`; downgrade drops constraint + both columns.
- **Test** `tests/repositories/test_books_toc_validation.py` (DB-integration): upgrade head →
  insert book with `toc_validation=None` ok; set `"verified"`/`"mismatch"`/`"skipped"` ok;
  set `"bogus"` → `IntegrityError` (CHECK bites). **Bite-proof:** drop the constraint locally → `"bogus"` stops raising.
- **Commands**: `createdb -U macmini5 edu_v1 && RUN_DB_INTEGRATION=1 DATABASE_URL=…edu_v1 uv run alembic upgrade head && RUN_DB_INTEGRATION=1 … uv run python -m pytest tests/repositories/test_books_toc_validation.py -q`
- **Commit**: `feat(books): add toc_validation columns + migration 0041`

## Task 2 — `agent.validate_toc` (Gemini-flash vision call)

- **Schema** `app/schemas/toc.py`: add
  ```python
  class TOCValidation(BaseModel):
      verdict: Literal["verified", "mismatch"]
      confidence: Literal["low", "medium", "high"]
      issues: list[str]  # concrete problems; empty when verified
  ```
- **Result dataclass** in `agent.py` (near `extract_toc`):
  `TOCValidationResult(status: Literal["verified","mismatch","skipped"], confidence: str|None, issues: list[str], detail: str)`.
- **Function** `agent.validate_toc(*, entries: list[TOCEntryExtracted], pdf_path, subject, book_id, provider, model, transport) -> TOCValidationResult`:
  1. Build the window via `_toc_source_pdf(pdf_path, settings.extract_toc_front_pages, settings.extract_toc_back_pages)`. `None` → return `skipped` (detail "no contents-page window").
  2. For gemini the window vision-attaches over `transport=api` (matches the `extract_toc` vision rule `agent.py:1376`); else force `transport="cli"`.
  3. Prompt: present the extracted entries (compact `section_number  section_title  p.start` lines) + instruct: "The attached pages are the textbook's printed contents page(s). Decide whether the list faithfully reflects them. Return `mismatch` ONLY if entries are clearly wrong/garbled/invented or major sections are missing; minor ordering/page-number noise is `verified`." Schema-constrained to `TOCValidation`.
  4. `_spawn` (pinned `provider`/`model`), parse `TOCValidation`, record usage `operation="toc.validate"` (success/fail), always `window.unlink()` in `finally`.
  5. **Any** exception (spawn, rc!=0, ValidationError) → return `skipped` (detail = short reason); NEVER raise. No retry loop (one-shot; failure degrades, doesn't block).
  6. `verdict=="mismatch"` → `status="mismatch"`; else `status="verified"`. `detail` = `"; ".join(issues)` (capped).
- **Test** `tests/services/test_validate_toc.py` (monkeypatch `_spawn` + `_record_usage`): verified verdict → `status="verified"`; mismatch + issues → `status="mismatch"`, issues surfaced; `_spawn` raises → `skipped`; bad JSON → `skipped`; `_toc_source_pdf` returns None (monkeypatch) → `skipped` and `_spawn` NOT called.
- **Commands**: `uv run python -m pytest tests/services/test_validate_toc.py -q`
- **Commit**: `feat(agent): validate_toc — gemini-flash vision check of extracted TOC`

## Task 3 — wire soft-gate into `toc_extractor.run` + settings

- **Settings** `app/config.py` (Extract-robustness block ~`:172`):
  `toc_validation_enabled: bool = True`, `toc_validation_provider: str = "gemini"`,
  `toc_validation_model: str = "gemini-2.5-flash"`.
- **`toc_extractor.run`** (`toc_extractor.py`): after the 0-entry guard (`:82`), before persist:
  - `result = None`; if `settings.toc_validation_enabled`: `result = await agent.validate_toc(entries=extracted.entries, pdf_path=file_path, subject=subject, book_id=book_id, provider=settings.toc_validation_provider, model=settings.toc_validation_model, transport=toc_transport)`. (Disabled → `result` stays `None` → behaves exactly like today.)
  - Persist entries (unchanged `delete_for_book`+`bulk_create`).
  - `final_status = "toc_review" if (result and result.status == "mismatch") else "toc_ready"`.
  - `await books_repo.set_status(session, book_id, final_status)`; **only when `result is not None`** also `await books_repo.set_toc_validation(session, book_id, result.status, result.detail or None)` (new repo helper, Task 4) — disabled leaves `toc_validation` NULL ("not validated"), distinct from `skipped` ("validator ran, no window").
  - SSE: on `toc_review` publish `("toc_review", {"entries":[…], "validation": {"verdict": result.status, "issues": result.issues}})`; on `toc_ready` keep the existing `("toc_ready", {"entries":[…]})`.
  - Log the verdict.
- **Test** `tests/services/test_toc_extractor_validation.py` (monkeypatch `agent.extract_toc`, `agent.validate_toc`, repos/bus, `SessionLocal`): mismatch → `set_status(...,"toc_review")` + entries persisted + `toc_review` SSE; verified → `toc_ready`; skipped → `toc_ready`; `toc_validation_enabled=False` → `validate_toc` NOT called, `toc_ready`.
- **Commands**: `uv run python -m pytest tests/services/test_toc_extractor_validation.py -q`
- **Commit**: `feat(toc): soft-gate extraction on vision validator verdict`

## Task 4 — API: accept endpoint, retry-from-review, BookOut fields, review TOC attach

- **Repo** `app/repositories/books.py`: `set_toc_validation(session, book_id, verdict: str|None, detail: str|None)` (assigns both fields; mirrors `set_status`). Add `"toc_review"` to `list_running_for_sweep`? **No** — sweep is for stuck in-flight rows; `toc_review` is a settled state, leave it.
- **Schema** `app/schemas/book.py` `BookOut`: add `toc_validation: Optional[str] = None`, `toc_validation_detail: Optional[str] = None`.
- **`_book_out_with_toc`** (`books.py:447`): attach `toc` for `status in ("toc_ready","toc_review")`.
- **Accept endpoint** `POST /books/{book_id}/toc/accept`: load book; 404 if missing; 409 unless `status=="toc_review"`; `set_status(...,"toc_ready")`; commit; return `_book_out_with_toc`. (Validation columns kept as the audit trail.)
- **Retry** (`retry_toc_extraction` `:238`): add `"toc_review"` to the allowed statuses so Retry re-extracts a flagged book.
- **SSE `stream_toc`** (`:282`): add a `toc_review` branch — emit `("toc_review", {entries, validation})` as a terminal event (mirror the `toc_ready` branch, status from `book.toc_validation`/`error_message`).
- **Test** `tests/api/test_toc_accept.py` (httpx, monkeypatch repos as siblings do): accept on a `toc_review` book → `toc_ready`; accept on `toc_ready` → 409; accept on missing → 404; retry allowed from `toc_review`. `BookOut` serializes the two new fields.
- **Commands**: `uv run python -m pytest tests/api/test_toc_accept.py -q`
- **Commit**: `feat(books-api): accept/retry a toc_review book + expose validation fields`

## Task 5 — FE types + API client

- `web/src/lib/types.ts`: extend the `BookStatus` union (`:64`) with `"toc_review"`; on the
  `Book` interface (`:94-107`, near `error_message` `:102`) add
  `toc_validation?: "verified" | "mismatch" | "skipped" | null` and `toc_validation_detail?: string | null`.
- `web/src/lib/api.ts`: add `acceptToc(bookId)` → `POST /api/v1/books/{id}/toc/accept` returning
  `Book`, mirroring `retryBookToc` (`:281-287`).
- **Acceptance**: `cd web && npx tsc -p tsconfig.app.json --noEmit`.
- **Commit**: `feat(web): Book.toc_validation types + acceptToc client`

## Task 6 — FE library badge + book-page review panel + launcher visibility

- `web/src/routes/library.tsx`: add `toc_review` to the `StatusBadge` map (`:486-505`) — an amber
  "needs review" chip (distinct from `toc_extracting`'s "indexing"). The `ready` / `inFlight`
  derivations (`:302-303`) stay as-is: `toc_review` is intentionally neither (not launchable, not in-flight).
- `web/src/components/fleet/launcher.tsx`: the tray split (`:156-161`, re-applied `:406-411`) groups
  preparing / failed / ready and would **drop `toc_review` from all three** (it vanishes). Add
  `toc_review` to the **failed/attention** bucket so a flagged book stays visible and links to its
  book page — it must not appear under "ready".
- `web/src/routes/book.tsx`: add a `toc_review` branch. Render a review panel (reuse the error-block
  region `:175-192`) showing the `toc_validation_detail` issues + **Accept anyway** (`api.acceptToc`
  → clear/refetch, stream re-enable like `handleRetry` `:68-82`) and **Retry** (`api.retryBookToc`)
  buttons. Keep the TOC entries rendered below so the operator judges them (`_book_out_with_toc` now
  returns `toc` for `toc_review`). Add `toc_review` to `STATUS_LABEL` (`:31-34`) and handle the new
  `toc_review` SSE event (`:89-92` sibling) to populate entries + issues.
- **Acceptance**: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`.
- **Commit**: `feat(web): toc_review badge + book-page Accept/Retry panel + launcher visibility`

## Task 7 — Acceptance smoke (real Gemini)

- `scripts/toc_validate_smoke.py` (`-m scripts.toc_validate_smoke`): against a real local book PDF, call `agent.validate_toc` twice — (a) with the book's actual extracted entries → expect `verified`; (b) with a deliberately scrambled/truncated entry list → expect `mismatch`. Print verdicts + issues. Real gemini call (Vertex/api per env). This is the fact-over-theory proof the verdict discriminates.
- **Commit**: `test(toc): real-gemini validate_toc smoke`

## Task 8 — Finish

- Full suite green (`uv run python -m pytest tests/ -q`).
- Rebase-check: `git fetch origin` + `git log HEAD..origin/Nggaev-v2`; rebase onto `origin/Nggaev-v2` if it moved; re-run suite.
- Worklog entry in `docs/memory/MASTER_MEMORY.md` + INDEX row; close any related ROADMAP/WISHLIST line.
- `git mv` this plan → `docs/superpowers/plans/shipped/`.
- De-stale `docs/HOW_IT_WORKS.md` (TOC-extract flow + new status), `docs/CODE_MAP.md` (validate_toc, accept endpoint), `docs/DATABASE.md` (new columns/status), `README.md` if status set is documented.
