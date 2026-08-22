# Regeneration Model Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Settings-default and four-role override model selection to regeneration campaigns.

**Architecture:** Keep the server contract unchanged and add a small frontend domain layer that resolves effective Content, Judge, Solver, and Extract pairs from either `/settings` or browser-persisted overrides. Feed those four explicit pairs into the existing immutable regeneration launch contract and render the same resolved choices in the wizard and review step.

**Tech Stack:** React 19, TypeScript, TanStack Query, Node test runner, React server rendering, Vite.

**Spec:** `docs/superpowers/specs/2026-08-22-regeneration-model-selection.md`

## Global Constraints

- Regeneration remains API-only for Content, Judge, Solver, and Extract.
- Defaults are read-only on this screen; `/settings` remains their sole editor.
- A created campaign receives four explicit provider/model pairs and is immutable.
- Existing saved lesson, phase, destination, and model choices must survive reload.
- No backend schema or database migration is introduced.
- Work stays on `feat/regeneration-model-selection-defaults` and reaches `Nggaev-v2` only through a pull request.

---

### Task 1: Four-role draft and effective contract

**Files:**
- Create: `web/src/lib/regeneration-model-selection.ts`
- Create: `web/src/lib/regeneration-model-selection.test.ts`
- Modify: `web/src/lib/regeneration-draft.ts`
- Modify: `web/src/lib/regeneration-draft.test.ts`

**Interfaces:**
- Consumes: `LaunchDefaults`, `ProviderModelManifest`, and `RegenerationLaunchContract` from `web/src/lib/types.ts`.
- Produces: `RegenerationModelSelectionMode`, `effectiveRegenerationModels(draft, defaults)`, `regenerationModelSelectionIssue(draft, defaults, manifest)`, and `regenerationLaunchContract(draft, defaults)`.

- [ ] **Step 1: Write failing draft tests**

Add literal assertions proving a blank draft defaults to `settings`, a legacy saved draft with a content model decodes as `override`, all four override pairs round-trip, and pruning clears each retired override model independently.

- [ ] **Step 2: Run the draft test and verify RED**

Run: `npm test -- --test-name-pattern='model|draft'`

Expected: FAIL because the mode and role fields/functions do not exist.

- [ ] **Step 3: Implement the backward-compatible draft fields**

Add these fields to `GuidedRegenerationDraft` and its default/decoder/pruner:

```ts
modelSelectionMode: "settings" | "override";
judgeProvider: string | null;
judgeModel: string | null;
solverProvider: string | null;
solverModel: string | null;
extractProvider: string | null;
extractModel: string | null;
```

Replace the content-only initializer with `initializeDraftModels`, which fills only
missing override pairs from a complete `LaunchDefaults` value and never overwrites a
restored explicit choice.

- [ ] **Step 4: Run the draft test and verify GREEN**

Run: `npm test -- --test-name-pattern='model|draft'`

Expected: PASS.

- [ ] **Step 5: Write failing effective-selection tests**

Test these hand-derived contracts:

```ts
// Settings mode uses the four defaults and pins every transport to API.
assert.deepStrictEqual(regenerationLaunchContract(settingsDraft, defaults), {
  provider: "gemini",
  model: "gemini-3.6-flash",
  transport: "api",
  judge_provider: "gemini",
  judge_model: "gemini-3.6-flash",
  judge_transport: "api",
  solver_provider: "gemini",
  solver_model: "gemini-3.5-flash-lite",
  solver_transport: "api",
  extract_provider: "gemini",
  extract_model: "gemini-3.5-flash-lite",
  extract_transport: "api",
  session_limit_strategy: "inherit",
});
```

Also prove override mode ignores Settings pairs, a missing/retired/non-API pair returns
an actionable issue, and an API-only provider is rejected for Extract.

- [ ] **Step 6: Run the selection test and verify RED**

Run: `node --import tsx --test src/lib/regeneration-model-selection.test.ts`

Expected: FAIL because `regeneration-model-selection.ts` does not exist.

- [ ] **Step 7: Implement the effective-selection domain layer**

Resolve four nullable pairs from the selected mode, validate them against
`manifest.providers`, `manifest.api_supported`, and `manifest.api_only`, and build the
existing `RegenerationLaunchContract` with literal API transports.

- [ ] **Step 8: Run Task 1 tests and commit**

Run:

```bash
npm test
npm run build
git add web/src/lib/regeneration-model-selection.ts web/src/lib/regeneration-model-selection.test.ts web/src/lib/regeneration-draft.ts web/src/lib/regeneration-draft.test.ts
git commit -m "feat(regeneration): model four-role campaign choices"
```

Expected: tests and build PASS.

### Task 2: Defaults and override controls

**Files:**
- Create: `web/src/components/regeneration/model-selection.tsx`
- Create: `web/src/lib/regeneration-model-selection-view.test.ts`
- Modify: `web/src/components/regeneration/content-step.tsx`
- Modify: `web/src/components/regeneration/regeneration-wizard.tsx`

**Interfaces:**
- Consumes: Task 1's draft fields and selection helpers plus `LaunchDefaults` and `ProviderModelManifest`.
- Produces: `RegenerationModelSelection`, a controlled React component receiving `draft`, `defaults`, `manifest`, and `onChange`.

- [ ] **Step 1: Write failing rendered-view tests**

Use `renderToStaticMarkup(createElement(...))` against the real component. Assert that
Settings mode renders four current default pairs and an `/settings` link without four
editable role selectors, while Override mode renders one provider and one model select
for each named role plus an inline validation problem.

- [ ] **Step 2: Run the rendered-view test and verify RED**

Run: `node --import tsx --test src/lib/regeneration-model-selection-view.test.ts`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the controlled model-selection component**

Render two mode cards, the Settings summary, and four compact override rows. Filter
providers to API-supported entries and exclude `manifest.api_only` entries only from
the Extract row. On provider changes, clear that role's model; preserve all other roles.

- [ ] **Step 4: Wire the component into the Content step**

Pass `launchDefaults` through `RegenerationWizard` into `ContentStep`; remove the old
single Content provider/model picker. Disable Continue using the actionable selection
issue rather than `draft.model` alone.

- [ ] **Step 5: Run Task 2 tests and commit**

Run:

```bash
npm test
npm run build
git add web/src/components/regeneration/model-selection.tsx web/src/components/regeneration/content-step.tsx web/src/components/regeneration/regeneration-wizard.tsx web/src/lib/regeneration-model-selection-view.test.ts
git commit -m "feat(regeneration): add defaults and override model UI"
```

Expected: tests and build PASS.

### Task 3: Route, review, and immutable payload integration

**Files:**
- Modify: `web/src/routes/regeneration.tsx`
- Modify: `web/src/components/regeneration/review-step.tsx`
- Modify: `web/src/lib/regeneration-api.test.ts`
- Modify: `web/src/lib/regeneration-state.test.ts`

**Interfaces:**
- Consumes: Task 1's `regenerationLaunchContract` and `regenerationModelSelectionIssue`; Task 2's wizard props.
- Produces: estimate and campaign-create requests carrying the same explicit four-role contract and a review summary of its source and values.

- [ ] **Step 1: Write failing integration tests**

Extend the frontend contract fixtures with four explicit provider/model pairs. Add pure
assertions proving Settings mode and Override mode yield the intended request contract,
and keep the existing source-level API-transport guard for every role.

- [ ] **Step 2: Run the integration tests and verify RED**

Run: `npm test -- --test-name-pattern='regeneration'`

Expected: FAIL because the route still emits null role pairs and review renders one model.

- [ ] **Step 3: Wire one effective contract through the route**

Make `estimateRequest` and `campaignDraft` receive the loaded defaults and call
`regenerationLaunchContract`. Gate estimation and wizard navigation on
`regenerationModelSelectionIssue(...) === null`. Reset a completed/discarded draft with
`initializeDraftModels` so Override starts prefilled without changing Settings mode.

- [ ] **Step 4: Render the effective four-role review**

Pass `launchDefaults` to `ReviewStep`. Render `Settings defaults` or `Overrides` and
four `provider/model` values derived through `effectiveRegenerationModels`; do not
reimplement resolution inside JSX.

- [ ] **Step 5: Run focused verification and commit**

Run:

```bash
npm test
npm run lint
npm run build
git add web/src/routes/regeneration.tsx web/src/components/regeneration/review-step.tsx web/src/lib/regeneration-api.test.ts web/src/lib/regeneration-state.test.ts docs/superpowers/specs/2026-08-22-regeneration-model-selection.md docs/superpowers/plans/2026-08-22-regeneration-model-selection.md
git commit -m "feat(regeneration): freeze selected role models in campaigns"
```

Expected: tests, lint, and build PASS.

### Task 4: Branch verification and pull request

**Files:**
- Review: all files changed from `origin/Nggaev-v2`.

**Interfaces:**
- Consumes: the complete feature branch.
- Produces: a pushed branch and owned pull request targeting `Nggaev-v2`.

- [ ] **Step 1: Re-run the collision/base-movement gate**

Run `git fetch --all --prune`, inspect open PR files and verify whether
`origin/Nggaev-v2` moved from `260f15e0ed0e40e191963b453c8449ecd67e90fe`. Rebase only
the owned feature branch if required; never modify a PR owned by `s1gmamale1`.

- [ ] **Step 2: Run final verification**

Run:

```bash
npm test
npm run lint
npm run build
uv run pytest tests/models/test_regeneration_models.py tests/services/test_regeneration_campaign.py tests/schemas/test_regeneration_schemas.py -q
git diff --check origin/Nggaev-v2...HEAD
```

Expected: all frontend checks pass; Python focused suite has no regression; diff check is clean.

- [ ] **Step 3: Review the branch diff**

Inspect `git diff --stat`, every changed path, and `git status --short`. Confirm no env,
database, generated bundle, dependency lock, or unrelated file entered the branch.

- [ ] **Step 4: Push and open the PR**

Push `feat/regeneration-model-selection-defaults` and open an owned PR against
`Nggaev-v2`. The PR body must include the two modes, API-only boundary, persistence,
test evidence, no-migration statement, and collision-gate result. Do not merge it.

