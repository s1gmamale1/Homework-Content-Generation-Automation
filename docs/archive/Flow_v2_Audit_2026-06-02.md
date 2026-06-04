# NETS Homework Flow v2 — Code & Conformance Audit

**Date:** 2026-06-02
**Branch:** `Nggaev-v2`
**Scope:** Does the codebase have code/logic bugs, and does it generate homework exactly as [`docs/New_Flow.md`](./New_Flow.md) describes (section structure + the 20 flow-level forbidden rules)?
**Method:** 9 independent parallel auditors (flow orchestration, schema contracts, learning prompts, practice/boss/reflection prompts, backend services, end-to-end wiring, API/models, frontend, live test run). Every finding was re-opened at its cited `file:line` by an adversarial verifier before being counted. **53 findings → 36 confirmed at source, 1 refuted, rest low/info passthrough.**

---

## Executive summary

1. **Bugs?** — **Yes.** Several real code/logic bugs. None crash the happy path, but one (`_subset_pdf` page mapping) can make a packet describe **the wrong textbook pages**, and several schema/prompt gaps let spec-violating content through.
2. **Builds homework per `New_Flow.md`?** — **Generator backend: mostly.** Correct section cycle for all 7 subjects, deadlock-free DAG, no silent-data-loss wiring gaps, and ~14 of 20 forbidden rules structurally enforced. **Three real divergences:** the **Reflection phase contradicts the spec**, the **practice-game schema isn't pinned to its skill**, and several "fail-fast" rules are only weakly enforced. **Frontend runtime: no** — it cannot render any v2 output.

### Conformance scorecard

| Dimension | Verdict |
|---|---|
| Flow orchestration & assembly (`flows.py`, `pipeline.py`) | ✅ Conforms |
| End-to-end wiring integrity (no silent data loss) | ✅ Conforms |
| Pydantic content contracts (`app/schemas/*`) | 🟡 Partial |
| Learning prompts (CBP, flashcards, memory-check) | 🟡 Partial |
| Practice / Boss / **Reflection** prompts | 🟡 Partial (Reflection diverges) |
| Backend services (worker, agent, providers, toc) | 🟡 Partial |
| API / model manifest / SSE events | 🟡 Partial |
| **Frontend SPA** | 🔴 Diverges |

### What is solidly correct

- `flows.flow_for()` yields the spec cycle for every subject: **Learning Sections** (case-based-preview, flashcards, memory-check) → **Practice Arc** (practice-rlc, practice-error-detection, one subject-matched game) → **Boss Arena** → **Reflection**.
- The wave scheduler (`_run_content_phases_parallel`) is **acyclic, deadlock-free, and race-free** on the shared `prior_outputs` dict (single-threaded asyncio; a phase launches only after its deps are present).
- **Wiring is complete:** all 11 tail phases pass through every link (prompt → `STRUCTURED_PHASE_SCHEMAS` → `_synth_md_for_structured` → `_JSON_COLUMN_SETTERS` → repo setter → JSONB column → alembic migration → assembly division). No phase silently produces an empty body. `reflection` is intentionally prose and routes through the markdown path with an empty-output retry.
- Strongly enforced forbidden rules: **#4/#6** (CBP exactly 3 checkpoints + required DPE), **#5** (simulation carries correct+wrong path — structurally present), **#15/#16** (Boss requires non-empty Why→How→What, no MCQ field), **#1/#2** (concept-id source-fidelity check vs the SourceMap), **#10/#11** (memory-check depends on flashcards; recall-not-apply; 0.60 threshold).

---

## Confirmed findings

Severity = adversarial-verifier-corrected. IDs prefixed by audit dimension. Files are repo-relative.

### A. Backend service bugs

| ID | Sev | File:line | Issue | Fix |
|---|---|---|---|---|
| `backend-3` | **Med** | `app/services/agent.py:1257-1300, 1336-1337` | **`_subset_pdf` maps textbook *printed* page numbers 1:1 onto *physical* PDF indices**, unconditionally, whenever the TOC range is valid. Any book with front-matter offset (cover/preface/TOC — very common) reads the **wrong pages** → wrong `lesson_context` → wrong SourceMap → the whole packet can be about the wrong material. Fallback to full PDF only triggers on error/empty, never on offset. The docstring itself admits this is unverified. | Learn/store a per-book printed-vs-physical page delta and apply before slicing; or only subset when the PDF exceeds the extractor size limit; or attach the full PDF and let the prompt name the printed range. |
| `backend-2` | **Med** | `app/services/agent.py:1086-1109` | **>20 MB Gemini guard is bypassed for scanned/image-only PDFs.** The `keep_pdf`/clear-attachments guard lives inside `if has_local_toc_text:`. When pypdf yields no text, the guard is skipped and the full PDF stays attached → Gemini rejects >20 MB → TOC extraction crashes instead of degrading. | Hoist the size check + `keep_pdf` gate out of the `has_local_toc_text` block so an oversized PDF drops the attachment regardless. |
| `backend-1` | **Med** | `app/api/v1/books.py:80` | **TOC extraction is a fire-and-forget `asyncio.create_task` with no retained reference** → may be GC-cancelled mid-run (asyncio keeps only a weak ref). The book then stalls with no TOC and no error. `worker.py` shows the correct retain-in-set pattern. | Hold a strong ref (`set.add(task)` + `add_done_callback(discard)`), or move TOC onto the Postgres queue/worker. |
| `backend-4` | Low | `app/api/v1/jobs.py:213-274` | SSE streams can miss the terminal `job_completed`/`error` event via a check-then-subscribe race. | Subscribe **before** the DB precheck (then read status, then drain), or re-read status after subscribe and replay the terminal event. |
| `flow-1` | Low | `app/services/pipeline.py:795-798` | The "scheduler stuck" diagnostic embeds `{p: list(resolve_phase_deps(...))}` as **literal text in an f-string** (printed verbatim, never evaluated). Cosmetic; only on an unreachable path. | Compute the dict into a var, then interpolate it. |

### B. API / SSE contract

| ID | Sev | File:line | Issue | Fix |
|---|---|---|---|---|
| `api-1` | **Med** | `app/api/v1/jobs.py:359` | **`/agent/stats` silently drops all `opencode` usage.** `_STATS_PROVIDERS` is hardcoded to 4 providers; `opencode` is a registered 5th, so its rows (aggregated fine in SQL) never reach the dashboard. | Derive the tuple from `providers.PROVIDERS` / `MODEL_MANIFEST` keys so it can't drift. |
| `api-2` | Low | `app/schemas/events.py` | `source_map_ready` and `concept_fidelity_warning` are emitted by the pipeline but have **no schema** in `events.py`. | Add `SourceMapReadyEvent` / `ConceptFidelityWarningEvent`. |
| `api-3` | Low | `app/api/v1/jobs.py:240-244` | `difficulty_classified` SSE event + `set_difficulty` repo method are **dead code** — classify/difficulty was removed from the v2 runtime. | Remove the event, replay branch, setter, and (ideally) `JobOut.difficulty`. |

### C. Schema contracts — weak fail-fast (lets spec-violating content validate)

| ID | Sev | Rule | File:line | Issue | Fix |
|---|---|---|---|---|---|
| `schemas-1` | **Med** | #14/#12 | `app/schemas/practice_games.py:210-233` (+ `agent.py:126-129`) | **`CbpModeGame.interaction_mode` is not pinned to its phase.** A `practice-tictactoe` phase validates a `memory_match` game and renders no board → "tasks must match target skill" not enforced. | Per-mode subclasses (`interaction_mode: Literal[...]`) mapped one-per-phase, or a phase→mode check post-parse. |
| `schemas-4` | **Med** | #12 | `app/schemas/practice_games.py:162-199` | TicTacToe accepts **all 9 cells correct**; MemoryMatch accepts identical self-referential pairs; Jigsaw has **no solution/correctness field**. Degenerate "games" with no real interaction. | Bound TicTacToe correct count; require MemoryMatch `left != right` + distinct pairs; add a required Jigsaw `correct_order`. |
| `schemas-2` | **Med** | #6 | `app/schemas/flow_v2.py:75-85` | `CaseCheckpoint.correct_index` has no range check; `feedback` may be empty; an `mcq` checkpoint can have zero options. → checkpoint decisions with no usable consequence. | `model_validator` asserting non-empty feedback and `0 <= correct_index < len(options)` for choice forms. |
| `schemas-3` | **Med** | #5 | `app/schemas/flow_v2.py:87-92` | `CaseSimulation.correct_path` / `wrong_path` can be empty (only `why_wrong_fails` has `min_length=1`). | Add `Field(min_length=1)` to both paths. |
| `schemas-5` | Low | — | `app/schemas/practice_games.py:106-135` | `ErrorDetection` correction may equal the broken block (no real fix). | Validator: `correct_answer != is_error block content`. |
| `schemas-6` | Low | #11 | `app/schemas/memory_check.py:41-67` | `why_prompt` is "REQUIRED for science" in the prompt but never enforced by schema. | Thread subject into validation, or correct the docstring's "enforced by schema" claim. |
| `schemas-8` | Low | — | `app/schemas/memory_check.py:19-24` | Models only 3 kinds vs the 6 question types the spec lists (see `prompts-2` learning). | Extend `MemoryCheckKind`, or record the 3-kind set as an accepted deviation. |

### D. Prompt conformance — Reflection is the headline divergence

> **Reflection (`prompts/_general/reflection.md`) is the single biggest conformance failure.** `New_Flow.md` (lines 179-199) requires a debrief + marking layer (score, weak/strong points, passed/not-passed, redo route, reflection questions) with specific pass/retake terminology. The prompt instead declares "no scoring / Not scored" and emits a generic closing ritual — exactly what the spec forbids.

| ID | Sev | Rule | File:line | Issue |
|---|---|---|---|---|
| `prompts-1` (practice) | **High** | #18, #20 | `prompts/_general/reflection.md:3,44-49` | Declares "no scoring / Not scored"; emits none of the required marking outputs (score, weak/strong points, passed/not-passed, redo route). The generic, performance-disconnected closing the spec forbids. |
| `prompts-3` (practice) | Med | #19 | `prompts/_general/reflection.md:44-49` | No "same concepts, not same questions" retake rule; no redo route. |
| `prompts-4` (practice) | Med | — | `prompts/_general/reflection.md:3,41-42,47` | Self-contradiction: says "Not scored" yet branches the closing line on ≥60%/<60% — a score the phase never receives. |
| `prompts-5` (practice) | Med | — | `prompts/_general/reflection.md:33-42` | Reverts to superseded Consolidation content (Spaced Repetition Schedule, fixed motivational closing) — spec marks Consolidation superseded. |
| `prompts-2` (practice) | Low | #20 | `prompts/_general/reflection.md:40-49` | Never uses mandated "Attempt completed, homework not passed" / "Needs Retry"; risks "Not Completed" as a sole label. |
| `prompts-7` (practice) | Low | — | `prompts/_general/reflection.md:42` | Malformed Uzbek word in student-facing output (`kuchaytirad` → `kuchaytiradi`). |
| `prompts-6` (practice) | Low | #12 | `prompts/_general/practice-tictactoe.md:1-12` (+ peers) | Practice-game prompts don't instruct a connected mission arc toward the Boss. |
| `prompts-1` (learning) | Med | — | `prompts/_general/memory-check.md:35,86` | Internal contradiction: rule "Use all 3 kinds" vs self-check "at least 2 of the 3". A model can legitimately emit 2 kinds. |
| `prompts-2` (learning) | Med | — | `prompts/_general/memory-check.md:19` | Implemented 3 kinds (MCQ, fill-blank, choose-explanation) don't cover the spec's 6 types (True/False, Tile Match, Term↔Def, Formula↔Name, Vocab↔Meaning). Recall intent preserved; type menu diverges. |
| `prompts-3` (learning) | Low | — | `prompts/_general/flashcards.md:24,77` | Hand-written type enum omits 4 schema-allowed values while a later rule still says "Include formulas". |

### E. Frontend SPA — diverges entirely (runtime display layer)

> The generator backend is the primary subject; these are runtime-render gaps. Net effect: a generated v2 job's interactive Case-Based Preview, Memory Check, Practice Arc, and Boss Arena are **unrenderable** — the student sees a flat markdown blob.

| ID | Sev | Rule | File:line | Issue |
|---|---|---|---|---|
| `frontend-1` | **High** | — | `web/src/lib/types.ts:64-82` | `Job` declares only the 5 legacy structured columns; **zero** references to the 10 v2 JSON columns anywhere in `web/src`. |
| `frontend-2` | **High** | — | `web/src/routes/preview.tsx:94-164` | `SegmentKind` covers only legacy phases; no renderer for cbp / memory-check / boss-arena / any practice game. |
| `frontend-4` | **High** | #16 | `web/src/components/boss-fight/boss-fight.tsx` | Boss renderer surfaces no Why→How→What (grep count 0) and is fed legacy `final_challenge_json`, not `boss_arena_json`. |
| `frontend-3` | Med | — | `web/src/routes/preview.tsx:118-143` | Markdown split keys off legacy `## Flashcards/Memory Sprint/...` headings the v2 assembler no longer emits → whole packet collapses to one `{kind:'md'}` blob. |
| `frontend-5` | Med | — | `web/src/routes/preview.tsx:192-301` | No Unlock-Gate UI; the required "Enter Practice Arc" / "Practice Arc Unlocked" gate is absent (forbidden "Start Homework" only vacuously avoided). |
| `frontend-6` | Med | — | `web/src/routes/job.tsx:287-304` | `DonePanel` summary counts only legacy columns; v2 jobs show partial/empty stats. |
| `frontend-7` | Med | — | `web/src/lib/types.ts:105-198` | No TS interfaces for any v2 structured shape (CBP, MemoryCheck, BossArena, practice games). |

---

## Refuted finding (for transparency)

| ID | Claim | Why refuted |
|---|---|---|
| `api-4` | "Job stream/download/read endpoints enforce no auth — content is world-readable by UUID." | **False.** Auth is enforced one layer up: `app/api/v1/__init__.py:11-12` mounts both routers with `dependencies=[Depends(get_current_user)]`, which applies to every route. `get_current_user` reads both `Authorization: Bearer` and `?token=`. Proven by `tests/test_api.py::test_list_books_requires_token` (401 without token). Corrected to info. |

---

## Test-suite caveat (NOT Nggaev-v2 bugs)

The `tests-run` auditor saw `uv run python -m pytest tests/ -q` **abort on a collection error** plus 9 failures. **All of these come from 8 stale test files carried over from `main`** (untracked; none exist on `origin/Nggaev-v2`): `test_flows.py` (imports removed `SUBJECT_FLOWS`), `test_providers_registry.py` / `test_agent_models.py` (assert 4 providers; code correctly ships 5 incl. `opencode`), and `test_api.py` (7 failures = no local Postgres on :5432). The application code is correct in every case.

**The committed Nggaev-v2 suite is green** — verified by running only tracked tests: **227 passed, 0 failed** (schemas 57 ✓, synth/flow tests ✓). The new tests genuinely lock most New_Flow guarantees (3 checkpoints, DPE-non-MCQ, Boss Why/How/What, memory-check kinds + threshold, source fidelity, 3-division assembly).

- Findings `tests-1` / `backend-5` / `tests-2` / `tests-3`: artifacts of the stale carried-over files — **delete or port them to the v2 API** so `pytest tests/` runs clean.
- Real coverage hole: **no test asserts Reflection pass/retake terminology (#18/#19/#20)** — and the prompt actively contradicts the spec (see `prompts-1` practice).

---

## Prioritized remediation plan

1. **Rewrite `prompts/_general/reflection.md`** — emit the spec's marking block (score, weak/strong points, passed/not-passed, redo route tied to boss/CBP prior outputs) + the same-concepts retake rule + correct terminology; drop "no scoring" and the consolidation ritual. *(Top conformance value — fixes `prompts-1/2/3/4/5/7` practice.)*
2. **Fix `_subset_pdf` page mapping** (`backend-3`) — *top correctness value.*
3. **Pin `CbpModeGame` to its phase mode** + tighten schema fail-fast (`schemas-1/2/3/4`).
4. **Backend hardening** — retain the TOC task ref (`backend-1`); hoist the >20 MB guard (`backend-2`); add `opencode` to `/agent/stats` (`api-1`).
5. **Memory-check prompt consistency** (`prompts-1/2` learning) + flashcards enum (`prompts-3` learning).
6. **Frontend** — add v2 types + renderers + Unlock-Gate, route boss to `boss_arena_json` (`frontend-1..7`) — when the runtime UI is in scope.
7. **Tests** — delete/port the carried-over `main` test files; add a Reflection conformance test.

---

## Appendix — files audited

`app/services/{flows,pipeline,agent,worker,events_bus,toc_extractor,prompts,agent_models}.py`, `app/services/providers/*`, `app/schemas/*`, `app/api/v1/{jobs,books,health}.py`, `app/models/homework_job.py`, `app/repositories/*`, `app/auth.py`, `main.py`, `alembic/versions/0006-0015_*`, `prompts/_general/*.md`, `web/src/**`, `tests/**`. Ground truth: `docs/New_Flow.md`.

*Generated by a 9-dimension parallel audit with per-finding adversarial verification.*
