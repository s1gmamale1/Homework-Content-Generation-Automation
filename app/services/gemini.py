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
from app.schemas import ExtractedTOC

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
        logger.info(f"gemini client initialised | model={settings.gemini_model}")
    return _client


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
        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
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

    text = response.text or ""
    duration_s = perf_counter() - t0
    usage = _extract_usage(response)
    prompt_total = int((usage["usage_metadata"] or {}).get("prompt_token_count") or 0) or usage[
        "input_text_token_count"
    ]
    cached_in = _cached_tokens(response)

    logger.success(
        f"gemini lesson.extract done | section={section_number} "
        f"output_chars={len(text)} input={prompt_total:,} (cached={cached_in:,}) "
        f"output={usage['candidates_token_count']:,} duration_ms={duration_s * 1000:.0f}"
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
    return text, usage["input_text_token_count"] or None, usage["candidates_token_count"] or None


# ─────────────────────────────────────────────────────────────────────
# Phase prompt (homework content phases)
# ─────────────────────────────────────────────────────────────────────

async def run_phase_prompt(
    *,
    phase_prompt: str,
    file_uri: Optional[str] = None,
    attach_file: bool = False,
    cached_content: Optional[str] = None,
    lesson_context: str,
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
    phase_name: str = "?",
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

    user_blocks: list[str] = ["## Lesson context", lesson_context]
    if difficulty is not None:
        user_blocks.extend(["", "## Difficulty", difficulty.upper()])
    if prior_outputs:
        user_blocks.append("\n## Prior phase outputs")
        for name, body in prior_outputs.items():
            user_blocks.append(f"\n### {name}\n{body}")
    user_blocks.append(_NO_PREAMBLE)
    user_text = "\n".join(user_blocks)

    contents: list[Any] = []
    file_attached = bool(cached_content) or (attach_file and file_uri)
    if cached_content is None and attach_file and file_uri:
        contents.append(
            types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf")
        )
    contents.append(user_text)

    config_kwargs: dict[str, Any] = {"system_instruction": phase_prompt}
    if cached_content:
        config_kwargs["cached_content"] = cached_content

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
    return text, in_total or None, out_total or None


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

        total_token_count, input_text_token_count, input_audio_token_count,
        candidates_token_count, thoughts_token_count, usage_metadata (raw dict)
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
            out["usage_metadata"] = {"prompt_token_count": prompt_total}
    except Exception:
        out["usage_metadata"] = {"prompt_token_count": prompt_total}

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
    input_text_token_count: int = 0,
    input_audio_token_count: int = 0,
    candidates_token_count: int = 0,
    thoughts_token_count: int = 0,
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
                input_text_token_count=input_text_token_count,
                input_audio_token_count=input_audio_token_count,
                candidates_token_count=candidates_token_count,
                thoughts_token_count=thoughts_token_count,
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
