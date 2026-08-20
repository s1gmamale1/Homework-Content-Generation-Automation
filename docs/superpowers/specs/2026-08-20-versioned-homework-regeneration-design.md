# Versioned Homework Regeneration Design

**Date:** 2026-08-20

**Status:** Approved by the user and independent design review

**Feature flag:** `REGENERATION_ENABLED=false` until a separate rollout decision
**Supersedes:** the prompt-set-selection and replace-current-publication assumptions in `docs/superpowers/plans/2026-08-17-selective-regeneration-campaigns.md`

## 1. Decision Summary

The system will support selective regeneration of already-generated homework without replacing any existing Notion content.

Each successful, approved regeneration creates a new versioned Notion child page beside the original homework:

```text
Lesson Topic
├── Homework
├── Homework V2
├── Homework V3
└── ...
```

The existing `Homework` page is treated as V1 and is never modified by this feature. The first published regeneration is V2, the next is V3, and so on.

There is one human quality gate: the canary review. Canary revisions remain unpublished until the operator approves the campaign. After approval, the canaries publish and every remaining successful revision publishes automatically. There is no per-lesson publication approval.

The regenerator always uses the currently deployed prompt files. Old prompts are not selectable and are retained only through Git history and stored prompt hashes. The prompt-set registry in PR #136 is not a dependency of this design.

Normal Fleet generation and its existing Notion archival behavior remain unchanged. Regeneration publication uses its own small, durable target-state queue; the global durable archive outbox at `feat/selective-regeneration-preview@a5666f6` is not required.

## 2. Goals

1. Regenerate one or many already-completed lessons with the current prompts.
2. Regenerate selected phases while producing a complete revision snapshot.
3. Include affected downstream phases automatically, with a deliberate warning-backed exclusion override.
4. Make extraction optional and off by default.
5. Preserve the current judge, retry, repair, solver, cost and usage-accounting behavior.
6. Review a small canary before spending on or publishing the full campaign.
7. Publish approved revisions automatically as `Homework V2`, `Homework V3`, and later pages.
8. Preserve every previously published homework version.
9. Keep regeneration visibly separate from normal Fleet generation in the API and UI.
10. Make generation and Notion publication failures independently visible and retryable.

## 3. Non-Goals

1. Replacing, clearing, renaming or deleting an existing Notion homework page.
2. Selecting between old and new prompt sets.
3. Editing prompts in the database or through the regeneration UI.
4. Retrofitting normal Fleet archival onto a global outbox.
5. Publishing a partial revision.
6. Per-lesson publication approval after the campaign canary is approved.
7. Deleting earlier V1/V2/V3 pages when a later version publishes.
8. Running multiple active regeneration campaigns for the same lesson and output language.

## 4. Operator Workflow

### 4.1 Create and estimate

The operator opens a dedicated **Regeneration** area, not the Fleet batch launcher, and:

1. Selects one or many completed source lessons.
2. Selects phases to regenerate.
3. Reviews the automatically included downstream phases.
4. Optionally excludes affected phases after acknowledging a consistency warning.
5. Optionally enables extraction; extraction defaults off.
6. Reviews an estimate showing:
   - target lesson count;
   - selected, automatically included and explicitly excluded phases;
   - regenerated versus copied phase counts;
   - expected model calls and estimated cost;
   - canary size;
   - each lesson's expected next publication version.

Before canary launch, a regeneration-scoped Notion preflight verifies that each target either already has a known Lesson Topic page or has a resolvable subject/grade/language destination from which that parent can be created. Missing destinations are returned as one actionable list and block launch before model spend. This preflight does not alter normal Fleet generation.

Creating or estimating a campaign makes no model calls and creates no Notion pages.

### 4.2 Generate the canary

The operator launches a small canary. Canary targets:

1. Create separate revision jobs linked to their completed source jobs.
2. Copy unchanged phases into the revision snapshot.
3. Regenerate selected phases and non-excluded downstream phases.
4. Run the existing judge and solver behavior.
5. Record actual usage and cost from the revision jobs.
6. Stop in `awaiting_canary_approval` without publishing to Notion.

Only canary targets receive revision job rows before approval. Non-canary targets remain campaign-target rows with no pending homework job, so ordinary workers cannot claim bulk work before the human gate.

The canary review shows the complete revised homework, phase provenance, judge statuses, warnings, latency and actual cost.

### 4.3 Approve or reject

**Reject:** cancel the campaign. Canary revisions remain available for audit in the application, but no Notion page is created and no publication version is consumed.

**Approve:** mark the campaign approved once. Approval:

1. Makes successful canary targets eligible for automatic publication.
2. Releases the remaining targets for generation.
3. Makes each later successful target eligible for automatic publication as soon as its complete snapshot is ready.

Approval creates/releases revision jobs for the remaining targets exactly once. A repeated approval request cannot create duplicate jobs.

For a one-lesson campaign, that lesson is the canary. The flow is generate, review, approve, publish. There is no empty bulk-approval step.

### 4.4 Complete and report

The campaign report separates:

- generated and published;
- generated but publication pending;
- generated but publication failed;
- generation failed;
- cancelled or abandoned before completion.

It also reports copied/regenerated phase counts, judge-status counts, token usage, API cost, Notion version and Notion page link per lesson and output language.

## 5. Phase Selection and Complete Snapshots

### 5.1 Source eligibility

A source job must be a completed homework job with a complete usable phase snapshot. A failed, cancelled, partial or teacher-material job cannot be used as a source.

Every regeneration lineage is scoped by `(toc_entry_id, output_language)`. UZ, RU and EN homework for the same TOC lesson have independent sources, active-campaign locks and V2/V3 sequences.

For V3 and later, the default source is the latest successfully published homework revision for that lesson and output language. If no regeneration version has been published in that language, the source is the existing completed V1 job in that language.

`revision_of_job_id` records the immediate source job and is required for every regeneration job.

### 5.2 Dependency closure

The existing `PHASE_DEPS` graph is the authority. Selecting a phase automatically includes its transitive downstream dependents. The UI must show the real expansion before launch, including the known examples:

- `flashcards` regenerates 10 of 11 content phases;
- `memory-check` regenerates 5 phases;
- `boss-arena` regenerates 2 phases;
- `reflection` regenerates only itself.

The feature must not describe an early-phase selection as cheap or isolated when the graph makes it nearly a full regeneration.

### 5.3 Exclusion override

The operator may exclude automatically included downstream phases. This can create a snapshot where copied content was authored against an older upstream output. The UI must:

1. Identify each excluded affected phase.
2. Explain that the resulting homework may be internally inconsistent.
3. Require an explicit acknowledgement before launch.
4. Persist the exclusion and acknowledgement in the immutable campaign specification.

### 5.4 Extraction

Extraction defaults off. When off, the source extract is copied with provenance and incurs no extraction cost.

When extraction is enabled, every content phase is downstream of the new extract and is automatically included for regeneration. The same warning-backed exclusion override remains available, but the estimate must make the near-full regeneration cost explicit.

### 5.5 Snapshot rules

Every successful revision contains exactly one terminal row for every required phase:

- regenerated rows contain new content, usage and current prompt provenance;
- copied rows preserve content, judge state and a link to their source phase row;
- no phase row is silently borrowed at render or publication time;
- a partial snapshot cannot transition to publication-pending.

This lets downloads, reports and Notion rendering read one self-contained revision job.

Copied rows are seeded as `done` at their canonical positions in the complete subject flow. Regenerated phase rows are left absent for the existing pipeline to create. Internal revision jobs use the full-flow pipeline shape (`selected_phases=NULL`), not the normal custom-prompt subset contract: the existing resume logic skips seeded copied rows and runs only the missing rows while preserving canonical `phase_order`. The selected/affected/excluded regeneration plan lives on the immutable campaign target, not in `HomeworkJob.selected_phases`.

This deliberately bypasses the normal `/generate` rule that a selected phase requires an uploaded custom prompt. Regeneration uses built-in currently deployed prompts and is created only through its own service/router.

## 6. Prompt Behavior and Provenance

There is one active prompt tree: the prompt files deployed with the running application.

The regeneration UI exposes no prompt-set selector. A campaign records:

- the application Git revision;
- the resolved prompt hash for every regenerated phase;
- any per-job custom prompt already supported by the normal generation contract;
- the source phase provenance for copied phases.

Old prompt text is available through Git history. PR #136's repository-backed prompt-set registry is intentionally outside this design.

The operator guarantees that no ordinary generation is running during a prompt cutover. This design therefore adds no mixed-deployment draining or old-prompt compatibility mechanism.

## 7. Judge and Repair Semantics

Regeneration preserves the existing pipeline rules exactly; it does not introduce a stricter publication gate.

For each regenerated judged phase:

1. A transiently unavailable judge is retried once.
2. If still unavailable, the phase is retained with `judge_status="unavailable"`.
3. A content-policy refusal is not retried and is retained with `judge_status="refused"`.
4. A major finding triggers regeneration and re-judging up to the configured `max_judge_regens` budget.
5. If major findings remain after that budget, the best final artifact is retained with `judge_status="major_shipped"`.
6. A non-critical repair-generation failure retains the best available artifact with `judge_status="major_regen_failed"`.
7. Existing authentication/configuration failures retain their current hard-failure behavior.

The soft statuses `unavailable`, `refused`, `major_shipped` and `major_regen_failed` do not make the snapshot incomplete and do not independently block automatic publication after canary approval. They are prominent in canary review and campaign reporting.

A real phase-generation failure, missing required phase, invalid structured artifact or other existing hard pipeline failure prevents publication.

The existing terminal solver outcome `solver_status="mismatch_blocked"` persists the inspected phase as failed and maps the regeneration target to `generation_failed`; it is not a soft judge warning and cannot publish.

Copied phases keep their recorded judge status; they are not re-judged and do not incur judge cost.

## 8. Data Model

Exact column names may be adjusted during the implementation plan, but these invariants are required.

### 8.1 Regeneration campaign

`RegenerationCampaign` owns:

- immutable selection specification;
- requested phases;
- dependency expansion;
- exclusions and acknowledgement;
- extraction choice;
- canary membership;
- model/transport selections inherited from the approved launch contract;
- estimated cost;
- lifecycle state and timestamps;
- approval/rejection/cancellation audit fields.

Suggested lifecycle:

```text
draft
→ canary_running
→ awaiting_canary_approval
→ approved / rejected
→ bulk_running
→ attention_required
→ completed / completed_with_abandonments / cancelled
```

Campaign completion is derived from terminal targets; it is not based only on job completion.

### 8.2 Regeneration target

`RegenerationTarget` owns one lesson within one campaign:

- campaign ID;
- lesson/TOC entry ID;
- output language;
- source job ID;
- revision job ID;
- canary membership;
- generation status;
- publication status;
- reserved publication version;
- Notion page ID;
- publication attempt count, last error and retry timing;
- terminal reason where applicable.

Generation and publication states remain separate. A generated revision is never regenerated merely because Notion delivery failed.

Suggested target outcomes:

```text
planned
generating
awaiting_canary_approval
publication_pending
publishing
published
generation_failed
publication_failed
abandoned
```

Target terminality is explicit:

- `published` and `abandoned` are terminal and set `terminal_at`;
- every other target state is non-terminal and keeps `terminal_at=NULL`;
- `generation_failed` and `publication_failed` are attention-required, retryable, non-terminal states;
- the cross-campaign uniqueness constraint is a partial unique index on `(toc_entry_id, output_language)` where `terminal_at IS NULL`.

A generation failure therefore blocks a competing campaign for the same lesson and output language until the operator retries the existing target or explicitly abandons it. Generation abandonment records a reason, creates no Notion page, consumes no publication version when publication never began, and transitions the target to terminal `abandoned`.

A campaign cannot report terminal completion while any target is attention-required. It reports `attention_required` until every failed target is retried to `published` or explicitly moved to `abandoned`. A terminal campaign distinguishes all-published completion from completion with abandoned targets.

### 8.3 Revision job

A regeneration job remains a homework job so it can reuse the existing pipeline, phase, cost and download machinery. It additionally has:

- required `revision_of_job_id`;
- required regeneration campaign/target identity;
- a regeneration marker that makes the pipeline skip normal automatic Notion archival.

The marker-column design is pinned: a revision remains `kind="homework"`, and `revision_of_job_id IS NOT NULL` identifies it as a regeneration job. This avoids widening the existing `kind` flow discriminator while giving every legacy query and archive entry point one unambiguous exclusion predicate.

Revision jobs are not normal Fleet jobs. They must:

- keep `batch_id=NULL` and never become members of normal Fleet batches;
- be excluded from normal `find_active_for_section`, `latest_for_section`, `latest_by_section`, batch adoption/resume, TOC status enrichment and prior-cost/dedup queries;
- appear in lesson history and campaign aggregates only through regeneration-aware repositories and API responses;
- remain eligible for the existing worker claim and pipeline machinery after the regeneration campaign explicitly creates/releases them.

Existing by-ID worker, cancellation, phase, download and SSE machinery may operate on a revision job where that behavior is required for pipeline reuse. Existing by-ID archive operations are the explicit exception and must reject it.

The database must reject a regeneration job with a normal Fleet `batch_id`. The implementation must not rely on callers remembering to avoid this combination.

The source-job foreign key uses restrictive deletion semantics while a revision exists. Campaign reporting may keep nullable historical source links after an explicitly ordered child-first purge, but deleting a source out from under a live revision must fail cleanly rather than surface a raw database error.

For this release, the existing book-delete route returns a clear `409` when regeneration history exists rather than attempting an implicit multi-table purge. A future explicit purge may delete revision children first, but raw foreign-key errors and automatic audit-history destruction are not acceptable.

### 8.4 Integrity and concurrency

Database constraints must enforce:

- a revision job always has a source job and campaign target;
- one target per campaign and lesson;
- no more than one non-terminal regeneration target for a lesson and output language across campaigns;
- one publication version per lesson, output language and version number;
- one revision job cannot be attached to multiple targets;
- a published target has a version and Notion page ID;
- a target cannot publish before campaign approval.
- a revision job cannot carry a normal Fleet batch ID.

## 9. Version Allocation

The existing `Homework` page is logical version 1. It is not renamed.

The next version is reserved atomically when publication first begins, not when the campaign or canary is created. Therefore:

- the first allocated database version is 2 because logical V1 has no version row;
- a rejected canary consumes no version;
- a never-started or generation-failed target consumes no version;
- once publication begins, its version is never reused, even if delivery fails or the campaign is later cancelled;
- every retry uses the same reserved version and page identity;
- a later campaign is blocked while an earlier version for the lesson is active or retryable.

The allocator must serialize on `(toc_entry_id, output_language)` and enforce a unique `(toc_entry_id, output_language, publication_version)` constraint. It must not infer authority from page titles alone. UZ V2 and RU V2 are valid independent publications.

## 10. Notion Publication

### 10.1 Isolation from normal archival

Normal jobs continue through the existing `notion_archive.archive_job` path.

When the pipeline completes a regeneration job, it finalizes the revision target and deliberately skips the normal `Homework` archive call.

That pipeline branch is an optimization, not the load-bearing V1 guard. The existing legacy `notion_archive.archive_job` function itself must inspect the loaded job and refuse every job with `revision_of_job_id IS NOT NULL`, regardless of `force`, claim token or caller. It records a deterministic skip reason and performs no Notion read or write. This intrinsic guard covers automatic pipeline archival, per-job retry/force-rearchive, batch force sweeps and any future caller.

Operator-facing legacy archive endpoints must reject a revision job synchronously with a clear conflict response instead of launching background work that will only be refused later. Batch re-archive selection must exclude revision jobs defensively even though revision jobs cannot have `batch_id`.

### 10.2 Versioned renderer and page identity

The versioned publisher reuses the existing homework rendering and upload primitives but writes a sibling child page named `Homework V{n}`.

Each page includes a machine-readable marker containing at least:

- lesson/TOC entry ID;
- output language;
- revision job ID;
- regeneration campaign ID;
- publication version.

The stored page ID is authoritative after creation. On retry, the publisher:

1. Uses the stored page ID when available.
2. Otherwise searches for the exact immutable revision marker.
3. Adopts a matching page created before a crash.
4. Creates a page only when neither exists.

Title matching alone is insufficient for adoption.

The implementation may enumerate the Lesson Topic's child pages to find the exact `Homework V{n}` title, but it must then read that candidate's blocks and validate the immutable marker before adoption. A same-title page with a different or missing marker is a visible collision failure and is never cleared, overwritten or silently adopted. The generic legacy `find_or_create(..., "Homework")` path is not reused without this validation.

### 10.3 Regeneration-scoped durable publisher

Automatic publication is driven by `RegenerationTarget` rows rather than a global archive-intents table.

A single head-side regeneration publication loop:

1. Claims approved `publication_pending` or retry-due `publication_failed` targets with a bounded lease.
2. Reserves the next version atomically if the target has none.
3. Builds the complete revision payload before any destructive or remote write.
4. Creates or adopts the exact versioned page.
5. Persists the page ID and `published` state.
6. Records bounded retry/backoff state on failure.
7. Recovers expired leases after a process crash.

The publisher revalidates its Notion destination when it claims a target because configuration may have changed after the campaign preflight.

This is intentionally narrower than Group 1's global outbox: it handles regeneration targets only and does not change ordinary homework or teacher-material archival.

### 10.4 Publication retries and terminal failures

Transient Notion failures retry automatically with bounded exponential backoff. After the automatic budget is exhausted, the target remains `publication_failed` and exposes an operator retry action.

A permanently failed publication remains unresolved and blocks a later campaign for that lesson until the operator either retries it or explicitly abandons that publication. Abandonment is an operational failure resolution, not a content-approval gate: it never publishes or deletes a page, records the reason, and never reuses a version that was already reserved.

Retrying publication:

- never calls Gemini;
- never creates a new revision job;
- never allocates a new version;
- never modifies V1 or earlier version pages;
- must be idempotent across timeouts and process crashes.

## 11. Cancellation and Rejection

Rejecting the canary:

- prevents every publication;
- prevents release of non-canary targets;
- consumes no publication versions;
- retains canary revision records for audit.

Cancelling an approved campaign:

- prevents planned targets from launching;
- requests cancellation through the existing safe job-cancellation path for active generation;
- abandons complete but not-yet-claimed publication targets;
- does not interrupt an unknown-outcome Notion request merely by deleting state;
- preserves already-published pages;
- preserves any version already reserved once publication began.

No cancellation path deletes a Notion page.

## 12. Cost Accounting

The estimate counts only work expected from revision jobs:

- copied phases cost zero;
- regenerated phases use the regeneration estimator defined below;
- copied extract costs zero;
- extraction enabled adds extract cost;
- judge and repair estimates follow current configuration;
- Notion publication has no model-token cost.

Actual campaign cost is derived only from `agent_usages` attached to revision jobs. Copied phases never duplicate source usage records or cost. A Notion publication retry never increases model cost.

The regeneration estimator uses successful API `agent_usages` from the previous 30 days, joined through `phase_output_id` to phase name, and computes an observed mean for matching operation, phase, provider and model. Where no matching history exists, a documented conservative token envelope is priced with the existing static pricing table. Expected calls include one authoring and one judge call per regenerated judged phase, solver calls for solver-enabled phases, and extraction only when enabled. The high estimate adds the existing schema retry and configured judge/solver regeneration budgets. Estimates are explicitly labeled estimates.

When extraction is copied, the revision records the existing zero-cost cache-usage marker with source job and source phase IDs. That marker is provenance, not a paid model call, and is excluded from real-call counts. Other copied phases keep row-level provenance and do not clone usage rows.

The canary screen compares estimated and actual cost before approval.

## 13. API and UI Boundaries

### 13.1 API responsibilities

The regeneration API is a separate router and namespace. It provides operations for:

- discovery of eligible lessons;
- phase-plan preview and dependency expansion;
- cost estimation;
- campaign creation;
- canary launch;
- canary approval or rejection;
- campaign cancellation;
- campaign and target reports;
- publication retry;
- generation retry;
- explicit abandonment of a permanently failed generation or publication.

Normal `/generate`, Fleet batch launch and ordinary archive-retry endpoints do not become regeneration controls.

All mutation endpoints are idempotent and enforce legal state transitions server-side.

### 13.2 UI responsibilities

The UI must always say whether the operator is **Generating** or **Regenerating**. The regeneration flow has its own entry point and campaign list.

Before canary launch it shows:

- selected lessons;
- source version;
- expected next version;
- selected, included and excluded phases;
- extraction choice;
- cascade size and cost consequences;
- inconsistency acknowledgements;
- estimated cost.

The canary review shows full revised content, source/revision provenance, judge statuses and actual cost. One campaign-level approval starts automatic publication and remaining generation.

The report always renders human-readable reasons for failure or abandonment rather than requiring an operator to interpret internal status codes.

## 14. Failure Handling

| Failure | Result | Retry behavior |
|---|---|---|
| Hard phase generation failure | No publication; target `generation_failed` | Existing safe job retry or explicit target retry |
| Soft judge status | Complete snapshot remains eligible after campaign approval | Reported; no automatic generation retry beyond existing judge budget |
| Canary rejected | No publication and no bulk launch | New campaign required |
| Notion mapping/config missing at launch | Canary launch is blocked before model spend | Fix configuration, rerun preflight |
| Notion configuration changes after launch | No model rerun; publication fails visibly | Fix configuration, retry publication |
| Transient Notion failure | Revision preserved; target retryable | Automatic bounded retry, then operator retry |
| Crash after page creation before DB stamp | Target lease expires | Marker-backed adoption of the same page/version |
| Duplicate approval request | No duplicate launch or page | Idempotent success/current state |
| Concurrent campaign for same lesson and output language | Second campaign rejected before spend | Retry after first becomes terminal |
| Campaign cancellation | Unfinished work stops; published pages remain | No automatic resume |

Both `generation_failed` and `publication_failed` remain blocking until retry or explicit abandonment. Abandonment is audited and never deletes a Notion page.

## 15. Testing Strategy

All automated model and Notion integrations use fakes. Development must not call paid models, production databases or live Notion.

### 15.1 Pure unit tests

- dependency closure and deterministic ordering;
- exclusion validation and acknowledgement;
- extraction expansion;
- copied/regenerated phase accounting;
- estimate math;
- campaign and target state transitions;
- version allocation;
- marker encoding and matching;
- retry/backoff decisions;
- campaign rollups and report reason rendering.

### 15.2 Repository and migration tests

- required revision/source/campaign relationships;
- unique active target per lesson and output language;
- unique publication version per lesson, output language and version number;
- source deletion rejects cleanly while revisions exist;
- concurrent version allocation;
- durable publication claim and expired-lease recovery;
- idempotent approval, cancellation and retry writes;
- one Alembic head from the actual implementation base.

### 15.3 Service and pipeline tests

- normal generation still calls the legacy archive path unchanged;
- regeneration completion never calls the legacy `Homework` publisher;
- the legacy publisher intrinsically refuses revision jobs even with `force=True`;
- per-job retry/force-rearchive and batch force-sweep paths reject or exclude revision jobs;
- normal Fleet launch/resume never adopts a revision job and never attaches one to a batch;
- `latest_for_section`, `latest_by_section`, TOC enrichment and normal prior-cost lookup exclude revision jobs;
- copied phases form a complete snapshot without model calls;
- copied rows plus missing regenerated rows preserve canonical phase order without uniqueness collisions;
- selected phases and dependency closure regenerate exactly once;
- current judge retry/repair/soft-degrade statuses are preserved;
- hard incomplete snapshots cannot publish;
- canaries remain unpublished before approval;
- approval releases canary publication and bulk generation once;
- no non-canary job row exists before approval;
- bulk successes become publication-pending automatically;
- Notion retries never rerun Gemini;
- V2 then V3 create distinct sibling pages;
- crash recovery adopts the exact marker-backed page;
- cancellation never deletes an existing page.

### 15.4 API and UI tests

- generation and regeneration are visibly distinct;
- single-lesson campaigns have no empty bulk gate;
- cascade count and cost warnings are prominent;
- exclusion acknowledgement is mandatory;
- canary approval/rejection is idempotent;
- report buckets and reasons are complete;
- publication failure retry is visible;
- generation failure retry/abandonment is visible;
- permanently failed publication can be explicitly abandoned without reusing its version;
- feature flag off hides UI and rejects mutation endpoints.

### 15.5 End-to-end acceptance

Using fake Gemini and fake Notion:

1. Start from an existing V1 lesson.
2. Regenerate selected phases into a complete canary revision.
3. Prove no Notion call occurs before approval.
4. Approve and publish `Homework V2`.
5. Run a later campaign in the same output language and publish `Homework V3`.
6. Independently publish `Homework V2` for a second output language.
7. Prove V1 and V2 were never cleared or rewritten.
8. Inject a Notion timeout after page creation and prove retry adopts one V3 page.
9. Verify cost counts only actual revision model usage.

## 16. Rollout

1. Land schema and backend behavior with `REGENERATION_ENABLED=false`.
2. Land the separate UI hidden by the same flag.
3. Run fake-provider acceptance and migration tests.
4. Deploy flag-off and verify normal Fleet generation and archival are unchanged.
5. Run a bounded, explicitly authorized sample campaign with the current prompts.
6. Review cost, judge-status distribution, page layout and retry behavior.
7. Enable the feature only through a separate operator rollout decision.

No prompt deployment, production campaign, paid model call, live Notion write or feature-flag change is authorized by implementation approval alone.

## 17. Repository Collision and Integration Order

Collision gate refreshed 2026-08-20:

- Planning branch: `plan/selective-regeneration-campaign`, rebased onto `origin/Nggaev-v2@a955cfebbf5bc8456974a9f40a95fc4852c866e4` before this specification was written.
- PR #136 / `feat/prompt-set-registry@b1f54d6` overlaps prompt routing and is superseded by the single-current-prompt decision. It must not be merged as a prerequisite.
- `feat/selective-regeneration-preview@a5666f6` contains the broad global outbox and prompt registry. It remains a shelved reference, not an implementation base.
- `feat/selective-regeneration-integration@f2b325f` and the retained Claude worktree contain the earlier outbox stack and remain untouched.
- `origin/pigganigeon@e0e499d` contains the current candidate prompt edits and remains a separate, unmerged prompt-review lane.
- PRs #117/#118 overlap later pipeline, prompt and phase-output paths. Before implementation, the collision gate must be repeated against their then-current state and an explicit integration order recorded.
- PRs #108, #128 and #131 do not overlap this design's core runtime paths based on the refreshed inspection.

Implementation must start from a newly verified current base in isolated worktrees. No agent may push, merge, update a PR, deploy, access a production database, call live Notion or make paid model calls unless the user separately authorizes that action.

## 18. Agent Execution and Review Contract

After this specification is approved:

1. Write a detailed implementation plan with exact paths, tests, dependency gates and commit boundaries.
2. Dispatch a separate external Claude Fable agent to review the complete plan before implementation.
3. Verify each Fable finding against the repository and revise the plan where warranted.
4. Present the reviewed plan to the user for implementation approval.
5. Verify that the Claude CLI exposes the exact requested Opus 5 model; do not silently substitute another model.
6. Dispatch external Claude Opus 5 controllers in isolated worktrees only after approval.
7. Require each controller to use Claude's own subagent-driven development workflow: fresh implementer, tests first, independent reviewer, fix rounds and verification.
8. The primary session acts as operations manager: collision scans, ownership boundaries, dependency sequencing, monitoring, independent diff/test gates and user approval before integration.
9. Codex collaboration subagents are not used for implementation.
