import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

from app.config import settings
from app.schemas import ExtractedTOC

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def upload_file(path: Path, mime_type: str = "application/pdf") -> tuple[str, datetime]:
    client = _get_client()
    file = await asyncio.to_thread(
        client.files.upload, file=str(path), config={"mime_type": mime_type}
    )
    if not file.name:
        raise RuntimeError("Gemini Files API upload returned no file name")
    expires_at = datetime.now(timezone.utc) + timedelta(hours=47)
    return file.name, expires_at


async def extract_toc(file_uri: str, subject: str) -> ExtractedTOC:
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

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.gemini_model,
        contents=[
            types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedTOC,
        ),
    )

    parsed = response.parsed
    if not isinstance(parsed, ExtractedTOC):
        text_preview = (response.text or "")[:200]
        raise RuntimeError(
            f"Gemini returned no parseable TOC structure "
            f"(parsed type={type(parsed).__name__}, text_preview={text_preview!r})"
        )
    return parsed


_EXTRACT_PHASE_PROMPT = (
    'You are reading the attached textbook. The lesson is "{title}" '
    "(section {number}, pages {ps}-{pe}).\n\n"
    "Extract all factual lesson content the textbook teaches on these pages. "
    "Include: key terms with definitions, named processes/mechanisms with steps, "
    "diagrams/visuals (describe them), worked examples, formulas, "
    "organisms/structures with functions, historical references, experiments, "
    "and comparison tables.\n\n"
    "Output as structured Markdown. Be faithful to the source — do not invent."
)


async def extract_lesson_context(
    file_uri: str,
    section_title: str,
    section_number: str,
    page_start: Optional[int],
    page_end: Optional[int],
) -> tuple[str, Optional[int], Optional[int]]:
    client = _get_client()
    prompt = _EXTRACT_PHASE_PROMPT.format(
        title=section_title,
        number=section_number,
        ps=page_start if page_start is not None else "?",
        pe=page_end if page_end is not None else "?",
    )
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.gemini_model,
        contents=[
            types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf"),
            prompt,
        ],
    )
    return response.text or "", _tokens_in(response), _tokens_out(response)


async def run_phase_prompt(
    *,
    phase_prompt: str,
    file_uri: str,
    lesson_context: str,
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
) -> tuple[str, Optional[int], Optional[int]]:
    client = _get_client()

    user_blocks: list[str] = ["## Lesson context", lesson_context]
    if difficulty is not None:
        user_blocks.extend(["", "## Difficulty", difficulty.upper()])
    if prior_outputs:
        user_blocks.append("\n## Prior phase outputs")
        for name, body in prior_outputs.items():
            user_blocks.append(f"\n### {name}\n{body}")
    user_text = "\n".join(user_blocks)

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.gemini_model,
        contents=[
            types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf"),
            user_text,
        ],
        config=types.GenerateContentConfig(system_instruction=phase_prompt),
    )
    return response.text or "", _tokens_in(response), _tokens_out(response)


def _uri(file_name: str) -> str:
    if file_name.startswith("https://") or file_name.startswith("files/"):
        return file_name
    return f"files/{file_name}"


def _tokens_in(response) -> Optional[int]:
    try:
        return response.usage_metadata.prompt_token_count
    except AttributeError:
        return None


def _tokens_out(response) -> Optional[int]:
    try:
        return response.usage_metadata.candidates_token_count
    except AttributeError:
        return None
