# Plan — Pre-mass-gen derisk (observability fixes)

**Goal:** before the Gemini-API mass-gen campaign starts, make a big run **debuggable from the DB and the log alone** — without rebuilding anything. Two small, low-risk code fixes. The throughput fix (`concurrency-knob-1` token-bucket) is explicitly OUT of scope here (separate gated plan).

## Approach & key decisions

- **Two independent small fixes**, each its own task + commit, TDD-first. No schema changes, no API changes.
- **`log-hygiene-1`**: the loguru format (`app/log.py:14`) emits `{time:HH:mm:ss.SSS}` — no date — and the file sink rotates by size (`20 MB`). Across a midnight boundary, one file silently mixes two days/runs (this is why the last audit needed model-discrimination + DB cross-checks). Fix = add the **date** to the shared `_FMT` time token, and switch the file sink to **daily rotation** (`rotation="00:00"`). Console gets the date too (harmless). Load-bearing change is the date in the line; daily rotation is the bonus that splits files per day.
- **`api-error-capture-1`**: on an api (SDK) failure, `api_transport.generate` returns the real error string as the 4th tuple element (`stderr`), but `run_phase` (`agent.py:721` `err = f"{provider} CLI exited rc={rc}"`) and `summarize_lesson` write only that **generic, CLI-worded** string into `agent_usages.error_message`, and `raw_envelope` carries nothing (api `usage["raw"]` is `{}`). So the DB can't tell 429 vs DNS vs auth — only the raw log can. Fix = (a) a small pure helper that builds a **transport-aware** message **including the real error preview**, used at both failure sites; (b) thread the real error string into `raw_envelope` via `extra_envelope`. Decision: introduce `_spawn_failure_message(...)` as a pure function so it's unit-testable red-first (avoids driving the whole `run_phase` retry loop in a test).
- **Verified load-bearing facts** (read against tip before writing this):
  - `app/log.py:14-19` `_FMT` (shared by both sinks); file sink `rotation="20 MB"` `retention="7 days"` (`:42-43`).
  - `app/services/agent.py:721` generic `err`; `_record_usage` (`:479`) builds `raw = dict(usage.get("raw") or {})` then `raw.update(extra_envelope)` → so adding `"error"` to `extra_envelope` lands it in `raw_envelope`.
  - `summarize_lesson` failure path writes `error_message=... f"{provider} CLI exited rc={rc}"` and raises `RuntimeError("lesson.extract: ... CLI exited rc=...")`.
  - `_failure_preview(stderr, text)` already exists (used at `agent.py:741`) — reuse it.
  - api failure stderr source: `api_transport._gemini`/`_claude` `except` → `return 1, "", dict(_EMPTY_USAGE), str(exc)`.

---

## Task 1 — `log-hygiene-1`: date in log + daily rotation

**TDD:**
1. **RED** — add `tests/test_log_format.py`:
   - Build a fresh loguru handler with `app.log._FMT` into a capture sink; emit one `logger.info("x")`; assert the captured line matches `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} ` (date present). Run it; it must FAIL on the current `_FMT`.
2. **GREEN** — in `app/log.py`:
   - Change the time token in `_FMT` from `{time:HH:mm:ss.SSS}` → `{time:YYYY-MM-DD HH:mm:ss.SSS}`.
   - In the **file** `logger.add("var/server.log", ...)`: change `rotation="20 MB"` → `rotation="00:00"` (daily at midnight); keep `retention="7 days"`. Leave the stderr sink otherwise unchanged.
3. Re-run the new test → GREEN. Then full suite must stay green.

**Exact commands:**
```
uv run python -m pytest tests/test_log_format.py -q
uv run python -m pytest tests/ -q
```
**Commit:** `fix(log): add date to log format + daily rotation (log-hygiene-1)`

**Acceptance:** start the server briefly (or import-and-configure in a REPL), emit a line, confirm it begins with `YYYY-MM-DD HH:MM:SS.mmm`.

---

## Task 2 — `api-error-capture-1`: capture the real api error into the DB

**TDD:**
1. **RED** — add to `tests/services/test_agent.py`:
   - `test_spawn_failure_message_includes_real_error`: call `agent._spawn_failure_message(provider="gemini", transport="api", rc=1, stderr="429 RESOURCE_EXHAUSTED: quota", text="")` and assert the result contains `"429 RESOURCE_EXHAUSTED"` AND the word `"api"` (NOT `"CLI"`). Also assert `transport="cli"` yields `"CLI"`. Run → FAILS (helper doesn't exist).
2. **GREEN** — in `app/services/agent.py`:
   - Add the pure helper near `_failure_preview`:
     ```python
     def _spawn_failure_message(provider: str, transport: str, rc: int, stderr: str, text: str) -> str:
         """Transport-aware failure string that INCLUDES the real error preview
         (api stderr carries the 429/DNS/auth cause; the old 'CLI exited rc=N'
         wording dropped it). Used at every rc!=0 record-usage + raise site."""
         word = "api" if transport == "api" else "CLI"
         return f"{provider} {word} call failed rc={rc}: {_failure_preview(stderr, text)}"
     ```
   - In `run_phase` rc!=0 branch (`agent.py:721`): replace `err = f"{provider} CLI exited rc={rc}"` with `err = _spawn_failure_message(provider, transport, rc, stderr, text)`. Add the real error to the usage row by extending the `extra_envelope` on that `_record_usage` call: `extra_envelope={"phase_name": phase_name, "attempt": attempt, "error": (stderr or "")[:2000]}`.
   - In `summarize_lesson`'s failure path: build the same `err = _spawn_failure_message(provider, transport, rc, stderr, text)`, pass it as `error_message=err` to `_record_usage`, add `"error": (stderr or "")[:2000]` to its `extra_envelope`, and use `err` in the raised `RuntimeError`.
   - Do NOT touch the `spawn_failed is not None` branch (it already records `str(spawn_failed)` — the real exception).
3. **RED #2 (DB-level, optional but recommended)** — `test_run_phase_api_failure_records_real_error`: monkeypatch `agent._spawn` to return `(1, "", dict(agent._EMPTY_USAGE) if hasattr(agent,'_EMPTY_USAGE') else {"raw":{}}, "429 RESOURCE_EXHAUSTED: quota")`, monkeypatch `agent._record_usage` to capture kwargs into a list, call `run_phase(..., transport="api")` inside `pytest.raises(RuntimeError)`, assert the captured `error_message` contains `"429 RESOURCE_EXHAUSTED"` and `extra_envelope["error"]` is set. (If wiring the full `run_phase` args is heavy, the Task-2.1 unit test on the helper + a manual check is acceptable — note it in the commit.)
4. Full suite green.

**Exact commands:**
```
uv run python -m pytest tests/services/test_agent.py -q
uv run python -m pytest tests/ -q
```
**Commit:** `fix(agent): capture real api error into error_message + raw_envelope (api-error-capture-1)`

**Acceptance (real, since it affects observability):** a real failing api smoke — force a bad model id or an over-quota call via `api_transport`, run one `summarize_lesson`/`run_phase`, then `SELECT error_message, raw_envelope FROM agent_usages WHERE success=false ORDER BY created_at DESC LIMIT 1;` and confirm the **real** error text (not `"... CLI exited rc=1"`) is present.

---

## Finish (controller, after both tasks)
- Full suite green: `uv run python -m pytest tests/ -q`.
- Update `docs/memory/WISHLIST.md`: mark `log-hygiene-1` and `api-error-capture-1` ✅ SHIPPED with the commit refs.
- Worklog entry in `docs/memory/MASTER_MEMORY.md` + INDEX row.
- No reference-doc de-stale needed (no behavior/API/schema change beyond log format).

---

## Operator checklist (NOT implementer tasks — the user does these; ~20 min)
These are config, not code. Do them before launching the campaign:
1. **Arm cost caps** (C4 is already built; just set the env `$` ceilings — `0.0` = disabled today):
   - `COST_CAP_BATCH_USD=<e.g. 50>` (per-batch auto-pause)
   - `COST_CAP_FLEET_DAILY_USD=<e.g. 200>` (fleet/day auto-pause)
   - (config fields: `settings.cost_cap_batch_usd` / `cost_cap_fleet_daily_usd`, `config.py:208/210`.)
2. **Lower concurrency** as the interim 429 mitigation until the token-bucket lands: `WORKER_CONCURRENCY=2` (or 3) on **both** PCs (`settings.worker_concurrency`, `config.py:44`).
3. **Oliver reliability** (`fleet-net-1` + `pg-hba-ipv6-1`): put Oliver on **wired Ethernet**, ensure a stable DNS resolver, and set its `DATABASE_URL` host to the **literal IPv4** `192.168.1.15` (avoid hostname→IPv6).
4. Restart the server so all of the above (and the new log format / capabilities) take effect.

## Out of scope (separate, larger gated plans — do after the campaign is rolling)
`concurrency-knob-1` (token-bucket + Retry-After backoff — the real throughput fix), `launcher-capability-gate-1`, `fleet-api-1` (batch transport), `notion-archive-1` (R15), FE dashboards.
