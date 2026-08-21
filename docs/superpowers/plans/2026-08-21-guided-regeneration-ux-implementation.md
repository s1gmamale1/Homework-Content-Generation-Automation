# Guided Regeneration UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Execution rule:** External Claude Code controllers and their implementation/review subagents must use `claude-opus-5`. Codex is the operations manager: it owns collision gates, worktrees, independent verification, integration order, and stop/go decisions. No Claude agent may push, merge, deploy, edit another lane, or use live credentials.

**Goal:** Replace the dense regeneration screen with a browser-persistent four-step flow that defaults to a full current-prompt rebuild, assigns one exact campaign version such as V3, proves worker and Notion readiness before spend, launches the canary in one safe action, and automatically publishes approved results under the reviewed Lesson Topic.

**Architecture:** Extend the existing regeneration campaign rather than creating a second workflow. A nullable-for-history but required-for-new-campaign `publication_version` is frozen on the campaign; every new target also freezes a reviewed Notion parent decision. Reactive estimate stays DB-only and resolves effective worker requirements; an explicitly triggered, read-only Notion check produces the destination digest, and campaign creation re-resolves it outside any DB transaction before a short insert transaction compares/freezes it. The React route is decomposed into small guided-step components backed by a versioned local-storage adapter; server-derived plans, prices, workers and Notion data are always refreshed.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy async/PostgreSQL, Alembic, pytest, React 19, TypeScript, TanStack Query, browser `localStorage`, Notion API wrapper.

**Spec:** `docs/superpowers/specs/2026-08-21-guided-regeneration-ux-design.md`

## Global Constraints

- This plan starts from the integrated regeneration preview branch, never from `Nggaev-v2`; the assembled feature remains in `/Users/macmini5/Documents/HCGA-regen-v3-local-test` until the user separately approves integration.
- Run the repository branch-collision gate before the first edit in every lane and repeat it whenever the base or scope changes. PRs authored by `s1gmamale1` are strictly read-only.
- The current remote base is `origin/Nggaev-v2@631d163d`; its only path after this branch's merge base is `docs/SPEED-REPORT-2026-08-19.md`, so it does not overlap this plan. Re-check this conclusion immediately before execution.
- Open PRs #136, #131, #128, #118, #117 and #108 are authored by `AdxamAxatov`. Do not push to, edit, merge, close, retarget or comment on any PR during this implementation.
- Choose the Alembic revision identifier from the single head present after the execution branch is refreshed. Do not assume `0064` is still free and do not create a second head.
- `REGENERATION_ENABLED=false` and `REGENERATION_PUBLISHER_ENABLED=false` remain the default outside the isolated test environment.
- Publication is always additive: V1 and earlier `Homework Vn` pages are never replaced, renamed or deleted.
- New campaigns require one exact integer `publication_version >= 2`; the initial UI value is `3`, gaps are allowed, and every target in the campaign receives that exact version.
- Legacy campaign rows may retain `publication_version=NULL`; API and reports label them `Legacy mixed/automatic version` rather than inventing a value. Every new create path refuses a null version.
- Full rebuild is the default. It regenerates every canonical content phase; extraction remains reused unless the operator explicitly enables refresh.
- Canary review is the sole human content gate. Approval automatically releases publication and the remaining bounded wave. A one-target campaign has no empty bulk approval step.
- Estimate and campaign creation perform no model call. Campaign creation alone also spends nothing. The first paid action is launching the canary.
- `/estimate` remains DB-only and reactive. Live Notion reads run only from the explicit destination-check action, campaign creation revalidation, canary-start revalidation and publication, always outside an open DB transaction or held row lock.
- Review and publication both use `app.services.notion.page_creator._normalize` unchanged; no second title-normalization rule is permitted.
- A newly created campaign cannot silently adopt another campaign's `Homework Vn`; only retry of the same target may adopt a page with the complete matching revision marker.
- An ambiguous Lesson Topic blocks creation until the operator chooses a server-returned safe candidate or removes that target.
- Persist only operator input in `localStorage`; never persist eligibility, plan, estimate, manifest, worker or Notion response data.
- All automated provider and Notion tests use fakes. Live Notion and Gemini are permitted only in Task 8's isolated acceptance environment, after every offline gate passes.
- Task 8 may mutate only a dedicated Notion sandbox subject page unless the user separately approves the exact curriculum page tree immediately before the write.
- No merge into `Nggaev-v2`, push, PR mutation, deployment or production flag change is authorized by this plan.

## File and Responsibility Map

- `app/models/regeneration_campaign.py`: immutable requested campaign version.
- `app/models/regeneration_target.py`: frozen reviewed Notion destination decision and existing actual version-page ID.
- `app/repositories/regeneration_campaigns.py`: campaign insert with exact version.
- `app/repositories/regeneration_targets.py`: target insert and exact-version reservation under the existing lineage lock.
- `app/repositories/regeneration_sources.py`: read-only version-consumption/conflict queries.
- `app/services/regeneration_destination.py`: read-only exact Notion Lesson Topic/version-page resolution and stable digest.
- `app/services/regeneration_notion_readiness.py`: shared side-effect-free Notion enablement/credential readiness predicate.
- `app/services/regeneration_executability.py`: pure effective-provider computation plus active-worker compatibility.
- `app/services/regeneration_campaign.py`: authoritative create-time revalidation and freezing of version/destination decisions.
- `app/services/regeneration_publisher.py`: consume the frozen parent decision and revalidate membership before writing.
- `app/schemas/regeneration.py` and `app/api/v1/regeneration.py`: request/response boundary and estimate/create orchestration.
- `web/src/lib/regeneration-draft.ts`: versioned local-storage codec, pruning and effective phase selection.
- `web/src/lib/api.ts`: bounded all-books pagination and expanded regeneration contracts.
- `web/src/components/regeneration/guided-progress.tsx`: four-step navigation only.
- `web/src/components/regeneration/lesson-step.tsx`: library filters, lesson selection and destination overrides.
- `web/src/components/regeneration/content-step.tsx`: full/selective modes and Advanced controls.
- `web/src/components/regeneration/review-step.tsx`: non-spending readiness summary and first-paid-action button.
- `web/src/components/regeneration/canary-step.tsx`: existing canary report/actions presented as step 4.
- `web/src/components/regeneration/regeneration-wizard.tsx`: guided-step composition, not remote mutation ownership.
- `web/src/routes/regeneration.tsx`: queries, create-then-canary transaction semantics, draft clearing and campaign navigation.

## Dependency Graph and External-Agent Lanes

```text
                         reviewed preview base
                     /           |             \
            T1 schema/data   T3 executability   T5 draft/library
                 |                  |                |
            T2 exact version        |                |
                 |                  |                |
            T4 Notion destination   |                |
                  \                 |               /
                   \------------- T6 API ----------/
                                   |
                              T7 guided UI
                                   |
                              T8 acceptance
```

- **Wave 1, parallel:** Tasks 1, 3 and 5. Their owned files are disjoint.
- **Wave 2:** Task 2 follows Task 1. Task 4 follows Tasks 1 and 2 because its collision result includes the campaign's exact version.
- **Wave 3:** Task 6 follows Tasks 2, 3 and 4. Task 7 follows Tasks 5 and 6.
- **Wave 4:** Task 8 follows every reviewed and locally integrated task.
- Each task uses a fresh manually-created worktree and branch from the current reviewed preview integration SHA. A later task never starts from an unreviewed dependency.
- At most three Claude controllers run concurrently. Each controller uses a fresh implementation subagent, then a different Opus 5 spec reviewer, then a different Opus 5 quality reviewer. Codex independently inspects the diff and reruns the task's named tests before cherry-picking it into the preview worktree.

## External-Agent and Disposable-Database Protocol

Before dispatching each lane, Codex records `git fetch --all --prune`, current
preview/base SHAs, all worktrees/branches, open PR authors and actual overlapping
path diffs. Create worktrees manually with `git worktree add`; do not let Claude
choose or reuse a worktree. The controller receives the exact task section,
allowed files, forbidden files, base SHA and commands. It uses an implementation
subagent, an independent spec reviewer and an independent quality reviewer, all
on `claude-opus-5`. The controller may commit only task-owned files after RED →
GREEN. Codex reads the diff, confirms ownership, reruns every named command and
only then cherry-picks into the local preview branch.

DB-writing lanes use separate databases and never share them:

| Task | Database |
|---|---|
| Task 1 | `hcga_guided_regen_t1` |
| Task 2 | `hcga_guided_regen_t2` |
| Task 3 | `hcga_guided_regen_t3` |
| Task 6 | `hcga_guided_regen_t6` |
| Task 8 | `hcga_guided_regen_acceptance` |

Before creation, run the literal-name existence check below with the owning
name. If it returns a row, stop and establish ownership; never drop or reuse an
unknown database.

```bash
psql -h 127.0.0.1 -U macmini5 -d postgres -Atc \
  "SELECT datname FROM pg_database WHERE datname='hcga_guided_regen_t1'"
createdb -h 127.0.0.1 -U macmini5 hcga_guided_regen_t1
export DATABASE_URL='postgresql+asyncpg://macmini5@127.0.0.1:5432/hcga_guided_regen_t1'
export RUN_DB_INTEGRATION=1
export REGEN_REQUIRE_DB=1
uv run alembic upgrade head
```

Repeat with the exact owning name; do not use a shell-computed name. Preserve
Task 8's database for the user's evidence review. Cleanup is a separate final
action: re-resolve the exact name and host, confirm it is one of the table's
disposable names, then use one literal `dropdb -h 127.0.0.1 -U macmini5 NAME`
per approved database. No live credential enters Tasks 1–7.

---

### Task 1: Campaign Version and Reviewed Destination Persistence

**Files:**
- Modify: `app/models/regeneration_campaign.py`
- Modify: `app/models/regeneration_target.py`
- Modify: `app/repositories/regeneration_campaigns.py`
- Modify: `app/repositories/regeneration_targets.py`
- Create: the next single-head migration under `alembic/versions/`
- Modify: `tests/models/test_regeneration_models.py`
- Modify: `tests/migrations/test_regeneration_schema.py`
- Modify: `tests/integration/test_regeneration_constraints.py`

**Interfaces:**
- Consumes: current `RegenerationCampaign`, `RegenerationTarget`, target partial unique indexes and migration head `0063_regeneration_campaigns` as observed on the planning branch.
- Produces: `RegenerationCampaign.publication_version: Optional[int]`; `RegenerationTarget.notion_container_policy: Optional[str]`; `RegenerationTarget.reviewed_notion_container_page_id: Optional[str]`; `RegenerationTarget.notion_parent_policy: Optional[str]`; `RegenerationTarget.reviewed_notion_lesson_page_id: Optional[str]`; `RegenerationTarget.reviewed_notion_lesson_title: Optional[str]`; extended repository insert parameters with the same names.

- [ ] **Step 1: Write failing model and migration tests**

```python
def test_new_campaign_version_and_target_destination_columns_are_declared():
    assert RegenerationCampaign.__table__.c.publication_version.nullable is True
    assert RegenerationTarget.__table__.c.notion_container_policy.nullable is True
    assert RegenerationTarget.__table__.c.reviewed_notion_container_page_id.nullable is True
    assert RegenerationTarget.__table__.c.notion_parent_policy.nullable is True
    assert RegenerationTarget.__table__.c.reviewed_notion_lesson_page_id.nullable is True
    assert RegenerationTarget.__table__.c.reviewed_notion_lesson_title.nullable is True

def test_destination_check_accepts_only_legacy_reuse_or_create_shapes(db_conn):
    legacy = insert_target(db_conn, notion_parent_policy=None)
    reuse = insert_target(
        db_conn,
        notion_container_policy="reuse",
        reviewed_notion_container_page_id="container-1",
        notion_parent_policy="reuse",
        reviewed_notion_lesson_page_id="lesson-page",
        reviewed_notion_lesson_title="7 Photosynthesis",
    )
    create = insert_target(
        db_conn,
        notion_container_policy="create",
        reviewed_notion_container_page_id=None,
        notion_parent_policy="create",
        reviewed_notion_lesson_page_id=None,
        reviewed_notion_lesson_title="7 Photosynthesis",
    )
    assert {legacy, reuse, create}
```

Also assert that `publication_version=1`, container/lesson `reuse` without a
page ID, `create` with a page ID, an unknown policy, a reused Lesson Topic under
a create-new container, or any non-null reviewed field with null policy is
rejected by named checks. The migration test upgrades from the execution-time
single head, inspects all six columns and both checks, downgrades, and confirms
they disappear.

- [ ] **Step 2: Run the new tests and record RED**

Run:

```bash
uv run pytest -q tests/models/test_regeneration_models.py tests/migrations/test_regeneration_schema.py -k 'campaign_version or destination'
RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 uv run pytest -q tests/integration/test_regeneration_constraints.py -k 'campaign_version or destination'
```

Expected: failures name missing columns/checks; DB tests must run with zero skips against the lane's explicit `127.0.0.1` disposable database.

- [ ] **Step 3: Add the nullable historical columns and named constraints**

```python
# RegenerationCampaign
publication_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

# RegenerationTarget
notion_container_policy: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
reviewed_notion_container_page_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
notion_parent_policy: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
reviewed_notion_lesson_page_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
reviewed_notion_lesson_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

Add these exact logical checks to both ORM metadata and Alembic:

```sql
publication_version IS NULL OR publication_version >= 2
```

```sql
(notion_parent_policy IS NULL
 AND notion_container_policy IS NULL
 AND reviewed_notion_container_page_id IS NULL
 AND reviewed_notion_lesson_page_id IS NULL
 AND reviewed_notion_lesson_title IS NULL)
OR
(notion_parent_policy IN ('reuse','create')
 AND reviewed_notion_lesson_title IS NOT NULL
 AND (
   (notion_container_policy = 'reuse'
    AND reviewed_notion_container_page_id IS NOT NULL)
   OR
   (notion_container_policy = 'create'
    AND reviewed_notion_container_page_id IS NULL)
 )
 AND (
   (notion_parent_policy = 'reuse'
    AND notion_container_policy = 'reuse'
    AND reviewed_notion_lesson_page_id IS NOT NULL)
   OR
   (notion_parent_policy = 'create'
    AND reviewed_notion_lesson_page_id IS NULL)
 ))
```

Name them `ck_regeneration_campaigns_publication_version` and `ck_regeneration_targets_notion_parent_decision`. Keep the columns nullable so historical campaigns/targets are not assigned false campaign-wide semantics; new-service non-null enforcement belongs to Task 2/4.

- [ ] **Step 4: Extend repository inserts without adding mutable update methods**

```python
async def create_campaign(
    session: AsyncSession,
    *,
    publication_version: Optional[int] = None,
    status: str = "draft",
    selection_spec: dict,
    requested_phases: list[str],
    excluded_phases: list[str],
    launch_contract: dict,
    refresh_extraction: bool,
    exclusion_acknowledged: bool,
    canary_size: int,
    estimated_cost_low_usd: Optional[float],
    estimated_cost_high_usd: Optional[float],
    app_git_revision: Optional[str],
) -> RegenerationCampaign:
    if publication_version is not None and publication_version < 2:
        raise ValueError("publication_version must be >= 2")
```

```python
async def create_target(
    session: AsyncSession,
    *,
    campaign_id: UUID,
    toc_entry_id: UUID,
    output_language: str,
    phase_plan: dict,
    source_job_id: Optional[UUID] = None,
    is_canary: bool = False,
    status: str = "planned",
    notion_container_policy: Optional[str] = None,
    reviewed_notion_container_page_id: Optional[str] = None,
    notion_parent_policy: Optional[str] = None,
    reviewed_notion_lesson_page_id: Optional[str] = None,
    reviewed_notion_lesson_title: Optional[str] = None,
) -> RegenerationTarget:
```

Validate the three-field destination shape before constructing the ORM row.
The all-null defaults preserve historical/internal call compatibility until
Task 4 makes the fields mandatory on every new service-created campaign; any
non-null partial shape is refused. Do not expose a later setter: the reviewed
decision is immutable after target creation.

- [ ] **Step 5: Run focused tests and single-head checks**

```bash
uv run pytest -q tests/models/test_regeneration_models.py tests/migrations/test_regeneration_schema.py
RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 uv run pytest -q tests/integration/test_regeneration_constraints.py
uv run alembic heads
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
uv run pytest -q
```

Expected: zero skips in DB tests and exactly one Alembic head.

- [ ] **Step 6: Commit the reviewed task**

```bash
git add app/models/regeneration_campaign.py app/models/regeneration_target.py app/repositories/regeneration_campaigns.py app/repositories/regeneration_targets.py alembic/versions tests/models/test_regeneration_models.py tests/migrations/test_regeneration_schema.py tests/integration/test_regeneration_constraints.py
git commit -m "feat(regeneration): persist campaign version and destination"
```

---

### Task 2: Exact Campaign Version Validation and Reservation

**Files:**
- Modify: `app/repositories/regeneration_sources.py`
- Modify: `app/repositories/regeneration_targets.py`
- Modify: `app/services/regeneration_campaign.py`
- Modify: `app/services/regeneration_publisher.py`
- Modify: `tests/services/test_regeneration_campaign.py`
- Modify: `tests/services/test_regeneration_publisher.py`
- Modify: `tests/repositories/test_regeneration_repositories.py`
- Modify: `tests/integration/test_regeneration_source_and_version_queries.py`
- Modify: `tests/integration/test_regeneration_publication_claims.py`
- Modify: `tests/integration/test_regeneration_campaign_concurrency.py`

**Interfaces:**
- Consumes: Task 1's `RegenerationCampaign.publication_version` and campaign repository argument.
- Produces: `VersionConflict`; `publication_version_conflicts(...)`; backward-compatible `CreateCampaignSpec.publication_version: Optional[int]`; exact-number behavior in `reserve_publication_version(...)`; non-retryable `PublicationVersionUnavailable` handling.

- [ ] **Step 1: Write RED tests for V1-to-V3, consumed, stale and concurrent conflicts**

```python
async def test_v1_source_can_request_v3_and_all_targets_freeze_v3(service):
    campaign = await service.create_campaign(spec(publication_version=3))
    assert campaign.publication_version == 3
    assert all(t.publication_version is None for t in campaign.targets)

async def test_reservation_uses_campaign_version_not_max_plus_one(repo_fixture):
    target, token = await claimed_target(campaign_version=5, prior_versions=(2, 3))
    assert await reserve_publication_version(
        repo_fixture.session, target_id=target.id, claim_token=token
    ) == 5
```

Add cases for source version `>= requested`, an already-consumed DB version,
two concurrent reservations for the same lineage/version, retry returning the
already-reserved number, and a historical null-version campaign retaining the
existing `max + 1` behavior.
In `tests/integration/test_regeneration_campaign_concurrency.py`, race two
`create_campaign` calls requesting the same exact version for one lineage and
race two claimed-target reservations for that lineage/version. Exactly one wins
each race; the loser is a typed, non-retryable operator-facing conflict and no
second campaign/number is silently created.

- [ ] **Step 2: Run the version suite and record RED**

```bash
uv run pytest -q tests/services/test_regeneration_campaign.py -k publication_version
RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 uv run pytest -q tests/integration/test_regeneration_source_and_version_queries.py tests/integration/test_regeneration_publication_claims.py -k version
```

Expected: current `max + 1` assertions fail for requested V3/V5 and the create spec lacks the new field.

- [ ] **Step 3: Add one machine-readable conflict type and read query**

```python
@dataclass(frozen=True)
class VersionConflict:
    toc_entry_id: UUID
    output_language: str
    requested_version: int
    reason: Literal["source_not_older", "already_consumed"]
    existing_version: int

async def publication_version_conflicts(
    session: AsyncSession,
    *,
    sources: Sequence[EligibleRegenerationSource],
    requested_version: int,
) -> tuple[VersionConflict, ...]:
```

`source_not_older` compares the immediate source's actual `source_publication_version`. `already_consumed` queries all regeneration targets, including terminal/abandoned rows, because consumed versions are never reusable.

- [ ] **Step 4: Require and freeze the exact version at campaign creation**

```python
@dataclass(frozen=True)
class CreateCampaignSpec:
    selection: CampaignSelection
    contract: LaunchContract
    selected_phases: tuple[str, ...]
    publication_version: Optional[int] = None
    excluded_affected_phases: tuple[str, ...] = ()
    refresh_extraction: bool = False
    exclusion_acknowledged: bool = False
    canary_size: int = 1
    estimated_cost_low_usd: Optional[float] = None
    estimated_cost_high_usd: Optional[float] = None
    app_git_revision: Optional[str] = None
    actor: str = ""
    notes: dict = field(default_factory=dict)
```

Reject a non-null value `<2`, collect every conflict before inserting the
campaign, and raise `RequestedPublicationVersionConflict(conflicts)` so the API
can render every affected lesson at once. `None` preserves existing internal and
API constructors through Tasks 2–5 and keeps legacy automatic-version tests
green. Task 6 makes the public estimate/create request require an integer and
therefore makes every newly-created operator campaign exact-versioned.

- [ ] **Step 5: Change reservation under the existing advisory lock**

After locking the lineage and target, load the owning campaign and use:

```python
requested = campaign.publication_version
if target.publication_version is not None:
    if requested is not None and target.publication_version != requested:
        raise PublicationVersionUnavailable("reserved version differs from campaign")
    return target.publication_version
if requested is None:
    highest = await session.scalar(
        select(func.max(RegenerationTarget.publication_version)).where(
            RegenerationTarget.toc_entry_id == toc_entry_id,
            RegenerationTarget.output_language == output_language,
        )
    )
    requested = max(highest or 1, 1) + 1  # historical campaign compatibility
conflict = await session.scalar(
    select(RegenerationTarget.id).where(
        RegenerationTarget.toc_entry_id == toc_entry_id,
        RegenerationTarget.output_language == output_language,
        RegenerationTarget.publication_version == requested,
        RegenerationTarget.id != target.id,
    )
)
if conflict is not None:
    raise PublicationVersionUnavailable(
        f"Homework V{requested} is already consumed for this lesson and language"
    )
version = requested
```

Retain the existing claim-token fence, target row lock, advisory lock and partial unique index. Never silently fall forward to another number.

Define `PublicationVersionUnavailable` beside `StalePublicationClaim` in
`app/repositories/regeneration_targets.py`. In
`RegenerationPublisher._prepare`, translate it and an `IntegrityError` whose
constraint is `uq_regeneration_targets_publication_version` into
`_Refusal(reason, retryable=False)` only after explicitly rolling back the
failed preparation session. The outer publisher then records the refusal in its
normal fresh outcome session. Other `IntegrityError` values still roll back and
raise; do not hide unrelated database defects behind a version message.

- [ ] **Step 6: Run unit, repository and concurrent DB tests**

```bash
uv run pytest -q tests/services/test_regeneration_campaign.py tests/repositories/test_regeneration_repositories.py -k 'version or create_campaign'
RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 uv run pytest -q tests/integration/test_regeneration_source_and_version_queries.py tests/integration/test_regeneration_publication_claims.py
RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 uv run pytest -q tests/integration/test_regeneration_campaign_concurrency.py -k publication_version
uv run pytest -q tests/services/test_regeneration_publisher.py -k publication_version
uv run pytest -q
```

- [ ] **Step 7: Commit the reviewed task**

```bash
git add app/repositories/regeneration_sources.py app/repositories/regeneration_targets.py app/services/regeneration_campaign.py app/services/regeneration_publisher.py tests/services/test_regeneration_campaign.py tests/services/test_regeneration_publisher.py tests/repositories/test_regeneration_repositories.py tests/integration/test_regeneration_source_and_version_queries.py tests/integration/test_regeneration_publication_claims.py tests/integration/test_regeneration_campaign_concurrency.py
git commit -m "feat(regeneration): reserve exact campaign version"
```

---

### Task 3: Active-Worker Executability Preflight

**Files:**
- Create: `app/services/regeneration_executability.py`
- Create: `tests/services/test_regeneration_executability.py`
- Create: `tests/repositories/test_regeneration_claim_parity.py`

**Interfaces:**
- Consumes: `ResolvedLaunchContract`, `model_tiers.resolve_judge`, `model_tiers.resolve_solver`, `agent_models.resolve_role_transport`, and `workers_repo.list_with_liveness(...)`.
- Produces: `required_api_providers(contract) -> frozenset[str]`; `worker_can_execute(contract, worker, fleet_api_paused) -> bool`; `async check_active_workers(session, contract, stale_after_seconds) -> WorkerExecutability`.

- [ ] **Step 1: Write pure RED tests matching the claim gate**

```python
def test_self_solver_requires_peer_provider_credential():
    contract = resolved_contract(
        provider="gemini",
        model="gemini-3.1-pro-preview",
        solver_provider="gemini",
        solver_model="gemini-3.1-pro-preview",
    )
    assert required_api_providers(contract) == frozenset({"gemini", "claude"})

def test_worker_needs_every_effective_api_provider():
    worker = online_worker(api={"gemini": True, "claude": False})
    assert worker_can_execute(contract_requiring("gemini", "claude"), worker) is False
```

Cover content, extract, judge and solver; inherited transport; self-grade/self-solve fallback; offline/stale/draining workers; missing capability blobs; and one compatible worker among several incompatible workers.
Add a fleet-budget-paused case that refuses every API contract even when the
credential set is otherwise compatible.

- [ ] **Step 2: Run and record RED**

```bash
uv run pytest -q tests/services/test_regeneration_executability.py
```

Expected: import failure for the new service.

- [ ] **Step 3: Implement the pure and DB-backed result**

```python
@dataclass(frozen=True)
class WorkerExecutability:
    ok: bool
    workers_online: int
    compatible_worker_ids: tuple[str, ...]
    required_api_providers: tuple[str, ...]
    fleet_api_paused: bool
    reason: Optional[str]

def required_api_providers(
    contract: ResolvedLaunchContract,
) -> frozenset[str]:
    required = {contract.provider}
    if resolve_role_transport(contract.extract_transport, contract.transport) == "api":
        required.add(contract.extract_provider)
    if resolve_role_transport(contract.judge_transport, contract.transport) == "api":
        required.add(resolve_judge(
            contract.provider, contract.model,
            contract.judge_provider, contract.judge_model,
        )[0])
    if resolve_role_transport(contract.solver_transport, contract.transport) == "api":
        required.add(resolve_solver(
            contract.provider, contract.model,
            contract.solver_provider, contract.solver_model,
        )[0])
    return frozenset(required)
```

`check_active_workers` reads `budget_repo.get_state(session)` and refuses first
when `api_paused_at` is non-null, with reason `fleet API spend is paused`.
`worker_can_execute` requires `fleet_api_paused is False`,
`worker["online"] is True`, `worker["status"] == "online"`, and truthy
`worker["capabilities"]["api"][provider]` for every required provider.
`draining` is deliberately a safe preflight refusal: the worker may claim only
until it observes the drain signal and must not be promised as capacity for a
new campaign. The SQL parity table uses status-online workers because
`claim_next_job` receives credential flags, not registry status.

- [ ] **Step 4: Add a parity test against real claim behavior**

In `tests/repositories/test_regeneration_claim_parity.py`, for a table of
resolved contracts, capability blobs and fleet-pause states, create a pending
revision job in the repository test fixture and assert:

```python
assert worker_can_execute(contract, worker_view) is (
    await jobs_repo.claim_next_job(
        session,
        worker_id="parity-worker",
        max_attempts=3,
        capabilities=credential_caps(worker_view),
        fleet_api_paused=fleet_api_paused,
    )
    is not None
)
```

This prevents the preflight and SQL claim gate from drifting on self-grade/self-solve rules.

- [ ] **Step 5: Run tests**

```bash
uv run pytest -q tests/services/test_regeneration_executability.py
RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 uv run pytest -q tests/repositories/test_regeneration_claim_parity.py
uv run pytest -q
```

- [ ] **Step 6: Commit the reviewed task**

```bash
git add app/services/regeneration_executability.py tests/services/test_regeneration_executability.py tests/repositories/test_regeneration_claim_parity.py
git commit -m "feat(regeneration): preflight active worker compatibility"
```

---

### Task 4: Exact Read-Only Notion Destination Resolution

**Files:**
- Create: `app/services/regeneration_destination.py`
- Create: `app/services/regeneration_notion_readiness.py`
- Modify: `app/services/regeneration_publisher.py`
- Create: `tests/services/test_regeneration_destination.py`
- Modify: `tests/services/test_regeneration_publisher.py`
- Modify: `tests/services/test_regeneration_publisher_lifespan.py`

**Interfaces:**
- Consumes: Task 1 destination-column shapes, `notion_archive.resolve_lesson_title`, `_resolve_subject_page_id`, `notion.page_creator._normalize`, and `NotionClientWrapper.get_child_pages/get_page_parent`.
- Produces: shared `publication_unavailable_reason()` in the neutral readiness module; scalar-only `DestinationSource`, `DestinationOverride`, `DestinationCandidate`, `DestinationResolution`, `DestinationPreflight`, `resolve_destinations(...)`, and `destination_digest(...)`; backward-compatible publisher support for frozen decisions.

- [ ] **Step 1: Write RED resolver tests with a fake Notion reader**

```python
async def test_valid_stored_pointer_is_reused(resolver):
    result = await resolver.resolve(sources=[source(pointer="lesson-1")], overrides=())
    assert result.resolutions[0].lesson_policy == "reuse"
    assert result.resolutions[0].lesson_page_id == "lesson-1"

async def test_two_safe_matches_block_until_operator_selects_one(resolver):
    result = await resolver.resolve(sources=[source(pointer=None)], overrides=())
    assert result.resolutions[0].status == "ambiguous"
    assert [c.page_id for c in result.resolutions[0].candidates] == ["a", "b"]
    assert result.ok is False
```

Cover one normalized match, no match (`lesson_policy=create`), invalid stored pointer in a different language container, valid override, override not in candidates, missing subject mapping, missing/ambiguous `Generated Homeworks` container, existing `Homework V3`, stable digest ordering, a disabled/uncredentialed Notion head, the 500-target bound, and a simulated rate-limit exception surfaced as retryable preflight failure. Include the measured duplicate shape where both `7 Photosynthesis` and `7 Photosynthesis (2)` exist; it must be ambiguous in review.

- [ ] **Step 2: Run resolver tests and record RED**

```bash
uv run pytest -q tests/services/test_regeneration_destination.py
```

- [ ] **Step 3: Implement focused immutable result types**

```python
LineageKey = tuple[UUID, str]

@dataclass(frozen=True)
class DestinationSource:
    toc_entry_id: UUID
    output_language: str
    source_job_id: UUID
    subject: str
    grade: Optional[str]
    book_filename: str
    section_number: Optional[str]
    section_title: str
    chapter_title: str
    page_start: Optional[int]
    notion_lesson_page_id: Optional[str]
    lesson_title: str

@dataclass(frozen=True)
class DestinationOverride:
    toc_entry_id: UUID
    output_language: str
    notion_lesson_page_id: str

@dataclass(frozen=True)
class DestinationCandidate:
    page_id: str
    title: str

@dataclass(frozen=True)
class DestinationResolution:
    toc_entry_id: UUID
    output_language: str
    lesson_title: str
    status: Literal["reuse", "create", "ambiguous", "blocked"]
    container_policy: Optional[Literal["reuse", "create"]]
    container_page_id: Optional[str]
    lesson_policy: Optional[Literal["reuse", "create"]]
    lesson_page_id: Optional[str]
    candidates: tuple[DestinationCandidate, ...]
    reason: Optional[str]

@dataclass(frozen=True)
class DestinationPreflight:
    ok: bool
    resolutions: tuple[DestinationResolution, ...]
    digest: str
```

`destination_digest` canonicalizes sorted lineage, requested version, title,
status, container policy/ID, Lesson Topic policy/ID as compact JSON and returns SHA-256.
Candidate-list ordering does not alter the digest once a unique reviewed
decision exists. The module accepts only scalar dataclasses—never an ORM row or
session—so callers must close their DB transaction before invoking it.

- [ ] **Step 4: Implement bounded cached remote reads**

The async entry point performs all synchronous Notion work through one
`asyncio.to_thread` call and owns one client/rate limiter for the entire bounded
scan:

```python
async def resolve_destinations(
    *,
    sources: Sequence[DestinationSource],
    requested_version: int,
    overrides: Sequence[DestinationOverride],
    client_factory: Callable[[], NotionClientWrapper] = default_client,
    maximum_targets: int = 500,
) -> DestinationPreflight:
```

Move the existing side-effect-free `publication_unavailable_reason()` from
`regeneration_publisher.py` into `regeneration_notion_readiness.py`; the
publisher imports and re-exports it so existing callers/tests do not break.
Both publisher and resolver use this one predicate, avoiding a campaign ↔
publisher import cycle. Call it before constructing a client; a
non-null reason raises `DestinationServiceUnavailable` and no result is treated
as approved. Reject more than `maximum_targets` instead of scanning a prefix.
Within one call, cache subject-page resolution, `Generated Homeworks` child
scans, Lesson Topic child scans, and page-parent lookups by ID. Zero normalized
container matches produces `container_policy="create"`; exactly one produces
`container_policy="reuse"` plus its page ID; multiple containers block as
ambiguous. Do not call
`find_or_create` or any write method. A stored pointer is valid only when its
page ID is present in the exact language/subject/grade container. Import and
call `notion.page_creator._normalize` verbatim for both canonical titles and
child titles; do not copy its regex. Retain section number and the existing
canonical `resolve_lesson_title` disambiguators. An override must exactly equal
a returned safe candidate for the same lineage. For a resolved existing lesson,
scan its children for `version_page_title(requested_version)`; any matching
title blocks before spend regardless of marker, because a new campaign may not
adopt it. The response reports `checked_target_count == len(sources)`.

- [ ] **Step 5: Make the publisher consume frozen decisions without breaking legacy targets**

Keep `PublicationInputs.legacy_lesson_page_id` and append optional reviewed
fields after the current required `phase_md` field so every existing keyword
constructor remains valid:

```python
notion_container_policy: Optional[Literal["reuse", "create"]] = None
reviewed_container_page_id: Optional[str] = None
notion_parent_policy: Optional[Literal["reuse", "create"]] = None
reviewed_lesson_page_id: Optional[str] = None
reviewed_lesson_title: Optional[str] = None
```

`_prepare` copies the five new target columns. A historical target whose
policy is null follows the existing `legacy_lesson_page_id` +
`notion_archive.find_or_create` path unchanged. A reviewed target uses:

```python
subject_children = client.get_child_pages(inputs.subject_page_id)
container_matches = [
    page for page in subject_children
    if _normalize(page["title"]) == _normalize(notion_archive.CONTAINER_TITLE)
]
if inputs.notion_container_policy == "reuse":
    if not any(p["id"] == inputs.reviewed_container_page_id for p in container_matches):
        raise ReviewedDestinationChanged("reviewed Generated Homeworks container moved")
    container_id = cast(str, inputs.reviewed_container_page_id)
elif len(container_matches) == 0:
    container_id = client.create_page(
        inputs.subject_page_id, notion_archive.CONTAINER_TITLE
    )["id"]
elif len(container_matches) == 1:
    container_id = container_matches[0]["id"]
else:
    raise ReviewedDestinationChanged("reviewed container creation became ambiguous")
children = client.get_child_pages(container_id)
if inputs.notion_parent_policy == "reuse":
    if not any(p["id"] == inputs.reviewed_lesson_page_id for p in children):
        raise ReviewedDestinationChanged("reviewed Lesson Topic left its container")
    return cast(str, inputs.reviewed_lesson_page_id)
matches = [
    page for page in children
    if _normalize(page["title"]) == _normalize(cast(str, inputs.reviewed_lesson_title))
]
if len(matches) == 1:
    return matches[0]["id"]
if len(matches) > 1:
    raise ReviewedDestinationChanged("reviewed create-new title became ambiguous")
return client.create_page(container_id, inputs.reviewed_lesson_title)["id"]
```

Because review and publication import the same `_normalize`, trailing `(N)` is
folded identically. A create policy adopts one normalized match, creates only
when there are zero, and fails closed when there are multiple. Version-page
marker rules remain the final authority below it. Preserve the current pointer
backfill: when `legacy_lesson_page_id` was absent, stamp the resolved/created
Lesson Topic for both `reuse` and `create`; `_stamp_lesson_page` already writes
only when the TOC pointer is null and therefore never overwrites a valid pointer.

`ReviewedDestinationChanged` occurs in `_deliver`, after `_prepare` has reserved
the exact campaign version. Catch it immediately before the generic delivery
exception and call `_resolve_failure(..., retryable=False)`. The target parks in
manual-action-required state, the report says the already-reserved version
remains consumed, and automatic backoff does not burn attempts on an authority
change. The operator may restore the reviewed membership and explicitly retry
the same target/version; if that decision cannot be restored, they abandon the
target and start a new campaign at a new version. Add tests for moved container,
moved Lesson Topic and newly ambiguous create policy asserting: one attempt,
null next-attempt timestamp, preserved publication version and actionable
reason.

- [ ] **Step 6: Run resolver and publisher suites plus repo-wide regression**

```bash
uv run pytest -q tests/services/test_regeneration_destination.py
uv run pytest -q tests/services/test_regeneration_publisher.py tests/services/test_regeneration_publisher_lifespan.py
uv run pytest -q
```

- [ ] **Step 7: Commit the reviewed task**

```bash
git add app/services/regeneration_destination.py app/services/regeneration_notion_readiness.py app/services/regeneration_publisher.py tests/services/test_regeneration_destination.py tests/services/test_regeneration_publisher.py tests/services/test_regeneration_publisher_lifespan.py
git commit -m "feat(regeneration): freeze reviewed Notion destination"
```

---

### Task 5: Persistent Draft and Complete Library Loading

**Files:**
- Create: `web/src/lib/regeneration-draft.ts`
- Create: `web/src/lib/regeneration-draft.test.ts`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/regeneration-api.test.ts`

**Interfaces:**
- Consumes: current `RegenerationDraftState` fields and bounded `/api/v1/books?limit=2001&offset=0`.
- Produces: `GuidedRegenerationDraft`; `loadRegenerationDraft`; `saveRegenerationDraft`; `clearRegenerationDraft`; `pruneRegenerationDraft`; `effectiveSelectedPhases`; `api.listAllBooks()`.

- [ ] **Step 1: Write RED storage and pagination tests**

```typescript
test("default draft is V3 full rebuild with extraction off", () => {
  const draft = defaultGuidedRegenerationDraft();
  assert.equal(draft.mode, "full");
  assert.equal(draft.publicationVersion, 3);
  assert.equal(draft.refreshExtraction, false);
  assert.equal(draft.step, "lessons");
});

test("restoring a draft resets acknowledgement and prunes stale lessons", () => {
  const restored = pruneRegenerationDraft(savedDraft, {
    eligibleTocEntryIds: new Set(["kept"]),
    validModelRefs: new Set(["gemini/gemini-3.6-flash"]),
    validPhaseNames: new Set(["reflection"]),
  });
  assert.deepEqual(restored.draft.selectedTocEntryIds, ["kept"]);
  assert.equal(restored.draft.acknowledged, false);
  assert.equal(restored.removedLessonCount, 1);
});
```

Add corrupt JSON, unknown schema version, storage read/write exceptions,
destination-override pruning, canary clamping, full-mode canonical phases,
unknown requested/excluded phase pruning, selective-empty fallback to full mode,
and all 246 books loaded by one bounded 2001-row request. Assert a 2001-row
response throws a bounded-library error instead of silently truncating.

- [ ] **Step 2: Run frontend unit tests and record RED**

```bash
cd web
npm test -- --test-name-pattern='draft|all books'
```

- [ ] **Step 3: Implement a versioned operator-input-only codec**

```typescript
export const REGENERATION_DRAFT_KEY = "hcga.regeneration.draft.v1";
export type RegenerationWizardStep = "lessons" | "content" | "review" | "canary";
export type RegenerationMode = "full" | "selective";

export interface DestinationOverrideDraft {
  tocEntryId: string;
  outputLanguage: RegenerationOutputLanguage;
  notionLessonPageId: string;
}

export interface GuidedRegenerationDraft extends RegenerationScopeState {
  schemaVersion: 1;
  step: RegenerationWizardStep;
  mode: RegenerationMode;
  refreshExtraction: boolean;
  provider: string;
  model: string | null;
  publicationVersion: number;
  publicationVersionMode: "automatic" | "manual";
  destinationOverrides: DestinationOverrideDraft[];
}
```

Use explicit field-by-field parsing; do not cast parsed JSON to the interface.
`loadRegenerationDraft(storage)` returns `{draft, warning}` and never throws.
`saveRegenerationDraft` catches quota/private-mode failures and returns a
warning. Derived estimates/plans/destination results have no field in this
type. Reusing `RegenerationScopeState`'s existing `subjectFilter` and
`gradeFilter` names keeps `regenerationNarrowScope` and
`regenerationToggleLesson` as the one authority for which lesson/phase fields a
scope change clears. While `publicationVersionMode === "automatic"`, compute the displayed
version as `max(3, ...selectedSources.map(s => s.next_expected_version))`;
editing the version flips the mode to `manual` and persists the exact choice.

- [ ] **Step 4: Implement one bounded complete-library read**

```typescript
async listAllBooks(): Promise<Book[]> {
  const rows = unwrap<Book[]>(await authFetch("/api/v1/books?limit=2001&offset=0"));
  if (rows.length > 2000) {
    throw new Error("Book library exceeded the guided picker safety limit of 2000 rows");
  }
  return rows;
}
```

This single database statement avoids unstable offset windows. The guided route
uses the dedicated TanStack key `["books", "all"]`; Fleet and Library retain
their existing `["books"]` 100-row cache and cannot overwrite this result.

- [ ] **Step 5: Run tests and TypeScript build**

```bash
cd web
npm test
npm run build
```

- [ ] **Step 6: Commit the reviewed task**

```bash
git add web/src/lib/regeneration-draft.ts web/src/lib/regeneration-draft.test.ts web/src/lib/api.ts web/src/lib/regeneration-api.test.ts
git commit -m "feat(web): persist regeneration draft and load full library"
```

---

### Task 6: Estimate/Create API Contract and Preflight Integration

**Files:**
- Modify: `app/schemas/regeneration.py`
- Modify: `app/api/v1/regeneration.py`
- Modify: `app/main.py`
- Modify: `app/services/regeneration_campaign.py`
- Modify: `tests/schemas/test_regeneration_schemas.py`
- Modify: `tests/api/test_regeneration_api.py`
- Modify: `tests/api/test_regeneration_feature_flag.py`
- Modify: `tests/api/test_regeneration_reports.py`
- Modify: `tests/integration/test_regeneration_campaign_concurrency.py`
- Modify: `tests/integration/test_regeneration_e2e.py`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/types.ts`
- Modify: `web/src/lib/regeneration-api.test.ts`
- Modify: `web/src/lib/regeneration-state.test.ts`
- Modify: `web/src/components/regeneration/regeneration-wizard.tsx`
- Modify: `web/src/routes/regeneration.tsx`

**Interfaces:**
- Consumes: Tasks 2–4 backend services and Task 5 frontend draft types.
- Produces: exact JSON contract for publication version, destination overrides/digest/results and worker executability; campaign report version/destination fields.

- [ ] **Step 1: Write failing schema/API tests for the full review contract**

```python
def test_estimate_requires_campaign_version_and_returns_db_readiness(client):
    response = client.post("/api/v1/regeneration/estimate", json=estimate_body(3))
    assert response.status_code == 200
    body = response.json()
    assert body["publication_version"] == 3
    assert body["worker_executability"]["ok"] is True
    assert "destination_digest" not in body
    assert fake_notion.calls == []

def test_destination_check_is_explicit_and_returns_every_target(client):
    response = client.post(
        "/api/v1/regeneration/destinations", json=destination_body(3)
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["destination_digest"]) == 64
    assert body["checked_target_count"] == body["target_count"]
    assert body["destinations"][0]["status"] == "reuse"

def test_create_refuses_changed_destination_digest_without_a_row(client):
    response = client.post(
        "/api/v1/regeneration/campaigns",
        json=create_body(approved_destination_digest="stale"),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "destination_review_changed"
    assert campaign_count() == 0
```

Add request validation for V1, duplicate/malformed overrides, override outside
selection, no compatible worker, fleet API paused, source/consumed/Notion V3
conflicts, ambiguous destination, Notion disabled/uncredentialed, all-target
scan accounting, destination HTTP occurring with no active DB transaction, and
legacy campaign report labeling. Add a launch test proving remote
revalidation runs before the campaign/target `FOR UPDATE` phase.

In `tests/api/test_regeneration_feature_flag.py`, replace
`test_pricing_and_creation_are_not_gated_on_notion` with three explicit
invariants: DB-only `/estimate` remains available while Notion is off; a valid
destination check or valid campaign-create body returns structured 409
`notion_destination_unavailable`; malformed `{}` create remains Pydantic 422
because readiness is checked inside the validated handler, never as a route
dependency. Update `app/main.py`'s startup warning to say estimation remains
available but destination check, campaign creation, canary start and publication
are blocked until Notion is configured.

- [ ] **Step 2: Run API/schema tests and record RED**

```bash
uv run pytest -q tests/schemas/test_regeneration_schemas.py tests/api/test_regeneration_api.py tests/api/test_regeneration_feature_flag.py tests/api/test_regeneration_reports.py -k 'publication_version or destination or worker or notion'
```

- [ ] **Step 3: Add strict request and response models**

```python
class DestinationOverrideIn(_Strict):
    toc_entry_id: UUID
    output_language: str
    notion_lesson_page_id: str = Field(min_length=1, max_length=128)

class EstimateRequest(_PhaseSelectionIn):
    selection: CampaignSelectionIn = Field(default_factory=CampaignSelectionIn)
    contract: LaunchContract
    publication_version: int = Field(ge=2)
    canary_size: int = Field(default=1, ge=1)

class CreateCampaignRequest(EstimateRequest):
    destination_overrides: list[DestinationOverrideIn] = Field(default_factory=list)
    approved_destination_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    estimated_cost_low_usd: Optional[float] = Field(default=None, ge=0)
    estimated_cost_high_usd: Optional[float] = Field(default=None, ge=0)
    app_git_revision: Optional[str] = Field(default=None, max_length=64)
    actor: str = ""
    notes: dict = Field(default_factory=dict)

class DestinationCheckRequest(_Strict):
    selection: CampaignSelectionIn
    publication_version: int = Field(ge=2)
    destination_overrides: list[DestinationOverrideIn] = Field(default_factory=list)
```

Validate override languages, unique lineage keys, and that each override lineage belongs to the request selection. Define output models matching Task 4 types plus:

```python
class WorkerExecutabilityOut(BaseModel):
    ok: bool
    workers_online: int
    compatible_worker_ids: list[str]
    required_api_providers: list[str]
    fleet_api_paused: bool
    reason: Optional[str]
```

- [ ] **Step 4: Separate DB-only estimate from explicit remote destination check**

Add a DB-only helper that performs, in order: bounded selection, discovery,
phase plans, launch-contract resolution, exact-version DB conflicts, worker
executability, extraction-source warning calculation, then pricing. A missing
head-local PDF with `refresh_extraction=true` is a warning, not a refusal:
R13 workers can already have or pull a cached copy and `VAR_DIR` is not
guaranteed shared. Keep `estimate()`'s existing “makes no Notion call” docstring
true and retain `regeneration_discovery.preflight_notion_destinations` as its
current cheap mapping-only helper.

```python
@dataclass(frozen=True)
class CampaignPreview:
    sources: tuple[EligibleRegenerationSource, ...]
    plans: Mapping[str, tuple[RegenerationPhasePlan, bool]]
    contract: ResolvedLaunchContract
    publication_version: int
    worker_executability: WorkerExecutability
    source_availability_warnings: tuple[str, ...]
```

Implement `POST /api/v1/regeneration/destinations` as a separate action. Its DB
phase loads `EligibleRegenerationSource` rows, sibling-derived lesson titles and
all `DestinationSource` scalars, then commits/closes the session. The route does
not inject a request-lifetime `AsyncSession`; it calls the service's bounded
short-session loader. Only then does
it call Task 4's `resolve_destinations` in a worker thread. If Notion is off or
uncredentialed, return structured `notion_destination_unavailable`; do not
degrade to an unverified campaign. The response always reports
`checked_target_count`, `target_count`, per-target results and the digest.
Enforce readiness inside the already-Pydantic-validated destination/create
handlers or service call, not with `Depends(require_publication_available)`, so
malformed bodies remain 422 and the readiness state is not disclosed first.
`/estimate` does not invoke this gate.

- [ ] **Step 5: Re-resolve outside transactions, then freeze inside a short create transaction**

Keep `CreateCampaignSpec` backward-compatible for direct historical callers:

```python
publication_version: Optional[int] = None
destination_overrides: tuple[DestinationOverride, ...] = ()
approved_destination_digest: str = ""
```

The public schema always supplies all three. Refactor service creation into
three explicit phases:

```python
async def prepare_campaign(self, spec: CreateCampaignSpec) -> PreparedCampaign:
    # open session -> resolve DB facts/contract/plans -> copy scalars -> close

async def resolve_prepared_destinations(
    self, prepared: PreparedCampaign, spec: CreateCampaignSpec
) -> DestinationPreflight:
    # no DB session -> Notion thread call

async def insert_prepared_campaign(
    self,
    prepared: PreparedCampaign,
    destinations: DestinationPreflight,
    spec: CreateCampaignSpec,
) -> RegenerationCampaign:
    # new session -> recheck source IDs, active lineages, version conflicts,
    # digest and worker pause/capability -> insert campaign/targets -> commit
```

`create_campaign` composes those phases. It refuses an empty digest when
`publication_version` is non-null, re-resolves destinations, compares the fresh
digest with `approved_destination_digest`, and raises
`DestinationReviewChanged` before insert on mismatch. The short insert phase
passes every target's container policy/ID and Lesson Topic policy/ID/title to
`create_target`. It performs no Notion call and never holds a session idle
across HTTP. Direct legacy callers
with `publication_version=None` retain the current automatic-version/current
publisher path until migrated.

The current router-level `_reconcile(session)` call must not leave its injected
session transaction open while service creation performs Notion I/O. Replace it
for create/canary with a short `SessionLocal` reconciliation helper that commits
and closes before calling the three-phase service. Keep the shared
`_campaign_action` behavior unchanged for approve/reject/cancel; add an
`already_reconciled: bool = False` parameter and have only the canary route run
the closed-session helper first and call `_campaign_action(...,
already_reconciled=True)`. With that flag, the injected response session is not
touched until after the remote service action returns. Open the create
response/report transaction only after its remote phase returns.

- [ ] **Step 6: Revalidate canary readiness outside row locks**

Split `launch_canary` similarly:

1. Open a read session, load campaign/target/source scalars and stored contract,
   then close it without locks.
2. In a short DB session, run worker/budget executability; close it.
3. With no DB session open, resolve the frozen destinations again and require
   their decisions/digest to match the stored targets and requested version.
4. Open a new session, lock campaign then targets in the existing order,
   re-check status/source/version/lineage facts, prepare the canary wave and
   commit before `_create_wave` performs its existing job creation.

A failed phase 2/3 leaves the campaign in `draft`, creates no job and returns an
actionable `Retry canary` reason. The unavoidable Notion race after phase 3 is
still caught by publisher membership/marker validation; no DB or remote lock can
atomically cover both systems.

- [ ] **Step 7: Extend campaign/report outputs**

New campaigns return integer `publication_version`; legacy rows return null plus `publication_version_label="Legacy mixed/automatic version"`. Each target report returns reviewed parent policy/title/page link separately from `notion_page_id`, which remains the actual published `Homework Vn` page.

- [ ] **Step 8: Mirror the exact contract in TypeScript**

```typescript
export interface RegenerationDestinationOverride {
  toc_entry_id: string;
  output_language: RegenerationOutputLanguage;
  notion_lesson_page_id: string;
}

export interface RegenerationEstimateRequest {
  publication_version: number;
  selection: RegenerationSelection;
  contract: RegenerationLaunchContract;
  selected_phases: string[];
  excluded_affected_phases: string[];
  refresh_extraction: boolean;
  exclusion_acknowledged: boolean;
  canary_size: number;
}

export interface RegenerationCampaignDraft extends RegenerationEstimateRequest {
  destination_overrides: RegenerationDestinationOverride[];
  approved_destination_digest: string;
  estimated_cost_low_usd: number | null;
  estimated_cost_high_usd: number | null;
}

export interface RegenerationDestinationCheckRequest {
  publication_version: number;
  selection: RegenerationSelection;
  destination_overrides: RegenerationDestinationOverride[];
}

export interface RegenerationDestinationCandidate {
  page_id: string;
  title: string;
  notion_page_url: string;
}

export interface RegenerationDestinationResolution {
  toc_entry_id: string;
  output_language: RegenerationOutputLanguage;
  lesson_title: string;
  status: "reuse" | "create" | "ambiguous" | "blocked";
  container_policy: "reuse" | "create" | null;
  container_page_id: string | null;
  lesson_policy: "reuse" | "create" | null;
  lesson_page_id: string | null;
  notion_page_url: string | null;
  candidates: RegenerationDestinationCandidate[];
  reason: string | null;
}

export interface RegenerationDestinationCheckResponse {
  ok: boolean;
  target_count: number;
  checked_target_count: number;
  destination_digest: string;
  destinations: RegenerationDestinationResolution[];
}
```

`approved_destination_digest` and overrides are absent from estimate requests.
`api.checkRegenerationDestinations` owns the explicit remote request; the create
method keeps the codebase's existing `RegenerationCampaignDraft` name and
requires the returned digest. Do not introduce a parallel
`RegenerationCreateRequest` alias or represent these with one loose optional
type.

- [ ] **Step 9: Keep the existing dense screen functional across the strict API cut**

Before the guided redesign in Task 7, minimally wire the new contract into the
current screen so Task 6 is independently green and usable. Extend the current
`RegenerationDraftState` with `publicationVersion` (default 3) and
`destinationOverrides`; keep the server-derived destination response/digest in
route state, never in the draft. Add a plain version input and **Check Notion
destinations** action to the current final review section. Render every result,
allow candidate selection, and disable current campaign creation until the
check is complete and current.

Change `campaignDraft` to accept the reviewed result explicitly:

```typescript
function apiOnlyContract(draft: RegenerationDraftState): RegenerationLaunchContract {
  return {
    provider: draft.provider,
    model: draft.model,
    transport: "api",
    extract_transport: "api",
    extract_provider: null,
    extract_model: null,
    judge_transport: "api",
    judge_provider: null,
    judge_model: null,
    solver_transport: "api",
    solver_provider: null,
    solver_model: null,
    session_limit_strategy: "inherit",
  };
}

function campaignDraft(
  draft: RegenerationDraftState,
  estimateLow: number | null,
  estimateHigh: number | null,
  destinations: RegenerationDestinationCheckResponse,
): RegenerationCampaignDraft {
  return {
    publication_version: draft.publicationVersion,
    destination_overrides: draft.destinationOverrides.map(toApiOverride),
    approved_destination_digest: destinations.destination_digest,
    selection: {
      book_ids: draft.bookId ? [draft.bookId] : [],
      toc_entry_ids: draft.selectedTocEntryIds,
      output_languages: [draft.language],
    },
    contract: apiOnlyContract(draft),
    selected_phases: draft.selectedPhases,
    excluded_affected_phases: draft.excludedPhases,
    refresh_extraction: draft.refreshExtraction,
    exclusion_acknowledged: draft.acknowledged,
    canary_size: clampCanarySize(draft.canarySize, draft.selectedTocEntryIds.length),
    estimated_cost_low_usd: estimateLow,
    estimated_cost_high_usd: estimateHigh,
    app_git_revision: null,
    actor: "",
    notes: {},
  };
}
```

`regenerationEstimateBody` builds its own `RegenerationEstimateRequest` and
therefore never needs a fake digest. Invalidate the destination response only
when lesson IDs, language, version or overrides change. This bridge is
deliberately dense but fully safe; Task 7 replaces its presentation while
reusing the working mutation/API seam.

- [ ] **Step 10: Run backend, real-DB and frontend contract suites**

```bash
uv run pytest -q tests/schemas/test_regeneration_schemas.py tests/api/test_regeneration_api.py tests/api/test_regeneration_feature_flag.py tests/api/test_regeneration_reports.py
RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 uv run pytest -q tests/integration/test_regeneration_campaign_concurrency.py tests/integration/test_regeneration_e2e.py
uv run pytest -q
cd web
npm test
npm run build
```

- [ ] **Step 11: Commit the reviewed task**

```bash
git add app/schemas/regeneration.py app/api/v1/regeneration.py app/main.py app/services/regeneration_campaign.py tests/schemas/test_regeneration_schemas.py tests/api/test_regeneration_api.py tests/api/test_regeneration_feature_flag.py tests/api/test_regeneration_reports.py tests/integration/test_regeneration_campaign_concurrency.py tests/integration/test_regeneration_e2e.py web/src/lib/api.ts web/src/lib/types.ts web/src/lib/regeneration-api.test.ts web/src/lib/regeneration-state.test.ts web/src/components/regeneration/regeneration-wizard.tsx web/src/routes/regeneration.tsx
git commit -m "feat(regeneration): expose exact campaign readiness review"
```

---

### Task 7: Guided Four-Step Regeneration UI

**Files:**
- Create: `web/src/components/regeneration/guided-progress.tsx`
- Create: `web/src/components/regeneration/lesson-step.tsx`
- Create: `web/src/components/regeneration/content-step.tsx`
- Create: `web/src/components/regeneration/review-step.tsx`
- Create: `web/src/components/regeneration/canary-step.tsx`
- Modify: `web/src/components/regeneration/regeneration-wizard.tsx`
- Modify: `web/src/routes/regeneration.tsx`
- Modify: `web/src/lib/regeneration-state.ts`
- Modify: `web/src/lib/regeneration-state.test.ts`
- Modify: `web/src/lib/regeneration-draft.test.ts`

**Interfaces:**
- Consumes: Tasks 5–6 draft/API types, current `CampaignList`, `CanaryReview`, `CampaignReport`, phase plan and mutation-attribution helpers.
- Produces: approved four-step A/A1 UI, destination override selection, safe create-then-canary mutation, and persistent draft lifecycle.

- [ ] **Step 1: Write RED tests for pure navigation and mutation decisions**

```typescript
test("review cannot open until estimate, worker and destinations are ready", () => {
  assert.equal(reviewGate({ estimate: null, workerOk: false, destinationsOk: false }).ok, false);
  assert.equal(reviewGate({ estimate: priced, workerOk: true, destinationsOk: true }).ok, true);
});

test("created campaign plus failed canary never permits create again", () => {
  const result = nextCreateCanaryAction({ campaignId: "campaign-1", canaryStarted: false });
  assert.deepEqual(result, { kind: "launch-existing-canary", campaignId: "campaign-1" });
});
```

Add full-rebuild-first, extraction-off, selective expansion copy, advanced
exclusion acknowledgement reset, ambiguous candidate selection, destination
override pruning, destination-result invalidation only on lesson/language/version
changes, stale restored lessons/phases messages, successful-create draft
clearing, mutation-only wave-failure preservation, and one-target approval copy
with no bulk wording. Also assert that an empty/new draft adopts
`content_provider` and `content_model` from `api.getLaunchDefaults()`, while a
restored explicit model choice is never overwritten by a later defaults
response.

- [ ] **Step 2: Run frontend tests and record RED**

```bash
cd web
npm test -- --test-name-pattern='guided|review|canary|draft'
```

- [ ] **Step 3: Split the wizard into four focused presentational steps**

`GuidedProgress` receives `active`, `highestReachable`, and `onSelect`.
`LessonStep` reads the dedicated `["books", "all"]` query, filters the fully
loaded book list by subject/grade, fetches eligible rows for one selected book,
and shows only the DB-known-pointer hint; it makes no Notion call and does not
claim an exact destination. Ambiguous candidate buttons render in `ReviewStep`
after the explicit destination check. Pass the draft directly through the
existing generic `regenerationNarrowScope` and `regenerationToggleLesson`
helpers; `GuidedRegenerationDraft` extends their complete nine-field scope
contract. `ContentStep` uses:

```typescript
export function effectiveSelectedPhases(
  draft: GuidedRegenerationDraft,
  canonicalPhases: string[],
): string[] {
  return draft.mode === "full" ? [...canonicalPhases] : [...draft.selectedPhases];
}
```

Full mode hides phase checkboxes and says `Rebuilds all N content phases`. Selective mode renders existing dependency expansion. Exclusions, role details and extraction refresh live under Advanced; switching mode or changing requested/excluded phases resets `acknowledged=false`.

- [ ] **Step 4: Hydrate and persist at the route boundary**

Initialize state with a lazy `loadRegenerationDraft(window.localStorage)`. Save after every operator change. After books/eligible/manifest load, call `pruneRegenerationDraft` once per changed server input and surface its warning without a blocking modal. `Discard draft` clears storage and resets to the default. Successful campaign creation clears storage immediately; later cancel/reject does not restore it.

Fetch `api.getLaunchDefaults()` beside the model manifest. When the draft has
no explicit model (new draft or a restored null), set provider/model from the
launch-default content pair once. Track that initialization separately so a
subsequent refetch cannot overwrite an operator's model choice.

Replace the existing `defaultRegenerationDraft` comment that says the model is
never defaulted; this feature now intentionally uses the operator-controlled DB
launch default, not the first manifest entry.

Keep `/estimate` as its current reactive DB-only query. Add a separate disabled
TanStack query/mutation for `api.checkRegenerationDestinations` and run it only
when the operator clicks **Check Notion destinations** in Step 3. A change to
selected lesson IDs, output language, publication version or destination
overrides clears that result; phase, model, extraction and canary-size changes
do not. Never write the returned candidates/digest to local storage.

- [ ] **Step 5: Orchestrate one safe first-paid action**

Use a mutation whose variables are a frozen submitted request, not live React state:

```typescript
type CreateCanaryResult = {
  campaign: RegenerationCampaignDetail;
  canaryStarted: boolean;
  canaryError: unknown | null;
};

async function createAndStartCanary(
  request: RegenerationCampaignDraft,
  onCampaignCreated: (campaign: RegenerationCampaignDetail) => void,
): Promise<CreateCanaryResult> {
  const campaign = await api.createRegenerationCampaign(request);
  onCampaignCreated(campaign);
  try {
    return {
      campaign: await api.launchRegenerationCanary(campaign.id),
      canaryStarted: true,
      canaryError: null,
    };
  } catch (canaryError) {
    return { campaign, canaryStarted: false, canaryError };
  }
}
```

`onCampaignCreated` synchronously clears local storage, resets the in-memory
draft, calls `adopt(campaign)`, invalidates eligibility, and selects/navigates
to `campaign.id` before the canary request begins. This closes the browser-crash
window between the two requests. Canary success calls `forgetFailures` then
`adopt`; a response carrying `released_failures` passes through
`rememberFailures` exactly as today's mutations do. If canary fails, retain
`campaign.id`, show `Campaign created; canary not started`, and render only
`Retry canary`. Never re-run create from that state. If a restored pre-create
draft nevertheless hits `active_lineage_conflict`, link to the conflicting
existing campaign rather than presenting creation as retryable.

- [ ] **Step 6: Render the review and canary behavior**

Review first shows the DB-only estimate and worker result. Its **Check Notion
destinations** action warns that the complete bounded scan can take minutes and
shows checked/total counts. Only after every target resolves does it show exact
`Homework Vn`, target/canary counts, regenerated/copied phases, extract mode,
model, estimate, compatible worker count and every Notion decision/link. The
create button label is `Create campaign and start N canary lesson(s)` with
`First paid action` text. Notion-disabled/credential errors block it with the
server's configuration message.

Canary step reuses the existing phase progress, judge report, homework preview, approve and reject actions. Approval copy is `Approve canary and continue`; after approval, successful targets publish automatically. For one target, say `Approve and publish this lesson`; never display an empty bulk gate.

- [ ] **Step 7: Keep campaign history available without competing with the wizard**

Move `CampaignList` below the guided card or into a secondary `Previous campaigns` panel. Selecting an existing campaign opens step 4/report mode without overwriting or deleting the saved draft. Returning to `New campaign` restores the draft's saved step.

- [ ] **Step 8: Run frontend verification**

```bash
cd web
npm test
npm run build
```

Manually run the dev server and verify at 1440 px and 390 px widths: step labels do not wrap into controls, Lesson rows preserve title/context, Advanced is collapsed by default, and Review has exactly one primary paid-action button.

- [ ] **Step 9: Commit the reviewed task**

```bash
git add web/src/components/regeneration/guided-progress.tsx web/src/components/regeneration/lesson-step.tsx web/src/components/regeneration/content-step.tsx web/src/components/regeneration/review-step.tsx web/src/components/regeneration/canary-step.tsx web/src/components/regeneration/regeneration-wizard.tsx web/src/routes/regeneration.tsx web/src/lib/regeneration-state.ts web/src/lib/regeneration-state.test.ts web/src/lib/regeneration-draft.test.ts
git commit -m "feat(web): add guided persistent regeneration flow"
```

---

### Task 8: Whole-Branch Review and Isolated V3 Acceptance

**Files:**
- Modify only if verification exposes a defect, using a separate fix commit owned by the failing task.
- Create: `docs/testing/REGENERATION-V3-GUIDED-ACCEPTANCE.md`
- Modify: the repository's current worklog/index files only after implementation is accepted locally and their next IDs are re-resolved.

**Interfaces:**
- Consumes: every reviewed Task 1–7 commit integrated into `test/regen-v3-local`.
- Produces: reproducible offline evidence, one isolated live V3 canary result, Notion parent/version IDs, cost evidence and a final Opus 5 whole-branch verdict. It does not produce a merge.

- [ ] **Step 1: Repeat the collision gate and verify integrated ancestry**

```bash
git fetch --all --prune
git status --short --branch
git worktree list --porcelain
git log --oneline --decorate --graph --max-count=40
git diff --check
git diff --name-status ffde81eb..HEAD
```

`ffde81eb` is the immutable approved-design commit immediately before this plan.
Confirm no task branch contains commits absent from the preview branch and no
unrelated worktree was modified.

- [ ] **Step 2: Run fresh-database migration and backend gates**

Create one new explicit `127.0.0.1` database after confirming its name does not exist. Point only this worktree's `DATABASE_URL` at it, then run:

```bash
RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 uv run alembic upgrade head
RUN_DB_INTEGRATION=1 REGEN_REQUIRE_DB=1 uv run pytest -q \
  tests/migrations/test_regeneration_schema.py \
  tests/integration/test_regeneration_constraints.py \
  tests/integration/test_regeneration_source_and_version_queries.py \
  tests/integration/test_regeneration_publication_claims.py \
  tests/integration/test_regeneration_campaign_concurrency.py \
  tests/integration/test_regeneration_e2e.py \
  tests/integration/test_regeneration_failure_e2e.py
uv run pytest -q tests/services/test_regeneration_*.py tests/api/test_regeneration_*.py tests/schemas/test_regeneration_schemas.py
```

Expected: zero skips in DB commands. Record any pre-existing unrelated full-suite failures separately; a feature-owned failure blocks acceptance.

- [ ] **Step 3: Run frontend and full offline regression**

```bash
cd web
npm ci
npm test
npm run build
cd ..
uv run pytest -q
```

- [ ] **Step 4: Dispatch an independent Claude Opus 5 whole-branch review**

The reviewer receives the spec, this plan, base/head SHAs, full diff, test evidence, and these explicit questions:

```text
1. Can any path spend before exact version, worker compatibility and Notion destination pass?
2. Can create/canary retry create a duplicate campaign?
3. Can publisher choose a different Lesson Topic than Review showed?
4. Can a new campaign adopt another campaign's Homework Vn?
5. Can localStorage restore stale server-derived authority?
6. Does one-target approval complete without an empty bulk gate?
```

Critical/Major findings return to the owning task and repeat its RED/GREEN/review cycle. Do not proceed to live credentials on an unresolved finding.

- [ ] **Step 5: Build a test-specific environment without copying the whole `.env`**

Start from an empty explicit file outside tracked paths and copy only values
required for the test: `127.0.0.1` test `DATABASE_URL`, auth token,
`GEMINI_API_KEY`, Notion key and a mapping whose page ID is the dedicated
sandbox subject page. Set `REGENERATION_ENABLED=true`,
`REGENERATION_PUBLISHER_ENABLED=true`, `NOTION_ENABLED=true`, test-specific
ports/`VAR_DIR`, and safe bounded worker concurrency. Do not echo secret values
into logs or commit the environment file. Before starting, print only
`app.config.__file__`, the redacted database host/name and `settings.var_dir`;
assert the module path belongs to this worktree and the database host is exactly
`127.0.0.1`, preventing parent-directory `.env` discovery or the dual-local-PG
trap.

- [ ] **Step 6: Gate and prepare the dedicated Notion sandbox**

Resolve the exact sandbox subject page ID and child tree read-only. If no
dedicated sandbox exists, stop and ask the user to approve creating one; do not
substitute a curriculum subject page. Before any write, present the sandbox URL,
the one Lesson Topic title/ID to be reused, the intended `Homework V3` title,
and the exact cleanup action (archive that V3 page only). Record that approval
in the acceptance document. The live run is single-shot: do not repeat it until
the prior V3 page has been inspected and explicitly cleaned up or a new version
is chosen.

- [ ] **Step 7: Execute the isolated browser-restart and live V3 canary**

Use one source lesson whose test-DB TOC pointer is deliberately mapped to the
known sandbox Lesson Topic. Record IDs before starting. Then:

1. Select a book beyond the first 100 rows and a lesson.
2. Leave full rebuild selected, extraction refresh off, model at the launch-default `gemini-3.6-flash`, and version at V3.
3. Resolve any ambiguity by choosing the displayed existing Lesson Topic.
4. Refresh the browser and restart it; confirm the operator draft returns and server-derived readiness is re-fetched.
5. Confirm Review names one compatible worker and links the intended Lesson Topic.
6. Click the one paid action once; confirm exactly one campaign and one canary revision job exist.
7. Monitor all 11 content phases, existing judge bounded retries/fallback, spend and final snapshot.
8. Open the generated homework and judge report; approve the canary.
9. Confirm automatic publication creates exactly one `Homework V3` under the reviewed existing sandbox Lesson Topic, creates no duplicate Lesson Topic, preserves the sandbox's V1/V2 fixtures, and reaches terminal campaign state.
10. Confirm campaign/target reports show requested V3, actual V3 page link, source version, frozen parent decision and real `agent_usages` cost.

- [ ] **Step 8: Write the acceptance record**

`docs/testing/REGENERATION-V3-GUIDED-ACCEPTANCE.md` records: date, branch/head SHA, migration head, exact test commands/counts, test DB name, source/campaign/target/revision IDs, reviewed Lesson Topic ID, published V3 ID, prompt revision, model/worker, phase count, extraction mode, judge result, token/cost totals, screenshots or URLs without credentials, cleanup instructions and reviewer verdict.

- [ ] **Step 9: Commit evidence and stop**

```bash
git add docs/testing/REGENERATION-V3-GUIDED-ACCEPTANCE.md
git diff --cached --name-status
git commit -m "docs(regeneration): record guided V3 acceptance"
```

If a worklog entry is required by the repository's then-current convention,
resolve its next ID after the acceptance commit and stage only the exact new
entry and exact index file in a separate documentation commit. Never stage an
entire memory directory. Stop with the feature local and unmerged. Present the
user with the complete evidence and ask for a separate push/PR/integration
decision.

## Final Acceptance Checklist

- [ ] Branch-collision record is current and no project-manager-owned PR was changed.
- [ ] Every Task 1–7 commit passed implementation review, spec review, quality review and Codex rerun before integration.
- [ ] One single Alembic head upgrades on a fresh `127.0.0.1` database with zero skipped regeneration DB tests.
- [ ] New campaign version is exact and immutable; V1 can intentionally produce V3; no silent version increment exists. This cross-table invariant is service-enforced (not a PostgreSQL cross-table CHECK) and is proven by the Task 2 service/reservation race tests.
- [ ] Review and creation reject source/equal, consumed-DB and existing-Notion version conflicts.
- [ ] Review shows exact Lesson Topic decision; ambiguity requires a validated operator choice; publisher uses and revalidates the frozen decision.
- [ ] Worker preflight agrees with actual claim behavior, including self-grade/self-solve fallback credentials.
- [ ] Browser restart restores operator input, prunes stale selections, resets exclusion acknowledgement and re-fetches all authority.
- [ ] All books beyond the first 100 are reachable within the bounded pagination limit.
- [ ] Full rebuild and extraction reuse are the defaults; selective dependency expansion remains available under the secondary mode.
- [ ] Create-plus-canary partial success can retry only the existing campaign.
- [ ] Canary review is the only content gate; approval auto-publishes and a one-target campaign has no empty bulk gate.
- [ ] One isolated live V3 test creates one version page under the reviewed existing Lesson Topic and no duplicate Lesson Topic.
- [ ] Backend, frontend, fresh-DB, full offline and independent Opus 5 whole-branch gates are recorded.
- [ ] Nothing was pushed, merged into `Nggaev-v2`, deployed or enabled in production.
