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
