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
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader, PdfWriter

from app.config import settings
from app.db import SessionLocal
from app.repositories import agent_usage as usage_repo
from app.schemas import (
    ExtractedTOC,
)
from app.services.providers import Provider, get_provider
from app.services.proc_tree import kill_tree


# ─────────────────────────────────────────────────────────────────────
# Public types & constants
# ─────────────────────────────────────────────────────────────────────


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


class AuthEnvError(RuntimeError):
    """A spawn's credentials could not be assembled for the requested
    transport (missing/empty key, no Vertex SA, api-unsupported provider).
    Typed so auth classification is isinstance-based, never substring luck
    (spec 4.1 §5a) — a judge hitting this on an api job must fail LOUDLY."""


def _auth_env(provider_name: str, transport: str, base_env: dict[str, str]) -> dict[str, str]:
    """Per-call auth shaping (spec §4). cli is the unconditional baseline for
    EVERY spawn; api is the only deviation. Scrub both provider keys first, then
    grant exactly what the (provider, transport) needs — so an api gemini spawn
    never carries the Anthropic key, and a cli spawn never accidentally bills."""
    env = dict(base_env)
    env.pop("GEMINI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
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


def _is_rate_limited(text: str) -> bool:
    """True iff ``text`` names a transient rate-limit worth retrying.

    Matches Vertex + anthropic rate-limit shapes; never matches auth (401/403)
    or truncation (MAX_TOKENS), which do not self-heal.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(term in lowered for term in _RATE_LIMIT_TERMS)


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
    """Run a single provider call with bounded retry-on-rate-limit.

    Delegates each attempt to :func:`_spawn_once`; on a transient 429/
    rate-limit it backs off (``asyncio.sleep`` holds NO concurrency slot —
    ``_spawn_once`` acquires the semaphore internally) and retries up to
    ``settings.rate_limit_max_retries`` times. A persistent rate-limit (or any
    other failure) returns the failure tuple unchanged, exactly as before.
    """
    for attempt in range(settings.rate_limit_max_retries + 1):
        rc, text, usage, stderr = await _spawn_once(
            provider=provider, model=model, prompt=prompt,
            attachments=attachments, transport=transport,
        )
        if rc == 0 or not _is_rate_limited(stderr or text):
            return rc, text, usage, stderr
        if attempt >= settings.rate_limit_max_retries:
            logger.warning(
                f"agent.spawn rate-limited, retries exhausted | provider={provider.name}"
            )
            return rc, text, usage, stderr
        delay = _rate_limit_delay(attempt)
        logger.warning(
            f"agent.spawn rate-limited (429) | provider={provider.name} "
            f"attempt={attempt + 1}/{settings.rate_limit_max_retries} backoff={delay:.1f}s"
        )
        await asyncio.sleep(delay)


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

    try:
        async with SessionLocal() as session:
            await usage_repo.create(
                session,
                operation=operation,
                provider=provider,
                model_name=model_name or "<default>",
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

    # Schema mode but both attempts failed validation.
    raise RuntimeError(
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


def validate_extract_text(text: str) -> Optional[str]:
    """Gate A — deterministic check on the RAW local PDF text. Returns a failure
    reason string, or None if the text looks like real, readable content.
    Terminal: a failure here means the input is unreadable (scanned / broken
    font), which no provider can fix."""
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
    return None


def validate_extract_summary(summary: str) -> Optional[str]:
    """Gate B — deterministic check on a produced summary. Returns a failure
    reason, or None if it looks like a real summary. A failure triggers
    failover (the run_fn raises ExtractRefusal)."""
    stripped = (summary or "").strip()
    if len(stripped) < settings.extract_min_summary_chars:
        return f"summary too short ({len(stripped)} chars) — likely a refusal"
    head = stripped[:_REFUSAL_HEAD_CHARS].lower()
    for marker in _EXTRACT_REFUSAL_MARKERS:
        if marker in head:
            return f"refusal marker in summary head: {marker!r}"
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

    toc_text_usable = has_local_toc_text and not extract_text_is_too_sparse(
        toc_source_text, toc_source_meta.get("pages_read", 0)
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
            transport = "cli"   # vision needs attachments; api is text-only
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
        keep_pdf = provider == "gemini" and pdf_size <= _GEMINI_PDF_MAX_BYTES
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

_SUMMARIZE_LESSON_PROMPT = """You are given the full text of a textbook below. \
Locate the lesson titled "{title}" (section {number}; it is printed around pages \
{ps}-{pe} — treat the page numbers only as a hint, find it by its TITLE) and write \
a concise, factual summary of THAT lesson's content for downstream homework \
generation. Summarize only that lesson. {rules}

===== FULL TEXTBOOK TEXT =====
{book_text}
===== END TEXTBOOK TEXT ====="""


_SUMMARIZE_VISION_PROMPT = """The attached PDF pages contain a textbook lesson. \
Locate the lesson titled "{title}" (section {number}; it is printed around pages \
{ps}-{pe} — treat the page numbers only as a hint, find it by its TITLE) and write \
a concise, factual summary of THAT lesson's content for downstream homework \
generation. Summarize only that lesson. {rules}"""


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
        rules=_NO_PREAMBLE,
        book_text=book_text,
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
) -> tuple[str, int, int]:
    """VISION fallback extract (scanned / unreadable-text PDFs): attach the
    section's page window as a small PDF and have the model read it visually.

    Vision is ALWAYS cli — there is no api PDF-attach path — so this function
    takes no ``transport`` param and hardcodes ``transport="cli"`` for both the
    spawn and the usage row. Returns ``(text, prompt_tokens, output_tokens)``.
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
        rules=_NO_PREAMBLE,
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
            transport="cli",
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
        auth_mode="cli",
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        error_message=None if ok else f"{provider} CLI exited rc={rc}",
        extra_envelope={
            "section_number": section_number,
            "section_title": section_title,
            "vision": True,
        },
    )
    if not ok:
        raise RuntimeError(
            f"lesson.extract (vision): {provider} CLI exited rc={rc} "
            f":: {_failure_preview(stderr, text)}"
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
    "_PROVIDER_DEFAULT_MODEL",
    "_resolve_model",
    "run_phase",
    "extract_toc",
    "extract_lesson_context",
    "run_phase_prompt",
    "record_cached_lesson_extract",
]
