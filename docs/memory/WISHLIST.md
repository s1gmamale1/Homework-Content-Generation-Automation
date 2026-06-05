# Project Wishlist — capture inbox

> Raw capture of bugs / issues / ideas — **just the idea itself**, one line each.
> No analysis here. When an item is understood (root cause + deliverable), promote it
> to [ROADMAP.md](./ROADMAP.md) and remove it from this list.
> Tracked in git (committed alongside the work).

## Open

> Audited against current code **2026-06-04** (post job-resilience [0031] + lesson-matching [0032]). Items that referenced now-deleted code (structured schemas, source-map, source-fidelity) were removed as moot — see **Done / promoted**. Worked-up items live in [ROADMAP.md](./ROADMAP.md): **R9** (Notion SVG/tables→escaped text), **R10** (broken-font PDF → near-empty TOC), **R11** (provider failover not legibly recorded). Larger planned work (WS5 full Uzbek contract; Notion **Phase 2 pull**) tracked in `next-steps-flow-v2` memory. NOTE: a few inline `pipeline.py:NNN` line-refs below predate the resilience resume code growing `run()` — treat them as approximate.

- `fetch-1`: **Fetch From Notion >20MB ceiling hits ~43% of textbooks** (20/47 supported-subject books exceed 20MB; one grade-9 Jahon tarixi is 497MB). Today they're rejected with a clear message and no book row — but it's the top product gap. Deliverable: **subset-TOC** (extract just the picked lesson's pages, like R2's `_subset_pdf` but as a pre-extract shrink) or auto-downscale, so big books are still fetchable. Quantified live in worklog [0033].
- `fetch-2`: Fetch From Notion takes the **first PDF in page order** when a subject page has multiple attachments (e.g. "Student book + workbook") — prefer the textbook (`darslik`) over the workbook (`ish daftari`). Low priority. (worklog [0033])
- `fe-redesign`: **Operator console (`web/`) full redesign — DEFERRED mid-brainstorm 2026-06-05.** Driver: looks dated/generic + clunky + missing things + unfinished. Agreed **3-slice sequencing** (each ships working, own spec→plan cycle): **Slice 1 Foundation** = delete dead student-play code (`components/boss-fight, games/, flashcards, memory-sprint, reading` — none routed) + establish design language + clean component kit; **Slice 2 IA/workflow** = restructure nav + upload→generate→monitor→review flow, job/queue visibility; **Slice 3 capabilities** = batch generation, live queue dashboard, Notion archive/validation status. **Start with Slice 1.** Aesthetic direction explored & user said "sounds good": **"Quiet Precision"** — editorial-clean, content-first, real material depth; *Newsreader* serif (display) + *Hanken Grotesk* (UI) + *JetBrains Mono* (metadata); restrained bronze/amber accent (keeps brand); the **live-generation hero** (9-phase pipeline progress) as the centerpiece for the "lose track of what's running" pain. Light-vs-dark NOT yet chosen. **Crafted mockup preserved → `docs/design/2026-06-05-console-redesign-quiet-precision.html`** (open in a browser — light & dark, same Library screen). Resume = re-enter brainstorming on Slice 1 from this mockup.

### Effort B — faithful Infra-spec prompt rewrite — ✅ SHIPPED (worklog [0030], 2026-06-04)

> Done: **Option A** `{{FAMILY_RULES}}` token (family-of-4, not subject-of-7); CBP + Flashcards rewritten family-aware (**humanities CBP authored** — no source spec); light/compact/polish passes on the other 9 phases; all folded-in fixes (memory-check **≥2-of-3**, flashcards **in-prompt enum** since `FlashcardType` is deleted, reflection **`#` title**). Live-verified on real gemini history output (validator-clean, 0 dead-vocab). Full detail in worklog [0030]; spec `0dbb34a`, plan `61dc6c6`.
>
> **Still pending (the follow-on, NOT shipped):** the **WS5 Uzbek language contract**. ~~per-phase `phase_validator.RULES`~~ is **SUPERSEDED** — the LLM-phase-validator design (`docs/superpowers/specs/2026-06-04-llm-phase-validator-design.md`) *retires the deterministic rule engine entirely* (its plan deletes `phase_validator.py`) in favour of a self-verifying LLM judge. Do NOT hand-author `RULES`; that direction is dead.

### Backend

**API / events / dead code**
- `api-3`: the classify-era cluster is **dead but still present** — `difficulty_classified` event (emitted `jobs.py:254`), `DifficultyClassifiedEvent` (`events.py:33`), `set_difficulty` (`jobs.py:134`), `JobOut.difficulty` (`job.py:30`) + `homework_jobs.difficulty` column. Classify was removed; difficulty is pinned `None` in the pipeline. Remove the lot (or keep the column nullable if cheaper than a migration).
- `flow-1`: `pipeline.py:428` "Phase scheduler stuck" diagnostic embeds the dep dict as a literal double-brace f-string (`{{p: ...}}`) → renders as static text, never evaluated. Cosmetic, unreachable path.

**Robustness / data / features**
- Scanned / image-only PDFs: TOC extraction unsupported (only text PDFs decode; image pages rely on gemini native read). Sibling of R10. (`toc_extractor.py` unchanged.)
- `opencode` is **too flaky to be a *primary* provider** — live-run 2026-06-04 (job `6a760767`, §0031): hung the full **600s** per-attempt timeout on **every** failover wave, blowing the 1800s job budget → failover+resume rescued it but burned 2 attempts (~35 min wall). Keep as last-resort fallback ONLY; never *request* it. Consider a shorter per-attempt timeout for known-flaky providers (the failover chain + resume already handle it gracefully). (Was: "never run against a real install" — now run, and it hangs.)
- Bad book data: math-algebra book `9e7833bc…` has a 4 KB stub PDF (not a real textbook) — clean up or replace.
- Confirm the English grade→CEFR ladder against the official Uzbek curriculum — needs curriculum-owner sign-off (external).
- _(2026-06-02)_ SSE teardown noise: `/toc/stream` (`books.py:113-150`) logs a benign asyncpg / sse-starlette `CancelledError` when an `EventSource` closes; request still 200 — cosmetic. Guard the cancel on session teardown.
- _(2026-06-02)_ Stale `pending` jobs with `attempts == max_attempts` (e.g. `2848dbcb`) never claimed → stuck `pending` forever (claim query skips attempts-exhausted rows but never fails them). A startup/periodic sweep should mark them `failed`. _(Separate from the job-resilience spec.)_
- _(2026-06-05, DEFERRED — not now, not soon)_ **Notion archive validator** — fully spec'd (`78b0c73`, `docs/superpowers/specs/2026-06-04-notion-archive-validator-design.md`) + TDD-planned (`9aad1ae`, `…/plans/2026-06-04-notion-archive-validator-plan.md`) but **NOT executed**. Auto, best-effort structural check that the live Notion tree matches `_HOMEWORK_LAYOUT`/`PHASE_TITLES` after `archive_job`, recording `homework_jobs.notion_validation` (verified/mismatch/archive-incomplete/skipped). Parked by decision; revisit only if archive correctness becomes a real pain.
- _(2026-06-04)_ Surface the new `homework_jobs.notion_validation` result (verified / mismatch / archive-incomplete) in the operator console — queryable in DB only for v1. (Follow-on to the deferred notion-archive-validator above.)
- _(2026-06-04)_ Phase judge writes `"judge-unavailable: <ExcType>"` into `phase_outputs.validation_warnings` on CLI/parse failure — an *infra* signal that renders in the console like a *content* defect. Distinguish them (separate field, or a prefix the console styles differently) so operators don't chase phantom content issues. (Code-review nit from the LLM-phase-validator build.)
- _(2026-06-02)_ Notion anchor **auto-resolve** (ties to Phase 2): resolve the subject-page ID by crawling (grade → `{N}-sinf` → child matching the subject label) instead of the hand-maintained `NOTION_SUBJECT_PAGES`. Would eliminate the **silent per-subject skip** (Kimyo incident). Surface unmapped skips in the UI/job result either way.

### Frontend

- _(2026-06-02)_ Upload-form intro copy (`web/src/routes/upload.tsx:66`) still says "classifies the lesson you choose" — classify was removed; reword.

### Database / Persistence

> Catalogue of data-model / persistence / job-lifecycle issues. Scope = schema shape,
> column semantics, row lifecycle, data integrity, and how persisted state is (or isn't)
> used on retry/resume. NOTE: items here may have their *fix* in pipeline/worker code
> even when the *symptom* is about persisted state — tag each with where the fix lives.

- _(none open)_ — phase-level resume shipped in [0031]; see **Done / promoted**.

## Done / promoted

- **Phase-level resume + faster orphan-reclaim — ✅ SHIPPED (worklog [0031], 2026-06-04).** Both former Open items closed by the job-resilience effort: (1) "No phase-level resume on job retry" — the pipeline now reads prior `done` rows and skips them (`pipeline.py` `_done_phase_md`/`_pending_phases`, always-on; `force`=new job, `/retry`=resume). (2) "Orphan-reclaim window hardcoded `job_timeout × 2`" — replaced by `reclaim_stale_seconds` lease-TTL (heartbeat-gated) + startup orphan-reset of `running` jobs. Also delivered: per-phase provider failover (`_run_with_failover`, classifier, claude reserved-for-user) + `phase_outputs.provider` attribution. Live-verified (job `6a760767`). Follow-on observability gap tracked as **R11** in ROADMAP.
- **Backlog audit 2026-06-03 — removed as STALE** (Effort A md-per-phase flip made them moot, verified by code check): memory-check `why_prompt`-not-schema-enforced + `MemoryCheckKind` 3-vs-6 question types (schema `app/schemas/memory_check.py` **deleted**); `api-2` `source_map_ready` / `concept_fidelity_warning` event schemas (**source map removed**); source-fidelity detect-only invented-`concept_id` warning (`_unknown_concept_ids` **removed**); `mistake_provenance` (`source`|`inferred`) tag on the CBP common-mistake (was a deleted schema field, **not** preserved in prompt form). None of these reference live code anymore.
- **Boss Arena 3-tier hint ladder — ✅ already implemented as prompt content** (`prompts/_general/boss-arena.md:25-28`: Hint 1 → Why, Hint 2 → How, Hint 3 → synthesis, never the answer). The schema (`boss_arena.py`) is gone but the ladder lives in the prompt.
- **Frontend trio — ✅ FIXED 2026-06-02 (Nggaev-v2, commit `e582f53`; tsc + vite build clean. Pending: user browser-verify).** (1) `frontend-6` — `job.tsx` `DonePanel` now counts the v2 columns (source concepts · case checkpoints · memory checks · Boss Arena questions w/ legacy final-challenge fallback · practice-arc games), so v2 jobs show real done-stats. (2) **Grade input** — optional Grade select (1–11) added to `upload.tsx`, wired `api.uploadBook → POST /books` `grade` field (no more SQL for new uploads). (3) `<Select>` uncontrolled→controlled — model picker `section.tsx` now `value={model ?? ""}`. _(Note: DonePanel was later rewritten in Effort A to count phases/warnings instead of `*_json` columns.)_
- W1 — `opencode` as 5th CLI provider — ✅ DONE 2026-05-29 (commit 8a96435), see [[MASTER_MEMORY]] §0010. (Verification follow-up moved to Open above.)
- ~~Stale `main` test files break `pytest tests/`~~ — **REMOVED 2026-06-01 (stale/not-applicable).** Those files (`test_flows.py`, `test_providers_registry.py`, `test_agent_models.py`, `test_api.py`) don't exist in our tree — they were the audit machine's untracked locals.
