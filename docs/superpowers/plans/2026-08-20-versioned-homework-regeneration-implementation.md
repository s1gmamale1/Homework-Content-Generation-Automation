# Versioned Homework Regeneration Implementation Plan

> **Execution rule:** implement this plan through external Claude Code agents using subagent-driven development. Every implementer and reviewer must use `claude-opus-5`; do not configure a fallback model. The operations manager creates and verifies each worktree, gates every commit, and integrates only after the task's independent review passes.

**Goal:** Add a separate, feature-flagged workflow that selectively regenerates complete homework snapshots with the currently deployed prompts, pauses once for campaign canary review, and then automatically publishes approved results as immutable `Homework V2`, `Homework V3`, and later sibling pages without modifying V1 or earlier versions.

**Architecture:** Reuse `HomeworkJob`, the existing worker/pipeline, phase outputs, judge/solver behavior, downloads, usage accounting, and normal cancellation by creating revision jobs that are intrinsically excluded from Fleet discovery and legacy Notion archival. New campaign and target rows hold the immutable phase plan and form a regeneration-only durable publication queue. A marker-backed publisher owns version allocation and creates/adopts one exact versioned Notion page. The UI is a separate Regeneration route hidden behind the same backend flag.

**Stack:** Python 3.13, FastAPI, SQLAlchemy async/PostgreSQL, Alembic, pytest, React 19, TypeScript, TanStack Query, Notion API wrapper.

**Approved design:** `docs/superpowers/specs/2026-08-20-versioned-homework-regeneration-design.md`

---

## 1. Safety, Authority, and Collision Gate

Implementation approval authorizes local code, tests, commits, and local branch integration only. It does **not** authorize a push, PR mutation, merge, deployment, production database write, live Notion write, paid model call, prompt deployment, or feature-flag enablement.

Before the first implementation worktree is created, and again whenever the base or scope changes:

1. Run `git fetch --all --prune` in the repository.
2. Record the current `origin/Nggaev-v2` SHA, `git branch -a`, `git worktree list --porcelain`, and all open PR authors/heads.
3. Inspect the actual diffs and tests of overlapping branches/PRs, especially PRs #117, #118, #136, `origin/pigganigeon`, `feat/selective-regeneration-preview`, and `feat/selective-regeneration-integration`.
4. Treat PRs authored by `s1gmamale1` as strictly read-only. Do not edit, review, comment, retarget, close, or merge them.
5. Stop on equivalent work. On partial overlap, establish ownership and integration order before an edit.
6. Create manual isolated worktrees from the verified base. Do not use Claude's native `--worktree` option.
7. Confirm every worktree starts clean and at the recorded SHA before dispatching an agent.

Collision snapshot at planning time:

- plan worktree: `plan/selective-regeneration-campaign@b2025cd`;
- implementation base last inspected: `origin/Nggaev-v2@a955cfebbf5bc8456974a9f40a95fc4852c866e4`;
- `feat/prompt-set-registry@b1f54d6` / PR #136 is not a dependency and must remain untouched;
- `feat/selective-regeneration-preview@a5666f6` is the shelved broad-outbox implementation, not an implementation base;
- `feat/selective-regeneration-integration@f2b325f` and its Claude worktree remain untouched;
- `origin/pigganigeon@e0e499d` is the separate current-prompt review lane and is not merged by this feature;
- PRs #117/#118 overlap pipeline/prompt/phase paths and must be re-inspected immediately before work begins.

The migration number `0063` below is provisional. The schema agent must derive the filename and `down_revision` from the single Alembic head on the verified execution base. If the head is no longer `0062`, rename the migration rather than creating a second head.

## 2. External-Agent Operating Protocol

For every task:

1. The operations manager gives one Claude Opus 5 controller the exact task section, approved design, allowed files, forbidden files, base SHA, and test commands.
2. The controller uses the repository's `superpowers:subagent-driven-development` workflow: a fresh implementation subagent follows RED → GREEN → REFACTOR; a different Claude Opus 5 subagent reviews specification compliance and code quality.
3. Agents may not push, merge, deploy, use live credentials, or alter another worktree.
4. The controller commits only the task-owned files after its tests pass.
5. The operations manager independently inspects the diff, verifies no out-of-scope files changed, runs the named task tests, and records the reviewer verdict.
6. Failed review returns to the same task branch. A task is not integrated because an agent merely says it is complete.
7. Integration uses cherry-picks into a local preview branch in the dependency order below. Resolve overlaps deliberately; never accept a blanket conflict resolution.

All automated provider and Notion tests use fakes. Set `REGENERATION_ENABLED=false`, `NOTION_ENABLED=false`, and fake-provider settings in test invocations unless a test explicitly opts into the guarded behavior.

## 3. Dependency Graph and Parallel Waves

```text
                         verified execution base
                    /            |           |          \
          T1 schema/models   T2 planner   T3 Notion   T4 UI shell
                    \            /           |          |
                 integration checkpoint 1    |          |
                   /                 \        |          |
       T5 discovery/estimate    T6 snapshot + isolation  |
                   \                 /                   |
                    T7 campaign orchestration            |
                              |              \            |
                              |              T8 publisher |
                              \               /           |
                               T9 API/report              |
                                      \                  /
                                       T10 UI wiring
                                             |
                                       T11 acceptance
```

Parallel execution is allowed only in these waves:

- **Wave 1:** Tasks 1, 2, 3, and 4 from the same verified base. Their file ownership is disjoint.
- **Wave 2:** Tasks 5 and 6 after Tasks 1 and 2 are integrated. Task 5 owns discovery/estimation and cost queries; Task 6 owns revision snapshots, Fleet isolation, pipeline completion, and legacy archive guards.
- **Wave 3:** No backend parallelism. Task 7, then Task 8, then Task 9 are load-bearing state-machine steps.
- **Wave 4:** Task 10 after Task 9; Task 11 after everything.

The operations manager keeps at most four Claude controllers active and reserves one integration worktree. A later task never begins against an unreviewed dependency commit.

---

## Task 1: Schema, Models, and Database Invariants

**Lane:** A, Wave 1

**Owns:**

- Create `app/models/regeneration_campaign.py`
- Create `app/models/regeneration_target.py`
- Modify `app/models/homework_job.py`
- Modify `app/models/phase_output.py`
- Modify `app/models/__init__.py`
- Create the next migration under `alembic/versions/`
- Create `tests/models/test_regeneration_models.py`
- Create `tests/migrations/test_regeneration_schema.py`
- Create `tests/integration/test_regeneration_constraints.py`

**Must not touch:** services, repositories, API, `pipeline.py`, Notion code, or web files.

### Data contract

`RegenerationCampaign` contains:

- `status`: `draft | canary_running | awaiting_canary_approval | approved | bulk_running | attention_required | completed | completed_with_abandonments | rejected | cancelled`;
- immutable JSON fields `requested_phases`, `excluded_phases`, `selection_spec`, and `launch_contract`;
- `refresh_extraction`, `exclusion_acknowledged`, `canary_size`, `estimated_cost_low_usd`, `estimated_cost_high_usd`, and `app_git_revision`;
- audit timestamps `canary_launched_at`, `approved_at`, `rejected_at`, `cancel_requested_at`, `completed_at` and text audit reasons.

`RegenerationTarget` contains:

- `campaign_id`, `toc_entry_id`, `output_language`, `source_job_id`, `is_canary`, and immutable `phase_plan`;
- `status`: `planned | generating | awaiting_canary_approval | publication_pending | publishing | published | generation_failed | publication_failed | abandoned`;
- `publication_released_at`, `publication_version`, `notion_page_id`;
- durable claim fields `publication_claim_token`, `publication_claimed_at`, `publication_attempts`, `publication_next_attempt_at`, `publication_last_error`;
- `terminal_at` and `terminal_reason`.

`HomeworkJob` adds nullable `revision_of_job_id` with `ON DELETE RESTRICT` and nullable unique `regeneration_target_id` with `ON DELETE RESTRICT`. A check requires the two columns to be both null or both non-null. A second check requires `batch_id IS NULL` when `revision_of_job_id IS NOT NULL`.

`PhaseOutput` adds nullable `copied_from_phase_output_id` with `ON DELETE RESTRICT`.

Database invariants:

- unique target `(campaign_id, toc_entry_id, output_language)`;
- partial unique active lineage `(toc_entry_id, output_language) WHERE terminal_at IS NULL`;
- unique `(toc_entry_id, output_language, publication_version)` for non-null versions;
- unique `homework_jobs.regeneration_target_id`;
- `published` requires non-null version, page ID, publication release, and terminal time;
- `abandoned` requires terminal time; all other target states require `terminal_at IS NULL`;
- publication states require `publication_released_at IS NOT NULL`;
- a PostgreSQL trigger rejects transition into `publication_pending`, `publishing`, or `published` unless the owning campaign has `approved_at IS NOT NULL` and is not rejected/cancelled.

Do not add `revision_job_id` to the target. The authoritative one-to-one link is `HomeworkJob.regeneration_target_id`; the target's revision job is read through that unique relationship, avoiding a cyclic pair of mutable foreign keys.

### TDD steps

1. Write model tests asserting the new columns, relationships, check names, partial index predicate, and restrictive FK semantics.
2. Write migration tests that upgrade from the current head, inspect every table/index/check/FK/trigger, then downgrade cleanly.
3. Write PostgreSQL integration tests that race two active targets in the same language, allow UZ V2 and RU V2 independently, reject duplicate versions in one language, reject revision+batch, reject source deletion, and reject publication before approval.
4. Run the tests and record the RED failures.
5. Implement the models and migration with named constraints.
6. Run:

```bash
uv run pytest -q tests/models/test_regeneration_models.py tests/migrations/test_regeneration_schema.py
RUN_DB_INTEGRATION=1 uv run pytest -q tests/integration/test_regeneration_constraints.py
uv run alembic heads
```

Expected: all named tests pass and `alembic heads` prints exactly one head.

7. Commit: `feat(regeneration): add campaign and revision schema`

---

## Task 2: Pure Phase Planner and State Rules

**Lane:** B, Wave 1

**Owns:**

- Create `app/services/regeneration_planner.py`
- Create `app/services/regeneration_states.py`
- Create `tests/services/test_regeneration_planner.py`
- Create `tests/services/test_regeneration_states.py`

**Must not touch:** models, repositories, API, pipeline, Notion, or web files.

### Interfaces

```python
@dataclass(frozen=True)
class DependencyEdge:
    upstream: str
    downstream: str

@dataclass(frozen=True)
class RegenerationPhasePlan:
    canonical_phases: tuple[str, ...]
    selected_phases: tuple[str, ...]
    auto_included_phases: tuple[str, ...]
    regenerated_phases: tuple[str, ...]
    copied_phases: tuple[str, ...]
    excluded_affected_phases: tuple[str, ...]
    broken_dependency_edges: tuple[DependencyEdge, ...]
    refresh_extraction: bool

def build_phase_plan(
    *,
    subject: str,
    selected_phases: Collection[str],
    excluded_affected_phases: Collection[str] = (),
    refresh_extraction: bool = False,
    exclusion_acknowledged: bool = False,
) -> RegenerationPhasePlan: ...
```

The implementation imports the canonical subject flow and `PHASE_DEPS` from the same production authority used by the pipeline. It preserves canonical order, computes transitive downstream closure, refuses unknown or unavailable phases, and requires acknowledgement only when an exclusion actually breaks an affected edge. With extraction enabled, all content phases enter the closure before exclusions.

`regeneration_states.py` exposes pure functions for legal campaign/target transitions and `roll_up_campaign(target_statuses, approved, cancelled)` so services do not duplicate terminality rules. `generation_failed` and `publication_failed` remain nonterminal/attention-required; only `published` and `abandoned` are terminal.

### TDD steps

1. Write table-driven tests for every subject flow and graph edge.
2. Pin these measured examples: flashcards → 10/11 content phases, memory-check → 5, boss-arena → 2, reflection → 1.
3. Test deterministic ordering, duplicate inputs, invalid phases, extraction off/on, warning-backed exclusion, and unaffected exclusions.
4. Test every legal and illegal campaign/target transition and complete rollups including mixed published/abandoned/failure cases.
5. Run RED, implement the smallest pure code, then run:

```bash
uv run pytest -q tests/services/test_regeneration_planner.py tests/services/test_regeneration_states.py
```

6. Commit: `feat(regeneration): add phase planning and state rules`

---

## Task 3: Marker-Backed Versioned Notion Writer

**Lane:** C, Wave 1

**Owns:**

- Create `app/services/notion_versioned_homework.py`
- Modify `app/services/notion_archive.py` only to extract/reuse rendering primitives without changing legacy behavior
- Create `tests/services/test_notion_versioned_homework.py`
- Modify `tests/services/test_notion_archive.py` only for parity coverage necessitated by the extraction

**Must not touch:** models, repositories, pipeline completion, API endpoints, config, main lifespan, or web files.

### Interfaces and behavior

```python
@dataclass(frozen=True)
class HomeworkRevisionMarker:
    toc_entry_id: UUID
    output_language: str
    revision_job_id: UUID
    campaign_id: UUID
    publication_version: int

class VersionPageCollision(RuntimeError): ...

def encode_revision_marker(marker: HomeworkRevisionMarker) -> str: ...
def decode_revision_marker(blocks: Sequence[dict]) -> HomeworkRevisionMarker | None: ...

def write_or_adopt_versioned_homework(
    *, client: NotionClientWrapper, lesson_page_id: str,
    phase_md: Mapping[str, str], marker: HomeworkRevisionMarker,
    stored_page_id: str | None,
) -> str: ...
```

The first block of `Homework V{n}` is a deterministic machine-readable marker containing all five fields. The stored page ID wins. Without it, enumerate exact-title child pages, read the candidate's first blocks, and adopt only an exact marker match. A same-title page with missing/different marker raises `VersionPageCollision`; it is never cleared or overwritten. With no candidate, create the page with the marker and the same grouped homework layout used by V1. The function is synchronous; retry/leases remain Task 8's responsibility.

Refactor the existing V1 renderer into shared pure helpers while preserving the current `Homework` title, file attachments, nested layout, replace semantics, and call ordering byte-for-byte at the Notion-block level. Do not route V1 through marker logic.

### TDD steps

1. Write fake-client tests for marker round-trip, stored-ID reuse, crash-window marker adoption, wrong-marker collision, missing-marker collision, independent UZ/RU V2 markers, and V2/V3 sibling titles.
2. Add parity tests proving the legacy V1 `_push_to_notion` produces the same page tree and blocks before/after helper extraction.
3. Run RED, implement, then run:

```bash
uv run pytest -q tests/services/test_notion_versioned_homework.py tests/services/test_notion_archive.py tests/services/test_notion_archive_teacher.py
```

4. Commit: `feat(regeneration): add immutable versioned Notion writer`

---

## Task 4: Feature-Flagged Regeneration UI Shell

**Lane:** D, Wave 1

**Owns:**

- Create `web/src/routes/regeneration.tsx`
- Create `web/src/components/regeneration/regeneration-wizard.tsx`
- Create `web/src/components/regeneration/campaign-list.tsx`
- Create `web/src/components/regeneration/canary-review.tsx`
- Create `web/src/components/regeneration/campaign-report.tsx`
- Create `web/src/lib/regeneration-state.ts`
- Create `web/src/lib/regeneration-feature.ts`
- Create `web/src/lib/regeneration-state.test.ts`
- Modify `web/src/App.tsx`
- Modify `web/src/components/layout.tsx`

**Must not touch:** backend files, `web/src/lib/api.ts`, or `web/src/lib/types.ts`.

Use local fixture objects declared in `regeneration-state.test.ts` and component files; Task 10 replaces them with typed API data. `regeneration-feature.ts` exports `IS_REGENERATION_ENABLED = import.meta.env.VITE_REGENERATION_ENABLED === "1"`; the route and nav item are absent when false. The backend flag remains the authoritative safety gate. The UI must say **Regenerating**, never reuse Fleet's **Generating** label.

The shell must visibly support:

- lesson/language selection;
- phase selection with `Regenerates X of Y phases` cascade disclosure;
- exclusion warning and acknowledgement;
- extraction off by default;
- low/high estimate and canary size;
- complete canary packet review with copied/regenerated provenance and judge/solver warnings;
- one campaign-level Approve or Reject gate;
- report buckets for published, pending, generation failed, publication failed, and abandoned;
- retry/abandon action positions, disabled until Task 10 wiring.

For a one-target campaign, label the approval `Approve canary and publish V2`; never render an empty bulk-generation gate.

### TDD steps

1. Add pure TypeScript tests for cascade disclosure text, warning acknowledgement state, single-target labels, report reason rendering, and feature-flag visibility.
2. Run RED, build the route and components with fixtures, then run:

```bash
cd web
npm test -- --test-name-pattern=regeneration
npm run build
npm run lint
```

3. Commit: `feat(web): add regeneration workflow shell`

---

## Integration Checkpoint 1

After Tasks 1–4 pass independent review:

1. Create the local preview branch from the execution base: `feat/versioned-homework-regeneration-preview`.
2. Cherry-pick Task 1, Task 2, Task 3, then Task 4.
3. Confirm no unexpected conflict and run:

```bash
uv run pytest -q \
  tests/models/test_regeneration_models.py \
  tests/migrations/test_regeneration_schema.py \
  tests/services/test_regeneration_planner.py \
  tests/services/test_regeneration_states.py \
  tests/services/test_notion_versioned_homework.py \
  tests/services/test_notion_archive.py \
  tests/services/test_notion_archive_teacher.py
uv run alembic heads
cd web && npm test && npm run build
```

4. Run an Opus 5 integration review limited to the combined schema/planner/Notion/UI boundaries.
5. Tasks 5 and 6 branch from this reviewed checkpoint, not from their original Wave 1 branches.

---

## Task 5: Source Discovery, Preflight, and Cost Estimation

**Lane:** E, Wave 2; may run beside Task 6

**Owns:**

- Create `app/repositories/regeneration_campaigns.py`
- Create `app/repositories/regeneration_targets.py`
- Create `app/services/regeneration_discovery.py`
- Create `app/services/regeneration_estimator.py`
- Modify `app/repositories/cost.py`
- Create `tests/repositories/test_regeneration_repositories.py`
- Create `tests/services/test_regeneration_discovery.py`
- Create `tests/services/test_regeneration_estimator.py`
- Create `tests/integration/test_regeneration_source_and_version_queries.py`

**Must not touch:** `app/repositories/jobs.py`, `pipeline.py`, phase-output writes, legacy archive callers, API router, publisher, main lifespan, or web files.

### Source and discovery contract

`regeneration_discovery.py` exposes:

```python
async def list_eligible_sources(
    session: AsyncSession, *, book_ids: Collection[UUID] | None,
    toc_entry_ids: Collection[UUID] | None,
    output_languages: Collection[str] | None,
) -> list[EligibleRegenerationSource]: ...

async def resolve_default_source(
    session: AsyncSession, *, toc_entry_id: UUID, output_language: str,
) -> HomeworkJob: ...

async def preflight_notion_destinations(
    session: AsyncSession, sources: Sequence[EligibleRegenerationSource],
) -> list[NotionPreflightFailure]: ...
```

Eligibility requires a done `kind="homework"` job with a complete usable snapshot. For V3+, `resolve_default_source` chooses the highest successfully published `publication_version` for the same `(toc_entry_id, output_language)`; otherwise it chooses the latest completed non-revision V1 job in that language. Never choose an unpublished or abandoned revision.

Preflight reuses `_resolve_subject_page_id`, existing lesson-title disambiguation, stored `notion_lesson_page_id`, and subject/grade/language mapping. It performs read-only validation and returns all missing mappings together. It makes no Notion writes and no model calls.

### Estimator contract

```python
async def estimate_regeneration(
    session: AsyncSession, *, targets: Sequence[EligibleRegenerationSource],
    plans: Mapping[UUID, RegenerationPhasePlan], launch_contract: LaunchContract,
    now: datetime,
) -> RegenerationEstimate: ...
```

The estimator:

- queries successful API `AgentUsage` rows from `[now - 30 days, now]`;
- joins `AgentUsage.phase_output_id -> PhaseOutput.phase_name`;
- groups observed means by operation, phase, provider, and model;
- confirms judge/solver usage is linked to the inspected phase-output row rather than a detached synthetic phase;
- counts copied phases and copied extract as zero;
- counts authoring, judge, solver, optional extraction, schema retry, judge-regeneration, and solver-regeneration budgets separately;
- falls back to a documented conservative token envelope multiplied by the existing static pricing table when no matching observation exists;
- returns low/high USD estimates plus a line-item explanation and an explicit `is_estimate=true` marker.

### TDD steps

1. Write discovery tests for incomplete/failed/teacher-material exclusion, immediate source lineage, V1 fallback, latest-published V3 source choice, and language isolation.
2. Write preflight tests for stored lesson page, resolvable mapping, missing mapping aggregation, and no remote write.
3. Write estimator tests with fixed time and fixed usage fixtures for exact 30-day window, phase linkage, copied-zero behavior, retry budgets, and static fallback.
4. Write repository/integration tests for row locks and authoritative `latest_published_source` and `next_expected_version` queries.
5. Run RED, implement, then run:

```bash
uv run pytest -q \
  tests/repositories/test_regeneration_repositories.py \
  tests/services/test_regeneration_discovery.py \
  tests/services/test_regeneration_estimator.py
RUN_DB_INTEGRATION=1 uv run pytest -q tests/integration/test_regeneration_source_and_version_queries.py
```

6. Commit: `feat(regeneration): add source discovery and estimates`

---

## Task 6: Complete Revision Snapshots and Runtime Isolation

**Lane:** F, Wave 2; may run beside Task 5

**Owns:**

- Create `app/services/regeneration_snapshot.py`
- Modify `app/repositories/jobs.py`
- Modify `app/repositories/phase_outputs.py`
- Modify `app/services/pipeline.py`
- Modify `app/services/agent.py` only for copied-extract provenance if the existing helper cannot accept the source identifiers
- Modify `app/services/notion_archive.py` for the intrinsic revision guard
- Modify `app/api/v1/jobs.py` for synchronous archive-route conflicts
- Modify `app/api/v1/batch.py` for defensive sweep exclusion
- Modify `app/api/v1/books.py` for clean book-delete conflict and TOC isolation
- Create `tests/services/test_regeneration_snapshot.py`
- Create `tests/services/test_regeneration_pipeline.py`
- Create `tests/services/test_regeneration_archive_isolation.py`
- Create `tests/repositories/test_regeneration_fleet_isolation.py`
- Create `tests/api/test_regeneration_archive_isolation.py`
- Create `tests/api/test_regeneration_book_delete.py`

**Must not touch:** Task 5 files, campaign state orchestration, publisher, new regeneration API router, config/main, or web files.

### Snapshot service

```python
async def create_revision_job(
    session: AsyncSession, *, target_id: UUID, launch_contract: LaunchContract,
) -> HomeworkJob: ...
```

The service locks the target, returns the already-linked revision job on repeat, verifies the source is still eligible, and creates a `kind="homework"`, `batch_id=NULL`, `selected_phases=NULL` revision linked to its immediate source and target.

Copy exactly these `PhaseOutput` columns for each phase in `plan.copied_phases`:

- `phase_name`, `phase_order`, `prompt_hash`, `model_name`, `provider`, `output_md`;
- `tokens_input`, `tokens_output`, `status`, `error_message`, `validation_warnings`;
- `judge_status`, `solver_status`, `started_at`, `completed_at`;
- `content_json`, `authoring_mode`, `content_schema_version`, `renderer_version`;
- set `claim_token=NULL` and `copied_from_phase_output_id=source_phase.id`.

Copied rows must be source-terminal and usable. Do not copy `id`, `job_id`, or the source claim token. Seed copied rows at their canonical original `phase_order`; leave regenerated rows absent. When extraction is copied, also call the existing zero-cost `record_cached_lesson_extract` path with source job/phase provenance. Do not clone any paid `AgentUsage` row for any copied phase.

The ordinary pipeline then sees `selected_phases=NULL`, skips seeded done rows, creates missing rows, and preserves existing judge/solver behavior unchanged. On successful revision completion, update the target to `awaiting_canary_approval` or `publication_pending` according to campaign approval and **do not** call legacy `archive_job`. Hard job failure maps to `generation_failed`; `solver_status="mismatch_blocked"` remains hard. Soft judge states remain publishable exactly as specified.

Before the target becomes publication-pending, assert a complete terminal row exists for every required canonical phase and every phase is usable under the existing structured-content rules.

### Fleet and archive isolation

Every normal Fleet query must explicitly include `HomeworkJob.revision_of_job_id IS NULL`, including:

- `find_active_for_section`;
- `latest_for_section`;
- `latest_by_section`;
- batch adoption and resume selection;
- batch status/rollup paths that query jobs outside a batch join;
- book TOC status enrichment;
- `section_prior_api_cost` and normal dedup/rebill warnings.

Revision jobs remain claimable through the generic worker queue and available by explicit job ID for pipeline, phase, SSE, download, cancellation, and safe retry reuse.

When legacy archival is enabled and `notion_archive.archive_job` loads a job, reject any `revision_of_job_id IS NOT NULL` before resolving lesson/page identity or constructing a Notion client: persist deterministic skip reason `regeneration revision: use versioned publisher` and return. This guard applies even with `force=True` or a claim token. The pipeline completion branch already avoids calling it for revisions; when Notion is globally disabled, retain the current no-DB-work early return. `POST /jobs/{id}/retry-archive` and the force route must synchronously return 409 for revision jobs. Batch rearchive selection excludes them defensively.

Book deletion with any regeneration campaign/target/revision history returns a controlled 409 before deletes; do not leak a raw restrictive-FK error.

### TDD steps

1. Write snapshot tests that fail first and pin the full copied-column set, canonical ordering, idempotent job creation, missing-source refusal, zero cloned usages, copied extract marker, and complete-snapshot validation.
2. Write pipeline tests using fake agent/judge/solver for mixed copied/regenerated flows, soft judge statuses, hard failure, solver blocked, canary hold, and approved publication release.
3. Write one test for every Fleet query/caller listed above. Do not accept one indirect test as coverage for several SQL functions.
4. Write archive tests proving `force=True`, automatic claim token, retry route, force route, and batch sweep all cannot read or write Notion for a revision.
5. Write the clean book-delete 409 test.
6. Run RED, implement, then run:

```bash
uv run pytest -q \
  tests/services/test_regeneration_snapshot.py \
  tests/services/test_regeneration_pipeline.py \
  tests/services/test_regeneration_archive_isolation.py \
  tests/repositories/test_regeneration_fleet_isolation.py \
  tests/api/test_regeneration_archive_isolation.py \
  tests/api/test_regeneration_book_delete.py \
  tests/services/test_pipeline_flow1.py \
  tests/api/test_batch_resume_endpoint.py \
  tests/api/test_books_kind_status.py \
  tests/api/test_never_pay_twice.py
```

7. Commit: `feat(regeneration): create isolated complete revision jobs`

---

## Integration Checkpoint 2

After Tasks 5 and 6 pass independent review:

1. Cherry-pick Task 5 then Task 6 into the preview branch.
2. If `cost.py` or model imports conflict because the base moved, resolve by retaining Task 5's estimator queries and Task 6's normal-Fleet exclusion. Add a regression test for both behaviors before continuing.
3. Run all Task 1–6 tests plus:

```bash
uv run pytest -q tests/repositories tests/services/test_pipeline_flow1.py tests/api/test_never_pay_twice.py
RUN_DB_INTEGRATION=1 uv run pytest -q \
  tests/integration/test_regeneration_constraints.py \
  tests/integration/test_regeneration_source_and_version_queries.py
```

4. Run an Opus 5 integration review focused on revision leakage, copied data, usage accounting, and the V1 archive guard.

---

## Task 7: Campaign Orchestration, Canary Gate, Retry, Cancellation, and Abandonment

**Lane:** G, Wave 3; starts after Checkpoint 2

**Owns:**

- Create `app/services/regeneration_campaign.py`
- Extend `app/repositories/regeneration_campaigns.py`
- Extend `app/repositories/regeneration_targets.py`
- Create `tests/services/test_regeneration_campaign.py`
- Create `tests/integration/test_regeneration_campaign_concurrency.py`

**Must not touch:** publisher/Notion writer, API router, config/main, or web files.

### Service interface

```python
class RegenerationCampaignService:
    async def create_campaign(self, spec: CreateCampaignSpec) -> RegenerationCampaign: ...
    async def launch_canary(self, campaign_id: UUID) -> RegenerationCampaign: ...
    async def approve_canary(self, campaign_id: UUID, *, actor: str) -> RegenerationCampaign: ...
    async def reject_canary(self, campaign_id: UUID, *, actor: str, reason: str) -> RegenerationCampaign: ...
    async def cancel(self, campaign_id: UUID, *, actor: str, reason: str) -> RegenerationCampaign: ...
    async def retry_generation(self, target_id: UUID) -> RegenerationTarget: ...
    async def retry_publication(self, target_id: UUID) -> RegenerationTarget: ...
    async def abandon(self, target_id: UUID, *, actor: str, reason: str) -> RegenerationTarget: ...
```

Creation resolves and stores each target's source and phase plan, rejects any active same-language lineage, chooses deterministic canaries from a stable `(book_id, toc order, language, target id)` order, and creates no jobs or external calls. Launch preflights all destinations once, then creates only canary revision jobs. Non-canary targets remain `planned` with no job row.

Approval locks the campaign and targets, sets `approved_at` once, releases successful canaries to `publication_pending`, creates all remaining revision jobs exactly once, and moves them to `generating`. Repeated approval returns the current campaign without duplicate jobs. A one-target campaign uses this same approval but has no separate bulk gate.

Reject-before-approval transitions every canary revision target and every planned target to terminal `abandoned`, sets a reason distinguishing rejected canary, creates no version, and never publishes.

Use this complete cancel/abandon table:

| Current target state | Reject canary | Cancel approved campaign | Explicit abandon |
|---|---|---|---|
| `planned` | `abandoned`, no job/version | `abandoned`, no job/version | `abandoned` |
| `generating` | request safe job cancellation, then `abandoned` when terminal | same | same |
| `awaiting_canary_approval` | `abandoned`, no version | not reachable after approval | `abandoned` |
| `publication_pending` | not reachable before approval | `abandoned`, preserve reserved version if any | `abandoned` |
| `publishing` | not reachable before approval | do not revoke unknown remote request; set cancel intent and let claim resolve to `published` or `publication_failed`, then operator resolves | same safe rule |
| `generation_failed` | `abandoned` | remains attention-required unless explicit abandonment is part of cancel | `abandoned` |
| `publication_failed` | not reachable before approval | remains attention-required unless explicit abandonment is part of cancel | `abandoned`, preserve version |
| `published` | unchanged | unchanged | illegal |
| `abandoned` | unchanged | unchanged | idempotent |

Campaign cancellation is not terminal until every target is `published` or `abandoned`. It must never leave a nonterminal target hidden behind a terminal campaign. `completed` means all published; `completed_with_abandonments` means at least one abandoned; `cancelled` is used only when cancellation produced no published target and all targets are abandoned. Reports preserve reasons.

Generation retry creates/requeues through the safe existing job retry semantics without duplicating snapshots or changing the phase plan. Publication retry clears backoff/claim error and moves the same target/version to `publication_pending`; it never calls a model.

### TDD steps

1. Write exhaustive parameterized tests for every row of the table and for rollup outcomes.
2. Test deterministic canary selection, all-target preflight before spend, only-canary job creation, one-target flow, idempotent launch/approve/reject/cancel/retry, and no dangling nonterminal targets.
3. Add DB race tests for two approvals, approval versus cancel, two job creators, and active-lineage conflicts.
4. Run RED, implement with row locks and repository compare-and-set updates, then run:

```bash
uv run pytest -q tests/services/test_regeneration_campaign.py
RUN_DB_INTEGRATION=1 uv run pytest -q tests/integration/test_regeneration_campaign_concurrency.py
```

5. Commit: `feat(regeneration): orchestrate canary and campaign lifecycle`

---

## Task 8: Durable Regeneration Publisher and Version Allocation

**Lane:** H, Wave 3; starts after Task 7

**Owns:**

- Create `app/services/regeneration_publisher.py`
- Extend `app/repositories/regeneration_targets.py`
- Modify `app/config.py`
- Modify `main.py`
- Create `tests/services/test_regeneration_publisher.py`
- Create `tests/services/test_regeneration_publisher_lifespan.py`
- Create `tests/integration/test_regeneration_publication_claims.py`

**Must not touch:** legacy archive behavior, campaign transition policy, API router, or web files.

### Repository and publisher interfaces

```python
async def claim_next_publication(
    session: AsyncSession, *, now: datetime, lease_seconds: int,
) -> ClaimedRegenerationTarget | None: ...

async def reserve_publication_version(
    session: AsyncSession, *, target_id: UUID, claim_token: UUID,
) -> int: ...

class RegenerationPublisher:
    async def run_once(self) -> bool: ...
    async def run_forever(self, stop: asyncio.Event) -> None: ...
```

Claim approved `publication_pending` and retry-due `publication_failed` rows using `FOR UPDATE SKIP LOCKED`, mint a UUID lease, increment attempts, and reclaim expired `publishing` leases. All completion/failure writes compare the current claim token.

`reserve_publication_version` takes a transaction-scoped PostgreSQL advisory lock derived from `(toc_entry_id, output_language)`, returns an existing reserved version unchanged, otherwise stores `max(existing version, 1) + 1`. The database unique constraint remains the final fence. Version 1 is logical and absent from the table. Reserved numbers are never cleared or reused.

For each claim:

1. Reload and validate campaign approval, target claim, complete revision snapshot, source language, and Notion destination.
2. Reserve/reuse the version.
3. Build the full `phase_md` mapping before remote I/O.
4. Call `write_or_adopt_versioned_homework` in a worker thread with the stored page ID and immutable marker.
5. On success, compare claim token and set page ID, `published`, `terminal_at`; then roll up the campaign.
6. On transient failure, preserve page/version identity, calculate bounded exponential backoff, and return to retryable `publication_failed`.
7. On collision or exhausted automatic attempts, leave `publication_failed` for operator retry/abandonment.

Configuration:

- `regeneration_enabled: bool = False` gates the feature;
- `regeneration_publisher_enabled: bool = False` gates the loop separately;
- bounded interval, lease, automatic attempts, and backoff settings have conservative defaults;
- production enables the publisher only on the designated head/API process, but the claim protocol is safe if two processes accidentally run it.

`main.py` starts the loop only when both flags are true and stops/awaits it during lifespan shutdown. It must not start under test unless explicitly enabled. Ordinary worker and event-bus lifespan behavior remains unchanged.

### TDD steps

1. Write unit tests for no-work, successful V2/V3, language-independent V2, stale claim fencing, transient backoff, exhausted retries, collision, no model call, and campaign rollup.
2. Simulate crash after page creation but before DB stamp; expire the lease and prove the next attempt marker-adopts exactly one page.
3. Write integration races for two publishers, expired-lease recovery, advisory version allocation, and unique-version fencing.
4. Write lifespan tests for all four flag combinations and clean shutdown.
5. Run RED, implement, then run:

```bash
uv run pytest -q \
  tests/services/test_regeneration_publisher.py \
  tests/services/test_regeneration_publisher_lifespan.py \
  tests/services/test_notion_versioned_homework.py
RUN_DB_INTEGRATION=1 uv run pytest -q tests/integration/test_regeneration_publication_claims.py
```

6. Commit: `feat(regeneration): publish durable versioned homework pages`

---

## Task 9: Regeneration API, Schemas, Reports, and Feature Gate

**Lane:** I, Wave 3; starts after Task 8

**Owns:**

- Create `app/schemas/regeneration.py`
- Create `app/api/v1/regeneration.py`
- Modify `app/api/v1/__init__.py`
- Create `tests/schemas/test_regeneration_schemas.py`
- Create `tests/api/test_regeneration_api.py`
- Create `tests/api/test_regeneration_reports.py`
- Create `tests/api/test_regeneration_feature_flag.py`

**Must not touch:** internal service semantics, publisher loop, legacy Fleet endpoints, or web files.

### Endpoints

Mount under `/api/v1/regeneration`:

- `GET /eligible` — filterable eligible sources and current/latest version per language;
- `POST /phase-plan` — dependency closure and broken-edge warning preview;
- `POST /estimate` — read-only target/cost/preflight estimate;
- `POST /campaigns` — create immutable campaign/targets;
- `GET /campaigns` and `GET /campaigns/{campaign_id}` — list/detail/report;
- `POST /campaigns/{campaign_id}/canary` — preflight and launch canaries;
- `POST /campaigns/{campaign_id}/approve` — one human gate;
- `POST /campaigns/{campaign_id}/reject`;
- `POST /campaigns/{campaign_id}/cancel`;
- `POST /targets/{target_id}/retry-generation`;
- `POST /targets/{target_id}/retry-publication`;
- `POST /targets/{target_id}/abandon`.

Every route is unavailable with HTTP 404 when `REGENERATION_ENABLED=false`, so the hidden feature cannot be mutated by a stale UI. State conflicts return 409 with a human-readable reason. Invalid exclusion acknowledgement returns 422. Preflight failures return one structured 409 response containing every affected lesson. Repeated idempotent operations return the current resource, not a duplicate or generic error.

Campaign detail includes:

- immutable requested/expanded/excluded phase plan and extraction choice;
- estimate and actual `AgentUsage` cost from revision jobs only;
- canary content/download job IDs and copied/regenerated provenance;
- judge/solver status counts;
- per-target source version, revision job, generation state, publication state, version, page link, attempts, errors, terminal reason;
- explicit buckets `published`, `publication_pending`, `publication_failed`, `generation_failed`, `abandoned`;
- human-readable reason text for every failure/abandonment.

Do not expose a prompt-set selector or a per-target publication approval endpoint.

### TDD steps

1. Write schema tests for stable JSON shape and exclusion validation.
2. Write API tests with service fakes for every route, idempotency, 404 flag-off behavior, 409 state conflicts, aggregated preflight failures, and single-target approval.
3. Write report tests for all buckets, soft judge counts, copied provenance, actual cost isolation, publication history, and human-readable reasons.
4. Run RED, implement the thin router/schemas, then run:

```bash
uv run pytest -q \
  tests/schemas/test_regeneration_schemas.py \
  tests/api/test_regeneration_api.py \
  tests/api/test_regeneration_reports.py \
  tests/api/test_regeneration_feature_flag.py
```

5. Commit: `feat(api): expose versioned regeneration campaigns`

---

## Task 10: Wire the Regeneration UI to the Real API

**Lane:** J, Wave 4; starts after Task 9

**Owns:**

- Modify `web/src/lib/types.ts`
- Modify `web/src/lib/api.ts`
- Modify `web/src/routes/regeneration.tsx`
- Modify `web/src/components/regeneration/regeneration-wizard.tsx`
- Modify `web/src/components/regeneration/campaign-list.tsx`
- Modify `web/src/components/regeneration/canary-review.tsx`
- Modify `web/src/components/regeneration/campaign-report.tsx`
- Create `web/src/lib/regeneration-api.test.ts`
- Extend `web/src/lib/regeneration-state.test.ts`

**Must not touch:** backend files or unrelated Fleet UI.

Replace fixtures with exact Task 9 schemas and TanStack Query calls. Keep server state authoritative; disable duplicate mutations while pending but rely on backend idempotency. Poll campaign detail while canary generation, bulk generation, publication, or retry is active. Use existing job detail/download links to inspect complete canary revisions.

Required behavior:

- feature flag hides navigation and route;
- create/estimate are visibly no-spend/no-publish steps;
- launch button says `Generate canary`;
- canary screen makes judge/solver warnings and actual cost prominent;
- approve copy says remaining lessons generate and successful versions publish automatically;
- reject/cancel confirmations say no existing Notion version is deleted;
- publication retry explicitly says `No Gemini call`;
- abandonment requires a typed reason and explains that a reserved version may remain unused;
- one-target campaign uses one approval action, no empty bulk gate;
- all API reasons are rendered in plain language.

### TDD steps

1. Write API serialization tests for every endpoint and error shape.
2. Extend state tests for polling decisions, disabled mutations, report buckets, one-target copy, and flag-off routing.
3. Run RED, wire the components, then run:

```bash
cd web
npm test
npm run build
npm run lint
```

4. Commit: `feat(web): wire versioned regeneration campaigns`

---

## Task 11: End-to-End Acceptance, Regression Gate, and Operator Documentation

**Lane:** K, Wave 4; starts after Task 10

**Owns:**

- Create `tests/integration/test_regeneration_e2e.py`
- Create `tests/integration/test_regeneration_failure_e2e.py`
- Create `docs/runbooks/versioned-homework-regeneration.md`
- Modify `.env.example`
- Modify `README.md` only to link the runbook and describe the disabled flag
- Add a worklog entry only if repository policy requires it at this local completion point

**Must not:** enable the feature, call live Notion/Gemini, deploy, push, or change prompt files.

### Fake end-to-end acceptance

Build a deterministic fake provider and fake Notion client and prove:

1. Start with an existing UZ V1 `Homework` job/page.
2. Create a selective campaign and prove estimate/create makes no model or Notion call.
3. Generate a complete canary revision with a mixture of copied and regenerated phases.
4. Exercise judge unavailable retry/soft status, refusal, major regeneration budget, `major_shipped`, `major_regen_failed`, and hard `mismatch_blocked` in focused scenarios; confirm behavior matches existing pipeline tests.
5. Prove no Notion call before approval.
6. Approve once, publish canary `Homework V2`, release remaining jobs once, and auto-publish their successful V2 pages.
7. Run a later campaign sourced from the published V2 and publish UZ V3.
8. Independently publish RU V2 for the same TOC entry.
9. Assert V1 and earlier version pages were never cleared, rewritten, renamed, or deleted.
10. Inject timeout after remote V3 page creation and prove marker adoption yields one page and one reserved version.
11. Inject generation and publication failures; retry each through its correct path; prove publication retry makes no model call.
12. Cancel at every nonterminal target state using the Task 7 table and prove no terminal campaign leaves nonterminal targets.
13. Prove actual campaign cost is exactly the successful revision `AgentUsage` sum and copied phases add zero.
14. Prove normal Fleet generation, batch adoption, archive retry, teacher-material archival, and book TOC enrichment remain unchanged and never see revision jobs.

### Runbook

Document in plain operator language:

- V1 is preserved; regenerations create V2/V3 pages;
- current deployed prompts are always used;
- how phase cascade/exclusion and extraction work;
- canary is the only review gate;
- what soft judge warnings versus hard generation failures mean;
- how to retry generation, retry publication, or abandon;
- feature/publisher flags and head-process ownership;
- flag-off deployment verification;
- no production enablement or sample campaign without a separate approval;
- rollback: turn both flags off; existing V1/V2/V3 pages remain untouched.

### Verification steps

1. Run the new E2E tests with fakes:

```bash
RUN_DB_INTEGRATION=1 REGENERATION_ENABLED=true NOTION_ENABLED=false \
  uv run pytest -q \
  tests/integration/test_regeneration_e2e.py \
  tests/integration/test_regeneration_failure_e2e.py
```

2. Run the complete focused backend suite:

```bash
uv run pytest -q \
  tests/models/test_regeneration_models.py \
  tests/migrations/test_regeneration_schema.py \
  tests/repositories/test_regeneration_repositories.py \
  tests/repositories/test_regeneration_fleet_isolation.py \
  tests/services/test_regeneration_planner.py \
  tests/services/test_regeneration_states.py \
  tests/services/test_regeneration_discovery.py \
  tests/services/test_regeneration_estimator.py \
  tests/services/test_regeneration_snapshot.py \
  tests/services/test_regeneration_pipeline.py \
  tests/services/test_regeneration_archive_isolation.py \
  tests/services/test_notion_versioned_homework.py \
  tests/services/test_regeneration_campaign.py \
  tests/services/test_regeneration_publisher.py \
  tests/services/test_regeneration_publisher_lifespan.py \
  tests/api/test_regeneration_archive_isolation.py \
  tests/api/test_regeneration_book_delete.py \
  tests/api/test_regeneration_api.py \
  tests/api/test_regeneration_reports.py \
  tests/api/test_regeneration_feature_flag.py
```

3. Run relevant unchanged regression suites:

```bash
uv run pytest -q tests/services tests/repositories tests/api
```

4. Run database integration tests and compare any failure to the same command on the untouched execution base. Do not report inherited base rot as a new pass or silently dismiss a new failure:

```bash
RUN_DB_INTEGRATION=1 uv run pytest -q tests/integration
```

5. Run frontend verification:

```bash
cd web
npm test
npm run build
npm run lint
```

6. Verify migration shape and tree cleanliness:

```bash
uv run alembic heads
git status --short
git diff --check <execution-base-sha>...HEAD
```

7. Commit: `test(regeneration): prove versioned campaign workflow`

---

## 4. Final Review and Stop Gate

After Task 11:

1. Dispatch a fresh external Claude Opus 5 whole-branch reviewer with the approved design, this plan, execution-base SHA, complete diff, test evidence, and known baseline failures.
2. Require two explicit verdicts: `SPEC_COMPLIANT` and `CODE_QUALITY_APPROVED`. Any blocker returns to the owning task lane and is re-reviewed.
3. The operations manager independently runs the verification commands above and checks:
   - one Alembic head;
   - no edits to PR-owned branches/worktrees;
   - no prompt-tree changes;
   - no broad outbox or prompt-set registry accidentally included;
   - feature and publisher flags remain false;
   - no live credentials or generated secrets in the diff;
   - every revision exclusion query and archive caller has a focused test;
   - all failures are classified as new, inherited, or test pollution with isolation evidence.
4. Stop and present the local preview branch, commit sequence, reviewer reports, and exact verification evidence to the user.

Do **not** push, open/update a PR, merge, deploy, enable flags, or run a paid/live sample at this gate. Those are separate user decisions.

## 5. Expected Local Commit Sequence

1. `feat(regeneration): add campaign and revision schema`
2. `feat(regeneration): add phase planning and state rules`
3. `feat(regeneration): add immutable versioned Notion writer`
4. `feat(web): add regeneration workflow shell`
5. `feat(regeneration): add source discovery and estimates`
6. `feat(regeneration): create isolated complete revision jobs`
7. `feat(regeneration): orchestrate canary and campaign lifecycle`
8. `feat(regeneration): publish durable versioned homework pages`
9. `feat(api): expose versioned regeneration campaigns`
10. `feat(web): wire versioned regeneration campaigns`
11. `test(regeneration): prove versioned campaign workflow`

This sequence is a review history, not permission to merge. The feature remains dormant behind `REGENERATION_ENABLED=false` and `REGENERATION_PUBLISHER_ENABLED=false` until a separate rollout approval.
