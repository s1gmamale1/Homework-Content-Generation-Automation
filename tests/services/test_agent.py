"""Unit tests for ``app.services.agent``.

Coverage:
- ``_resolve_model`` regression guard (Gemini default must be ``None``;
  no provider's default may leak into another's resolution path).
- The schema-validation retry path in ``run_phase``: the first attempt
  returns invalid JSON, the second returns valid JSON, and exactly two
  ``AgentUsage`` rows are persisted (success=False, then success=True).

Mocking strategy
----------------
We don't have a real test database wired up, so the schema-retry test
mocks at two layers:

1. ``app.services.agent._spawn`` — patched to return a queue of canned
   ``(rc, text, usage, stderr)`` tuples instead of spawning a subprocess.
2. ``app.services.agent._record_usage`` — patched to capture the kwargs
   of each call instead of opening a SQLAlchemy session.

Patching ``_record_usage`` is intentional: it's where every code path
serializes its outcome, so spying on it is equivalent to inspecting the
``agent_usages`` table without needing a Postgres fixture. If/when a
test DB fixture lands in ``tests/conftest.py`` we can swap to a
``usage_repo.create`` spy without changing assertions.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel

from app.services import agent as agent_module
from app.services.agent import (
    _PROVIDER_DEFAULT_MODEL,
    _is_rate_limited,
    _rate_limit_delay,
    _resolve_model,
    extract_toc,
    run_phase,
)
from app.config import settings


# ─────────────────────────────────────────────────────────────────────
# _resolve_model — regression guard
# ─────────────────────────────────────────────────────────────────────


def test_resolve_model_gemini_default_is_none() -> None:
    """Gemini must NOT inherit Claude's haiku default. Locked by the
    ``_PROVIDER_DEFAULT_MODEL`` table; this is the regression guard from
    the prior session where one provider's default leaked into another's."""
    assert _resolve_model("gemini", None) is None


def test_resolve_model_kimi_default_is_none() -> None:
    assert _resolve_model("kimi", None) is None


def test_resolve_model_codex_default_is_none() -> None:
    assert _resolve_model("codex", None) is None


def test_resolve_model_clodex_default_is_none() -> None:
    assert _resolve_model("clodex", None) is None


def test_resolve_model_claude_default_is_pinned() -> None:
    assert _resolve_model("claude", None) == "claude-sonnet-4-6"


def test_resolve_model_explicit_overrides_default_gemini() -> None:
    assert _resolve_model("gemini", "gemini-3.1-pro") == "gemini-3.1-pro"


def test_resolve_model_explicit_overrides_default_claude() -> None:
    assert _resolve_model("claude", "claude-opus-4-7") == "claude-opus-4-7"


def test_provider_default_model_table_keys() -> None:
    """The dict must register exactly the supported providers; an accidental
    rename / drop would break ``run_phase`` silently."""
    assert set(_PROVIDER_DEFAULT_MODEL.keys()) == {
        "claude", "kimi", "codex", "gemini", "opencode", "clodex",
    }


# ─────────────────────────────────────────────────────────────────────
# run_phase — schema validation retry path
# ─────────────────────────────────────────────────────────────────────


class _RetrySchema(BaseModel):
    """Tiny pydantic model used to drive ``run_phase``'s retry loop."""

    answer: str
    confidence: float


def _make_usage(
    *, prompt: int = 100, output: int = 50, cached: int = 0
) -> dict[str, Any]:
    return {
        "prompt_tokens": prompt,
        "output_tokens": output,
        "cached_tokens": cached,
        "total_tokens": prompt + output + cached,
        "raw": {"events": []},
    }


@pytest.mark.asyncio
async def test_run_phase_schema_retry_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First spawn returns invalid JSON → ``ValidationError`` → second
    spawn returns valid JSON. We assert:

    1. The retry prompt embeds the validation error.
    2. ``_record_usage`` is called twice — first with ``success=False``,
       then with ``success=True``.
    3. The returned ``PhaseResult.parsed`` is a ``_RetrySchema`` instance.
    """
    valid_json = json.dumps({"answer": "42", "confidence": 0.95})
    invalid_json = "{not even close to valid"

    spawn_outputs: list[tuple[int, str, dict[str, Any], str]] = [
        (0, invalid_json, _make_usage(prompt=120, output=10), ""),
        (0, valid_json, _make_usage(prompt=180, output=40), ""),
    ]
    spawn_prompts: list[str] = []

    async def fake_spawn(
        *,
        provider: Any,
        model: Any,
        prompt: str,
        attachments: list[Any],
        transport: str = "cli",
    ) -> tuple[int, str, dict[str, Any], str]:
        spawn_prompts.append(prompt)
        return spawn_outputs.pop(0)

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    result = await run_phase(
        provider="claude",
        model=None,
        phase_prompt="Compute the answer.",
        phase_name="retry-test",
        homework_job_id=None,
        phase_output_id=None,
        schema=_RetrySchema,
    )

    # Returned the validated parse, not None.
    assert isinstance(result.parsed, _RetrySchema)
    assert result.parsed.answer == "42"
    assert result.parsed.confidence == 0.95
    assert result.text == valid_json

    # Two spawn calls. Second prompt must include the validation error
    # appended after the base prompt.
    assert len(spawn_prompts) == 2
    assert "previous response failed schema validation" in spawn_prompts[1]
    assert "Respond with valid JSON matching the schema." in spawn_prompts[1]
    # First prompt must NOT include the retry suffix.
    assert "previous response failed schema validation" not in spawn_prompts[0]

    # Two AgentUsage rows: failed attempt then success.
    assert len(record_calls) == 2
    first, second = record_calls
    assert first["success"] is False
    assert first["operation"] == "phase.run"
    # The error message references schema validation.
    assert "schema validation failed" in (first["error_message"] or "")
    assert first["extra_envelope"]["attempt"] == 1
    assert first["extra_envelope"]["schema"] == "_RetrySchema"

    assert second["success"] is True
    assert second["operation"] == "phase.run"
    assert second["extra_envelope"]["attempt"] == 2
    assert second["extra_envelope"]["schema"] == "_RetrySchema"


@pytest.mark.asyncio
async def test_run_phase_schema_retry_exhausts_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both attempts return invalid JSON. ``run_phase`` must raise after
    the retry budget is used and emit two failure ``AgentUsage`` rows."""
    invalid_json = "{still not valid"

    spawn_outputs: list[tuple[int, str, dict[str, Any], str]] = [
        (0, invalid_json, _make_usage(), ""),
        (0, invalid_json, _make_usage(), ""),
    ]

    async def fake_spawn(**_kwargs: Any) -> tuple[int, str, dict[str, Any], str]:
        return spawn_outputs.pop(0)

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    with pytest.raises(RuntimeError, match="validation failed after"):
        await run_phase(
            provider="claude",
            model=None,
            phase_prompt="Compute the answer.",
            phase_name="retry-exhaust",
            homework_job_id=None,
            phase_output_id=None,
            schema=_RetrySchema,
        )

    # Two failure rows recorded before the raise.
    assert len(record_calls) == 2
    assert all(c["success"] is False for c in record_calls)
    assert record_calls[0]["extra_envelope"]["attempt"] == 1
    assert record_calls[1]["extra_envelope"]["attempt"] == 2


# ─────────────────────────────────────────────────────────────────────
# extract_toc — schema validation retry path
# ─────────────────────────────────────────────────────────────────────


BOOK_ID = UUID("00000000-0000-0000-0000-000000000123")


@pytest.mark.asyncio
async def test_extract_toc_schema_retry_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """TOC extraction has its own structured path. A prose/apology response
    should get one schema retry before the upload is marked failed."""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")
    valid_json = json.dumps(
        {
            "entries": [
                {
                    "chapter_number": "1",
                    "chapter_title": "Numbers",
                    "section_number": "1.1",
                    "section_title": "Counting",
                    "page_start": 7,
                    "page_end": 10,
                }
            ]
        }
    )
    spawn_outputs: list[tuple[int, str, dict[str, Any], str]] = [
        (0, "I cannot directly read this PDF.", _make_usage(), ""),
        (0, valid_json, _make_usage(prompt=140, output=30), ""),
    ]
    spawn_prompts: list[str] = []
    spawn_attachments: list[list[Any]] = []

    async def fake_spawn(
        *,
        provider: Any,
        model: Any,
        prompt: str,
        attachments: list[Any],
        transport: str = "cli",
    ) -> tuple[int, str, dict[str, Any], str]:
        spawn_prompts.append(prompt)
        spawn_attachments.append(attachments)
        return spawn_outputs.pop(0)

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)
    monkeypatch.setattr(
        agent_module,
        "_extract_toc_source_text",
        lambda _path: ("Contents\n1.1 Counting 7", {"chars": 24, "pages_read": 1}),
    )

    toc = await extract_toc(
        provider="claude",
        model=None,
        pdf_path=pdf,
        subject="math",
        book_id=BOOK_ID,
    )

    assert len(toc.entries) == 1
    assert toc.entries[0].section_title == "Counting"
    assert len(spawn_prompts) == 2
    assert "previous response failed schema validation" in spawn_prompts[1]
    assert spawn_attachments == [[], []]

    assert len(record_calls) == 2
    assert record_calls[0]["success"] is False
    assert record_calls[0]["operation"] == "toc.extract"
    assert record_calls[0]["extra_envelope"]["attempt"] == 1
    assert record_calls[0]["extra_envelope"]["source"] == "local_pdf_text"
    assert record_calls[1]["success"] is True
    assert record_calls[1]["extra_envelope"]["attempt"] == 2
    assert record_calls[1]["extra_envelope"]["entries"] == 1


@pytest.mark.asyncio
async def test_extract_toc_schema_retry_exhausts_and_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Both TOC attempts returning non-JSON should raise after two recorded
    failures, matching the normal structured phase behavior."""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")
    spawn_outputs: list[tuple[int, str, dict[str, Any], str]] = [
        (0, "I cannot directly read this PDF.", _make_usage(), ""),
        (0, "Still not JSON.", _make_usage(), ""),
    ]

    async def fake_spawn(**_kwargs: Any) -> tuple[int, str, dict[str, Any], str]:
        return spawn_outputs.pop(0)

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)
    monkeypatch.setattr(
        agent_module,
        "_extract_toc_source_text",
        lambda _path: ("Contents\n1.1 Counting 7", {"chars": 24, "pages_read": 1}),
    )

    with pytest.raises(RuntimeError, match="validation failed after 2 attempts"):
        await extract_toc(
            provider="claude",
            model=None,
            pdf_path=pdf,
            subject="math",
            book_id=BOOK_ID,
        )

    assert len(record_calls) == 2
    assert all(c["success"] is False for c in record_calls)
    assert record_calls[0]["extra_envelope"]["attempt"] == 1
    assert record_calls[1]["extra_envelope"]["attempt"] == 2


# ─────────────────────────────────────────────────────────────────────
# _should_subset_for_extract — R2 size gate
# ─────────────────────────────────────────────────────────────────────


def test_should_subset_for_extract_only_when_over_limit(tmp_path, monkeypatch):
    from app.services import agent
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 1000)  # ~1 KB
    # Under the real 20 MB limit → no subsetting (attach full PDF).
    assert agent._should_subset_for_extract(pdf) is False
    # Force a tiny limit → now it should subset.
    monkeypatch.setattr(agent, "_GEMINI_PDF_MAX_BYTES", 100)
    assert agent._should_subset_for_extract(pdf) is True
    # Missing file degrades to False (don't subset a file we can't stat).
    assert agent._should_subset_for_extract(tmp_path / "nope.pdf") is False


@pytest.mark.asyncio
async def test_spawn_api_gemini_delegates_to_sdk_inside_semaphore(monkeypatch):
    """transport=api + gemini routes to api_transport.generate, never spawns a
    subprocess, and holds the concurrency semaphore while doing so."""
    import asyncio as _asyncio
    from app.services import agent, api_transport

    held = {"sem": False}
    seen = {}

    # Pin a 1-slot semaphore: .locked() is True only when fully exhausted, so the
    # default Semaphore(8) would read False after acquiring 1 slot. With Semaphore(1)
    # the single in-use slot makes .locked() True while the SDK call is in flight.
    monkeypatch.setattr(agent, "_agent_semaphore", _asyncio.Semaphore(1))

    async def fake_generate(**kw):
        seen.update(kw)
        held["sem"] = agent._semaphore().locked()   # True iff the slot is held
        return (0, "SDK_SENTINEL", {"raw": {}}, "")

    monkeypatch.setattr(api_transport, "generate", fake_generate)

    def boom(*a, **k):
        raise AssertionError("api path must not spawn a subprocess")
    monkeypatch.setattr(_asyncio, "create_subprocess_exec", boom)

    rc, text, usage, err = await agent._spawn(
        provider=agent.get_provider("gemini"), model="gemini-2.5-flash",
        prompt="p", attachments=[], transport="api")

    assert text == "SDK_SENTINEL"
    assert seen["provider"] == "gemini" and seen["model"] == "gemini-2.5-flash"
    assert held["sem"] is True


@pytest.mark.asyncio
async def test_spawn_api_cli_only_provider_still_uses_cli(monkeypatch):
    """transport=api with a non-(gemini|claude) provider does NOT take the SDK
    branch — it falls through to the CLI path."""
    from app.services import agent, api_transport

    async def boom(**kw):
        raise AssertionError("kimi must not use the SDK path")
    monkeypatch.setattr(api_transport, "generate", boom)

    # Reaching _resolve_binary proves we left the SDK branch; stop there cheaply.
    monkeypatch.setattr(agent, "_resolve_binary",
                        lambda prov: (_ for _ in ()).throw(RuntimeError("reached-cli")))
    with pytest.raises(RuntimeError, match="reached-cli"):
        await agent._spawn(provider=agent.get_provider("kimi"), model=None,
                           prompt="p", attachments=[], transport="api")


@pytest.mark.asyncio
async def test_spawn_api_dispatch_reads_api_providers_membership(monkeypatch):
    """API dispatch must read membership from
    ``agent_models.API_PROVIDERS`` (not a hardcoded ("gemini", "claude")
    tuple), so Clodex routes to the SDK with no second hardcoded list."""
    from app.services import agent, agent_models, api_transport

    monkeypatch.setattr(
        agent_models, "API_PROVIDERS", frozenset({"claude", "gemini", "clodex"})
    )

    seen: dict[str, object] = {}

    async def fake_generate(**kw):
        seen.update(kw)
        return (0, "CLODEX_SENTINEL", {"raw": {}}, "")

    monkeypatch.setattr(api_transport, "generate", fake_generate)

    rc, text, usage, err = await agent._spawn(
        provider=agent.get_provider("clodex"), model="gpt-5.6-luna",
        prompt="p", attachments=[], transport="api")

    assert text == "CLODEX_SENTINEL"
    assert seen["provider"] == "clodex" and seen["model"] == "gpt-5.6-luna"


@pytest.mark.asyncio
async def test_spawn_api_dispatch_codex_kimi_still_use_cli_with_clodex_in_set(
    monkeypatch,
):
    """codex/kimi are NOT in API_PROVIDERS even after it grows to include
    clodex — they must still fall through to the CLI path, never the SDK."""
    from app.services import agent, agent_models, api_transport

    monkeypatch.setattr(
        agent_models, "API_PROVIDERS", frozenset({"claude", "gemini", "clodex"})
    )

    async def boom(**kw):
        raise AssertionError("codex/kimi must not use the SDK path")

    monkeypatch.setattr(api_transport, "generate", boom)
    monkeypatch.setattr(
        agent, "_resolve_binary",
        lambda prov: (_ for _ in ()).throw(RuntimeError("reached-cli")),
    )

    for name in ("codex", "kimi"):
        with pytest.raises(RuntimeError, match="reached-cli"):
            await agent._spawn(
                provider=agent.get_provider(name), model=None,
                prompt="p", attachments=[], transport="api",
            )


@pytest.mark.asyncio
async def test_run_phase_clodex_api_prompt_composes_no_suffix(monkeypatch):
    """Clodex format_attachments/prompt_suffix return "" (like claude/gemini,
    per Provider.base defaults) so run_phase's prompt composition — which
    calls them BEFORE transport dispatch — neither raises nor appends any
    provider visual-policy suffix for an API call."""
    captured: dict[str, object] = {}

    async def fake_spawn(*, provider, model, prompt, attachments, transport):
        captured["prompt"] = prompt
        captured["provider_name"] = provider.name
        return 0, "ok body", {
            "prompt_tokens": 1, "output_tokens": 1,
            "cached_tokens": 0, "total_tokens": 2, "raw": {},
        }, ""

    async def fake_record_usage(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record_usage)

    result = await run_phase(
        provider="clodex", model="gpt-5.6-luna", phase_prompt="p",
        phase_name="test", homework_job_id=None, phase_output_id=None,
        transport="api",
    )

    assert captured["provider_name"] == "clodex"
    assert "Visual policy" not in captured["prompt"]
    assert result.text == "ok body"


# ── api-error-capture-1 ────────────────────────────────────────────────


def test_spawn_failure_message_includes_real_error() -> None:
    """The failure string must carry the REAL error (api stderr = the 429/DNS/
    auth cause) and be transport-aware: 'api' for api, 'CLI' for cli."""
    api_msg = agent_module._spawn_failure_message(
        provider="gemini", transport="api", rc=1,
        stderr="429 RESOURCE_EXHAUSTED: quota", text="",
    )
    assert "429 RESOURCE_EXHAUSTED" in api_msg
    assert "api" in api_msg
    assert "CLI" not in api_msg

    cli_msg = agent_module._spawn_failure_message(
        provider="claude", transport="cli", rc=2, stderr="boom", text="",
    )
    assert "CLI" in cli_msg
    assert "boom" in cli_msg


@pytest.mark.asyncio
async def test_run_phase_api_failure_records_real_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An api (rc!=0) failure must land the REAL error string in both the
    recorded ``error_message`` and ``extra_envelope['error']`` (→ raw_envelope),
    not the old generic 'CLI exited rc=1'."""
    async def fake_spawn(
        *, provider: Any, model: Any, prompt: str,
        attachments: list[Any], transport: str = "cli",
    ) -> tuple[int, str, dict[str, Any], str]:
        return (1, "", _make_usage(prompt=0, output=0), "429 RESOURCE_EXHAUSTED: quota exceeded")

    record_calls: list[dict[str, Any]] = []

    async def fake_record(**kwargs: Any) -> None:
        record_calls.append(kwargs)

    monkeypatch.setattr(agent_module, "_spawn", fake_spawn)
    monkeypatch.setattr(agent_module, "_record_usage", fake_record)

    with pytest.raises(RuntimeError) as ei:
        await run_phase(
            provider="gemini", model="gemini-2.5-flash",
            phase_prompt="x", phase_name="p",
            homework_job_id=None, phase_output_id=None,
            transport="api",
        )

    assert "429 RESOURCE_EXHAUSTED" in str(ei.value)
    assert len(record_calls) == 1
    rec = record_calls[0]
    assert rec["success"] is False
    assert "429 RESOURCE_EXHAUSTED" in (rec["error_message"] or "")
    assert "api" in (rec["error_message"] or "")
    assert "CLI" not in (rec["error_message"] or "")
    assert "429 RESOURCE_EXHAUSTED" in rec["extra_envelope"]["error"]



# ─────────────────────────────────────────────────────────────────────
# Rate-limit detection + backoff schedule (concurrency-knob-1, Phase 1)
# ─────────────────────────────────────────────────────────────────────

_LIVE_VERTEX_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
    "'Resource exhausted. Please try again later.', 'status': "
    "'RESOURCE_EXHAUSTED'}}"
)


@pytest.mark.parametrize(
    "text",
    [
        _LIVE_VERTEX_429,
        "429 RESOURCE_EXHAUSTED",
        "Resource exhausted",
        "rate_limit_error",
        "overloaded_error",
        "Too Many Requests",
    ],
)
def test_is_rate_limited_true(text: str) -> None:
    """Retry ONLY on a genuine rate-limit — Vertex + anthropic shapes."""
    assert _is_rate_limited(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "401 UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "output truncated: finish_reason=MAX_TOKENS",
        "ModelNotFoundError",
    ],
)
def test_is_rate_limited_false(text: str) -> None:
    """Auth (401/403) and truncation never self-heal — must NOT retry."""
    assert _is_rate_limited(text) is False


def test_rate_limit_delay_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff is positive, non-decreasing, and capped at ``cap + base``.

    Pin the jitter to its max (2nd arg of ``random.uniform``) for determinism.
    """
    monkeypatch.setattr("random.uniform", lambda _a, b: b)
    base = settings.rate_limit_base_delay_seconds
    cap = settings.rate_limit_max_delay_seconds
    delays = [_rate_limit_delay(a) for a in range(5)]
    assert all(d > 0 for d in delays)
    assert delays == sorted(delays)  # non-decreasing
    assert all(d <= cap + base for d in delays)


# ─────────────────────────────────────────────────────────────────────
# _spawn retry loop (concurrency-knob-1, Phase 1) — mocked sleep
# ─────────────────────────────────────────────────────────────────────


class _StubProvider:
    """Minimal stand-in for a Provider — _spawn only reads ``.name``."""

    name = "gemini"


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace ``asyncio.sleep`` with a no-op that records each delay."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(agent_module.asyncio, "sleep", fake_sleep)
    return sleeps


@pytest.mark.asyncio
async def test_spawn_retries_on_rate_limit_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two 429s then success → _spawn returns the success tuple after 3
    attempts, with 2 backoff sleeps in between."""
    outputs: list[tuple[int, str, dict[str, Any], str]] = [
        (1, "", {"raw": {}}, "429 RESOURCE_EXHAUSTED"),
        (1, "", {"raw": {}}, "429 RESOURCE_EXHAUSTED"),
        (0, "ok", {"raw": {}}, ""),
    ]
    calls = {"n": 0}

    async def fake_once(**_kwargs: Any) -> tuple[int, str, dict[str, Any], str]:
        calls["n"] += 1
        return outputs.pop(0)

    monkeypatch.setattr(agent_module, "_spawn_once", fake_once)
    sleeps = _patch_sleep(monkeypatch)

    rc, text, _usage, stderr = await agent_module._spawn(
        provider=_StubProvider(), model="gemini-2.5-flash", prompt="x",
        attachments=[], transport="api",
    )

    assert (rc, text) == (0, "ok")
    assert calls["n"] == 3
    assert len(sleeps) == 2


@pytest.mark.asyncio
async def test_spawn_gives_up_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent 429 → _spawn returns the failure tuple after exactly
    ``max_retries + 1`` attempts (no infinite loop); sleeps == max_retries."""
    calls = {"n": 0}

    async def fake_once(**_kwargs: Any) -> tuple[int, str, dict[str, Any], str]:
        calls["n"] += 1
        return (1, "", {"raw": {}}, "429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(agent_module, "_spawn_once", fake_once)
    sleeps = _patch_sleep(monkeypatch)

    rc, _text, _usage, stderr = await agent_module._spawn(
        provider=_StubProvider(), model="gemini-2.5-flash", prompt="x",
        attachments=[], transport="api",
    )

    assert rc == 1
    assert "RESOURCE_EXHAUSTED" in stderr
    assert calls["n"] == settings.rate_limit_max_retries + 1
    assert len(sleeps) == settings.rate_limit_max_retries


@pytest.mark.asyncio
async def test_spawn_does_not_retry_non_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-rate-limit failure (401) returns immediately — no retry, no sleep."""
    calls = {"n": 0}

    async def fake_once(**_kwargs: Any) -> tuple[int, str, dict[str, Any], str]:
        calls["n"] += 1
        return (1, "", "", "401 UNAUTHENTICATED")

    monkeypatch.setattr(agent_module, "_spawn_once", fake_once)
    sleeps = _patch_sleep(monkeypatch)

    rc, _text, _usage, stderr = await agent_module._spawn(
        provider=_StubProvider(), model="gemini-2.5-flash", prompt="x",
        attachments=[], transport="api",
    )

    assert rc == 1
    assert "401" in stderr
    assert calls["n"] == 1
    assert sleeps == []


from app.services.agent import validate_extract_summary

_COMPACT_CONTRACT = (
    "Dars: algebraik kasrlar.\n\n"
    "## Concepts & terms\n- Algebraik kasr\n"
    "## Worked-example types\n- Ikki kasrni ko'paytirib qisqartirish\n"
)  # ~110 chars — below the OLD 400 floor, structurally valid

def test_gate_b_passes_compact_contract_below_old_floor():
    # regression: this is the §5 false-positive class — must PASS now
    assert validate_extract_summary(_COMPACT_CONTRACT) is None

def test_gate_b_fails_refusal_marker_regardless_of_contract():
    bad = "Manba fayli o'qib bo'lmadi.\n## Concepts & terms\n- x\n"
    assert validate_extract_summary(bad) is not None

def test_gate_b_fails_near_empty_no_contract():
    assert validate_extract_summary("ok") is not None

def test_gate_b_passes_unformatted_but_substantial_prose():
    prose = "Bu dars algebraik kasrlar haqida. " * 6  # >120c, no contract headers
    assert validate_extract_summary(prose) is None


from app.services.agent import _SUMMARIZE_LESSON_PROMPT, _SUMMARIZE_VISION_PROMPT

_REQUIRED_HEADERS = ["## Concepts", "## Rules", "## Formulas", "## Worked-example types", "## Key facts"]

def test_extract_prompts_specify_the_contract_headers():
    for p in (_SUMMARIZE_LESSON_PROMPT, _SUMMARIZE_VISION_PROMPT):
        for h in _REQUIRED_HEADERS:
            assert h in p, f"{h!r} missing from extract prompt"
        assert "worked-example" in p.lower()
        # headers stay English; items in the lesson language
        assert "lesson's language" in p.lower() or "same language" in p.lower()
