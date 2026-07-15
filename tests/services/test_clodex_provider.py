"""Unit tests for the Clodex API-only provider stub."""

from __future__ import annotations

import pathlib

import pytest

from app.services.providers import get_provider
from app.services.providers.clodex import Clodex


def test_registry_resolves_clodex() -> None:
    prov = get_provider("clodex")
    assert prov.name == "clodex"
    assert isinstance(prov, Clodex)


def test_binary_names_is_empty_tuple() -> None:
    assert Clodex().binary_names == ()


def test_cli_methods_raise_runtime_error() -> None:
    provider = Clodex()
    with pytest.raises(RuntimeError, match="clodex is api-only"):
        provider.build_argv(binary="clodex", model=None, last_msg_path=pathlib.Path("x"))
    with pytest.raises(RuntimeError, match="clodex is api-only"):
        provider.parse_envelope("stdout", last_msg_path=pathlib.Path("x"))


def test_prompt_helpers_are_empty() -> None:
    provider = Clodex()
    assert provider.format_attachments([pathlib.Path("a.pdf")]) == ""
    assert provider.prompt_suffix(None) == ""
