"""Unit tests for the opencode CLI provider (``app.services.providers.opencode``).

opencode (https://opencode.ai/) is invoked as ``opencode run --format json
[-m provider/model] [-f file ...]`` with the prompt on stdin, and emits a JSONL
event stream. The provider is pure (argv builder + envelope parser), so these
tests use stdout fixtures — no subprocess needed.
"""

from __future__ import annotations

import pathlib

from app.services.agent import _PROVIDER_DEFAULT_MODEL, _resolve_model, _stdin_and_argv
from app.services.agent_models import MODEL_MANIFEST
from app.services.providers import get_provider
from app.services.providers.claude import Claude
from app.services.providers.opencode import OpenCode


def test_build_argv_base_and_model() -> None:
    argv = OpenCode().build_argv(
        binary="opencode.cmd", model="opencode/deepseek-v4-flash-free",
        last_msg_path=pathlib.Path("x"),
    )
    assert argv[:4] == ["opencode.cmd", "run", "--format", "json"]
    assert "-m" in argv and "opencode/deepseek-v4-flash-free" in argv


def test_build_argv_omits_model_flag_when_none() -> None:
    argv = OpenCode().build_argv(
        binary="opencode", model=None, last_msg_path=pathlib.Path("x")
    )
    assert "-m" not in argv


def test_build_argv_passes_attachments_with_f_flag(tmp_path: pathlib.Path) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    argv = OpenCode().build_argv(
        binary="opencode", model=None, last_msg_path=pathlib.Path("x"),
        attachments=[pdf],
    )
    assert "-f" in argv
    assert str(pdf.resolve()) in argv


def test_format_attachments_is_empty() -> None:
    # Files go via -f argv tokens, like Claude's positional approach.
    assert OpenCode().format_attachments([pathlib.Path("a.pdf")]) == ""


def test_parse_envelope_concatenates_text_and_reads_tokens() -> None:
    stdout = "\n".join([
        '{"type":"step_start","part":{}}',
        '{"type":"text","part":{"type":"text","text":"Hello "}}',
        '{"type":"text","part":{"type":"text","text":"world"}}',
        '{"type":"step_finish","part":{"tokens":{"input":10,"output":5,'
        '"total":15,"cache":{"read":3}}}}',
    ])
    text, usage = OpenCode().parse_envelope(stdout, last_msg_path=pathlib.Path("x"))
    assert text == "Hello world"
    assert usage["prompt_tokens"] == 10
    assert usage["output_tokens"] == 5
    assert usage["cached_tokens"] == 3
    assert usage["total_tokens"] == 15


def test_parse_envelope_ignores_non_json_banner_lines() -> None:
    stdout = "\n".join([
        "opencode v1.2 — migrating config…",
        '{"type":"text","part":{"type":"text","text":"answer"}}',
        '{"type":"step_finish","part":{"tokens":{"input":1,"output":1}}}',
    ])
    text, usage = OpenCode().parse_envelope(stdout, last_msg_path=pathlib.Path("x"))
    assert text == "answer"
    assert usage["total_tokens"] == 2  # derived from input+output when no total


def test_registered_in_providers_registry() -> None:
    prov = get_provider("opencode")
    assert prov.name == "opencode"


def test_in_model_manifest_with_free_default() -> None:
    assert "opencode" in MODEL_MANIFEST
    assert "opencode/deepseek-v4-flash-free" in MODEL_MANIFEST["opencode"]


def test_resolve_model_opencode_default_is_free_zen() -> None:
    # opencode REQUIRES a provider/model (can't run bare), so unlike
    # gemini/kimi/codex it has a non-None default — a free zen model.
    assert _resolve_model("opencode", None) == "opencode/deepseek-v4-flash-free"
    assert _PROVIDER_DEFAULT_MODEL["opencode"] == "opencode/deepseek-v4-flash-free"


def test_resolve_model_opencode_explicit_overrides() -> None:
    assert _resolve_model("opencode", "opencode/big-pickle") == "opencode/big-pickle"


def test_opencode_takes_prompt_positionally_not_stdin() -> None:
    # opencode run wants the prompt as a positional arg (per opencode.ai docs).
    assert OpenCode().prompt_on_stdin is False
    cmd, stdin = _stdin_and_argv(OpenCode(), ["opencode", "run", "--format", "json"], "hi there")
    assert cmd[-1] == "hi there"          # prompt appended as final argv token
    assert stdin == b""                    # nothing piped on stdin


def test_stdin_providers_keep_prompt_on_stdin() -> None:
    # claude (and the other 3) still pipe the prompt on stdin, argv untouched.
    assert Claude().prompt_on_stdin is True
    base = ["claude", "-p"]
    cmd, stdin = _stdin_and_argv(Claude(), base, "hello")
    assert cmd == base                     # argv unchanged
    assert stdin == b"hello"               # prompt on stdin
