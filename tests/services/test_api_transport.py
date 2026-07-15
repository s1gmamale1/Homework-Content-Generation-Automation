"""api_transport — SDK generation for transport=api. SDK clients are stubbed via
the _gemini_client/_claude_client factory seams; no network."""
from pathlib import Path

import pytest

from app.config import settings
from app.services import api_transport


# ---- gemini fakes ----
class _FR:  # finish_reason enum stand-in (has .name)
    def __init__(self, name): self.name = name

class _Part:
    def __init__(self, text): self.text = text

class _Content:
    def __init__(self, parts): self.parts = parts

class _Cand:
    def __init__(self, parts, finish): self.content = _Content(parts); self.finish_reason = finish

class _UM:  # gemini usage_metadata
    def __init__(self, **kw): self.__dict__.update(kw)

class _GResp:
    def __init__(self, parts, finish, um):
        self.candidates = [_Cand(parts, finish)] if parts is not None else []
        self.usage_metadata = um

class _GModels:
    def __init__(self, resp=None, exc=None): self._resp, self._exc = resp, exc
    async def generate_content(self, *, model, contents):
        if self._exc: raise self._exc
        return self._resp

class _GClient:
    def __init__(self, resp=None, exc=None):
        self.aio = type("aio", (), {"models": _GModels(resp, exc)})()


@pytest.mark.asyncio
async def test_gemini_success_usage(monkeypatch):
    um = _UM(prompt_token_count=100, candidates_token_count=50, thoughts_token_count=20,
             cached_content_token_count=10, total_token_count=170)
    resp = _GResp([_Part("hello")], _FR("STOP"), um)
    monkeypatch.setattr(api_transport, "_gemini_client", lambda: _GClient(resp=resp))
    rc, text, usage, err = await api_transport.generate(
        provider="gemini", model="gemini-2.5-flash", prompt="x", attachments=[])
    assert (rc, text) == (0, "hello")
    assert usage["prompt_tokens"] == 100
    assert usage["output_tokens"] == 70          # candidates + thoughts
    assert usage["cached_tokens"] == 10
    assert usage["total_tokens"] == 170


@pytest.mark.asyncio
async def test_gemini_truncation_is_loud(monkeypatch):
    resp = _GResp([_Part("partial")], _FR("MAX_TOKENS"), None)
    monkeypatch.setattr(api_transport, "_gemini_client", lambda: _GClient(resp=resp))
    rc, text, usage, err = await api_transport.generate(
        provider="gemini", model="m", prompt="x", attachments=[])
    assert rc == 1 and text == "" and "truncated" in err


@pytest.mark.asyncio
async def test_gemini_blocked_empty_is_retryable(monkeypatch):
    resp = _GResp(parts=[], finish=_FR("SAFETY"), um=None)   # no usable text
    monkeypatch.setattr(api_transport, "_gemini_client", lambda: _GClient(resp=resp))
    rc, text, usage, err = await api_transport.generate(
        provider="gemini", model="m", prompt="x", attachments=[])
    assert rc == 0 and text == ""                            # -> run_phase empty-body retry


@pytest.mark.asyncio
async def test_gemini_sdk_exception_maps_to_rc1(monkeypatch):
    monkeypatch.setattr(api_transport, "_gemini_client",
                        lambda: _GClient(exc=RuntimeError("permission_denied: nope")))
    rc, text, usage, err = await api_transport.generate(
        provider="gemini", model="m", prompt="x", attachments=[])
    assert rc == 1 and text == "" and "permission_denied" in err
    assert usage["prompt_tokens"] is None                    # empty usage, no crash


@pytest.mark.asyncio
async def test_gemini_missing_usage_no_crash(monkeypatch):
    resp = _GResp([_Part("hi")], _FR("STOP"), um=None)       # usage_metadata None
    monkeypatch.setattr(api_transport, "_gemini_client", lambda: _GClient(resp=resp))
    rc, text, usage, err = await api_transport.generate(
        provider="gemini", model="m", prompt="x", attachments=[])
    assert rc == 0 and usage["total_tokens"] is None


def test_gemini_client_credentials(monkeypatch):
    import google.genai as genai
    seen = {}
    monkeypatch.setattr(genai, "Client", lambda **kw: seen.update(kw) or "client")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    api_transport._gemini_client(); assert seen == {"api_key": "k"}
    seen.clear()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/sa.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    api_transport._gemini_client()
    assert seen == {"vertexai": True, "project": "p", "location": "global"}
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(RuntimeError):
        api_transport._gemini_client()


# ---- claude fakes ----
class _Block:
    def __init__(self, text): self.type = "text"; self.text = text

class _CUsage:
    def __init__(self, **kw): self.__dict__.update(kw)

class _Msg:
    def __init__(self, blocks, stop, usage): self.content = blocks; self.stop_reason = stop; self.usage = usage

class _CMessages:
    last = None
    def __init__(self, msg=None, exc=None): self._msg, self._exc = msg, exc
    async def create(self, **kw):
        _CMessages.last = kw
        if self._exc: raise self._exc
        return self._msg

class _CClient:
    def __init__(self, msg=None, exc=None): self.messages = _CMessages(msg, exc)


@pytest.mark.asyncio
async def test_claude_success_total_includes_cache(monkeypatch):
    u = _CUsage(input_tokens=100, output_tokens=50,
                cache_read_input_tokens=10, cache_creation_input_tokens=5)
    msg = _Msg([_Block("hi")], "end_turn", u)
    monkeypatch.setattr(api_transport, "_claude_client", lambda: _CClient(msg=msg))
    rc, text, usage, err = await api_transport.generate(
        provider="claude", model="claude-opus-4-8", prompt="x", attachments=[])
    assert (rc, text) == (0, "hi")
    assert usage["prompt_tokens"] == 100 and usage["cached_tokens"] == 10
    assert usage["total_tokens"] == 165          # 100+50+10+5 (matches CLI provider)


@pytest.mark.asyncio
async def test_claude_truncation_is_loud(monkeypatch):
    msg = _Msg([_Block("partial...")], "max_tokens", None)
    monkeypatch.setattr(api_transport, "_claude_client", lambda: _CClient(msg=msg))
    rc, text, usage, err = await api_transport.generate(
        provider="claude", model="m", prompt="x", attachments=[])
    assert rc == 1 and text == "" and "truncated" in err


@pytest.mark.asyncio
async def test_claude_cap_passed(monkeypatch):
    msg = _Msg([_Block("ok")], "end_turn", None)
    monkeypatch.setattr(api_transport, "_claude_client", lambda: _CClient(msg=msg))
    monkeypatch.setattr(settings, "api_max_output_tokens", 12345)
    await api_transport.generate(provider="claude", model="m", prompt="x", attachments=[])
    assert _CMessages.last["max_tokens"] == 12345


def test_claude_client_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        api_transport._claude_client()


@pytest.mark.asyncio
async def test_guards():
    with pytest.raises(ValueError):
        await api_transport.generate(provider="gemini", model=None, prompt="x", attachments=[])
    # gemini+attachments is now ALLOWED — see test_generate_gemini_accepts_attachments
    # claude+attachments still raises — see test_generate_claude_still_rejects_attachments


# ---- new multimodal tests (Task 1 additions) ----

class _CapturingModels:
    """Captures the `contents` arg passed to generate_content for assertion."""

    def __init__(self):
        self.last_contents = None
        self._resp = _GResp([_Part("ok")], _FR("STOP"), None)

    async def generate_content(self, *, model, contents):
        self.last_contents = contents
        return self._resp


class _CapturingClient:
    def __init__(self):
        self._models = _CapturingModels()
        self.aio = type("_aio", (), {"models": self._models})()

    @property
    def last_contents(self):
        return self._models.last_contents


@pytest.mark.asyncio
async def test_generate_gemini_accepts_attachments(monkeypatch, tmp_path):
    """Gemini + attachments → Part per file, no NotImplementedError."""
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")

    cap = _CapturingClient()
    monkeypatch.setattr(api_transport, "_gemini_client", lambda: cap)

    rc, text, _usage, err = await api_transport.generate(
        provider="gemini", model="gemini-2.5-flash", prompt="hi", attachments=[pdf]
    )
    assert rc == 0, f"unexpected failure: {err}"
    assert text == "ok"

    contents = cap.last_contents
    assert isinstance(contents, list), "expected [prompt, *parts] list"
    assert contents[0] == "hi"
    assert len(contents) == 2, f"expected 2 items (prompt + 1 Part), got {len(contents)}"

    part = contents[1]
    assert part.inline_data.mime_type == "application/pdf"
    assert part.inline_data.data == b"%PDF-1.4 x"


@pytest.mark.asyncio
async def test_generate_gemini_no_attachments_unchanged(monkeypatch):
    """Gemini + no attachments → bare string `contents`, same as before."""
    cap = _CapturingClient()
    monkeypatch.setattr(api_transport, "_gemini_client", lambda: cap)

    rc, text, _usage, err = await api_transport.generate(
        provider="gemini", model="gemini-2.5-flash", prompt="hi", attachments=[]
    )
    assert rc == 0 and text == "ok"
    assert cap.last_contents == "hi", (
        f"expected bare string 'hi', got {cap.last_contents!r}"
    )


@pytest.mark.asyncio
async def test_generate_claude_still_rejects_attachments(tmp_path):
    """Claude + any attachments → NotImplementedError (claude stays text-only)."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 x")
    with pytest.raises(NotImplementedError):
        await api_transport.generate(
            provider="claude", model="claude-opus-4-8", prompt="hi", attachments=[f]
        )


# ---- Clodex (OpenAI-compatible) fakes ----
class _OMessage:
    def __init__(self, content): self.content = content

class _OChoice:
    def __init__(self, content, finish_reason):
        self.message = _OMessage(content)
        self.finish_reason = finish_reason

class _OPromptDetails:
    def __init__(self, cached_tokens=None): self.cached_tokens = cached_tokens

class _OCompletionDetails:
    def __init__(self, reasoning_tokens=None): self.reasoning_tokens = reasoning_tokens

class _OUsage:
    def __init__(self, prompt_tokens=None, completion_tokens=None, total_tokens=None,
                 prompt_tokens_details=None, completion_tokens_details=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.prompt_tokens_details = prompt_tokens_details
        self.completion_tokens_details = completion_tokens_details

class _OResp:
    def __init__(self, choices, usage, model="gpt-5.6-terra"):
        self.choices = choices
        self.usage = usage
        self.model = model

class _OCompletions:
    last = None
    def __init__(self, resp=None, exc=None): self._resp, self._exc = resp, exc
    async def create(self, **kw):
        _OCompletions.last = kw
        if self._exc: raise self._exc
        return self._resp

class _OChat:
    def __init__(self, completions): self.completions = completions

class _OClient:
    def __init__(self, resp=None, exc=None): self.chat = _OChat(_OCompletions(resp, exc))


@pytest.mark.asyncio
async def test_clodex_success_usage_and_served_model(monkeypatch):
    u = _OUsage(prompt_tokens=100, completion_tokens=50, total_tokens=160,
                prompt_tokens_details=_OPromptDetails(cached_tokens=10),
                completion_tokens_details=_OCompletionDetails(reasoning_tokens=12))
    resp = _OResp([_OChoice("hi", "stop")], u)
    monkeypatch.setattr(api_transport, "_clodex_client", lambda: _OClient(resp=resp))
    rc, text, usage, err = await api_transport.generate(
        provider="clodex", model="gpt-5.6-luna", prompt="x", attachments=[])
    assert (rc, text) == (0, "hi")
    assert usage["prompt_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["cached_tokens"] == 10
    assert usage["total_tokens"] == 160
    assert usage["raw"]["reasoning_tokens"] == 12
    assert usage["raw"]["requested_model"] == "gpt-5.6-luna"
    assert usage["raw"]["served_model"] == "gpt-5.6-terra"


@pytest.mark.asyncio
async def test_clodex_cached_tokens_absent_defaults_zero(monkeypatch):
    u = _OUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150,
                prompt_tokens_details=None)
    resp = _OResp([_OChoice("hi", "stop")], u)
    monkeypatch.setattr(api_transport, "_clodex_client", lambda: _OClient(resp=resp))
    rc, text, usage, err = await api_transport.generate(
        provider="clodex", model="gpt-5.6-sol", prompt="x", attachments=[])
    assert usage["cached_tokens"] == 0


@pytest.mark.asyncio
async def test_clodex_truncation_is_loud(monkeypatch):
    resp = _OResp([_OChoice("partial...", "length")], None)
    monkeypatch.setattr(api_transport, "_clodex_client", lambda: _OClient(resp=resp))
    rc, text, usage, err = await api_transport.generate(
        provider="clodex", model="m", prompt="x", attachments=[])
    assert rc == 1 and text == "" and "truncated" in err


@pytest.mark.asyncio
async def test_clodex_cap_passed(monkeypatch):
    resp = _OResp([_OChoice("ok", "stop")], None)
    monkeypatch.setattr(api_transport, "_clodex_client", lambda: _OClient(resp=resp))
    monkeypatch.setattr(settings, "api_max_output_tokens", 12345)
    await api_transport.generate(provider="clodex", model="m", prompt="x", attachments=[])
    assert _OCompletions.last["max_completion_tokens"] == 12345


def test_clodex_client_requires_its_own_key(monkeypatch):
    monkeypatch.delenv("CLODEX_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-forwarded")
    with pytest.raises(api_transport.AuthEnvError, match="CLODEX_API_KEY"):
        api_transport._clodex_client()


def test_clodex_client_base_url_default_and_override(monkeypatch):
    import openai
    seen = {}
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: seen.update(kw) or "client")
    monkeypatch.setenv("CLODEX_API_KEY", "k")
    monkeypatch.setenv("CLODEX_BASE_URL", "https://custom.example/v1")
    api_transport._clodex_client()
    assert seen == {"api_key": "k", "base_url": "https://custom.example/v1"}
    seen.clear()
    monkeypatch.delenv("CLODEX_BASE_URL", raising=False)
    api_transport._clodex_client()
    assert seen == {"api_key": "k", "base_url": "https://clodex.xyz/v1"}


@pytest.mark.asyncio
async def test_generate_clodex_rejects_attachments(tmp_path):
    """Clodex + any attachments -> NotImplementedError (contract PIN; already green
    via the generic guard at api_transport.py:37-40, not new RED)."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 x")
    with pytest.raises(NotImplementedError):
        await api_transport.generate(
            provider="clodex", model="gpt-5.6-sol", prompt="hi", attachments=[f]
        )


def test_mime_for_suffix():
    """_mime_for maps file extensions to MIME types correctly."""
    _mime_for = api_transport._mime_for
    assert _mime_for(Path("x.pdf")) == "application/pdf"
    assert _mime_for(Path("x.png")) == "image/png"
    assert _mime_for(Path("x.jpg")) == "image/jpeg"
    assert _mime_for(Path("x.jpeg")) == "image/jpeg"
    assert _mime_for(Path("x.unknown")) == "application/pdf"   # default
    assert _mime_for(Path("WINDOW.PDF")) == "application/pdf"  # case-insensitive
    assert _mime_for(Path("scan.JPG")) == "image/jpeg"
