# Backlog R2–R8 — Backend Fix Pass (Design)

**Date:** 2026-06-01 · **Branch:** Nggaev-v2 · **Source backlog:** `docs/memory/ROADMAP.md` (R2–R8), re-verified at source 2026-06-01.

**Goal:** Fix the seven worked-up backend issues R2–R8 in order, as one cohesive pass, then hand to `writing-plans` for an ordered task-by-task plan.

**Scope discipline:** all changes are **backend** (`app/…`, `prompts/_general/…`). The live frontend session is `web/`-only, so this work is **file-orthogonal and parallel-safe**: stage only backend files, never start a server, commit per fix. This repo is the **content factory** — R3 emits marking *structure as content*, it does NOT compute live scores.

---

## Decisions settled in brainstorming (the 3 design-bearing items)

### R2 — `_subset_pdf` reads the wrong physical pages → **Approach B: size-gated subsetting**
- **Root cause (verified):** `_subset_pdf` (`agent.py:1282`) slices by printed page number used as a physical index, no front-matter offset. `toc_entries` has only `page_start/page_end` (printed); **no offset column exists**. The subset is built unconditionally whenever a range exists (`agent.py:1336`).
- **Fix:** in `extract_lesson_context`, only call `_subset_pdf` when `pdf_path.stat().st_size > _GEMINI_PDF_MAX_BYTES` (`agent.py:143` = 20 MB). Otherwise attach the **full** PDF (the printed page range is already in the extract prompt, `agent.py:1324-1330`). For sub-20 MB books (the common case, incl. the 15.7 MB test book) no slicing happens → wrong-pages bug gone.
- **Residual:** for >20 MB books, subsetting still runs and the offset risk remains *there only* — log a warning; revisit with a per-book offset (the rejected Approach A) only if a real >20 MB book bites. YAGNI.
- **Note:** independent of R6 — R6's `keep_pdf` guard is in `extract_toc`; R2's subset is in `extract_lesson_context`. Different functions.

### R4 — `CbpModeGame` not pinned to its phase's mode → **Approach A: per-phase `Literal` subclasses**
- **Root cause (verified):** all 4 game phases map to the same `CbpModeGame` (`agent.py:126-129`); `_payload_matches_mode` checks payload↔mode but never phase↔mode. So `practice-tictactoe` can validate a `memory_match` game.
- **Fix:** 4 thin subclasses overriding the field to a single-value `Literal` (e.g. `class TicTacToeGame(CbpModeGame): interaction_mode: Literal["tictactoe"]`), mapped one-per-phase in `STRUCTURED_PHASE_SCHEMAS`. Pydantic then *fails* a mismatch (→ schema-retry), and the embedded JSON Schema becomes phase-specific so the model is **told** the required mode (prevents the mismatch + lowers first-pass failures, synergy with R8).
- **Safe (verified):** no `is/== CbpModeGame` identity assumptions exist; synth uses `getattr`; subclasses are `CbpModeGame` instances; frontend reads the JSON `interaction_mode` field, not the Python class. `_payload_matches_mode` keeps working (a Literal mode just constrains the valid payload).

### R8 — structured phases leak SVG into their JSON → **Approach A: trim `_SVG_PHASES`**
- **Root cause (verified):** `_SVG_PHASES` membership appends `_SVG_RULES` to the prompt (`agent.py:599-600`). 6 structured phases (boss-arena, practice-rlc, practice-error-detection, the 4 games) are told to emit SVG but their schemas have **no SVG field** → markup leaks around the JSON → `model_validate_json` fails first pass (observed live: jobs `442cc7c4`).
- **Fix:** remove those 6 from `_SVG_PHASES`. CBP stays (it has `LearningBlock.visual_svg`). This mirrors the **already-proven flashcards fix** (the comment at `agent.py:265-269`) and aligns with Path B's deliberate game-lightening. `_SVG_PHASES` is used only at `agent.py:599` (verified), so removal only drops the SVG instruction.
- **Deferred defense-in-depth:** tolerant JSON extraction ("strip non-JSON before validate" + "emit ONLY the JSON" prompt rule) is a *complementary* hardening, not part of this fix — note it, don't build it now.

---

## Mechanical items (ROADMAP deliverables — no design choice)

### R3 — `reflection.md` contradicts the spec (verified firsthand, all 5 present)
Rewrite `prompts/_general/reflection.md` to:
- **Drop the contradictions/stale content:** the "no scoring"/"Not scored" claim (L3/L47) that coexists with the **≥60%/<60% closing-line branch** (L41-42, a score it never receives); the superseded **"Spaced Repetition Schedule"** Consolidation block (L33-37); the **`kuchaytirad`→`kuchaytiradi`** typo (L42); and the **stale "Mode: Easy or Hard"** input line (L9 — dead since Path A removed easy/hard).
- **Add the spec's debrief content as STRUCTURE (not live scoring):** weak/strong-point prompts tied to boss-arena/CBP prior outputs, a redo route, the "same concepts, not the same questions" retake rule, reflection question(s). The separate student app populates any score — this prompt only emits the scaffolding.
- **Add a conformance test** asserting the contradictions are gone (no "Not scored" + score branch; no Spaced-Repetition heading; no "Easy or Hard").

### R5 — schema fail-fast gaps
Add `model_validator`s: `CaseCheckpoint` (non-empty `feedback`; `0 <= correct_index < len(options)` for choice forms; `mcq` needs options); `CaseSimulation` `correct_path`/`wrong_path` `min_length=1`; bound `TicTacToePayload` correct-cell count (≥1 and not all 9); `MemoryMatchPair.left != right` + distinct pairs; a required Jigsaw **solution** field — `solution: list[list[str]]` (the correct piece-id groupings/pairings the assembly must form, each id referencing a `pieces[].id`; ≥1 grouping; validator checks every id exists in `pieces`); `ErrorDetection` correction ≠ broken-block content.

### R6 — backend/API hardening (all 3 verified)
- Retain the TOC task ref: `books.py:80` `asyncio.create_task(...)` is GC-cancellable → keep a module-level `set`, `.add()` + `.add_done_callback(discard)` (the pattern `worker.py` already uses).
- Hoist the `keep_pdf`/clear-attachments guard out of `if has_local_toc_text:` (`agent.py:1089` → guard at `1106`) so image-only PDFs (no pypdf text) still get the >20 MB protection.
- Derive `_STATS_PROVIDERS` (`jobs.py:359`) from the provider registry / `MODEL_MANIFEST` keys instead of a hardcoded 4 — so `opencode` usage isn't silently dropped.

### R7 — job timeout default too short
Raise `job_timeout_seconds` default (`config.py:38`) 600 → **1800**; fix the stale "HARD biology ~60-90s" comment (Flow v2 CBP alone is ~274 s). Provider-aware timeout = out of scope (YAGNI). (`.env` already has the interim 1800; this makes the code default match.)

---

## Cross-cutting coordination flags
1. **R4 and R5 both edit `practice_games.py`** — sequence R4 before R5 in the plan to avoid churn (same file, sequential).
2. **R5's Jigsaw solution field has a frontend touchpoint.** Adding it means (a) the schema [backend], (b) the jigsaw prompt must emit it [`prompts/_general/practice-jigsaw.md`, backend], and (c) ideally the frontend renderer shows it [`web/src/components/flow-v2/cbp-mode-game.tsx`, **the other session's file**]. To avoid breaking their renderer or old stored data: make the new field **optional in the TS types, populated at generation (forward-only)**. Do **not** silently edit `web/` — flag it for the frontend owner.

## Ordering & testing
- **Order:** R2 → R3 → R4 → R5 → R6 → R7 → R8 (the user's requested order; R4-before-R5 satisfied automatically).
- **Testing:** TDD per item — R2 a size-gating test; R3 the conformance test above; R4 a phase→mode mismatch test (a `practice-tictactoe` schema rejects a `memory_match` payload); R5 one test per new validator; R6 a `_STATS_PROVIDERS`-covers-opencode test + a guard-reached-for-image-PDF test; R7 a default-value test; R8 a `_SVG_PHASES`-excludes-the-6 test. Baseline 227 green; full suite green per fix.
- **Execution:** subagent-driven on `Nggaev-v2`, no worktree, backend files only, commit per fix.
