# Project Wishlist — capture inbox

> Raw capture of bugs / issues / ideas — **just the idea itself**, one line each.
> No analysis here. When an item is understood (root cause + deliverable), promote it
> to [ROADMAP.md](./ROADMAP.md) and remove it from this list.
> Tracked in git (committed alongside the work).

## Open

> All items below re-verified against current code **2026-06-02** — none stale (all still present). Worked-up items live in [ROADMAP.md](./ROADMAP.md): **R9** (Notion SVG/tables→escaped text), **R10** (broken-font PDF → near-empty TOC). Larger planned work (WS5 full Uzbek contract; Notion **Phase 2 pull**) tracked in `next-steps-flow-v2` memory.
>
> _**2026-06-03** — md-per-phase architecture flip SHIPPED + live-verified (worklog **0028**). **Effort B — DEFERRED by decision (2026-06-03), do it once + subject-specific.** Do NOT rewrite the prompts as *general* prompts now. Rationale: the `docs/Infra_prompts` specs are already organized **by subject-family** (CBP & Flashcards ship standard/languages/math/sciences variants), and our app subjects map onto those families — sciences = biology·kimyo·physics, math = math-algebra·geometriya, languages = english, humanities = history. Collapsing those variants into one general prompt now and re-splitting later = doing the variant work **twice**. The clean path is **one pass, subject-specific**: flip `USE_SUBJECT_PROMPTS=True` and populate `prompts/<subject>/<phase>.md` straight from the matching Infra family variant. No urgency — the Effort-A *minimal* general prompts already produce good content (Kimyo §1 smoke verified, worklog 0028). **Prompts are pure content (no backend change to edit them — just a server restart to reload the startup cache); the only code touch would be a new template token or the `USE_SUBJECT_PROMPTS` flip.** **Open design Q for that effort:** the prompt resolver keys on **subject** (7 dirs) but the specs vary by **family** (4) — either duplicate each family prompt across its subjects, or add a small family-resolution layer. The per-phase deterministic validator rules (`phase_validator.RULES`) + the Uzbek Foundation language contract belong to this same future effort — validator rules are *derived from* the finalized prompts, so they come **after** the rewrite, not before. **Quick Effort-B win:** `reflection.md` outputs `##`-level headings (no top-level `#`), so the warn-only validator flags **every** reflection with "missing top-level heading" — give reflection a `#` title (or exempt it from that rule). **Validator is warn-only today** (flag = logged + stored on `phase_outputs.validation_warnings` + shown in console; never blocks/regenerates, flagged content still ships to Notion/download). Future option: a per-rule `blocking` flag that regenerates the phase once on failure (the old schema path did this) — promote rules only once proven._

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
- _(2026-06-02)_ SSE teardown noise: the book TOC stream (`/toc/stream`) logs a benign `Exception terminating connection … CancelledError` when an `EventSource` closes (asyncpg + sse-starlette session cleanup races the cancel). Request still 200 — cosmetic. Fix: swallow the cancel on the SSE session teardown.
- _(2026-06-02)_ Stale `pending` jobs with `attempts == max_attempts` (e.g. `2848dbcb`) are never claimed → stuck in `pending` **forever** (misleading status; the claim query skips attempts-exhausted rows but never fails them). A startup/periodic sweep should mark attempts-exhausted `pending` rows `failed`.
- _(2026-06-02)_ Orphan-reclaim window is hardcoded `job_timeout × 2` (`worker.py:235`, = 1 hr at 1800s). A manually-killed job can't be resumed/cleared without DB edits for ~1 hr. Add a separate `reclaim_stale_seconds` setting so dead-job recovery is faster **without** shortening the real job-execution timeout (the R7 concern).
- _(2026-06-02)_ Notion anchor **auto-resolve** (ties to Phase 2): the Notion `{N}-sinf` page lists every subject by display name (Kimyo, Fizika, Algebra…), so the app could resolve the subject-page ID by crawling (grade → `{N}-sinf` → child whose title matches the subject label) instead of the hand-maintained `NOTION_SUBJECT_PAGES` map. Would eliminate the **silent per-subject skip** (Kimyo incident: an unmapped subject just logs "no subject-page mapping … skipping" and the homework never pushes). Surface unmapped skips in the UI/job result either way.
- _(2026-06-02)_ Upload-form intro copy still says "classifies the lesson you choose" — classify was removed; reword.

### Frontend

_None open — the 3 prior frontend items were fixed 2026-06-02 (see **Done / promoted** below)._

### Database / Persistence

> Catalogue of data-model / persistence / job-lifecycle issues. Scope = schema shape,
> column semantics, row lifecycle, data integrity, and how persisted state is (or isn't)
> used on retry/resume. NOTE: items here may have their *fix* in pipeline/worker code
> even when the *symptom* is about persisted state — tag each with where the fix lives.

- _(2026-06-03)_ **No phase-level resume on job retry** (fix lives in **`pipeline.py`**, NOT a schema change — the schema is already sufficient). When a job is retried/reclaimed (e.g. worker died mid-job on a session/throttle limit), the scheduler rebuilds `pending = set(content_phases)` (`pipeline.py:351`) with **no filter for already-`done` phases**, and `create_or_reset` (`pipeline.py:468`) **wipes each existing phase row** (clears `output_md`, status→pending). So all content phases regenerate from scratch — a job that completed 8/9 phases and only lost `reflection` re-runs all 8 good phases (~16 min + budget) to recover the one missing phase. **Only `extract` is reused** (cross-job cache, `find_latest_extract`). The DB is fine — `phase_outputs` already persists per-phase `output_md` + `status`, exactly what a resume needs; the gap is that the pipeline doesn't *read* prior `done` rows and skip them. **Desired:** on (re)start, skip phases already `done` for this job (re-inject their `output_md` into `prior_outputs`), only run `pending`/`failed` ones. High-value for 24/7 autonomy (a mid-job throttle currently wastes all prior phases) — ties to the autonomous-generation reliability section ([[PRODUCTION_AUTONOMOUS_GENERATION]] §3).

## Done / promoted

- **Frontend trio — ✅ FIXED 2026-06-02 (Nggaev-v2, commit `e582f53`; tsc + vite build clean. Pending: user browser-verify).** (1) `frontend-6` — `job.tsx` `DonePanel` now counts the v2 columns (source concepts · case checkpoints · memory checks · Boss Arena questions w/ legacy final-challenge fallback · practice-arc games), so v2 jobs show real done-stats. (2) **Grade input** — optional Grade select (1–11) added to `upload.tsx`, wired `api.uploadBook → POST /books` `grade` field (no more SQL for new uploads). (3) `<Select>` uncontrolled→controlled — model picker `section.tsx` now `value={model ?? ""}`.
- W1 — `opencode` as 5th CLI provider — ✅ DONE 2026-05-29 (commit 8a96435), see [[MASTER_MEMORY]] §0010. (Verification follow-up moved to Open above.)
- ~~Stale `main` test files break `pytest tests/`~~ — **REMOVED 2026-06-01 (stale/not-applicable).** Those files (`test_flows.py`, `test_providers_registry.py`, `test_agent_models.py`, `test_api.py`) don't exist in our tree — they were the audit machine's untracked locals. Our suite is green (227); the only `SUBJECT_FLOWS` ref is a passing negative assertion (`test_general_flow.py:33`).
