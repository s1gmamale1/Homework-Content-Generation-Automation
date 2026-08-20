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
8. Always cut worktrees from the fully qualified remote ref, for example `git worktree add <explicit-path> -b <task-branch> origin/Nggaev-v2`; never cut from the stale local `Nggaev-v2` branch.

Collision snapshot at planning time:

- plan worktree: `plan/selective-regeneration-campaign@b2025cd`;
- implementation base last inspected: `origin/Nggaev-v2@a955cfebbf5bc8456974a9f40a95fc4852c866e4`;
- `feat/prompt-set-registry@b1f54d6` / PR #136 is not a dependency and must remain untouched;
- `feat/selective-regeneration-preview@a5666f6` is the shelved broad-outbox implementation, not an implementation base;
- `feat/selective-regeneration-integration@f2b325f` and its Claude worktree remain untouched;
- `origin/pigganigeon@e0e499d` is the separate current-prompt review lane and is not merged by this feature;
- PRs #117/#118 overlap pipeline/prompt/phase paths. They are not dependencies and are not integrated into the preview branch. Cut the preview from the then-current `origin/Nggaev-v2`; if either PR has merged before that cut, re-gate its actual merged diff and adapt the plan before dispatch. If either merges after the cut, finish and test the feature on its isolated preview base first; then, only in a separate integration worktree and only after user approval, rebase onto the newer remote base, resolve the concrete conflicts, and rerun the complete gate. Do not let a parallel agent edit either PR branch.

The migration number `0063` below is provisional. The schema agent must derive the filename and `down_revision` from the single Alembic head on the verified execution base. If the head is no longer `0062`, rename the migration rather than creating a second head.

The preview branch is created from `origin/Nggaev-v2`, receives the approved specification/plan commits, and stays in its own worktree for the entire build. No task branch or preview branch is merged into `Nggaev-v2`. After the feature is completely assembled, the **preview worktree itself** must pass migration, fake E2E, focused regression, full regression, frontend build/lint, and whole-branch Opus 5 review. Even then, integration into `Nggaev-v2` is a separate explicit user decision.

### Disposable PostgreSQL test database

Database-backed claims may not be accepted from skipped tests. Parallel lanes never share a writable database. The operations manager provisions these explicit localhost-only disposable databases before their owning tasks: `hcga_regen_lane_a_test`, `hcga_regen_lane_e_test`, `hcga_regen_lane_f_test`, `hcga_regen_lane_g_test`, `hcga_regen_lane_h_test`, and final integration database `hcga_regeneration_preview_test`. The command shape below is shown for the final database and is repeated with the exact owning lane name:

```bash
psql -h 127.0.0.1 -U macmini5 -d postgres -Atc \
  "SELECT datname FROM pg_database WHERE datname='hcga_regeneration_preview_test'"
createdb -h 127.0.0.1 -U macmini5 hcga_regeneration_preview_test
export DATABASE_URL='postgresql+asyncpg://macmini5@127.0.0.1:5432/hcga_regeneration_preview_test'
export RUN_DB_INTEGRATION=1
export REGEN_REQUIRE_DB=1
uv run alembic upgrade head
```

If any database already exists, stop and establish ownership; do not drop or reuse an unknown database. If local PostgreSQL or the role is unavailable, the DB-owning task is blocked—do not waive the database tests and report green. Every database command must pass the repository's localhost/non-production guard. Agents receive only their lane's explicit `DATABASE_URL`. At final cleanup, only after re-resolving and validating each exact database name and host, matching explicit `dropdb -h 127.0.0.1 -U macmini5 <exact-name>` commands are allowed; preserve the final preview database until the user has received the test evidence.

## 2. External-Agent Operating Protocol

For every task:

1. The operations manager gives one Claude Opus 5 controller the exact task section, approved design, allowed files, forbidden files, base SHA, and test commands.
2. The controller uses the repository's `superpowers:subagent-driven-development` workflow: a fresh implementation subagent follows RED → GREEN → REFACTOR; a different Claude Opus 5 subagent reviews specification compliance and code quality.
3. Agents may not push, merge, deploy, use live credentials, or alter another worktree.
4. The controller commits only the task-owned files after its tests pass.
5. The operations manager independently inspects the diff, verifies no out-of-scope files changed, runs the named task tests, and records the reviewer verdict.
6. Failed review returns to the same task branch. A task is not integrated because an agent merely says it is complete.
7. Integration uses cherry-picks into a local preview branch in the dependency order below. Resolve overlaps deliberately; never accept a blanket conflict resolution.
8. Frontend worktree setup runs `cd web && npm ci` against the committed lockfile before frontend tests; `web/node_modules` is not shared or assumed to exist.

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
- Create `app/schemas/regeneration_contract.py`
- Create `app/repositories/regeneration_campaigns.py`
- Create `app/repositories/regeneration_targets.py`
- Modify `app/models/homework_job.py`
- Modify `app/models/phase_output.py`
- Modify `app/models/__init__.py`
- Modify `app/config.py`
- Modify `tests/conftest.py`
- Create the next migration under `alembic/versions/`
- Create `tests/models/test_regeneration_models.py`
- Create `tests/migrations/test_regeneration_schema.py`
- Create `tests/integration/test_regeneration_constraints.py`

**Must not touch:** orchestration services, API, `pipeline.py`, Notion code, or web files.

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
- cancellation convergence fields `abandon_requested_at` and `abandon_requested_reason`, used when a running revision must finish cancellation before the target can become terminal.

`HomeworkJob` adds nullable `revision_of_job_id` with `ON DELETE RESTRICT` and nullable unique `regeneration_target_id` with `ON DELETE RESTRICT`. A check requires the two columns to be both null or both non-null. A second check requires `batch_id IS NULL` when `revision_of_job_id IS NOT NULL`.

`PhaseOutput` adds nullable `copied_from_phase_output_id` with `ON DELETE RESTRICT`.
While editing the model, correct the existing `judge_status` comment to include the already-emitted soft value `refused`; this is documentation parity only and does not change its database type or pipeline behavior.

Database invariants:

- unique target `(campaign_id, toc_entry_id, output_language)`;
- partial unique active lineage `(toc_entry_id, output_language) WHERE terminal_at IS NULL`;
- unique `(toc_entry_id, output_language, publication_version)` for non-null versions;
- unique `homework_jobs.regeneration_target_id`;
- `published` requires non-null version, page ID, publication release, and terminal time;
- `abandoned` requires terminal time; all other target states require `terminal_at IS NULL`;
- publication states require `publication_released_at IS NOT NULL`;
- a PostgreSQL trigger rejects transition into `publication_pending`, `publishing`, or `published` unless the owning campaign has `approved_at IS NOT NULL` and is not rejected/cancelled.
- `RegenerationTarget.toc_entry_id` uses explicit `ON DELETE RESTRICT`; no implicit TOC cascade may erase or partially detach audit history.

Do not add `revision_job_id` to the target. The authoritative one-to-one link is `HomeworkJob.regeneration_target_id`; the target's revision job is read through that unique relationship, avoiding a cyclic pair of mutable foreign keys.

`app/schemas/regeneration_contract.py` is the single owner of the immutable `LaunchContract` Pydantic model used by Tasks 5–7. It contains content/extract/judge/solver provider, model and transport values, output language, session-limit strategy, solver toggle, and any existing launch option required by `HomeworkJob`. It validates through the same production helpers as ordinary launches and serializes to the campaign JSON column; no later task defines a second launch-contract type.

The two repositories expose only common primitives needed by later lanes: `get_campaign_for_update`, `get_target_for_update`, `get_target_by_revision_job`, `revision_job_for_target`, `create_campaign`, `create_target`, and fenced status/claim updates. Task 5 creates separate read-only source queries; Task 6 uses these common target primitives; Tasks 7–8 extend the repositories sequentially.

Configuration is declared here so later lanes share one contract: `regeneration_enabled=false`, `regeneration_publisher_enabled=false`, publisher interval/lease/attempt/backoff settings, and `regeneration_launch_wave_size` / `regeneration_launch_wave_interval_seconds` conservative stagger defaults. No task enables either flag.

Add a test-only collection guard in `tests/conftest.py`: when `REGEN_REQUIRE_DB=1`, collection fails immediately unless `RUN_DB_INTEGRATION=1` and an explicit localhost, non-production `DATABASE_URL` are present. The lane and final integration commands set both flags; this converts an accidental skip into a hard failure.

### TDD steps

1. Write model tests asserting the new columns, relationships, check names, partial index predicate, and restrictive FK semantics.
2. Write migration tests that upgrade from the current head, inspect every table/index/check/FK/trigger, then downgrade cleanly. Because this repository has no trigger precedent, also assert the trigger function body, trigger timing/events, failed direct SQL before approval, allowed SQL after approval, and removal of both trigger and function on downgrade. The trigger reads the owning campaign `FOR KEY SHARE` before checking `approved_at`/status, so it waits for a concurrent approval transaction rather than deciding from a racing snapshot.
3. Write PostgreSQL integration tests that race two active targets in the same language, allow UZ V2 and RU V2 independently, reject duplicate versions in one language, reject revision+batch, reject source deletion, and reject publication before approval.
4. Run the tests and record the RED failures.
5. Implement the models and migration with named constraints.
6. Run:

```bash
RUN_DB_INTEGRATION=1 uv run pytest -q tests/models/test_regeneration_models.py tests/migrations/test_regeneration_schema.py
RUN_DB_INTEGRATION=1 uv run pytest -q tests/integration/test_regeneration_constraints.py
uv run alembic heads
```

Expected: all named tests pass with **zero skips**, and `alembic heads` prints exactly one head. Run `pytest ... -ra` and fail the gate if any regeneration migration/integration test reports skipped.

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

class PhaseRowView(Protocol):
    phase_name: str
    phase_order: int
    status: str
    output_md: str | None
    content_json: dict | None

@dataclass(frozen=True)
class SnapshotValidation:
    usable: bool
    reasons: tuple[str, ...]

def validate_complete_snapshot(
    *, subject: str, rows: Collection[PhaseRowView]
) -> SnapshotValidation: ...
```

The implementation imports the canonical subject flow and `PHASE_DEPS` from the same production authority used by the pipeline. It preserves canonical order, computes transitive downstream closure, refuses unknown or unavailable phases, and requires acknowledgement only when an exclusion actually breaks an affected edge. With extraction enabled, all content phases enter the closure before exclusions.

`validate_complete_snapshot` is the single Wave-1 authority used by both Task 5 discovery and Task 6 copy/publication gates. It requires exactly one row for extract plus every canonical content phase; each row must satisfy the pipeline's resumability predicate (`status == "done"` and nonblank `output_md` or non-null `content_json`); and each phase name/order must match the current canonical sequence. It returns stable operator-facing reasons, including an explicit `source flow differs from the currently deployed flow` reason. Later lanes may add job-kind/status checks around it but may not redefine row completeness.

`regeneration_states.py` exposes pure functions for legal campaign/target transitions and `roll_up_campaign(target_statuses, approved, cancelled)` so services do not duplicate terminality rules. `generation_failed` and `publication_failed` remain nonterminal/attention-required; only `published` and `abandoned` are terminal.

### TDD steps

1. Write table-driven tests for every subject flow and graph edge.
2. Pin these measured examples: flashcards → 10/11 content phases, memory-check → 5, boss-arena → 2, reflection → 1.
3. Test deterministic ordering, duplicate inputs, invalid phases, extraction off/on, warning-backed exclusion, and unaffected exclusions.
4. Test complete-snapshot validation for missing, duplicate, failed, blank, structured, order-drifted, and flow-drifted rows; pin the stable reason codes/text.
5. Test every legal and illegal campaign/target transition and complete rollups including mixed published/abandoned/failure cases.
6. Run RED, implement the smallest pure code, then run:

```bash
uv run pytest -q tests/services/test_regeneration_planner.py tests/services/test_regeneration_states.py
```

7. Commit: `feat(regeneration): add phase planning and state rules`

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

The first block of `Homework V{n}` is a deterministic machine-readable marker containing all five fields. The stored page ID wins only after its marker is revalidated. Without it, enumerate exact-title child pages, read the candidate's first blocks, and adopt only an exact marker match. A same-title page with missing/different marker raises `VersionPageCollision`; it is never cleared or overwritten. With no candidate, create the page with the marker and the same grouped homework layout used by V1. The function is synchronous; retry/leases remain Task 8's responsibility.

Crash recovery covers the **whole page tree**, not only root-page creation. Compute a deterministic digest over the ordered phase names and markdown. Append a completion marker only after every expected child/leaf is populated. When a matching root marker exists without the matching completion digest, the writer may clear and rebuild only the child/leaf pages owned beneath that exact marked revision page, then stamp completion. It must never clear the root marker, V1, a different version, or a same-title page whose marker does not match. A retry with the matching completion digest performs no uploads or writes.

Refactor the existing V1 renderer into shared pure helpers while preserving the current `Homework` title, file attachments, nested layout, replace semantics, and call ordering byte-for-byte at the Notion-block level. Do not route V1 through marker logic.

### TDD steps

1. Write fake-client tests for marker round-trip, stored-ID revalidation/reuse, root-only crash repair, partially populated leaf repair, completion-digest idempotency, wrong-marker collision, missing-marker collision, independent UZ/RU V2 markers, and V2/V3 sibling titles.
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

Use local fixture objects declared in `regeneration-state.test.ts` and component files; Task 10 replaces them with typed API data. `regeneration-feature.ts` exports a pure `isRegenerationEnabled(env)` helper plus `IS_REGENERATION_ENABLED`, reading `(import.meta as ImportMeta & { env?: Record<string, string> }).env ?? {}` so Node's `tsx --test` runner does not throw when `import.meta.env` is absent. The route and nav item are absent when false. The backend flag remains the authoritative safety gate. The UI must say **Regenerating**, never reuse Fleet's **Generating** label.

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
npm test
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

- Create `app/repositories/regeneration_sources.py`
- Create `app/services/regeneration_discovery.py`
- Create `app/services/regeneration_estimator.py`
- Modify `app/repositories/cost.py`
- Create `tests/repositories/test_regeneration_repositories.py`
- Create `tests/services/test_regeneration_discovery.py`
- Create `tests/services/test_regeneration_estimator.py`
- Create `tests/integration/test_regeneration_source_and_version_queries.py`

**Must not touch:** `app/repositories/regeneration_campaigns.py`, `app/repositories/regeneration_targets.py`, `app/repositories/jobs.py`, `pipeline.py`, phase-output writes, legacy archive callers, API router, publisher, main lifespan, or web files.

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

Eligibility requires a done `kind="homework"` job whose rows pass Task 2's `validate_complete_snapshot`; Task 5 must import that predicate and surface its stable reasons rather than implement another completeness definition. For V3+, `resolve_default_source` chooses the highest successfully published `publication_version` for the same `(toc_entry_id, output_language)`; otherwise it chooses the latest completed non-revision V1 job in that language. Never choose an unpublished or abandoned revision. If later flow changes invalidate an old source, discovery explains that the source flow differs from the currently deployed flow.

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

This task is the sole Wave-2 owner of `app/repositories/cost.py`. In the same commit, add `HomeworkJob.revision_of_job_id IS NULL` to `section_prior_api_cost` and every ordinary rebill/dedup cost lookup, with focused tests proving revision usage is excluded from normal Fleet prior-cost warnings while still included in regeneration actual-cost queries.

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
- Create `app/services/regeneration_job_state.py`
- Modify `app/repositories/jobs.py`
- Modify `app/repositories/phase_outputs.py`
- Modify `app/repositories/regeneration_targets.py`
- Modify `app/repositories/subject_coverage.py`
- Modify `app/services/pipeline.py`
- Modify `app/services/worker.py`
- Modify `app/services/notion_archive.py` for the intrinsic revision guard
- Modify `app/api/v1/jobs.py` for synchronous archive-route conflicts and revision-aware cancel reconciliation
- Modify `app/api/v1/batch.py` for defensive sweep exclusion
- Modify `app/api/v1/books.py` for clean book-delete conflict and TOC isolation
- Modify `main.py` only to reconcile revision targets after existing startup terminal-job sweeps
- Create `tests/services/test_regeneration_snapshot.py`
- Create `tests/services/test_regeneration_pipeline.py`
- Create `tests/services/test_regeneration_archive_isolation.py`
- Create `tests/repositories/test_regeneration_fleet_isolation.py`
- Create `tests/repositories/test_regeneration_terminal_reconciliation.py`
- Create `tests/api/test_regeneration_archive_isolation.py`
- Create `tests/api/test_regeneration_book_delete.py`

**Must not touch:** Task 5 files (including `app/repositories/cost.py` and `app/repositories/regeneration_sources.py`), `app/services/agent.py` (its existing cached-extract helper already accepts both source identifiers), `app/repositories/books.py`, `app/repositories/toc_entries.py`, campaign state orchestration, publisher, new regeneration API router, config, or web files. `main.py` ownership is limited to a separate guarded reconciliation step after the existing startup sweep; Task 8 later adds the publisher lifespan sequentially.

### Snapshot service

```python
async def create_revision_job(
    session: AsyncSession, *, target_id: UUID, launch_contract: LaunchContract,
    start_offset_seconds: int = 0,
) -> HomeworkJob: ...
```

The service locks the target, returns the already-linked revision job on repeat without changing its schedule, verifies the source is still eligible, and creates a `kind="homework"`, `batch_id=NULL`, `selected_phases=NULL` revision linked to its immediate source and target. Copy `book_id`, `toc_entry_id`, `subject`, and `output_language` exactly from the immediate source job; apply provider/model/transport/role settings from the validated `LaunchContract`; and pass `start_offset_seconds` through to `jobs_repo.create` so `scheduled_at` is set atomically at first creation.

Copy exactly these `PhaseOutput` columns for each phase in `plan.copied_phases`:

- `phase_name`, `phase_order`, `prompt_hash`, `model_name`, `provider`, `output_md`;
- `tokens_input`, `tokens_output`, `status`, `error_message`, `validation_warnings`;
- `judge_status`, `solver_status`, `started_at`, `completed_at`;
- `content_json`, `authoring_mode`, `content_schema_version`, `renderer_version`;
- set `claim_token=NULL` and `copied_from_phase_output_id=source_phase.id`.

Before copying, call Task 2's `validate_complete_snapshot`; do not duplicate its predicate. For each source row already validated there, use its verified canonical `phase_order`; never silently renumber it. Do not copy `id`, `job_id`, or the source claim token. Seed copied rows at that verified canonical `phase_order`; leave regenerated rows absent. When extraction is copied, call the existing zero-cost `record_cached_lesson_extract` path with source job/phase provenance without changing `agent.py`. Do not clone any paid `AgentUsage` row for any copied phase.

The ordinary pipeline then sees `selected_phases=NULL`, skips seeded done rows, creates missing rows, and preserves existing judge/solver behavior unchanged. Pipeline completion must **not** call legacy `archive_job` for a revision. Soft judge states remain publishable exactly as specified; `solver_status="mismatch_blocked"` remains a hard job failure.

### Terminal job → target reconciliation

`regeneration_job_state.py` owns one idempotent function and one repair sweep:

```python
async def reconcile_revision_job(session: AsyncSession, job_id: UUID) -> None: ...
async def reconcile_terminal_revision_jobs(session: AsyncSession) -> int: ...
```

It joins the job, target, and campaign under row locks and maps current job truth:

- `done` + complete snapshot + campaign not approved → `awaiting_canary_approval`;
- `done` + complete snapshot + approved/released campaign → `publication_pending`;
- `failed` → `generation_failed` unless `abandon_requested_at` is set, then terminal `abandoned`;
- `cancelled` → `generation_failed` unless `abandon_requested_at` is set, then terminal `abandoned`;
- `pending`, `running`, or `cancelling` remain `generating`.

At claim time, stash whether the claimed `HomeworkJob.revision_of_job_id` is non-null beside the existing lease handoff; direct-call tests pass the same explicit marker. This lets ordinary jobs short-circuit without opening a reconciliation session.

In `_execute_job`'s `finally`, preserve the existing cleanup order first: remove local running/lease bookkeeping, cancel and settle the heartbeat, and release `self._slots`. Only then, as the **last operation**, reconcile when the stashed marker says this is a revision. Mirror the existing shutdown-safe pattern: run the own-session reconciliation through `asyncio.shield`, guard it with `except BaseException` so a second shutdown cancellation cannot bypass cleanup or escape as a new failure, and never re-raise a reconciliation error. This placement covers normal `pipeline.run` return, hard-return failure, raised `CancelWonSignal`, `LeaseLostSignal`, worker crash/`_mark_failed`, and the `SessionLimitPause` / `SlotSaturation` branches whose requeue can finalize a concurrent cancellation without leaking a semaphore permit or heartbeat. API cancel of a pending revision also reconciles after its job transaction.

The repair sweep never shares a transaction with `fail_exhausted_pending_jobs`, `reclaim_stale_cancelling`, worker-registry maintenance, or startup's critical reconcile. In both worker maintenance and `main._reconcile_on_startup`, run `reconcile_terminal_revision_jobs` as a subsequent named step with its own session/transaction and its own broad logging guard. If the regeneration table/migration is temporarily unavailable, ordinary stuck-job reclaim and process startup continue unaffected. The bulk function repairs a crash between a job terminal commit and its target update; Task 7 invokes it before campaign actions/reports, and Task 8 invokes it at the start of each publisher pass.

Before the target becomes publication-pending, assert a complete terminal row exists for every required canonical phase and every phase is usable under the existing structured-content rules.

### Fleet and archive isolation

Every normal Fleet query must explicitly include `HomeworkJob.revision_of_job_id IS NULL`, including:

- `find_active_for_section`;
- `latest_for_section`;
- `latest_by_section`;
- batch adoption and resume selection;
- batch status/rollup paths that query jobs outside a batch join;
- book TOC status enrichment;
- `subject_coverage.job_status_by_book` used by the dashboard;
- `section_prior_api_cost` and normal dedup/rebill warnings.

Task 5 owns the `section_prior_api_cost` implementation and its focused tests; Task 6's integration test verifies the combined checkpoint behavior without editing `cost.py`.

Revision jobs remain claimable through the generic worker queue and available by explicit job ID for pipeline, phase, SSE, download, cancellation, and safe retry reuse.

When legacy archival is enabled and `notion_archive.archive_job` loads a job, reject any `revision_of_job_id IS NOT NULL` before resolving lesson/page identity or constructing a Notion client: persist deterministic skip reason `regeneration revision: use versioned publisher` and return. This guard applies even with `force=True` or a claim token. The pipeline completion branch already avoids calling it for revisions; when Notion is globally disabled, retain the current no-DB-work early return. `POST /jobs/{id}/retry-archive` and the force route must synchronously return 409 for revision jobs. Batch rearchive selection excludes them defensively.

Book deletion, `DELETE /books/{book_id}/toc/{entry_id}`, and TOC re-extraction/replacement with any regeneration campaign/target/revision history return controlled 409 responses in `app/api/v1/books.py` before any repository delete; do not leak a raw restrictive-FK error. The underlying `books_repo.delete` and `toc_repo.delete` retain their restrictive-FK behavior and are not modified by this task; DB tests assert they reject when called without the route guard. Critically, `jobs_repo.list_for_book` must **not** gain a revision filter: `/toc/retry` relies on it to see revision history and return the structured 409 before replacement.

### TDD steps

1. Write snapshot tests that fail first and pin source `book_id`/TOC/subject/language provenance, launch-contract fields, `start_offset_seconds`, the full copied-column set, exact `_done_phase_md` predicate, canonical-order validation/refusal, idempotent job creation without re-stagger, missing-source refusal, zero cloned usages, copied extract marker, and complete-snapshot validation.
2. Write pipeline tests using fake agent/judge/solver for mixed copied/regenerated flows, soft judge statuses, hard failure, solver blocked, canary hold, and approved publication release.
3. Write one test for every Fleet query/caller listed above, including `subject_coverage.job_status_by_book`. Do not accept one indirect test as coverage for several SQL functions.
4. Write archive tests proving `force=True`, automatic claim token, retry route, force route, and batch sweep all cannot read or write Notion for a revision.
5. Write clean 409 tests for book delete, TOC-entry delete, TOC retry/re-extract, and repository deletion.
6. Write a test for every worker exit/terminal writer: pipeline hard-return failure, successful return, worker crash retry/terminal failure, raised `CancelWonSignal`, `SessionLimitPause` cancel-wins race, `SlotSaturation` cancel-wins race, `LeaseLostSignal`, pending cancel, running cancel finalization, stale-cancelling sweep, exhausted-pending sweep, and crash-repair bulk reconciliation. Assert ordinary jobs open no reconciliation session; heartbeat settles and semaphore releases before reconciliation; a second shutdown cancel during reconciliation cannot leak either resource; no terminal job leaves its target `generating`; reconciliation failure cannot abort critical sweeps/startup; and no active-lineage lock is permanently orphaned.
7. Run RED, implement, then run:

```bash
uv run pytest -q \
  tests/services/test_regeneration_snapshot.py \
  tests/services/test_regeneration_pipeline.py \
  tests/services/test_regeneration_archive_isolation.py \
  tests/repositories/test_regeneration_fleet_isolation.py \
  tests/repositories/test_regeneration_terminal_reconciliation.py \
  tests/api/test_regeneration_archive_isolation.py \
  tests/api/test_regeneration_book_delete.py \
  tests/services/test_pipeline_flow1.py \
  tests/api/test_batch_resume_endpoint.py \
  tests/api/test_books_kind_status.py \
  tests/api/test_never_pay_twice.py
```

8. Commit: `feat(regeneration): create isolated complete revision jobs`

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

Creation resolves and stores each target's source and phase plan, rejects any active same-language lineage, chooses deterministic canaries from a stable `(book_id, toc order, language, target id)` order, and creates no jobs or external calls. A draft intentionally holds the active-lineage lock until it is launched or cancelled; draft cancellation is supported and prominent, with no automatic expiry in v1. Launch preflights all destinations once, then creates only canary revision jobs. Non-canary targets remain `planned` with no job row.

Approval locks the campaign and targets, sets `approved_at` once, releases successful canaries to `publication_pending`, creates all remaining revision jobs exactly once, and moves them to `generating`. Repeated approval returns the current campaign without duplicate jobs. A one-target campaign uses this same approval but has no separate bulk gate.

Bulk release must reuse `app.services.launch_stagger.stagger_offset`. Order only the jobs actually created/released, compute each offset from `regeneration_launch_wave_size` and `regeneration_launch_wave_interval_seconds`, and pass it as `start_offset_seconds` to Task 6's `create_revision_job`; do not update `scheduled_at` afterward. Compute the wave count inside the campaign service; do not import the private router helper `app.api.v1.batch._stagger_summary`. The response/report records wave count and final scheduled offset. Tests pin that a campaign larger than one wave is decorrelated, a one-target canary starts immediately, repeated approval does not re-stagger existing jobs, and zero-valued knobs are the explicit kill switch. Revision jobs intentionally have no normal batch pause; campaign cancel is their authoritative bulk stop control and must visit every nonterminal target/job. Running-job cancellation may converge on the next worker heartbeat rather than killing an in-process task immediately; the UI/report stays nonterminal until reconciliation confirms it. Because batchless queue fairness lanes by `book_id`, campaigns spanning many books can occupy many lanes; the regeneration stagger knobs are the explicit shaping control.

Reject-before-approval transitions every canary revision target and every planned target to terminal `abandoned`, sets a reason distinguishing rejected canary, creates no version, and never publishes.

Use this complete cancel/abandon table:

| Current target state | Reject canary | Cancel approved campaign | Explicit abandon |
|---|---|---|---|
| `planned` | `abandoned`, no job/version | `abandoned`, no job/version | `abandoned` |
| `generating` | request safe job cancellation, then `abandoned` when terminal | same | same |
| `awaiting_canary_approval` | `abandoned`, no version | not reachable after approval | `abandoned` |
| `publication_pending` | not reachable before approval | `abandoned`, preserve reserved version if any | `abandoned` |
| `publishing` | not reachable before approval | do not revoke unknown remote request; set abandon intent and let claim resolve to `published` or `abandoned` after a failed request | same safe rule |
| `generation_failed` | `abandoned` | `abandoned` with campaign-cancel reason | `abandoned` |
| `publication_failed` | not reachable before approval | `abandoned` with campaign-cancel reason, preserve version | `abandoned`, preserve version |
| `published` | unchanged | unchanged | illegal |
| `abandoned` | unchanged | unchanged | idempotent |

Campaign cancellation first stamps `abandon_requested_at/reason` on every generating revision, then uses the existing safe job-cancellation path. Terminal reconciliation completes those targets as `abandoned`; a crash or delayed running cancellation is repaired by the reconciler. Campaign cancellation is not terminal until every target is `published` or `abandoned`. It must never leave a nonterminal target hidden behind a terminal campaign. `completed` means all published; `completed_with_abandonments` means at least one abandoned; `cancelled` is used only when cancellation produced no published target and all targets are abandoned. Reports preserve reasons.

Generation retry creates/requeues through the safe existing job retry semantics without duplicating snapshots or changing the phase plan. Publication retry clears backoff/claim error and moves the same target/version to `publication_pending`; it never calls a model.

### TDD steps

1. Write exhaustive parameterized tests for every row of the table and for rollup outcomes.
2. Test deterministic canary selection, all-target preflight before spend, only-canary job creation, one-target flow, staggered bulk scheduling, campaign-wide cancel without a batch, idempotent launch/approve/reject/cancel/retry, and no dangling nonterminal targets.
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

At the beginning of every `run_once`, call `reconcile_terminal_revision_jobs` before selecting publication work, so a crash between a job terminal write and target update self-heals even without an API read. For each claim:

1. Reload and validate campaign approval, target claim, complete revision snapshot, source language, and Notion destination.
2. Reserve/reuse the version.
3. While the async DB session is open, load siblings with `toc_repo.titles_for_subject_grade(session, subject=job.subject, grade=book.grade)` and compute the collision-safe `lesson_title = resolve_lesson_title(section, siblings)`. Also resolve the language-aware subject-page ID and copy all scalar inputs needed remotely; close the DB session before Notion I/O. In one `asyncio.to_thread` call, resolve the Lesson Topic parent: reuse `toc_entries.notion_lesson_page_id` when present, otherwise synchronously `find_or_create` the existing `Generated Homeworks` container and then `find_or_create` the computed Lesson Topic title. No `NotionClientWrapper`, `find_or_create`, child listing, block read, upload, append, or clear call may run on the event loop. A crash before stamping is safe because the same collision-aware title path adopts the parent on retry. In a new fenced DB transaction, persist only `toc_entries.notion_lesson_page_id`.
4. Build the full `phase_md` mapping before version-page remote I/O.
5. Call `write_or_adopt_versioned_homework` in a worker thread with the resolved lesson page, stored version-page ID, and immutable marker.
6. On success, compare claim token and set page ID, `published`, `terminal_at`; then roll up the campaign.
7. On transient failure, preserve page/version identity and either calculate bounded exponential backoff into retryable `publication_failed`, or transition to terminal `abandoned` when campaign cancellation already stamped an abandon intent.
8. On collision or exhausted automatic attempts, leave `publication_failed` for operator retry/abandonment.

The versioned publisher must never write or clear `toc_entries.notion_homework_page_id`, `toc_entries.notion_archived_job_id`, `homework_jobs.notion_archived_at`, or `homework_jobs.notion_skip_reason`. Those columns remain V1/legacy-archive authority. The only TOC pointer this feature may backfill is the shared `notion_lesson_page_id`; version-page identity lives on `RegenerationTarget.notion_page_id`.

Configuration declared by Task 1 is consumed here:

- `regeneration_enabled: bool = False` gates the feature;
- `regeneration_publisher_enabled: bool = False` gates the loop separately;
- bounded interval, lease, automatic attempts, and backoff settings have conservative defaults;
- production enables the publisher only on the designated head/API process, but the claim protocol is safe if two processes accidentally run it.

`main.py` starts the loop only when both flags are true, after `events_bus.start_listener()` and beside the embedded worker startup, then stops/signals/awaits it using the same shutdown shape as the worker. It must not start under test unless explicitly enabled. The security-sensitive auth, vault, prompt, database-reconcile, version-floor, and listener ordering before it remains unchanged.

### TDD steps

1. Write unit tests for no-work, existing Lesson Topic reuse, missing-parent create/adopt/stamp, failed parent resolution, successful V2/V3, language-independent V2, stale claim fencing, transient backoff, exhausted retries, collision, no model call, V1 pointer non-mutation, and campaign rollup.
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

In `app/api/v1/__init__.py`, include `regeneration.router` with `dependencies=[Depends(get_current_user)]`, exactly like books/batch/jobs. Do not rely on route location or the feature flag for authentication. API tests must prove an anonymous request cannot read eligible sources, estimates, campaigns, or reports and cannot invoke any mutation; then prove a valid operator token reaches the feature/state gate. Do not use the strict SA-key-only dependency for this operator workflow.

Every route is unavailable with HTTP 404 when `REGENERATION_ENABLED=false`, so the hidden feature cannot be mutated by a stale UI. State conflicts return 409 with a human-readable reason. Invalid exclusion acknowledgement returns 422. Preflight failures return one structured 409 response containing every affected lesson. Repeated idempotent operations return the current resource, not a duplicate or generic error.

Campaign detail includes:

- immutable requested/expanded/excluded phase plan and extraction choice;
- estimate and actual `AgentUsage` cost from revision jobs only;
- canary content/download job IDs and copied/regenerated provenance;
- judge/solver status counts;
- per-target source version, revision job, generation state, publication state, version, page link, attempts, errors, terminal reason;
- explicit buckets `published`, `publication_pending`, `publication_failed`, `generation_failed`, `abandoned`;
- human-readable reason text for every failure/abandonment.

Before campaign detail/report and before every state-changing campaign action, run terminal revision reconciliation in the same request transaction. This is the operator-facing crash-repair path and prevents a terminal job from remaining visibly or logically `generating` if the worker died between commits.

Do not expose a prompt-set selector or a per-target publication approval endpoint.

### TDD steps

1. Write schema tests for stable JSON shape and exclusion validation.
2. Write API tests with service fakes for every route, router-level authentication on reads and writes, idempotency, 404 flag-off behavior, 409 state conflicts, aggregated preflight failures, and single-target approval.
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
- Modify `README.md`
- Modify `docs/HOW_IT_WORKS.md`
- Modify `docs/CODE_MAP.md`
- Modify `docs/DATABASE.md`
- Modify `docs/DEPLOY.md`
- Modify `docs/memory/MASTER_MEMORY.md`
- Modify `docs/memory/INDEX.md`
- Modify `docs/memory/ROADMAP.md`
- Move this plan with `git mv` to `docs/superpowers/plans/shipped/2026-08-20-versioned-homework-regeneration-implementation.md` only after every implementation and acceptance test passes

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

Repository completion documentation is mandatory, not conditional: write the indexed worklog, close the shipped roadmap item, update all live architecture/database/deploy references above, and history-preserve the plan move. These documentation changes describe the code as shipped-but-flag-off; they do not authorize enablement or merge.

Immediately before committing the worklog, re-read `docs/memory/INDEX.md`, choose the next unused counter, and apply the same number to both the INDEX row and the `docs/memory/MASTER_MEMORY.md` heading. Do not reserve the number earlier in a parallel lane.

The runbook states both cost-cap scopes precisely: revision jobs have `batch_id=NULL`, so they do **not** count toward any individual batch cost cap; their API usage does count toward the fleet-daily API cap (the fleet query intentionally has no job-kind filter), so a large regeneration can pause ordinary API batches. Operators must budget and stage the campaign accordingly.

The runbook also states that regeneration history deliberately makes the containing book/TOC entry undeletable through current delete routes until a future explicit child-first purge exists. Cancellation/abandonment does not erase audit history.

Automated fake-provider acceptance is the implementation gate. CLAUDE.md's real-generation acceptance remains explicitly outstanding and is deferred to the separately authorized bounded rollout sample; neither local suite success nor branch review claims to satisfy that paid/live gate.

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

Run the regeneration DB/migration subsets with `-ra` and require zero skipped regeneration tests. A skipped constraint, trigger, lease, race, or migration test fails this gate even if pytest exits zero.

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
