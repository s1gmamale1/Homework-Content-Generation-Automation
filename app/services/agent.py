"""CLI-subprocess router for homework-builder phases.

Replaces the google-genai SDK calls in :mod:`app.services.gemini` with a
provider-neutral subprocess driver. Each call:

1. Resolves a binary on PATH from the chosen provider's ``binary_names``.
2. Builds the argv via the provider, spawns it via ``asyncio.create_subprocess_exec``,
   pipes the master prompt to stdin, and decodes stdout as UTF-8.
3. Parses the provider-specific envelope into ``(text, usage)``.
4. Persists one ``AgentUsage`` row per call (success or failure).

All calls are gated by a process-wide ``asyncio.Semaphore`` so the worker pool
× per-pipeline parallel scheduler can't fan out past whatever the local CLI
quota allows. The semaphore size is controlled by ``settings.agent_max_concurrency``
(the live knob); ``settings.gemini_max_concurrency`` is a deprecated fallback used
only when ``agent_max_concurrency`` is left at its default of 8.

Public functions deliberately mirror the surface of :mod:`app.services.gemini`
so callers (``pipeline.py``) can be migrated incrementally; the gemini module
keeps importing during the transition.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Optional
from uuid import UUID

from loguru import logger
from pydantic import BaseModel, Field, ValidationError
from pypdf import PdfReader, PdfWriter

from app.config import settings
from app.db import SessionLocal
from app.repositories import agent_usage as usage_repo
from app.schemas import (
    ExtractedTOC,
    TOCEntryExtracted,
    TOCValidation,
)
from app.services import agent_models, content_lint, errors, failure_classifier
from app.services.errors import AuthEnvError
from app.services.providers import Provider, get_provider
from app.services.proc_tree import kill_tree


# ─────────────────────────────────────────────────────────────────────
# Public types & constants
# ─────────────────────────────────────────────────────────────────────


class SchemaValidationExhausted(RuntimeError):
    """Schema mode exhausted every attempt without producing a valid model.

    Subclasses RuntimeError so existing callers that catch RuntimeError keep
    working; the distinct type is what lets the pipeline tell "the model cannot
    produce this config" apart from a transport fault.
    """


@dataclass
class PhaseResult:
    """Outcome of a single ``run_phase`` call.

    ``text`` is always the raw assistant text (post-envelope-extraction).
    ``parsed`` is populated only when ``run_phase`` was given a ``schema`` and
    Pydantic validated it. ``usage`` is the normalized token-count dict
    surfaced by the provider (keys: prompt/output/cached/total tokens, raw).
    """

    text: str
    parsed: Optional[BaseModel] = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw_envelope: dict[str, Any] = field(default_factory=dict)


@dataclass
class TOCValidationResult:
    """Outcome of a ``validate_toc`` call.

    ``status`` is one of "verified", "mismatch", or "skipped".
    "skipped" means the validator was unable to produce a verdict (window
    build failure, spawn error, or parse error) and the caller should
    treat the TOC as unverified rather than failing loudly.
    """

    status: str  # "verified" | "mismatch" | "skipped"
    confidence: Optional[str]  # "low" | "medium" | "high" | None
    issues: list[str]
    detail: str


# Default-model lookup. **Regression guard from a prior session**:
# ``_PROVIDER_DEFAULT_MODEL["gemini"]`` MUST stay ``None`` so that one
# provider's default never leaks into another's resolution path. Each CLI's
# own default is preferred when ``model`` is unset.
_PROVIDER_DEFAULT_MODEL: dict[str, Optional[str]] = {
    "claude": "claude-sonnet-4-6",
    "kimi":   None,
    "codex":  None,
    "gemini": None,
    # opencode cannot run bare — it REQUIRES a provider/model — so unlike the
    # others it carries a non-None default (a free zen model). This does NOT
    # violate the no-leak invariant: kimi/codex/gemini stay None.
    "opencode": "opencode/deepseek-v4-flash-free",
    "clodex": None,
}


def _resolve_model(provider: str, model: Optional[str]) -> Optional[str]:
    """Pick the model identifier to pass to ``provider.build_argv``.

    Caller-supplied ``model`` always wins. Otherwise we look up the
    provider's default; ``None`` means "let the CLI pick its own default"
    (no ``--model`` flag injected by ``build_argv``).
    """
    if model:
        return model
    return _PROVIDER_DEFAULT_MODEL.get(provider)


_TOC_TEXT_MAX_PAGES = 40        # front scan ceiling (physical pages)
_TOC_TEXT_MAX_CHARS = 60_000    # total char budget (front + tail combined)
# Many textbooks (esp. Uzbek "Mundarija") put the contents page at the BACK of
# the book. So we also scan the last _TOC_TAIL_PAGES pages, reserving
# _TOC_TAIL_MAX_CHARS of the budget for them so a dense front matter can't
# starve the tail scan.
_TOC_TAIL_PAGES = 15
_TOC_TAIL_MAX_CHARS = 20_000
# Gemini's CLI rejects PDFs larger than ~20 MB; below this we can attach the
# whole PDF for native reading alongside the text excerpt (see extract_toc).
_GEMINI_PDF_MAX_BYTES = 20 * 1024 * 1024


# Universal "no preamble" directive (lifted verbatim from gemini.py:274–285).
_NO_PREAMBLE = (
    "\n\n## OUTPUT RULES\n"
    "Return ONLY the requested deliverable. Do NOT write any introduction, "
    "preface, meta-commentary, header sentence, or closing summary. Do NOT "
    "begin with openers like 'Mana,', 'Quyida,', 'Here are', 'Below is', "
    "'Avvalo,', 'Albatta,', 'Of course,', 'Sure,' or similar. Start the "
    "response immediately with the actual content.\n\n"
    "Faqat so'ralgan natijani qaytaring. Hech qanday kirish, muqaddima, izoh, "
    "sarlavha gap yoki yakuniy xulosa yozmang. 'Mana,', 'Quyida,', 'Avvalo,', "
    "'Albatta,' kabi kirish iboralari bilan boshlamang. Javobni darhol asosiy "
    "mazmun bilan boshlang."
)


# Placeholder visual rules: phases never emit <svg>/images, only described
# placeholders the frontend renders (see web/src/components/rich-text.tsx).
# Replaces the old _SVG_RULES styling block.
_PLACEHOLDER_RULES = (
    "\n\n## VISUAL RULES (placeholders only \u2014 never emit `<svg>` or images)\n"
    "Do NOT output inline `<svg>`, raster images, `<img>`, `<foreignObject>`, or "
    "any image/HTML markup. When a visual genuinely helps, emit ONE described "
    "placeholder the runtime will render:\n"
    "  `![visual: <diagram|photo> \u2014 <what to depict> \u2014 image gen required](placeholder)`\n"
    "Description rules:\n"
    "  - Name the medium: `diagram` (figure/chart/timeline/structure) or `photo` "
    "(real scene/object).\n"
    "  - Be self-sufficient: state every label, value, axis, part, and "
    "relationship so the visual can be built from the text alone.\n"
    "  - Keep the link target literally `](placeholder)` \u2014 it is the sentinel "
    "the renderer matches on.\n"
    "  - Default to NO visual; add one only when it carries the concept. Never "
    "fabricate an image, never invent an image URL."
)


# Builtin per-section "extract" prompt (lifted verbatim from gemini.py:253–267).
_EXTRACT_PHASE_PROMPT = (
    'You are reading the attached textbook. The lesson is "{title}" '
    "(section {number}, pages {ps}-{pe}).\n\n"
    "Extract all factual lesson content the textbook teaches on these pages. "
    "Include: key terms with definitions, named processes/mechanisms with steps, "
    "diagrams/visuals (describe them concisely), worked examples, formulas, "
    "organisms/structures with functions, historical references, experiments, "
    "and comparison tables.\n\n"
    "Output as structured Markdown. Be faithful to the source — do not invent.\n\n"
    "BE CONCISE — keep the entire extraction under 2000 words. Prefer dense "
    "bullet lists over prose. Skip anything not directly part of the lesson "
    "(no front-matter, no exercises, no acknowledgements). This output is "
    "consumed by every downstream homework phase, so smaller is cheaper."
    + "{rules}"
)


# Phases that may include a visual -> get the placeholder rules appended.
# (Same membership as the old _SVG_PHASES; flashcards/boss-arena/games are
# omitted — they are atomic and rarely need a figure.)
_VISUAL_PHASES: set[str] = {
    "preview-hard", "preview-easy", "preview",
    "case-based-preview",
    "real-life", "consolidation",
    "game-breaks", "final-challenge", "reading",
}


# Strip inline SVGs from prior_outputs before injection (lifted from
# flows.py:_SVG_BLOCK_RE). Downstream phases need the *concepts* an upstream
# taught, not the SVG bytes — replacing with a placeholder lets the model
# still know a diagram was present without re-paying ~800 input tokens.
_SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>.*?</svg>", re.DOTALL | re.IGNORECASE)


def _strip_svgs(text: str) -> str:
    return _SVG_BLOCK_RE.sub("[diagram omitted]", text)


# ─────────────────────────────────────────────────────────────────────
# Concurrency gate
# ─────────────────────────────────────────────────────────────────────

# Default concurrency — must match config.py field defaults for both
# agent_max_concurrency and gemini_max_concurrency.
_DEFAULT_CONCURRENCY = 8

# Lazy-init module-level semaphore. First access creates an
# ``asyncio.Semaphore(_effective_concurrency())``. Bound to the running
# loop on first await — works regardless of when callers import this module.
_agent_semaphore: Optional[asyncio.Semaphore] = None


def _effective_concurrency() -> int:
    """Return the process-wide CLI-concurrency cap.

    ``settings.agent_max_concurrency`` is the live knob.  When it has been
    left at its default (``8``), we fall back to
    ``settings.gemini_max_concurrency`` so that existing ``.env`` files that
    set only ``GEMINI_MAX_CONCURRENCY`` continue to work as before.  An
    operator who sets ``AGENT_MAX_CONCURRENCY`` explicitly always wins.
    """
    if settings.agent_max_concurrency != _DEFAULT_CONCURRENCY:
        return settings.agent_max_concurrency
    return settings.gemini_max_concurrency


def _semaphore() -> asyncio.Semaphore:
    global _agent_semaphore
    if _agent_semaphore is None:
        _agent_semaphore = asyncio.Semaphore(_effective_concurrency())
    return _agent_semaphore


# ─────────────────────────────────────────────────────────────────────
# Subprocess driver
# ─────────────────────────────────────────────────────────────────────


def _resolve_binary(provider: Provider) -> str:
    """Walk ``provider.binary_names`` and return the first ``shutil.which`` hit."""
    for name in provider.binary_names:
        binary = shutil.which(name)
        if binary:
            return binary
    raise FileNotFoundError(
        f"{provider.name} CLI not found on PATH; install one of "
        f"{list(provider.binary_names)}"
    )


def provider_cli_installed(provider_name: str) -> bool:
    """True if at least one of the provider's CLI binaries is on PATH. Lets the
    failover chain skip a FALLBACK provider that isn't installed on this worker,
    instead of trying it and dying with a confusing ``<prov> CLI not found`` that
    burns an attempt (the R13 field-test failure mode). (WISHLIST `fleet-failover-1`.)"""
    try:
        provider = get_provider(provider_name)
    except Exception:  # unknown provider name → treat as not installed
        return False
    return any(shutil.which(n) for n in provider.binary_names)


def _auth_env(provider_name: str, transport: str, base_env: dict[str, str]) -> dict[str, str]:
    """Per-call auth shaping (spec §4). cli is the unconditional baseline for
    EVERY spawn; api is the only deviation. Scrub both provider keys first, then
    grant exactly what the (provider, transport) needs — so an api gemini spawn
    never carries the Anthropic key, and a cli spawn never accidentally bills."""
    env = dict(base_env)
    env.pop("GEMINI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    # Defensive hygiene, not a verified mis-billing fix: codex-CLI's own auth
    # flip is CODEX_API_KEY, not this var (WISHLIST tracks scrubbing that one
    # separately). Scrubbed here anyway, same class as the two pops above, so
    # API credentials never leak into a CLI subprocess.
    env.pop("OPENAI_API_KEY", None)
    env.pop("CLODEX_API_KEY", None)
    env.pop("CLODEX_BASE_URL", None)
    env.pop("GOOGLE_GENAI_USE_GCA", None)
    # Also scrub the Vertex selector: gemini-cli 0.46.0 getAuthTypeFromEnv
    # checks GCA first, then GOOGLE_GENAI_USE_VERTEXAI, then GEMINI_API_KEY —
    # GCA-first means a lingering selector is harmless for cli, but scrub-first
    # keeps every mode's auth signal explicit.
    env.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
    # Scrub the GCP project/SA vars too (2026-06-12, caught by the 2-PC live
    # test): GOOGLE_CLOUD_PROJECT selects no auth type, but it RE-SCOPES the
    # OAuth/Code-Assist call to that GCP project — a cli spawn inheriting it
    # from a worker's .env (Vertex creds kept for api mode) 403s ("Cloud Code
    # Private API has not been used in project …") when the project lacks that
    # API. Proven live on the head PC: bare gemini OK; same call with
    # GOOGLE_CLOUD_PROJECT=dummy → exit 403. The api Vertex branch re-grants
    # all three explicitly below.
    env.pop("GOOGLE_CLOUD_PROJECT", None)
    env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    env.pop("GOOGLE_CLOUD_LOCATION", None)
    if transport == "api":
        # Missing credentials in api mode must be LOUD: an empty env var is
        # falsy to both CLIs → claude would silently fall back to OAuth
        # (billing the subscription while the row says auth_mode=api). The
        # claim gate makes this near-unreachable, but defense-in-depth for
        # this phase's exact failure class. Raise rather than inject "".
        if provider_name == "gemini":
            key = base_env.get("GEMINI_API_KEY")
            if key:
                # AI-Studio key — the primary api path; wins over SA creds.
                env["GEMINI_API_KEY"] = key
            elif base_env.get("GOOGLE_APPLICATION_CREDENTIALS") and base_env.get(
                "GOOGLE_CLOUD_PROJECT"
            ):
                # fleet-api-6: Vertex AI service-account fallback. Verified on
                # gemini-cli 0.46.0 (2026-06-11): USE_VERTEXAI=true selects
                # vertex-ai when no persisted selectedType overrides; location
                # must default to "global" (regional endpoints 404 for this
                # project class). SA vars were scrubbed by the baseline above —
                # re-grant all three explicitly for this branch only.
                env["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
                env["GOOGLE_APPLICATION_CREDENTIALS"] = base_env["GOOGLE_APPLICATION_CREDENTIALS"]
                env["GOOGLE_CLOUD_PROJECT"] = base_env["GOOGLE_CLOUD_PROJECT"]
                env["GOOGLE_CLOUD_LOCATION"] = base_env.get("GOOGLE_CLOUD_LOCATION") or "global"
            else:
                raise AuthEnvError(
                    "transport=api for gemini but GEMINI_API_KEY is unset/empty "
                    "and no Vertex service account is configured "
                    "(GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT)"
                )
        elif provider_name == "claude":
            key = base_env.get("ANTHROPIC_API_KEY")
            if not key:
                raise AuthEnvError(
                    "transport=api for claude but ANTHROPIC_API_KEY is unset/empty"
                )
            env["ANTHROPIC_API_KEY"] = key
        else:
            # kimi/codex/opencode never reach api (blocked at validation)
            raise AuthEnvError(f"transport=api unsupported for {provider_name}")
    else:  # cli baseline
        if provider_name == "gemini":
            env["GOOGLE_GENAI_USE_GCA"] = "true"  # GCA OAuth, wins over any key
        # claude/others: scrubbed keys above IS the whole cli adapter
    return env


# ─────────────────────────────────────────────────────────────────────
# Reactive rate-limit backoff (concurrency-knob-1, Phase 1)
# ─────────────────────────────────────────────────────────────────────

# Terms that mean "rate-limited, retry will likely succeed". Lower-cased match.
# Covers Vertex (429 / RESOURCE_EXHAUSTED / "resource exhausted") and anthropic
# (rate_limit / overloaded_error / 429 / too many requests). Deliberately NOT
# auth (401/403/PERMISSION_DENIED/UNAUTHENTICATED) or truncation (MAX_TOKENS),
# which never self-heal and must bubble up unchanged.
_RATE_LIMIT_TERMS = (
    "429",
    "resource_exhausted",
    "resource exhausted",
    "rate_limit",
    "rate limit",
    "overloaded_error",
    "too many requests",
)

# Terms that mean "transient network / DNS / socket error; retry will likely
# succeed once connectivity is restored".  Lower-cased substring match.
#
# The tuple itself lives in `failure_classifier` — the SINGLE SOURCE OF TRUTH,
# shared with `failure_classifier._TRANSIENT` so this in-spawn retry loop and
# the phase/queue-level retry decisions (`pipeline._run_with_failover`,
# `pipeline._requeue_worthy`) can never drift apart again.  See that tuple's
# comment for the 2026-08-13 httpx incident that the split caused.
# Deliberately NOT auth (401/403/PERMISSION_DENIED/UNAUTHENTICATED) or
# truncation (MAX_TOKENS / "prompt is too long"), which never self-heal.
# Also deliberately NOT matching the Claude session-limit string
# "You've hit your session limit · resets …" — that must propagate unchanged
# so higher layers can detect and auto-pause the worker (tasks 2-5).
_TRANSIENT_NET_TERMS = failure_classifier.TRANSIENT_NET_TERMS


def _is_rate_limited(text: str) -> bool:
    """True iff ``text`` names a transient rate-limit worth retrying.

    Matches Vertex + anthropic rate-limit shapes; never matches auth (401/403)
    or truncation (MAX_TOKENS), which do not self-heal.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(term in lowered for term in _RATE_LIMIT_TERMS)


def _is_transient_net(text: str) -> bool:
    """True iff ``text`` signals a transient network/DNS/socket error.

    Matches the live error shapes observed in flaky-network workers (DNS
    failures, TCP resets, Windows socket errors, urllib3 pool timeouts).
    Never matches auth, truncation, or the Claude session-limit string.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(term in lowered for term in _TRANSIENT_NET_TERMS)


def _rate_limit_delay(
    attempt: int, *, base: float | None = None, cap: float | None = None
) -> float:
    """Exponential backoff with jitter for retry ``attempt`` (0-indexed).

    ``delay = min(base * 2**attempt, cap) + random.uniform(0, base)`` — the
    jitter spreads concurrent retriers so they don't re-collide in lockstep.
    """
    base = settings.rate_limit_base_delay_seconds if base is None else base
    cap = settings.rate_limit_max_delay_seconds if cap is None else cap
    return min(base * (2 ** attempt), cap) + random.uniform(0, base)


async def _spawn(
    *,
    provider: Provider,
    model: Optional[str],
    prompt: str,
    attachments: list[Path],
    transport: str = "cli",
) -> tuple[int, str, dict[str, Any], str]:
    """Run a single provider call with bounded retry-on-rate-limit/net-error.

    Delegates each attempt to :func:`_spawn_once`; on a transient 429/
    rate-limit **or** a transient network/DNS/socket error it backs off
    (``asyncio.sleep`` holds NO concurrency slot — ``_spawn_once`` acquires
    the semaphore internally) and retries up to
    ``settings.rate_limit_max_retries`` times, reusing the same backoff/jitter.
    A persistent error (auth 401/403, truncation, session-limit) returns the
    failure tuple unchanged on the first attempt, exactly as before. A fleet
    slot-exhaustion 429 also returns after ONE attempt (never retried here —
    the pipeline raises SlotSaturation and the worker parks the job).
    """
    for attempt in range(settings.rate_limit_max_retries + 1):
        rc, text, usage, stderr = await _spawn_once(
            provider=provider, model=model, prompt=prompt,
            attachments=attachments, transport=transport,
        )
        combined = stderr or text
        # Fleet slot exhaustion is deliberately 429-shaped, but retrying it
        # in-process re-burns a full credential_slot_wait_seconds wait per
        # attempt (the 600s-timeout burn, queue-correctness-1). Return it
        # unretried — the pipeline converts it to SlotSaturation and the
        # worker parks the job.
        if errors.is_slot_saturation(combined):
            return rc, text, usage, stderr
        is_retryable = _is_rate_limited(combined) or _is_transient_net(combined)
        if rc == 0 or not is_retryable:
            return rc, text, usage, stderr
        if attempt >= settings.rate_limit_max_retries:
            logger.warning(
                f"agent.spawn retryable error, retries exhausted | provider={provider.name}"
            )
            return rc, text, usage, stderr
        delay = _rate_limit_delay(attempt)
        reason = "rate-limited (429)" if _is_rate_limited(combined) else "transient net error"
        logger.warning(
            f"agent.spawn {reason} | provider={provider.name} "
            f"attempt={attempt + 1}/{settings.rate_limit_max_retries} backoff={delay:.1f}s"
        )
        await asyncio.sleep(delay)


# ─────────────────────────────────────────────────────────────────────
# Fleet credential limiter wiring (BE-16 task 5)
# ─────────────────────────────────────────────────────────────────────

# Throttle window for the "credential limiter: BYPASSED" ERROR log — a DB
# outage would otherwise flood the log once per api call (review M3/M8).
_BYPASS_LOG_INTERVAL_S = 60.0
_last_bypass_log_at: float = float("-inf")  # -inf so the FIRST bypass always logs
_bypass_count_since_log: int = 0


def _log_credential_bypass(exc: Exception) -> None:
    """Throttled ERROR log (<=1/60s) for a credential-limiter DB failure that
    forces this call to bypass the fleet-wide slot (proceed unlimited, same
    as no cap configured). Bypasses that land inside a throttle window still
    happen silently — the count is folded into the next line that IS
    logged, so operators can see how bad an outage was without a flood."""
    global _last_bypass_log_at, _bypass_count_since_log
    _bypass_count_since_log += 1
    now = perf_counter()
    if now - _last_bypass_log_at < _BYPASS_LOG_INTERVAL_S:
        return
    logger.error(
        f"credential limiter: BYPASSED ({exc}) "
        f"[{_bypass_count_since_log} bypass(es) since last report]"
    )
    _last_bypass_log_at = now
    _bypass_count_since_log = 0


async def _release_credential_slot(slot: Any) -> None:
    """Release a fleet credential slot. Logs (never raises) a DB error —
    this runs inside a caller's ``finally``, so an error here must NEVER
    mask the model result, an in-flight provider exception, or a
    cancellation propagating through the caller (codex-review #5)."""
    from app.services import credential_limiter
    try:
        await credential_limiter.release(slot)
    except Exception:
        logger.exception("credential limiter: release failed")


async def _spawn_once(
    *,
    provider: Provider,
    model: Optional[str],
    prompt: str,
    attachments: list[Path],
    transport: str = "cli",
) -> tuple[int, str, dict[str, Any], str]:
    """Run the provider's CLI once with ``prompt`` on stdin.

    Returns ``(returncode, result_text, usage, stderr)``. ``usage`` keys:
    ``prompt_tokens``, ``output_tokens``, ``cached_tokens``, ``total_tokens``,
    ``raw`` — exactly what the provider's ``parse_envelope`` returned.
    ``stderr`` is the decoded stderr stream so callers can surface the real
    failure cause (``ModelNotFoundError``, auth errors, etc.) instead of a
    parsed-stdout decoy.
    """
    # transport=api for any API_PROVIDERS member -> direct SDK call, not the
    # CLI. Membership reads app.services.agent_models.API_PROVIDERS (the
    # single source of truth also used by validate_transport/is_valid) so
    # adding a new api-only provider there (e.g. clodex) is a one-place
    # change — no second hardcoded list to keep in sync here. Kept BEFORE
    # _resolve_binary so a pure-API worker needs no CLI on PATH; kept INSIDE
    # _semaphore() so direct-API fan-out is bounded exactly like CLI subprocesses.
    if transport == "api" and provider.name in agent_models.API_PROVIDERS:
        from app.services import api_transport, credential_id, credential_limiter

        # The local process-wide semaphore is entered FIRST, before any
        # fleet-wide credential-slot chain (review fix, task-5 CRITICAL
        # finding): a won fleet slot must never be held while this call
        # queues for a local slot — that would let a caller hog a scarce
        # fleet-wide credential slot for the entire local queueing wait.
        # Entering the semaphore first also means at most
        # `agent_max_concurrency` callers per process ever poll `acquire`
        # concurrently, matching the CLI-subprocess branch below.
        async with _semaphore():
            slot: Any = credential_limiter.BYPASS
            credential = credential_id.credential_for(provider.name, os.environ)
            if credential is not None:
                # Unknown-provider guard (BE-16 task 5, deferred Important from
                # task 4's review; made to actually bite by the final-review
                # fix). `resolve_limit`'s provider -> settings-attr lookup
                # (`getattr(settings, f"credential_max_concurrent_{provider}",
                # 0)`) silently falls back to 0 (== BYPASS, limiter OFF) for a
                # provider name it doesn't recognize. Asserting
                # `provider.name in agent_models.API_PROVIDERS` here was
                # TAUTOLOGICAL — this branch already only runs when that
                # exact condition holds (the `if` above), so it could never
                # fire. The real drift this must catch: a provider added to
                # `API_PROVIDERS` + wired into `credential_for` but with no
                # matching `credential_max_concurrent_<name>` settings field
                # ever added to `Settings` — assert the settings field
                # actually exists so that drift fails LOUD here, never
                # silently degrades into a BYPASS.
                assert hasattr(settings, f"credential_max_concurrent_{provider.name}"), (
                    f"credential limiter: provider {provider.name!r} is in "
                    "API_PROVIDERS and has a credential, but Settings has no "
                    f"credential_max_concurrent_{provider.name} field — "
                    "resolve_limit's getattr(..., 0) default would silently "
                    "return 0 and BYPASS the limiter for this provider"
                )
                try:
                    async with SessionLocal() as session:
                        limit = await credential_limiter.resolve_limit(
                            session, provider.name, credential
                        )
                    slot = await credential_limiter.acquire(
                        credential, limit,
                        wait_budget_s=settings.credential_slot_wait_seconds,
                    )
                except Exception as exc:  # noqa: BLE001 — DB outage degrades, never blocks
                    _log_credential_bypass(exc)
                    slot = credential_limiter.BYPASS

                if slot is None:
                    # Budget exhausted, no slot ever freed up. Shaped exactly
                    # like a provider 429 (`_is_rate_limited` matches "429") so
                    # `_spawn`'s existing backoff/retry loop handles fleet
                    # saturation the same way it handles a real rate limit.
                    return (
                        1, "", {},
                        "429 fleet credential slot wait exhausted "
                        f"(credential={credential}, "
                        f"budget={settings.credential_slot_wait_seconds}s)",
                    )

            try:
                return await api_transport.generate(
                    provider=provider.name, model=model, prompt=prompt,
                    attachments=attachments,
                )
            finally:
                # Shielded release (mirrors worker.py's documented
                # uncancel/shield craft at the cancel-finalize site): a
                # SECOND cancellation delivered while we're awaiting the
                # release must not stop the release itself — it only
                # detaches us from waiting for it; the orphaned task keeps
                # running on the live loop (STALE_TTL is the backstop if it
                # somehow never completes). Nothing is re-raised here — the
                # original outcome of the `try` (a successful return, an
                # in-flight provider exception, or the FIRST cancellation)
                # propagates through this `finally` untouched.
                release_task = asyncio.create_task(_release_credential_slot(slot))
                try:
                    await asyncio.shield(release_task)
                except asyncio.CancelledError:
                    pass

    binary = _resolve_binary(provider)

    # Unique per-call sentinel for the codex last-msg path. Using the system
    # temp dir so we don't need a build_dir; we ``unlink`` it after parsing.
    fd, last_msg = tempfile.mkstemp(suffix=".txt", prefix=f"agent-{provider.name}-")
    os.close(fd)
    last_msg_path = Path(last_msg)

    cmd = provider.build_argv(
        binary=binary,
        model=model,
        last_msg_path=last_msg_path,
        attachments=list(attachments),
    )

    # Force UTF-8 in the child process. Without this, Python-based CLIs (kimi)
    # default to cp1252 on Windows and crash on any non-ASCII output character.
    child_env = _auth_env(provider.name, transport, {**os.environ, "PYTHONIOENCODING": "utf-8"})

    logger.info(
        f"agent.spawn | provider={provider.name} model={model or '<default>'} "
        f"binary={binary} prompt_chars={len(prompt)} attachments={len(attachments)}"
    )

    async with _semaphore():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
            )
        except (OSError, FileNotFoundError) as exc:
            try:
                last_msg_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(
                f"failed to spawn {provider.name} CLI: {exc}"
            ) from exc

        try:
            stdout_b, stderr_b = await proc.communicate(prompt.encode("utf-8"))
        except asyncio.CancelledError:
            kill_tree(proc.pid)   # whole tree - provider CLIs spawn helpers
            try:
                last_msg_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    rc = proc.returncode if proc.returncode is not None else -1
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if stderr:
        # Most providers chatter on stderr (warnings, deprecations); keep at
        # debug so successful runs stay quiet.
        logger.debug(
            f"agent.stderr | provider={provider.name} chars={len(stderr)} "
            f"preview={stderr[:200]!r}"
        )

    try:
        result_text, usage = provider.parse_envelope(
            stdout, last_msg_path=last_msg_path
        )
    finally:
        try:
            last_msg_path.unlink(missing_ok=True)
        except OSError:
            pass

    if rc != 0:
        # Surface the first stderr/stdout snippet so the caller sees *why*.
        snippet = (stderr or stdout)[:500]
        logger.warning(
            f"agent.spawn nonzero | provider={provider.name} rc={rc} "
            f"snippet={snippet!r}"
        )

    return rc, result_text, usage, stderr


# ─────────────────────────────────────────────────────────────────────
# Persistence helper
# ─────────────────────────────────────────────────────────────────────


def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.2f}s"


def _failure_preview(stderr: str, text: str, *, limit: int = 400) -> str:
    """Pick the most informative failure snippet for a non-zero subprocess exit.

    Prefers stderr (where ModelNotFoundError, auth errors, etc. live) over
    parsed stdout text (which may carry decoy warnings like 'MCP issues
    detected'). Strips ANSI / box-drawing noise so a single-line snippet
    fits in a log/UI message. Truncates to ``limit`` chars.
    """
    raw = stderr.strip() or text.strip()
    if not raw:
        return ""
    # Collapse whitespace; drop common terminal box-drawing chars so log
    # lines stay readable.
    cleaned = " ".join(raw.split())
    cleaned = cleaned.replace("─", "").replace("│", "")
    cleaned = cleaned.replace("┌", "").replace("┐", "")
    cleaned = cleaned.replace("└", "").replace("┘", "")
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned


def _spawn_failure_message(provider: str, transport: str, rc: int, stderr: str, text: str) -> str:
    """Transport-aware failure string that INCLUDES the real error preview
    (api stderr carries the 429/DNS/auth cause; the old 'CLI exited rc=N'
    wording dropped it). Used at every rc!=0 record-usage + raise site."""
    word = "api" if transport == "api" else "CLI"
    return f"{provider} {word} call failed rc={rc}: {_failure_preview(stderr, text)}"


def _accounting_model_name(
    provider: str, requested_model: Optional[str], raw: dict[str, Any]
) -> str:
    """Model id used by the cost ledger.

    Clodex can serve a different tier than the requested alias. Attribute the
    token row to the provider-reported model so pricing and budget guards use
    the served tier; ``raw`` retains both ids for audit. Other providers keep
    their existing requested/default behavior.
    """
    if provider == "clodex":
        served = raw.get("served_model")
        if isinstance(served, str) and served.strip():
            return served
    return requested_model or "<default>"


async def _record_usage(
    *,
    operation: str,
    provider: str,
    model_name: Optional[str],
    usage: dict[str, Any],
    duration_s: float,
    started_at: datetime,
    success: bool,
    auth_mode: str = "cli",
    book_id: Optional[UUID] = None,
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
    error_message: Optional[str] = None,
    extra_envelope: Optional[dict[str, Any]] = None,
) -> None:
    """Best-effort write to ``agent_usages``. Failures are logged and swallowed
    so a usage-table outage can't crash a phase mid-flight."""
    raw = dict(usage.get("raw") or {})
    if extra_envelope:
        raw.update(extra_envelope)
    if provider == "clodex" and model_name:
        raw.setdefault("requested_model", model_name)
    accounting_model = _accounting_model_name(provider, model_name, raw)

    try:
        async with SessionLocal() as session:
            await usage_repo.create(
                session,
                operation=operation,
                provider=provider,
                model_name=accounting_model,
                auth_mode=auth_mode,
                book_id=book_id,
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cached_tokens=int(usage.get("cached_tokens") or 0),
                cache_creation_tokens=int(usage.get("cache_creation_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
                raw_envelope=raw or None,
                duration=_format_duration(duration_s),
                success=success,
                error_message=error_message,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            await session.commit()
    except Exception as exc:
        logger.warning(f"failed to record agent_usage: {exc!r}")


# ─────────────────────────────────────────────────────────────────────
# Prompt assembly
# ─────────────────────────────────────────────────────────────────────


def _build_master_prompt(
    *,
    phase_prompt: str,
    phase_name: str,
    lesson_context: Optional[str],
    prior_outputs: Optional[dict[str, str]],
    difficulty: Optional[str],
    schema: Optional[type[BaseModel]],
    provider_suffix: str,
    attachment_preamble: str = "",
    source_map_digest: str = "",
) -> str:
    """Assemble the user-visible prompt the CLI consumes on stdin.

    Layout:
        <attachment_preamble>           ← per-provider; empty for Claude (uses argv)
        <phase_prompt>
        --- LESSON CONTEXT ---
        <lesson_context (or "(none)")>
        --- END LESSON CONTEXT ---
        ## Prior phase outputs
        ### <name>
        <body with SVGs stripped>
        Difficulty: <value or "unspecified">
        <SVG rules if phase emits diagrams>
        <NO_PREAMBLE if not in JSON-schema mode>
        Respond with a single JSON object matching this schema: ... (if schema)
        <provider visual-policy suffix>
    """
    parts: list[str] = []
    if attachment_preamble:
        parts.append(attachment_preamble.rstrip())
    parts.append(phase_prompt.rstrip())

    parts.append("")
    parts.append("--- LESSON CONTEXT ---")
    parts.append(lesson_context.strip() if lesson_context else "(none)")
    parts.append("--- END LESSON CONTEXT ---")

    if source_map_digest:
        parts.append(source_map_digest)

    if prior_outputs:
        parts.append("")
        parts.append("## Prior phase outputs (for cross-phase consistency)")
        for name, body in prior_outputs.items():
            stripped = _strip_svgs(body or "")
            parts.append(f"\n### {name}\n{stripped}")

    parts.append("")
    parts.append(f"Difficulty: {difficulty or 'unspecified'}")

    # Placeholder rules: only for phases that may include a visual. Other
    # phases (memory-sprint, reflection, classify) get a slimmer prompt.
    if phase_name in _VISUAL_PHASES:
        parts.append(_PLACEHOLDER_RULES)

    if schema is not None:
        # JSON-schema mode: the deliverable IS the JSON. Skip NO_PREAMBLE
        # because by definition the response cannot start with prose.
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        parts.append(
            "\n## OUTPUT FORMAT\n"
            "Respond with a single JSON object matching this schema:\n"
            f"{schema_json}\n"
            "Do not include any text outside the JSON. "
            "Do not wrap the JSON in code fences.\n\n"
            "**Curriculum metadata tags** like `[Bloom: LX]`, `[PISA: LX]`, "
            "`[Damage: -X HP]`, `[Difficulty: ...]` must go ONLY in their "
            "matching schema fields. NEVER embed these bracket tags inside "
            "`prompt`, `options`, `explanation`, `front`, `back`, or any "
            "other student-facing string. If the schema has no field for a "
            "particular tag, drop the tag entirely."
        )
    else:
        parts.append(_NO_PREAMBLE)

    if provider_suffix:
        parts.append(provider_suffix)

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────
# Public API: run_phase
# ─────────────────────────────────────────────────────────────────────


async def run_phase(
    *,
    provider: str,
    model: Optional[str],
    phase_prompt: str,
    phase_name: str,
    homework_job_id: Optional[UUID],
    phase_output_id: Optional[UUID],
    lesson_context: Optional[str] = None,
    prior_outputs: Optional[dict[str, str]] = None,
    attachments: list[Path] = (),
    schema: Optional[type[BaseModel]] = None,
    difficulty: Optional[str] = None,
    max_output_tokens: Optional[int] = None,  # noqa: ARG001 — providers ignore today
    source_map_digest: str = "",
    operation: str = "phase.run",
    transport: str = "cli",
) -> PhaseResult:
    """Run one phase and return the result + usage envelope.

    When ``schema`` is provided, the prompt embeds the schema's JSON Schema and
    we attempt ``schema.model_validate_json(text)``. On ``ValidationError`` we
    retry exactly once with the validation error appended to the prompt; both
    attempts are recorded as separate ``AgentUsage`` rows (the failed one with
    ``success=False``).
    """
    prov = get_provider(provider)
    resolved_model = _resolve_model(provider, model)
    suffix = prov.prompt_suffix(None)
    attachment_preamble = prov.format_attachments(list(attachments))

    base_prompt = _build_master_prompt(
        phase_prompt=phase_prompt,
        phase_name=phase_name,
        lesson_context=lesson_context,
        prior_outputs=prior_outputs,
        difficulty=difficulty,
        schema=schema,
        provider_suffix=suffix,
        attachment_preamble=attachment_preamble,
        source_map_digest=source_map_digest,
    )

    attempt_prompt = base_prompt
    last_error: Optional[ValidationError] = None
    parsed_obj: Optional[BaseModel] = None
    last_text = ""
    last_stderr = ""
    last_usage: dict[str, Any] = {}

    # Up to two attempts: schema mode retries on a validation error; markdown
    # mode retries on an empty body (transient rc=0 + blank output).
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        started_at = datetime.now(timezone.utc)
        t0 = perf_counter()

        spawn_failed: Optional[Exception] = None
        rc = -1
        text = ""
        stderr = ""
        usage: dict[str, Any] = {
            "prompt_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "raw": {},
        }
        try:
            rc, text, usage, stderr = await _spawn(
                provider=prov,
                model=resolved_model,
                prompt=attempt_prompt,
                attachments=list(attachments),
                transport=transport,
            )
        except Exception as exc:
            spawn_failed = exc

        duration_s = perf_counter() - t0
        last_text = text
        last_stderr = stderr
        last_usage = usage

        if spawn_failed is not None:
            await _record_usage(
                operation=operation,
                provider=provider,
                model_name=resolved_model,
                usage=usage,
                duration_s=duration_s,
                started_at=started_at,
                success=False,
                auth_mode=transport,
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                error_message=str(spawn_failed),
                extra_envelope={"phase_name": phase_name, "attempt": attempt},
            )
            raise spawn_failed

        if rc != 0:
            err = _spawn_failure_message(provider, transport, rc, stderr, text)
            await _record_usage(
                operation=operation,
                provider=provider,
                model_name=resolved_model,
                usage=usage,
                duration_s=duration_s,
                started_at=started_at,
                success=False,
                auth_mode=transport,
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                error_message=err,
                extra_envelope={"phase_name": phase_name, "attempt": attempt, "error": (stderr or "")[:2000]},
            )
            raise RuntimeError(
                f"phase.run {phase_name}: {err} "
                f":: {_failure_preview(stderr, text)}"
            )

        if schema is None:
            # Markdown-output phase. An empty/whitespace body is a transient CLI
            # failure (rc=0 but blank — e.g. gemini INVALID_STREAM); treat it as
            # a failure and retry once before giving up, rather than storing a
            # blank phase as success.
            if not text.strip():
                await _record_usage(
                    operation=operation,
                    provider=provider,
                    model_name=resolved_model,
                    usage=usage,
                    duration_s=duration_s,
                    started_at=started_at,
                    success=False,
                    auth_mode=transport,
                    homework_job_id=homework_job_id,
                    phase_output_id=phase_output_id,
                    error_message="empty output body",
                    extra_envelope={"phase_name": phase_name, "attempt": attempt},
                )
                logger.warning(
                    f"agent.phase empty body | provider={provider} "
                    f"phase={phase_name} attempt={attempt}"
                )
                if attempt < max_attempts:
                    attempt_prompt = (
                        base_prompt
                        + "\n\nYour previous response was empty. Produce the "
                        "full markdown deliverable for this phase — do not use "
                        "any tools, just write the content."
                    )
                    continue
                raise RuntimeError(
                    f"phase.run {phase_name}: empty output after {attempt} attempts "
                    f":: {_failure_preview(stderr, text)}"
                )

            # Non-empty markdown. Record success, return.
            await _record_usage(
                operation=operation,
                provider=provider,
                model_name=resolved_model,
                usage=usage,
                duration_s=duration_s,
                started_at=started_at,
                success=True,
                auth_mode=transport,
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                extra_envelope={
                    "phase_name": phase_name,
                    "difficulty": difficulty,
                    "attempt": attempt,
                },
            )
            logger.success(
                f"agent.phase done | provider={provider} phase={phase_name} "
                f"output_chars={len(text)} duration_ms={duration_s * 1000:.0f}"
            )
            return PhaseResult(
                text=text,
                parsed=None,
                usage=usage,
                raw_envelope=usage.get("raw") or {},
            )

        # Structured-output path: try Pydantic validation. Be lenient about
        # surrounding whitespace and code-fence wrappers some CLIs add.
        candidate = _strip_code_fences(text).strip()
        try:
            parsed_obj = schema.model_validate_json(candidate)
        except ValidationError as exc:
            last_error = exc
            await _record_usage(
                operation=operation,
                provider=provider,
                model_name=resolved_model,
                usage=usage,
                duration_s=duration_s,
                started_at=started_at,
                success=False,
                auth_mode=transport,
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                error_message=f"schema validation failed: {exc}",
                extra_envelope={
                    "phase_name": phase_name,
                    "schema": schema.__name__,
                    "attempt": attempt,
                    "text_preview": candidate[:200],
                },
            )
            logger.warning(
                f"agent.phase validation failed | provider={provider} "
                f"phase={phase_name} attempt={attempt} schema={schema.__name__} "
                f"err={str(exc)[:200]!r}"
            )
            if attempt < max_attempts:
                attempt_prompt = (
                    base_prompt
                    + "\n\nYour previous response failed schema validation:\n"
                    + str(exc)
                    + "\nRespond with valid JSON matching the schema."
                )
                continue
            # Out of retries — fall through to the raise below.
            break

        # Validated. Record success and return.
        await _record_usage(
            operation=operation,
            provider=provider,
            model_name=resolved_model,
            usage=usage,
            duration_s=duration_s,
            started_at=started_at,
            success=True,
            auth_mode=transport,
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
            extra_envelope={
                "phase_name": phase_name,
                "difficulty": difficulty,
                "schema": schema.__name__,
                "attempt": attempt,
            },
        )
        logger.success(
            f"agent.phase done | provider={provider} phase={phase_name} "
            f"schema={schema.__name__} attempt={attempt} "
            f"duration_ms={duration_s * 1000:.0f}"
        )
        return PhaseResult(
            text=text,
            parsed=parsed_obj,
            usage=usage,
            raw_envelope=usage.get("raw") or {},
        )

    # Schema mode but both attempts failed validation. Typed (not a bare
    # RuntimeError) so the caller can tell "this model cannot author this
    # config" apart from a transport fault and route it to the markdown
    # fallback instead of failing the job.
    raise SchemaValidationExhausted(
        f"phase.run {phase_name}: schema {schema.__name__ if schema else '?'} "
        f"validation failed after {max_attempts} attempts: {last_error} "
        f":: {_failure_preview(last_stderr, last_text)}"
    )


def _strip_code_fences(text: str) -> str:
    """Best-effort unwrap of ```json ... ``` fences some CLIs sprinkle around
    structured output. Returns ``text`` unchanged if no fences are detected."""
    s = text.strip()
    if not s.startswith("```"):
        return text
    # Drop the opening fence line (```json or ```).
    first_nl = s.find("\n")
    if first_nl < 0:
        return text
    body = s[first_nl + 1:]
    # Trim a trailing ``` if present.
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -3]
    return body


_GLYPH_NAME_RE = re.compile(r"/G([0-9A-Fa-f]{2})")


def _decode_glyph_text(text: str) -> str:
    """Recover real text from PDFs whose font subset has no ToUnicode map.

    Such PDFs make pypdf emit glyph names like ``/G55/G6D/G75`` instead of
    characters. Where the glyph code equals the original byte value (a common
    case), ``/G<hex>`` decoded as a cp1252 byte recovers the text
    (``/G55/G6D/G75/G6D/G69/G79`` → ``Umumiy``). Only fires when glyph tokens
    clearly dominate the page, so ordinary text containing an incidental
    ``/G...`` is left untouched.
    """
    if "/G" not in text:
        return text
    if len(_GLYPH_NAME_RE.findall(text)) < 20:
        return text

    def _sub(m: "re.Match[str]") -> str:
        try:
            return bytes([int(m.group(1), 16)]).decode("cp1252", "replace")
        except Exception:
            return ""

    return _GLYPH_NAME_RE.sub(_sub, text)


# Refusal phrases (lowercased) that, when they appear NEAR THE START of a short
# output, mark a non-summary. Anchored to the first chars + short length so a
# legitimate "...ma'lumot mavjud emas" inside a real summary never false-fires.
_EXTRACT_REFUSAL_MARKERS = (
    "ignore pattern", "ignore sozlama", "couldn't read", "could not read",
    "o'qib bo'lmadi", "o`qib bo'lmadi", "konteksti mavjud emas",
    "konteksti bo'sh", "konteksti bo`sh", "manba fayli", "no text layer",
    "no lesson content",
)
_REFUSAL_HEAD_CHARS = 240


# Alphabets a real curriculum textbook is written in: ASCII Latin, the Cyrillic
# block, and the Uzbek modifier letters (ʻ/ʼ and their curly-quote variants).
# Text extracted as the WRONG bytes — cp1251 Cyrillic mis-decoded as latin1
# (mojibake: "Ó÷åáíèê"), or a subset font whose glyph codes don't equal their
# byte values — stays `.isalpha()` but lands mostly OUTSIDE these blocks. Real
# books score ~1.00; the RU-mojibake book f20db30c scores 0.07 (measured). This
# is the signal validate_extract_text's letter-DENSITY ratio cannot see (garbage
# letters are still letters). Below _ALPHA_RATIO_MIN_SAMPLE alphabetic chars we
# cannot judge, so we return 1.0 (plausible) — never false-fire on a tiny slice.
_UZBEK_MODIFIER_LETTERS = frozenset("ʻʼ‘’")
_ALPHA_RATIO_MIN_SAMPLE = 200


def _is_expected_alpha(c: str) -> bool:
    if ("a" <= c <= "z") or ("A" <= c <= "Z"):
        return True
    if 0x0400 <= ord(c) <= 0x04FF:   # Cyrillic
        return True
    return c in _UZBEK_MODIFIER_LETTERS


def _alpha_plausibility_ratio(text: str) -> float:
    """Fraction of alphabetic chars that belong to an alphabet a real textbook is
    written in. 1.0 when there is too little text to judge. See the block comment
    above for why this catches garbage the letter-density ratio passes."""
    letters = [c for c in (text or "") if c.isalpha()]
    if len(letters) < _ALPHA_RATIO_MIN_SAMPLE:
        return 1.0
    good = sum(1 for c in letters if _is_expected_alpha(c))
    return good / len(letters)


# A worked-example expression worth grounding: a run of digits/letters/parens/
# operators that has a structural math operator ('/' or '=') AND (a digit OR a
# parenthesis). The parenthesis arm is load-bearing: audited drift #2 is a
# DIGITLESS invented example — (a−b)/(a+b), (a−b)²/(a+b) — which a digit-only
# gate would never surface (and the flash verify can only catch what this
# surfaces). Prose like 'va/yoki' has '/' but no digit and no paren → excluded,
# so the false-positive posture (bare numbers, page ranges) is preserved.
_FIDELITY_EXPR_RE = re.compile(r"[0-9A-Za-z()][0-9A-Za-z()/=+\-−–.·*×÷²³]{1,38}")
_FIDELITY_MAX_CANDIDATES = 12


def _normalize_expr(s: str) -> str:
    out = s.lower().replace(" ", "")
    for ch in "−–—":          # minus variants → ascii hyphen
        out = out.replace(ch, "-")
    for ch in "·*×":          # multiplication variants
        out = out.replace(ch, "*")
    out = out.replace("÷", "/")
    return out


def extract_math_expressions(text: str) -> set[str]:
    """Normalized fraction/equation expressions in `text`: contain '/' or '=' AND
    (a digit OR a parenthesis). Captures both numeric (21/100, x=5) and digitless
    algebraic (a−b)/(a+b) worked examples; skips prose slashes (va/yoki)."""
    found: set[str] = set()
    for m in _FIDELITY_EXPR_RE.findall(text or ""):
        # char class allows '.' (decimals like 3.14) so a token can trail a
        # sentence period ("21/120.") — strip surrounding punctuation first.
        m = m.strip(".,;:")
        if ("/" not in m) and ("=" not in m):
            continue
        if any(c.isdigit() for c in m) or ("(" in m) or (")" in m):
            found.add(_normalize_expr(m))
    return {e for e in found if len(e) >= 3}


def extract_fidelity_candidates(summary: str, book_text: str, strict: bool = False) -> list[str]:
    """Worked-example expressions in the extract SUMMARY that do not appear in the
    source BOOK_TEXT — candidate transcription drift. Free (no model call).
    Grounds against the FULL book_text (conservative → fewer wasted verify calls).
    An empty list means the deterministic pass found nothing to verify.

    `strict=True` (languages/humanities subjects — see pipeline._STRICT_FIDELITY_FAMILIES)
    additionally requires a digit OR an '=' — the bare parenthesis arm that
    exists to catch digitless algebra like (a−b)/(a+b) also catches prose
    glosses like (likes/dislikes) on those subjects, which are pure noise.
    Measured: every dropped gloss across the corpus contains '/' and none
    contains '=', so keeping '=' costs nothing on measured data while still
    covering a digitless '='-formula in a humanities subject with no corpus
    data today (economics/law/pre-conscription). Applied BEFORE the
    _FIDELITY_MAX_CANDIDATES slice so a flood of glosses cannot crowd a real
    digit-bearing hit out of the capped list."""
    norm_book = _normalize_expr(book_text or "")
    cands = sorted(e for e in extract_math_expressions(summary) if e not in norm_book)
    if strict:
        cands = [c for c in cands if any(ch.isdigit() for ch in c) or "=" in c]
    return cands[:_FIDELITY_MAX_CANDIDATES]


class ExtractFidelityVerdict(BaseModel):
    """Model verdict for the extract-fidelity check. `mismatches` is empty when
    every suspect expression is faithfully grounded in the source."""
    mismatches: list[str] = Field(default_factory=list)


_VERIFY_FIDELITY_PROMPT = (
    "You are checking a LESSON SUMMARY against the SOURCE TEXTBOOK TEXT it was "
    "written from. Below are SUSPECT expressions found in the summary that a "
    "quick scan could not locate in the source. For each, decide whether the "
    "summary faithfully reflects a worked example / value from the source. "
    "Report ONLY genuine transcription errors — a value or worked example the "
    "summary states that CONTRADICTS the source (e.g. summary '-3/(2a)' vs "
    "source '-3/a', or an example the source does not contain). Do NOT report a "
    "value the source simply phrases differently, rounds, or that the summary "
    "legitimately derived. For each real error, add one line "
    "'summary says X; source has Y'. If all are fine, return an empty list.\n\n"
    "SUSPECT EXPRESSIONS:\n{suspects}\n\n"
    "===== LESSON SUMMARY =====\n{summary}\n===== END SUMMARY =====\n\n"
    "===== SOURCE TEXTBOOK TEXT =====\n{book_text}\n===== END SOURCE ====="
)


async def verify_extract_fidelity(
    *, summary: str, book_text: str, candidates: list[str],
    provider: str, model: Optional[str], transport: str,
    homework_job_id: Optional[UUID], phase_output_id: Optional[UUID],
) -> list[str]:
    """One structured gemini-flash call: which of `candidates` are real extract
    transcription errors vs the source. Returns confirmed mismatch descriptions
    (empty = clean). Never raises for a bad verdict — on any failure returns []
    (fail-open: the guard must never block or corrupt a good extract)."""
    if not candidates:
        return []
    prompt = _VERIFY_FIDELITY_PROMPT.format(
        suspects="\n".join(f"- {c}" for c in candidates),
        summary=summary, book_text=book_text,
    )
    try:
        result = await run_phase(
            provider=provider, model=model, phase_prompt=prompt,
            phase_name="lesson.extract.verify", schema=ExtractFidelityVerdict,
            homework_job_id=homework_job_id, phase_output_id=phase_output_id,
            operation="lesson.extract.verify", transport=transport,
        )
    except Exception as exc:
        logger.warning(f"agent.verify_extract_fidelity failed (fail-open): {exc!r}")
        return []
    parsed = result.parsed
    if isinstance(parsed, ExtractFidelityVerdict):
        return [m for m in parsed.mismatches if m.strip()]
    return []


class ExtractCoverageMiss(BaseModel):
    """One core teachable item the SOURCE lesson has and the extract dropped."""
    label: str
    central: bool = False


class ExtractCoverageVerdict(BaseModel):
    """Model verdict for the extract-completeness check. `missing` is empty when
    the extract captures everything the lesson teaches."""
    missing: list[ExtractCoverageMiss] = Field(default_factory=list)


_CHECK_COVERAGE_PROMPT = (
    "You are checking whether a LESSON SUMMARY is COMPLETE with respect to the "
    "SOURCE textbook text it was written from. Downstream homework generators "
    "read ONLY the summary — they never see the source — so anything the "
    "summary omits can never be taught.\n\n"
    "The SOURCE below is a printed page window containing the lesson titled "
    '"{title}" (section {number}). The window may also contain fragments of the '
    "NEIGHBOURING lessons — ignore anything that is not part of that lesson.\n\n"
    "List the CORE teachable items of THAT lesson that the SUMMARY does NOT "
    "capture — what a student is expected to learn, recall or apply:\n"
    "- concepts / terms the lesson defines\n"
    "- rules / theorems / formulas the lesson states\n"
    "- WORKED-EXAMPLE and problem TYPES the lesson demonstrates (what the "
    "student must be able to solve) — these are dropped most often, so check "
    "them explicitly\n"
    "- key facts (dates, names, classifications) the lesson teaches\n\n"
    "Rules:\n"
    "- Report an item ONLY if it is genuinely absent from the SUMMARY. Different "
    "wording, a shorter phrasing, or a more general statement that still covers "
    "the item is NOT a miss.\n"
    "- Do NOT report items belonging to a neighbouring lesson, nor background "
    "the lesson only mentions in passing.\n"
    "- Set `central` true ONLY for primary teaching points; secondary or "
    "supporting details are false.\n"
    "- `label` is a short name for the item, in the lesson's language.\n"
    "- If the summary captures everything, return an empty list.\n\n"
    "===== LESSON SUMMARY =====\n{summary}\n===== END SUMMARY =====\n\n"
    "===== SOURCE TEXTBOOK TEXT =====\n{source}\n===== END SOURCE ====="
)


async def check_extract_coverage(
    *, summary: str, source_text: str, section_title: str, section_number: str,
    provider: str, model: Optional[str], transport: str,
    homework_job_id: Optional[UUID], phase_output_id: Optional[UUID],
) -> list[ExtractCoverageMiss]:
    """One structured call: which core items of the SOURCE lesson are absent
    from the extract SUMMARY. WARN-ONLY — the caller records the result and
    never acts on it. Never raises: on any failure returns [] (fail-open, the
    same contract as verify_extract_fidelity)."""
    if not (summary or "").strip() or not (source_text or "").strip():
        return []
    prompt = _CHECK_COVERAGE_PROMPT.format(
        title=section_title, number=section_number,
        summary=summary, source=source_text,
    )
    try:
        result = await run_phase(
            provider=provider, model=model, phase_prompt=prompt,
            phase_name="lesson.extract.coverage", schema=ExtractCoverageVerdict,
            homework_job_id=homework_job_id, phase_output_id=phase_output_id,
            operation="lesson.extract.coverage", transport=transport,
        )
    except Exception as exc:  # noqa: BLE001 — advisory: must never fail a job
        logger.warning(f"agent.check_extract_coverage failed (fail-open): {exc!r}")
        return []
    parsed = result.parsed
    if isinstance(parsed, ExtractCoverageVerdict):
        return [m for m in parsed.missing if (m.label or "").strip()]
    return []


def validate_extract_text(text: str) -> Optional[str]:
    """Gate A — deterministic check on the RAW local PDF text. Returns a failure
    reason string, or None if the text looks like real, readable content. A
    failure marks the local text unusable (scanned / broken-font / garbled); the
    pipeline routes it to a vision extract, which fails loud only if it also
    cannot read the pages."""
    stripped = (text or "").strip()
    if len(stripped) < settings.extract_min_text_chars:
        return f"unreadable PDF (no text layer): only {len(stripped)} chars extracted"
    letters = sum(c.isalpha() for c in stripped)
    # Ratio over VISIBLE (non-whitespace) chars, not total. Math/science books
    # extract with huge layout whitespace (equations, spacing) — dividing by
    # total wrongly sank a readable algebra book to 0.44 and terminal-failed it.
    # "of the actual glyphs, how many are letters" is the real readability
    # signal and is immune to layout whitespace. Real books score >=0.68 here;
    # broken-font / glyph soup still scores <0.40, so the 0.55 floor separates
    # them cleanly.
    visible = sum(1 for c in stripped if not c.isspace())
    ratio = letters / visible if visible else 0.0
    if ratio < settings.extract_min_printable_ratio:
        return f"unreadable PDF (no text layer): printable-letter ratio {ratio:.2f}"
    plaus = _alpha_plausibility_ratio(stripped)
    if plaus < settings.extract_min_alpha_ratio:
        return f"garbled PDF text layer: alphabet-plausibility {plaus:.2f}"
    return None


def validate_extract_summary(summary: str) -> Optional[str]:
    """Gate B — structural validity of a produced extract contract. Returns a
    failure reason, or None if it looks like a real extract. A failure triggers
    failover (the run_fn raises ExtractRefusal).

    Refusal markers always fail. Otherwise a parseable enumerated contract is
    valid regardless of length (a compact lesson is legitimately short — the old
    char-floor false-failed it). Only when NO contract parses do we fall back to
    a low length floor to reject near-empty / unformatted-refusal output."""
    stripped = (summary or "").strip()
    head = stripped[:_REFUSAL_HEAD_CHARS].lower()
    for marker in _EXTRACT_REFUSAL_MARKERS:
        if marker in head:
            return f"refusal marker in summary head: {marker!r}"
    if content_lint.contract_has_items(stripped):
        return None
    if len(stripped) < settings.extract_min_summary_chars:
        return f"summary too short ({len(stripped)} chars) and no contract sections — likely a refusal"
    return None


def _read_pdf_pages(
    reader, indices, *, budget: int, already: set[int], pdf_name: str
) -> tuple[list[str], list[int]]:
    """Read text from the given 1-based page ``indices`` into labeled chunks,
    stopping once ``budget`` chars are consumed. Skips pages in ``already`` so
    overlapping windows never emit the same page twice. Returns
    ``(chunks, pages_read)``."""
    chunks: list[str] = []
    pages_read: list[int] = []
    chars = 0
    for idx in indices:
        if idx in already:
            continue
        page = reader.pages[idx - 1]
        try:
            page_text = _decode_glyph_text(page.extract_text() or "").strip()
        except Exception as exc:
            logger.debug(f"toc text extraction skipped page {idx} of {pdf_name}: {exc!r}")
            continue
        if not page_text:
            continue
        chunk = f"\n\n--- PDF page {idx} ---\n{page_text}"
        remaining = budget - chars
        if remaining <= 0:
            break
        if len(chunk) > remaining:
            chunk = chunk[:remaining]
        chunks.append(chunk)
        chars += len(chunk)
        pages_read.append(idx)
        if chars >= budget:
            break
    return chunks, pages_read


# Read a MARGIN past the gate threshold so an oversize book is DETECTABLE.
# _read_pdf_pages truncates exactly to its budget, so reading only to the
# threshold would make "len > threshold" never fire (the strip even drops 2
# chars) → the size gate would silently miss every oversize book.
_EXTRACT_OVERSIZE_MARGIN = 65_536


def read_whole_book_text(pdf_path: Path) -> str:
    """Read the WHOLE book's text locally via pypdf (no CLI, no file-read by any
    model → dodges the gitignore block and the >20MB CLI ceiling). Reads up to
    settings.extract_max_text_chars + _EXTRACT_OVERSIZE_MARGIN so the size gate
    can SEE overflow. Glyph-decoded per page. '' if the PDF yields no text."""
    reader = PdfReader(str(pdf_path))
    n = len(reader.pages)
    chunks, _pages = _read_pdf_pages(
        reader,
        range(1, n + 1),
        budget=settings.extract_max_text_chars + _EXTRACT_OVERSIZE_MARGIN,
        already=set(),
        pdf_name=pdf_path.name,
    )
    return "".join(chunks).strip()


def read_page_range_text(pdf_path: Path, page_start: int, page_end: int, *, margin: int = 0) -> str:
    """Glyph-decoded text for printed pages [page_start-margin .. page_end+margin],
    clamped to the PDF's real [1..n] page range, budgeted to extract_max_text_chars.
    Returns '' if the range yields no text. Built on _read_pdf_pages."""
    reader = PdfReader(str(pdf_path))
    n = len(reader.pages)
    start = max(1, page_start - margin)
    end = min(n, page_end + margin)
    if start > end:
        return ""
    chunks, _pages = _read_pdf_pages(
        reader,
        range(start, end + 1),
        budget=settings.extract_max_text_chars,
        already=set(),
        pdf_name=pdf_path.name,
    )
    return "".join(chunks).strip()


def pdf_page_count(pdf_path: Path) -> int:
    """Physical page count of the PDF; 0 on any read error."""
    try:
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 0


def extract_text_is_too_sparse(text: str, n_pages: int) -> bool:
    """True if the local text averages fewer than settings.extract_min_chars_per_page
    chars per page — a 'has some header/watermark text but the lesson bodies are
    image-only' scan that Gate A's absolute floor misses. False when n_pages<=0
    (can't judge) so it never fires spuriously."""
    if n_pages <= 0:
        return False
    return len((text or "").strip()) / n_pages < settings.extract_min_chars_per_page


def _toc_text_is_usable(toc_source_text: str, pages_scanned: int) -> bool:
    """True when the locally-extracted TOC text is present, dense enough (not a
    watermark-only scan), AND written in a real alphabet (not mojibake/glyph
    garbage). Any failure → the caller vision-attaches the printed contents page."""
    if not toc_source_text:
        return False
    if extract_text_is_too_sparse(toc_source_text, pages_scanned):
        return False
    return _alpha_plausibility_ratio(toc_source_text) >= settings.extract_min_alpha_ratio


def _toc_pages_scanned(total_pages: int) -> int:
    """Pages the TOC text-scan COVERS (front window + non-overlapping tail) — the
    correct denominator for sparseness. Using pages-that-yielded-text instead
    lets a few dense cover/watermark pages mask an otherwise image-only scan
    (the scanned RU-textbook bug: 3317 chars over 8 text pages reads as 414/page
    and skips vision, when the same text over the 55 scanned pages is 60/page)."""
    front = min(total_pages, _TOC_TEXT_MAX_PAGES)
    tail = min(_TOC_TAIL_PAGES, max(0, total_pages - front))
    return front + tail


def extract_text_is_oversize(text: str) -> bool:
    """True if the local text exceeds the whole-text budget → terminal 'too
    large, needs subset'. Pure (unit-testable); read_whole_book_text reads a
    margin past the budget so this comparison is meaningful for huge books."""
    return len((text or "").strip()) > settings.extract_max_text_chars


def _extract_toc_source_text(pdf_path: Path) -> tuple[str, dict[str, Any]]:
    """Extract a bounded text slice from BOTH ends of the PDF for TOC prompts.

    Gemini CLI's file-reading path can reject large PDFs before the model sees
    the contents, so feeding a compact local text excerpt is faster and avoids
    provider file limits. Crucially we scan the front pages AND the last
    ``_TOC_TAIL_PAGES`` pages: many textbooks (esp. Uzbek "Mundarija") print
    their table of contents at the BACK of the book, and a front-only scan
    returned 0 entries for those. The tail gets a reserved char budget so a
    dense front matter can't starve it.
    """
    meta: dict[str, Any] = {
        "source": "pdf_text",
        "max_pages": _TOC_TEXT_MAX_PAGES,
        "max_chars": _TOC_TEXT_MAX_CHARS,
        "tail_pages": _TOC_TAIL_PAGES,
    }
    try:
        from pypdf import PdfReader
    except Exception as exc:
        meta["error"] = f"pypdf unavailable: {exc}"
        return "", meta

    try:
        reader = PdfReader(str(pdf_path))
        total_pages = len(reader.pages)
        meta["total_pages"] = total_pages

        # Front scan: pages 1..N, capped so it can't consume the tail's budget.
        front_budget = _TOC_TEXT_MAX_CHARS - _TOC_TAIL_MAX_CHARS
        front_indices = range(1, min(total_pages, _TOC_TEXT_MAX_PAGES) + 1)
        front_chunks, front_pages = _read_pdf_pages(
            reader, front_indices, budget=front_budget, already=set(), pdf_name=pdf_path.name
        )

        # Tail scan: the last _TOC_TAIL_PAGES pages not already read by the front
        # scan (overlap on short books is skipped). Gets whatever budget remains.
        # Read from the LAST page BACKWARD so the very end of the book — where a
        # back-of-book "Mundarija" usually sits — is captured first if the budget
        # runs out, then present the chunks in ascending page order.
        already = set(front_pages)
        tail_start = max(1, total_pages - _TOC_TAIL_PAGES + 1)
        tail_indices = list(range(total_pages, tail_start - 1, -1))
        tail_budget = _TOC_TEXT_MAX_CHARS - sum(len(c) for c in front_chunks)
        tail_chunks, tail_pages = _read_pdf_pages(
            reader, tail_indices, budget=tail_budget, already=already, pdf_name=pdf_path.name
        )
        if tail_pages:
            _ordered = sorted(zip(tail_pages, tail_chunks), key=lambda kv: kv[0])
            tail_pages = [p for p, _ in _ordered]
            tail_chunks = [c for _, c in _ordered]
    except Exception as exc:
        meta["error"] = str(exc)
        return "", meta

    text = "".join(front_chunks + tail_chunks).strip()
    meta["front_pages"] = front_pages
    meta["tail_pages_read"] = tail_pages
    meta["pages_read"] = len(front_pages) + len(tail_pages)
    meta["pages_scanned"] = _toc_pages_scanned(total_pages)
    meta["chars"] = len(text)
    return text, meta


# ─────────────────────────────────────────────────────────────────────
# Public API: TOC extraction
# ─────────────────────────────────────────────────────────────────────


async def extract_toc(
    *,
    provider: str,
    model: Optional[str],
    pdf_path: Path,
    subject: str,
    book_id: UUID,
    transport: str = "cli",
) -> ExtractedTOC:
    """Extract a table of contents from a textbook PDF.

    Builds a JSON-Schema-constrained prompt referencing ``ExtractedTOC``,
    attaches the PDF via ``provider.build_argv`` (Claude consumes it; other
    providers may ignore), runs through the subprocess driver, and parses
    the result into the ``ExtractedTOC`` model. Persists an
    ``operation='toc.extract'`` ``AgentUsage`` row.
    """
    prov = get_provider(provider)
    resolved_model = _resolve_model(provider, model)

    toc_source_text, toc_source_meta = _extract_toc_source_text(pdf_path)
    has_local_toc_text = bool(toc_source_text)
    if has_local_toc_text:
        logger.info(
            f"agent.toc source text | pdf={pdf_path.name} "
            f"pages_read={toc_source_meta.get('pages_read')} "
            f"chars={toc_source_meta.get('chars')}"
        )
    else:
        logger.warning(
            f"agent.toc source text unavailable | pdf={pdf_path.name} "
            f"reason={toc_source_meta.get('error') or 'no text extracted'}"
        )

    instruction = (
        f"You are reading a {subject} curriculum textbook. "
        "Extract the full Table of Contents as structured JSON. "
        "For every numbered section (e.g., §1, §2 ... or '1.1', '1.2' ...), "
        "produce one entry with: chapter_number (text), chapter_title, "
        "section_number, section_title, page_start, page_end. "
        "Some titles may be corrupted by a broken PDF font: the letters are "
        "correct but the LETTER-CASE is scrambled and spurious spaces are "
        "injected mid-word (e.g. 'KUr SININg e Ng MUh IM' should read "
        "'Kursining Eng Muhim'), most often in chapter/theme headings. ONLY "
        "when a title shows this scrambling, reconstruct it into clean, "
        "normally-spaced Uzbek. Leave every title that already reads normally "
        "exactly as printed — do not change its casing, spacing, or wording. "
        "If the book is organized by chapters, use the chapter title as "
        "'chapter_title' for every section under it. Do not invent sections. "
        "Order entries as they appear. If the source text does not contain "
        "a readable table of contents, return {\"entries\": []}; do not "
        "explain that you cannot read the PDF."
    )

    toc_text_usable = _toc_text_is_usable(
        toc_source_text, toc_source_meta.get("pages_scanned", 0)
    )
    window: Optional[Path] = None
    toc_mode = "attachment"
    if not toc_text_usable:
        # Scanned / sparse-text TOC: the local excerpt is watermark junk that would
        # steer the model to return []. Drop it and vision-attach a bounded front+back
        # page-window so the provider OCRs the printed contents page (front OR back).
        window = _toc_source_pdf(
            pdf_path, settings.extract_toc_front_pages, settings.extract_toc_back_pages
        )
        lesson_context = None
        if window is None:
            # cannot build a window → no attachment; 0 entries will fail loud + actionable
            attachment_preamble, attachments = "", []
        else:
            logger.info(
                f"agent.toc | sparse/scanned text "
                f"({toc_source_meta.get('chars')} chars / {toc_source_meta.get('pages_read')} pages) "
                f"→ vision-attach front {settings.extract_toc_front_pages} + "
                f"back {settings.extract_toc_back_pages} pages"
            )
            attachment_preamble = prov.format_attachments([window])
            attachments = [window]
            if not (transport == "api" and provider == "gemini"):
                transport = "cli"   # vision needs attachments; api PDF-attach only for gemini
            toc_mode = "vision_toc"
    else:
        lesson_context = (
            "Locally extracted text from the FIRST and LAST pages of the PDF "
            "follows (a textbook's table of contents may be at the front OR the "
            "back). Use this text to identify TOC entries and printed page "
            "numbers. The headings `--- PDF page N ---` are physical PDF page "
            "markers, not textbook page numbers.\n\n"
            f"{toc_source_text}"
        )
        attachment_preamble = prov.format_attachments([pdf_path])
        attachments = [pdf_path]
        try:
            pdf_size = pdf_path.stat().st_size
        except OSError:
            pdf_size = _GEMINI_PDF_MAX_BYTES + 1
        # api transport is text-only (api_transport raises on any attachment):
        # drop the PDF and rely on the local TOC text in lesson_context.
        keep_pdf = (
            transport != "api"
            and provider == "gemini"
            and pdf_size <= _GEMINI_PDF_MAX_BYTES
        )
        if not keep_pdf:
            attachment_preamble = ""
            attachments = []

    base_prompt = _build_master_prompt(
        phase_prompt=instruction,
        phase_name="toc.extract",
        lesson_context=lesson_context,
        prior_outputs=None,
        difficulty=None,
        schema=ExtractedTOC,
        provider_suffix=prov.prompt_suffix(None),
        attachment_preamble=attachment_preamble,
    )

    attempt_prompt = base_prompt
    last_error: Optional[ValidationError] = None
    last_text = ""
    last_stderr = ""
    max_attempts = 2

    try:
        for attempt in range(1, max_attempts + 1):
            started_at = datetime.now(timezone.utc)
            t0 = perf_counter()
            spawn_failed: Optional[Exception] = None
            rc = -1
            text = ""
            stderr = ""
            usage: dict[str, Any] = {
                "prompt_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
                "raw": {},
            }
            try:
                rc, text, usage, stderr = await _spawn(
                    provider=prov,
                    model=resolved_model,
                    prompt=attempt_prompt,
                    attachments=attachments,
                    transport=transport,
                )
            except Exception as exc:
                spawn_failed = exc

            duration_s = perf_counter() - t0
            last_text = text
            last_stderr = stderr

            usage_extra = {
                "subject": subject,
                "pdf": pdf_path.name,
                "attempt": attempt,
                "source": "vision_toc" if toc_mode == "vision_toc" else ("local_pdf_text" if has_local_toc_text else "attachment"),
                "source_meta": toc_source_meta,
            }

            if spawn_failed is not None:
                await _record_usage(
                    operation="toc.extract",
                    provider=provider,
                    model_name=resolved_model,
                    usage=usage,
                    duration_s=duration_s,
                    started_at=started_at,
                    success=False,
                    auth_mode=transport,
                    book_id=book_id,
                    error_message=str(spawn_failed),
                    extra_envelope=usage_extra,
                )
                raise spawn_failed

            if rc != 0:
                err = f"{provider} CLI exited rc={rc}"
                await _record_usage(
                    operation="toc.extract",
                    provider=provider,
                    model_name=resolved_model,
                    usage=usage,
                    duration_s=duration_s,
                    started_at=started_at,
                    success=False,
                    auth_mode=transport,
                    book_id=book_id,
                    error_message=err,
                    extra_envelope=usage_extra,
                )
                raise RuntimeError(
                    f"toc.extract: {err} :: {_failure_preview(stderr, text)}"
                )

            candidate = _strip_code_fences(text).strip()
            try:
                toc = ExtractedTOC.model_validate_json(candidate)
            except ValidationError as exc:
                last_error = exc
                await _record_usage(
                    operation="toc.extract",
                    provider=provider,
                    model_name=resolved_model,
                    usage=usage,
                    duration_s=duration_s,
                    started_at=started_at,
                    success=False,
                    auth_mode=transport,
                    book_id=book_id,
                    error_message=f"schema validation failed: {exc}",
                    extra_envelope={**usage_extra, "text_preview": candidate[:200]},
                )
                logger.warning(
                    f"agent.toc validation failed | provider={provider} "
                    f"attempt={attempt} err={str(exc)[:200]!r}"
                )
                if attempt < max_attempts:
                    attempt_prompt = (
                        base_prompt
                        + "\n\nYour previous response failed schema validation:\n"
                        + str(exc)
                        + "\nRespond with valid JSON matching the schema. "
                        + "If the TOC cannot be extracted, return {\"entries\": []}."
                    )
                    continue
                break

            await _record_usage(
                operation="toc.extract",
                provider=provider,
                model_name=resolved_model,
                usage=usage,
                duration_s=duration_s,
                started_at=started_at,
                success=True,
                auth_mode=transport,
                book_id=book_id,
                extra_envelope={**usage_extra, "entries": len(toc.entries)},
            )
            logger.success(
                f"agent.toc done | provider={provider} subject={subject} "
                f"entries={len(toc.entries)} duration_ms={duration_s * 1000:.0f}"
            )
            return toc

        raise RuntimeError(
            f"toc.extract: ExtractedTOC validation failed after {max_attempts} "
            f"attempts: {last_error} :: {_failure_preview(last_stderr, last_text)}"
        )
    finally:
        if window is not None:
            try:
                window.unlink()
            except OSError:
                pass



async def validate_toc(
    *,
    entries: list[TOCEntryExtracted],
    pdf_path: Path,
    subject: str,
    book_id: UUID,
    provider: str,
    model: Optional[str],
    transport: str = "cli",
) -> TOCValidationResult:
    """Vision-check extracted TOC entries against the textbook's printed contents page.

    Builds a bounded front+back page window (same as ``extract_toc``), makes a
    one-shot Gemini-flash vision call constrained to ``TOCValidation``, and
    returns a ``TOCValidationResult``.

    Designed to degrade gracefully: ANY failure (window build, spawn error,
    rc != 0, parse error) returns status="skipped" rather than raising.  The
    caller treats a skipped verdict as "unverified" and continues the pipeline.
    """
    prov = get_provider(provider)
    resolved_model = _resolve_model(provider, model)

    # Build the front+back page window PDF.
    window = _toc_source_pdf(
        pdf_path, settings.extract_toc_front_pages, settings.extract_toc_back_pages
    )
    if window is None:
        return TOCValidationResult(
            status="skipped",
            confidence=None,
            issues=[],
            detail="no contents-page window",
        )

    # Everything from here is inside try/finally so ANY failure (prompt build,
    # spawn, parse) returns "skipped" and the temp window is always unlinked —
    # validate_toc must never raise into toc_extractor.run (hard invariant).
    try:
        # Vision transport rule — mirrors extract_toc:1376–1377.
        # api PDF-attach only works for gemini; all other paths use cli.
        if not (transport == "api" and provider == "gemini"):
            transport = "cli"

        attachment_preamble = prov.format_attachments([window])
        attachments: list[Path] = [window]

        # Build the compact entry list for the prompt.
        entry_lines = "\n".join(
            f"{e.section_number or ''}  {e.section_title}  p.{e.page_start or '?'}"
            for e in entries
        )
        instruction = (
            f"You are reviewing a {subject} curriculum textbook. "
            "The attached pages are the textbook's printed contents page(s). "
            "The extracted TOC entries below should faithfully reflect what is printed. "
            "Decide whether the extraction is correct. "
            "Return mismatch ONLY if entries are clearly wrong, garbled, invented, "
            "or if major sections are missing. "
            "Minor ordering differences or small page-number discrepancies are verified.\n\n"
            "Extracted entries:\n"
            f"{entry_lines}"
        )

        prompt = _build_master_prompt(
            phase_prompt=instruction,
            phase_name="toc.validate",
            lesson_context=None,
            prior_outputs=None,
            difficulty=None,
            schema=TOCValidation,
            provider_suffix=prov.prompt_suffix(None),
            attachment_preamble=attachment_preamble,
        )

        started_at = datetime.now(timezone.utc)
        t0 = perf_counter()
        usage: dict[str, Any] = {
            "prompt_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "raw": {},
        }

        try:
            rc, text, usage, stderr = await _spawn(
                provider=prov,
                model=resolved_model,
                prompt=prompt,
                attachments=attachments,
                transport=transport,
            )
        except Exception as exc:
            duration_s = perf_counter() - t0
            await _record_usage(
                operation="toc.validate",
                provider=provider,
                model_name=resolved_model,
                usage=usage,
                duration_s=duration_s,
                started_at=started_at,
                success=False,
                auth_mode=transport,
                book_id=book_id,
                error_message=str(exc),
            )
            logger.warning(f"agent.validate_toc spawn error | {exc!r}")
            return TOCValidationResult(
                status="skipped",
                confidence=None,
                issues=[],
                detail=f"spawn error: {str(exc)[:200]}",
            )

        duration_s = perf_counter() - t0

        if rc != 0:
            err = f"{provider} CLI exited rc={rc}"
            await _record_usage(
                operation="toc.validate",
                provider=provider,
                model_name=resolved_model,
                usage=usage,
                duration_s=duration_s,
                started_at=started_at,
                success=False,
                auth_mode=transport,
                book_id=book_id,
                error_message=err,
            )
            logger.warning(f"agent.validate_toc {err}")
            return TOCValidationResult(
                status="skipped",
                confidence=None,
                issues=[],
                detail=err,
            )

        candidate = _strip_code_fences(text).strip()
        try:
            toc_validation = TOCValidation.model_validate_json(candidate)
        except Exception as exc:
            await _record_usage(
                operation="toc.validate",
                provider=provider,
                model_name=resolved_model,
                usage=usage,
                duration_s=duration_s,
                started_at=started_at,
                success=False,
                auth_mode=transport,
                book_id=book_id,
                error_message=f"parse error: {exc}",
            )
            logger.warning(
                f"agent.validate_toc parse error | provider={provider} err={str(exc)[:200]!r}"
            )
            return TOCValidationResult(
                status="skipped",
                confidence=None,
                issues=[],
                detail=f"parse error: {str(exc)[:200]}",
            )

        await _record_usage(
            operation="toc.validate",
            provider=provider,
            model_name=resolved_model,
            usage=usage,
            duration_s=duration_s,
            started_at=started_at,
            success=True,
            auth_mode=transport,
            book_id=book_id,
        )
        status = "mismatch" if toc_validation.verdict == "mismatch" else "verified"
        detail = "; ".join(toc_validation.issues)[:1000]
        logger.info(
            f"agent.validate_toc done | provider={provider} subject={subject} "
            f"status={status} confidence={toc_validation.confidence} "
            f"issues={len(toc_validation.issues)} duration_ms={duration_s * 1000:.0f}"
        )
        return TOCValidationResult(
            status=status,
            confidence=toc_validation.confidence,
            issues=toc_validation.issues,
            detail=detail,
        )

    except Exception as exc:
        # Pre-spawn failures (format_attachments, prompt build) or any
        # uncaught error — degrade to skipped rather than raising.
        logger.warning(f"agent.validate_toc unexpected error | {exc!r}")
        return TOCValidationResult(
            status="skipped",
            confidence=None,
            issues=[],
            detail=f"validate_toc error: {str(exc)[:200]}",
        )
    finally:
        try:
            window.unlink()
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────
# Public API: lesson context (per-section "extract" phase)
# ─────────────────────────────────────────────────────────────────────


def _toc_source_pdf(pdf_path: Path, front_pages: int, back_pages: int) -> Optional[Path]:
    """Write a bounded TOC-search PDF: the first ``front_pages`` + last ``back_pages``
    pages of ``pdf_path`` (deduped, in order) into a temp PDF. Returns its path, or
    ``None`` on any problem (caller falls back / fails loud). Bounded so it works for
    >20MB scans where the whole-PDF attach is rejected."""
    try:
        reader = PdfReader(str(pdf_path))
        n = len(reader.pages)
        indices = sorted(
            set(range(0, min(front_pages, n))) | set(range(max(0, n - back_pages), n))
        )
        if not indices:
            return None
        writer = PdfWriter()
        for i in indices:
            writer.add_page(reader.pages[i])
        if len(writer.pages) == 0:
            return None
        fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="toc_window_")
        os.close(fd)
        with open(tmp, "wb") as fh:
            writer.write(fh)
        return Path(tmp)
    except Exception as exc:
        logger.warning(f"_toc_source_pdf failed ({exc!r})")
        return None


def _subset_pdf(
    pdf_path: Path,
    page_start: Optional[int],
    page_end: Optional[int],
    *,
    margin: int = 0,
    max_pages: Optional[int] = None,
) -> Optional[Path]:
    """Write pages ``[page_start..page_end]`` (1-based, inclusive) of ``pdf_path``
    into a small temp PDF and return its path; ``None`` on any problem so the
    caller falls back to attaching the full PDF.

    ``margin`` widens the requested range to ``[page_start - margin ..
    page_end + margin]`` (then clamped to the PDF's real ``[1..n]`` bounds) so a
    front-matter offset doesn't slice off the section's real pages. ``max_pages``
    then caps the widened window to at most that many pages, centered on the
    original ``[page_start..page_end]`` midpoint. With ``margin=0,
    max_pages=None`` the output is byte-for-byte the legacy range.

    Why: the extractor CLI (gemini) rejects PDFs > 20 MB, and its refusal
    message then poisons every downstream phase. Attaching only the section's
    pages keeps the upload tiny while PRESERVING diagram/visual content (a
    text-only read would lose figures).

    NOTE: ``page_start``/``page_end`` are the section's *textbook* page numbers;
    this assumes they map 1:1 to PDF page order. A textbook with front-matter
    offset could make the slice off-by-N — **verify against a real book before
    trusting this on large PDFs.** On any out-of-range/empty/error result we
    return ``None`` (full-PDF fallback) rather than risk a wrong slice.
    """
    if not page_start or not page_end or page_start <= 0 or page_end < page_start:
        return None
    try:
        reader = PdfReader(str(pdf_path))
        n = len(reader.pages)
        start_idx = max(0, (page_start - margin) - 1)
        end_idx = min(n - 1, (page_end + margin) - 1)
        if start_idx > end_idx:
            return None
        if max_pages is not None and (end_idx - start_idx + 1) > max_pages:
            # Trim to exactly max_pages, centered on the original range midpoint.
            # NOTE: a single lesson spanning > max_pages pages is clipped to this
            # window (centered on its midpoint) — acceptable for an extract summary,
            # but raise extract_window_max_pages if very long lessons get truncated.
            mid = ((page_start - 1) + (page_end - 1)) // 2
            start_idx = mid - max_pages // 2
            end_idx = start_idx + max_pages - 1
            # Clamp back into bounds, preserving the window width.
            if start_idx < 0:
                start_idx = 0
                end_idx = max_pages - 1
            if end_idx > n - 1:
                end_idx = n - 1
                start_idx = max(0, end_idx - max_pages + 1)
        writer = PdfWriter()
        for i in range(start_idx, end_idx + 1):
            writer.add_page(reader.pages[i])
        if len(writer.pages) == 0:
            return None
        fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="extract_section_")
        os.close(fd)
        with open(tmp, "wb") as f:
            writer.write(f)
        return Path(tmp)
    except Exception as exc:
        logger.warning(
            f"_subset_pdf failed ({exc!r}); falling back to full PDF attach"
        )
        return None


def _should_subset_for_extract(pdf_path: Path) -> bool:
    """R2: only carve a page subset when the full PDF would exceed the gemini
    extractor's size limit. Below the limit we attach the whole book (the
    printed page range is named in the extract prompt), which avoids the
    printed-vs-physical page-offset bug entirely. Unstattable path → False."""
    try:
        return pdf_path.stat().st_size > _GEMINI_PDF_MAX_BYTES
    except OSError:
        return False


async def extract_lesson_context(
    *,
    provider: str,
    model: Optional[str],
    pdf_path: Path,
    section_title: str,
    section_number: str,
    page_start: int,
    page_end: int,
    homework_job_id: UUID,
    phase_output_id: UUID,
    transport: str = "cli",
) -> tuple[str, int, int]:
    """Run the per-section extract phase. Returns ``(text, prompt_tokens, output_tokens)``.

    Uses the same builtin prompt body as gemini.py's ``extract_lesson_context``
    so downstream phases see identical lesson_context shape regardless of which
    provider produced it.
    """
    prov = get_provider(provider)
    resolved_model = _resolve_model(provider, model)

    instruction = _EXTRACT_PHASE_PROMPT.format(
        title=section_title,
        number=section_number,
        ps=page_start if page_start is not None else "?",
        pe=page_end if page_end is not None else "?",
        rules=_NO_PREAMBLE,
    )

    # R2: only subset oversized PDFs; otherwise attach the full book and let the
    # prompt name the printed page range (no physical-page-offset risk).
    subset_pdf = (
        _subset_pdf(pdf_path, page_start, page_end)
        if _should_subset_for_extract(pdf_path)
        else None
    )
    attach_path = subset_pdf or pdf_path
    if subset_pdf is not None:
        logger.info(
            f"agent.lesson.extract | attaching section subset PDF "
            f"pages {page_start}-{page_end} ({subset_pdf.name}) instead of full book"
        )

    # No schema — markdown deliverable. Prior outputs / lesson_context are
    # not meaningful here (this phase IS the lesson_context source).
    prompt = _build_master_prompt(
        phase_prompt=instruction,
        phase_name="lesson.extract",
        lesson_context=None,
        prior_outputs=None,
        difficulty=None,
        schema=None,
        provider_suffix=prov.prompt_suffix(None),
        attachment_preamble=prov.format_attachments([attach_path]),
    )

    started_at = datetime.now(timezone.utc)
    t0 = perf_counter()
    spawn_failed: Optional[Exception] = None
    rc = -1
    text = ""
    stderr = ""
    usage: dict[str, Any] = {
        "prompt_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "raw": {},
    }
    try:
        rc, text, usage, stderr = await _spawn(
            provider=prov,
            model=resolved_model,
            prompt=prompt,
            attachments=[attach_path],
            transport=transport,
        )
    except Exception as exc:
        spawn_failed = exc
    finally:
        # Remove the temp subset PDF (never the book's source.pdf — every
        # downstream phase re-reads that).
        if subset_pdf is not None:
            try:
                subset_pdf.unlink()
            except OSError:
                pass

    duration_s = perf_counter() - t0

    if spawn_failed is not None:
        await _record_usage(
            operation="lesson.extract",
            provider=provider,
            model_name=resolved_model,
            usage=usage,
            duration_s=duration_s,
            started_at=started_at,
            success=False,
            auth_mode=transport,
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
            error_message=str(spawn_failed),
            extra_envelope={
                "section_number": section_number,
                "section_title": section_title,
            },
        )
        raise spawn_failed

    if rc != 0:
        err = f"{provider} CLI exited rc={rc}"
        await _record_usage(
            operation="lesson.extract",
            provider=provider,
            model_name=resolved_model,
            usage=usage,
            duration_s=duration_s,
            started_at=started_at,
            success=False,
            auth_mode=transport,
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
            error_message=err,
            extra_envelope={
                "section_number": section_number,
                "section_title": section_title,
            },
        )
        raise RuntimeError(
            f"lesson.extract: {err} :: {_failure_preview(stderr, text)}"
        )

    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)

    await _record_usage(
        operation="lesson.extract",
        provider=provider,
        model_name=resolved_model,
        usage=usage,
        duration_s=duration_s,
        started_at=started_at,
        success=True,
        auth_mode=transport,
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        extra_envelope={
            "section_number": section_number,
            "section_title": section_title,
            "page_start": page_start,
            "page_end": page_end,
        },
    )
    logger.success(
        f"agent.lesson.extract done | provider={provider} "
        f"section={section_number} chars={len(text)} "
        f"input={prompt_tokens:,} output={output_tokens:,} "
        f"duration_ms={duration_s * 1000:.0f}"
    )
    return text, prompt_tokens, output_tokens


# ─────────────────────────────────────────────────────────────────────
# summarize_lesson — single-provider, whole-book TEXT injected (no PDF)
# ─────────────────────────────────────────────────────────────────────

_CONTRACT_INSTRUCTIONS = """Write the summary as an ENUMERATED COVERAGE CONTRACT so that \
every downstream generator can see the full inventory of what this lesson teaches. \
Begin with ONE short sentence naming the lesson (the gist). Then emit ONLY these \
section headings, using the EXACT English words below (do NOT translate the headings), \
with the ITEMS written in the lesson's language:

## Concepts & terms
## Rules & theorems
## Formulas
## Worked-example types
## Key facts
## Vocabulary & set phrases
## Source sentences & passages

Under each heading list one bullet ("- ") per item. OMIT a heading entirely if the \
lesson has no such items (e.g. a history lesson usually has no Formulas). \
"## Worked-example types" is REQUIRED whenever the lesson contains any worked example, \
sample problem, solved exercise, OR any task the lesson trains the student to carry out — \
list the TYPE of each (what the student must be able to do), not the full worked solution. \
This is NOT only for calculation subjects: a history, literature or geography lesson has \
task types too (define a term, date an event, trace a cause, compare two cases, answer a \
comprehension question about the text), and they belong here. \
"## Vocabulary & set phrases" is REQUIRED whenever the lesson teaches words, phrases or set \
expressions the student must be able to USE (a language lesson's word list, a science lesson's \
new terms). Format each bullet EXACTLY as `item — meaning` and nothing more — never repeat the \
meaning in a trailing parenthetical. Take the meaning from the source's own gloss, translation \
or definition whenever it gives one. When the source gives NO meaning (a bare word list), still \
supply the standard meaning but MARK it: `item — meaning [not in source]`. That marker matters: \
downstream generators and the reviewer treat this summary as the lesson's ground truth, so an \
unmarked meaning you supplied yourself would be enforced as if the textbook had said it. \
This heading differs from "## Concepts & terms", which carries the IDEAS the lesson explains: \
a word the student must be able to USE belongs here, not there. List every such item, up to a \
MAXIMUM of 40 bullets; if the source list is longer (a reference word-list or end-of-book \
glossary), list the 40 most central and add one final bullet `- (+N further items in the \
source list)`. \
"## Source sentences & passages" is REQUIRED whenever the lesson presents model sentences, \
example dialogue, or a reading text the student learns from — quote them VERBATIM (up to 10 \
sentences; for a long reading text quote its key sentences), never paraphrase them, and never \
compose a sentence of your own here. \
"## Key facts" is REQUIRED whenever the lesson states facts the student must recall — dates, \
names, quantities, events, or causal claims. Emit it even when the lesson ALSO teaches \
vocabulary or contains a reading text: a language or literature lesson still carries facts \
(who wrote it, when, what happens, what the terms of the plot are), and those belong here, \
not in the vocabulary list. \
Be complete but concise: capture every distinct \
teachable item, especially the problem/exercise types, and do not invent items absent \
from the source."""

_SUMMARIZE_LESSON_PROMPT = """You are given the full text of a textbook below. \
Locate the lesson titled "{title}" (section {number}; it is printed around pages \
{ps}-{pe} — treat the page numbers only as a hint, find it by its TITLE) and write \
a factual coverage contract of THAT lesson's content for downstream homework \
generation. Summarize only that lesson. """ + _CONTRACT_INSTRUCTIONS + """ {rules}

===== FULL TEXTBOOK TEXT =====
{book_text}
===== END TEXTBOOK TEXT ====="""


_SUMMARIZE_VISION_PROMPT = """The attached PDF pages contain a textbook lesson. \
Locate the lesson titled "{title}" (section {number}; it is printed around pages \
{ps}-{pe} — treat the page numbers only as a hint, find it by its TITLE) and write \
a factual coverage contract of THAT lesson's content for downstream homework \
generation. Summarize only that lesson. """ + _CONTRACT_INSTRUCTIONS + """ {rules}"""


# English-only vocabulary override (2026-08-26, owner decision): english lessons
# carry a dedicated student-facing Topic Vocabulary phase that authors the
# meanings, so the extract's vocabulary section lists the ITEMS ONLY. This kills
# the degenerate `item — item [not in source]` glosses the extract model
# produced on bare picture-wordlist textbook pages.
_ENGLISH_VOCAB_EXTRACT_RULE = (
    'FOR THIS ENGLISH LESSON the "## Vocabulary & set phrases" section lists '
    "the items BARE: one bullet per word or phrase exactly as the source shows "
    "it (`- item`), with NO meaning, NO em-dash gloss, and NO \"[not in "
    'source]" marker — a dedicated downstream Vocabulary phase authors the '
    "meanings. Every other section keeps the rules above. "
)


# Math-notation contract for the extract (2026-09-02 format decision: LaTeX in
# $-delimiters is the wire format platform-wide). The extract is the phase that
# naturally transcribes textbook LaTeX, and BARE fragments (a_n outside
# dollars) are exactly what reaches students as mangled literal text — so the
# rule here is wrap-always, KaTeX-safe subset, chemistry stays Unicode.
_EXTRACT_NOTATION_RULE = (
    "Mathematics notation: wrap EVERY mathematical expression — every "
    "variable, formula, fraction, power, inequality, sequence — in dollar "
    "delimiters, $...$ inline or $$...$$ on its own line, using standard "
    "KaTeX-safe LaTeX (\\frac{a}{b}, a_{n+1}, x^{2}, \\sqrt{...}, \\cdot, "
    "\\neq, \\leq, \\geq, Greek). NEVER leave a bare fragment like a_n or "
    "\\frac outside dollars — unwrapped fragments render as literal text. "
    "One $ pair per expression, opened and closed on the same line; no "
    "\\begin{...} environments; no \\text{...} — units and words stay "
    "outside the span. Chemical formulas, ions and reaction arrows stay "
    "Unicode plain text (H₂O, SO₄²⁻, →), never inside $. Never use $ as a "
    "currency symbol — write the currency as a word. "
)


def _extract_rules(subject: "Optional[str]") -> str:
    rules = _EXTRACT_NOTATION_RULE + _NO_PREAMBLE
    return (_ENGLISH_VOCAB_EXTRACT_RULE + rules) if subject == "english" \
        else rules


async def summarize_lesson(
    *,
    provider: str,
    model: Optional[str],
    book_text: str,
    section_title: str,
    section_number: str,
    page_start: int,
    page_end: int,
    homework_job_id: UUID,
    phase_output_id: UUID,
    transport: str = "cli",
    correction_hint: str = "",
    subject: Optional[str] = None,
) -> tuple[str, int, int]:
    """Single-provider extract: inject the whole-book TEXT (no PDF attached),
    model locates the lesson by title and summarizes. Returns (text, prompt_tokens,
    output_tokens). Raises on CLI failure. NO Gate B / NO failover here (the
    _execute_phase extract branch wraps this in _run_with_failover + Gate B)."""
    prov = get_provider(provider)
    resolved_model = _resolve_model(provider, model)
    instruction = _SUMMARIZE_LESSON_PROMPT.format(
        title=section_title,
        number=section_number,
        ps=page_start if page_start is not None else "?",
        pe=page_end if page_end is not None else "?",
        rules=_extract_rules(subject),
        book_text=book_text,
    )
    if correction_hint:
        instruction += (
            "\n\nIMPORTANT — your previous summary mis-transcribed the source. "
            "Correct these and re-summarize faithfully from the textbook text:\n"
            f"{correction_hint}"
        )
    prompt = _build_master_prompt(
        phase_prompt=instruction,
        phase_name="lesson.extract",
        lesson_context=None,
        prior_outputs=None,
        difficulty=None,
        schema=None,
        provider_suffix=prov.prompt_suffix(None),
        attachment_preamble="",   # no attachment preamble — text is inline
    )
    started_at = datetime.now(timezone.utc)
    t0 = perf_counter()
    rc, text, usage, stderr = await _spawn(
        provider=prov, model=resolved_model, prompt=prompt, attachments=[], transport=transport,
    )
    duration_s = perf_counter() - t0
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    ok = rc == 0
    err = None if ok else _spawn_failure_message(provider, transport, rc, stderr, text)
    extra: dict[str, Any] = {"section_number": section_number, "section_title": section_title}
    if not ok:
        extra["error"] = (stderr or "")[:2000]
    await _record_usage(
        operation="lesson.extract", provider=provider, model_name=resolved_model,
        usage=usage, duration_s=duration_s, started_at=started_at, success=ok,
        auth_mode=transport,
        homework_job_id=homework_job_id, phase_output_id=phase_output_id,
        error_message=err,
        extra_envelope=extra,
    )
    if not ok:
        raise RuntimeError(f"lesson.extract: {err}")
    logger.success(
        f"agent.lesson.extract done | provider={provider} section={section_number} "
        f"chars={len(text)} input={prompt_tokens:,} output={output_tokens:,} duration_ms={duration_s * 1000:.0f}"
    )
    return text, prompt_tokens, output_tokens


# ─────────────────────────────────────────────────────────────────────
# summarize_lesson_vision — forced-cli, section page-window PDF attached
# ─────────────────────────────────────────────────────────────────────


async def summarize_lesson_vision(
    *,
    provider: str,
    model: Optional[str],
    pdf_path: Path,
    section_title: str,
    section_number: str,
    page_start: int,
    page_end: int,
    homework_job_id: UUID,
    phase_output_id: UUID,
    transport: str = "cli",
    subject: Optional[str] = None,
) -> tuple[str, int, int]:
    """VISION fallback extract (scanned / unreadable-text PDFs): attach the
    section's page window as a small PDF and have the model read it visually.

    Vision is cli by default — most providers have no api PDF-attach path.
    Exception: ``provider="gemini"`` with ``transport="api"`` attaches the
    window PDF over Vertex (Gemini api natively supports file attachments).
    For all other providers ``transport`` must remain ``"cli"``.

    Returns ``(text, prompt_tokens, output_tokens)``.
    Raises ``RuntimeError`` (fail loud) when the page range can't be scoped or
    the CLI exits non-zero. Records a usage row even on failure."""
    prov = get_provider(provider)
    resolved_model = _resolve_model(provider, model)

    # Scope the window BEFORE any spawn — fail loud if we can't carve it.
    window_pdf = _subset_pdf(
        pdf_path,
        page_start,
        page_end,
        margin=settings.extract_window_pages,
        max_pages=settings.extract_window_max_pages,
    )
    if window_pdf is None:
        raise RuntimeError(
            f"lesson.extract (vision): cannot scope page range "
            f"{page_start}-{page_end} of {pdf_path.name}"
        )

    instruction = _SUMMARIZE_VISION_PROMPT.format(
        title=section_title,
        number=section_number,
        ps=page_start if page_start is not None else "?",
        pe=page_end if page_end is not None else "?",
        rules=_extract_rules(subject),
    )
    prompt = _build_master_prompt(
        phase_prompt=instruction,
        phase_name="lesson.extract",
        lesson_context=None,
        prior_outputs=None,
        difficulty=None,
        schema=None,
        provider_suffix=prov.prompt_suffix(None),
        attachment_preamble=prov.format_attachments([window_pdf]),
    )

    started_at = datetime.now(timezone.utc)
    t0 = perf_counter()
    try:
        rc, text, usage, stderr = await _spawn(
            provider=prov,
            model=resolved_model,
            prompt=prompt,
            attachments=[window_pdf],
            transport=transport,
        )
    finally:
        # Remove the temp window PDF (never the book's source.pdf).
        try:
            window_pdf.unlink()
        except OSError:
            pass

    duration_s = perf_counter() - t0
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    ok = rc == 0
    await _record_usage(
        operation="lesson.extract",
        provider=provider,
        model_name=resolved_model,
        usage=usage,
        duration_s=duration_s,
        started_at=started_at,
        success=ok,
        auth_mode=transport,
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        error_message=None if ok else _spawn_failure_message(provider, transport, rc, stderr, text),
        extra_envelope={
            "section_number": section_number,
            "section_title": section_title,
            "vision": True,
        },
    )
    if not ok:
        raise RuntimeError(
            f"lesson.extract (vision): {_spawn_failure_message(provider, transport, rc, stderr, text)}"
        )
    logger.success(
        f"agent.lesson.extract done (vision) | provider={provider} "
        f"section={section_number} chars={len(text)} "
        f"input={prompt_tokens:,} output={output_tokens:,} "
        f"duration_ms={duration_s * 1000:.0f}"
    )
    return text, prompt_tokens, output_tokens


# ─────────────────────────────────────────────────────────────────────
# Compatibility shims: pipeline.py migrates to these incrementally
# ─────────────────────────────────────────────────────────────────────


async def run_phase_prompt(
    *,
    provider: str,
    model: Optional[str] = None,
    phase_prompt: str,
    lesson_context: str,
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
    phase_name: str = "?",
    max_output_tokens: Optional[int] = None,
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
    attachments: list[Path] = (),
    source_map_digest: str = "",
    transport: str = "cli",
) -> tuple[str, Optional[int], Optional[int]]:
    """Markdown-output phase. Wraps :func:`run_phase` and returns
    ``(text, prompt_tokens, output_tokens)`` to mirror gemini.run_phase_prompt's
    return shape so pipeline.py can switch providers without restructuring."""
    result = await run_phase(
        provider=provider,
        model=model,
        phase_prompt=phase_prompt,
        phase_name=phase_name,
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        lesson_context=lesson_context,
        prior_outputs=prior_outputs,
        attachments=list(attachments),
        schema=None,
        difficulty=difficulty,
        max_output_tokens=max_output_tokens,
        source_map_digest=source_map_digest,
        transport=transport,
    )
    pt = int(result.usage.get("prompt_tokens") or 0)
    ot = int(result.usage.get("output_tokens") or 0)
    return result.text, pt or None, ot or None


# ─────────────────────────────────────────────────────────────────────
# Cross-job extract-reuse marker
# ─────────────────────────────────────────────────────────────────────


async def record_cached_lesson_extract(
    *,
    homework_job_id: UUID,
    phase_output_id: UUID,
    source_job_id: UUID,
    source_phase_output_id: UUID,
) -> None:
    """Persist a free ``lesson.extract`` row marking that a previous job's
    extract was reused. Mirrors :func:`app.services.gemini.record_cached_lesson_extract`."""
    await _record_usage(
        operation="lesson.extract",
        provider="<cache>",
        model_name="<cache>",
        usage={
            "prompt_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "raw": {},
        },
        duration_s=0.0,
        started_at=datetime.now(timezone.utc),
        success=True,
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        extra_envelope={
            "cache_hit": True,
            "source_job_id": str(source_job_id),
            "source_phase_output_id": str(source_phase_output_id),
        },
    )


__all__ = [
    "PhaseResult",
    "TOCValidationResult",
    "_PROVIDER_DEFAULT_MODEL",
    "_resolve_model",
    "run_phase",
    "extract_toc",
    "validate_toc",
    "extract_lesson_context",
    "run_phase_prompt",
    "record_cached_lesson_extract",
]
