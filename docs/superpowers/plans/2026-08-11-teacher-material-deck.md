# Teacher Material — structured lesson-plan deck ("Oʻqituvchi konspekti")

Status: **DRAFT — awaiting single approval gate.** No code until approved. (Rev 2 — folds in the Fable adversarial review: 1 Critical, 8 Important, 5 Minor findings.)

## Approach & key decisions

**What we're building.** A new per-lesson deliverable: a structured teacher lesson-plan deck (cover, lesson passport, Bloom objectives, core idea, 45-minute timed lesson map, 7 stage-by-stage teacher/student scripts incl. the on-screen hook + the video choreography, a 5-question quiz + teacher-only answer key, pair work, conclusion, and a 10-point rubric — rendered as 18 slides, the quiz expanding to 5). Generated from the **cached lesson `extract`** (verified against the real 11-sinf Jahon tarixi textbook, topic 19 — every template fact traces to textbook pp. 85–89), NOT from the student homework. The model supplies only the *pedagogy layer* on top of the extract's facts.

**Chosen approach (all locked with the user):**
- **Source = the cached `extract`.** Reuses the not-kind-aware extract cache (`pipeline.py:1602-1641`; key `(toc_entry_id, prompt_hash, provider, model)`, transport excluded) — a teacher-material job reuses a lesson's extract if homework already ran, else computes it (extract head phase).
- **Placement = the existing pipeline**, as a **new job kind** (`homework_jobs.kind`), not a parallel table — the whole pipeline is keyed on `homework_jobs.id`.
- **Trigger = a "Teacher material" mode on the Fleet launcher** producing `kind="teacher_material"` jobs into their **own batch** (batches unique key widened with `kind`, mirroring the migration-0038 precedent). Never bundled into a homework launch. One topic or many (reuses the launcher's book+TOC picker unchanged).
- **Generation = ONE coherent structured pass** (Option A): the `teacher-deck` phase produces the entire deck object in a single schema-validated call, so the core idea threads through hook → anchor points → quiz → conclusion. Stored as `phase_outputs.content_json` (JSONB lane from migration 0050), `authoring_mode="structured"`. **Uses the structured-content resilience wrapper** (per-attempt timeout, `SlotSaturation` parking, session-limit pause, same-provider retry) — NOT a bare `run_phase` — and **does not** call `artifact_from_config` (no teacher-deck markdown renderer exists → would raise `StructuredPhaseError`).
- **Quality gate = schema mechanics + one factual-fidelity pass, regen once.** Pydantic validators enforce mechanics (4 options / exactly one correct, answer key covers all quiz items, lesson-map minutes sum to 45, rubric points sum to total). Factual fidelity reuses `phase_judge.judge(lesson_context=<extract>)` on a **serialized text view** of the deck's claims, driven by a **purpose-written fidelity contract passed via `contract_override=`** (NOT the JSON-authoring prompt, and NOT the default `get_prompt` lookup — which cannot find a `structured/` file and would silently return `available=False`). On `has_major`, regenerate the whole deck once feeding the feedback (mirror `pipeline.py:1878-1944`), fail-open keeping the original + warnings; api-auth errors re-raise.
- **Output = structured → fixed slide renderer.** The FE owns the design: a `/deck/:id` route renders dedicated slide components from `content_json` (not markdown). PDF via `window.print()` + a `@media print` stylesheet (self-contained, no new deps).
- **Video = referenced, not generated.** A `video_ref` slot; the video *choreography* (before/during/after + the observation task, tailored to real names/dates from the extract) is generated into the Stage-3 fields.
- **Model/transport = the operator's per-job `provider`/`model`/`transport`** (like content phases). Extract head stays pinned to the `launch_defaults` extract role. `output_language` follows the book (uz/ru).
- **Cross-kind isolation is a first-class concern.** A new `kind` must not silently corrupt homework read paths. Every place that reads "the latest job for a lesson" or "the book's batch" must be kind-scoped: batch adoption/resume/preview, the batch rollup payload + FE batch-identity, the subject-coverage dashboard, and the Notion auto-archive hook.

**Rejected alternatives:** derive-from-homework; a parallel `teacher_decks` table; a per-section DAG; model-emits-HTML; jsPDF/html2canvas. (Rationale in the conversation; all superseded by the decisions above.)

**Load-bearing facts (verified against code across three exploration passes + one adversarial review — no stale anchors found):**
- `homework_jobs` has **no** `kind`/`flow`/`type` column (`app/models/homework_job.py`); worker claim is job-column-based (`claim_next_job`) and **kind-inert** — no capability change needed.
- `flow_for` (`app/services/flows.py:43-46`) is one fixed sequence; pipeline builds `sequence = ["extract", *content_planned]` at `pipeline.py:342-348`; head-extract (`:384-450`) + DAG scheduler (`:804-990`) are kind-agnostic.
- `phase_outputs.content_json` JSONB + `authoring_mode`/`content_schema_version`/`renderer_version` exist (migration `0050`; model `app/models/phase_output.py:53-56`); uniqueness `(job_id, phase_order)` (`:64`) — extract at order 0, teacher-deck at order 1.
- `agent.run_phase(..., schema=Model)` (`app/services/agent.py:944`) → `result.parsed`; raises `SchemaValidationExhausted` after 2 attempts. But calling it **directly drops the resilience layer** (per `_run_structured_attempt` docstring `pipeline.py:1164-1179`).
- `settings.structured_output_enabled` defaults **False** (`config.py:172`); `_generate_artifact` gates SCHEMAS phases on it and **falls back to markdown** (`pipeline.py:1248-1268`). Teacher-deck must bypass both the kill switch and the fallback.
- `SCHEMAS` registry `app/schemas/content_json/__init__.py:5`. Structured prompts `prompts/_general/structured/<phase>.md` via `get_structured_prompt(subject, phase, ...)`, which substitutes **`{{SUBJECT}}` and `{{LANGUAGE_RULES}}`** (`prompts.py:515-526,555-559`) — there is **no `{{OUTPUT_LANGUAGE}}` token**. `get_prompt`'s non-recursive glob **excludes** `structured/` (`prompts.py:550-553`); a missing contract raises `KeyError` (`:511`).
- `phase_judge.judge(...)` (`app/services/phase_judge.py:201`) has `contract_override=` (`:215`); `_FIDELITY_RULE` treats `lesson_context` as ground truth → `has_major`; broad `except` → `available=False` on any contract error (`:250`); api-auth re-raises (`:255-257`). Regen-once loop `pipeline.py:1878-1944`. Real phase-exec fn is **`_execute_one_phase`** (`pipeline.py:653`); `job.kind` must be captured in the first session block (~`:253`) before the ORM object detaches.
- Launch: `launch_batch` `app/api/v1/batch.py:137`, body `:71-96`, job-create `:406-420`; it also calls `latest_for_section` (`:373`) + `reset_for_retry(...)` (`:401`) and a preview loop (`:293`) — **none kind-scoped today**; `custom_prompts`/`selected_phases` validated against `flow_for(book.subject)` (`:205-226`). `batches` unique key `(book_id, transport, output_language)` (`app/models/batch.py:58-60`); `_rollup_payload` emits no `kind` (`:99-133`); `rollup_for_batch` is batch-member-scoped (`batches.py:91-94`) — one-phase rollup works.
- Read paths that need kind-scoping: `subject_coverage.job_status_by_book` `DISTINCT ON (book_id, toc_entry_id)` **no kind filter** (`app/repositories/subject_coverage.py:44-69`); Notion auto-archive at `pipeline.py:564` + `notion_archive.py:402-404` (teacher jobs have no `output_md` phase → permanent skip noise; **but nothing can be pushed** — no `_PAGES` entry — so no student-page corruption). `scripts/export_homeworks.py` (`WHERE j.status='done'`) is **untracked / not ours — do NOT modify; flag only**.
- FE: `launchBody` `web/src/components/fleet/launcher.tsx:1095-1120`; batch identity resolved by transport alone (`~:1085`, `batchedTransports.has(transport)`); `api.launchBatch` `web/src/lib/api.ts:425-439`; deliverable view `web/src/routes/preview.tsx` (`PhasesPreview:143-323`, `MD_COMPONENTS:19-80`); routing `App.tsx:54-68`; **no PDF/print code anywhere**. Alembic heads at **0053**.

## Global Constraints

- **`transport=api` is the production path.** The acceptance smoke MUST run `transport=api`, in-process, no server. CLI smoke doesn't count.
- **Zero production/Notion writes during dev.** Tests use fake clients + a **scratch DB** with an explicit `DATABASE_URL` overriding `../.env` (production `edu_copy`). Never `RUN_DB_INTEGRATION` against the production `.env`. The acceptance smoke may **read** `edu_copy` for the cached extract text but **persists nothing** to it.
- **Stage only the files each task lists**; never `git add -A`. Hard branch guard before every commit; dedicated worktree.
- **`_resolve_model("gemini", None) is None`** invariant untouched; no cross-provider defaults; no hardcoded model names.
- **Facts come only from `lesson_context`.** The deck prompt forbids inventing dates/numbers/names; teaching/exercise numbers exempt from fidelity flagging (mirror `_FIDELITY_RULE`).
- **`kind` defaults to `'homework'`** everywhere (column default, request default, repo default) so every existing path is behavior-preserving. Every new read-path guard must leave homework behavior byte-identical.
- **No spawn env outside `agent._auth_env`.**
- **TDD per task, commit per task.** RED first (prove it fails), then GREEN, commit only that task's files. Controller re-reads the diff AND re-runs the tests for every commit.

## Schema sketch (Task 4 authors the real thing; fixture from the committed template transcription)

`TeacherDeck` (Pydantic, `app/schemas/content_json/teacher_deck.py`), field **keys English, values in the book language**:
- `meta`: `subject_label, grade, topic_number, topic_title, duration_min(=45), lesson_type, method: list[str], materials: list[str], video_ref: str|None`
- `passport`: `fan_sinf, mavzu, dars_turi, metod, kerakli_vosita, baholash`
- `objectives`: `{bilib_oladi, qila_oladi, tushunadi}`
- `core_idea`: `{statement, elaboration}`
- `lesson_map`: `list[{index, title, description, minutes}]` — validator: `sum(minutes) == meta.duration_min`
- `stages`: `list[{index, title, minutes, badge: 'ekranga'|'teacher_only'|'none', points: list[{title, detail}], teacher_action, student_action, screen_text: str|None}]` — **the hook is stage 2's `screen_text`; the video before/during/after + observation task are stage 3's `points`/`teacher_action`/`student_action`**
- `quiz`: `list[{number, question, options: list[{label:'A'|'B'|'C'|'D', text}], correct_label, hint}]` — validators: exactly 4 options, labels A–D unique, `correct_label` in options
- `answer_key`: `list[{number, correct_label, explanation}]` — validator: number set == quiz number set, correct_label matches quiz
- `pair_work`: `{intro, tasks: list[{title, prompt}]}`
- `conclusion`: `{questions: list[str]}`
- `rubric`: `{components: list[{points, title, detail}], total, bands: list[{range, grade}]}` — validator: `sum(points) == total`

---

## Tasks

TDD (RED → GREEN → commit). Scratch DB per task that needs it: pin `DATABASE_URL=postgresql://macmini5@127.0.0.1:5432/edu_scratch_teacherdeck` explicitly; canonical bar is unit tests that run **without** `RUN_DB_INTEGRATION`. **High-risk tasks get an adversarial review in SDD: 4, 6, 7, 11, 12.**

### Task 1 — DB: `kind` on jobs + batches, widened batch key
- **Files:** `alembic/versions/00XX_teacher_material_kind.py` (new — number after `uv run alembic heads`; heads are at 0053, do NOT guess), `app/models/homework_job.py`, `app/models/batch.py`.
- **RED:** `tests/models/test_teacher_material_kind.py` — `HomeworkJob.kind` defaults `'homework'`, CHECK `IN ('homework','teacher_material')`; `Batch.kind` exists and the unique constraint is `(book_id, transport, output_language, kind)` renamed `uq_batches_book_id_transport_output_language_kind`.
- **GREEN:** `kind = Column(String(32), nullable=False, server_default="homework")` on both + CHECK on jobs; migration mirrors 0050's `add_column` + 0038's unique-key widen (drop+recreate). Existing rows backfill via `server_default`.
- **Verify:** `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` clean on scratch DB; `uv run python -m pytest tests/models/test_teacher_material_kind.py -q`.
- **Commit:** `feat(db): add job/batch kind discriminator for teacher material`

### Task 2 — Repos: thread `kind` + kind-scope ALL section read paths
- **Files:** `app/repositories/jobs.py`, `app/repositories/batches.py`.
- **RED:** `tests/repositories/test_kind_threading.py` — `jobs_repo.create(..., kind="teacher_material")` persists; default → `'homework'`; `get_or_create_for_book(..., kind=)` returns a **distinct** batch per kind on the same book/transport/language; **`find_active_for_section`, `latest_for_section`, and any adoption/reset helper all filter by `kind`** (a homework job is neither adopted nor resumed for a teacher launch, and vice versa).
- **GREEN:** add `kind` param (default `'homework'`) to `create`, `get_or_create_for_book` (conflict target includes `kind`), `find_active_for_section`, `latest_for_section`, and the reset/adopt query.
- **Verify:** `uv run python -m pytest tests/repositories/test_kind_threading.py -q`.
- **Commit:** `feat(repos): kind-scoped job/batch creation, adoption, and latest-lookup`

### Task 3 — Flow: `teacher_material_flow_for` + pipeline sequence branch
- **Files:** `app/services/flows.py`, `app/services/pipeline.py`.
- **RED:** `tests/services/test_teacher_flow.py` — `flows.teacher_material_flow_for(subject) == ["teacher-deck"]`; **`"teacher-deck"` is NOT added to `PHASE_DEPS`** (extract flows via `lesson_context`, not `prior_outputs`; absent = no deps); a `kind="teacher_material"` job builds `sequence == ["extract", "teacher-deck"]`.
- **GREEN:** add `teacher_material_flow_for`; at `pipeline.py:342-348` branch `full_flow = teacher_material_flow_for(subject) if job.kind == "teacher_material" else flow_for(subject)`. Do not touch `PHASE_DEPS`.
- **Verify:** `uv run python -m pytest tests/services/test_teacher_flow.py -q`.
- **Commit:** `feat(flows): teacher-material single-phase flow`

### Task 4 — Schema + committed template transcription
- **Files:** `app/schemas/content_json/teacher_deck.py` (new), `app/schemas/content_json/__init__.py`, `tests/fixtures/teacher_deck/hindiston_topic19.json` (new — a faithful transcription of the 18-slide template deck, the committed source of truth for fixtures + the smoke's fact spot-checks).
- **RED:** `tests/schemas/test_teacher_deck_schema.py` — the committed fixture validates; each mechanical rule rejects: minutes ≠ 45; quiz item with ≠4 options; `correct_label` not among options; answer-key number set ≠ quiz set; rubric points ≠ total; hook (stage-2 `screen_text`) and video choreography (stage-3 `points`) round-trip.
- **GREEN:** author the schema per the sketch with validators; register `"teacher-deck": TeacherDeck` in `SCHEMAS`; transcribe the template into the fixture.
- **Verify:** `uv run python -m pytest tests/schemas/test_teacher_deck_schema.py -q`.
- **Commit:** `feat(schemas): TeacherDeck model + template fixture`

### Task 5 — Prompts: structured authoring prompt + fidelity judge contract
- **Files:** `prompts/_general/structured/teacher-deck.md` (new — authoring), and the fidelity contract (new — either `prompts/_general/structured/teacher-deck.fidelity.md` loaded explicitly, or a module constant in `app/services/teacher_deck.py`; it is passed to the judge via `contract_override`, never via `get_prompt`).
- **RED:** `tests/prompts/test_teacher_deck_prompt.py` — `get_structured_prompt("history", "teacher-deck")` returns non-None, resolves `{{SUBJECT}}`/`{{LANGUAGE_RULES}}` (NOT `{{OUTPUT_LANGUAGE}}`), contains the facts-only-from-context directive + the 45-min / 7-stage / 5-question structure; the fidelity contract loader returns text that instructs grading a **serialized deck (plain text, not JSON)** for factual fidelity against lesson_context and explicitly says teaching/exercise numbers are not defects.
- **GREEN:** write both. The fidelity contract must never demand JSON (else it majors "output isn't JSON").
- **Verify:** `uv run python -m pytest tests/prompts/test_teacher_deck_prompt.py -q`.
- **Commit:** `feat(prompts): teacher-deck authoring prompt + fidelity contract`

### Task 6 — Pipeline: teacher-deck generation (resilient, structured, stored as content_json)
- **Files:** `app/services/pipeline.py` (+ helper in `app/services/teacher_deck.py` if needed).
- **RED:** `tests/services/test_teacher_deck_generation.py` — with the resilient generate faked to return a valid `TeacherDeck`, executing `teacher-deck` writes `content_json` (the deck dict) with `authoring_mode="structured"` + stamped `content_schema_version`, using `job.provider/model/transport` + `lesson_context=<extract>`; it does **NOT** call `artifact_from_config`; and **it generates structured even though `settings.structured_output_enabled is False`** (assert the flag is False in the test and structured output still lands). On `SchemaValidationExhausted`, the phase fails loudly (no markdown fallback).
- **GREEN:** in `_execute_one_phase`, branch `phase_name == "teacher-deck"`: call the **same resilience wrapper the structured content lane uses** (per-attempt timeout / `SlotSaturation` parking / session-limit pause / same-provider retry — the layer above `run_phase`) around `run_phase(schema=TeacherDeck, phase_prompt=get_structured_prompt(...), lesson_context=<extract>)`, bypassing the `structured_output_enabled` gate and the markdown fallback; persist `result.parsed.model_dump()` via `phase_repo.set_status(..., content_json=...)`. Capture `job.kind` at `pipeline.py:~253`.
- **Verify:** `uv run python -m pytest tests/services/test_teacher_deck_generation.py -q`.
- **Commit:** `feat(pipeline): resilient structured teacher-deck generation`

### Task 7 — Pipeline: factual-fidelity check + regen-once (via contract_override)
- **Files:** `app/services/pipeline.py`, `app/services/teacher_deck.py` (serializer).
- **RED:** `tests/services/test_teacher_deck_fidelity.py` — **on the happy path `outcome.available is True`** (guards the dead-gate defect); a deck whose answer-key date contradicts the extract → `has_major` → exactly one regeneration feeding feedback; a clean deck → zero regens; a regen failure keeps the original + warnings (fail-open); an api-auth error re-raises.
- **GREEN:** serialize the deck's factual claims (objectives, core idea, stage points, quiz Q+options, answer-key explanations) to plain text; `phase_judge.judge(subject, phase_name="teacher-deck", output_md=<serialized>, lesson_context=<extract>, contract_override=<fidelity contract>, transport=job.transport, ...)`; on `outcome.available and outcome.has_major`, regenerate the whole deck once with `feedback` appended, re-judge, keep-original-on-failure — mirror `pipeline.py:1878-1944`; bound by `settings.max_judge_regens`; wrap in `_judge_with_timeout`.
- **Verify:** `uv run python -m pytest tests/services/test_teacher_deck_fidelity.py -q`.
- **Commit:** `feat(pipeline): teacher-deck fidelity gate with regen-once`

### Task 8 — API: launch `kind` + validation + kind-scoped resume + deck fetch + rollup kind
- **Files:** `app/api/v1/batch.py`, the jobs/deck route module.
- **RED:** `tests/api/test_teacher_material_api.py` — `POST /jobs/batch` with `kind="teacher_material"` creates teacher jobs in a **distinct** batch; default still creates homework; a teacher launch does **not** resume/adopt a lesson's homework job (covers `latest_for_section` + the preview path + `reset_for_retry`); `custom_prompts`/`selected_phases` with `kind="teacher_material"` → **400**; `GET /jobs/{id}/deck` returns `content_json` (404/409 when absent); the rollup payload carries `kind`.
- **GREEN:** add `kind: str = "homework"` to `BatchLaunchRequest` (validate `IN {homework, teacher_material}`); thread into `get_or_create_for_book` + `jobs_repo.create` (`:406-420`); make the adopt/resume/preview loop kind-scoped; 400 on custom_prompts/selected_phases for teacher_material; skip the `flow_for(book.subject)` phase validation for teacher_material; add `kind` to `_rollup_payload`; add `GET /api/v1/jobs/{id}/deck`.
- **Verify:** `uv run python -m pytest tests/api/test_teacher_material_api.py -q`.
- **Commit:** `feat(api): teacher-material launch, kind-scoped resume, deck fetch`

### Task 9 — Cross-kind read-path guards (dashboard + Notion archive)
- **Files:** `app/repositories/subject_coverage.py`, `app/services/pipeline.py` (archive hook `:564`), possibly `app/services/notion_archive.py`.
- **RED:** `tests/services/test_kind_readpath_guards.py` — `subject_coverage.job_status_by_book` ignores teacher-material jobs (a teacher job created after a homework job does NOT replace the lesson's homework status in the coverage dashboard); the auto-archive hook **skips** `kind="teacher_material"` jobs (no permanent `notion_skip_reason` noise; teacher batches not counted `unarchived`).
- **GREEN:** add `kind='homework'` filter to the coverage `DISTINCT ON` query; guard the archive hook to no-op (or mark archived-N/A) for teacher jobs. Add a plan/worklog note that `scripts/export_homeworks.py` (not ours) also lacks a kind filter — **flag to the user, do not modify**.
- **Verify:** `uv run python -m pytest tests/services/test_kind_readpath_guards.py -q`.
- **Commit:** `feat(guards): kind-scope coverage dashboard and skip teacher-job archive`

### Task 10 — FE types + api client
- **Files:** `web/src/lib/types.ts`, `web/src/lib/api.ts`.
- **GREEN:** `TeacherDeck` TS type mirroring the Pydantic schema; `kind` on the launch payload; `kind` on `BatchSummary`/rollup; `api.getDeck(jobId)` → `GET /api/v1/jobs/{id}/deck`.
- **Verify:** `cd web && npx tsc -p tsconfig.app.json --noEmit`.
- **Commit:** `feat(web): teacher-deck types + api client + batch kind`

### Task 11 — FE launcher: mode toggle + kind-aware batch identity
- **Files:** `web/src/components/fleet/launcher.tsx`, `web/src/lib/launcher-config.ts`.
- **GREEN:** `launchMode` state in `ReadyCard` (~`:907-925`); toggle in the controls toolbar (~`:1253`) reusing `PRESSABLE`+`FRAME_ON/OFF`; persist via `LauncherConfig` (`:1061-1077`); thread into `launchBody` (`:1095-1120`) as `kind`. **Make batch identity kind-aware** — the "already batched"/rollup resolution (`~:1085`, `batchedTransports`) must not let a teacher batch corrupt the homework card's state (filter by `kind` matching the current mode). Success toast points at the deck viewer for teacher launches. Book+TOC picker unchanged.
- **Verify:** `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`.
- **Commit:** `feat(web): teacher-material launch mode + kind-aware batch identity`

### Task 12 — FE deck viewer route + slide components
- **Files:** `web/src/routes/deck.tsx` (new), `web/src/components/deck/*` (new), `web/src/App.tsx`, `web/src/components/layout.tsx`.
- **GREEN:** register `/deck/:id` in the `!IS_VIEWER` block (`App.tsx:54-68`); pager mirroring `PhasesPreview` with dedicated components: `CoverSlide, PassportSlide, ObjectivesSlide, CoreIdeaSlide, LessonMapSlide, StageSlide, QuizSlide, AnswerKeySlide, PairWorkSlide, ConclusionSlide, RubricSlide` — matching the template (dark cover/hook slides, light content slides, stage accents, two-column teacher/student, EKRANGA vs teacher-only badges; quiz[] expands to 5 slides). Reuse `ui.ts`, `SpaceBackdrop`, `motion.ts`, `PHASE_ACCENTS`. Fetch via `api.getDeck`.
- **Verify:** `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`; visual check vs the template.
- **Commit:** `feat(web): teacher-deck slide viewer`

### Task 13 — FE PDF export via print CSS
- **Files:** `web/src/styles/globals.css`, `web/src/routes/deck.tsx`.
- **GREEN:** `@media print` block — `.deck-slide { break-after: page }`, light backgrounds + `print-color-adjust: exact`, hide `SpaceBackdrop`/nav/footer; render all slides when printing; "Download PDF" button → `window.print()`.
- **Verify:** `cd web && npm run build`; manual print-preview: one slide per page, chrome hidden.
- **Commit:** `feat(web): teacher-deck PDF export via print stylesheet`

### Task 14 — Acceptance: real api generation smoke (n≥3, read-only prod)
- **Files:** `scripts/smoke_teacher_deck.py` (ad-hoc; may stay untracked).
- **What:** in-process, `transport=api`. **Read-only** fetch the cached extract text for **book `03cd6e82-…` (11-sinf Jahon tarixi), topic 19 (Hindiston, pp. 85–89)** from `edu_copy` (superuser, SELECT only) — persist nothing to prod, do not require the migration on prod. Generate the deck in-process against that extract; assert it validates against `TeacherDeck`; spot-check every answer-key date/number/name against the committed fixture facts (Narasimxa Rao; 1992 grain export; IT 2nd / sci-tech 3rd; three NW states 2000; 400 mln poor / ~1/3 illiterate / 1.35 bn); minutes sum to 45. **Run 3×** (n≥1 proves nothing on a stochastic model); report validity, fidelity, and `cost_usd`. Render one run through the viewer to eyeball template fidelity.
- **Verify + report:** capture the 3 runs.
- **Commit:** evidence recorded in the finish worklog.

### Task 15 — Finish
- Full suite green: `uv run python -m pytest tests/ -q`; `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`.
- **Rebase check:** `git fetch origin` → `git log HEAD..origin/Nggaev-v2`; if base moved, rebase onto `origin/Nggaev-v2`, resolve, re-run suite.
- `finishing-a-development-branch` (user decides push/merge; default push to the working branch).
- Docs: worklog in `docs/memory/MASTER_MEMORY.md` + `docs/memory/INDEX.md` row (reserve number at finish, re-check tail); close the ROADMAP item if tracked; `git mv` this plan to `docs/superpowers/plans/shipped/`.
- De-stale: `README.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, `docs/DATABASE.md` (new `kind` columns + widened batch key + the deck deliverable). Note the `scripts/export_homeworks.py` kind-filter gap for the user (not ours to edit).

---

## Review disposition (Fable adversarial pass)
- **Critical #1 (dead fidelity gate)** → Task 5 authors a separate fidelity contract; Task 7 passes it via `contract_override` and RED-asserts `available is True`.
- **Important #2/#3 (dropped resilience / kill-switch fallback)** → Task 6 uses the resilience wrapper, bypasses `structured_output_enabled` + markdown fallback, no `artifact_from_config`; test proves structured while the flag is False.
- **Important #4 (cross-kind resume)** → Task 2/8 kind-scope `latest_for_section`, adopt/reset, and the preview loop.
- **Important #5 (FE batch identity) / #6 (coverage + export) / #7 (Notion archive)** → Tasks 8/9/10/11 add `kind` to rollup + batch identity + coverage query + archive skip; `export_homeworks.py` flagged (not ours).
- **Important #8 (`{{OUTPUT_LANGUAGE}}` doesn't exist)** → Task 5 uses `{{LANGUAGE_RULES}}`.
- **Important #9 (schema coverage + no committed template)** → schema notes hook/video map to stage fields; Task 4 commits the template transcription fixture.
- **Minor #10 (`PHASE_DEPS`) / #11 (`_execute_one_phase`, capture `job.kind`) / #12 (read-only smoke) / #13 (reject custom_prompts) / #14 (conservative claim gating, noted)** → folded into Tasks 3/6/8/14.
- **Verified sound (kept):** kind-on-existing-tables; widened key mirrors 0038; extract-cache reuse kind-agnostic; claim gate needs no change; one-phase rollup works; regen-once mirror; no task-dependency inversions.
