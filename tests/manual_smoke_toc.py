"""Manual smoke test: prove ``agent.extract_toc`` actually receives the PDF
attachment for the Gemini provider (the failure mode reported in the bug).

Pre-fix, gemini was getting an attachment-less prompt and replying with
``"I could not find the matched information"`` — which then exploded on
``ExtractedTOC.model_validate_json``.

Post-fix, this should produce ≥1 TOC entry. Uses the existing failed book's
PDF on disk; doesn't touch the DB beyond a usage-write swallow.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import agent as agent_module
from app.services.agent import extract_toc


BOOK_ID = UUID("d387c61c-9e27-491b-ac34-cb277a66579d")
# Real DB book PDF is 26 MB which exceeds gemini-cli's 20 MB read_file
# limit. For smoke testing, allow overriding with a smaller fixture.
PDF_PATH = Path(
    sys.argv[1] if len(sys.argv) > 1
    else f"var/books/{BOOK_ID}/source.pdf"
)


async def _patch_record(**_kwargs: object) -> None:
    return None


async def main() -> int:
    if not PDF_PATH.exists():
        print(f"PDF missing at {PDF_PATH.resolve()}")
        return 1

    agent_module._record_usage = _patch_record  # type: ignore[assignment]

    print(f"PDF: {PDF_PATH.resolve()} "
          f"({PDF_PATH.stat().st_size / 1024 / 1024:.1f} MB)")
    print("Running gemini TOC extract...")
    try:
        toc = await extract_toc(
            provider="gemini",
            model=None,
            pdf_path=PDF_PATH,
            subject="math-algebra",
            book_id=BOOK_ID,
        )
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print(f"OK — extracted {len(toc.entries)} entries")
    for e in toc.entries[:3]:
        print(
            f"  ch={e.chapter_number!r:>6} "
            f"sec={e.section_number!r:>6} "
            f"title={e.section_title!r:>40} "
            f"pages={e.page_start}-{e.page_end}"
        )
    if len(toc.entries) > 3:
        print(f"  ... ({len(toc.entries) - 3} more)")
    return 0 if toc.entries else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
