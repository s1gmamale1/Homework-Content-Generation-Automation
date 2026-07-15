"""Unit tests for the openai api-only provider stub
(``app.services.providers.openai_api``).

``openai`` is api-only — there is no CLI. The stub exists purely so
``get_provider("openai")`` resolves (name lookup happens before ``_spawn``
picks the api vs. cli branch) and so ``run_phase``'s prompt composition
(``prov.prompt_suffix`` / ``prov.format_attachments``, called BEFORE
transport dispatch) doesn't blow up. ``build_argv``/``parse_envelope`` are
CLI-only surface — unreachable in practice because ``binary_names=()``
makes ``_resolve_binary`` raise ``FileNotFoundError`` first (agent.py:256-259)
and validation blocks cli-transport openai jobs before that point — but they
still raise loudly (never silently no-op) if ever called directly, tested
here on the stub itself.
"""

from __future__ import annotations

import pathlib

import pytest

from app.services.providers import get_provider
from app.services.providers.openai_api import OpenAiApi


def test_registry_resolves_openai() -> None:
    prov = get_provider("openai")
    assert prov.name == "openai"
    assert isinstance(prov, OpenAiApi)


def test_binary_names_is_empty_tuple() -> None:
    # No CLI exists for this provider — _resolve_binary must fail fast.
    assert OpenAiApi().binary_names == ()


def test_build_argv_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="openai is api-only"):
        OpenAiApi().build_argv(
            binary="openai", model=None, last_msg_path=pathlib.Path("x"),
        )


def test_parse_envelope_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="openai is api-only"):
        OpenAiApi().parse_envelope("stdout", last_msg_path=pathlib.Path("x"))


def test_format_attachments_is_empty() -> None:
    assert OpenAiApi().format_attachments([pathlib.Path("a.pdf")]) == ""
    assert OpenAiApi().format_attachments([]) == ""


def test_prompt_suffix_is_empty() -> None:
    assert OpenAiApi().prompt_suffix(None) == ""
