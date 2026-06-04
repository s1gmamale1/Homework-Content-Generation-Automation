# Project Wishlist — capture inbox

> Raw capture of bugs / issues / ideas — **just the idea itself**, one line each.
> No analysis here. When an item is understood (root cause + deliverable), promote it
> to [ROADMAP.md](./ROADMAP.md) and remove it from this list.
> Tracked in git (committed alongside the work).

## Open

> Audited against current code **2026-06-03** (post-Effort-A md-per-phase flip). Items that referenced now-deleted code (structured schemas, source-map, source-fidelity) were removed as moot — see **Done / promoted**. Worked-up items live in [ROADMAP.md](./ROADMAP.md): **R9** (Notion SVG/tables→escaped text), **R10** (broken-font PDF → near-empty TOC). Larger planned work (WS5 full Uzbek contract; Notion **Phase 2 pull**) tracked in `next-steps-flow-v2` memory.

### Effort B — faithful Infra-spec prompt rewrite — ✅ SHIPPED (worklog [0030], 2026-06-04)

> Done: **Option A** `{{FAMILY_RULES}}` token (family-of-4, not subject-of-7); CBP + Flashcards rewritten family-aware (**humanities CBP authored** — no source spec); light/compact/polish passes on the other 9 phases; all folded-in fixes (memory-check **≥2-of-3**, flashcards **in-prompt enum** since `FlashcardType` is deleted, reflection **`#` title**). Live-verified on real gemini history output (validator-clean, 0 dead-vocab). Full detail in worklog [0030]; spec `0dbb34a`, plan `61dc6c6`.
>
> **Still pending (the follow-on, NOT shipped):** per-phase `phase_validator.RULES` (derive from the now-finalized prompts) + the **WS5 Uzbek language contract**. Validator stays warn-only until rules are written; a per-rule `blocking` flag (regenerate a phase on a hard violation) is a future option — promote rules only once proven.

### Backend

**API / events / dead code**
- `api-3`: the classify-era cluster is **dead but still present** — `difficulty_classified` event (emitted `jobs.py:254`), `DifficultyClassifiedEvent` (`events.py:33`), `set_difficulty` (`jobs.py:134`), `JobOut.difficulty` (`job.py:30`) + `homework_jobs.difficulty` column. Classify was removed; difficulty is pinned `None` in the pipeline. Remove the lot (or keep the column nullable if cheaper than a migration).
- `flow-1`: `pipeline.py:396` "Phase scheduler stuck" diagnostic embeds the dep dict as a literal double-brace f-string (`{{p: ...}}`) → renders as static text, never evaluated. Cosmetic, unreachable path.

**Robustness / data / features**
- Scanned / image-only PDFs: TOC extraction unsupported (only text PDFs decode; image pages rely on gemini native read). Sibling of R10. (`toc_extractor.py` unchanged.)
- `opencode` is **too flaky to be a *primary* provider** — live-run 2026-06-04 (job `6a760767`, §0031): hung the full **600s** per-attempt timeout on **every** failover wave, blowing the 1800s job budget → failover+resume rescued it but burned 2 attempts (~35 min wall). Keep as last-resort fallback ONLY; never *request* it. Consider a shorter per-attempt timeout for known-flaky providers (the failover chain + resume already handle it gracefully). (Was: "never run against a real install" — now run, and it hangs.)
- Bad book data: math-algebra book `9e7833bc…` has a 4 KB stub PDF (not a real textbook) — clean up or replace.
- Confirm the English grade→CEFR ladder against the official Uzbek curriculum — needs curriculum-owner sign-off (external).
- _(2026-06-02)_ SSE teardown noise: `/toc/stream` (`books.py:113-150`) logs a benign asyncpg / sse-starlette `CancelledError` when an `EventSource` closes; request still 200 — cosmetic. Guard the cancel on session teardown.
- _(2026-06-02)_ Stale `pending` jobs with `attempts == max_attempts` (e.g. `2848dbcb`) never claimed → stuck `pending` forever (claim query skips attempts-exhausted rows but never fails them). A startup/periodic sweep should mark them `failed`. _(Separate from the job-resilience spec.)_
- _(2026-06-04)_ Surface the new `homework_jobs.notion_validation` result (verified / mismatch / archive-incomplete) in the operator console — queryable in DB only for v1. (Follow-on to the notion-archive-validator spec.)
- _(2026-06-02)_ Notion anchor **auto-resolve** (ties to Phase 2): resolve the subject-page ID by crawling (grade → `{N}-sinf` → child matching the subject label) instead of the hand-maintained `NOTION_SUBJECT_PAGES`. Would eliminate the **silent per-subject skip** (Kimyo incident). Surface unmapped skips in the UI/job result either way.
- _(2026-06-02)_ Orphan-reclaim window hardcoded `job_timeout × 2` (`worker.py:235`, ~1 hr). **→ DESIGNED in** `docs/superpowers/specs/2026-06-03-job-resilience-resume-failover-design.md` (`reclaim_stale_seconds` + startup sweep resets orphaned `running` jobs). Awaiting build.

### Frontend

- _(2026-06-02)_ Upload-form intro copy (`web/src/routes/upload.tsx:66`) still says "classifies the lesson you choose" — classify was removed; reword.

### Database / Persistence

> Catalogue of data-model / persistence / job-lifecycle issues. Scope = schema shape,
> column semantics, row lifecycle, data integrity, and how persisted state is (or isn't)
> used on retry/resume. NOTE: items here may have their *fix* in pipeline/worker code
> even when the *symptom* is about persisted state — tag each with where the fix lives.

- _(2026-06-03)_ **No phase-level resume on job retry** (fix lives in **`pipeline.py`**, NOT a schema change — the schema is already sufficient). When a job is retried/reclaimed (e.g. worker died mid-job on a session/throttle limit), the scheduler rebuilds `pending = set(content_phases)` (`pipeline.py:351`) with **no filter for already-`done` phases**, and `create_or_reset` (`pipeline.py:468`) **wipes each existing phase row** (clears `output_md`, status→pending). So all content phases regenerate from scratch — a job that completed 8/9 phases and only lost `reflection` re-runs all 8 good phases (~16 min + budget) to recover the one missing phase. **Only `extract` is reused** (cross-job cache, `find_latest_extract`). The DB is fine — `phase_outputs` already persists per-phase `output_md` + `status`, exactly what a resume needs; the gap is that the pipeline doesn't *read* prior `done` rows and skip them. **→ DESIGNED, awaiting build:** full solution speced in `docs/superpowers/specs/2026-06-03-job-resilience-resume-failover-design.md` (phase resume + provider failover policy-b + faster reclaim + `phase_outputs.provider` attribution; failover order `[codex,gemini,kimi,opencode]`, claude reserved-for-user). Ties to autonomy reliability ([[PRODUCTION_AUTONOMOUS_GENERATION]] §3). Next step: writing-plans → execution.

## Done / promoted

- **Backlog audit 2026-06-03 — removed as STALE** (Effort A md-per-phase flip made them moot, verified by code check): memory-check `why_prompt`-not-schema-enforced + `MemoryCheckKind` 3-vs-6 question types (schema `app/schemas/memory_check.py` **deleted**); `api-2` `source_map_ready` / `concept_fidelity_warning` event schemas (**source map removed**); source-fidelity detect-only invented-`concept_id` warning (`_unknown_concept_ids` **removed**); `mistake_provenance` (`source`|`inferred`) tag on the CBP common-mistake (was a deleted schema field, **not** preserved in prompt form). None of these reference live code anymore.
- **Boss Arena 3-tier hint ladder — ✅ already implemented as prompt content** (`prompts/_general/boss-arena.md:25-28`: Hint 1 → Why, Hint 2 → How, Hint 3 → synthesis, never the answer). The schema (`boss_arena.py`) is gone but the ladder lives in the prompt.
- **Frontend trio — ✅ FIXED 2026-06-02 (Nggaev-v2, commit `e582f53`; tsc + vite build clean. Pending: user browser-verify).** (1) `frontend-6` — `job.tsx` `DonePanel` now counts the v2 columns (source concepts · case checkpoints · memory checks · Boss Arena questions w/ legacy final-challenge fallback · practice-arc games), so v2 jobs show real done-stats. (2) **Grade input** — optional Grade select (1–11) added to `upload.tsx`, wired `api.uploadBook → POST /books` `grade` field (no more SQL for new uploads). (3) `<Select>` uncontrolled→controlled — model picker `section.tsx` now `value={model ?? ""}`. _(Note: DonePanel was later rewritten in Effort A to count phases/warnings instead of `*_json` columns.)_
- W1 — `opencode` as 5th CLI provider — ✅ DONE 2026-05-29 (commit 8a96435), see [[MASTER_MEMORY]] §0010. (Verification follow-up moved to Open above.)
- ~~Stale `main` test files break `pytest tests/`~~ — **REMOVED 2026-06-01 (stale/not-applicable).** Those files (`test_flows.py`, `test_providers_registry.py`, `test_agent_models.py`, `test_api.py`) don't exist in our tree — they were the audit machine's untracked locals.
