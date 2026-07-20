# Queue-Correctness: Saturation Requeue + Transient Retry Propagation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. TDD per task, commit per task, stage only listed files.

**Goal:** Transient phase failures (attempt timeouts, 429s, net blips) reach the worker's bounded queue retry instead of terminal-failing; fleet credential-slot saturation parks the job with a cooldown instead of burning the 600s attempt budget; timeout errors carry a readable message; cancelled sibling phases never leave `running` rows behind.

**Architecture:** Two new typed signals (`SlotSaturation`, `TransientPhaseError`) plus an existing-pattern clone (`PhaseAttemptTimeout` message wrapper) propagate from `_run_with_failover` / `_execute_one_phase` up through `pipeline.run()` to the worker — exactly the path `SessionLimitPause` already walks. Slot saturation is detected by string marker at the pipeline boundary (same craft as session-limit detection), so nothing threads through `agent.py`'s many broad excepts. Hard failures keep today's behavior (terminal fail, swallowed upward).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, pytest (mock-based; real-DB tests behind `RUN_DB_INTEGRATION=1`).

## Approach & key decisions

- **User-locked (2026-07-20):** (a) slot saturation → **requeue with cooldown** (new `SlotSaturation` signal, SessionLimitPause-shaped: park pending, don't burn an attempt, `scheduled_at = now + 90s`) — fleet saturation is back-pressure, "try later" frees the worker slot at $0; (b) **transient-only queue retry** — `PhaseAttemptTimeout` OR `agent._is_rate_limited(str(exc))` OR `failure_classifier.classify(exc) == "transient"` propagate as `TransientPhaseError` to the worker's existing `_mark_failed → jobs_repo.mark_failed_with_retry` (bounded, `queue_max_attempts=3`, exp backoff); hard failures stay terminal-failed in place.
- **Marker-string detection, not exception threading:** `_spawn_once`'s slot-exhaustion already returns a distinctive `"429 fleet credential slot wait exhausted"` stderr (agent.py:608-613) which survives into the `RuntimeError` text `run_phase` raises. Detecting it at `_run_with_failover` (and the judge/solver catch sites) mirrors how `is_session_limit` works and avoids auditing ~20 broad `except Exception` sites in agent.py.
- **Stop the in-process burn too:** `_spawn`'s retry loop returns immediately on the slot-exhaustion marker (one ≤120s wait per episode, not 5×120s) — the requeue handles the "try later".
- **No worker change for TransientPhaseError:** the worker's existing `except Exception → self._mark_failed` (worker.py:556-560) already performs the bounded requeue once the pipeline stops swallowing. Only `SlotSaturation` gets a new worker branch.
- **Load-bearing facts verified against code (2026-07-20):** `wait_for` wraps the whole `run_fn` (pipeline.py:830); `str(asyncio.TimeoutError()) == ""` → blank `error_message` at pipeline.py:576 (2 such rows in prod DB); head/content/top-level paths all swallow (pipeline.py:333-336, :403-407, :460-471) so `mark_failed_with_retry` (jobs.py:654) is unreachable for phase failures; scheduler peer-cancel (pipeline.py:736-746) leaves `running` phase rows because `CancelledError` is a `BaseException`; `requeue_session_limited` (jobs.py:705) is the park precedent (attempts decrement compensates the claim's increment); `phase_outputs.set_status` guards `done` rows (phase_outputs.py:142-143); classifier `_TRANSIENT` does NOT match bare "429" — that's `agent._is_rate_limited`'s job.
- **Rejected:** excluding slot-wait from the 600s timer by restructuring (invasive, still occupies a worker slot for 5×120s); all-failures queue retry (3× bill on deterministic errors violates the money rule); raising `SlotSaturation` from `_spawn_once` (would need pass-throughs at every broad except between agent and pipeline).
- **Scope guards:** TOC extraction (no job) keeps current behavior — the marker-bearing error just fails the extraction as today; judge/solver get explicit marker pass-throughs so saturation parks the job instead of silently shipping unjudged packets. `agent_usages`-row-on-cancellation (review claim 6) is out of scope — evidence-only claim, no behavioral defect beyond what the above fixes.
- **Cross-plan:** no file overlap with in-flight `2026-07-20-gemini-25-flash-global-default.md` (api_transport.py lane) or PR #108 (web/). No migration — `scheduled_at`/`attempts`/phase columns all exist.

## Global Constraints

- **No migration.** No schema change anywhere in this plan.
- **Money rule:** no mass generation; the only real model call is Task 8's single bounded smoke (report its $).
- Stage ONLY the files each task lists; never `git add -A`.
- Commit trailer on every commit: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Suite must stay green: `uv run python -m pytest tests/ -q` (canonical bar = WITHOUT `RUN_DB_INTEGRATION`).
- All work in the `../HCGA-queue-fix` worktree on branch `feat/queue-transient-retry`.

---

### Task 1: Typed signals in `app/services/errors.py`

**Files:**
- Modify: `app/services/errors.py`
- Test: `tests/services/test_errors_queue.py` (new)

**Interfaces (produced for later tasks):**
- `SLOT_SATURATION_MARKER: str` — the exact stderr marker `"fleet credential slot wait exhausted"`.
- `is_slot_saturation(exc: BaseException | str) -> bool`
- `class SlotSaturation(Exception)` — carries a readable message.
- `class TransientPhaseError(Exception)` — carries `"{phase}: {reason}"`.
- `class PhaseAttemptTimeout(Exception)` — `str()` never blank.

- [ ] **Step 1: Write the failing test** — `tests/services/test_errors_queue.py`:

```python
"""Typed queue-correctness signals (errors.py).

RED-proofs: is_slot_saturation must match the exact _spawn_once marker and
reject near-misses; PhaseAttemptTimeout must never stringify blank (the
asyncio.TimeoutError bug this replaces)."""
from app.services.errors import (
    SLOT_SATURATION_MARKER,
    PhaseAttemptTimeout,
    SlotSaturation,
    TransientPhaseError,
    is_slot_saturation,
)


def test_marker_matches_spawn_once_literal():
    # Must equal the literal in agent._spawn_once's slot-exhaustion return.
    assert SLOT_SATURATION_MARKER == "fleet credential slot wait exhausted"


def test_is_slot_saturation_on_exception_and_string():
    exc = RuntimeError(
        "gemini api call failed rc=1: 429 fleet credential slot wait "
        "exhausted (credential=gemini:project-x, budget=120s)"
    )
    assert is_slot_saturation(exc) is True
    assert is_slot_saturation(str(exc)) is True


def test_is_slot_saturation_rejects_plain_429():
    assert is_slot_saturation(RuntimeError("429 RESOURCE_EXHAUSTED")) is False


def test_phase_attempt_timeout_never_blank():
    exc = PhaseAttemptTimeout("per-attempt timeout after 600s (provider=gemini)")
    assert str(exc)  # non-empty — the whole point
    assert "600" in str(exc)


def test_signal_types_are_distinct():
    # Worker/pipeline dispatch on type — none may inherit from another.
    for a, b in [(SlotSaturation, TransientPhaseError),
                 (SlotSaturation, PhaseAttemptTimeout),
                 (TransientPhaseError, PhaseAttemptTimeout)]:
        assert not issubclass(a, b) and not issubclass(b, a)
```

- [ ] **Step 2: Run to verify it fails** — `cd /Users/macmini5/Documents/HCGA-queue-fix && uv run python -m pytest tests/services/test_errors_queue.py -q` → FAIL (ImportError).
- [ ] **Step 3: Implement** — append to `app/services/errors.py` (keep the module import-free per its docstring):

```python
# ── Queue-correctness signals (queue-correctness-1) ──────────────────────────

# Must match the literal embedded in agent._spawn_once's slot-exhaustion
# return ("429 fleet credential slot wait exhausted (credential=…, budget=…)").
SLOT_SATURATION_MARKER = "fleet credential slot wait exhausted"


def is_slot_saturation(exc: "BaseException | str") -> bool:
    """True when an error/text carries the fleet slot-exhaustion marker."""
    return SLOT_SATURATION_MARKER in str(exc)


class SlotSaturation(Exception):
    """Fleet credential-slot wait exhausted. The worker parks the job
    (status='pending', scheduled_at pushed by a cooldown, attempt refunded) —
    the job must NOT be marked failed and must NOT burn a retry attempt."""


class TransientPhaseError(Exception):
    """A phase failed with a transient-class error (attempt timeout, 429,
    net blip) after in-process retries were exhausted. Propagates to the
    worker so jobs_repo.mark_failed_with_retry applies the bounded queue
    retry. Message shape: '<phase>: <reason>'."""


class PhaseAttemptTimeout(Exception):
    """An attempt exceeded settings.per_attempt_timeout_seconds. Replaces the
    raw asyncio.TimeoutError whose str() is '' (blank error_message bug)."""
```

- [ ] **Step 4: Run to verify pass** — same command → 5 passed.
- [ ] **Step 5: Commit**

```bash
git add app/services/errors.py tests/services/test_errors_queue.py
git commit -m "feat(errors): SlotSaturation + TransientPhaseError + PhaseAttemptTimeout signals

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 2: `agent._spawn` stops burning retries on slot exhaustion

**Files:**
- Modify: `app/services/agent.py:464-484` (the `_spawn` retry loop)
- Test: `tests/services/test_spawn_slot_saturation.py` (new)

**Interfaces:**
- Consumes: `errors.is_slot_saturation` (Task 1).
- Produces: `_spawn` returns the slot-exhaustion failure tuple after exactly ONE `_spawn_once` call (was: up to `rate_limit_max_retries + 1 = 5`).

- [ ] **Step 1: Write the failing test:**

```python
"""_spawn must NOT retry a slot-exhaustion 429 — each retry burns another
120s fleet-slot wait; the pipeline parks the job instead (SlotSaturation).

RED-proof: today _is_rate_limited matches the '429 …' marker text, so the
loop burns all rate_limit_max_retries attempts."""
import asyncio

import pytest

from app.services import agent


SLOT_TUPLE = (
    1, "", {},
    "429 fleet credential slot wait exhausted (credential=gemini:p, budget=120s)",
)


def test_spawn_returns_slot_exhaustion_after_single_attempt(monkeypatch):
    calls = []

    async def fake_spawn_once(**kwargs):
        calls.append(1)
        return SLOT_TUPLE

    async def no_sleep(_):  # pragma: no cover — must not be reached
        raise AssertionError("slot exhaustion must not back off and retry")

    monkeypatch.setattr(agent, "_spawn_once", fake_spawn_once)
    monkeypatch.setattr(agent.asyncio, "sleep", no_sleep)

    rc, text, usage, stderr = asyncio.run(agent._spawn(
        provider=agent._provider("gemini"), model="gemini-2.5-flash",
        prompt="x", attachments=[], transport="api",
    ))
    assert calls == [1]
    assert rc == 1 and "fleet credential slot wait exhausted" in stderr


def test_spawn_still_retries_ordinary_429(monkeypatch):
    """Guard: an ordinary provider 429 keeps the existing backoff loop."""
    calls = []

    async def fake_spawn_once(**kwargs):
        calls.append(1)
        return (1, "", {}, "429 RESOURCE_EXHAUSTED")

    async def instant_sleep(_):
        return None

    monkeypatch.setattr(agent, "_spawn_once", fake_spawn_once)
    monkeypatch.setattr(agent.asyncio, "sleep", instant_sleep)

    asyncio.run(agent._spawn(
        provider=agent._provider("gemini"), model="gemini-2.5-flash",
        prompt="x", attachments=[], transport="api",
    ))
    assert len(calls) == agent.settings.rate_limit_max_retries + 1
```

  Note for the implementer: if `agent._provider("gemini")` is not the real accessor, find the provider-registry lookup used by `run_phase` (grep `def _provider\|PROVIDERS\[` in agent.py) and use that — the test needs any real `Provider` instance.

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/services/test_spawn_slot_saturation.py -q` → first test FAILS (5 calls, or AssertionError from `no_sleep`); second PASSES (guard baseline).
- [ ] **Step 3: Implement** — in `_spawn` (agent.py:469-471), before the retryable check:

```python
        combined = stderr or text
        # Fleet slot exhaustion is deliberately 429-shaped, but retrying it
        # in-process re-burns a full credential_slot_wait_seconds wait per
        # attempt (the 600s-timeout burn, queue-correctness-1). Return it
        # unretried — the pipeline converts it to SlotSaturation and the
        # worker parks the job.
        if errors.is_slot_saturation(combined):
            return rc, text, usage, stderr
        is_retryable = _is_rate_limited(combined) or _is_transient_net(combined)
```

  Add `from app.services import errors` to agent.py's imports (top of file, with the other app-level imports).
- [ ] **Step 4: Run** — both tests pass; then `uv run python -m pytest tests/services/test_agent.py -q` (no regression in the module's suite).
- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_spawn_slot_saturation.py
git commit -m "fix(agent): slot-exhaustion 429 returns after one attempt, never re-burns the slot wait

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 3: `_run_with_failover` — typed timeout + SlotSaturation raise

**Files:**
- Modify: `app/services/pipeline.py:835-841` (timeout branch) and `:842-847` (generic branch head)
- Test: `tests/services/test_failover_signals.py` (new)

**Interfaces:**
- Consumes: `errors.PhaseAttemptTimeout`, `errors.SlotSaturation`, `errors.is_slot_saturation` (Task 1).
- Produces: `_run_with_failover` raises `SlotSaturation` on marker errors (immediately, no same-provider retries) and raises `PhaseAttemptTimeout` (never a blank `asyncio.TimeoutError`) when attempts time out and the chain exhausts.

- [ ] **Step 1: Write the failing test:**

```python
"""_run_with_failover emits typed signals (queue-correctness-1).

RED-proofs: today a timing-out run_fn raises bare asyncio.TimeoutError with
str()=='' (blank error_message bug), and a slot-exhaustion RuntimeError is
classified like any wall/hard error instead of raising SlotSaturation."""
import asyncio

import pytest

from app.services.errors import PhaseAttemptTimeout, SlotSaturation
from app.services.pipeline import _run_with_failover


def test_persistent_timeout_raises_typed_nonblank_error(monkeypatch):
    from app.services import pipeline

    monkeypatch.setattr(pipeline.settings, "per_attempt_timeout_seconds", 0.02)

    async def hung_run_fn(provider, model):
        await asyncio.sleep(5)

    with pytest.raises(PhaseAttemptTimeout) as ei:
        asyncio.run(_run_with_failover(
            requested_provider="gemini", model="gemini-2.5-flash",
            run_fn=hung_run_fn, transport="api",
        ))
    assert str(ei.value)                       # never blank
    assert "timeout" in str(ei.value).lower()
    assert "gemini" in str(ei.value)


def test_slot_saturation_raises_immediately_no_retry():
    calls = []

    async def saturated_run_fn(provider, model):
        calls.append(provider)
        raise RuntimeError(
            "gemini api call failed rc=1: 429 fleet credential slot wait "
            "exhausted (credential=gemini:p, budget=120s)"
        )

    with pytest.raises(SlotSaturation):
        asyncio.run(_run_with_failover(
            requested_provider="gemini", model="gemini-2.5-flash",
            run_fn=saturated_run_fn, transport="api",
        ))
    assert calls == ["gemini"]     # exactly one attempt — park, don't grind
```

- [ ] **Step 2: Run to verify fail** — `uv run python -m pytest tests/services/test_failover_signals.py -q` → both FAIL (bare `TimeoutError`; retried/classified error).
- [ ] **Step 3: Implement** — in `_run_with_failover`:

  (a) timeout branch (pipeline.py:835-841) — replace `last_exc = exc`:

```python
            except asyncio.TimeoutError:
                # Attempt blew per_attempt_timeout — hung/too-slow provider.
                # Wrap in a typed, NON-BLANK error (str(asyncio.TimeoutError())
                # is '' → the blank "<phase>: " error_message bug). Fail over
                # immediately (no same-provider retry) exactly as before.
                last_exc = PhaseAttemptTimeout(
                    f"per-attempt timeout after "
                    f"{settings.per_attempt_timeout_seconds}s "
                    f"(provider={prov}, transport={transport})"
                )
                break
```

  (b) generic branch — FIRST line inside `except Exception as exc:` (before the session-limit check at :847):

```python
                # Fleet credential-slot exhaustion: park the job (worker
                # requeues with cooldown) — never classify, never retry,
                # never mark failed. Checked BEFORE is_session_limit/classify
                # for the same reason the session-limit check precedes
                # classify: the '429 …' text would otherwise be misrouted.
                if is_slot_saturation(exc):
                    raise SlotSaturation(str(exc))
```

  (c) imports — extend the existing `from app.services.errors import …` line in pipeline.py (grep for `SessionLimitPause` import) to include `PhaseAttemptTimeout, SlotSaturation, TransientPhaseError, is_slot_saturation` (`TransientPhaseError` is used in Task 4).
- [ ] **Step 4: Run** — new tests pass + `uv run python -m pytest tests/services/test_failover_api.py tests/services/test_failover_driver.py tests/services/test_session_limit_strategy_failover.py -q` stays green.
- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py tests/services/test_failover_signals.py
git commit -m "feat(pipeline): typed PhaseAttemptTimeout + SlotSaturation raise in _run_with_failover

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 4: `_execute_one_phase` transient/hard split + full propagation

**Files:**
- Modify: `app/services/pipeline.py` — `_execute_one_phase` except-chain (:565-582), head loop (:331-336), content-path catch (:401-408), scheduler `task.result()` handling (:724-746), top-level `run()` handler (:456-460)
- Test: `tests/services/test_pipeline_transient_propagation.py` (new)

**Interfaces:**
- Consumes: `TransientPhaseError`, `SlotSaturation`, `PhaseAttemptTimeout` (Task 1); `agent._is_rate_limited`; `failure_classifier.classify`.
- Produces: module-level helpers `_phase_error_message(phase_name: str, exc: BaseException) -> str` and `_requeue_worthy(exc: BaseException) -> bool`; `pipeline.run()` now RAISES `TransientPhaseError` / `SlotSaturation` (worker handles both — Task 6), still returns normally on hard failures.

- [ ] **Step 1: Write the failing test** (stub pattern copied from `tests/services/test_pipeline_judge_status.py` — module import + `monkeypatch.setattr(pipeline, …)`):

```python
"""Transient failures propagate for queue retry; hard failures stay terminal.

RED-proofs: today _execute_one_phase marks the job failed for EVERY class and
every upward path swallows, so (a) asserts an exception escapes where today
none does, and (c) asserts NO set_status('failed') call where today there is
one."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import pipeline
from app.services.errors import (
    PhaseAttemptTimeout,
    SlotSaturation,
    TransientPhaseError,
)


def test_phase_error_message_never_blank():
    assert pipeline._phase_error_message("extract", asyncio.TimeoutError()) == \
        "extract: TimeoutError()"
    assert pipeline._phase_error_message("extract", RuntimeError("boom")) == \
        "extract: boom"


@pytest.mark.parametrize("exc,expected", [
    (PhaseAttemptTimeout("per-attempt timeout after 600s"), True),
    (RuntimeError("429 RESOURCE_EXHAUSTED"), True),          # rate-limited
    (RuntimeError("socket connection closed unexpectedly"), True),  # transient
    (RuntimeError("malformed response envelope"), False),    # hard
    (RuntimeError("quota exceeded for project"), False),     # wall stays terminal
])
def test_requeue_worthy_classes(exc, expected):
    assert pipeline._requeue_worthy(exc) is expected


def _phase_kwargs(**over):
    """Minimal _execute_one_phase kwargs; implementer: copy the full kwarg
    set from test_pipeline_judge_status.py's existing call helper and merge."""
    ...


def test_transient_failure_raises_transient_phase_error(monkeypatch):
    async def failing(*a, **k):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(pipeline, "_execute_phase", failing)
    set_status = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", set_status)
    with pytest.raises(TransientPhaseError) as ei:
        asyncio.run(pipeline._execute_one_phase(**_phase_kwargs()))
    assert str(ei.value).startswith("extract: ")
    set_status.assert_not_awaited()     # job NOT marked failed — worker decides


def test_hard_failure_marks_failed_and_raises(monkeypatch):
    async def failing(*a, **k):
        raise RuntimeError("malformed response envelope")
    monkeypatch.setattr(pipeline, "_execute_phase", failing)
    set_status = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", set_status)
    with pytest.raises(RuntimeError):
        asyncio.run(pipeline._execute_one_phase(**_phase_kwargs()))
    assert set_status.await_args.kwargs["error_message"] == \
        "extract: malformed response envelope"


def test_slot_saturation_passes_through_unmarked(monkeypatch):
    async def saturated(*a, **k):
        raise SlotSaturation("429 fleet credential slot wait exhausted (…)")
    monkeypatch.setattr(pipeline, "_execute_phase", saturated)
    set_status = AsyncMock()
    monkeypatch.setattr(pipeline.jobs_repo, "set_status", set_status)
    with pytest.raises(SlotSaturation):
        asyncio.run(pipeline._execute_one_phase(**_phase_kwargs()))
    set_status.assert_not_awaited()
```

  The implementer fills `_phase_kwargs` from the real signature (pipeline.py:485-564) — every parameter, `phase_name="extract"`, `transport="api"`; `events_bus.publish` monkeypatched with `AsyncMock()`; `SessionLocal` monkeypatched with the same async-context stub `test_pipeline_judge_status.py` uses.

- [ ] **Step 2: Run to verify fail** — `uv run python -m pytest tests/services/test_pipeline_transient_propagation.py -q` → helpers missing / wrong propagation.
- [ ] **Step 3: Implement** in `pipeline.py`:

  (a) module-level helpers (place near `_custom_for`, pipeline.py:890):

```python
def _phase_error_message(phase_name: str, exc: BaseException) -> str:
    """'<phase>: <reason>' with a guaranteed non-blank reason (repr fallback
    for exceptions like asyncio.TimeoutError whose str() is '')."""
    text = str(exc).strip() or repr(exc)
    return f"{phase_name}: {text}"


def _requeue_worthy(exc: BaseException) -> bool:
    """Transient-only queue-retry policy (user-locked 2026-07-20): attempt
    timeouts, rate-limit 429s, and transient net errors get the bounded
    queue retry; hard errors and walls stay terminal (retries bill real $)."""
    if isinstance(exc, PhaseAttemptTimeout):
        return True
    if agent._is_rate_limited(str(exc)):
        return True
    return failure_classifier.classify(exc) == "transient"
```

  (b) `_execute_one_phase` except-chain (:565-582) becomes:

```python
    except SessionLimitPause:
        raise  # worker requeues — job must NOT be marked failed
    except SlotSaturation:
        raise  # worker parks with cooldown — job must NOT be marked failed
    except Exception as exc:
        phase_ms = (perf_counter() - t_phase) * 1000
        msg = _phase_error_message(phase_name, exc)
        log.exception(
            f"[job {job_id}] phase '{phase_name}' FAILED after {phase_ms:.0f}ms: {msg}"
        )
        await events_bus.publish(
            resource_id, "error", {"phase_name": phase_name, "message": msg}
        )
        if _requeue_worthy(exc):
            # Do NOT mark failed here — propagate so the worker applies the
            # bounded queue retry (mark_failed_with_retry, queue-correctness-1).
            raise TransientPhaseError(msg) from exc
        async with SessionLocal() as session:
            await jobs_repo.set_status(
                session, job_id, "failed",
                completed_at=_utcnow(),
                error_message=msg,
            )
            await session.commit()
        raise
```

  (c) head loop (:331-336):

```python
            except (SessionLimitPause, SlotSaturation, TransientPhaseError):
                raise  # propagate to worker — requeue/park, not a swallow
            except Exception:
                # _execute_one_phase already published the error event and
                # marked the job failed (hard class). We just unwind cleanly.
                return
```

  (d) content-path catch (:401-408): same triple re-raise replaces the bare `except SessionLimitPause: raise`; keep the `RuntimeError` sentinel branch unchanged.

  (e) scheduler `task.result()` (:724-746): change the pause branch's `except SessionLimitPause:` to `except (SessionLimitPause, SlotSaturation, TransientPhaseError):` (cancel peers → drain → `raise`); the generic `except Exception:` branch stays the hard-class path.

  (f) top-level `run()` (:456-459): `except SessionLimitPause:` → `except (SessionLimitPause, SlotSaturation, TransientPhaseError):` with the same close-bus-and-raise comment.

- [ ] **Step 4: Run** — new file green, then the pipeline neighborhood: `uv run python -m pytest tests/services/ -q -k "pipeline or failover or session_limit"` → green.
- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py tests/services/test_pipeline_transient_propagation.py
git commit -m "feat(pipeline): transient failures propagate for bounded queue retry; hard stay terminal

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 5: Cancelled-sibling phase rows are reset (+ judge/solver saturation pass-through)

**Files:**
- Modify: `app/repositories/phase_outputs.py` (new `fail_abandoned_phases`), `app/services/pipeline.py` (`_abandon_inflight` helper + calls at the three cancel sites), `app/services/phase_judge.py:246-251`, `app/services/solver.py:154`
- Test: `tests/repositories/test_phase_outputs_abandoned.py` (new, real-DB-marked), `tests/services/test_scheduler_abandoned_rows.py` (new)

**Interfaces:**
- Produces: `phase_outputs.fail_abandoned_phases(session, job_id, *, phase_names: list[str], error_message: str) -> int` (rows updated); `pipeline._abandon_inflight(job_id, phase_names, reason) -> None` (shielded, never raises).

- [ ] **Step 1: repo test** — `tests/repositories/test_phase_outputs_abandoned.py`, gated like the repo's other real-DB tests (`pytest.mark.skipif(not os.environ.get("RUN_DB_INTEGRATION"), …)`; copy the skip idiom + session fixture from an existing `tests/repositories/` real-DB file):

```python
async def test_fail_abandoned_marks_pending_and_running_only(db_session, seeded_job):
    """Seed 4 phase rows: pending, running, done, failed. RED-proof: predicate
    must flip exactly pending+running → failed and freeze done."""
    n = await phase_outputs.fail_abandoned_phases(
        db_session, seeded_job.id,
        phase_names=["flashcards", "boss-arena", "reading", "reflection"],
        error_message="abandoned: sibling phase failed",
    )
    assert n == 2
    rows = {r.phase_name: r for r in await phase_outputs.list_for_job(db_session, seeded_job.id)}
    assert rows["flashcards"].status == "failed"       # was pending
    assert rows["boss-arena"].status == "failed"       # was running
    assert rows["boss-arena"].error_message == "abandoned: sibling phase failed"
    assert rows["reading"].status == "done"            # frozen
    assert rows["reflection"].status == "failed"       # untouched, no message overwrite
    assert rows["reflection"].error_message is None
```

- [ ] **Step 2: implement repo function** — `app/repositories/phase_outputs.py`:

```python
async def fail_abandoned_phases(
    session: AsyncSession,
    job_id: UUID,
    *,
    phase_names: list[str],
    error_message: str,
) -> int:
    """Mark still-pending/running phases of a job 'failed' after their
    siblings' cancellation orphaned them (scheduler peer-cancel leaves rows
    'running': CancelledError is a BaseException, so per-phase except-Exception
    cleanup never ran — queue-correctness-1). 'done' rows are untouched."""
    if not phase_names:
        return 0
    from sqlalchemy import func as sa_func
    stmt = (
        update(PhaseOutput)
        .where(
            PhaseOutput.job_id == job_id,
            PhaseOutput.phase_name.in_(phase_names),
            PhaseOutput.status.in_(("pending", "running")),
        )
        .values(status="failed", error_message=error_message,
                completed_at=sa_func.now())
    )
    result = await session.execute(stmt)
    return result.rowcount
```

  Run the repo test against the scratch DB (recipe from the SDD memory: `createdb -O edu edu_scratch_qc` on 127.0.0.1, `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_scratch_qc uv run python -m pytest tests/repositories/test_phase_outputs_abandoned.py -q`) → RED first (function missing), then GREEN.

- [ ] **Step 3: scheduler test** — `tests/services/test_scheduler_abandoned_rows.py`: monkeypatch `pipeline._execute_one_phase` so phase "a" fails hard instantly and phase "b" sleeps forever; monkeypatch `pipeline._abandon_inflight` with an `AsyncMock`; run `_run_content_phases_parallel` with a 2-phase `content_phases` and `PHASE_DEPS` making both root-level; assert it was awaited once with `phase_names=["b"]` and a reason containing `"sibling"`. RED: helper doesn't exist.
- [ ] **Step 4: implement pipeline side** — helper next to `_emit_started`:

```python
async def _abandon_inflight(job_id: UUID, phase_names: list[str], reason: str) -> None:
    """Best-effort, cancellation-shielded reset of orphaned phase rows.
    Mirrors worker.py's shielded cancel-finalize craft: a cancellation
    delivered while this write runs must not kill the write."""
    if not phase_names:
        return
    async def _do() -> None:
        try:
            async with SessionLocal() as session:
                await phase_repo.fail_abandoned_phases(
                    session, job_id,
                    phase_names=phase_names, error_message=reason,
                )
                await session.commit()
        except Exception:
            logger.exception(f"[job {job_id}] abandoned-phase reset failed")
    task = asyncio.create_task(_do())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        pass
```

  At each of the three cancel-and-drain sites in `_run_content_phases_parallel`, capture the names BEFORE clearing and call the helper after the `gather`:

```python
                    abandoned = list(in_flight.keys())
                    for peer in in_flight.values():
                        peer.cancel()
                    if in_flight:
                        await asyncio.gather(*in_flight.values(), return_exceptions=True)
                        in_flight.clear()
                    await _abandon_inflight(
                        job_id, abandoned, "abandoned: sibling phase failed"
                    )
```

  Reasons per site: pause/park branch → `"abandoned: job requeued (pause/park)"`; hard-failure branch → `"abandoned: sibling phase failed"`; external-cancel branch (:749-759) → `"abandoned: job cancelled"`. (`_run_content_phases_parallel` already receives `job_id`.)
- [ ] **Step 5: judge/solver pass-through** — `phase_judge.py:246` head of the except block, BEFORE the auth check:

```python
        if is_slot_saturation(exc):
            raise SlotSaturation(str(exc))  # park the job — do not ship unjudged
```

  and identically at `solver.py:154`'s except head. Add `from app.services.errors import SlotSaturation, is_slot_saturation` imports. Extend `tests/services/test_pipeline_judge_status.py`-style: two small tests (one per module) that a marker-bearing RuntimeError from the underlying call raises `SlotSaturation` instead of degrading.
- [ ] **Step 6: Run** — `uv run python -m pytest tests/services/ tests/repositories/ -q` green (real-DB file skips without the flag; run it once WITH the flag on the scratch DB and paste the pass into the commit body).
- [ ] **Step 7: Commit**

```bash
git add app/repositories/phase_outputs.py app/services/pipeline.py app/services/phase_judge.py app/services/solver.py tests/repositories/test_phase_outputs_abandoned.py tests/services/test_scheduler_abandoned_rows.py
git commit -m "fix(pipeline): reset orphaned sibling phase rows on cancel; judge/solver park on slot saturation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 6: Worker parks `SlotSaturation` + repo requeue + setting

**Files:**
- Modify: `app/config.py` (one field), `app/repositories/jobs.py` (new `requeue_slot_saturated`), `app/services/worker.py` (`_run_job` except-chain, worker.py:536-560)
- Test: `tests/services/test_worker_slot_saturation.py` (new), `tests/repositories/test_jobs_requeue_slot.py` (new, real-DB-marked)

**Interfaces:**
- Consumes: `SlotSaturation` (Task 1).
- Produces: `jobs_repo.requeue_slot_saturated(session, job_id, *, error: str, cooldown_seconds: int) -> None`; `settings.slot_saturation_requeue_seconds: int = 90`.

- [ ] **Step 1: Write failing tests** — `tests/services/test_worker_slot_saturation.py` (mock pattern from `test_worker_cooldown.py`):

```python
"""Worker parks a SlotSaturation job: requeue with cooldown, attempt refunded,
_mark_failed NEVER called, and NO worker-level cooldown (other credentials'
jobs must keep flowing).

RED-proof: without the except SlotSaturation branch, the generic
except Exception → _mark_failed handler burns an attempt."""
```

  Tests: patch `worker_mod.pipeline.run` (`AsyncMock(side_effect=SlotSaturation("429 fleet credential slot wait exhausted (…)"))`), patch `worker_mod.jobs_repo.requeue_slot_saturated` + `worker_mod.jobs_repo.mark_failed_with_retry` with `AsyncMock`s, patch `SessionLocal` with the async-context stub used in the existing worker tests, run `Worker(concurrency=1)._run_job(<uuid>, …)` (copy the invocation shape from whichever existing test drives `_run_job`; if none does, drive the smallest worker method that owns the except-chain). Assert: `requeue_slot_saturated` awaited once with `cooldown_seconds=90`; `mark_failed_with_retry` NOT awaited; `worker._cooldown_until` still `None`.

  `tests/repositories/test_jobs_requeue_slot.py` (real-DB-marked, scratch-DB): seed a job `status='running', attempts=2, claimed_by='w'`; call `requeue_slot_saturated(cooldown_seconds=90)`; assert `status=='pending'`, `attempts==1` (refund), `claimed_by is None`, `current_phase is None`, and `scheduled_at > now() + 60s` (DB clock) — the RED-proof that the interval actually lands in SQL.

- [ ] **Step 2: Run to verify fail** — both files → FAIL (missing function/branch).
- [ ] **Step 3: Implement:**

  `app/config.py` (next to `credential_slot_wait_seconds`, config.py:191):

```python
    # Cooldown for a job parked by fleet credential-slot saturation
    # (queue-correctness-1): status='pending' with scheduled_at pushed this
    # far into the future. Attempt is refunded — saturation is back-pressure,
    # not a job defect.
    slot_saturation_requeue_seconds: int = Field(default=90, ge=1)
```

  `app/repositories/jobs.py` (below `requeue_session_limited`, jobs.py:705-733 — same shape, different `scheduled_at`):

```python
async def requeue_slot_saturated(
    session: AsyncSession,
    job_id: UUID,
    *,
    error: str,
    cooldown_seconds: int,
) -> None:
    """Park a job whose api call exhausted the fleet credential-slot wait.

    Like requeue_session_limited: attempt refunded (claim's increment is
    compensated), claim cleared, NOT failed. Unlike it: scheduled_at is
    pushed cooldown_seconds into the future (DB clock) so the fleet backs
    off the saturated credential instead of thrashing re-claims."""
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(
            status="pending",
            attempts=func.greatest(HomeworkJob.attempts - 1, 0),
            claimed_at=None,
            claimed_by=None,
            current_phase=None,
            last_error=error,
            scheduled_at=func.now()
            + func.make_interval(0, 0, 0, 0, 0, 0, cooldown_seconds),
        )
    )
```

  `app/services/worker.py` — insert BETWEEN the `except SessionLimitPause` block (:536-555) and `except Exception` (:556):

```python
            except SlotSaturation as e:
                # Fleet credential saturation: park the job with a cooldown.
                # No worker cooldown (unlike session-limit) — jobs billing
                # OTHER credentials must keep claiming.
                try:
                    async with SessionLocal() as session:
                        await jobs_repo.requeue_slot_saturated(
                            session, job_id, error=str(e),
                            cooldown_seconds=settings.slot_saturation_requeue_seconds,
                        )
                        await session.commit()
                except Exception:
                    logger.exception(
                        f"worker {self.id} job={job_id} requeue_slot_saturated failed"
                    )
                logger.warning(
                    f"worker {self.id} job={job_id} slot saturation → requeued "
                    f"+{settings.slot_saturation_requeue_seconds}s: {e}"
                )
```

  Import `SlotSaturation` alongside the existing `SessionLimitPause` import in worker.py.
- [ ] **Step 4: Run** — new tests green (repo one under the flag on the scratch DB); `uv run python -m pytest tests/services/ -q -k worker` green.
- [ ] **Step 5: Commit**

```bash
git add app/config.py app/repositories/jobs.py app/services/worker.py tests/services/test_worker_slot_saturation.py tests/repositories/test_jobs_requeue_slot.py
git commit -m "feat(worker): SlotSaturation parks the job — requeue with cooldown, attempt refunded

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 7: End-to-end connective test (the review's named gap)

**Files:**
- Test: `tests/services/test_queue_retry_e2e.py` (new)

**Interfaces:** consumes everything above; produces no new API.

- [ ] **Step 1: Write the chain test** — each link REAL, only the boundaries stubbed:

```python
"""E2E: persistent attempt timeout → pipeline propagation → worker
mark_failed_with_retry → bounded pending retry. Closes the review's named
test gap (no test connected these four links)."""
```

  - (a) **failover link (real):** `_run_with_failover` with `per_attempt_timeout_seconds=0.02` and a hung `run_fn` → raises `PhaseAttemptTimeout` (reuses Task 3's fixture shape, asserts the TYPE the next link consumes).
  - (b) **phase link (real):** `_execute_one_phase` with `_execute_phase` raising exactly that `PhaseAttemptTimeout` instance → raises `TransientPhaseError` whose message starts `"extract: per-attempt timeout"`; `jobs_repo.set_status` mock asserts NOT awaited.
  - (c) **run link (real):** `pipeline.run` with `_execute_one_phase` patched to raise that `TransientPhaseError` → `pytest.raises(TransientPhaseError)` (RED-proof vs today's swallow at :460); `events_bus` stubbed.
  - (d) **worker link (real):** worker `_run_job` with `pipeline.run` raising `TransientPhaseError("extract: per-attempt timeout after 600s")` → `mark_failed_with_retry` awaited once, `error_message` containing `"per-attempt timeout"`, `max_attempts=worker.max_attempts`.
  - (e) **bounded-retry link (real-DB-marked, scratch DB):** seed job `attempts=1` → `mark_failed_with_retry` returns `"pending"` with future `scheduled_at`; bump to `attempts=3` → returns `"failed"` terminal. (Exercises jobs.py:672-702 for real.)
- [ ] **Step 2: RED where promised** — run against a stashed pre-Task-4 checkout is NOT required; instead each link's RED-proof is documented inline (links a–d fail by construction if the corresponding task's change is reverted — state which assertion catches it in each docstring).
- [ ] **Step 3: Run** — `uv run python -m pytest tests/services/test_queue_retry_e2e.py -q` green (link e under the flag).
- [ ] **Step 4: Commit**

```bash
git add tests/services/test_queue_retry_e2e.py
git commit -m "test(queue): e2e chain — attempt timeout to bounded pending retry

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 8: Full suite, acceptance smoke, finish (controller)

**Files:**
- Modify: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `docs/HOW_IT_WORKS.md` (retry/error-handling paragraphs), `docs/CODE_MAP.md` (errors.py + new repo functions), `docs/memory/ROADMAP.md` (close the queue-correctness item if filed)
- Move: this plan → `docs/superpowers/plans/shipped/`

- [ ] **Step 1:** `uv run python -m pytest tests/ -q` → full suite green (canonical bar, no flag). Then once more with `RUN_DB_INTEGRATION=1` + scratch DB for the marked files.
- [ ] **Step 2: Acceptance smoke (real api, bounded, money-rule-compliant):** one minimal real generation call over `transport=api` proving the happy path is untouched — reuse the established single-call smoke shape (in-process `run_phase_prompt` with a tiny prompt, gemini flash). Expected < $0.01; report the actual $ from `agent_usages`. **Fact over theory:** also verify in logs that the call acquired+released a credential slot normally.
- [ ] **Step 3: Worklog + docs:** MASTER_MEMORY entry + INDEX row (**re-check the INDEX tail number at write time** — 0154 is taken by PR #108's branch); de-stale `docs/HOW_IT_WORKS.md` (the "what happens when a phase fails" story now has three outcomes: transient→bounded requeue, saturation→park+cooldown, hard→terminal) and `docs/CODE_MAP.md` (errors.py signals, `fail_abandoned_phases`, `requeue_slot_saturated`).
- [ ] **Step 4: Finish:** `git fetch origin && git log HEAD..origin/Nggaev-v2` → rebase if moved + re-run suite; `git mv` this plan to `shipped/`; push `feat/queue-transient-retry`; open PR to `Nggaev-v2` for GK2 (never self-merge).

## Self-review (done at write time)

- **Coverage vs the verified findings:** claim 1+3 (timeout includes slot wait / not transient) → Tasks 2+3+4+6; claim 2 (blank error) → Tasks 1+3+4; claim 4 (queue retry bypassed, incl. the broader :460 swallow) → Task 4 (c–f) + Task 7c; claim 5 (orphaned running rows) → Task 5; claim 6 (usage row) → out of scope, stated in Approach; test gap → Task 7.
- **Type consistency:** `SlotSaturation`/`TransientPhaseError`/`PhaseAttemptTimeout` names and `fail_abandoned_phases`/`requeue_slot_saturated` signatures are identical across Tasks 1–7. `_requeue_worthy` and `_phase_error_message` defined in Task 4, consumed only there and in tests.
- **Known open point for the implementer:** the exact accessor for a `Provider` instance in Task 2's test and the exact `_run_job` invocation shape in Task 6's test are to be copied from existing tests/greps named in the steps — flagged inline, not placeholders for behavior.
