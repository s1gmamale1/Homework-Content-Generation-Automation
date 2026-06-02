# Project Wishlist — capture inbox

> Raw capture of bugs / issues / ideas — **just the idea itself**, one line each.
> No analysis here. When an item is understood (root cause + deliverable), promote it
> to [ROADMAP.md](./ROADMAP.md) and remove it from this list.
> Tracked in git (committed alongside the work).

## Open

> All items below re-verified against current code **2026-06-02** — none stale (all still present). Worked-up items live in [ROADMAP.md](./ROADMAP.md): **R9** (Notion SVG/tables→escaped text), **R10** (broken-font PDF → near-empty TOC). Larger planned work (WS5 full Uzbek contract; Notion **Phase 2 pull**) tracked in `next-steps-flow-v2` memory.

### Backend

**Prompts / schema**
- memory-check prompt contradiction: `prompts/_general/memory-check.md:35` "Use all 3 kinds" vs self-check `:86` "at least 2 of the 3" — a model can emit only 2. Tighten.
- flashcards prompt `:24` type enum omits schema-allowed `formula`/`vocabulary`/`grammar`/`example` — align the prompt enum to `FlashcardType` (`app/schemas/flashcards.py`).
- memory-check `why_prompt` is "required for science" in the prompt/docstring but NOT schema-enforced (`app/schemas/memory_check.py`, `schemas-6`) — add a subject-threaded validator or fix the docstring claim.
- memory-check models 3 kinds (`MemoryCheckKind`) vs spec's 6 question types (`schemas-8`) — extend the enum or record as an accepted deviation.

**API / events / dead code**
- `api-2`: pipeline emits `source_map_ready` / `concept_fidelity_warning` but they have no schema in `app/schemas/events.py` — add the event schemas.
- `api-3`: `difficulty_classified` event + `set_difficulty` repo method + `JobOut.difficulty` + `DifficultyClassifiedEvent` are functionally dead (classify removed) — remove them.
- `flow-1`: `pipeline.py:802` "scheduler stuck" diagnostic embeds the dep dict as literal f-string text (double-braces, never evaluated) — cosmetic, unreachable path.

**Robustness / data / features**
- Source-fidelity is detect-only (logs a warning on invented concept_ids) — could escalate to hard-fail / retry.
- Scanned / image-only PDFs: TOC extraction unsupported (only text PDFs decode; image pages rely on gemini native read). Sibling of R10.
- `opencode` provider implemented but never run against a real install — first action: one real generation (watch the stdin/positional hang). Detail: [[MASTER_MEMORY]] §0010.
- Bad book data: math-algebra book `9e7833bc…` has a 4 KB stub PDF (not a real textbook) — clean up or replace.
- Boss Arena `hints` allows 0 / unstructured — would like a 3-tier hint ladder (Why → How → synthesis).
- `mistake_provenance` tag (`source` | `inferred`) on the CBP common-mistake — deferred from WS1.
- Confirm the English grade→CEFR ladder against the official Uzbek curriculum — needs curriculum-owner sign-off (external).

### Frontend

_None open — the 3 prior frontend items were fixed 2026-06-02 (see **Done / promoted** below)._

## Done / promoted

- **Frontend trio — ✅ FIXED 2026-06-02 (Nggaev-v2, commit `e582f53`; tsc + vite build clean. Pending: user browser-verify).** (1) `frontend-6` — `job.tsx` `DonePanel` now counts the v2 columns (source concepts · case checkpoints · memory checks · Boss Arena questions w/ legacy final-challenge fallback · practice-arc games), so v2 jobs show real done-stats. (2) **Grade input** — optional Grade select (1–11) added to `upload.tsx`, wired `api.uploadBook → POST /books` `grade` field (no more SQL for new uploads). (3) `<Select>` uncontrolled→controlled — model picker `section.tsx` now `value={model ?? ""}`.
- W1 — `opencode` as 5th CLI provider — ✅ DONE 2026-05-29 (commit 8a96435), see [[MASTER_MEMORY]] §0010. (Verification follow-up moved to Open above.)
- ~~Stale `main` test files break `pytest tests/`~~ — **REMOVED 2026-06-01 (stale/not-applicable).** Those files (`test_flows.py`, `test_providers_registry.py`, `test_agent_models.py`, `test_api.py`) don't exist in our tree — they were the audit machine's untracked locals. Our suite is green (227); the only `SUBJECT_FLOWS` ref is a passing negative assertion (`test_general_flow.py:33`).
