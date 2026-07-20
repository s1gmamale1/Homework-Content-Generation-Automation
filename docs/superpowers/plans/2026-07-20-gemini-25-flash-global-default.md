# Gemini 2.5 Flash Global Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore `global` as the built-in Vertex location for `gemini-2.5-flash` while preserving the operator override.

**Architecture:** Keep the existing `_location_for(model)` precedence and client construction unchanged. Change only the built-in map value, update tests to pin the new default, and replace stale operational history in the code and root documentation.

**Tech Stack:** Python 3.14, pytest, google-genai Vertex SDK.

## Global Constraints

- `GEMINI_MODEL_LOCATIONS` remains the highest-precedence per-model override.
- Routing for models other than `gemini-2.5-flash` remains unchanged.
- Do not add automatic location failover or change retry/concurrency behavior.
- No frontend, database, migration, or job-restamping changes.
- Run a real in-process `api_transport` Gemini call after automated verification.

---

### Task 1: Restore the Global Default

**Files:**
- Modify: `tests/services/test_api_transport.py:120-175`
- Modify: `tests/services/test_api_transport.py:513-537`
- Modify: `app/services/api_transport.py:64-70`
- Modify: `CLAUDE.md:76`

**Interfaces:**
- Consumes: `_location_for(model: str) -> str` and `_gemini_client(model: str)` from `app.services.api_transport`.
- Produces: `gemini-2.5-flash -> global` by default; valid `GEMINI_MODEL_LOCATIONS` entries still override it.

- [ ] **Step 1: Change focused tests to require the global default**

Replace the stale historical comment and every focused `gemini-2.5-flash`
default assertion:

```python
# ---- Vertex per-model location router (Task 1) ----
# The 2026-07-16 global DSQ incident cleared by 2026-07-20, while production
# data showed recurring us-central1 congestion. Gemini 2.5 Flash therefore
# defaults to global again. GEMINI_MODEL_LOCATIONS remains the per-model
# emergency override.

assert api_transport._location_for("gemini-2.5-flash") == "global"
assert result == "global"
assert seen["location"] == "global"
assert _location_for("gemini-2.5-flash") == "global"
```

Keep the existing explicit override assertion unchanged:

```python
monkeypatch.setenv(
    "GEMINI_MODEL_LOCATIONS",
    '{"gemini-2.5-flash":"europe-west4"}',
)
assert api_transport._location_for("gemini-2.5-flash") == "europe-west4"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
uv run pytest tests/services/test_api_transport.py -q
```

Expected: failures showing actual `us-central1` where the updated assertions
require `global`; explicit override and unrelated-model assertions remain
green.

- [ ] **Step 3: Make the minimal production and documentation change**

In `app/services/api_transport.py`, replace the incident-specific comment and
map:

```python
# 2026-07-16: the Vertex global DSQ pool returned 429 across every pool
# project, so PR #97 temporarily routed gemini-2.5-flash to us-central1.
# 2026-07-20: global had recovered while production us-central1 congestion
# reached ~30% fleet-wide and effectively hard-downed two projects. Default
# back to global to avoid pinning PayGo traffic to one regional DSQ pool.
# GEMINI_MODEL_LOCATIONS remains the no-deploy per-model rollback lever.
_DEFAULT_MODEL_LOCATIONS = {"gemini-2.5-flash": "global"}
```

In `CLAUDE.md`, replace the stale `gemini-2.5-flash→us-central1` statement with:

```markdown
the Vertex **location is routed per model** (`api_transport._location_for`: built-in map `gemini-2.5-flash→global` after the 2026-07-16 global DSQ incident cleared and `us-central1` congestion recurred on 2026-07-20; overridable via env `GEMINI_MODEL_LOCATIONS` JSON, merge semantics and the rollback lever `{"gemini-2.5-flash":"us-central1"}`; unmapped models use `GOOGLE_CLOUD_LOCATION` or `global`)
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/services/test_api_transport.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run broader backend verification**

Run:

```bash
uv run pytest -q
```

Expected: zero failures.

- [ ] **Step 6: Run the real production-path smoke**

Using one uploaded service-account key without printing or reading its
contents, temporarily set only the subprocess environment:

```python
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(storage.sa_key_path(key_id))
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ.pop("GEMINI_MODEL_LOCATIONS", None)
rc, body, usage, stderr = await api_transport.generate(
    provider="gemini",
    model="gemini-2.5-flash",
    prompt="Return exactly: OK",
    attachments=[],
)
assert api_transport._location_for("gemini-2.5-flash") == "global"
assert rc == 0 and body.strip() == "OK", stderr
```

Expected: the actual `api_transport.generate` path reports default location
`global` and returns `OK`. This is one cheap model call.

- [ ] **Step 7: Commit implementation**

```bash
git add app/services/api_transport.py tests/services/test_api_transport.py CLAUDE.md
git commit -m "fix(gemini): restore 2.5 Flash global routing"
```
