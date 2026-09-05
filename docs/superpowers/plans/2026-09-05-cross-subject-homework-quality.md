# Cross-subject homework quality implementation plan

## Approach & key decisions

The user authorizes analysis, fixes, fresh fleet generation, Notion upload, import through localhost:8095, and test-student checking. The earlier history review supplies concrete defects, not a restriction to history. Shared rules must reach all 26 registered subjects, all three output languages, and both Markdown and structured authoring. Subject-specific patterns are conditional on the lesson's actual concepts and available information. Preserve the existing envelope and importer grammar. Enforce substantive content failures in the generator and stop the importer auto-approving explicit major judge failures. Keep polite language while reducing lower-grade abstraction and redundant writing. Correct the already-verified textbook issues through a scoped, documented lesson correction, not global substitutions. Unit tests prove wiring/state transitions; actual model evaluations and freshly imported homework prove content behavior.

The frontend at :8095 is `_wt-unified-web` (dcd6aba2), proxying to `_wt-unified-backend` at :8090 (9f98285c). Both contain unrelated uncommitted work. The generator baseline is 3412920, isolated here on `fix/cross-subject-homework-quality-20260905`. Its baseline prompt/judge/solver suite passes 50 tests on both Mac and this checkout. API transport is the operational path. Changes must not restart the user-owned generation head or open the mass queue. Only the bounded acceptance jobs are authorized for this run.

> For agentic workers: use subagent-driven-development for implementation tasks, with task-scoped review and controller verification. User authorization covers proceeding from analysis to the described repairs and acceptance run; do not introduce another permission pause.

**Goal:** Repair the evidenced content defects across subjects and prove the repaired path from generation through Notion import to learner use.

**Architecture:** One shared learner-quality contract, conditional subject-family instructions, stronger independent judge/solver review, and explicit rejection of unresolved major defects. Keep existing phase schemas and use the actual platform's import/grade paths.

**Tech stack:** Python 3.14, FastAPI/SQLAlchemy generator, Django platform, React/Vite web, pytest, Playwright, Notion versioned attachments.

**Spec/evidence:** `C:/Users/Pekka/Codex/Sessions/_homework-review/g5-history-18-savdo-yollari/REVIEW-FINDINGS.md`, sibling `PROMPT-CAUSE-ANALYSIS.md`, and `C:/Users/Pekka/Codex/Sessions/_homework-quality/platform-audit-report.md`.

## Global constraints

- All 26 entries in `app/services/subjects.py::REGISTRY` remain supported; Uzbek/Russian/English output and L2 target/scaffolding language rules remain distinct.
- Preserve `hcg-notion-envelope@1`, its digest, ordered phases (optional extract/vocabulary then six learner phases), phase names, answer markers, ten CBP H2 headings, and five RLC step kinds.
- Preserve `rlc_config@1` and `sentence_fill_config@1`, nonblank rendered Markdown, question-first RLC steps, and typed mathematics/chemistry notation.
- Retain lesson facts and qualifiers except the explicitly documented, lesson-scoped correction. Never invent source authors, quotations, data or provenance to make an exercise answerable.
- All necessary evidence and premises must be in student-visible content. Hidden keys, rubrics, metadata and previous student answers are not supplied evidence.
- Unavailable review is not a verified pass. Preserve operational distinctions between infrastructure failure, content rejection, lease loss, cancellation and queue saturation.
- Do not overwrite existing platform edits, edit the frozen `Generated Homeworks` Notion tree, restart the generation head, or resume unrelated/cancelled jobs. Use only `Platform Homeworks` for authorized fresh artifacts.
- Use a dedicated test student/school for assignment checks. Never reveal credentials in reports or logs.
- Stage explicit files. Use the generator's established author identity `molotovgit <molotovgit@users.noreply.github.com>` for its commits.

## Task 1: Shared learner contract and lesson-appropriate tasks

**Files:** create `prompts/_general/_learner-quality.md`; modify `app/services/prompts.py`, the six active `_general` learner Markdown prompts, and structured RLC/sentence prompts where their own conflicting rules need replacement; create `tests/services/test_learner_quality_prompts.py`; update existing prompt tests only for intentionally changed behavior.

**Consumes:** registry, `get_prompt`, `get_structured_prompt`, original review fixtures. **Produces:** resolved prompts carrying one common learner-quality policy across active learner phases, without changing teacher material or dormant phases.

- [ ] Add failing resolver tests over every registered subject and each output language. Assert shared policy inclusion exactly once for learner contracts and structured RLC/sentence; teacher-deck/teacher-pack stay outside the learner policy. Verify provider suffix placement, absence of unresolved template tokens and changed hashes when shared policy changes. These are wiring tests, not claims that a model obeys the policy.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/services/test_learner_quality_prompts.py -q` and capture the expected missing-policy failures.
- [ ] Implement the shared policy with these explicit requirements: teach/test only this lesson; family templates apply only when the method is taught and its prerequisites/data are supplied; show an identified excerpt when asking provenance/purpose, otherwise use labelled lesson information cards; distinguish choosing a useful source/test from observing its result; supply results before conclusions; align each graded component with visible evidence; make every choice/follow-up answerable after any earlier choice; no multiple defensible options, synonyms, equivalent forms, subset answers or style-only cues; preserve approximate/modal/geographic/chronological meaning; define necessary new terms; use grade-appropriate instructions without exposing authoring jargon; reference only present text/data/visuals; require a different situation and use of knowledge in the application than in the preview.
- [ ] Replace the unconditional history author/purpose/three-question override. When an actual source is present and source analysis is taught, retain the source method with metadata and limited conclusions. Otherwise assess the supplied lesson facts and how they support one concrete decision. Source-category names alone are not proof.
- [ ] Apply the same lesson-fit correction to blanket physics frame, biology level, chemistry composition and mathematics numeric-dispute prescriptions: retain each where taught; otherwise use this lesson's actual concept, classification, relation or procedure. Preserve valid subject-specific prohibitions and language production tasks.
- [ ] For lower grades, keep respectful `Siz` without professional/bureaucratic wording; familiar roles are allowed. Grade 1–6 CBP DPE may be one concrete question answered in 1–2 short sentences, with only necessary scoring components, while retaining all ten section headings. For Grade 1–6 RLC, omit redundant per-choice Why/confidence prose and keep the final required reasoning step brief. The platform audit proves those per-choice fields are unsupported and the five-step schema is unchanged. Use the lower existing recall/cloze counts, not expanded quotas. Error-detection accepts a sufficient concrete explanation without mandatory abstract rule recitation. Optional sentence reflection must use actual supplied facts, not absent maps.
- [ ] Run prompt/language/structured/notation suites and commit the scoped change. Record any structural requirements that prevented shortening; do not silently delete machine fields.

## Task 2: Judge, solver and terminal content-quality enforcement

**Files:** modify `app/services/phase_judge.py`, `app/services/solver.py`, `app/schemas/solver.py`, `app/services/errors.py`, `app/services/pipeline.py`; add `tests/services/test_pipeline_content_quality.py`; extend judge/solver and pipeline tests.

**Consumes:** Task 1 contract and existing `JudgeOutcome`, `SolveOutcome`, `PhaseArtifact`, fenced writes and bounded retries. **Produces:** semantic review of answerability/all options and no completed learner phase with an unresolved major defect.

- [ ] Add failing tests: a contradictory `passed=True` verdict with a major failure must not erase the failure; `agrees=True` with a high-confidence solver discrepancy must not erase it. Add pipeline cases for persistent major, zero regeneration budget, hard failed repair, successful repair, minor-only pass, known-major followed by unavailable recheck, lease loss/cancellation, and structured artifact consistency.
- [ ] Add semantic reviewer instructions. The judge must explicitly check visible prerequisites, rubric answerability, unavailable evidence/visual references, grade-inappropriate untaught methods, misleading extra certainty, and cross-phase repeated application. Keep absence-only ordinary exercise data/scenarios permissible, but missing required evidence or fabricated provenance is a major exception. Require quoted evidence; do not turn every unmentioned ordinary fact into a major violation.
- [ ] Extend solver instructions and schema descriptions to independently judge every option and the feedback, not only the marked key. Treat a defensible second answer or no answer under the wording as a discrepancy; accept genuinely open tasks with sufficient evidence and aligned rubrics. Preserve confidence thresholds. The test examples must include math-equivalent options, language synonyms/context-dependent alternatives, scientific category overlap and historical terminology.
- [ ] Extend independent solver coverage to CBP and sentence-fill, alongside the existing key-bearing phases. Preserve teacher artifacts and phase output grammar.
- [ ] Implement a typed `PersistentContentQualityFailure` and a fenced failed-artifact write (similar to `PersistentSolverMismatch`) with `judge_status="major_blocked"`. On exhausted/hard-failed major repair, store the actual inspected artifact plus warnings for review, raise the typed terminal error, and never write done or archive. Do not misclassify quoted words such as “timeout” inside a content failure as a provider transient.
- [ ] After a solver repair changes the artifact, judge that same final artifact again before acceptance. A previous successful judge verdict cannot validate a changed artifact. If a known major cannot be revalidated, retain its blocking state. Preserve existing retry/control-signal behavior and source of token/provider/artifact metadata.
- [ ] Update the old tests that explicitly expected `major_shipped` on learner phases. Run the full affected pipeline, solver, auth, retry, lease, structured-output and envelope suites. Commit only after all relevant cases pass.

## Task 3: Scoped correction of verified source issues

**Files:** create `app/services/lesson_errata.py`, `tests/services/test_lesson_errata.py`, a documented errata entry in `docs/`; modify extract handling in `app/services/pipeline.py` and its extract-cache tests.

**Consumes:** known lesson UUID `768820b7-54ea-45d2-bbb4-d95275ef95e6`, history subject, original extract and cited factual review. **Produces:** a corrected, auditable extract for this source without changing unrelated lessons.

- [ ] Define a pure, idempotent `apply_lesson_errata(output_md: str, *, section_id: str, subject: str) -> str`. Exact lesson identity and subject guard the correction. Tests use the actual original extract, a second application, a different lesson and another subject.
- [ ] For this verified lesson, remove the incorrect Yellow-River-bank qualifier from Sian, avoid asserting la’l and lojuvard are synonyms while keeping the school route name, and preserve the textbook's two Royal Road directions and qualified duration/distance. Do not invent an ancient document or replace the school answer. Record citations in the maintainer errata document, not as student-facing technical metadata.
- [ ] Apply the correction to both fresh and reused extract outputs before persistence/return, so the imported primer and every downstream phase see the same corrected facts. Ensure the corrected extract is not hidden merely in later generator context. Version the extract hash for this correction if required to prevent resuming a stale uncorrected phase; prove cache and fresh paths with behavioral tests.
- [ ] Run extract, coverage, source-boundary, errata and pipeline tests; commit.

## Task 4: Prevent automatic publication of explicit major failures

**Separate repository:** isolated branch/check-out of the platform backend based on the verified running version, retaining existing user changes. Target files `apps/library/services/auto_publish.py` and `apps/library/tests/test_notion_homework_autopublish.py` (and narrowly necessary test helpers). Do not edit provisioners, parser grammar or frontend as part of this task.

**Consumes:** existing `transform_report.artifacts` phase provenance and ordinary auto-publish result. **Produces:** explicit major verdicts yield the existing held outcome rather than automatic approval.

- [ ] Add failing cases for `major_shipped`, `major_regen_failed`, and `major_blocked`: an otherwise structurally valid transformed packet must not call approval/publish. Cover `needs_review`, already approved but not published, and repeated/duplicate import. Positive tests cover `ok`, absent optional judge status and harmless minor findings.
- [ ] Read the provenance list and block those explicit major states before automatic approval. Persist actionable `publish_error.reasons`, leave/demote the unpublished import to `needs_review`, and return `attempted=true, published=false` so the current UI says held. Do not silently withdraw already-published material or claim it was withdrawn; that legacy remediation is outside these newly generated canaries.
- [ ] Run auto-publish/import API/completeness tests against a scratch test database. Apply the verified commit to the local running backend while preserving unrelated edits, then restart only the local :8090 process if needed. Verify :8095 still reaches it and a rejected fixture reports held. Commit and document.

## Task 5: Real model regression and full-path acceptance

**Artifacts:** create `scripts/smoke_homework_quality.py`, adversarial/positive fixtures under `tests/fixtures/homework_quality/`, focused harness tests, and a results ledger outside credential files. Keep actual generation IDs, revision/prompt hashes, Notion page IDs, import IDs, library topic/pack IDs and screenshots.

- [ ] Build paired negative/positive model cases for each defect class from the original 14 findings. Include missing source/author, false proof, ambiguous alternatives, wrong-answer-dependent prompts, changed certainty/sequence/branching, repeated application, style cues, adult/untaught jargon, overloaded rubric, absent map/data, and unclear referents. Source errata has deterministic exact-fixture coverage. Include valid mathematical generated data, a supplied science experiment, a language passage with an open supported answer, and a properly attributed history excerpt to measure over-rejection.
- [ ] The runner uses existing production `agent.run_phase` / judge / solver API transport and existing configured credentials. It writes sanitized JSON findings with fixture ID, actual model/revision, expected vs observed result and decisive evidence. Unit tests cover runner parsing/scoring, not fake claims of model behavior. Run old/new reviewer comparisons when useful and fix newly evidenced failures rather than lowering expectations.
- [ ] Run the full generator suite with sentinel test environment. Separate any baseline/environment failures using an unchanged checkout; fix regressions from this work. Run the broad final code review and resolve material issues before deployment.
- [ ] Deploy the verified generator commit to the existing worker pin/floor mechanism without restarting the head or unhalting unrelated queued work. Verify actual worker versions/heartbeats and which worker claims each canary. No inference from a pin file alone.
- [ ] Select a bounded set of existing lessons covering history, mathematics, science, language, and default-family content where available. Use all required learner phases, no hand-edited generated answers. Create fresh jobs, record IDs and live handles, wait for terminal state, inspect every question/key/rubric and overall workload. Require no major judge failures and accepted solver results. Any failure remains incomplete and is repaired/retested.
- [ ] Verify real `Platform Homeworks` attachments correspond to those exact fresh job IDs and final revision. Open localhost:8095 Lesson Library, click Import from Notion, scan/select those lessons, and execute import through the UI. Verify per-row success plus actual published library sections/content; do not substitute a direct-ingest API for this user requirement.
- [ ] Use the actual runtime preview and a dedicated test student assignment where needed to verify student-visible evidence, notation, answer hiding, wrong-choice independence and persistent server grading. Test both correct and incorrect paths. Capture screenshots and the corresponding sanitized submission/verdict evidence.
- [ ] Write a requirement-by-requirement result table: original defect, general fix, regression evidence, fresh homework evidence, imported/student evidence, limits. Update generator worklog/index/live docs, retain the plan as live until acceptance is complete, then move it to shipped. Do not claim that a finite stochastic test guarantees all future output; state the enforced rejection and observed coverage precisely.
