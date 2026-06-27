"""Acceptance smoke: TOC extraction over transport='api' (Vertex), no gemini CLI.
Run with Vertex creds in env (GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT):
    uv run python -m scripts.smoke_toc_api <book_id>
Re-extracts the on-disk PDF for <book_id> straight through agent.extract_toc.
Pass = a non-empty ExtractedTOC came back via the api path (auth_mode=api) with
no initOauthClient. One real call (a single TOC read) — within the no-mass-gen rule.

Reads extract_provider, extract_model, and toc_transport from the launch_defaults
DB row (operator-editable at /settings). Set toc_transport=api there before running.
"""
import asyncio
import sys
from uuid import UUID

from app.db import SessionLocal
from app.repositories import launch_defaults as launch_defaults_repo
from app.services import agent
from app.services.storage import book_pdf_path


async def _main(book_id: str):
    async with SessionLocal() as session:
        ld = await launch_defaults_repo.get(session)

    assert ld.toc_transport == "api", (
        "set toc_transport=api in launch defaults (/settings) for this smoke")
    pdf = book_pdf_path(UUID(book_id))
    assert pdf.exists(), f"no PDF on disk at {pdf}"
    toc = await agent.extract_toc(
        provider=ld.extract_provider,
        model=ld.extract_model,
        pdf_path=pdf,
        subject="smoke",
        book_id=UUID(book_id),
        transport="api",
    )
    assert toc.entries, "api TOC extraction returned 0 entries"
    print(f"SMOKE PASS: {len(toc.entries)} entries via transport=api (Vertex). "
          f"First: {toc.entries[0].section_title!r}")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1]))
