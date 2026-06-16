# SDK-based `transport=api` (Gemini + Claude) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `transport=api`, generate via the official provider SDKs (gemini `google-genai`, claude `anthropic`) instead of spawning the CLI — same `(rc, text, usage, stderr)` contract, dispatched at the single `_spawn` chokepoint, with truncation/auth failures loud.

**Architecture:** A new `app/services/api_transport.py` returns the same 4-tuple `agent._spawn` returns. `_spawn` gains one early branch (inside the concurrency semaphore) that delegates gemini/claude api spawns to it. `transport=cli` and all downstream machinery (usage recording, pricing, failover, judge) are unchanged.

**Tech Stack:** Python, asyncio, `google-genai`, `anthropic`, pytest + pytest-asyncio (strict mode — async tests need `@pytest.mark.asyncio`). Spec: `docs/superpowers/specs/2026-06-16-sdk-api-transport-design.md`.

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `pyproject.toml` / `uv.lock` | add `google-genai`, `anthropic` (`uv add`) | runtime deps for the api path |
| `app/config.py` | add `api_max_output_tokens: int = 16384` | claude output cap (Anthropic requires `max_tokens`) |
| `app/services/api_transport.py` | **new** | SDK clients, generate(), usage mapping, truncation/blocked/guard handling — returns `_spawn`'s 4-tuple |
| `app/services/agent.py` (`_spawn`, ~line 319) | one dispatch branch | route api gemini/claude spawns to `api_transport`, inside `_semaphore()` |
| `CLAUDE.md` (`:9`, `:143`) | reword | scope the no-SDK rule to cli; document the api SDK path |
| `tests/services/test_api_transport.py` | **new** | unit-test the module (network stubbed) |
| `tests/services/test_agent.py` | add | dispatch routing test |
| `tests/services/test_phase_judge.py` | add | SDK auth strings trip `_AUTH_SIGNALS` |

---

## Task 1: Dependencies + config knob

**Files:**
- Modify: `pyproject.toml` + `uv.lock` (via `uv add`)
- Modify: `app/config.py` (after `gemini_max_concurrency`, ~line 63)

- [ ] **Step 1: Add the SDK dependencies**

Run: `uv add google-genai anthropic`
Expected: resolves, updates `pyproject.toml` `[project.dependencies]` and `uv.lock`, prints "Installed …".

- [ ] **Step 2: Add the claude output-cap setting**

In `app/config.py`, immediately after the `gemini_max_concurrency: int = 8 …` line, add:

```python
    # transport=api: claude's Messages API REQUIRES max_tokens (gemini does not,
    # and stays uncapped). 16384 gives headroom over the longest uncapped content
    # phases (reading/preview-hard); hitting it fails LOUD, never silent-truncates.
    api_max_output_tokens: int = 16384  # env API_MAX_OUTPUT_TOKENS
```

- [ ] **Step 3: Verify deps import + setting loads**

Run: `uv run python -c "import google.genai, anthropic; from app.config import settings; assert settings.api_max_output_tokens == 16384; print('OK')"`
Expected: prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock app/config.py
git commit -m "feat(api-sdk): add google-genai + anthropic deps and api_max_output_tokens setting

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `api_transport.py` module (TDD)

**Files:**
- Create: `app/services/api_transport.py`
- Test: `tests/services/test_api_transport.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_api_transport.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_api_transport.py -q`
Expected: FAIL with `ModuleNotFoundError: app.services.api_transport` (or attribute errors).

- [ ] **Step 3: Implement the module**

Create `app/services/api_transport.py`:

```python
"""Direct provider-SDK generation for transport=api.

Returns the SAME 4-tuple as agent._spawn — (rc, text, usage, stderr) — so the CLI
and api paths are interchangeable at the _spawn chokepoint. cli transport never
calls this. gemini -> google-genai, claude -> anthropic. Text-only in v1.
Spec: docs/superpowers/specs/2026-06-16-sdk-api-transport-design.md
"""
from __future__ import annotations

import os
from pathlib import Path

from app.config import settings

_EMPTY_USAGE = {
    "prompt_tokens": None,
    "output_tokens": None,
    "cached_tokens": None,
    "total_tokens": None,
    "raw": {},
}


async def generate(
    *, provider: str, model: str | None, prompt: str, attachments: list[Path]
) -> tuple[int, str, dict, str]:
    """Generate one response via the provider SDK. rc=0+text=success; rc=0+""=blank
    (-> run_phase empty-body retry); rc=1=hard failure incl. truncation (stderr has
    the cause). Raises (loud) on falsy model / attachments / missing credentials."""
    if not model:
        raise ValueError(f"{provider} api requires an explicit model")
    if attachments:
        raise NotImplementedError("api transport is text-only in v1")
    if provider == "gemini":
        return await _gemini(model, prompt)
    if provider == "claude":
        return await _claude(model, prompt)
    raise ValueError(f"api transport not supported for provider {provider!r}")


# ---------------------------------------------------------------- gemini
def _gemini_client():
    from google import genai

    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return genai.Client(api_key=key)
    proj = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and proj:
        loc = os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
        return genai.Client(vertexai=True, project=proj, location=loc)
    raise RuntimeError(
        "gemini api: no GEMINI_API_KEY and no Vertex SA "
        "(GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT)"
    )


def _gemini_usage(um) -> dict:
    if um is None:
        return dict(_EMPTY_USAGE)
    cand = getattr(um, "candidates_token_count", None)
    thoughts = getattr(um, "thoughts_token_count", None)
    output = None if (cand is None and thoughts is None) else (cand or 0) + (thoughts or 0)
    return {
        "prompt_tokens": getattr(um, "prompt_token_count", None),
        "output_tokens": output,
        "cached_tokens": getattr(um, "cached_content_token_count", None),
        "total_tokens": getattr(um, "total_token_count", None),
        "raw": {
            "prompt_token_count": getattr(um, "prompt_token_count", None),
            "candidates_token_count": cand,
            "thoughts_token_count": thoughts,
            "cached_content_token_count": getattr(um, "cached_content_token_count", None),
            "total_token_count": getattr(um, "total_token_count", None),
        },
    }


async def _gemini(model: str, prompt: str) -> tuple[int, str, dict, str]:
    client = _gemini_client()
    try:
        resp = await client.aio.models.generate_content(model=model, contents=prompt)
    except Exception as exc:  # noqa: BLE001 — surface to run_phase failure/failover
        return 1, "", dict(_EMPTY_USAGE), str(exc)
    usage = _gemini_usage(getattr(resp, "usage_metadata", None))
    candidates = getattr(resp, "candidates", None) or []
    cand = candidates[0] if candidates else None
    finish = getattr(cand, "finish_reason", None)
    fr = getattr(finish, "name", None) or str(finish or "")
    parts = getattr(getattr(cand, "content", None), "parts", None) or []
    text = "".join((getattr(p, "text", "") or "") for p in parts)
    if text and fr == "MAX_TOKENS":
        return 1, "", usage, "output truncated: finish_reason=MAX_TOKENS"
    if not text:
        return 0, "", usage, fr          # blocked / no part -> empty-body retry
    return 0, text, usage, ""


# ---------------------------------------------------------------- claude
def _claude_client():
    from anthropic import AsyncAnthropic

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("claude api: ANTHROPIC_API_KEY unset")
    return AsyncAnthropic(api_key=key)


def _claude_usage(u) -> dict:
    if u is None:
        return dict(_EMPTY_USAGE)
    inp = getattr(u, "input_tokens", None)
    out = getattr(u, "output_tokens", None)
    cread = getattr(u, "cache_read_input_tokens", None)
    ccreate = getattr(u, "cache_creation_input_tokens", None)
    terms = [t for t in (inp, out, cread, ccreate) if isinstance(t, int)]
    return {
        "prompt_tokens": inp,
        "output_tokens": out,
        "cached_tokens": cread,
        "total_tokens": sum(terms) if terms else None,   # matches providers/claude.py
        "raw": {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cread,
            "cache_creation_input_tokens": ccreate,
        },
    }


async def _claude(model: str, prompt: str) -> tuple[int, str, dict, str]:
    client = _claude_client()
    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=settings.api_max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        return 1, "", dict(_EMPTY_USAGE), str(exc)
    usage = _claude_usage(getattr(msg, "usage", None))
    blocks = getattr(msg, "content", None) or []
    text = "".join(
        (getattr(b, "text", "") or "") for b in blocks if getattr(b, "type", None) == "text"
    )
    if getattr(msg, "stop_reason", None) == "max_tokens":
        return 1, "", usage, f"output truncated at max_tokens={settings.api_max_output_tokens}"
    if not text:
        return 0, "", usage, str(getattr(msg, "stop_reason", ""))
    return 0, text, usage, ""
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run python -m pytest tests/services/test_api_transport.py -q`
Expected: all pass (11 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/api_transport.py tests/services/test_api_transport.py
git commit -m "feat(api-sdk): api_transport.generate — gemini/claude SDK calls, loud truncation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Dispatch in `_spawn` (inside the semaphore)

**Files:**
- Modify: `app/services/agent.py` (top of `_spawn`, between the closing docstring `"""` at ~line 319 and `binary = _resolve_binary(provider)` at ~line 320)
- Test: `tests/services/test_agent.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/services/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_spawn_api_gemini_delegates_to_sdk_inside_semaphore(monkeypatch):
    """transport=api + gemini routes to api_transport.generate, never spawns a
    subprocess, and holds the concurrency semaphore while doing so."""
    import asyncio as _asyncio
    from app.services import agent, api_transport

    held = {"sem": False}
    seen = {}

    # Pin a 1-slot semaphore: .locked() is True only when fully exhausted, so the
    # default Semaphore(8) would read False after acquiring 1 slot. With Semaphore(1)
    # the single in-use slot makes .locked() True while the SDK call is in flight.
    monkeypatch.setattr(agent, "_agent_semaphore", _asyncio.Semaphore(1))

    async def fake_generate(**kw):
        seen.update(kw)
        held["sem"] = agent._semaphore().locked()   # True iff the slot is held
        return (0, "SDK_SENTINEL", {"raw": {}}, "")

    monkeypatch.setattr(api_transport, "generate", fake_generate)

    def boom(*a, **k):
        raise AssertionError("api path must not spawn a subprocess")
    monkeypatch.setattr(_asyncio, "create_subprocess_exec", boom)

    rc, text, usage, err = await agent._spawn(
        provider=agent.get_provider("gemini"), model="gemini-2.5-flash",
        prompt="p", attachments=[], transport="api")

    assert text == "SDK_SENTINEL"
    assert seen["provider"] == "gemini" and seen["model"] == "gemini-2.5-flash"
    assert held["sem"] is True


@pytest.mark.asyncio
async def test_spawn_api_cli_only_provider_still_uses_cli(monkeypatch):
    """transport=api with a non-(gemini|claude) provider does NOT take the SDK
    branch — it falls through to the CLI path."""
    from app.services import agent, api_transport

    async def boom(**kw):
        raise AssertionError("kimi must not use the SDK path")
    monkeypatch.setattr(api_transport, "generate", boom)

    # Reaching _resolve_binary proves we left the SDK branch; stop there cheaply.
    monkeypatch.setattr(agent, "_resolve_binary",
                        lambda prov: (_ for _ in ()).throw(RuntimeError("reached-cli")))
    with pytest.raises(RuntimeError, match="reached-cli"):
        await agent._spawn(provider=agent.get_provider("kimi"), model=None,
                           prompt="p", attachments=[], transport="api")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_agent.py -k "delegates_to_sdk or cli_only_provider" -q`
Expected: FAIL — `test_..._delegates` spawns the CLI (AssertionError "must not spawn") because the branch doesn't exist yet.

- [ ] **Step 3: Add the dispatch branch**

In `app/services/agent.py`, immediately after the `_spawn` docstring's closing `"""` (line ~319) and BEFORE `binary = _resolve_binary(provider)`, insert:

```python
    # transport=api for gemini/claude -> direct SDK call, not the CLI. Kept BEFORE
    # _resolve_binary so a pure-API worker needs no CLI on PATH; kept INSIDE
    # _semaphore() so direct-API fan-out is bounded exactly like CLI subprocesses.
    if transport == "api" and provider.name in ("gemini", "claude"):
        from app.services import api_transport
        async with _semaphore():
            return await api_transport.generate(
                provider=provider.name, model=model, prompt=prompt,
                attachments=attachments,
            )

```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_agent.py -k "delegates_to_sdk or cli_only_provider" -q`
Expected: both pass.

- [ ] **Step 5: Module imports cleanly**

Run: `uv run python -c "import app.services.agent; print('import OK')"`
Expected: `import OK`.

- [ ] **Step 6: Commit**

```bash
git add app/services/agent.py tests/services/test_agent.py
git commit -m "feat(api-sdk): _spawn delegates gemini/claude api spawns to api_transport (in-semaphore)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Guard the auth-failure invariant

**Files:**
- Test: `tests/services/test_phase_judge.py` (add)

Context: an api auth failure must fail the job loudly (not degrade to `judge-unavailable`). The judge's `_is_auth_error` (`phase_judge.py:108`) lowercases the message and matches `_AUTH_SIGNALS` (`401`, `permission_denied`, `api key not valid`, `invalid_grant`, `unauthenticated`, …). `api_transport` surfaces SDK auth errors verbatim in the `stderr`/exception message. This test pins that the *real* SDK error strings trip that matcher (the concrete risk the design flags — strings must contain a signal within the 400-char `_failure_preview` window).

- [ ] **Step 1: Write the test**

Add to `tests/services/test_phase_judge.py`:

```python
def test_sdk_auth_error_strings_trip_auth_signals():
    """Representative google-genai / anthropic / AI-Studio auth-error strings must
    be recognized as auth errors, so an api job fails loud instead of degrading."""
    from app.services import phase_judge

    samples = [
        "google.api_core.exceptions.PermissionDenied: 403 permission_denied: ...",
        "PERMISSION_DENIED: Vertex AI API has not been used in project ...",
        "anthropic.AuthenticationError: Error code: 401 - invalid x-api-key",
        "google.genai.errors.ClientError: 400 API key not valid. Please pass a valid API key.",
        "RefreshError: invalid_grant: Invalid JWT Signature.",
    ]
    for s in samples:
        assert phase_judge._is_auth_error(RuntimeError(s)), s
```

- [ ] **Step 2: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_phase_judge.py::test_sdk_auth_error_strings_trip_auth_signals -v`
Expected: PASS. (If a sample fails, the SDK string lacks any `_AUTH_SIGNALS` substring after lowercasing — extend `_AUTH_SIGNALS` in `phase_judge.py` to cover it rather than weakening the test. Do NOT add a bare `"403"` — the existing comment at `phase_judge.py:94` explains why.)

- [ ] **Step 3: Commit**

```bash
git add tests/services/test_phase_judge.py
git commit -m "test(api-sdk): SDK auth-error strings trip _AUTH_SIGNALS (api fails loud)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Update CLAUDE.md (ships with the work)

**Files:**
- Modify: `CLAUDE.md:9` (header) and `CLAUDE.md:143` (Things not to do)

- [ ] **Step 1: Reword the header SDK claim**

In `CLAUDE.md`, replace the sentence at line 9:

```
Everything LLM-facing goes through `app/services/agent.py` (the CLI router); there is no Gemini SDK, OpenAI SDK, or Anthropic SDK in the runtime path. The five CLIs must be installed on `PATH`.
```

with:

```
Everything LLM-facing goes through `app/services/agent.py`. For `transport=cli` it drives the provider **CLIs** (the CLI router — the five CLIs must be installed on `PATH`); for `transport=api` it calls the provider **SDKs** directly via `app/services/api_transport.py` (gemini `google-genai`, claude `anthropic`).
```

- [ ] **Step 2: Reword the "Things not to do" rule**

In `CLAUDE.md`, replace the bullet at line 143:

```
- Don't reintroduce a Gemini / Anthropic / OpenAI SDK call. Everything goes through the CLI router. `google-genai` was deliberately removed from `pyproject.toml`.
```

with:

```
- Don't use a provider SDK on the `transport=cli` path — cli goes through the CLI router only. SDK calls are confined to `transport=api` (`app/services/api_transport.py`: gemini `google-genai`, claude `anthropic`). Don't add a third provider's SDK without extending that module **and** `agent_models.API_PROVIDERS`/`api_supported`.
```

- [ ] **Step 3: Sanity check the lines changed**

Run: `grep -n "transport=api. it calls the provider\|SDK calls are confined to" CLAUDE.md`
Expected: both new lines present; the old "there is no Gemini SDK … in the runtime path" / "Don't reintroduce a Gemini …" phrasings are gone.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(api-sdk): scope the no-SDK rule to transport=cli; document the api SDK path

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Acceptance + finish

**Files:** none (verification + docs)

- [ ] **Step 1: Full suite green**

Run: `uv run python -m pytest tests/ -q`
Expected: the new tests (Tasks 2-4) pass; baseline is **0 failed** (matches the api-3 run: ~408 passed / 48 skipped; integration tests skip without `RUN_DB_INTEGRATION=1`). Treat ANY failure as new — investigate, don't wave it off.

- [ ] **Step 2: FE untouched**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: clean (this is a backend-only change; FE shouldn't be affected — confirms nothing leaked).

- [ ] **Step 3: Real SDK smoke — gemini (already proven) + claude**

Gemini is already proven end-to-end via `uv run --with google-genai python scripts/api_vs_cli_compare.py --only sdk` (complete, graded-PASS CBP on the Vertex SA). Re-run it against the in-tree module path to confirm the production `api_transport.generate` works:

```bash
ANTHROPIC_API_KEY=<key> uv run python -c "
import asyncio
from app.services import api_transport
rc, text, usage, err = asyncio.run(api_transport.generate(
    provider='claude', model='claude-haiku-4-5-20251001',
    prompt='Write a 3-sentence explanation of photosynthesis for a 7th grader.',
    attachments=[]))
print('rc', rc, '| chars', len(text), '| usage', usage['prompt_tokens'], usage['output_tokens'], usage['total_tokens'])
assert rc == 0 and text and usage['total_tokens'], (rc, err)
print('CLAUDE SDK SMOKE OK')
"
```

Expected: `rc 0`, non-empty text, non-zero usage, `CLAUDE SDK SMOKE OK`. (Gemini equivalent: `provider='gemini', model='gemini-2.5-flash'` on a host with the Vertex SA / `GEMINI_API_KEY`.) Record the observed token counts in the worklog. If no claude key is available on this host, note it and rely on the gemini smoke + unit tests; do not fake it.

- [ ] **Step 4: Worklog + ROADMAP**

Add a `## [00NN]` entry (next free number — check `docs/memory/MASTER_MEMORY.md`; 0062 is taken by R13, so likely 0063) summarizing: SDK-based `transport=api` for gemini+claude; the `_spawn` in-semaphore dispatch; loud truncation/auth handling; deps + `api_max_output_tokens`; the CLAUDE.md scope change; suite + smoke results + the measured CLI-vs-SDK savings that motivated it. Add a matching `docs/memory/INDEX.md` row.

```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs(memory): worklog 00NN — SDK-based transport=api (gemini+claude)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: Finish the branch**

Use `superpowers:finishing-a-development-branch` — push `feat/sdk-api-transport` and open a PR into `Nggaev-v2` (project convention: one PR per feature). Note in the PR description that this reverses the prior no-SDK rule (CLAUDE.md updated in this branch) and includes the `scripts/api_vs_cli_compare.py` harness if you choose to commit it.

---

## Self-review

**Spec coverage:**
- Goal (gemini+claude SDK at `_spawn`) → Tasks 2, 3 ✓
- Decision 1 (SDKs) → Task 1 deps + Task 2 module ✓
- Decision 2 (both providers; judge benefit) → Task 2 (both branches), Task 3 (provider-based dispatch covers the claude judge) ✓
- Decision 3 (dispatch at chokepoint, in semaphore) → Task 3 (branch inside `async with _semaphore()`; test asserts `held['sem']`) ✓
- Decision 4 (gemini uncapped / claude `api_max_output_tokens`=16384 / loud truncation) → Task 1 (setting), Task 2 (`_gemini`/`_claude` truncation branches + tests `test_*_truncation_is_loud`, `test_claude_cap_passed`) ✓
- Decision 5 (text-only, loud attachment guard) → Task 2 (`generate` guard + `test_guards`) ✓
- Verified facts — `_spawn` 4-tuple (Task 2 return shape), semaphore (Task 3), `_auth_env` credential mirror (Task 2 `_gemini_client`/`_claude_client` + `test_gemini_client_credentials`/`test_claude_client_requires_key`), empty-body retry (`test_gemini_blocked_empty_is_retryable` returns `(0,"")`), usage semantics (`test_gemini_success_usage`, `test_claude_success_total_includes_cache`), defensive usage (`test_gemini_missing_usage_no_crash`) ✓
- §3 config → Task 1 ✓; §4 deps → Task 1 ✓; §5 CLAUDE.md → Task 5 ✓
- Testing list 1-9 → Task 2 (1-7), Task 3 (8), Task 4 (9) ✓
- Acceptance (real SDK smoke + suite + FE) → Task 6 ✓

**Placeholder scan:** none — every code step shows the literal code; commands have expected output. (Worklog number is "00NN — next free" with the lookup instruction, which is correct, not a placeholder.)

**Type/name consistency:** `api_transport.generate(provider, model, prompt, attachments)` signature is identical across the module, the `_spawn` dispatch, and all tests. The 4-tuple `(rc, text, usage, stderr)` is consistent. `_gemini_client`/`_claude_client` factory names match between the module and the tests that patch them. `api_max_output_tokens` consistent across config, module, and `test_claude_cap_passed`. Usage keys (`prompt_tokens`/`output_tokens`/`cached_tokens`/`total_tokens`/`raw`) match `_spawn`'s contract.
