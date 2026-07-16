# Per-model Vertex location router (gemini api transport)

**Trigger (live-verified 2026-07-16, 12 read-only probes):** `gemini-2.5-flash` now 429s
(`RESOURCE_EXHAUSTED`, instant) on the **global** Vertex endpoint across ALL five pool SA
projects, while serving fine on regional endpoints (`us-central1`, `europe-west4`); the preview
models (`gemini-3-flash-preview`, `gemini-3.1-pro-preview`) are the inverse — **global-only**
(404 regionally). One `GOOGLE_CLOUD_LOCATION` for all calls can no longer serve the production
mix (extract + judge defaults = 2.5-flash; content = 3-flash; solver = 3.1-pro). 19 pending jobs
carry frozen 2.5-flash stamps.

## Approach & key decisions (design locked with user in-session 2026-07-16; option "router only" chosen over a launch-defaults model flip)

- **Route location per model at client construction.** `api_transport._gemini_client()` gains a
  `model` param; the Vertex branch resolves `location = _location_for(model)`. The
  `GEMINI_API_KEY` branch is untouched (plain API keys take no location). The retired cli path
  (`agent.py:331` per-spawn env) is untouched.
- **Map: env-overridable, built-in default.** `GEMINI_MODEL_LOCATIONS` env var = JSON object
  `{model: location}`; built-in default `{"gemini-2.5-flash": "us-central1"}`. Unmatched models →
  existing behavior (`GOOGLE_CLOUD_LOCATION` or `"global"`). Malformed JSON → `logger.error`
  once + built-in default (routing config must not kill generation; unlike credentials, which
  raise loudly). Read from `os.environ` per call, matching the transport's existing style
  (deliberately NOT pydantic settings — same rationale as the api credentials, CLAUDE.md).
- **Why not flip launch_defaults to 3-flash/2.5-pro:** judge=3-flash would equal the content
  model → `model_tiers.resolve_judge` self-grade guard hard-swaps to claude-opus-4-7 (anthropic-
  keyed workers only, Opus rates); judge=2.5-pro costs ~4× (≈ +$200-400 for October). Router
  keeps 2.5-flash economics, needs NO restamp of the 19 pending jobs (stamps pin model, location
  is transport-level), no settings churn.
- Verified anchors: `_gemini_client` `api_transport.py:60-73` (fresh client per call, no cache);
  sole caller `_gemini(model, …)` `:99` has the model in scope; existing tests
  `tests/services/test_api_transport.py`. No migration. Worklog **0143** (0142 = BE-16 plan lane;
  re-verify INDEX tail at finish). Branch `feat/gemini-location-router`, worktree
  `../HCGA-loc-router`. Suite baseline: re-baseline in worktree (main is at 1572-era + merged
  lanes; expect current origin state).

## Tasks

### Task 1 — router + tests (RED → GREEN)

**Tests first** (`tests/services/test_api_transport.py`, follow its conventions — the gemini
client is presumably mocked; add pure tests for `_location_for` + construction-arg assertions):
- default map: `_location_for("gemini-2.5-flash") == "us-central1"` (RED: function missing);
  `_location_for("gemini-3-flash-preview") == "global"`;
- `GOOGLE_CLOUD_LOCATION=europe-west4` env → unmatched models get `europe-west4`, mapped model
  still `us-central1`;
- `GEMINI_MODEL_LOCATIONS='{"gemini-2.5-flash":"europe-west4"}'` overrides the default map;
- malformed `GEMINI_MODEL_LOCATIONS` → built-in default + an error log (caplog);
- `_gemini_client("gemini-2.5-flash")` under fake Vertex env constructs
  `genai.Client(vertexai=True, …, location="us-central1")` (assert via mock), and the
  `GEMINI_API_KEY` branch ignores the map entirely.

**Code** (`app/services/api_transport.py`): `_DEFAULT_MODEL_LOCATIONS = {"gemini-2.5-flash":
"us-central1"}`; `_location_for(model: str) -> str` (env JSON override → default map → env
default → "global"); `_gemini_client(model: str)`; update the `_gemini` call site. Docstring the
why (global-tier quota zeroed for 2.5-flash, previews global-only — 2026-07-16 probes).

Run: `uv run python -m pytest tests/services/test_api_transport.py -q` then full suite.
Commit: `feat(api): per-model Vertex location router — 2.5-flash regional, previews global (worklog 0143)`
Stage: `app/services/api_transport.py tests/services/test_api_transport.py`

### Task 2 — acceptance + docs + finish (controller)

- **Acceptance (real api calls through the PRODUCTION transport path, ~$0.002):** in-process
  `api_transport._gemini("gemini-2.5-flash", "Reply with exactly: OK")` under a pool SA key →
  expect OK (was 429 via global), and `_gemini("gemini-3-flash-preview", …)` → still OK
  (global). Paste outputs in the PR.
- Docs: CLAUDE.md Transport-toggle env-plumbing bullet gains one line (`GEMINI_MODEL_LOCATIONS`
  + the 2.5-flash/global situation); `docs/HOW_IT_WORKS.md` transport section if it names the
  location. Worklog 0143 + INDEX row (re-verify number). Wishlist: none (this CLOSES the need
  filed as "gemini-25flash-global-quota-1" — file nothing, note in worklog).
- Full suite; rebase check; push branch; PR → gate. **Deploy note for the PR: fleet pull+restart
  required** (workers run extract/judge api calls; until they pull, their 2.5-flash calls keep
  429ing — the 19 pending jobs recover only after the fleet restart).

## Flagged for the gate
1. Behavior change is api-transport-only and additive (unmatched models keep exact current
   behavior); the cli path and non-gemini providers untouched.
2. The default map hardcodes a Google-side fact that WILL drift — env override is the escape
   hatch, documented in CLAUDE.md.
