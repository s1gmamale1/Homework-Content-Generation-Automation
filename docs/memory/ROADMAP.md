# Project Roadmap — worked-up items

> Items promoted from [WISHLIST.md](./WISHLIST.md) once understood. Each entry states:
> **Issue** (what's wrong / wanted) · **Root cause** (why, with code/doc references) ·
> **Deliverable** (the concrete result after the fix). Move to "Shipped" when done.
> Local-only (gitignored `docs/memory/`).

---

## R1 — Inert subject prompts will mislead/break if the override layer is revived

- **Issue:** the per-subject `prompts/<subject>/*` dirs are dead but on disk (the `USE_SUBJECT_PROMPTS=False` override layer). If that switch is ever flipped True, two stale artifacts surface: (a) English prompts reference the deleted `classify.md` for CEFR; (b) `practice-rlc.md` files still carry a "reverse-test variant" the RLC spec never defined.
- **Root cause:** Path A (worklog 0019) intentionally left subject dirs untouched as a future override layer; `classify` was removed from the live path but not from those inert files; the spec-unsupported RLC reverse-test was only stripped from `_general/practice-rlc.md`, not the inert subject copies.
  - Refs: `app/services/prompts.py` `_resolve_dir`/`USE_SUBJECT_PROMPTS`; `prompts/english/classify.md`; `prompts/<subject>/practice-rlc.md`.
- **Deliverable:** when (if) the override layer is revived, scrub the inert subject prompts — remove `classify.md` CEFR references and the reverse-test variant — before flipping the switch.

---

> R2–R6 below: confirmed findings from `docs/Flow_v2_Audit_2026-06-02.md`, each re-verified at source by adversarial sub-agents (2026-06-01). The audit's frontend section was STALE (already fixed by the pushed `web/` Flow v2 work) and is not logged here.

## R2 — `_subset_pdf` maps printed→physical pages 1:1 (can read the WRONG pages)

- **Issue:** a packet can describe the wrong textbook pages. Top correctness bug in the audit.
- **Root cause:** `_subset_pdf` slices the PDF by `page_start-1 … page_end-1` (printed page numbers) as physical indices, with no front-matter offset correction. Any book with cover/preface/TOC offset reads the wrong physical pages → wrong `lesson_context` → wrong SourceMap → whole packet off. Fallback to full PDF fires only on error/empty, never on offset. The docstring itself admits it's unverified.
  - Refs: `app/services/agent.py:1257-1300, 1336-1337` (verified: slice at ~1282-1283, docstring ~1269-1273).
- **Deliverable:** learn/store a per-book printed-vs-physical page delta and apply before slicing; OR only subset when the PDF exceeds the extractor size limit; OR attach the full PDF and let the prompt name the printed range.

## R3 — Reflection phase contradicts the spec (stale Consolidation/stub hybrid)

- **Issue:** `reflection.md` is the single biggest conformance failure vs `New_Flow.md`. It declares "no scoring / Not scored" yet branches its closing line on ≥60%/<60% (a score it never receives — flat self-contradiction), reverts to superseded "Consolidation" content (Spaced Repetition Schedule), omits the spec's debrief/marking outputs (weak/strong points, passed/not-passed, redo route, retake rule), and has a Uzbek typo (`kuchaytirad`→`kuchaytiradi`).
- **Root cause:** the prompt was only partially migrated to v2 — a hybrid of the old Consolidation phase + a stub Reflection. Refs: `prompts/_general/reflection.md:3,33-49`; spec `docs/Infra_prompts/Flow/New_Flow.md` (~179-199, 215).
- **⚠ Scope caveat:** `New_Flow.md` wants a *marking* layer (score/pass-fail/redo). The project is **content-only — no student scoring here**. Conformant fix = **emit the marking STRUCTURE as content** (redo route, "same concepts not same questions" retake rule, reflection questions, weak/strong-point prompts tied to boss/CBP prior outputs) for the separate student app to populate — NOT compute a live score. The ≥60% branch + Consolidation ritual + typo are bugs regardless of scope.
- **Deliverable:** rewrite `reflection.md` to emit the spec's debrief/marking content (scope-respecting), drop "no scoring" self-contradiction + Spaced-Repetition block, fix the typo; add a Reflection conformance test.

## R4 — `CbpModeGame` is not pinned to its phase's interaction mode

- **Issue:** a `practice-tictactoe` phase can validate a `memory_match` game (and render no board) — "tasks must match target skill" not enforced. Gap in the Path B work we just shipped.
- **Root cause:** all 4 game phases map to the same `CbpModeGame` in `STRUCTURED_PHASE_SCHEMAS`; the `_payload_matches_mode` validator only checks payload↔mode internal consistency, never phase↔mode. Refs: `app/schemas/practice_games.py:210-233`, `app/services/agent.py:126-129`.
- **Deliverable:** per-phase `Literal[...]` mode subclasses mapped one-per-phase, OR a phase→mode check post-parse in the pipeline.

## R5 — Flow v2 schema fail-fast gaps (lets spec-violating content validate)

- **Issue:** several schemas accept degenerate/spec-violating content. (a) `CaseCheckpoint`: no `correct_index` range check, `feedback` can be empty, `mcq` can have 0 options. (b) `CaseSimulation`: `correct_path`/`wrong_path` can be empty (only `why_wrong_fails` is `min_length=1`). (c) `TicTacToePayload` accepts all 9 cells correct; `MemoryMatchPair` allows `left==right`; `JigsawPayload` has no correctness/solution field. (d) `ErrorDetection` correction may equal the broken block.
- **Root cause:** validators were never added for these. Refs: `app/schemas/flow_v2.py:75-92`; `app/schemas/practice_games.py:106-135,162-199`.
- **Deliverable:** add `model_validator`s — checkpoint (non-empty feedback + `0<=correct_index<len(options)` for choice forms); simulation paths `min_length=1`; bound TicTacToe correct count; `MemoryMatchPair.left!=right` + distinct pairs; required Jigsaw `correct_order`; ErrorDetection correction != broken-block content.

## R6 — Backend/API hardening (TOC task ref, >20MB guard, opencode stats)

- **Issue:** three independent robustness gaps. (a) TOC extraction can silently stall; (b) oversized scanned PDFs crash instead of degrading; (c) the usage dashboard silently drops all `opencode` consumption.
- **Root cause:** (a) `app/api/v1/books.py:80` fires TOC as `asyncio.create_task` with no retained ref (GC-cancellable; `worker.py` has the correct retain pattern). (b) `app/services/agent.py:1086-1109` — the >20MB Gemini `keep_pdf`/clear-attachments guard lives INSIDE `if has_local_toc_text:`, so image-only PDFs (no pypdf text) skip it. (c) `app/api/v1/jobs.py:359` — `_STATS_PROVIDERS` hardcodes 4 providers; `opencode` (registered 5th) is absent.
- **Deliverable:** retain the TOC task ref (`set.add` + `add_done_callback`); hoist the size/keep_pdf guard out of the `has_local_toc_text` block; derive `_STATS_PROVIDERS` from `providers.PROVIDERS`/`MODEL_MANIFEST` keys.

## R7 — Worker job timeout (600s) too short for a full Flow v2 / claude job

- **Issue:** a full claude Flow v2 generation cannot complete — the worker kills it at 600s and retries to failure. **Reproduced live** (job `2848dbcb`, attempt 1 `TIMED OUT after 600s` with boss-arena + reflection unfinished; never reaches assembly so `assembled_md` stays null and `/preview` shows "Not ready"). This is the strongest-verified finding — observed end-to-end, not inferred.
- **Root cause:** `job_timeout_seconds` defaults to 600 (`app/config.py:38`; `.env` `JOB_TIMEOUT_SECONDS`). The comment "HARD biology runs ~60-90s" is stale — Flow v2 CBP alone is ~274s on claude and a full job exceeds 600s. Refs: `app/services/worker.py:178` (`asyncio.wait_for(pipeline.run, timeout=self.job_timeout)`).
- **Deliverable:** raise the default (~1800s), update the stale comment, and consider a provider-aware timeout (cheap models finish faster). **Interim applied 2026-06-01:** set `JOB_TIMEOUT_SECONDS=1800` in `.env` to unblock live testing.

## R8 — Structured phases on visual subjects fail first-pass JSON validation (SVG/prose leaks around the envelope)

- **Issue:** on geometry, `practice-rlc` and `boss-arena` failed `model_validate_json` on attempt 1 — only the single schema-mode retry saved them. Doubles latency + cost (~2× a full claude call), and a **double-failure exhausts the 2 attempts → phase/job fails**. **Observed live** (job `442cc7c4`, 2026-06-01): rlc `Invalid JSON: trailing characters` with SVG markup (`12" fill="#0ea5e9"…`) after the JSON; boss-arena `Invalid JSON: expected value at line 1 column 1` (output began mid-prose `'oping. Agar ∠DAB…'`). Both recovered on attempt 2 that run, but it's fragile.
- **Root cause:** these structured phases (no SVG field in `RealLifeChallenge`/`BossArena`) still emit **non-JSON content around the JSON** — trailing SVG diagram markup and/or leading prose — which breaks `model_validate_json`. Geometry prompts encourage diagrams; the model bleeds raw SVG/prose into or around the structured output. Refs: `app/services/agent.py` `run_phase` retry-once-on-ValidationError (~684); `prompts/_general/practice-rlc.md`, `boss-arena.md`; live log lines 468/470.
- **Deliverable:** harden structured output for visual subjects — e.g. (a) explicit prompt rule "emit ONLY the JSON object, no SVG, no prose before/after" for these phases; and/or (b) tolerant extraction (strip leading/trailing non-JSON before `model_validate_json`); and/or (c) bump retries for structured phases. Measure first-attempt failure rate across subjects before picking.

---

## Shipped

_(none yet — move completed R-items here with their commit/worklog ref)_
