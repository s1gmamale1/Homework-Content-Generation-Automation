"""Unit tests for the per-CLI ``Provider`` subclasses.

Each provider has two pure entry points: ``build_argv`` (assembles the
subprocess argv) and ``parse_envelope`` (turns raw stdout + the optional
last-msg sentinel file into a ``(text, usage)`` tuple). Both are tested
without any real subprocess spawn.

Realistic fixtures are inlined as module-level constants so they're
easy to read alongside the assertions; copy-pasted from sample envelopes
in the autopilot reference implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.providers import Claude, Codex, Gemini, Kimi


# ─────────────────────────────────────────────────────────────────────
# Fixtures (realistic stdout samples per provider)
# ─────────────────────────────────────────────────────────────────────


CLAUDE_STDOUT = json.dumps(
    {
        "type": "result",
        "result": (
            "Here is the lesson summary.\n\n"
            "## Key concepts\n"
            "- mitosis is a stage of cell division"
        ),
        "usage": {
            "input_tokens": 1234,
            "cache_read_input_tokens": 890,
            "output_tokens": 567,
            "cache_creation_input_tokens": 0,
        },
        "is_error": False,
        "stop_reason": "end_turn",
    }
)


CLAUDE_STDOUT_IS_ERROR = json.dumps(
    {
        "type": "result",
        "result": "I hit a tool error but here's a partial answer.",
        "usage": {
            "input_tokens": 100,
            "cache_read_input_tokens": 0,
            "output_tokens": 25,
            "cache_creation_input_tokens": 0,
        },
        "is_error": True,
        "stop_reason": "tool_use_error",
    }
)


GEMINI_STDOUT_WITH_PREAMBLE = (
    "[WARN] gemini-cli: cache miss\n"
    "[WARN] retry attempt 1\n"
    + json.dumps(
        {
            "response": "Here is the lesson summary.",
            "stats": {
                "models": {
                    "gemini-2.5-flash": {
                        "tokens": {
                            "input": 1234,
                            "candidates": 567,
                            "cached": 890,
                            "total": 2691,
                        }
                    }
                }
            },
        }
    )
)


# Codex emits JSONL events on stdout; the assistant text lives in the
# last-msg file passed via -o. This sample contains a turn.completed event
# whose ``payload.tokens`` carries usage.
CODEX_STDOUT_JSONL = "\n".join(
    [
        json.dumps({"type": "thread.started", "payload": {}}),
        json.dumps(
            {
                "type": "turn.completed",
                "payload": {
                    "tokens": {"input": 1234, "output": 567, "cached": 890},
                },
            }
        ),
    ]
)
CODEX_LAST_MSG_TEXT = "Here is the lesson summary."


KIMI_STDOUT_STREAM_JSON = "\n".join(
    [
        json.dumps(
            {"type": "system", "subtype": "init", "data": {"session_id": "abc"}}
        ),
        json.dumps(
            {
                "type": "assistant",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Here is the lesson summary."}
                ],
            }
        ),
        json.dumps({"type": "result", "subtype": "success"}),
    ]
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


USAGE_KEYS = {
    "prompt_tokens",
    "output_tokens",
    "cached_tokens",
    "total_tokens",
    "raw",
}


def _sentinel(tmp_path: Path, body: str | None = None) -> Path:
    """Create a fresh last-msg sentinel file. ``body=None`` leaves it absent
    so we exercise the ``not last_msg_path.is_file()`` branch."""
    p = tmp_path / "last_msg.txt"
    if body is not None:
        p.write_text(body, encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────────
# Claude
# ─────────────────────────────────────────────────────────────────────


def test_claude_build_argv_no_model(tmp_path: Path) -> None:
    """When ``model=None``, ``--model`` must not appear in argv."""
    argv = Claude().build_argv(
        binary="/usr/bin/claude",
        model=None,
        last_msg_path=_sentinel(tmp_path),
        attachments=[],
    )
    assert argv[0] == "/usr/bin/claude"
    assert "--model" not in argv


def test_claude_build_argv_with_model(tmp_path: Path) -> None:
    argv = Claude().build_argv(
        binary="/usr/bin/claude",
        model="some-model-x",
        last_msg_path=_sentinel(tmp_path),
        attachments=[],
    )
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "some-model-x"


def test_claude_build_argv_attaches_at_paths(tmp_path: Path) -> None:
    """Claude consumes attachments as positional ``@<path>`` tokens.

    ``Path()`` normalizes separators per-OS (``\\`` on Windows), so we
    assert against the platform-rendered form rather than a hard-coded
    POSIX string.
    """
    attach = Path("/tmp/a.pdf")
    argv = Claude().build_argv(
        binary="/usr/bin/claude",
        model=None,
        last_msg_path=_sentinel(tmp_path),
        attachments=[attach],
    )
    assert f"@{attach}" in argv


def test_claude_parse_envelope_happy(tmp_path: Path) -> None:
    text, usage = Claude().parse_envelope(
        CLAUDE_STDOUT,
        last_msg_path=_sentinel(tmp_path),
    )
    assert isinstance(text, str)
    assert isinstance(usage, dict)
    assert text.startswith("Here is the lesson summary.")
    assert "mitosis" in text
    # Normalized keys
    assert set(usage.keys()) == USAGE_KEYS
    assert usage["prompt_tokens"] == 1234
    assert usage["output_tokens"] == 567
    assert usage["cached_tokens"] == 890
    # Total = sum of all four input variants + output (cache_creation = 0).
    assert usage["total_tokens"] == 1234 + 890 + 567 + 0
    # Raw envelope passed through verbatim — spot check a Claude-only field.
    assert usage["raw"]["stop_reason"] == "end_turn"
    assert usage["raw"]["is_error"] is False


def test_claude_parse_envelope_is_error_still_parses(tmp_path: Path) -> None:
    """``is_error=True`` doesn't blow up the parser; the driver decides."""
    text, usage = Claude().parse_envelope(
        CLAUDE_STDOUT_IS_ERROR,
        last_msg_path=_sentinel(tmp_path),
    )
    assert "partial answer" in text
    assert usage["raw"]["is_error"] is True
    assert usage["prompt_tokens"] == 100
    assert usage["output_tokens"] == 25


def test_claude_prompt_suffix_empty() -> None:
    assert Claude().prompt_suffix(None) == ""


def test_claude_format_attachments_empty(tmp_path: Path) -> None:
    """Claude consumes attachments via positional ``@<path>`` argv tokens
    (see :func:`test_claude_build_argv_attaches_at_paths`); no prompt-level
    preamble is needed."""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")
    assert Claude().format_attachments([pdf]) == ""
    assert Claude().format_attachments([]) == ""


# ─────────────────────────────────────────────────────────────────────
# Kimi
# ─────────────────────────────────────────────────────────────────────


def test_kimi_build_argv_no_model(tmp_path: Path) -> None:
    argv = Kimi().build_argv(
        binary="/usr/bin/kimi",
        model=None,
        last_msg_path=_sentinel(tmp_path),
        attachments=[],
    )
    assert argv[0] == "/usr/bin/kimi"
    assert "--model" not in argv
    assert "stream-json" in argv


def test_kimi_build_argv_with_model(tmp_path: Path) -> None:
    argv = Kimi().build_argv(
        binary="/usr/bin/kimi",
        model="some-model-x",
        last_msg_path=_sentinel(tmp_path),
        attachments=[],
    )
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "some-model-x"


def test_kimi_build_argv_widens_workspace_for_attachments(tmp_path: Path) -> None:
    """Kimi sandboxes file reads to its workspace. ``build_argv`` must inject
    ``--add-dir <parent>`` for each attachment so the ``ReadFile`` / ``Shell``
    tools are allowed to access them. Attachments are NOT passed as positional
    ``@<path>`` tokens (Kimi doesn't parse those)."""
    attach = tmp_path / "a.pdf"
    attach.write_bytes(b"%PDF-stub")
    argv = Kimi().build_argv(
        binary="/usr/bin/kimi",
        model=None,
        last_msg_path=_sentinel(tmp_path),
        attachments=[attach],
    )
    assert f"@{attach}" not in argv
    # Parent dir of the attachment must be on the workspace allow-list.
    assert "--add-dir" in argv
    assert str(attach.parent.resolve()) in argv


def test_kimi_format_attachments_pdf_uses_shell(tmp_path: Path) -> None:
    """PDF attachments must be steered toward the Shell tool + pdfplumber
    (``ReadFile`` returns binary garbage on PDFs)."""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")
    preamble = Kimi().format_attachments([pdf])
    assert str(pdf.resolve()) in preamble
    assert "Shell" in preamble
    assert "pdfplumber" in preamble


def test_kimi_format_attachments_text_uses_readfile(tmp_path: Path) -> None:
    """Text files should be steered toward the ReadFile tool."""
    txt = tmp_path / "notes.txt"
    txt.write_text("hi", encoding="utf-8")
    preamble = Kimi().format_attachments([txt])
    assert str(txt.resolve()) in preamble
    assert "ReadFile" in preamble


def test_kimi_format_attachments_empty() -> None:
    assert Kimi().format_attachments([]) == ""


def test_kimi_parse_envelope_happy(tmp_path: Path) -> None:
    """Multi-line JSONL: parser pulls the assistant text from the assistant
    message and ignores the system/result framing."""
    text, usage = Kimi().parse_envelope(
        KIMI_STDOUT_STREAM_JSON,
        last_msg_path=_sentinel(tmp_path),
    )
    assert text == "Here is the lesson summary."
    assert set(usage.keys()) == USAGE_KEYS
    # Kimi 1.30 doesn't surface tokens; everything is zero.
    assert usage["prompt_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["cached_tokens"] == 0
    assert usage["total_tokens"] == 0
    # The raw envelope captures every JSONL line verbatim.
    assert "events" in usage["raw"]
    types_seen = [e.get("type") for e in usage["raw"]["events"]]
    assert "system" in types_seen
    assert "result" in types_seen


def test_kimi_prompt_suffix_nonempty() -> None:
    suffix = Kimi().prompt_suffix(None)
    assert isinstance(suffix, str)
    assert len(suffix) > 0


# ─────────────────────────────────────────────────────────────────────
# Codex
# ─────────────────────────────────────────────────────────────────────


def test_codex_build_argv_no_model(tmp_path: Path) -> None:
    last = _sentinel(tmp_path)
    argv = Codex().build_argv(
        binary="/usr/bin/codex",
        model=None,
        last_msg_path=last,
        attachments=[],
    )
    assert argv[0] == "/usr/bin/codex"
    assert "--model" not in argv
    # last-msg path must be wired through ``-o``.
    assert "-o" in argv
    assert str(last) in argv


def test_codex_build_argv_with_model(tmp_path: Path) -> None:
    argv = Codex().build_argv(
        binary="/usr/bin/codex",
        model="some-model-x",
        last_msg_path=_sentinel(tmp_path),
        attachments=[],
    )
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "some-model-x"


def test_codex_build_argv_widens_workspace_for_attachments(tmp_path: Path) -> None:
    """Codex's read-only sandbox restricts file access to the cwd + any
    explicit ``--add-dir`` entries. ``build_argv`` must inject ``--add-dir
    <parent>`` per attachment so the shell tool can read them."""
    attach = tmp_path / "a.pdf"
    attach.write_bytes(b"%PDF-stub")
    argv = Codex().build_argv(
        binary="/usr/bin/codex",
        model=None,
        last_msg_path=_sentinel(tmp_path),
        attachments=[attach],
    )
    assert f"@{attach}" not in argv
    assert "--add-dir" in argv
    assert str(attach.parent.resolve()) in argv


def test_codex_format_attachments_mentions_path_and_shell(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")
    preamble = Codex().format_attachments([pdf])
    assert str(pdf.resolve()) in preamble
    # Codex has shell tools — the preamble must direct it to use them.
    assert "shell" in preamble.lower()


def test_codex_format_attachments_empty() -> None:
    assert Codex().format_attachments([]) == ""


def test_codex_parse_envelope_reads_last_msg_file(tmp_path: Path) -> None:
    """``stdout`` is JSONL with no assistant text. Truth lives in the
    last-msg file written by Codex via ``-o``."""
    last = _sentinel(tmp_path, body=CODEX_LAST_MSG_TEXT)
    text, usage = Codex().parse_envelope(
        CODEX_STDOUT_JSONL,
        last_msg_path=last,
    )
    assert text == CODEX_LAST_MSG_TEXT
    assert set(usage.keys()) == USAGE_KEYS
    assert usage["prompt_tokens"] == 1234
    assert usage["output_tokens"] == 567
    assert usage["cached_tokens"] == 890
    assert usage["total_tokens"] == 1234 + 567 + 890
    # Spot-check provider-specific structure: events list + parsed usage dict.
    assert "events" in usage["raw"]
    assert any(
        e.get("type") == "turn.completed" for e in usage["raw"]["events"]
    )
    assert usage["raw"]["usage"] == {
        "input": 1234,
        "output": 567,
        "cached": 890,
    }


def test_codex_prompt_suffix_nonempty() -> None:
    suffix = Codex().prompt_suffix(None)
    assert isinstance(suffix, str)
    assert len(suffix) > 0


# ─────────────────────────────────────────────────────────────────────
# Gemini
# ─────────────────────────────────────────────────────────────────────


def test_gemini_build_argv_no_model(tmp_path: Path) -> None:
    argv = Gemini().build_argv(
        binary="/usr/bin/gemini",
        model=None,
        last_msg_path=_sentinel(tmp_path),
        attachments=[],
    )
    assert argv[0] == "/usr/bin/gemini"
    assert "--model" not in argv


def test_gemini_build_argv_with_model(tmp_path: Path) -> None:
    argv = Gemini().build_argv(
        binary="/usr/bin/gemini",
        model="some-model-x",
        last_msg_path=_sentinel(tmp_path),
        attachments=[],
    )
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "some-model-x"


def test_gemini_build_argv_widens_workspace_for_attachments(tmp_path: Path) -> None:
    """Gemini sandboxes ``@<path>`` references to the workspace + project
    temp dir. ``build_argv`` must widen the sandbox via
    ``--include-directories <parent>`` per attachment so absolute paths
    resolve. Attachments are NOT positional argv tokens (Gemini doesn't
    parse those)."""
    attach = tmp_path / "a.pdf"
    attach.write_bytes(b"%PDF-stub")
    argv = Gemini().build_argv(
        binary="/usr/bin/gemini",
        model=None,
        last_msg_path=_sentinel(tmp_path),
        attachments=[attach],
    )
    assert f"@{attach}" not in argv
    assert "--include-directories" in argv
    assert str(attach.parent.resolve()) in argv


def test_gemini_build_argv_dedupes_parent_directories(tmp_path: Path) -> None:
    """Two attachments under the same parent must produce only one
    ``--include-directories`` entry — argv stays compact."""
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"%PDF-stub")
    b.write_bytes(b"%PDF-stub")
    argv = Gemini().build_argv(
        binary="/usr/bin/gemini",
        model=None,
        last_msg_path=_sentinel(tmp_path),
        attachments=[a, b],
    )
    parent = str(tmp_path.resolve())
    # Exactly one occurrence of the parent in argv (not two).
    assert argv.count(parent) == 1
    assert argv.count("--include-directories") == 1


def test_gemini_format_attachments_uses_at_path(tmp_path: Path) -> None:
    """Gemini reads files via ``@<absolute-path>`` references in the prompt."""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-stub")
    preamble = Gemini().format_attachments([pdf])
    assert f"@{pdf.resolve()}" in preamble


def test_gemini_format_attachments_empty() -> None:
    assert Gemini().format_attachments([]) == ""


def test_gemini_parse_envelope_skips_warning_preamble(tmp_path: Path) -> None:
    """Gemini prints WARN lines before the JSON envelope. The parser must
    locate the first ``{`` and parse from there."""
    text, usage = Gemini().parse_envelope(
        GEMINI_STDOUT_WITH_PREAMBLE,
        last_msg_path=_sentinel(tmp_path),
    )
    assert text == "Here is the lesson summary."
    assert set(usage.keys()) == USAGE_KEYS
    assert usage["prompt_tokens"] == 1234
    assert usage["output_tokens"] == 567
    assert usage["cached_tokens"] == 890
    # Gemini provides ``total`` directly; make sure we used it.
    assert usage["total_tokens"] == 2691
    # Spot-check provider-specific shape: ``stats.models.<id>.tokens``.
    raw = usage["raw"]
    assert "stats" in raw
    assert "models" in raw["stats"]


def test_gemini_prompt_suffix_empty() -> None:
    assert Gemini().prompt_suffix(None) == ""
