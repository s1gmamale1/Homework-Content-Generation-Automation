"""Unit tests for ``app.services.agent``.

Coverage:
- ``_resolve_model`` regression guard (Gemini default must be ``None``;
  no provider's default may leak into another's resolution path).
- ``STRUCTURED_PHASE_SCHEMAS`` shape (key set + values are pydantic models).
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
    STRUCTURED_PHASE_SCHEMAS,
    _PROVIDER_DEFAULT_MODEL,
    _resolve_model,
    extract_toc,
    run_phase,
)


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


def test_resolve_model_claude_default_is_pinned() -> None:
    assert _resolve_model("claude", None) == "claude-sonnet-4-6"


def test_resolve_model_explicit_overrides_default_gemini() -> None:
    assert _resolve_model("gemini", "gemini-3.1-pro") == "gemini-3.1-pro"


def test_resolve_model_explicit_overrides_default_claude() -> None:
    assert _resolve_model("claude", "claude-opus-4-7") == "claude-opus-4-7"


def test_provider_default_model_table_keys() -> None:
    """The dict must register exactly the four supported providers; an
    accidental rename / drop would break ``run_phase`` silently."""
    assert set(_PROVIDER_DEFAULT_MODEL.keys()) == {
        "claude", "kimi", "codex", "gemini",
    }


# ─────────────────────────────────────────────────────────────────────
# STRUCTURED_PHASE_SCHEMAS
# ─────────────────────────────────────────────────────────────────────


def test_structured_phase_schemas_keys_present() -> None:
    """Every phase that emits structured JSON must be registered. Asserting
    on the key set (not exact values) keeps this loose enough that adding
    a new phase doesn't fail this test."""
    expected = {
        "classify",
        "flashcards",
        "memory-sprint",
        "game-breaks",
        "final-challenge",
        "reading",
    }
    assert expected.issubset(set(STRUCTURED_PHASE_SCHEMAS.keys()))


def test_structured_phase_schemas_values_are_pydantic_models() -> None:
    for phase, schema in STRUCTURED_PHASE_SCHEMAS.items():
        assert isinstance(schema, type), f"{phase} → {schema!r} is not a class"
        assert issubclass(schema, BaseModel), (
            f"{phase} → {schema.__name__} is not a pydantic BaseModel subclass"
        )


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
