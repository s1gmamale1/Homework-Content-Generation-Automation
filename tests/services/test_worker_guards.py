"""Unit tests for the worker's advisory gemini-settings startup guards.

Both guards read `~/.gemini/settings.json`; we point `Path.home()` at a tmp
dir. They must warn on the dangerous state, stay silent on the safe one, and
never raise on missing/garbage files.
"""

import json
import pathlib

from loguru import logger

from app.services import worker


def _set_home(monkeypatch, tmp_path: pathlib.Path, settings: dict | None) -> None:
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    if settings is not None:
        gem = tmp_path / ".gemini"
        gem.mkdir(exist_ok=True)
        (gem / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _capture_warnings(func) -> list[str]:
    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(m.record["message"]), level="WARNING")
    try:
        func()
    finally:
        logger.remove(sink_id)
    return records


def test_selected_type_guard_warns_when_persisted(monkeypatch, tmp_path):
    _set_home(monkeypatch, tmp_path, {"security": {"auth": {"selectedType": "oauth-personal"}}})
    msgs = _capture_warnings(worker._warn_if_gemini_selected_type)
    assert any("selectedType" in m for m in msgs)


def test_selected_type_guard_silent_when_absent(monkeypatch, tmp_path):
    _set_home(monkeypatch, tmp_path, {"security": {"auth": {}}})
    assert _capture_warnings(worker._warn_if_gemini_selected_type) == []


def test_local_env_guard_warns_when_setting_missing(monkeypatch, tmp_path):
    # The .env self-poisoning state: no advanced.ignoreLocalEnv.
    _set_home(monkeypatch, tmp_path, {"security": {"auth": {}}})
    msgs = _capture_warnings(worker._warn_if_gemini_reads_local_env)
    assert any("ignoreLocalEnv" in m for m in msgs)


def test_local_env_guard_silent_when_enabled(monkeypatch, tmp_path):
    _set_home(monkeypatch, tmp_path, {"advanced": {"ignoreLocalEnv": True}})
    assert _capture_warnings(worker._warn_if_gemini_reads_local_env) == []


def test_guards_never_raise_on_missing_or_garbage_file(monkeypatch, tmp_path):
    _set_home(monkeypatch, tmp_path, None)  # no settings.json at all
    worker._warn_if_gemini_selected_type()
    worker._warn_if_gemini_reads_local_env()
    gem = tmp_path / ".gemini"
    gem.mkdir()
    (gem / "settings.json").write_text("﻿{not json", encoding="utf-8")
    worker._warn_if_gemini_selected_type()
    worker._warn_if_gemini_reads_local_env()
