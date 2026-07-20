"""Direct provider-SDK generation for transport=api.

Returns the SAME 4-tuple as agent._spawn — (rc, text, usage, stderr) — so the CLI
and api paths are interchangeable at the _spawn chokepoint. cli transport never
calls this. gemini -> google-genai, claude -> anthropic, clodex -> openai SDK.
Text + gemini multimodal (PDF/image attachments via Vertex); claude stays text-only.
Spec: docs/superpowers/specs/2026-06-16-sdk-api-transport-design.md
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.config import settings
from app.services.errors import AuthEnvError

logger = logging.getLogger(__name__)

_EMPTY_USAGE = {
    "prompt_tokens": None,
    "output_tokens": None,
    "cached_tokens": None,
    "cache_creation_tokens": 0,
    "total_tokens": None,
    "raw": {},
}


async def generate(
    *, provider: str, model: str | None, prompt: str, attachments: list[Path]
) -> tuple[int, str, dict, str]:
    """Generate one response via the provider SDK. rc=0+text=success; rc=0+""=blank
    (-> run_phase empty-body retry); rc=1=hard failure incl. truncation (stderr has
    the cause). Raises (loud) on falsy model / missing credentials.
    Gemini accepts PDF/image attachments (multimodal); claude stays text-only."""
    if not model:
        raise ValueError(f"{provider} api requires an explicit model")
    if provider == "gemini":
        return await _gemini(model, prompt, attachments)
    if attachments:
        raise NotImplementedError(
            f"api transport attachments are gemini-only (got provider={provider!r})"
        )
    if provider == "claude":
        return await _claude(model, prompt)
    if provider == "clodex":
        return await _clodex(model, prompt)
    raise ValueError(f"api transport not supported for provider {provider!r}")


# ---------------------------------------------------------------- gemini
def _mime_for(path: Path) -> str:
    """Return the MIME type for a PDF or image attachment path."""
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    return "application/pdf"  # the only current case (window subsets are PDFs)


# 2026-07-16: the Vertex global DSQ pool returned 429 across every pool
# project, so PR #97 temporarily routed gemini-2.5-flash to us-central1.
# 2026-07-20: global had recovered while production us-central1 congestion
# reached ~30% fleet-wide and effectively hard-downed two projects. Default
# back to global to avoid pinning PayGo traffic to one regional DSQ pool.
# GEMINI_MODEL_LOCATIONS remains the no-deploy per-model rollback lever.
_DEFAULT_MODEL_LOCATIONS = {"gemini-2.5-flash": "global"}


def _location_for(model: str) -> str:
    """Resolve the Vertex location for `model`.

    Precedence: a model key in the env `GEMINI_MODEL_LOCATIONS` (JSON object)
    -> a model key in the built-in `_DEFAULT_MODEL_LOCATIONS` -> env
    `GOOGLE_CLOUD_LOCATION` -> `"global"`.

    The env JSON is MERGED over the built-in default map, not swapped in
    wholesale: an override for one model doesn't blank the default for a
    model the env JSON doesn't mention. Models absent from both maps fall
    through to `GOOGLE_CLOUD_LOCATION`/`"global"` as before this router
    existed. Malformed or non-object JSON in `GEMINI_MODEL_LOCATIONS` logs
    an error and falls back to the built-in default map only — this never
    raises, since a bad env value must not take down generation.
    """
    overrides: dict = {}
    raw = os.environ.get("GEMINI_MODEL_LOCATIONS")
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.error(
                "GEMINI_MODEL_LOCATIONS is not valid JSON (%r); "
                "falling back to built-in defaults", raw,
            )
        else:
            if isinstance(parsed, dict):
                # Per-entry validation (PR #97 gate): only str->non-empty-str
                # entries may reach genai.Client(location=...). null/number/
                # empty/nested values are dropped LOUDLY, and a bad entry does
                # not discard the valid ones next to it.
                for k, v in parsed.items():
                    if isinstance(k, str) and isinstance(v, str) and v.strip():
                        overrides[k] = v.strip()
                    else:
                        logger.error(
                            "GEMINI_MODEL_LOCATIONS entry %r: %r is not a "
                            "non-empty string location; entry ignored", k, v,
                        )
            else:
                logger.error(
                    "GEMINI_MODEL_LOCATIONS must be a JSON object, got %s; "
                    "falling back to built-in defaults", type(parsed).__name__,
                )
    merged = {**_DEFAULT_MODEL_LOCATIONS, **overrides}
    if model in merged:
        return merged[model]
    return os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"


def _gemini_client(model: str):
    from google import genai
    from google.genai import types

    # HttpOptions.timeout is MILLISECONDS — verified against the installed
    # google-genai SDK: `types.HttpOptions.model_fields["timeout"]` is
    # `int | None`, described "Timeout for the request in milliseconds"
    # (BE-16 task 5, codex-review #3). settings.per_attempt_timeout_seconds
    # is SECONDS, so convert.
    http_options = types.HttpOptions(
        timeout=int(settings.per_attempt_timeout_seconds * 1000)
    )

    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return genai.Client(api_key=key, http_options=http_options)
    proj = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and proj:
        # Merge of #97 (per-model location routing) + BE-16 task 5 (SDK
        # timeout): route the location AND carry the http_options timeout.
        loc = _location_for(model)
        return genai.Client(
            vertexai=True, project=proj, location=loc, http_options=http_options
        )
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
        "cache_creation_tokens": 0,
        "total_tokens": getattr(um, "total_token_count", None),
        "raw": {
            "prompt_token_count": getattr(um, "prompt_token_count", None),
            "candidates_token_count": cand,
            "thoughts_token_count": thoughts,
            "cached_content_token_count": getattr(um, "cached_content_token_count", None),
            "total_token_count": getattr(um, "total_token_count", None),
        },
    }


async def _gemini(
    model: str, prompt: str, attachments: "list[Path] | tuple" = ()
) -> tuple[int, str, dict, str]:
    client = _gemini_client(model)
    contents: "str | list" = prompt
    if attachments:
        from google.genai import types  # lazy: only when needed

        attach_parts = [
            types.Part.from_bytes(
                data=Path(a).read_bytes(), mime_type=_mime_for(Path(a))
            )
            for a in attachments
        ]
        contents = [prompt, *attach_parts]
    try:
        resp = await client.aio.models.generate_content(model=model, contents=contents)
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
    # anthropic SDK's `timeout` kwarg is SECONDS (float) — verified against
    # the installed package's constructor signature — passed straight
    # through, no conversion (BE-16 task 5, codex-review #3).
    return AsyncAnthropic(api_key=key, timeout=settings.per_attempt_timeout_seconds)


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
        "cache_creation_tokens": ccreate,
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


# ---------------------------------------------------------------- clodex
_CLODEX_BASE_URL = "https://clodex.xyz/v1"


def _clodex_client():
    from openai import AsyncOpenAI

    key = os.environ.get("CLODEX_API_KEY")
    if not key:
        raise AuthEnvError("clodex api: CLODEX_API_KEY unset")
    base_url = os.environ.get("CLODEX_BASE_URL") or _CLODEX_BASE_URL
    # openai SDK's `timeout` kwarg is SECONDS (float) — verified against the
    # installed package's constructor signature — passed straight through,
    # no conversion (BE-16 task 5, codex-review #3).
    return AsyncOpenAI(
        api_key=key, base_url=base_url, timeout=settings.per_attempt_timeout_seconds
    )


def _clodex_usage(u, *, requested_model: str, served_model: str | None) -> dict:
    if u is None:
        usage = dict(_EMPTY_USAGE)
        usage["raw"] = {
            "requested_model": requested_model,
            "served_model": served_model,
        }
        return usage
    prompt = getattr(u, "prompt_tokens", None)
    completion = getattr(u, "completion_tokens", None)
    total = getattr(u, "total_tokens", None)
    prompt_details = getattr(u, "prompt_tokens_details", None)
    cached = (
        getattr(prompt_details, "cached_tokens", None)
        if prompt_details is not None else None
    )
    completion_details = getattr(u, "completion_tokens_details", None)
    reasoning = (
        getattr(completion_details, "reasoning_tokens", None)
        if completion_details is not None else None
    )
    return {
        "prompt_tokens": prompt,
        "output_tokens": completion,
        "cached_tokens": cached if cached is not None else 0,
        "cache_creation_tokens": 0,
        "total_tokens": total,
        "raw": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "cached_tokens": cached,
            "reasoning_tokens": reasoning,
            "requested_model": requested_model,
            "served_model": served_model,
        },
    }


async def _clodex(model: str, prompt: str) -> tuple[int, str, dict, str]:
    client = _clodex_client()
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_completion_tokens=settings.api_max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        return 1, "", dict(_EMPTY_USAGE), str(exc)
    usage = _clodex_usage(
        getattr(resp, "usage", None),
        requested_model=model,
        served_model=getattr(resp, "model", None),
    )
    choices = getattr(resp, "choices", None) or []
    choice = choices[0] if choices else None
    finish_reason = getattr(choice, "finish_reason", None)
    text = getattr(getattr(choice, "message", None), "content", None) or ""
    if finish_reason == "length":
        return (
            1,
            "",
            usage,
            f"output truncated at max_completion_tokens={settings.api_max_output_tokens}",
        )
    if not text:
        return 0, "", usage, str(finish_reason or "")
    return 0, text, usage, ""
