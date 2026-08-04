"""Unit tests for the committed operator ingest CLI.

No real HTTP calls: dry-run (the default) never touches ``_post``, and the
one test that reaches ``_post`` monkeypatches it out — nothing here opens a
socket. The token rules below are load-bearing: the platform's
``LIBRARY_INGEST_TOKEN`` is a comma-separated ACCEPTANCE LIST server-side, but
the server compares the ENTIRE presented Bearer value against each entry
(``presented = parts[1]``, then ``hmac.compare_digest``) — so a comma-joined
value never matches any single entry. The server also does
``parts = header.split()`` and requires ``len(parts) == 2``, so a token
containing whitespace also fails to authenticate. ``validate_token`` rejects
all of these before any HTTP request is attempted.
"""
from __future__ import annotations

import pytest

from scripts import ingest_to_platform as cli


def _md_payload(phases=None):
    """A legacy markdown-only envelope in the serializer's shape."""
    return {
        "source": "hcg", "source_ref": "b1", "language": "uz",
        "subject_id": 7, "grade": 8, "external_key": "j1",
        "payload": {"phases": phases if phases is not None else []},
    }


def _async_job(job, phases):
    """Async stub matching the real `_load_job` signature.

    The production path is `asyncio.run(_load_job(jid))` — a single shape. Tests
    match it instead of making production branch on `iscoroutine`, which would be
    test-shaped code in a shipping script.
    """
    async def _stub(jid):
        return job, phases
    return _stub



@pytest.mark.parametrize("bad", ["", "   ", "old,new", "tok en", "tok\ten", "a\nb"])

def test_validate_token_rejects_multi_blank_and_whitespace(bad):
    with pytest.raises(cli.TokenError):
        cli.validate_token(bad)


def test_validate_token_accepts_single_token():
    assert cli.validate_token("  abc123  ") == "abc123"


def test_dry_run_does_not_post(monkeypatch, capsys):
    def _explode(*a, **k):
        raise AssertionError("dry-run must not POST")

    monkeypatch.setattr(cli, "_post", _explode)
    monkeypatch.setattr(cli, "_load_job", _async_job({}, []))
    monkeypatch.setattr(cli, "_load_map", lambda: {"history": 7})
    monkeypatch.setenv("PLATFORM_BASE_URL", "https://example.test")
    monkeypatch.setenv("PLATFORM_INGEST_TOKEN", "tok")
    monkeypatch.setattr(cli, "build_ingest_payload", lambda **k: _md_payload())

    assert cli.main(["--job", "abc"]) == 0


def test_check_map_prints_and_exits_without_touching_base_url_or_token(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_map", lambda: {"math": 3})
    monkeypatch.delenv("PLATFORM_BASE_URL", raising=False)
    monkeypatch.delenv("PLATFORM_INGEST_TOKEN", raising=False)

    assert cli.main(["--check-map"]) == 0
    out = capsys.readouterr().out
    assert '"math": 3' in out


def test_main_raises_when_base_url_missing(monkeypatch):
    monkeypatch.setattr(cli, "_load_map", lambda: {"math": 3})
    monkeypatch.delenv("PLATFORM_BASE_URL", raising=False)

    with pytest.raises(cli.TokenError):
        cli.main([])


def test_main_raises_when_token_missing(monkeypatch):
    monkeypatch.setattr(cli, "_load_map", lambda: {"math": 3})
    monkeypatch.setenv("PLATFORM_BASE_URL", "https://example.test")
    monkeypatch.delenv("PLATFORM_INGEST_TOKEN", raising=False)

    with pytest.raises(cli.TokenError):
        cli.main([])


def test_post_flag_calls_post_and_never_opens_a_real_socket(monkeypatch):
    calls = []

    def _fake_post(base, token, payload, client=None):
        calls.append((base, token, payload))
        return 0

    monkeypatch.setattr(cli, "_post", _fake_post)
    monkeypatch.setattr(cli, "_load_job", _async_job({"id": "j"}, []))
    monkeypatch.setattr(cli, "_load_map", lambda: {"history": 7})
    monkeypatch.setattr(cli, "build_ingest_payload", lambda **k: _md_payload())
    monkeypatch.setenv("PLATFORM_BASE_URL", "https://example.test")
    monkeypatch.setenv("PLATFORM_INGEST_TOKEN", "tok")

    rc = cli.main(["--job", "abc", "--post"])

    assert rc == 0
    assert calls == [("https://example.test", "tok", _md_payload())]
