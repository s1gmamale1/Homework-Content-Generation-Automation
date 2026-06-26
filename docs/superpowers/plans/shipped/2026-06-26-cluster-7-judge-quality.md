# Cluster 7 — Judge quality (campaign-readiness) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** close the three residual judge-quality gaps after the Cluster-3 judge work ([0079]): a self-grade-guard hole, content-policy refusals masquerading as transient errors, and `judge_status` being written-but-never-surfaced (so infra signals render like content defects).

**Architecture:** all server-side except the FE half of item 3. Touches `model_tiers.py` (the self-grade fallback constant), `phase_judge.py` (refusal classification + a new `JudgeOutcome.refused` flag), `pipeline.py` (`judge_status` computation + the retry-once guard + de-comingling infra warnings), `app/schemas/job.py` (serialize `judge_status`), and `web/src` (types + the preview console chip). **No schema/migration change** — `phase_outputs.judge_status` is already `String(24)` and `"refused"` (7 chars) fits beside the existing `"major_regen_failed"` (18).

**Tech Stack:** FastAPI + SQLAlchemy + pytest (asyncio_mode=auto); React/TS + Vite.

---

## Approach & key decisions

- **Chosen approach:** three independent fixes, each TDD-per-task, in one cluster-7 plan (no split). They are cohesive judge-quality work and item 3's backend serialization + FE renderer are useless apart (a field with no renderer is dead; a renderer with no field is broken), so they ship together. Scope ~6 tasks — within the Cluster-1 single-plan precedent.
- **Item 1 — `judge-self-fallback-1` (verified live bug, not latent in *this* env):** `.env` pins `JUDGE_PROVIDER=gemini` / `JUDGE_MODEL=gemini-3.1-pro-preview` (campaign config). `model_tiers._SELF_FALLBACK` is itself `("gemini","gemini-3.1-pro-preview")` (`model_tiers.py:55`), so when `judge_model_for` (`:74-76`) hits the self-fallback path *and the default judge is gemini-3.1-pro-preview*, it returns the generator's own model → self-grade. This is why `test_null_judge_model_same_provider_is_not_self_grade` and `test_self_grade_fallback_is_never_self_for_a_gemini_3_1_generator` are RED in this environment. **A *fixed* fallback constant can never be "non-self for any generator"** — whatever model it is, a generator that happens to equal it self-matches again (claude-opus-4-7 alone would relocate the hole to a claude-opus generator under the default claude judge, `config.py:111-112`). **Fix (Option B, user-recommended):** make the fallback *generator-aware* — two distinct frontier peers (`_PRIMARY_SELF_FALLBACK=("claude","claude-opus-4-7")`, the user-locked strongest peer + documented default judge; `_ALT_SELF_FALLBACK=("gemini","gemini-3.1-pro-preview")`, the strongest non-claude peer) and a `_self_fallback(resolved_gen)` helper that returns the alternate only when the generator IS the primary. Provably non-self for ANY generator (the two peers are distinct). The test-hygiene + product halves are coupled (the change breaks the old `result != mt._SELF_FALLBACK` assertion, which only made sense while the fallback was a single gemini constant), so they are one TDD task: rewrite/extend the tests to encode the real invariant + be config-independent (RED) → introduce the pair + helper (GREEN). **Accepted trade-off:** a single-provider worker that hits this *rare* self-fallback path and lacks the *peer's* creds degrades that one judge call to `unavailable` (safe — never blocks the job); under the locked campaign config the path is latent anyway (generator is 2.5-pro/flash, never == either peer).
- **Item 2 — `judge-refusal-1`:** a content-policy refusal makes the judge emit prose instead of JSON → `run_phase` exhausts schema retries and **raises** a `RuntimeError` (`agent.py:974`) whose message embeds the raw model text via `_failure_preview(stderr or text)` (`agent.py:539-562`, `:977`). `phase_judge.judge`'s `except` (`phase_judge.py:221-231`) catches it and degrades to `unavailable`, and `pipeline.py:942` then **burns the retry-once** on it. **Fix:** classify the refusal from `str(exc)` (same mechanism as `_is_auth_error`, `phase_judge.py:153-160`) with an *anchored* first-person-decline signal list that deliberately does NOT match a verbose-but-non-refusal judge answer (e.g. "the output violates requirement 3") nor a schema/CLI error; add `JudgeOutcome.refused: bool`; return a distinct refused outcome. `pipeline` then records `judge_status="refused"` and **skips** the transient retry-once for it.
- **Item 3 — `judge-unavailable` FE rendering + de-comingle:** `judge_status` is written to the DB (`pipeline.py:1037`) but absent from `PhaseOut` (`schemas/job.py:8-20`) and the FE; meanwhile the `"judge-unavailable: <ExcType>"` string co-mingles into `validation_warnings` (`pipeline.py:1025`) and renders as a content defect (`preview.tsx:206-228`, `job.tsx:513`). **Fix (observability only):** (a) `warnings = outcome.warnings if outcome.available else []` — the infra states (`unavailable`/`refused`) carry ONLY the infra string, so dropping it from `validation_warnings` loses nothing (the ExcType stays in logs + `judge_status` is queryable); `major_shipped`/`major_regen_failed` keep `available=True` so their genuine content warnings survive. (b) serialize `judge_status` on `PhaseOut` (auto-populated via `from_attributes=True`). (c) FE: add the field + a distinct per-phase chip in the preview console.
- **Verified facts (read against tip):** `_SELF_FALLBACK` `model_tiers.py:55`; `judge_model_for` `:66-76`; `resolve_judge` `:79-103`. `phase_judge.judge` except `:221-231`, `_is_auth_error` `:153-160`, `JudgeOutcome` `:38-44`. `pipeline` retry-once `:942-953`, regen guard `:958-960`, judge_status compute `:1018-1024`, `warnings = outcome.warnings` `:1025`, final `set_status(..., judge_status=...)` `:1028-1038`. `PhaseOut` `schemas/job.py:8-20`; `PhaseOut.model_validate` `jobs.py:459`. `phase_outputs.judge_status` `models/phase_output.py:34-35` (`String(24)`). FE: `PhaseOut` type `types.ts:120-131`, preview warnings `preview.tsx:130,206-228`. Harness: `tests/services/test_pipeline_judge_status.py` (spies `judge_status` + `set_status_calls`).

### Parallel-run conventions (REMEDIATION_CLUSTERS.md)

- **Branch:** `cluster-7-judge-quality`, cut off the CURRENT `origin/Nggaev-v2` tip — the correct base regardless of local state. Use a worktree: `git worktree add -b cluster-7-judge-quality ../HCGA-c7-judge-quality origin/Nggaev-v2`. (`git fetch origin` first so the base is current.)
- **Commit prefix:** `c7:`. **PR title:** `[cluster-7] Judge quality residuals`.
- **Worklog ID:** C7 has **no pre-assigned ID** (the table covers C1–C6 only). Determine the next-free `## [00NN]` against the LIVE tip **at finish**, not now — parallel branches collide on IDs. Write only that one block + its INDEX row.
- **No migration** → no alembic multi-head risk.
- **Stage only this cluster's lane files** per task — never `git add -A`.

---

## Task 1: Generator-aware self-grade fallback — non-self for ANY generator (item 1 — both halves, Option B)

**Files:**
- Modify: `app/services/model_tiers.py:51-76` (replace the `_SELF_FALLBACK` constant with the peer pair + `_self_fallback` helper; rewire `judge_model_for`) + the `resolve_judge` docstring (`:94-96`)
- Test: `tests/services/test_judge_resolution.py` (rewrite the two brittle tests + add coverage for the previously-relocated claude-opus hole)

- [ ] **Step 1: Rewrite/extend the tests to encode "non-self for ANY generator" (RED).**

Add at the top of the file:

```python
import pytest

from app.config import settings
```

Replace `test_null_judge_model_same_provider_is_not_self_grade` (`:31-40`) and `test_self_grade_fallback_is_never_self_for_a_gemini_3_1_generator` (`:50-57`) with the versions below, and add the new tests. The two replacements keep their names so the suite's coverage map is stable. The `claude-opus generator` test is the one that documents the hole Option A would have shipped — it must be RED against a single-constant fallback:

```python
def test_null_judge_model_same_provider_is_not_self_grade(monkeypatch):
    # REGRESSION: a gemini generator (explicit) + a gemini judge with Auto/None
    # model must NOT self-grade. None resolves to the provider default BEFORE the
    # equality check. Pin the default judge so the assertion is config-independent.
    monkeypatch.setattr(settings, "judge_provider", "gemini")
    monkeypatch.setattr(settings, "judge_model", "gemini-3.1-pro-preview")
    result = mt.resolve_judge("gemini", "gemini-3.1-pro-preview", "gemini", None)
    assert result != ("gemini", "gemini-3.1-pro-preview")        # not the generator
    assert result != ("gemini", None)                            # not an ambiguous self
    assert result == ("claude", "claude-opus-4-7")               # the non-gemini peer


def test_self_fallback_holds_when_default_judge_is_gemini_3_1(monkeypatch):
    # Even when the DEFAULT judge IS gemini-3.1-pro-preview, a gemini-3.1-pro-preview
    # generator must not grade itself → the claude peer.
    monkeypatch.setattr(settings, "judge_provider", "gemini")
    monkeypatch.setattr(settings, "judge_model", "gemini-3.1-pro-preview")
    result = mt.judge_model_for("gemini", "gemini-3.1-pro-preview")
    assert result != ("gemini", "gemini-3.1-pro-preview")
    assert result == ("claude", "claude-opus-4-7")


def test_self_grade_fallback_is_never_self_for_a_gemini_3_1_generator(monkeypatch):
    # resolve_judge with an explicit judge == the gemini-3.1 generator must swap to
    # the non-gemini peer, regardless of the configured default judge.
    monkeypatch.setattr(settings, "judge_provider", "gemini")
    monkeypatch.setattr(settings, "judge_model", "gemini-3.1-pro-preview")
    result = mt.resolve_judge(
        "gemini", "gemini-3.1-pro-preview", "gemini", "gemini-3.1-pro-preview")
    assert result != ("gemini", "gemini-3.1-pro-preview")
    assert result == ("claude", "claude-opus-4-7")


def test_self_fallback_is_non_self_for_a_claude_opus_generator(monkeypatch):
    # THE OPTION-B FIX: a claude-opus-4-7 generator under the DEFAULT claude-opus-4-7
    # judge must NOT self-grade. A single fixed claude-opus fallback would return the
    # generator's own model here; the generator-aware fallback must return the
    # distinct alternate peer instead.
    monkeypatch.setattr(settings, "judge_provider", "claude")
    monkeypatch.setattr(settings, "judge_model", "claude-opus-4-7")
    result = mt.judge_model_for("claude", "claude-opus-4-7")
    assert result != ("claude", "claude-opus-4-7")               # not the generator
    assert result == ("gemini", "gemini-3.1-pro-preview")        # the alternate peer

    # …and via the explicit-override path too.
    result2 = mt.resolve_judge("claude", "claude-opus-4-7", "claude", "claude-opus-4-7")
    assert result2 != ("claude", "claude-opus-4-7")
    assert result2 == ("gemini", "gemini-3.1-pro-preview")
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `uv run python -m pytest tests/services/test_judge_resolution.py -q`
Expected: FAIL — current `_SELF_FALLBACK == ("gemini","gemini-3.1-pro-preview")` fails the gemini-generator asserts; and there is no generator-aware fallback so `test_self_fallback_is_non_self_for_a_claude_opus_generator` fails (it references `_self_fallback` behavior that does not exist yet).

- [ ] **Step 3: Replace the constant with the generator-aware pair + helper (GREEN).**

In `app/services/model_tiers.py`, replace lines 51-55 (the `_SELF_FALLBACK` block):

```python
# No-self rule: when the configured judge model IS the generator, grade with a
# different strong peer so a model never grades its own output. A *fixed* fallback
# constant can't satisfy this — whatever model it is, a generator equal to it
# self-matches again. So the fallback is generator-AWARE: two distinct frontier
# peers, returning whichever is NOT the generator. claude-opus-4-7 is the strongest
# peer (and the documented default judge, config.py:111-112); gemini-3.1-pro-preview
# is the strongest non-claude peer and reliably on PATH. The result is non-self for
# ANY generator (the two peers are distinct). On a single-provider worker lacking the
# chosen peer's creds this rare path degrades that one judge call to "unavailable"
# (safe — never blocks the job).
_PRIMARY_SELF_FALLBACK: tuple[str, str] = ("claude", "claude-opus-4-7")
_ALT_SELF_FALLBACK: tuple[str, str] = ("gemini", "gemini-3.1-pro-preview")


def _self_fallback(resolved_gen: tuple[str, str]) -> tuple[str, str]:
    """A strong frontier peer guaranteed != ``resolved_gen`` (the no-self rule).
    Returns the alternate only when the generator IS the primary peer, so the
    result is non-self for ANY generator (the two peers are distinct)."""
    return _ALT_SELF_FALLBACK if resolved_gen == _PRIMARY_SELF_FALLBACK else _PRIMARY_SELF_FALLBACK
```

Rewire `judge_model_for` (`:66-76`) to use the helper:

```python
def judge_model_for(gen_provider: str, gen_model: Optional[str]) -> tuple[str, str]:
    """Pick the judge (provider, model).

    The judge is ``settings.judge_provider`` / ``settings.judge_model`` (default
    claude / claude-opus-4-7 — the strongest model, so it is >= every generator).
    If that would be the generating model itself (no-self), fall back to a strong
    frontier peer that is guaranteed non-self for ANY generator.
    """
    judge = (settings.judge_provider, settings.judge_model)
    resolved_gen = (gen_provider, gen_model or default_model(gen_provider))
    return _self_fallback(resolved_gen) if judge == resolved_gen else judge
```

Update the `resolve_judge` docstring note (`:94-96`) — replace the sentence about `_SELF_FALLBACK` being a fixed gemini model with: "the self-grade fallback is `judge_model_for`, which uses the generator-aware `_self_fallback` (a frontier peer guaranteed non-self for ANY generator) rather than a fixed constant that could itself self-match the generator."

- [ ] **Step 4: Run the judge-resolution tests to verify they pass.**

Run: `uv run python -m pytest tests/services/test_judge_resolution.py -q`
Expected: PASS (all tests in the file, incl. the unchanged `test_both_auto_same_provider_is_not_self_grade` and `test_same_provider_different_explicit_models_is_allowed`).

- [ ] **Step 4b: Fix the consumer of the removed symbol — `worker.py` capability gate (REQUIRED — added during execution).**

`worker._compute_capabilities` reads `model_tiers._SELF_FALLBACK` at module load (`worker.py:74`, via `CAPABILITIES` at `:91`) to compute `judge_fallback_api_ok` — the claim-gate flag (`jobs.py:342-346`) for jobs that generate ON the judge pair (`job_is_judge_pair`, `jobs.py:321-324`), which `judge_model_for` self-falls-back. Removing `_SELF_FALLBACK` crashes the worker at import. **Verified-mechanical fix (NOT a design decision):** `job_is_judge_pair` pins the generator to the judge pair, so the fallback provider is `_self_fallback(judge_pair)` — a single value known at startup, no per-job threading. It also preserves today's behavior: default judge `claude-opus-4-7` → `_self_fallback` returns gemini = the old fixed value; campaign judge `gemini-3.1-pro-preview` → returns claude (the now-correct peer).

In `app/services/worker.py:74`, replace `fb_provider, _ = model_tiers._SELF_FALLBACK` with:

```python
    fb_provider, _ = model_tiers._self_fallback((judge_provider, judge_model))
```

Update the `:79` comment `# §4a: jobs generating ON the judge pair get judged by _SELF_FALLBACK` → `# §4a: jobs generating ON the judge pair get judged by the generator-aware self-fallback peer`.

In `app/repositories/jobs.py:289`, update the docstring `back to \`model_tiers._SELF_FALLBACK\` and the worker needs` → `back to the generator-aware self-fallback peer and the worker needs`.

In `tests/services/test_judge_resolution.py` (the unchanged `test_exact_self_grade_is_swapped_to_a_non_self_judge` comment, ~`:25-28`), replace the trailing `NOT _SELF_FALLBACK.` with `NOT the raw fallback constant.` (purely a comment fix for the renamed symbol).

Confirm no bare symbol remains: `grep -rn "_SELF_FALLBACK" app/ tests/` — every hit must be `_PRIMARY_SELF_FALLBACK` / `_ALT_SELF_FALLBACK` / `_self_fallback`, none bare `_SELF_FALLBACK`.

- [ ] **Step 4c: Add the worker-capability test (RED → GREEN with the 4b fix).**

Append to `tests/services/test_auth_env.py` (beside the existing `test_compute_capabilities_*`):

```python
def test_compute_capabilities_judge_fallback_tracks_generator_aware_peer():
    """C7/Option-B: with the default judge = gemini-3.1-pro-preview, a job generating
    ON the judge pair self-falls-back to the CLAUDE peer (not gemini), so
    judge_fallback_api_ok must track claude creds. The existing tests (judge =
    claude-opus-4-7 → fallback gemini) still pin the gemini direction."""
    from app.services import worker

    # anthropic-only worker, judge pinned to gemini-3.1-pro-preview:
    # fallback peer for a gemini-3.1 generator is claude → reachable here.
    caps = worker._compute_capabilities(
        {"ANTHROPIC_API_KEY": "a"}, "gemini", "gemini-3.1-pro-preview", "gemini"
    )
    assert caps["judge_fallback_api_ok"] is True

    # gemini-vertex-only worker, same judge: the claude fallback is NOT reachable.
    caps2 = worker._compute_capabilities(
        {"GOOGLE_APPLICATION_CREDENTIALS": "/sa.json", "GOOGLE_CLOUD_PROJECT": "p"},
        "gemini", "gemini-3.1-pro-preview", "gemini",
    )
    assert caps2["judge_fallback_api_ok"] is False
```

Run: `uv run python -m pytest tests/services/test_auth_env.py -q`
Expected: PASS — the new test + all existing `test_compute_capabilities_*` (they pin judge=claude-opus-4-7, so `_self_fallback` returns gemini, unchanged).

- [ ] **Step 5: Commit (stage the full corrected lane).**

```bash
git add app/services/model_tiers.py tests/services/test_judge_resolution.py \
        app/services/worker.py app/repositories/jobs.py tests/services/test_auth_env.py \
        docs/superpowers/plans/2026-06-26-cluster-7-judge-quality.md
git commit -m "c7: generator-aware self-grade fallback (non-self for any generator)

Also fixes worker._compute_capabilities, which read the removed _SELF_FALLBACK
constant at import: judge_fallback_api_ok now tracks _self_fallback(judge_pair),
correct for the campaign gemini-3.1 judge and unchanged for the default claude judge.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Classify content-policy refusals in `phase_judge` (item 2 — judge layer)

**Files:**
- Modify: `app/services/phase_judge.py` (add `_REFUSAL_SIGNALS` + `_is_refusal`; add `JudgeOutcome.refused`; branch in the `except`)
- Test: `tests/services/test_phase_judge.py`

- [ ] **Step 1: Write the failing tests.**

Append to `tests/services/test_phase_judge.py`:

```python
import pytest


@pytest.mark.parametrize("msg", [
    "I cannot assist with that request.",
    "I'm unable to help with this content.",
    "I must decline to evaluate this.",
    "I won't provide an evaluation of this material.",
    "This request goes against my guidelines.",
    "Sorry, this violates content policy and I can't continue.",
])
def test_is_refusal_true(msg):
    assert pj._is_refusal(RuntimeError(msg)) is True


@pytest.mark.parametrize("msg", [
    "",
    "schema validation failed: 1 validation error for Verdict\npassed\n  Field required",
    "RuntimeError: CLI exploded",
    "The output violates requirement 3: only 2 checkpoints found, contract requires 3.",
    "401 unauthorized: invalid api key",
])
def test_is_refusal_false(msg):
    assert pj._is_refusal(RuntimeError(msg)) is False


def test_judge_returns_refused_outcome_and_is_distinct_from_unavailable(monkeypatch):
    monkeypatch.setattr(pj, "get_prompt", lambda s, p: "CONTRACT")

    async def _refuse(**kwargs):
        # mirrors agent.run_phase's raise on a refusal: schema retries exhausted,
        # the model's refusal prose is embedded in the exception via _failure_preview.
        raise RuntimeError(
            "phase.run __judge__: schema Verdict validation failed after 2 attempts: "
            "... :: I cannot assist with creating or evaluating this content."
        )

    monkeypatch.setattr(agent, "run_phase", _refuse)
    out = _call_judge()
    assert out.available is False        # no verdict
    assert out.refused is True           # but distinctly a refusal, not a transient error
    assert out.passed is True            # never blocks generation
    assert out.warnings and out.warnings[0].startswith("judge-refused")
```

- [ ] **Step 2: Run to verify they fail.**

Run: `uv run python -m pytest tests/services/test_phase_judge.py -q`
Expected: FAIL — `pj._is_refusal` and `JudgeOutcome.refused` do not exist yet.

- [ ] **Step 3: Implement.**

In `app/services/phase_judge.py`, add `refused` to `JudgeOutcome` (after `has_major`, `:44`):

```python
    refused: bool = False    # judge declined on content policy (distinct from a transient error)
```

Add, just below `_AUTH_SIGNALS` / `_is_auth_error` (after `:160`):

```python
# Anchored first-person-decline phrases that mark a content-policy REFUSAL (the
# judge emitted prose instead of a Verdict, so run_phase exhausted schema retries
# and raised — the refusal text rides in the exception via _failure_preview).
# Deliberately anchored: must NOT match a verbose-but-substantive judge answer
# ("the output violates requirement 3"), a schema-validation error, or a CLI error.
_REFUSAL_SIGNALS = (
    "i cannot assist", "i can't assist",
    "i cannot help", "i can't help",
    "i am unable to assist", "i'm unable to assist",
    "i am unable to help", "i'm unable to help",
    "i must decline", "i cannot comply", "i can't comply",
    "i will not provide", "i won't provide",
    "i cannot create", "i can't create",
    "against my guidelines", "violates content polic",
)


def _is_refusal(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in _REFUSAL_SIGNALS)
```

In the `except Exception as exc` block (`:221-231`), insert the refusal branch AFTER the api-auth re-raise and BEFORE the generic unavailable return:

```python
    except Exception as exc:  # noqa: BLE001 — judge must NEVER block generation
        if transport == "api" and _is_auth_error(exc):
            logger.error(f"phase_judge api auth failure for {phase_name}: {exc!r}")
            raise
        if _is_refusal(exc):
            logger.warning(f"phase_judge refused (content policy) for {phase_name}: {exc!r}")
            return JudgeOutcome(
                available=False, refused=True, passed=True,
                warnings=["judge-refused: content policy"], feedback="",
            )
        logger.warning(f"phase_judge unavailable for {phase_name}: {exc!r}")
        return JudgeOutcome(
            available=False, passed=True,
            warnings=[f"judge-unavailable: {type(exc).__name__}"], feedback="",
        )
```

- [ ] **Step 4: Run to verify they pass.**

Run: `uv run python -m pytest tests/services/test_phase_judge.py -q`
Expected: PASS (incl. the existing `test_judge_degrades_when_run_phase_raises`, whose "CLI exploded" is NOT a refusal → still `unavailable`).

- [ ] **Step 5: Commit.**

```bash
git add app/services/phase_judge.py tests/services/test_phase_judge.py
git commit -m "c7: detect judge content-policy refusals (distinct from transient unavailable)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Pipeline records `judge_status=refused` and skips the retry-once (item 2 — pipeline layer)

**Files:**
- Modify: `app/services/pipeline.py:942` (retry-once guard) + `:1018-1024` (judge_status compute)
- Test: `tests/services/test_pipeline_judge_status.py`

- [ ] **Step 1: Write the failing test.**

Append to `tests/services/test_pipeline_judge_status.py` (reuses the file's `patch_io` fixture + `_make_kwargs`):

```python
def _refused() -> JudgeOutcome:
    return JudgeOutcome(
        available=False, refused=True, passed=True,
        warnings=["judge-refused: content policy"], feedback="",
    )


async def test_judge_status_refused_skips_retry_once(monkeypatch, patch_io):
    """A refusal is recorded as judge_status='refused' and is NOT retried (unlike a
    transient unavailable, which is retried once)."""
    calls = []

    async def fake_judge(**kw):
        calls.append("judge")
        return _refused()

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    kw = _make_kwargs()
    await pipeline._execute_phase(**kw)

    assert patch_io.judge_status == "refused", f"got {patch_io.judge_status!r}"
    assert len(calls) == 1, f"refusal must not be retried; got {len(calls)} judge calls"
```

- [ ] **Step 2: Run to verify it fails.**

Run: `uv run python -m pytest tests/services/test_pipeline_judge_status.py::test_judge_status_refused_skips_retry_once -q`
Expected: FAIL — current code retries (calls == 2) and computes `"unavailable"`.

- [ ] **Step 3: Implement.**

In `app/services/pipeline.py`, change the retry-once guard (`:942`) so a refusal is never retried:

```python
        if not outcome.available and not outcome.refused:
```

(Update the adjacent comment to note "a content-policy refusal is recorded distinctly and not retried.")

In the judge_status compute block (`:1018-1024`), add the `refused` branch first:

```python
        if judge_status is None:
            if getattr(outcome, "refused", False):
                judge_status = "refused"
            elif not outcome.available:
                judge_status = "unavailable"
            elif outcome.passed or not outcome.has_major:
                judge_status = "ok"
            else:
                judge_status = "major_shipped"
```

(The regen loop guard at `:958-960` already breaks on `not outcome.available`, so a refused outcome never triggers a regen — no change needed there.)

- [ ] **Step 4: Run to verify it passes (+ no regression in the file).**

Run: `uv run python -m pytest tests/services/test_pipeline_judge_status.py -q`
Expected: PASS (all 6 prior tests + the new one).

- [ ] **Step 5: Commit.**

```bash
git add app/services/pipeline.py tests/services/test_pipeline_judge_status.py
git commit -m "c7: pipeline records judge_status=refused and skips the retry-once for refusals

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Serialize `judge_status` + stop co-mingling infra warnings (item 3 — backend)

**Files:**
- Modify: `app/schemas/job.py:20` (add field to `PhaseOut`); `app/services/pipeline.py:1025`
- Test: `tests/services/test_pipeline_judge_status.py` (de-comingle assertion) + `tests/api/` (schema field)

- [ ] **Step 1: Write the failing tests.**

(a) De-comingle — append to `tests/services/test_pipeline_judge_status.py`:

```python
async def test_unavailable_does_not_comingle_into_validation_warnings(monkeypatch, patch_io):
    """judge_status captures the infra state; the 'judge-unavailable:' string must
    NOT leak into validation_warnings (content defects)."""
    async def fake_judge(**kw):
        return _unavail()

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    kw = _make_kwargs()
    await pipeline._execute_phase(**kw)

    done_call = next(c for c in patch_io.set_status_calls if c[0] == "done")
    vw = done_call[1].get("validation_warnings")
    assert not vw, f"infra warning should not co-mingle into validation_warnings; got {vw!r}"
    assert patch_io.judge_status == "unavailable"


async def test_major_shipped_keeps_content_warnings(monkeypatch, patch_io):
    """A real MAJOR content failure (available=True) keeps its warnings."""
    monkeypatch.setattr(_settings, "max_judge_regens", 0)

    async def fake_judge(**kw):
        return _major()

    monkeypatch.setattr(pipeline, "_judge_with_timeout", fake_judge)

    kw = _make_kwargs()
    await pipeline._execute_phase(**kw)

    done_call = next(c for c in patch_io.set_status_calls if c[0] == "done")
    assert done_call[1].get("validation_warnings") == ["MAJOR: content issue"]
    assert patch_io.judge_status == "major_shipped"
```

(b) Schema field — add to `tests/api/test_job_serialization.py` (create the file if absent):

```python
from app.schemas.job import PhaseOut


def test_phaseout_serializes_judge_status():
    class _Row:
        phase_name = "preview"
        phase_order = 1
        status = "done"
        output_md = "x"
        tokens_input = 1
        tokens_output = 1
        started_at = None
        completed_at = None
        error_message = None
        validation_warnings = None
        judge_status = "refused"

    out = PhaseOut.model_validate(_Row())
    assert out.judge_status == "refused"
```

- [ ] **Step 2: Run to verify they fail.**

Run: `uv run python -m pytest tests/services/test_pipeline_judge_status.py tests/api/test_job_serialization.py -q`
Expected: FAIL — `judge-unavailable: TimeoutError` currently lands in `validation_warnings`; `PhaseOut` has no `judge_status`.

- [ ] **Step 3: Implement.**

In `app/schemas/job.py`, add to `PhaseOut` (after `validation_warnings`, `:20`):

```python
    judge_status: Optional[str] = None    # ok | major_shipped | major_regen_failed | unavailable | refused | None
```

In `app/services/pipeline.py:1025`, replace `warnings = outcome.warnings` with:

```python
        # Infra states (unavailable/refused) carry ONLY the infra string — keep it
        # out of validation_warnings (content defects); judge_status records it and
        # the ExcType stays in the logs. major_shipped/major_regen_failed keep
        # available=True so their genuine content warnings survive.
        warnings = outcome.warnings if outcome.available else []
```

- [ ] **Step 4: Run to verify they pass.**

Run: `uv run python -m pytest tests/services/test_pipeline_judge_status.py tests/api/test_job_serialization.py -q`
Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/schemas/job.py app/services/pipeline.py tests/services/test_pipeline_judge_status.py tests/api/test_job_serialization.py
git commit -m "c7: serialize judge_status + stop co-mingling infra warnings into validation_warnings

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Surface `judge_status` in the preview console (item 3 — FE)

**Files:**
- Modify: `web/src/lib/types.ts:120-131` (add field); `web/src/routes/preview.tsx` (chip beside the warnings badge)

- [ ] **Step 1: Add the type field.**

In `web/src/lib/types.ts`, add to `PhaseOut` (after `validation_warnings`, `:130`):

```typescript
  judge_status: string | null;
```

- [ ] **Step 2: Render a distinct judge_status chip.**

In `web/src/routes/preview.tsx`, just after the active phase is picked (`:130`, `const warnings = p.validation_warnings ?? [];`) add a small helper + render a chip in the phase header beside the existing `⚠ {warnings.length}` badge (`:206-211`). Add above the component (near `phaseTitle`, `:106`):

```typescript
// Infra/judge states that are NOT content defects — surfaced distinctly from
// validation_warnings so an ungraded/declined phase doesn't read like a content bug.
const JUDGE_STATUS_LABEL: Record<string, string> = {
  unavailable: "judge unavailable",
  refused: "judge declined",
  major_regen_failed: "regen failed",
  major_shipped: "major issue shipped",
};
```

Then in the phase header, beside the warnings badge (inside the same flex row at `:206`):

```tsx
          {p.judge_status && JUDGE_STATUS_LABEL[p.judge_status] && (
            <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 font-mono text-[0.62rem] uppercase tracking-wider text-amber-300/90">
              {JUDGE_STATUS_LABEL[p.judge_status]}
            </span>
          )}
```

(`ok` and `null` render nothing — only the noteworthy states show a chip.)

- [ ] **Step 3: Typecheck + build.**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: clean typecheck; build writes `web/dist/`.

- [ ] **Step 4: Commit.**

```bash
git add web/src/lib/types.ts web/src/routes/preview.tsx
git commit -m "c7: surface judge_status in the preview console, separate from content warnings

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Acceptance — real judge smoke + full suite

**Files:**
- Create: `scripts/smoke_judge_c7.py`

- [ ] **Step 1: Write the smoke (happy path unbroken).**

The only generation-affecting change is in the judge path; prove a real judge call still parses a Verdict through the new code (the refusal/de-comingle branches are unit-proven — a refusal cannot be forced deterministically against a live model, and the classification is a string-match claim, not a model-behavior claim).

```python
"""Acceptance smoke for Cluster 7: a real api judge call still returns a usable
verdict through the new refusal/de-comingle code. Run: uv run python -m scripts.smoke_judge_c7"""
import asyncio

from app.services import phase_judge


async def _main():
    out = await phase_judge.judge(
        subject="matematika",
        phase_name="preview",
        output_md="# Preview\n\nThis lesson introduces linear equations and how to isolate a variable.",
        lesson_context="The lesson covers solving linear equations by isolating the variable.",
        prior_outputs={},
        gen_provider="gemini", gen_model="gemini-2.5-flash",
        judge_provider="gemini", judge_model="gemini-2.5-flash",
        transport="api",
    )
    print(f"available={out.available} passed={out.passed} refused={out.refused} "
          f"warnings={out.warnings}")
    assert out.available is True, "judge should run + parse a Verdict through the new code"
    assert out.refused is False, "a normal output is not a refusal"
    print("SMOKE PASS: live judge returns a usable verdict through the c7 code path")


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 2: Run the smoke.**

Run: `uv run python -m scripts.smoke_judge_c7`
Expected: `SMOKE PASS`. (Vertex creds load from `.env` via `config.load_dotenv`.)

- [ ] **Step 3: Full suite green.**

Run: `uv run python -m pytest tests/ -q`
Expected: green (the 2 previously-RED `test_judge_resolution` tests now pass via Task 1). Note any pre-existing unrelated failures explicitly.

- [ ] **Step 4: Commit the smoke.**

```bash
git add scripts/smoke_judge_c7.py
git commit -m "c7: real api judge acceptance smoke (happy path unbroken)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Finish (controller — after the gate merges, NOT during execution)

- Rebase-check on `origin/Nggaev-v2` before PR; re-run the suite.
- Worklog: next-free `## [00NN]` in `MASTER_MEMORY.md` (verify against the live tip — C7 has no reserved ID) + an `INDEX.md` row.
- Close in `REMEDIATION_CLUSTERS.md` / `WISHLIST.md`: `judge-self-fallback-1`, `judge-refusal-1`, `judge-unavailable` FE rendering.
- `git mv` this plan → `docs/superpowers/plans/shipped/`.
- De-stale reference docs if they describe judge_status / judge behavior (`docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`). No `DEPLOY.md`/`DATABASE.md` change (no new env var, no schema change).

## Self-review

- **Spec coverage:** item 1 → Task 1; item 2 → Tasks 2-3; item 3 → Tasks 4-5; acceptance → Task 6. All three Cluster-7 items covered.
- **Placeholder scan:** none — every step has the real code, real test, exact command.
- **Type consistency:** `JudgeOutcome.refused` (Task 2) is read via `getattr(outcome, "refused", False)` in Task 3 and never elsewhere; `judge_status` string values (`refused`) match `phase_output.py:34` doc + `String(24)` width; `PhaseOut.judge_status` (Task 4) matches `types.ts` `judge_status` (Task 5) and the model attribute name (`from_attributes`). The self-grade fallback peers `_PRIMARY_SELF_FALLBACK=("claude","claude-opus-4-7")` / `_ALT_SELF_FALLBACK=("gemini","gemini-3.1-pro-preview")` are distinct (so `_self_fallback` is provably non-self); primary matches `config.py:111-112` default judge. The old single `_SELF_FALLBACK` constant is fully removed — grep confirmed its only references were `model_tiers.py` + `test_judge_resolution.py`, both updated here.
