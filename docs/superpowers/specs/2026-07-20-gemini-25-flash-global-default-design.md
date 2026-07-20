# Gemini 2.5 Flash Global Default Design

**Date:** 2026-07-20

## Goal

Restore the built-in Vertex location for `gemini-2.5-flash` from
`us-central1` to `global`.

Production usage data showed recurring `us-central1` congestion worsening
from roughly 6% fleet-wide on 2026-07-17 to roughly 30% during the
2026-07-20 10:00–11:00 EDT incident. The two most affected projects were
effectively unavailable: `doombikesshop@gmail.com` had 81 of 83 calls
rejected and `fandcproperties1@gmail.com` had 10 of 11 rejected. Identical
minimal probes later served in both locations, confirming that a single
probe is only a point-in-time capacity signal.

The inverse incident that motivated the regional default has cleared:
`global` returned `429 RESOURCE_EXHAUSTED` across every pool project on
2026-07-16, but live 2026-07-20 probes confirmed that it serves the pool
again. The global endpoint avoids pinning all pay-as-you-go traffic to one
regional Dynamic Shared Quota pool.

## Scope

- Change the built-in `gemini-2.5-flash` location to `global`.
- Preserve `GEMINI_MODEL_LOCATIONS` as the highest-precedence per-model
  operator override.
- Leave routing for every other model unchanged.
- Update focused routing/client-construction tests and the stale explanatory
  comment.

## Non-goals

- No automatic cross-location failover.
- No retry-policy or concurrency-limiter changes.
- No frontend, database, migration, or job-restamping changes.

## Verification

Use test-driven development:

1. Change focused tests to require the global default and confirm they fail
   against the current `us-central1` implementation.
2. Make the minimal production change.
3. Run the focused API transport tests.
4. Run the relevant backend test suite.
5. Run one cheap real `gemini-2.5-flash` call through the production
   `api_transport` path with no location override and confirm that the new
   built-in default serves successfully, as required by `CLAUDE.md` for a
   generation-path change.

## Deployment

The model location is resolved inside each worker process. Fleet workers must
pull the resulting commit and restart before new calls use the global default.
Already-stamped jobs require no restamping because jobs store the model, not
the Vertex location.

If `global` is exhausted again, set the following worker environment override
and restart the affected workers; no code deployment or job restamping is
required:

```env
GEMINI_MODEL_LOCATIONS={"gemini-2.5-flash":"us-central1"}
```
