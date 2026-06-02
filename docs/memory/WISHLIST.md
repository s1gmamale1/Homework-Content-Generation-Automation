# Project Wishlist — capture inbox

> Raw capture of bugs / issues / ideas — **just the idea itself**, one line each.
> No analysis here. When an item is understood (root cause + deliverable), promote it
> to [ROADMAP.md](./ROADMAP.md) and remove it from this list.
> Local-only (gitignored `docs/memory/`).

## Open

- Scanned / image-only PDFs: TOC extraction unsupported (only text PDFs decode; image pages rely on gemini native read).
- Boss Arena `hints` allows 0 / unstructured — would like a 3-tier hint ladder (Why → How → synthesis).
- `mistake_provenance` tag (`source` | `inferred`) on the CBP common-mistake — deferred from WS1.
- Source-fidelity is detect-only (logs a warning on invented concept_ids) — could escalate to hard-fail / retry.
- `opencode` provider is implemented but never run against a real install — first action when installed: one real generation (watch for the stdin/positional hang). Detail: [[MASTER_MEMORY]] §0010.
- Bad book data: math-algebra book `9e7833bc…` has a 4 KB stub PDF (not a real textbook) — extraction would fail; clean up or replace.
- Confirm the English grade→CEFR ladder against the official Uzbek curriculum (values already adjusted this session — just needs curriculum-owner sign-off).
- _(audit 2026-06-02, verified)_ `frontend-6`: `web/src/routes/job.tsx` `DonePanel` summary counts only legacy columns → v2 jobs show partial/empty done-stats. (I rewired `preview.tsx` but not `job.tsx`.)
- _(audit)_ `api-3`: `difficulty_classified` SSE event + `set_difficulty` repo method + `JobOut.difficulty` are functionally dead (classify removed) — remove them.
- _(audit)_ `api-2`: pipeline emits `source_map_ready` / `concept_fidelity_warning` but they have no schema in `app/schemas/events.py` — add the event schemas.
- _(audit)_ memory-check prompt contradiction: `memory-check.md:35` "Use all 3 kinds" vs self-check `:86` "at least 2 of the 3" — a model can emit only 2. Tighten.
- _(audit)_ flashcards prompt `:24` type enum omits schema-allowed `formula`/`vocabulary`/`grammar`/`example` — align the prompt enum to `FlashcardType`.
- _(audit)_ memory-check `why_prompt` is "required for science" in the prompt/docstring but not schema-enforced (`schemas-6`) — enforce via subject-threaded validator, or fix the docstring claim.
- _(audit)_ memory-check models 3 kinds vs spec's 6 question types (`schemas-8`) — extend `MemoryCheckKind` or record as an accepted deviation.
- _(audit)_ `flow-1`: `pipeline.py:797` "scheduler stuck" diagnostic embeds the dep dict as literal f-string text (double-braces, never evaluated) — cosmetic, unreachable path.
- _(dev warning)_ pre-existing React "Select uncontrolled→controlled" on the `<Select>` in `web/src/routes/section.tsx` / `upload.tsx` (provider/model/subject pickers) — give it a default `value=""`. Benign, not from the Flow v2 work.

## Done / promoted

- W1 — `opencode` as 5th CLI provider — ✅ DONE 2026-05-29 (commit 8a96435), see [[MASTER_MEMORY]] §0010. (Verification follow-up moved to Open above.)
- ~~Stale `main` test files break `pytest tests/`~~ — **REMOVED 2026-06-01 (stale/not-applicable).** Those files (`test_flows.py`, `test_providers_registry.py`, `test_agent_models.py`, `test_api.py`) don't exist in our tree — they were the audit machine's untracked locals. Our suite is green (227); the only `SUBJECT_FLOWS` ref is a passing negative assertion (`test_general_flow.py:33`).
