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
    with pytest.raises(NotImplementedError):
        await api_transport.generate(provider="gemini", model="m", prompt="x",
                                     attachments=[Path("/x.pdf")])
