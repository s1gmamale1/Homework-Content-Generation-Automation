"""Async-native wrapper around the google-genai SDK.

All model calls use `client.aio.*` (no `asyncio.to_thread`) so:
- the asyncio event loop stays free during long Gemini calls;
- cancellation propagates cleanly when an SSE consumer disconnects;
- thread-pool slots aren't tied up under load.

Every public function emits step-by-step `loguru` logs *and* persists a row
into `gemini_usages` so end-to-end behaviour can be traced from the DB.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Optional
from uuid import UUID

from google import genai
from google.genai import types
from loguru import logger

from app.config import settings
from app.db import SessionLocal
from app.repositories import gemini_usage as usage_repo
from app.schemas import (
    ClassifyDecision,
    ExtractedTOC,
    FinalChallenge,
    FlashcardsPack,
    GamesPack,
    MemorySprintPack,
    ReadingPassage,
)

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
        logger.info(f"gemini client initialised | model={settings.gemini_model}")
    return _client


# Process-wide concurrency cap on Gemini calls. Without this, the queue
# worker (concurrency=N pipelines) × parallel scheduler (waves of 4-5
# concurrent phases per pipeline) can fan out to 20+ in-flight calls per
# second, blowing past most rate-limit tiers. This semaphore queues calls
# transparently so each succeeds rather than cascading into 429s.
#
# Lazy-init so the value picks up `settings` at first use (and so test
# code can rebind the setting before the first call).
_gemini_call_semaphore: Optional[asyncio.Semaphore] = None


def _gemini_semaphore() -> asyncio.Semaphore:
    global _gemini_call_semaphore
    if _gemini_call_semaphore is None:
        _gemini_call_semaphore = asyncio.Semaphore(settings.gemini_max_concurrency)
    return _gemini_call_semaphore


# ─────────────────────────────────────────────────────────────────────
# Files API
# ─────────────────────────────────────────────────────────────────────

async def upload_file(
    path: Path,
    mime_type: str = "application/pdf",
    retries: int = 60,
    poll_interval: float = 1.0,
    *,
    book_id: Optional[UUID] = None,
) -> tuple[types.File, datetime]:
    """Upload a file via Gemini's Files API and wait until it is ACTIVE."""
    client = _get_client()
    size_mb = path.stat().st_size / (1024 * 1024)
    logger.info(f"gemini upload start | file={path.name} size={size_mb:.2f}MB mime={mime_type}")
    started_at = datetime.now(timezone.utc)
    t0 = perf_counter()

    try:
        uploaded = await client.aio.files.upload(file=path)
        logger.info(
            f"gemini upload accepted | name={uploaded.name} state={uploaded.state.name} "
            f"upload_ms={(perf_counter() - t0) * 1000:.0f}"
        )

        # Poll for ACTIVE — backend may take a few seconds to process the file.
        poll_start = perf_counter()
        for attempt in range(1, retries + 1):
            f = await client.aio.files.get(name=uploaded.name)
            if f.state.name == "ACTIVE":
                total_ms = (perf_counter() - t0) * 1000
                poll_ms = (perf_counter() - poll_start) * 1000
                logger.success(
                    f"gemini file active | name={uploaded.name} attempt={attempt} "
                    f"poll_ms={poll_ms:.0f} total_ms={total_ms:.0f}"
                )
                expires_at = datetime.now(timezone.utc) + timedelta(hours=47)
                await _record(
                    operation="files.upload",
                    book_id=book_id,
                    started_at=started_at,
                    duration_s=total_ms / 1000,
                    success=True,
                    usage_metadata={
                        "file_name": uploaded.name,
                        "file_uri": uploaded.uri,
                        "file_size_bytes": path.stat().st_size,
                        "mime_type": mime_type,
                        "polls": attempt,
                    },
                )
                return uploaded, expires_at

            if f.state.name == "FAILED":
                logger.error(f"gemini file processing failed | name={uploaded.name}")
                raise RuntimeError("File processing failed")

            if attempt == 1 or attempt % 5 == 0:
                logger.debug(
                    f"gemini file pending | name={uploaded.name} attempt={attempt} "
                    f"state={f.state.name}"
                )
            await asyncio.sleep(poll_interval)

        raise TimeoutError(
            f"File processing timed out after {retries} polls "
            f"({retries * poll_interval:.0f}s)"
        )

    except Exception as exc:
        total_s = perf_counter() - t0
        await _record(
            operation="files.upload",
            book_id=book_id,
            started_at=started_at,
            duration_s=total_s,
            success=False,
            error_message=str(exc),
            usage_metadata={
                "file_size_bytes": path.stat().st_size if path.exists() else None,
                "mime_type": mime_type,
            },
        )
        raise


# ─────────────────────────────────────────────────────────────────────
# TOC extraction
# ─────────────────────────────────────────────────────────────────────

async def extract_toc(
    file: types.File,
    subject: str,
    *,
    book_id: Optional[UUID] = None,
) -> ExtractedTOC:
    client = _get_client()
    prompt = (
        f"You are reading a {subject} curriculum textbook. "
        "Extract the full Table of Contents as structured JSON. "
        "For every numbered section (e.g., §1, §2 ... or '1.1', '1.2' ...), "
        "produce one entry with: chapter_number (text), chapter_title, "
        "section_number, section_title, page_start, page_end. "
        "If the book is organized by chapters, use the chapter title as 'chapter_title' "
        "for every section under it. Do not invent sections. Order entries as they appear."
    )
    logger.info(f"gemini toc.extract start | subject={subject} file={file.name}")
    started_at = datetime.now(timezone.utc)
    t0 = perf_counter()

    try:
        async with _gemini_semaphore():
            response = await client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=[
                    types.Part.from_uri(file_uri=file.uri, mime_type=file.mime_type),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractedTOC,
                ),
            )
    except Exception as exc:
        total_s = perf_counter() - t0
        await _record(
            operation="toc.extract",
            book_id=book_id,
            started_at=started_at,
            duration_s=total_s,
            success=False,
            error_message=str(exc),
        )
        raise

    duration_s = perf_counter() - t0
    usage = _extract_usage(response)

    parsed = response.parsed
    if not isinstance(parsed, ExtractedTOC):
        text_preview = (response.text or "")[:200]
        msg = (
            f"Gemini returned no parseable TOC structure "
            f"(parsed type={type(parsed).__name__}, text_preview={text_preview!r})"
        )
        logger.error(
            f"gemini toc.extract returned no parseable structure | "
            f"parsed_type={type(parsed).__name__} duration_ms={duration_s * 1000:.0f}"
        )
        await _record(
            operation="toc.extract",
            book_id=book_id,
            started_at=started_at,
            duration_s=duration_s,
            success=False,
            error_message=msg,
            **usage,
        )
        raise RuntimeError(msg)

    prompt_total = int((usage["usage_metadata"] or {}).get("prompt_token_count") or 0) or usage[
        "input_text_token_count"
    ]
    cached_in = _cached_tokens(response)
    logger.success(
        f"gemini toc.extract done | entries={len(parsed.entries)} "
        f"input={prompt_total:,} (cached={cached_in:,}) "
        f"output={usage['candidates_token_count']:,} "
        f"duration_ms={duration_s * 1000:.0f}"
    )
    await _record(
        operation="toc.extract",
        book_id=book_id,
        started_at=started_at,
        duration_s=duration_s,
        success=True,
        **usage,
    )
    return parsed


# ─────────────────────────────────────────────────────────────────────
# Lesson context (per-section "extract" phase)
# ─────────────────────────────────────────────────────────────────────

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


# Universal "no preamble" directive. Appended to every phase user message so
# the model emits the deliverable directly without conversational scaffolding
# like 'Mana, …', 'Quyida, …', 'Here are …', 'Below is …'. Bilingual on purpose
# — the prompts are in Uzbek context and the curriculum output is in Uzbek.
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


# Universal SVG rules. Injected into every MD-producing curriculum phase so
# inline diagrams have a consistent look: white background, content-scaled
# size, dark high-contrast strokes, every part labeled. Overrides any
# conflicting size guidance inside the per-subject .md prompt files.
# Phases that produce inline SVG diagrams. Other phases (memory-sprint,
# reflection, classify) get a slimmer prompt — saving ~450 tokens per call.
_SVG_PHASES: set[str] = {
    "preview-hard", "preview-easy", "preview",
    "real-life", "consolidation",
    "flashcards", "game-breaks", "final-challenge", "reading",
}


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


async def extract_lesson_context(
    file_uri: str,
    section_title: str,
    section_number: str,
    page_start: Optional[int],
    page_end: Optional[int],
    *,
    cached_content: Optional[str] = None,
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
) -> tuple[str, Optional[int], Optional[int]]:
    """Run the per-section 'extract' phase. If `cached_content` is supplied,
    we skip the inline file Part and reference the cache instead — billed at
    ~25% of regular per-token rate for the PDF portion."""
    client = _get_client()
    prompt = _EXTRACT_PHASE_PROMPT.format(
        title=section_title,
        number=section_number,
        ps=page_start if page_start is not None else "?",
        pe=page_end if page_end is not None else "?",
        rules=_NO_PREAMBLE,
    )

    contents: list[Any] = []
    if cached_content is None:
        contents.append(types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf"))
    contents.append(prompt)

    config_kwargs: dict[str, Any] = {}
    if cached_content:
        config_kwargs["cached_content"] = cached_content

    file_state = "CACHE" if cached_content else "INLINE"
    logger.info(
        f"gemini lesson.extract start | section={section_number} "
        f"title={section_title!r} pages={page_start}-{page_end} file={file_state}"
    )
    started_at = datetime.now(timezone.utc)
    t0 = perf_counter()

    try:
        async with _gemini_semaphore():
            response = await client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
    except Exception as exc:
        total_s = perf_counter() - t0
        await _record(
            operation="lesson.extract",
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
            started_at=started_at,
            duration_s=total_s,
            success=False,
            error_message=str(exc),
        )
        raise

    text = _strip_preamble(response.text or "")
    duration_s = perf_counter() - t0
    usage = _extract_usage(response)
    cached_in = _cached_tokens(response)
    # Use prompt_token_count (full input incl. PDF/IMAGE modality) — the
    # text-only column would understate by orders of magnitude here since
    # extract_lesson_context always reads the PDF.
    prompt_total = usage["prompt_token_count"] or usage["input_text_token_count"]
    out_total = usage["candidates_token_count"]

    logger.success(
        f"gemini lesson.extract done | section={section_number} "
        f"output_chars={len(text)} input={prompt_total:,} (cached={cached_in:,}) "
        f"output={out_total:,} duration_ms={duration_s * 1000:.0f}"
    )
    await _record(
        operation="lesson.extract",
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        started_at=started_at,
        duration_s=duration_s,
        success=True,
        **usage,
    )
    return text, prompt_total or None, out_total or None


# ─────────────────────────────────────────────────────────────────────
# Phase prompt (homework content phases)
# ─────────────────────────────────────────────────────────────────────


# Phases that produce JSON directly via response_schema in one Gemini call.
# Replaces the older MD-then-extract pattern where each of these phases cost
# two roundtrips (one to write MD, another to re-parse it as JSON).
STRUCTURED_PHASE_SCHEMAS: dict[str, type] = {
    "classify": ClassifyDecision,
    "flashcards": FlashcardsPack,
    "memory-sprint": MemorySprintPack,
    "game-breaks": GamesPack,
    "final-challenge": FinalChallenge,
    "reading": ReadingPassage,
}


async def run_phase_prompt(
    *,
    phase_prompt: str,
    file_uri: Optional[str] = None,
    attach_file: bool = False,
    cached_content: Optional[str] = None,
    cache_holds_text_context: bool = False,
    lesson_context: str,
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
    phase_name: str = "?",
    max_output_tokens: Optional[int] = None,
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
) -> tuple[str, Optional[int], Optional[int]]:
    """Run a single content phase. Token-efficient by default:

    - `attach_file=False` (default) sends only the lesson_context — the PDF is
      not re-uploaded, saving the bulk of input tokens. Only opt in when a
      phase truly needs the original textbook visuals.
    - `cached_content` references a Gemini context cache (created via
      `create_cache`) so repeated phases that DO need the file pay ~25% rather
      than 100% of the per-call input cost.
    """
    client = _get_client()

    # Gemini API constraint: cached_content cannot be combined with
    # system_instruction in the same request. When a cache is active, move
    # the phase prompt into the user message instead — the model still sees
    # the same content, just without the elevated "system" role.
    use_cache = cached_content is not None
    user_blocks: list[str] = []
    if use_cache:
        user_blocks.extend(["## Phase instruction", phase_prompt, ""])
    # When cached_content holds the per-job text context (lesson_context +
    # SVG_RULES + NO_PREAMBLE), drop those from user_blocks — repeating them
    # would double the bill (cached portion + inline portion).
    if not cache_holds_text_context:
        user_blocks.extend(["## Lesson context", lesson_context])
    if difficulty is not None:
        user_blocks.extend(["", "## Difficulty", difficulty.upper()])
    # SVG rules are only useful for phases that actually generate diagrams.
    # Skip on text-only phases (memory-sprint, reflection, classify) — saves
    # ~450 tokens per call. Also skipped when already in the per-job cache.
    if not cache_holds_text_context and phase_name in _SVG_PHASES:
        user_blocks.append(_SVG_RULES)
    if prior_outputs:
        user_blocks.append("\n## Prior phase outputs")
        for name, body in prior_outputs.items():
            user_blocks.append(f"\n### {name}\n{body}")
    if not cache_holds_text_context:
        user_blocks.append(_NO_PREAMBLE)
    user_text = "\n".join(user_blocks)

    contents: list[Any] = []
    if cached_content is None and attach_file and file_uri:
        contents.append(
            types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf")
        )
    contents.append(user_text)

    config_kwargs: dict[str, Any] = {}
    if use_cache:
        config_kwargs["cached_content"] = cached_content
    else:
        config_kwargs["system_instruction"] = phase_prompt
    if max_output_tokens:
        config_kwargs["max_output_tokens"] = max_output_tokens

    # Optimization-visibility log: shows exactly which token-savers are active
    # for this phase. Use this to verify "did we actually skip the PDF here?".
    file_state = (
        "CACHE" if cached_content
        else "INLINE" if (attach_file and file_uri)
        else "OFF"
    )
    prior_names = list(prior_outputs.keys())
    logger.info(
        f"gemini phase.run config | phase={phase_name} "
        f"file={file_state} "
        f"prior={len(prior_names)}{prior_names if prior_names else ''} "
        f"lesson_chars={len(lesson_context)} "
        f"sys_chars={len(phase_prompt)} "
        f"user_chars={len(user_text)} "
        f"difficulty={difficulty}"
    )
    started_at = datetime.now(timezone.utc)
    t0 = perf_counter()

    try:
        async with _gemini_semaphore():
            response = await client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
    except Exception as exc:
        total_s = perf_counter() - t0
        await _record(
            operation="phase.run",
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
            started_at=started_at,
            duration_s=total_s,
            success=False,
            error_message=str(exc),
            usage_metadata={"phase_name": phase_name, "difficulty": difficulty},
        )
        raise

    text = _strip_preamble(response.text or "")
    duration_s = perf_counter() - t0
    usage = _extract_usage(response)
    cached_in = _cached_tokens(response)
    usage_meta = dict(usage["usage_metadata"] or {})
    usage_meta["phase_name"] = phase_name
    usage_meta["difficulty"] = difficulty
    usage["usage_metadata"] = usage_meta

    # Truth comes from the SDK's prompt_token_count (full prompt incl. PDF /
    # image modality). Our text-only column understates by orders of magnitude
    # whenever a phase attaches the PDF.
    prompt_total = int(usage_meta.get("prompt_token_count") or 0)
    if prompt_total == 0:
        prompt_total = usage["input_text_token_count"]
    out_total = usage["candidates_token_count"]
    fresh_in = max(prompt_total - cached_in, 0)
    cache_pct = (cached_in / prompt_total * 100) if prompt_total else 0

    logger.success(
        f"gemini phase.run billed | phase={phase_name} "
        f"input={prompt_total:,} (fresh={fresh_in:,} cached={cached_in:,} {cache_pct:.0f}%) "
        f"output={out_total:,} "
        f"output_chars={len(text)} duration_ms={duration_s * 1000:.0f}"
    )
    await _record(
        operation="phase.run",
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        started_at=started_at,
        duration_s=duration_s,
        success=True,
        **usage,
    )
    return text, prompt_total or None, out_total or None


async def run_phase_prompt_structured(
    *,
    phase_prompt: str,
    response_schema: type,
    file_uri: Optional[str] = None,
    attach_file: bool = False,
    cached_content: Optional[str] = None,
    cache_holds_text_context: bool = False,
    lesson_context: str,
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
    phase_name: str = "?",
    max_output_tokens: Optional[int] = None,
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
) -> tuple[Any, Optional[int], Optional[int]]:
    """Structured-output sibling of `run_phase_prompt`.

    Sends the same prompt + lesson_context + prior_outputs but constrains the
    response to JSON conforming to `response_schema`. Returns the parsed
    Pydantic instance directly. One Gemini call replaces the older
    MD-then-extract pattern (which paid for two roundtrips per JSON phase).
    """
    client = _get_client()

    # Same constraint as run_phase_prompt: cached_content forbids
    # system_instruction. When a cache is active, move phase prompt into the
    # user message instead.
    use_cache = cached_content is not None
    user_blocks: list[str] = []
    if use_cache:
        user_blocks.extend(["## Phase instruction", phase_prompt, ""])
    if not cache_holds_text_context:
        user_blocks.extend(["## Lesson context", lesson_context])
    if difficulty is not None:
        user_blocks.extend(["", "## Difficulty", difficulty.upper()])
    # SVG rules: only for phases whose schema string fields actually carry
    # diagrams. memory-sprint is tap-only short text — saves ~450 tokens.
    # Skipped when already in the per-job text cache.
    has_svg = phase_name in _SVG_PHASES
    if has_svg and not cache_holds_text_context:
        user_blocks.append(_SVG_RULES)
    if prior_outputs:
        user_blocks.append("\n## Prior phase outputs")
        for name, body in prior_outputs.items():
            user_blocks.append(f"\n### {name}\n{body}")
    # Schema-mode override. Note: NO_PREAMBLE is omitted here — Gemini's
    # structured-output decoder rejects preambles by definition (the response
    # must be valid JSON), so we save those tokens too.
    output_format = (
        "\n## OUTPUT FORMAT\n"
        "Respond with JSON conforming to the provided response schema. "
        "Ignore any markdown-formatting instructions in the prompt — "
        "the schema is the deliverable. Preserve the original language.\n\n"
        "**Curriculum metadata tags** like `[Bloom: LX]`, `[PISA: LX]`, "
        "`[Damage: -X HP]`, `[Difficulty: ...]` must go ONLY in their "
        "matching schema fields (e.g., bloom_level, pisa_level, damage). "
        "NEVER embed these bracket tags inside `prompt`, `options`, "
        "`explanation`, `front`, `back`, or any other student-facing "
        "string — the student sees those rendered raw. If the schema has "
        "no field for a particular tag, drop the tag entirely."
    )
    if has_svg:
        output_format += (
            "\n\nIf you embed inline <svg> in any string field, follow the "
            "SVG rules above (white background, content-scaled size, dark "
            "strokes)."
        )
    user_blocks.append(output_format)
    user_text = "\n".join(user_blocks)

    contents: list[Any] = []
    if cached_content is None and attach_file and file_uri:
        contents.append(
            types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf")
        )
    contents.append(user_text)

    config_kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
        "response_schema": response_schema,
    }
    if use_cache:
        config_kwargs["cached_content"] = cached_content
    else:
        config_kwargs["system_instruction"] = phase_prompt
    if max_output_tokens:
        config_kwargs["max_output_tokens"] = max_output_tokens

    file_state = (
        "CACHE" if cached_content
        else "INLINE" if (attach_file and file_uri)
        else "OFF"
    )
    prior_names = list(prior_outputs.keys())
    logger.info(
        f"gemini phase.run config | phase={phase_name} mode=JSON "
        f"file={file_state} "
        f"prior={len(prior_names)}{prior_names if prior_names else ''} "
        f"lesson_chars={len(lesson_context)} sys_chars={len(phase_prompt)} "
        f"user_chars={len(user_text)} difficulty={difficulty} "
        f"schema={response_schema.__name__}"
    )
    started_at = datetime.now(timezone.utc)
    t0 = perf_counter()

    try:
        async with _gemini_semaphore():
            response = await client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
    except Exception as exc:
        total_s = perf_counter() - t0
        await _record(
            operation="phase.run",
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
            started_at=started_at,
            duration_s=total_s,
            success=False,
            error_message=str(exc),
            usage_metadata={
                "phase_name": phase_name,
                "difficulty": difficulty,
                "mode": "json",
            },
        )
        raise

    duration_s = perf_counter() - t0
    usage = _extract_usage(response)
    parsed = response.parsed
    if not isinstance(parsed, response_schema):
        # Structured-mode parse failure. Pull every diagnostic the SDK
        # exposes — finish_reason, raw text preview, output token count —
        # so the next failure is debuggable from the log alone instead of
        # requiring a re-run with a debugger attached.
        finish_reason = "?"
        text_preview = ""
        try:
            cand = (response.candidates or [None])[0]
            if cand is not None:
                fr = getattr(cand, "finish_reason", None)
                finish_reason = getattr(fr, "name", str(fr)) if fr else "?"
            raw_text = response.text or ""
            text_preview = raw_text[:200].replace("\n", " ")
        except Exception:
            pass

        out_tokens = usage["candidates_token_count"]
        msg = (
            f"non-parseable structure (parsed={type(parsed).__name__}, "
            f"finish_reason={finish_reason}, output_tokens={out_tokens}, "
            f"preview={text_preview!r})"
        )
        usage_meta = dict(usage["usage_metadata"] or {})
        usage_meta["phase_name"] = phase_name
        usage_meta["difficulty"] = difficulty
        usage_meta["mode"] = "json"
        usage_meta["finish_reason"] = finish_reason
        usage["usage_metadata"] = usage_meta
        logger.warning(f"gemini phase.run | phase={phase_name} {msg}")
        await _record(
            operation="phase.run",
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
            started_at=started_at,
            duration_s=duration_s,
            success=False,
            error_message=msg,
            **usage,
        )
        raise RuntimeError(f"phase.run {phase_name}: {msg}")

    cached_in = _cached_tokens(response)
    usage_meta = dict(usage["usage_metadata"] or {})
    usage_meta["phase_name"] = phase_name
    usage_meta["difficulty"] = difficulty
    usage_meta["mode"] = "json"
    usage["usage_metadata"] = usage_meta

    prompt_total = int(usage_meta.get("prompt_token_count") or 0)
    if prompt_total == 0:
        prompt_total = usage["input_text_token_count"]
    out_total = usage["candidates_token_count"]
    fresh_in = max(prompt_total - cached_in, 0)
    cache_pct = (cached_in / prompt_total * 100) if prompt_total else 0

    logger.success(
        f"gemini phase.run billed | phase={phase_name} mode=JSON "
        f"input={prompt_total:,} (fresh={fresh_in:,} cached={cached_in:,} {cache_pct:.0f}%) "
        f"output={out_total:,} schema={response_schema.__name__} "
        f"duration_ms={duration_s * 1000:.0f}"
    )
    await _record(
        operation="phase.run",
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        started_at=started_at,
        duration_s=duration_s,
        success=True,
        **usage,
    )
    return parsed, prompt_total or None, out_total or None



# ─────────────────────────────────────────────────────────────────────
# Context cache (token saver for phases that re-attach the same PDF)
# ─────────────────────────────────────────────────────────────────────

async def create_cache(
    *,
    file_uri: str,
    extra_text: Optional[str] = None,
    ttl: str = "21600s",  # 6h — long enough that a second job on the same book hits the cache
) -> Optional[tuple[str, datetime]]:
    """Create a Gemini context cache containing the PDF and optionally extra
    text. Returns `(cache_name, expire_time)` so callers can persist the
    expiry alongside the name. Subsequent generate_content calls passing
    `cached_content=cache_name` are billed at ~25% of regular per-token cost
    for the cached portion.

    Returns None if creation fails (e.g., model doesn't support caching, or
    contents are below the SDK's minimum size). Callers should fall back to
    inline content in that case.
    """
    client = _get_client()
    contents: list[Any] = [
        types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf"),
    ]
    if extra_text:
        contents.append(extra_text)

    t0 = perf_counter()
    try:
        cache = await client.aio.caches.create(
            model=settings.gemini_model,
            config=types.CreateCachedContentConfig(contents=contents, ttl=ttl),
        )
    except Exception as exc:
        logger.warning(
            f"gemini cache.create failed: {exc!r} — falling back to inline content"
        )
        return None

    # Prefer the SDK's authoritative expire_time when present; fall back to
    # now+TTL so we always have a value to persist.
    expire_time = getattr(cache, "expire_time", None)
    if not isinstance(expire_time, datetime):
        ttl_seconds = int(ttl.rstrip("s")) if ttl.endswith("s") and ttl[:-1].isdigit() else 21600
        expire_time = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    duration_ms = (perf_counter() - t0) * 1000
    logger.success(
        f"gemini cache created | name={cache.name} expires={expire_time.isoformat()} "
        f"duration_ms={duration_ms:.0f}"
    )
    return cache.name, expire_time



async def create_text_cache(
    *,
    text: str,
    ttl: str = "1800s",  # 30 min — typical job lifetime + headroom
) -> Optional[str]:
    """Create a text-only Gemini context cache. Used per-job to hold the
    distilled `lesson_context` + universal directives (SVG rules, no-preamble)
    so every content-phase call references them at ~25% of input rate instead
    of paying full freight on each.

    Returns the cache name on success, None if Gemini rejects (commonly because
    the payload is below the model's minimum cache size — caller should fall
    back to inline content).
    """
    client = _get_client()
    t0 = perf_counter()
    try:
        cache = await client.aio.caches.create(
            model=settings.gemini_model,
            config=types.CreateCachedContentConfig(contents=[text], ttl=ttl),
        )
    except Exception as exc:
        # Most common failure: payload below model's min cache size (~1024
        # tokens for Flash). Quietly fall back so the pipeline still runs.
        logger.info(
            f"gemini text-cache create skipped: {type(exc).__name__} "
            f"(falling back to inline) | chars={len(text)}"
        )
        return None

    duration_ms = (perf_counter() - t0) * 1000
    logger.success(
        f"gemini text-cache created | name={cache.name} chars={len(text)} "
        f"duration_ms={duration_ms:.0f}"
    )
    return cache.name


async def delete_cache(cache_name: str) -> None:
    """Best-effort cache cleanup. Failures are logged but ignored — caches
    auto-expire after their TTL anyway."""
    client = _get_client()
    try:
        await client.aio.caches.delete(name=cache_name)
        logger.debug(f"gemini cache deleted | name={cache_name}")
    except Exception as exc:
        logger.warning(f"gemini cache.delete failed: {exc!r}")


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

# Common conversational openers Gemini sometimes emits before the actual
# deliverable, despite the explicit "no preamble" directive in the prompt.
# Stripped post-hoc as defense-in-depth. Match a single leading line only.
_PREAMBLE_PATTERNS = (
    "Mana,", "Mana ", "Quyida,", "Quyida ", "Avvalo,", "Albatta,",
    "Here are", "Here is", "Below is", "Below are", "Sure,", "Of course,",
    "Certainly,", "Marhamat,",
)


def _strip_preamble(text: str) -> str:
    """If the model emitted a single leading conversational line, drop it.

    Conservative on purpose — only strips an opening line that BOTH starts with
    a known pattern AND looks like a sentence (no markdown heading, no list
    bullet). Real homework content always begins with `#`, `-`, `**`, etc.
    """
    if not text:
        return text
    stripped = text.lstrip()
    if not stripped:
        return text
    first_line, _, rest = stripped.partition("\n")
    starts_with_pattern = any(first_line.startswith(p) for p in _PREAMBLE_PATTERNS)
    looks_like_content = first_line.startswith(("#", "-", "*", "•", "|", "```", "<"))
    if starts_with_pattern and not looks_like_content:
        return rest.lstrip()
    return text


def _uri(file_name: str) -> str:
    if file_name.startswith("https://") or file_name.startswith("files/"):
        return file_name
    return f"files/{file_name}"


def _extract_usage(response: Any) -> dict[str, Any]:
    """Pull all interesting fields out of the SDK response.usage_metadata so we
    can record one tidy dict to the DB. Returns a dict with these keys:

        total_token_count, prompt_token_count, input_text_token_count,
        input_image_token_count, candidates_token_count, thoughts_token_count,
        cached_content_token_count, usage_metadata (raw dict)

    The dict's keys are exactly the kwargs `_record(**usage)` accepts, so the
    call site can spread it through to the gemini_usages writer.
    """
    out: dict[str, Any] = {
        "total_token_count": 0,
        "prompt_token_count": 0,
        "input_text_token_count": 0,
        "input_image_token_count": 0,
        "candidates_token_count": 0,
        "thoughts_token_count": 0,
        "cached_content_token_count": 0,
        "usage_metadata": None,
    }
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return out

    out["total_token_count"] = getattr(meta, "total_token_count", 0) or 0
    out["candidates_token_count"] = getattr(meta, "candidates_token_count", 0) or 0
    out["thoughts_token_count"] = getattr(meta, "thoughts_token_count", 0) or 0
    out["cached_content_token_count"] = getattr(meta, "cached_content_token_count", 0) or 0
    out["prompt_token_count"] = getattr(meta, "prompt_token_count", 0) or 0

    # Input is split across modalities; SDK reports a list of (modality, token_count).
    # This project only encounters TEXT and IMAGE (PDF pages render as IMAGE).
    text_tokens = 0
    image_tokens = 0
    details = getattr(meta, "prompt_tokens_details", None) or []
    for d in details:
        modality = getattr(getattr(d, "modality", None), "name", "") or ""
        count = getattr(d, "token_count", 0) or 0
        if modality.upper() == "TEXT":
            text_tokens += count
        elif modality.upper() == "IMAGE":
            image_tokens += count
    # Fallback when SDK doesn't expose modality breakdown — assume all text.
    if text_tokens == 0 and image_tokens == 0:
        text_tokens = out["prompt_token_count"]
    out["input_text_token_count"] = text_tokens
    out["input_image_token_count"] = image_tokens

    # Raw dump for forward-compat with new SDK fields
    try:
        if hasattr(meta, "model_dump"):
            out["usage_metadata"] = meta.model_dump(mode="json")
        elif hasattr(meta, "to_json_dict"):
            out["usage_metadata"] = meta.to_json_dict()
        else:
            out["usage_metadata"] = {"prompt_token_count": out["prompt_token_count"]}
    except Exception:
        out["usage_metadata"] = {"prompt_token_count": out["prompt_token_count"]}

    return out


def _cached_tokens(response: Any) -> int:
    """Number of input tokens served from a Gemini context cache for this call.
    Returns 0 if caching wasn't used or the SDK doesn't surface the field.
    Logged so we can verify the cache is actually hitting at the API level."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return 0
    return getattr(meta, "cached_content_token_count", 0) or 0


def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.2f}s"


async def record_cached_lesson_extract(
    *,
    homework_job_id: UUID,
    phase_output_id: UUID,
    source_job_id: UUID,
    source_phase_output_id: UUID,
) -> None:
    """Persist a `lesson.extract` row with all-zero token counts and a
    cache_hit marker. This makes cross-job extract reuse visible in the
    token summary table (you'll see `lesson.extract` with input=0)."""
    await _record(
        operation="lesson.extract",
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
        started_at=datetime.now(timezone.utc),
        duration_s=0,
        success=True,
        usage_metadata={
            "cache_hit": True,
            "source_job_id": str(source_job_id),
            "source_phase_output_id": str(source_phase_output_id),
        },
    )


async def _record(
    *,
    operation: str,
    started_at: datetime,
    duration_s: float,
    success: bool = True,
    book_id: Optional[UUID] = None,
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
    total_token_count: int = 0,
    prompt_token_count: int = 0,
    input_text_token_count: int = 0,
    input_image_token_count: int = 0,
    candidates_token_count: int = 0,
    thoughts_token_count: int = 0,
    cached_content_token_count: int = 0,
    usage_metadata: Optional[dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> None:
    """Best-effort write to gemini_usages. Failures here MUST NOT crash the
    outer caller — we log and swallow."""
    try:
        async with SessionLocal() as session:
            await usage_repo.create(
                session,
                operation=operation,
                model_name=settings.gemini_model,
                book_id=book_id,
                homework_job_id=homework_job_id,
                phase_output_id=phase_output_id,
                total_token_count=total_token_count,
                prompt_token_count=prompt_token_count,
                input_text_token_count=input_text_token_count,
                input_image_token_count=input_image_token_count,
                candidates_token_count=candidates_token_count,
                thoughts_token_count=thoughts_token_count,
                cached_content_token_count=cached_content_token_count,
                usage_metadata=usage_metadata,
                duration=_format_duration(duration_s),
                success=success,
                error_message=error_message,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            await session.commit()
    except Exception as exc:
        logger.warning(f"failed to record gemini_usage: {exc}")
