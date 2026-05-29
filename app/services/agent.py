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

from app.config import settings
from app.db import SessionLocal
from app.repositories import agent_usage as usage_repo
from app.schemas import (
    CaseBasedPreview,
    ClassifyDecision,
    ExtractedTOC,
    FinalChallenge,
    FlashcardsPack,
    GamesPack,
    MemoryCheckPack,
    MemorySprintPack,
    ReadingPassage,
)
from app.services.providers import Provider, get_provider


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


@dataclass(frozen=True)
class ProviderSpec:
    """One link in a fallback chain: a provider name + optional model.

    ``model=None`` means "let the provider/CLI pick its own default" (resolved
    by :func:`_resolve_model`). Specs are ordered; the first that succeeds wins.
    """

    provider: str
    model: Optional[str] = None


# Default-model lookup. **Regression guard from a prior session**:
# ``_PROVIDER_DEFAULT_MODEL["gemini"]`` MUST stay ``None`` so that one
# provider's default never leaks into another's resolution path. Each CLI's
# own default is preferred when ``model`` is unset.
_PROVIDER_DEFAULT_MODEL: dict[str, Optional[str]] = {
    "claude": "claude-sonnet-4-6",
    "kimi":   None,
    "codex":  None,
    "gemini": None,
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


# Phases that emit JSON directly via a Pydantic schema. Mirror of the dict
# in :mod:`app.services.gemini` so ``pipeline.py`` can read the same map
# from either module during the migration.
STRUCTURED_PHASE_SCHEMAS: dict[str, type[BaseModel]] = {
    "classify": ClassifyDecision,
    # ── Learning Sections (Flow v2 Phase 3) ──────────────────────────
    "case-based-preview": CaseBasedPreview,   # replaces preview-easy/hard when CBP runtime ready
    "flashcards": FlashcardsPack,
    "memory-check": MemoryCheckPack,          # replaces memory-sprint; items reference flashcard IDs
    # ── Legacy phases (v1) ──────────────────────────────────────────
    "memory-sprint": MemorySprintPack,
    "game-breaks": GamesPack,
    "final-challenge": FinalChallenge,
    "reading": ReadingPassage,
}


_TOC_TEXT_MAX_PAGES = 40
_TOC_TEXT_MAX_CHARS = 60_000


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


# Universal SVG rules (lifted verbatim from gemini.py:301–378).
_SVG_RULES = (
    "\n\n## VISUAL / SVG RULES (when you embed inline <svg>)\n"
    "Every <svg> you generate MUST follow these rules. They override any "
    "size or styling guidance in the system prompt above. The #1 failure "
    "mode is cramped diagrams where labels overlap shapes or each other — "
    "if in doubt, pick a LARGER frame, not smaller.\n\n"

    "### 1. White background, always\n"
    "First child of every <svg> must be:\n"
    "  `<rect width=\"100%\" height=\"100%\" fill=\"#ffffff\" rx=\"6\"/>`\n\n"

    "### 2. Size scales with content — default to LARGER\n"
    "Pick width/height/viewBox to give every label and shape generous room. "
    "A diagram with extra whitespace is readable; a diagram with overlapping "
    "labels is unusable.\n"
    "  - Simple (1–3 labeled parts): width=320 height=220 "
    "viewBox=\"0 0 320 220\"\n"
    "  - Medium (4–8 labeled parts): width=520 height=340 "
    "viewBox=\"0 0 520 340\"\n"
    "  - Detailed (9+ parts, comparative diagrams, multi-step flows): "
    "width=720 height=480 viewBox=\"0 0 720 480\"\n"
    "If a frame would be cramped at the chosen tier, BUMP UP to the next "
    "tier. If even Detailed would crowd, emit TWO svgs (overview + zoom-in) "
    "instead of cramming into one.\n\n"

    "### 3. Padding & spacing budget (anti-overlap)\n"
    "  - **20px** minimum from every viewBox edge to the first stroke or "
    "text — never let content butt against the frame.\n"
    "  - **8px** minimum gap between any text label and the shape it "
    "labels (so they don't visually merge).\n"
    "  - **18px** minimum vertical gap between two stacked text lines.\n"
    "  - **40px** minimum width AND height for any shape that contains "
    "text INSIDE it. Smaller shapes need EXTERNAL labels (see §6).\n"
    "  - **30px** minimum gap between two labeled parts that sit "
    "side-by-side (e.g., comparative anatomy panels, before/after).\n\n"

    "### 4. Text rules — labels must be legible, never overlap\n"
    "  - Default: `<text font-size=\"14\" fill=\"#111827\" "
    "font-family=\"sans-serif\">`\n"
    "  - Centered text: include BOTH `text-anchor=\"middle\"` AND "
    "`dominant-baseline=\"middle\"` — without these, text drifts off-center "
    "and overlaps adjacent shapes.\n"
    "  - Left-aligned: `text-anchor=\"start\"`. Right-aligned: "
    "`text-anchor=\"end\"`.\n"
    "  - Multi-word labels too wide for one line: split with "
    "`<tspan x=\"...\" dy=\"1.2em\">word</tspan>` — never let text "
    "visually run off a shape.\n"
    "  - **Never let two text elements touch.** If they would, move one "
    "or enlarge the frame. If still impossible, use leader lines (§6).\n"
    "  - Long names (>12 chars) inside small shapes don't fit — put them "
    "OUTSIDE with a leader line.\n\n"

    "### 5. High-contrast strokes against white\n"
    "  - Default outline: `stroke=\"#1f2937\" stroke-width=\"2\" "
    "fill=\"none\"`\n"
    "  - Filled regions: `#fde68a` (highlight), `#bae6fd` (cool/water), "
    "`#bbf7d0` (plant/calm), `#fecaca` (warm/warning)\n"
    "  - Emphasis stroke: `#0ea5e9` or `#dc2626`\n"
    "  - NEVER use light grays (#ccc, #eee) or near-white pastels "
    "(#f0f8ff, #f8fafc) — they vanish on white.\n\n"

    "### 6. Leader-line pattern (for crowded diagrams)\n"
    "When labels can't fit inside their shapes, place them in the margins "
    "with a thin leader line connecting label to part:\n"
    "  `<line x1=\"shapeX\" y1=\"shapeY\" x2=\"labelX\" y2=\"labelY\" "
    "stroke=\"#1f2937\" stroke-width=\"1\"/>`\n"
    "  `<text x=\"labelX\" y=\"labelY\" text-anchor=\"start\">Label</text>`\n"
    "Use leader lines for cell organelles, anatomy parts, and any 4+ part "
    "diagram where internal labels would overlap.\n\n"

    "### 7. Clean output\n"
    "  - No raster `<image>`, no `<foreignObject>`, no external fonts.\n"
    "  - Group related parts with `<g id=\"...\">...</g>`.\n"
    "  - Add a small legend (corner) when colors carry meaning.\n"
    "  - Two-space indent for children.\n"
    "  - Place each <svg> directly after the prose it illustrates — never "
    "inside a code fence."
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


# Phases that produce inline SVG diagrams (mirrors gemini.py:_SVG_PHASES).
_SVG_PHASES: set[str] = {
    "preview-hard", "preview-easy", "preview",
    "real-life", "consolidation",
    "flashcards", "game-breaks", "final-challenge", "reading",
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


async def _spawn(
    *,
    provider: Provider,
    model: Optional[str],
    prompt: str,
    attachments: list[Path],
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
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

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
            proc.kill()
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

    if prior_outputs:
        parts.append("")
        parts.append("## Prior phase outputs (for cross-phase consistency)")
        for name, body in prior_outputs.items():
            stripped = _strip_svgs(body or "")
            parts.append(f"\n### {name}\n{stripped}")

    parts.append("")
    parts.append(f"Difficulty: {difficulty or 'unspecified'}")

    # SVG rules: only useful for phases that actually emit diagrams. Other
    # phases (memory-sprint, reflection, classify) get a slimmer prompt.
    if phase_name in _SVG_PHASES:
        parts.append(_SVG_RULES)

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
    )

    attempt_prompt = base_prompt
    last_error: Optional[ValidationError] = None
    parsed_obj: Optional[BaseModel] = None
    last_text = ""
    last_stderr = ""
    last_usage: dict[str, Any] = {}

    # Two attempts iff schema is set, otherwise one.
    max_attempts = 2 if schema is not None else 1
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
            )
        except Exception as exc:
            spawn_failed = exc

        duration_s = perf_counter() - t0
        last_text = text
        last_stderr = stderr
        last_usage = usage

        if spawn_failed is not None:
            await _record_usage(
                operation="phase.run",
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
                operation="phase.run",
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
            # Markdown-output phase. Record success, return.
            await _record_usage(
                operation="phase.run",
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
                operation="phase.run",
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
            operation="phase.run",
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


# ─────────────────────────────────────────────────────────────────────
# Public API: run_phase_with_fallback (provider fallback chain)
# ─────────────────────────────────────────────────────────────────────


async def run_phase_with_fallback(
    *,
    providers: list[ProviderSpec],
    phase_prompt: str,
    phase_name: str,
    homework_job_id: Optional[UUID],
    phase_output_id: Optional[UUID],
    lesson_context: Optional[str] = None,
    prior_outputs: Optional[dict[str, str]] = None,
    attachments: list[Path] = (),
    schema: Optional[type[BaseModel]] = None,
    difficulty: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
) -> PhaseResult:
    """Run a phase against an ordered provider chain, falling back on failure.

    Each :class:`ProviderSpec` is tried in order via :func:`run_phase`. A spec
    "fails" when ``run_phase`` raises — a spawn error, a non-zero CLI exit, or
    schema-validation exhaustion (each provider keeps its own one-shot repair
    retry *inside* ``run_phase``). The first spec that succeeds returns its
    ``PhaseResult``; if every spec fails, a single ``RuntimeError`` naming the
    exhausted chain is raised. Every underlying attempt still records its own
    ``AgentUsage`` row through ``run_phase``.
    """
    if not providers:
        raise ValueError("run_phase_with_fallback requires at least one provider")

    last_exc: Optional[Exception] = None
    for idx, spec in enumerate(providers, start=1):
        try:
            return await run_phase(
                provider=spec.provider,
                model=spec.model,
                phase_prompt=phase_prompt,
                phase_name=phase_name,
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                lesson_context=lesson_context,
                prior_outputs=prior_outputs,
                attachments=list(attachments),
                schema=schema,
                difficulty=difficulty,
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:  # noqa: BLE001 — any provider failure → try next
            last_exc = exc
            logger.warning(
                f"agent.fallback | provider={spec.provider} "
                f"({idx}/{len(providers)}) failed for phase={phase_name}: "
                f"{str(exc)[:200]!r}"
            )
            continue

    chain = " -> ".join(s.provider for s in providers)
    raise RuntimeError(
        f"phase.run {phase_name}: all {len(providers)} providers failed "
        f"[{chain}]; last error: {last_exc}"
    ) from last_exc


# ─────────────────────────────────────────────────────────────────────
# Task → model policy
# ─────────────────────────────────────────────────────────────────────

# Order the remaining CLIs are appended as fallbacks after the job's primary.
_DEFAULT_FALLBACK_ORDER: tuple[str, ...] = ("claude", "gemini", "codex", "kimi")

# Tasks pinned to the cheap extractor regardless of the job provider: these
# burn the most input tokens (whole-PDF / many-page reads) and emit a flat
# factual summary, so smart-tier rates buy nothing.
_EXTRACT_TASKS: frozenset[str] = frozenset({"toc.extract", "lesson.extract"})


def resolve_provider_chain(
    *,
    task: str,
    job_provider: str,
    job_model: Optional[str] = None,
    fallback_order: tuple[str, ...] = _DEFAULT_FALLBACK_ORDER,
) -> list[ProviderSpec]:
    """Map a task type to an ordered provider/model fallback chain.

    Extract-family tasks are pinned to ``settings.extract_provider`` /
    ``settings.extract_model`` regardless of the job's chosen provider. Every
    other task uses the job provider as primary, then the remaining CLIs (in
    ``fallback_order``) as fallbacks. The primary is never duplicated in the
    fallback tail, and ``job_model`` rides only on the primary.
    """
    if task in _EXTRACT_TASKS:
        return [ProviderSpec(settings.extract_provider, settings.extract_model)]

    chain = [ProviderSpec(job_provider, job_model)]
    for provider in fallback_order:
        if provider != job_provider:
            chain.append(ProviderSpec(provider))
    return chain


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


def _extract_toc_source_text(pdf_path: Path) -> tuple[str, dict[str, Any]]:
    """Extract a bounded front-matter text slice for TOC-only prompts.

    Gemini CLI's file-reading path can reject large PDFs before the model sees
    the contents. For TOC extraction we only need the early pages, so feeding a
    compact local text excerpt is both faster and avoids provider file limits.
    """
    meta: dict[str, Any] = {
        "source": "pdf_text",
        "max_pages": _TOC_TEXT_MAX_PAGES,
        "max_chars": _TOC_TEXT_MAX_CHARS,
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
        chunks: list[str] = []
        chars = 0
        pages_read = 0
        for idx in range(1, min(total_pages, _TOC_TEXT_MAX_PAGES) + 1):
            page = reader.pages[idx - 1]
            try:
                page_text = (page.extract_text() or "").strip()
            except Exception as exc:
                logger.debug(
                    f"toc text extraction skipped page {idx} of {pdf_path.name}: {exc!r}"
                )
                continue
            if not page_text:
                continue
            chunk = f"\n\n--- PDF page {idx} ---\n{page_text}"
            remaining = _TOC_TEXT_MAX_CHARS - chars
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            chunks.append(chunk)
            chars += len(chunk)
            pages_read = idx
            if chars >= _TOC_TEXT_MAX_CHARS:
                break
    except Exception as exc:
        meta["error"] = str(exc)
        return "", meta

    text = "".join(chunks).strip()
    meta["pages_read"] = pages_read
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
            "Locally extracted text from the first pages of the PDF follows. "
            "Use only this text to identify TOC entries and printed page "
            "numbers. The headings `--- PDF page N ---` are physical PDF page "
            "markers, not textbook page numbers.\n\n"
            f"{toc_source_text}"
        )
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
        attachment_preamble=prov.format_attachments([pdf_path]),
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
            attachments=[pdf_path],
        )
    except Exception as exc:
        spawn_failed = exc

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
    )
    pt = int(result.usage.get("prompt_tokens") or 0)
    ot = int(result.usage.get("output_tokens") or 0)
    return result.text, pt or None, ot or None


async def run_phase_prompt_structured(
    *,
    provider: str,
    model: Optional[str] = None,
    phase_prompt: str,
    response_schema: type[BaseModel],
    lesson_context: str,
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
    phase_name: str = "?",
    max_output_tokens: Optional[int] = None,
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
    attachments: list[Path] = (),
) -> tuple[BaseModel, Optional[int], Optional[int]]:
    """Structured-output phase. Wraps :func:`run_phase` and returns
    ``(parsed, prompt_tokens, output_tokens)`` to match the gemini equivalent."""
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
        schema=response_schema,
        difficulty=difficulty,
        max_output_tokens=max_output_tokens,
    )
    if result.parsed is None:
        # Should be unreachable: run_phase raises before returning a
        # PhaseResult with parsed=None when a schema is supplied.
        raise RuntimeError(
            f"run_phase_prompt_structured: parsed is None for {phase_name}"
        )
    pt = int(result.usage.get("prompt_tokens") or 0)
    ot = int(result.usage.get("output_tokens") or 0)
    return result.parsed, pt or None, ot or None


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
    "ProviderSpec",
    "STRUCTURED_PHASE_SCHEMAS",
    "_PROVIDER_DEFAULT_MODEL",
    "_resolve_model",
    "run_phase",
    "run_phase_with_fallback",
    "resolve_provider_chain",
    "extract_toc",
    "extract_lesson_context",
    "run_phase_prompt",
    "run_phase_prompt_structured",
    "record_cached_lesson_extract",
]
