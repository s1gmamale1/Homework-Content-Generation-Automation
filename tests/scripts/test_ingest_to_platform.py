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


_RLC = ("practice-rlc", "rlc_config@1")
_SENT = ("practice-sentence", "sentence_fill_config@1")


def _structured_payload():
    return _md_payload([
        {"phase_name": ph, "authoring_mode": "structured",
         "content_schema_version": ver, "output_md": "# x"}
        for ph, ver in (_RLC, _SENT)
    ] + [
        {"phase_name": "flashcards", "authoring_mode": "markdown_builtin",
         "content_schema_version": None, "output_md": "# f"},
    ])


class _FakeResponse:
    def __init__(self, status_code=200, body=None, text="", raises=False):
        self.status_code = status_code
        self._body = body
        self.text = text
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not JSON")
        return self._body


class _FakeClient:
    """Records probe calls; never opens a socket."""

    def __init__(self, response):
        self.response = response
        self.get_calls: list[str] = []

    def get(self, url, headers=None):
        self.get_calls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _caps(*pairs):
    return _FakeResponse(200, {"structured_phases": [
        {"phase_name": ph, "content_schema_version": ver} for ph, ver in pairs
    ]})


def _wire(monkeypatch, payload, post_calls, probe_response=None):
    """Wire main() so nothing touches the DB or a socket.

    `httpx.Client` itself is replaced, so the REAL `_fetch_capabilities` runs
    (status-code / malformed-JSON handling included) against a fake transport.
    """
    def _fake_post(base, token, p, client=None):
        post_calls.append(p)
        return 0

    client = _FakeClient(probe_response)
    monkeypatch.setattr(cli.httpx, "Client", lambda *a, **k: client)
    monkeypatch.setattr(cli, "_post", _fake_post)
    monkeypatch.setattr(cli, "_load_job", _async_job({"id": "j"}, []))
    monkeypatch.setattr(cli, "_load_map", lambda: {"history": 7})
    monkeypatch.setattr(cli, "build_ingest_payload", lambda **k: payload)
    monkeypatch.setenv("PLATFORM_BASE_URL", "https://example.test")
    monkeypatch.setenv("PLATFORM_INGEST_TOKEN", "tok")
    return client


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


# --- Fix 3: --post fails closed on structured phases -------------------------
# The ingest endpoint schedules transformation IMMEDIATELY, and the platform's
# current markdown parsers downgrade practice-rlc and DROP practice-sentence.
# So a structured post is refused unless the platform advertises native support
# for every (phase_name, content_schema_version) present. There is NO --force.


def test_structured_post_blocked_when_capability_endpoint_is_absent(monkeypatch, capsys):
    """Today's reality: the endpoint 404s, so every structured post is blocked."""
    posts: list = []
    client = _wire(monkeypatch, _structured_payload(), posts,
                   _FakeResponse(404, text="not found"))

    rc = cli.main(["--job", "abc", "--post"])

    assert rc != 0
    assert posts == []                       # nothing was POSTed
    assert client.get_calls == [f"https://example.test{cli.CAPABILITIES_PATH}"]
    err = capsys.readouterr().err
    assert "practice-rlc (rlc_config@1)" in err
    assert "practice-sentence (sentence_fill_config@1)" in err


def test_structured_post_blocked_when_capabilities_cover_only_one_pair(monkeypatch, capsys):
    posts: list = []
    _wire(monkeypatch, _structured_payload(), posts, _caps(_RLC))

    rc = cli.main(["--job", "abc", "--post"])

    assert rc != 0 and posts == []
    err = capsys.readouterr().err
    listed = [ln.strip() for ln in err.splitlines() if ln.strip().startswith("- ")]
    # Only the UNSUPPORTED pair is listed; practice-rlc is advertised as native.
    assert listed == ["- practice-sentence (sentence_fill_config@1)"]


@pytest.mark.parametrize("probe", [
    _FakeResponse(500, text="boom"),                       # non-200
    _FakeResponse(200, raises=True),                       # malformed body
    _FakeResponse(200, {"structured_phases": "yes"}),      # wrong shape
    _FakeResponse(200, {}),                                # incomplete
    _FakeResponse(200, ["practice-rlc"]),                  # not an object
    RuntimeError("connection refused"),                    # unreachable
])
def test_structured_post_blocked_on_any_unclear_capability_answer(monkeypatch, probe):
    posts: list = []
    _wire(monkeypatch, _structured_payload(), posts, probe)

    assert cli.main(["--job", "abc", "--post"]) != 0
    assert posts == []


def test_structured_post_allowed_when_every_pair_is_supported(monkeypatch):
    posts: list = []
    payload = _structured_payload()
    _wire(monkeypatch, payload, posts, _caps(_RLC, _SENT))

    assert cli.main(["--job", "abc", "--post"]) == 0
    assert posts == [payload]


def test_markdown_only_post_never_probes_and_posts_normally(monkeypatch):
    posts: list = []
    payload = _md_payload([
        {"phase_name": "flashcards", "authoring_mode": "markdown_builtin",
         "content_schema_version": None, "output_md": "# f"},
    ])
    client = _wire(monkeypatch, payload, posts,
                   _FakeResponse(404))   # would block if it were consulted

    assert cli.main(["--job", "abc", "--post"]) == 0
    assert posts == [payload]
    assert client.get_calls == []


def test_dry_run_never_probes_or_posts_even_for_structured(monkeypatch):
    posts: list = []
    client = _wire(monkeypatch, _structured_payload(), posts, _FakeResponse(404))

    assert cli.main(["--job", "abc"]) == 0
    assert posts == [] and client.get_calls == []


def test_check_map_never_probes_or_posts(monkeypatch):
    posts: list = []
    client = _wire(monkeypatch, _structured_payload(), posts, _FakeResponse(404))

    assert cli.main(["--check-map"]) == 0
    assert posts == [] and client.get_calls == []


def test_there_is_no_force_bypass(monkeypatch, capsys):
    """A --force flag is exactly the mechanism that turns 'we know this drops a
    phase' into 'we shipped a packet missing a phase'. It must not exist: argparse
    rejects the unknown option and SystemExit(2) fires before any probe or POST."""
    posts: list = []
    client = _wire(monkeypatch, _structured_payload(), posts, _FakeResponse(404))

    with pytest.raises(SystemExit) as exc:
        cli.main(["--job", "abc", "--post", "--force"])

    assert exc.value.code == 2
    assert posts == [] and client.get_calls == []


def test_supported_pairs_ignores_partial_entries():
    caps = {"structured_phases": [
        {"phase_name": "practice-rlc", "content_schema_version": "rlc_config@1"},
        {"phase_name": "practice-sentence"},          # no version
        {"content_schema_version": "x@1"},            # no phase
        {"phase_name": "", "content_schema_version": "x@1"},
        "practice-tictactoe",
    ]}
    assert cli.supported_pairs(caps) == {_RLC}
    assert cli.supported_pairs(None) == set()
