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
quota allows. The semaphore size reuses the existing ``settings.gemini_max_concurrency``
setting (renamed in Wave 3E).

Public functions deliberately mirror the surface of :mod:`app.services.gemini`
so callers (``pipeline.py``) can be migrated incrementally; the gemini module
keeps importing during the transition.
"""

from __future__ import annotations

import asyncio
import json
import os
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
from pypdf import PdfReader

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

# Lazy-init module-level semaphore. First access creates an
# ``asyncio.Semaphore(settings.gemini_max_concurrency)``; Wave 3E renames
# the setting to ``agent_max_concurrency``. Bound to the running loop on
# first await — works regardless of when callers import this module.
_agent_semaphore: Optional[asyncio.Semaphore] = None


def _semaphore() -> asyncio.Semaphore:
    global _agent_semaphore
    if _agent_semaphore is None:
        _agent_semaphore = asyncio.Semaphore(settings.gemini_max_concurrency)
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


def _auth_env(provider_name: str, transport: str, base_env: dict[str, str]) -> dict[str, str]:
    """Per-call auth shaping (spec §4). cli is the unconditional baseline for
    EVERY spawn; api is the only deviation. Scrub both provider keys first, then
    grant exactly what the (provider, transport) needs — so an api gemini spawn
    never carries the Anthropic key, and a cli spawn never accidentally bills."""
    env = dict(base_env)
    env.pop("GEMINI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("GOOGLE_GENAI_USE_GCA", None)
    if transport == "api":
        # Missing key in api mode must be LOUD: an empty env var is falsy to
        # both CLIs → claude would silently fall back to OAuth (billing the
        # subscription while the row says auth_mode=api). The claim gate makes
        # this near-unreachable, but defense-in-depth for this phase's exact
        # failure class. Raise rather than inject "".
        key_var = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY"}.get(provider_name)
        key = base_env.get(key_var) if key_var else None
        if not key:
            raise RuntimeError(f"transport=api for {provider_name} but {key_var} is unset/empty")
        env[key_var] = key
        # kimi/codex/opencode never reach api (blocked at validation)
    else:  # cli baseline
        if provider_name == "gemini":
            env["GOOGLE_GENAI_USE_GCA"] = "true"  # GCA OAuth, wins over any key
        # claude/others: scrubbed keys above IS the whole cli adapter
    return env


async def _spawn(
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


async def _record_usage(
    *,
    operation: str,
    provider: str,
    model_name: Optional[str],
    usage: dict[str, Any],
    duration_s: float,
    started_at: datetime,
    success: bool,
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
                book_id=book_id,
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cached_tokens=int(usage.get("cached_tokens") or 0),
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
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                error_message=str(spawn_failed),
                extra_envelope={"phase_name": phase_name, "attempt": attempt},
            )
            raise spawn_failed

        if rc != 0:
            err = f"{provider} CLI exited rc={rc}"
            await _record_usage(
                operation=operation,
                provider=provider,
                model_name=resolved_model,
                usage=usage,
                duration_s=duration_s,
                started_at=started_at,
                success=False,
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                error_message=err,
                extra_envelope={"phase_name": phase_name, "attempt": attempt},
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

    lesson_context = None
    attachment_preamble = prov.format_attachments([pdf_path])
    attachments = [pdf_path]
    if has_local_toc_text:
        lesson_context = (
            "Locally extracted text from the FIRST and LAST pages of the PDF "
            "follows (a textbook's table of contents may be at the front OR the "
            "back). Use this text to identify TOC entries and printed page "
            "numbers. The headings `--- PDF page N ---` are physical PDF page "
            "markers, not textbook page numbers.\n\n"
            f"{toc_source_text}"
        )
    # R6: gemini reads PDFs natively — for PDFs under its size limit keep the
    # whole PDF attached (alongside any text excerpt) so it can locate a TOC the
    # excerpt missed. Other providers, or oversized PDFs, fall back to no
    # attachment. This guard runs UNCONDITIONALLY so image-only PDFs (no local
    # TOC text) also get the >20 MB protection instead of crashing on upload.
    try:
        pdf_size = pdf_path.stat().st_size
    except OSError:
        pdf_size = _GEMINI_PDF_MAX_BYTES + 1  # force no-attachment on stat failure
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
            "source": "local_pdf_text" if has_local_toc_text else "attachment",
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


# ─────────────────────────────────────────────────────────────────────
# Public API: lesson context (per-section "extract" phase)
# ─────────────────────────────────────────────────────────────────────


def _subset_pdf(
    pdf_path: Path, page_start: Optional[int], page_end: Optional[int]
) -> Optional[Path]:
    """Write pages ``[page_start..page_end]`` (1-based, inclusive) of ``pdf_path``
    into a small temp PDF and return its path; ``None`` on any problem so the
    caller falls back to attaching the full PDF.

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
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(pdf_path))
        n = len(reader.pages)
        start_idx = max(0, page_start - 1)
        end_idx = min(n - 1, page_end - 1)
        if start_idx > end_idx:
            return None
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
    await _record_usage(
        operation="lesson.extract", provider=provider, model_name=resolved_model,
        usage=usage, duration_s=duration_s, started_at=started_at, success=ok,
        homework_job_id=homework_job_id, phase_output_id=phase_output_id,
        error_message=None if ok else f"{provider} CLI exited rc={rc}",
        extra_envelope={"section_number": section_number, "section_title": section_title},
    )
    if not ok:
        raise RuntimeError(f"lesson.extract: {provider} CLI exited rc={rc} :: {_failure_preview(stderr, text)}")
    logger.success(
        f"agent.lesson.extract done | provider={provider} section={section_number} "
        f"chars={len(text)} input={prompt_tokens:,} output={output_tokens:,} duration_ms={duration_s * 1000:.0f}"
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
