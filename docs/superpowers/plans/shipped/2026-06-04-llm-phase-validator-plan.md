# LLM Phase Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the deterministic `phase_validator` rule engine with a single, self-verifying LLM judge that grades each generated phase against its own prompt contract and regenerates once (with cited failures fed back) before accepting — never blocking the job.

**Architecture:** A pure cross-provider tier map picks a judge model one tier above the *actual producer*. `phase_judge.judge()` hands the judge `get_prompt(subject,phase)` + `lesson_context` + the declared `prior_outputs` + the `output_md` under review, in a single call that cites violations then refutes its own list (anti-hallucination), returning a `Verdict`. The judge slots into the live `_execute_phase` (post-resilience) where `phase_validator.validate(...)` is called today; the regen reuses the existing `_run`/`_run_with_failover` path keyed on `produced_by`.

**Tech Stack:** FastAPI, asyncio, pydantic, pytest (**DB-free suite** — pure-function unit tests + `inspect.signature`/`inspect.getsource`; LLM behaviour proven by a real CLI smoke per CLAUDE.md). Everything LLM-facing goes through `app/services/agent.py`.

**Spec:** `docs/superpowers/specs/2026-06-04-llm-phase-validator-design.md`

**Commands:**
- Backend tests: `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q` (single: `… -m pytest tests/path::test -v`)

**Test-harness note (critical):** this repo's suite is **DB-free** (`tests/conftest.py` injects sentinel env only). Pure helpers get real assertions; functions that do I/O (the judge CLI call, the pipeline wiring) are verified by `inspect.signature` / `inspect.getsource`, and the real model behaviour is proven by the Task 5 live smoke. Do NOT invent a DB fixture.

**Ordering rule:** T1 (tiers) → T2 (operation param) → T3 (judge) are additive and independent-ish; T3 imports T1. **T4 (pipeline wiring) depends on T1–T3.** T5 is acceptance.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `app/services/model_tiers.py` (new) | pure cross-provider tier map + `judge_model_for` | T1 |
| `app/services/agent.py` | add `operation` param to `run_phase` (thread to the 6 usage sites) | T2 |
| `app/services/phase_judge.py` (new) | `Verdict`/`Failure` models, meta-prompt builder, `judge()`, failure serialization, graceful degradation | T3 |
| `app/services/pipeline.py` · `app/services/phase_validator.py` | wire judge→regen→re-judge into `_execute_phase`; retire the rule engine | T4 |
| acceptance + worklog | real broken/correct/plausible smokes | T5 |

---

## Task 1: `model_tiers.py` — cross-provider tier-up judge selection

**Files:**
- Create: `app/services/model_tiers.py`
- Test: `tests/services/test_model_tiers.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_model_tiers.py
from app.services.agent_models import MODEL_MANIFEST
from app.services import model_tiers as mt


def test_every_manifest_model_has_a_tier():
    for provider, models in MODEL_MANIFEST.items():
        for model in models:
            t = mt.tier_of(provider, model)
            assert t in (1, 2, 3, 4), f"{provider}/{model} -> {t}"


def test_none_model_resolves_to_provider_default_tier():
    # gemini default is gemini-3.1-pro-preview (T1); codex default gpt-5.5 (T1)
    assert mt.tier_of("gemini", None) == mt.tier_of("gemini", "gemini-3.1-pro-preview")
    assert mt.tier_of("codex", None) == mt.tier_of("codex", "gpt-5.5")


def test_judge_is_one_tier_up_and_never_claude():
    # claude-sonnet-4-6 is T2 -> judge T1, and never claude
    jp, jm = mt.judge_model_for("claude", "claude-sonnet-4-6")
    assert jp != "claude"
    assert mt.tier_of(jp, jm) == 1


def test_top_tier_generator_judged_by_non_self_peer():
    # claude-opus-4-7 is T1 -> judged by a T1 peer, never itself, never claude
    jp, jm = mt.judge_model_for("claude", "claude-opus-4-7")
    assert (jp, jm) != ("claude", "claude-opus-4-7")
    assert jp != "claude"
    assert mt.tier_of(jp, jm) == 1


def test_collision_falls_back_to_alternate():
    # gemini-3.1-pro-preview is the T1 primary designate; a generator that IS the
    # designate must be judged by the alternate, not itself.
    jp, jm = mt.judge_model_for("gemini", "gemini-3.1-pro-preview")
    assert (jp, jm) != ("gemini", "gemini-3.1-pro-preview")


def test_light_generator_jumps_to_mid():
    # gpt-5-nano is T4 -> judge T3
    jp, jm = mt.judge_model_for("codex", "gpt-5-nano")
    assert mt.tier_of(jp, jm) == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_model_tiers.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.model_tiers`.

- [ ] **Step 3: Implement**

```python
# app/services/model_tiers.py
"""Cross-provider capability tiers + tier-up judge selection.

Pure, no I/O. A validation judge must be at least as capable as the model that
produced the output, so we pick the judge from the tier ABOVE the generator's.
Designates are intentionally NON-CLAUDE so validation never draws down the
scarce claude Max pool (mirrors the resilience effort's provider isolation).
Tier placement is a judgment call over partly-future models — kept here as data
so it re-tunes without touching logic.
"""

from __future__ import annotations

from typing import Optional

from app.services.agent_models import default_model

# 1 = strongest. Every MODEL_MANIFEST model appears exactly once.
_MODEL_TIER: dict[str, int] = {
    # Tier 1 — Frontier
    "claude-opus-4-7": 1,
    "gpt-5.5": 1,
    "gemini-3.1-pro-preview": 1,
    # Tier 2 — Strong
    "claude-sonnet-4-6": 2,
    "gpt-5.2": 2,
    "gpt-5": 2,
    "gemini-3-flash-preview": 2,
    "gemini-2.5-pro": 2,
    # Tier 3 — Mid
    "claude-haiku-4-5-20251001": 3,
    "gpt-5-mini": 3,
    "gemini-3.1-flash-lite-preview": 3,
    "gemini-2.5-flash": 3,
    "kimi-code/kimi-for-coding": 3,
    # Tier 4 — Light
    "gpt-5-nano": 4,
    "gemini-2.5-flash-lite": 4,
    "opencode/deepseek-v4-flash-free": 4,
    "opencode/nemotron-3-super-free": 4,
    "opencode/mimo-v2.5-free": 4,
    "opencode/big-pickle": 4,
}

# Unknown model (shouldn't happen — manifest is enforced at /generate) -> assume
# Strong, so the judge errs toward a Frontier check rather than under-grading.
_DEFAULT_TIER = 2

# Judge designate per JUDGE tier: (primary, alternate). Both non-claude. The
# alternate is used when the primary would equal the generating model (no-self).
_JUDGE_DESIGNATES: dict[int, tuple[tuple[str, str], tuple[str, str]]] = {
    1: (("gemini", "gemini-3.1-pro-preview"), ("codex", "gpt-5.5")),
    2: (("gemini", "gemini-2.5-pro"), ("codex", "gpt-5")),
    3: (("gemini", "gemini-2.5-flash"), ("codex", "gpt-5-mini")),
}


def tier_of(provider: str, model: Optional[str]) -> int:
    """Capability tier (1=strongest) of (provider, model). `model=None` resolves
    to the provider's default model first."""
    resolved = model or default_model(provider)
    return _MODEL_TIER.get(resolved or "", _DEFAULT_TIER)


def judge_model_for(gen_provider: str, gen_model: Optional[str]) -> tuple[str, str]:
    """Pick the judge (provider, model) one tier above the generator. Clamps at
    tier 1 (a top-tier generator gets a tier-1 peer). Falls back to the alternate
    designate if the primary would be the generating model itself (no-self)."""
    gen_tier = tier_of(gen_provider, gen_model)
    judge_tier = max(1, gen_tier - 1)
    primary, alternate = _JUDGE_DESIGNATES[judge_tier]
    resolved_gen = (gen_provider, gen_model or default_model(gen_provider))
    return alternate if primary == resolved_gen else primary
```

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_model_tiers.py -q`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/model_tiers.py tests/services/test_model_tiers.py
git commit -m "feat(validator): cross-provider tier-up judge selection (non-claude designates)"
```

---

## Task 2: `operation` parameter on `agent.run_phase`

The judge call must label its `agent_usages` row `judge:<phase>` instead of the hardcoded
`phase.run`. `run_phase` hardcodes `operation="phase.run"` at all six `_record_usage` sites
(`agent.py:642/659/683/714/748/783`) and has no `operation` param; `_record_usage` already
accepts `operation=`.

**Files:**
- Modify: `app/services/agent.py` (`run_phase` signature + the six `_record_usage` calls)
- Test: `tests/services/test_run_phase_operation.py` (new)

- [ ] **Step 1: Write the failing test** (DB-free — signature + source)

```python
# tests/services/test_run_phase_operation.py
import inspect

from app.services import agent


def test_run_phase_has_operation_param_defaulting_to_phase_run():
    sig = inspect.signature(agent.run_phase)
    assert "operation" in sig.parameters
    assert sig.parameters["operation"].default == "phase.run"


def test_run_phase_threads_operation_not_hardcoded():
    src = inspect.getsource(agent.run_phase)
    # No site still hardcodes the label; all six pass the param through.
    assert 'operation="phase.run"' not in src
    assert src.count("operation=operation") >= 6
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_run_phase_operation.py -q`
Expected: FAIL — `operation` not in signature; `operation="phase.run"` still present.

- [ ] **Step 3: Add the parameter**

In `app/services/agent.py`, add to the `run_phase` signature (after `source_map_digest: str = "",`):

```python
    operation: str = "phase.run",
```

Then replace **every** `operation="phase.run",` inside `run_phase` (all six `_record_usage`
calls) with:

```python
                operation=operation,
```

(Indentation must match each call site — they are nested at the same depth as the existing
`operation="phase.run",` lines. Do not touch `_record_usage` calls in other functions, e.g.
`extract_lesson_context` or `record_cached_lesson_extract`.)

- [ ] **Step 4: Run tests + import**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_run_phase_operation.py -q` and
`& ".\.venv\Scripts\python.exe" -c "import app.services.agent"`
Expected: 2 PASS; import clean.

- [ ] **Step 5: Run the full agent suite (no regression)**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_agent.py -q`
Expected: green (the default `operation="phase.run"` preserves all existing behaviour).

- [ ] **Step 6: Commit**

```bash
git add app/services/agent.py tests/services/test_run_phase_operation.py
git commit -m "feat(agent): run_phase operation param (enables judge:<phase> usage attribution)"
```

---

## Task 3: `phase_judge.py` — the self-verifying LLM judge

**Files:**
- Create: `app/services/phase_judge.py`
- Test: `tests/services/test_phase_judge.py` (new)

> The CLI call rides `agent.run_phase(schema=Verdict, operation="judge:<phase>", phase_name="__judge__", …)` — reusing its spawn, semaphore, envelope parse, `model_validate_json` + reparse, and usage recording. `"__judge__"` is NOT in `_SVG_PHASES` (which contains `case-based-preview`), so no SVG rules leak into the judge prompt. `judge()` itself does I/O, so it's verified by the pure helpers + the Task 5 smoke; the helpers carry the real assertions.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_phase_judge.py
import inspect

from app.services import phase_judge as pj


def test_verdict_models_shape():
    v = pj.Verdict(passed=False, failures=[pj.Failure(requirement="r", evidence="e")])
    assert v.passed is False
    assert v.failures[0].requirement == "r" and v.failures[0].evidence == "e"
    # failures defaults to empty
    assert pj.Verdict(passed=True).failures == []


def test_serialize_failures_to_strings():
    out = pj._serialize_failures(
        [pj.Failure(requirement="Exactly 3 checkpoints", evidence="found 4")]
    )
    assert out == ["Exactly 3 checkpoints — found 4"]


def test_build_judge_prompt_contains_contract_output_and_protocol():
    p = pj._build_judge_prompt(contract="CONTRACT-TEXT", output_md="OUTPUT-TEXT")
    assert "CONTRACT-TEXT" in p and "OUTPUT-TEXT" in p
    # cite-then-refute protocol present
    low = p.lower()
    assert "cite" in low or "quote" in low
    assert "refute" in low or "substantiate" in low or "cannot substantiate" in low
    # placeholder rule present (compliant vs invented URL)
    assert "placeholder" in low
    # no SVG-rules noise marker
    assert "VISUAL / SVG RULES" not in p


def test_build_feedback_lists_failures():
    fb = pj._build_feedback(["A — x", "B — y"])
    assert "A — x" in fb and "B — y" in fb


def test_judge_uses_run_phase_with_judge_operation_and_neutral_phase():
    src = inspect.getsource(pj.judge)
    assert "judge_model_for" in src                 # tier-up selection
    assert "schema=Verdict" in src                  # structured verdict
    assert 'operation=f"judge:' in src or "operation=f'judge:" in src
    assert '"__judge__"' in src or "'__judge__'" in src   # neutral phase_name
    assert "get_prompt(" in src                     # reads the resolved contract


def test_judge_is_async_and_returns_outcome_type():
    assert inspect.iscoroutinefunction(pj.judge)
    # graceful degradation path is present
    src = inspect.getsource(pj.judge)
    assert "judge-unavailable" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_phase_judge.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.phase_judge`.

- [ ] **Step 3: Implement**

```python
# app/services/phase_judge.py
"""Self-verifying LLM phase validator.

`judge()` grades a generated phase against its own prompt contract
(`get_prompt(subject, phase)`), seeing exactly the generator's inputs
(contract + lesson_context + declared prior_outputs + the output under review).
A single CLI call lists contract violations — each citing the exact offending
text — then refutes its own list, dropping anything it cannot substantiate, so a
hallucinated failure never triggers a needless regeneration. The judge model is
one capability tier above the ACTUAL producer (`model_tiers`). On any CLI/parse
error the judge degrades to "unavailable" and never blocks generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from loguru import logger
from pydantic import BaseModel

from app.services import agent, model_tiers
from app.services.prompts import get_prompt


class Failure(BaseModel):
    requirement: str   # the contract rule the output violates
    evidence: str      # the exact quote (or quoted absence) proving it


class Verdict(BaseModel):
    passed: bool
    failures: list[Failure] = []


@dataclass
class JudgeOutcome:
    available: bool         # False = judge CLI/parse failed (degraded)
    passed: bool            # meaningful only when available
    warnings: list[str]     # serialized failures, OR ["judge-unavailable: …"]
    feedback: str           # regen prompt addendum (empty when passed/unavailable)


_INSTRUCTIONS = (
    "You are a strict reviewer validating a generated homework phase against the "
    "authoring instructions it was produced from. You do not rewrite it; you only "
    "judge compliance.\n\n"
    "Do this in ONE response:\n"
    "1. List every requirement in the CONTRACT that the OUTPUT violates. For each, "
    "quote the EXACT offending text from the OUTPUT (or the exact missing element, "
    "naming where the CONTRACT requires it). No vague 'feels off'.\n"
    "2. Then challenge your own list: for each candidate, confirm it is genuinely "
    "violated by the quoted evidence. DROP any item you cannot substantiate with a "
    "direct citation — treat anything you cannot quote as your own hallucination.\n"
    "3. Output ONLY the survivors.\n\n"
    "Visual rule (do NOT over-flag): the CONTRACT tells the generator to emit "
    "`![placeholder: … — image gen required](placeholder)` for any raster/photo "
    "instead of creating one. A correctly-emitted placeholder is COMPLIANT — never "
    "raise a 'missing image / incomplete visual' failure over it. But a fabricated "
    "image or an invented http(s) image URL IS a violation — the contract forbids it."
)


def _build_judge_prompt(*, contract: str, output_md: str) -> str:
    return (
        f"{_INSTRUCTIONS}\n\n"
        "## CONTRACT (the authoring instructions the output must satisfy)\n"
        f"{contract.strip()}\n\n"
        "## OUTPUT UNDER REVIEW\n"
        f"{output_md.strip()}\n"
    )


def _serialize_failures(failures: list[Failure]) -> list[str]:
    return [f"{f.requirement} — {f.evidence}" for f in failures]


def _build_feedback(warnings: list[str]) -> str:
    bullets = "\n".join(f"- {w}" for w in warnings)
    return (
        "\n\n## Fix these (a reviewer rejected your previous attempt)\n"
        "Your previous output violated these contract requirements. Correct ALL of "
        "them and regenerate the full deliverable:\n"
        f"{bullets}"
    )


async def judge(
    *,
    subject: str,
    phase_name: str,
    output_md: str,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    gen_provider: str,
    gen_model: Optional[str],
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
) -> JudgeOutcome:
    """Grade `output_md` against its phase contract. Returns a JudgeOutcome;
    never raises (CLI/parse failure -> degraded 'unavailable')."""
    judge_provider, judge_model = model_tiers.judge_model_for(gen_provider, gen_model)
    contract = get_prompt(subject, phase_name)
    judge_prompt = _build_judge_prompt(contract=contract, output_md=output_md)

    try:
        result = await agent.run_phase(
            provider=judge_provider,
            model=judge_model,
            phase_prompt=judge_prompt,
            phase_name="__judge__",          # NOT in _SVG_PHASES -> no SVG noise
            schema=Verdict,
            lesson_context=lesson_context,
            prior_outputs=prior_outputs,
            difficulty=None,
            operation=f"judge:{phase_name}",
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
        )
        verdict = result.parsed
        assert isinstance(verdict, Verdict)
    except Exception as exc:  # noqa: BLE001 — judge must never block generation
        logger.warning(f"phase_judge unavailable for {phase_name}: {exc!r}")
        return JudgeOutcome(
            available=False, passed=True,
            warnings=[f"judge-unavailable: {type(exc).__name__}"], feedback="",
        )

    if verdict.passed or not verdict.failures:
        return JudgeOutcome(available=True, passed=True, warnings=[], feedback="")

    warnings = _serialize_failures(verdict.failures)
    return JudgeOutcome(
        available=True, passed=False, warnings=warnings,
        feedback=_build_feedback(warnings),
    )
```

- [ ] **Step 4: Run tests + import**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_phase_judge.py -q` and
`& ".\.venv\Scripts\python.exe" -c "import app.services.phase_judge"`
Expected: 6 PASS; import clean.

- [ ] **Step 5: Commit**

```bash
git add app/services/phase_judge.py tests/services/test_phase_judge.py
git commit -m "feat(validator): self-verifying LLM phase judge (prompt-derived, cite-then-refute)"
```

---

## Task 4: Wire the judge into `_execute_phase` + retire the rule engine

**Files:**
- Modify: `app/services/pipeline.py` (`_execute_phase` — replace the `phase_validator.validate(...)` block `:659-664`; restructure the non-extract `_run` into a regen-capable factory `:626-647`)
- Delete: `app/services/phase_validator.py`
- Delete: `tests/services/test_phase_validator.py`
- Test: `tests/services/test_execute_phase_judge.py` (new)

> The judge keys off the **actual producer**: `produced_by` + (`model if produced_by==provider else None`). The regen reuses `_run_with_failover` keyed `requested_provider=produced_by`, with the feedback appended to the phase prompt. The final `set_status` keeps `provider=produced_by` (now the regen's producer if a regen happened) and writes the surviving warnings.

- [ ] **Step 1: Write the failing test** (DB-free — source assertions)

```python
# tests/services/test_execute_phase_judge.py
import inspect

from app.services import pipeline


def test_execute_phase_invokes_the_judge():
    src = inspect.getsource(pipeline._execute_phase)
    assert "phase_judge.judge" in src
    # keyed off the actual producer, not the requested provider
    assert "produced_by" in src
    # regen path reuses the failover driver a second time
    assert src.count("_run_with_failover") >= 2
    # the regen is GUARDED — an exhausted regen must not fail the job
    assert "regen failed" in src


def test_execute_phase_no_longer_calls_deterministic_validator():
    src = inspect.getsource(pipeline._execute_phase)
    assert "phase_validator" not in src


def test_phase_validator_module_is_gone():
    import importlib
    try:
        importlib.import_module("app.services.phase_validator")
        raised = False
    except ModuleNotFoundError:
        raised = True
    assert raised, "phase_validator should be retired"
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_execute_phase_judge.py -q`
Expected: FAIL — `phase_judge.judge` not in source; `phase_validator` still imports.

- [ ] **Step 3: Add the import + remove the old one**

In `app/services/pipeline.py`, change the services import line (note: it includes
`failure_classifier`, added by the shipped resilience work — **keep it**, only swap
`phase_validator → phase_judge`):

```python
from app.services import agent, events_bus, failure_classifier, notion_archive, phase_validator
```

to

```python
from app.services import agent, events_bus, failure_classifier, notion_archive, phase_judge
```

(Dropping `failure_classifier` here would NameError in `_run_with_failover`, which calls
`failure_classifier.classify(...)`.)

- [ ] **Step 4: Make the non-extract generation regen-capable**

In `_execute_phase`, replace the non-extract `else` branch (currently `:626-648`):

```python
        else:
            phase_prompt = get_prompt(subject, phase_name)

            async def _run(prov: str, mdl: Optional[str]):
                return await agent.run_phase_prompt(
                    provider=prov,
                    model=mdl,
                    phase_prompt=phase_prompt,
                    attachments=[pdf_path] if attach_file else [],
                    lesson_context=lesson_context or "",
                    prior_outputs=prior_outputs,
                    difficulty=difficulty,
                    phase_name=phase_name,
                    max_output_tokens=max_output_tokens_for(phase_name),
                    homework_job_id=job_id,
                    phase_output_id=po_id,
                    source_map_digest=source_map_digest,
                )

            output_md, tin, tout, produced_by = await _run_with_failover(
                requested_provider=provider, model=model, run_fn=_run,
            )
            parsed_struct = None
```

with a factory that lets the regen reuse the same generation path with an augmented prompt:

```python
        else:
            base_phase_prompt = get_prompt(subject, phase_name)

            def _make_run(prompt_text: str):
                async def _run(prov: str, mdl: Optional[str]):
                    return await agent.run_phase_prompt(
                        provider=prov,
                        model=mdl,
                        phase_prompt=prompt_text,
                        attachments=[pdf_path] if attach_file else [],
                        lesson_context=lesson_context or "",
                        prior_outputs=prior_outputs,
                        difficulty=difficulty,
                        phase_name=phase_name,
                        max_output_tokens=max_output_tokens_for(phase_name),
                        homework_job_id=job_id,
                        phase_output_id=po_id,
                        source_map_digest=source_map_digest,
                    )
                return _run

            output_md, tin, tout, produced_by = await _run_with_failover(
                requested_provider=provider, model=model, run_fn=_make_run(base_phase_prompt),
            )
            parsed_struct = None
```

- [ ] **Step 5: Replace the validator block with judge → regen → re-judge**

Replace the current block (`:659-664`):

```python
    warnings = (
        phase_validator.validate(phase_name, output_md, subject=subject)
        if phase_name != "extract" else []
    )
    if warnings:
        logger.warning(f"[job {job_id}] {phase_name} validation warnings: {warnings}")
```

with:

```python
    warnings: list[str] = []
    if phase_name != "extract":
        # Judge against the phase's own contract, keyed off the ACTUAL producer
        # (produced_by + its resolved model). One regen with cited failures fed
        # back; still failing -> accept with warnings. Never blocks the job.
        def _gen_model_of(prod: str) -> Optional[str]:
            # After failover, the fallback ran on model=None (provider default), so
            # tier selection uses the provider's DEFAULT model — errs toward a
            # stronger judge (safe per "judge >= generator"), not the CLI's exact
            # default. Approximate-but-safe; do not mistake it for exact.
            return model if prod == provider else None

        outcome = await phase_judge.judge(
            subject=subject, phase_name=phase_name, output_md=output_md,
            lesson_context=lesson_context, prior_outputs=prior_outputs,
            gen_provider=produced_by, gen_model=_gen_model_of(produced_by),
            homework_job_id=job_id, phase_output_id=po_id,
        )
        if outcome.available and not outcome.passed:
            logger.info(
                f"[job {job_id}] {phase_name} judge rejected "
                f"({len(outcome.warnings)} issue(s)) — regenerating once"
            )
            # The regen runs through the failover driver, which CAN exhaust all
            # providers and raise. This block is OUTSIDE the generation try/except
            # (which marks the phase failed at :649-657), so an unguarded raise
            # here would fail the whole job — violating "validation never fails a
            # job". Guard it: on regen failure keep the judge-rejected-but-complete
            # original output + its warnings and proceed to `done`.
            try:
                regen_prompt = base_phase_prompt + outcome.feedback
                r_md, r_tin, r_tout, r_prod = await _run_with_failover(
                    requested_provider=produced_by,
                    model=_gen_model_of(produced_by),
                    run_fn=_make_run(regen_prompt),
                )
                # Commit to the regenerated output only after it actually succeeded.
                output_md, tin, tout, produced_by = r_md, r_tin, r_tout, r_prod
                outcome = await phase_judge.judge(
                    subject=subject, phase_name=phase_name, output_md=output_md,
                    lesson_context=lesson_context, prior_outputs=prior_outputs,
                    gen_provider=produced_by, gen_model=_gen_model_of(produced_by),
                    homework_job_id=job_id, phase_output_id=po_id,
                )
            except Exception as exc:  # noqa: BLE001 — validation must NEVER fail a job
                logger.warning(
                    f"[job {job_id}] {phase_name} regen failed ({exc!r}); "
                    f"keeping the judge-rejected original output + warnings"
                )
                # output_md/tin/tout/produced_by and `outcome` retain their original
                # pre-regen values — the phase still completes `done` with warnings.
        warnings = outcome.warnings
        if warnings:
            logger.warning(f"[job {job_id}] {phase_name} validation warnings: {warnings}")
```

(The final `set_status` at `:665-675` is unchanged — it already writes
`validation_warnings=warnings or None, provider=produced_by`, and both now reflect any regen.)

- [ ] **Step 6: Retire the rule engine + its tests**

```bash
git rm app/services/phase_validator.py tests/services/test_phase_validator.py
```

(Do NOT touch `tests/services/test_prompt_coverage.py` or the prompt global-guard test — they are
unrelated to the validator and must stay green.)

- [ ] **Step 7: Run tests + import + full suite**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_execute_phase_judge.py -q`,
then `& ".\.venv\Scripts\python.exe" -c "import app.services.pipeline"`,
then `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q`.
Expected: new tests PASS; import clean; full suite green except any pre-existing known red
(`test_notion_defaults_disabled` is the documented unrelated red). No `phase_validator` import errors.

- [ ] **Step 8: Commit**

```bash
git add app/services/pipeline.py tests/services/test_execute_phase_judge.py
git commit -m "feat(pipeline): wire self-verifying LLM judge into _execute_phase; retire deterministic validator"
```

---

## Task 5: Acceptance smoke + worklog

**No code.** Generation-affecting behaviour is proven by real runs (CLAUDE.md gate). Requires the
CLIs installed on PATH and a section to generate.

- [ ] **Step 1: Suites green**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q`
Expected: green except the documented pre-existing red.

- [ ] **Step 2: "Catches a real violation" smoke**

In a Python REPL / scratch script (in-process, no server), build a deliberately broken phase
output (e.g. a `case-based-preview` markdown with **4** checkpoints) and call
`phase_judge.judge(subject="biology", phase_name="case-based-preview", output_md=<broken>,
lesson_context=<short real extract>, prior_outputs={}, gen_provider="claude",
gen_model="claude-sonnet-4-6")`. Confirm:
- `outcome.available is True`, `outcome.passed is False`;
- at least one `warnings` entry names the checkpoint-count rule **and** quotes evidence;
- the judge model used was a tier-1 non-claude model (check the `agent_usages` row labelled
  `judge:case-based-preview`).

- [ ] **Step 3: "Passes a correct output" smoke**

Generate a real `case-based-preview` (or reuse a known-good one), call `judge(...)`. Confirm
`outcome.passed is True` and `outcome.warnings == []` — no regen.

- [ ] **Step 4: "Does NOT hallucinate" smoke (the anti-hallucination proof — mandatory)**

Take a correct, contract-satisfying output that is stylistically unusual but compliant (e.g. a
valid CBP whose narrative is terse). Call `judge(...)`. Confirm the judge does **not** invent a
failure — `outcome.passed is True`. If it flags something, inspect the `evidence`: it must be a
real, quotable violation; if not, the refutation step needs prompt tuning before sign-off.

- [ ] **Step 5: End-to-end regen smoke**

Run a real single-section generation (`POST /generate` or the pipeline directly) on a cheap
provider. Confirm in logs/DB: a phase that the judge rejects shows the "regenerating once" log,
a second generation occurs, and the phase ends `done` with either no warnings or the surviving
ones recorded in `phase_outputs.validation_warnings` (as `list[str]`, rendering fine in the
console).

- [ ] **Step 6: Worklog**

Add a worklog entry to `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md`: the
validator is now a single self-verifying LLM judge (prompt-derived, tier-up, retry-then-warn);
note the `run_phase` `operation` param, that R11 stays open/separate, and the judge-keys-off-
`produced_by` integration with the shipped failover.

---

## Self-review

**Spec coverage:** single LLM judge, no deterministic layer (T3 + T4 retire `phase_validator`) ✓ · prompt-derived via `get_prompt` (T3 `judge`) ✓ · judge sees generator's exact inputs — contract + lesson_context + declared prior_outputs + output (T3 + T4 pass `prior_outputs`/`lesson_context`) ✓ · tier-up, non-claude designates, no-self (T1) ✓ · single-call cite-then-refute (T3 `_INSTRUCTIONS`) ✓ · retry-then-warn, non-blocking, extract-exempt (T4 `if phase_name != "extract"`) ✓ · graceful degradation (T3 `judge-unavailable`) ✓ · **regen failure guarded** — keeps the original output + warnings, never fails the job (T4 Step 5 try/except, asserted by the wiring test) ✓ · `operation="judge:<phase>"` attribution (T2 + T3) ✓ · `list[str]` storage (T3 `_serialize_failures` → existing `validation_warnings`) ✓ · placeholder-compliant / invented-URL-violation rule (T3 `_INSTRUCTIONS`) ✓ · live resilience integration: key off `produced_by`, regen via `_run_with_failover` (T4) ✓ · R11 untouched (T4 keys off in-memory producer) ✓ · neutral `phase_name` avoids `_SVG_PHASES`/`case-based-preview` (T3) ✓.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; the LLM-behaviour steps (T3 `judge`, T5) are verified by pure-helper tests + `inspect.getsource` + the real CLI smoke (DB-free harness, per CLAUDE.md).

**Type consistency:** `judge_model_for(gen_provider, gen_model) -> tuple[str,str]` matches T1 tests and the T3 call site. `Verdict(passed: bool, failures: list[Failure])` / `Failure(requirement, evidence)` match T3 tests and `_serialize_failures`. `JudgeOutcome(available, passed, warnings, feedback)` matches the T4 wiring (`outcome.available`, `outcome.passed`, `outcome.warnings`, `outcome.feedback`). `judge(...)` keyword args (`subject, phase_name, output_md, lesson_context, prior_outputs, gen_provider, gen_model, homework_job_id, phase_output_id`) are identical in T3 def and both T4 call sites. `_make_run(prompt_text)` / `base_phase_prompt` names are consistent across T4 Steps 4–5. `run_phase(..., operation=...)` (T2) matches the T3 call.
