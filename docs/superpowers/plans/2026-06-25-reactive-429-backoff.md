# Plan — Reactive 429 backoff in the spawn path (concurrency-knob-1, Phase 1)

**Goal:** stop Vertex `429 RESOURCE_EXHAUSTED` from turning into failures/ungraded phases. On a rate-limit, **retry the same call with backoff** instead of returning failure. No DB, no quota number, no schema change. This is Phase 1 of `concurrency-knob-1`; the proactive Postgres token-bucket (Phase 2) is a SEPARATE later plan (needs the Vertex quota numbers).

## Approach & key decisions

- **Where:** wrap the single LLM call in `app/services/agent.py` `_spawn` with a bounded retry-on-rate-limit loop. `_spawn` is the ONE choke point every call goes through (content via `_run_with_failover`, the judge, extract, cli AND api), so fixing it here benefits all callers — **including the judge** (its `_spawn` 429s now retry internally → far fewer `judge_status="unavailable"`), with no `phase_judge` change.
- **Refactor, don't rewrite:** rename the current `_spawn` body to `_spawn_once` (unchanged behavior — both the api early-return branch and the cli subprocess branch), and make the new `_spawn` a thin loop that calls `_spawn_once`, inspects the result, and retries on a rate-limit. All callers keep calling `_spawn` unchanged.
- **Detect precisely:** retry ONLY on a rate-limit. A new `_is_rate_limited(text)` must match the real shapes — Vertex `"429"` / `"RESOURCE_EXHAUSTED"` / `"Resource exhausted"`, anthropic `"rate_limit"` / `"overloaded_error"` / `"429"` / `"too many requests"` — and must **NOT** match auth (`401`/`403`/`PERMISSION_DENIED`/`UNAUTHENTICATED`) or truncation (`MAX_TOKENS`), which never self-heal. Verify against the captured live string: `429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'Resource exhausted. Please try again later.', 'status': 'RESOURCE_EXHAUSTED'}}`. (`failure_classifier._WALL` has `"quota"`/`"rate limit reached"` but may MISS `RESOURCE_EXHAUSTED` — add the explicit terms; reuse/extend `failure_classifier` or add a dedicated helper, your call, but it must catch the live string above.)
- **Backoff:** exponential with jitter — `delay = min(base * 2**attempt, cap) + random.uniform(0, base)`. Defaults `base=2.0s`, `cap=30s`, `max_retries=4` → worst-case total ≈ 30–56s across retries, comfortably under `per_attempt_timeout_seconds=600`. (Retry-After header parsing is unreliable through the stringified SDK error — exponential is the core; surfacing a real Retry-After from `api_transport` is a noted Phase-2 nicety, NOT in scope here.)
- **Slot behavior (deliberate):** `_spawn_once` acquires `_semaphore()` internally, so the `asyncio.sleep(delay)` BETWEEN attempts (in `_spawn`) holds **no** slot — backing off frees the concurrency slot for other work. Correct for the reactive model.
- **Layering:** `_spawn` now absorbs *transient* 429s inline; only a persistent rate-limit (after `max_retries`) bubbles up to `_run_with_failover` / the judge's soft-fail exactly as today. No behavior change on the exhausted path.
- **Verified facts (read against tip):** `_spawn` at `app/services/agent.py:333`; api branch `:353-360` returns `api_transport.generate(...)`; cli branch builds argv + subprocess and returns `(rc, text, usage, stderr)` at `:443`; `_semaphore()` wraps each branch. `failure_classifier.classify` + `_WALL` at `app/services/failure_classifier.py:33,51`.

---

## Task 1 — rate-limit detector + backoff helpers (pure, TDD)

**RED** — add to `tests/services/test_agent.py`:
- `test_is_rate_limited`: assert True for the live Vertex string above, `"429 RESOURCE_EXHAUSTED"`, `"Resource exhausted"`, anthropic `"rate_limit_error"`, `"overloaded_error"`; assert **False** for `""`, `"401 UNAUTHENTICATED"`, `"PERMISSION_DENIED"`, `"output truncated: finish_reason=MAX_TOKENS"`, `"ModelNotFoundError"`.
- `test_rate_limit_delay_schedule`: `_rate_limit_delay(attempt)` for attempt 0..4 is positive, non-decreasing in expectation, and never exceeds `cap + base` (monkeypatch `random.uniform` → return its 2nd arg for determinism).

**GREEN** — in `app/services/agent.py`:
- `def _is_rate_limited(text: str) -> bool:` (precise term match per Approach; lower-cased; excludes auth/truncation).
- `def _rate_limit_delay(attempt: int, *, base: float | None = None, cap: float | None = None) -> float:` using `settings.rate_limit_base_delay_seconds` / `rate_limit_max_delay_seconds` defaults; exponential + `random.uniform(0, base)` jitter.
- In `app/config.py` add: `rate_limit_max_retries: int = 4`, `rate_limit_base_delay_seconds: float = 2.0`, `rate_limit_max_delay_seconds: float = 30.0` (env `RATE_LIMIT_MAX_RETRIES` etc.), with a short comment.

**Commands:** `uv run python -m pytest tests/services/test_agent.py -q`
**Commit:** `feat(agent): rate-limit detector + backoff helpers (concurrency-knob-1 ph1)`

---

## Task 2 — wire the retry loop into `_spawn` (TDD, mocked sleep)

**RED** — add to `tests/services/test_agent.py`:
- `test_spawn_retries_on_rate_limit_then_succeeds`: monkeypatch `agent._spawn_once` to return `(1,"",{"raw":{}},"429 RESOURCE_EXHAUSTED")` twice then `(0,"ok",{"raw":{}},"")`; monkeypatch `asyncio.sleep` (async) to append the delay to a list (no real sleep); call `await agent._spawn(...)`; assert it returns `(0,"ok",...)`, `_spawn_once` called 3×, and 2 delays recorded.
- `test_spawn_gives_up_after_max_retries`: `_spawn_once` always returns the 429 tuple; assert `_spawn` returns the failure tuple after exactly `rate_limit_max_retries+1` calls (no infinite loop), recorded delays == `max_retries`.
- `test_spawn_does_not_retry_non_rate_limit`: `_spawn_once` returns `(1,"","","401 UNAUTHENTICATED")`; assert `_spawn` returns immediately, `_spawn_once` called once, 0 sleeps.

**GREEN** — in `app/services/agent.py`:
- Rename the current `_spawn` body verbatim to `async def _spawn_once(...)` (same signature/return).
- New `async def _spawn(...)`:
  ```python
  for attempt in range(settings.rate_limit_max_retries + 1):
      rc, text, usage, stderr = await _spawn_once(provider=provider, model=model,
          prompt=prompt, attachments=attachments, transport=transport)
      if rc == 0 or not _is_rate_limited(stderr or text):
          return rc, text, usage, stderr
      if attempt >= settings.rate_limit_max_retries:
          logger.warning(f"agent.spawn rate-limited, retries exhausted | provider={provider.name}")
          return rc, text, usage, stderr
      delay = _rate_limit_delay(attempt)
      logger.warning(f"agent.spawn rate-limited (429) | provider={provider.name} "
                     f"attempt={attempt+1}/{settings.rate_limit_max_retries} backoff={delay:.1f}s")
      await asyncio.sleep(delay)
  ```
- Keep the docstring note that `_spawn` now retries transient rate-limits; `_spawn_once` is the single attempt.

**Commands:** `uv run python -m pytest tests/services/test_agent.py -q` then full suite `uv run python -m pytest tests/ -q`
**Commit:** `feat(agent): retry transient 429s with backoff in _spawn (concurrency-knob-1 ph1)`

---

## Task 3 — acceptance (real, since it changes generation behavior)
- **Happy path unbroken:** run one real api smoke (`scripts/` or an in-process `summarize_lesson`/`run_phase` with `transport="api"`, gemini) → confirm it still returns content (the retry wrapper doesn't break success). Fact over theory.
- **Retry path:** the mocked behavior tests (Task 2) prove the loop. The *live* 429-backoff will show in the next mass-gen run's log as `agent.spawn rate-limited (429) ... backoff=Ns` lines followed by success, and a drop in `judge_status="unavailable"` vs the 2026-06-24 run (27). Note this as the live-verification to watch on the next run.

## Finish (controller)
- Full suite green: `uv run python -m pytest tests/ -q`.
- WISHLIST: update `concurrency-knob-1` → "Phase 1 (reactive backoff) SHIPPED <commits>; Phase 2 (proactive token-bucket) still open, needs Vertex quota numbers."
- Worklog in `MASTER_MEMORY.md` + INDEX row. De-stale `docs/DEPLOY.md` if it documents retry/concurrency knobs (add the new `RATE_LIMIT_*` env vars).

## Out of scope (Phase 2, separate plan)
The proactive Postgres token-bucket per `(provider, model)` + true fleet-wide cap + (optional) real Retry-After surfacing — author once the Vertex per-model RPM/TPM quota is confirmed.
