# Regeneration Model Selection Design

**Status:** Approved on 2026-08-22

## Goal

Let an operator create a regeneration campaign with either the four provider/model
defaults configured on `/settings` or an explicit provider/model override for each
of Content, Judge, Solver, and Extract.

## Operator behavior

- The model section opens in **Use Settings defaults** mode.
- Defaults mode shows the live Content, Judge, Solver, and Extract provider/model
  pairs returned by `GET /api/v1/settings/launch-defaults` and links to `/settings`.
- **Override models** reveals provider/model controls for all four roles. The controls
  are initially seeded from the current Settings defaults and remain editable and
  browser-persisted.
- Regeneration remains API-only. Neither mode inherits CLI transports from Settings;
  every role remains pinned to `transport="api"` in the campaign contract.
- The review screen names the selection source and displays all four effective pairs.
- Campaign creation is disabled until all four effective pairs are present, currently
  offered by the model manifest, API-supported, and valid for their role. Extract may
  not use a provider marked API-only because the extraction pipeline requires its
  CLI-capable fallback surface.
- The campaign request carries four explicit provider/model pairs. This freezes one
  snapshot of the approved choices; later Settings changes cannot change the campaign.

## Persistence and compatibility

The existing local-storage draft stays backward compatible. Existing saved drafts
without a model-selection mode retain their content choice as an override when one is
present; otherwise they open in Settings-defaults mode. New override role choices are
decoded defensively and pruned when a provider/model disappears from the manifest.

## Architecture boundary

This is a frontend extension of an already-supported backend contract:
`LaunchContract` already accepts explicit Extract, Judge, and Solver provider/model
pairs and freezes its resolved value into `regeneration_campaigns.launch_contract`.
No API schema, database migration, worker protocol, or `/settings` mutation is needed.

## Collision-gate record

- Base: `origin/Nggaev-v2@260f15e0ed0e40e191963b453c8449ecd67e90fe`.
- Open PRs inspected: #138, #136, #131, #128, #118, #117, #108.
- Active worktrees and all local/remote branches were checked for changes to the
  regeneration draft, route, components, model manifest, settings API, and launch
  contract paths.
- Conclusion: no equivalent or partially overlapping implementation exists. Open PRs
  do not touch the affected regeneration frontend paths.

