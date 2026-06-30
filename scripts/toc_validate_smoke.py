"""Acceptance smoke for the post-TOC vision validator (agent.validate_toc).

Fact-over-theory proof that the validator DISCRIMINATES: against a real book
PDF's printed contents page, the genuine extracted TOC must read ``verified``
while a deliberately-scrambled TOC must read ``mismatch``.

Makes 2 real Gemini-2.5-flash vision calls (one per case) over whatever
transport the env credentials support (api: GEMINI_API_KEY or the Vertex SA
pair). One-time, cheap — NOT homework generation.

Run:  uv run python -m scripts.toc_validate_smoke [BOOK_ID]
If BOOK_ID is omitted, auto-picks a toc_ready book that has >=20 entries AND a
source.pdf on disk.

Exit 0 iff verified-case == "verified" AND scrambled-case == "mismatch".
"""
from __future__ import annotations

import asyncio
import sys
from uuid import UUID

from sqlalchemy import text

from app.db import SessionLocal
from app.schemas import TOCEntryExtracted
from app.services import agent, storage


async def _pick_book(session, explicit: str | None) -> tuple[UUID, str]:
    """Return (book_id, subject) for a usable book, or raise."""
    if explicit:
        row = (await session.execute(
            text("select id, subject from books where id = :id"),
            {"id": explicit},
        )).first()
        if row is None:
            raise SystemExit(f"book {explicit} not found")
        return row.id, row.subject
    rows = (await session.execute(text("""
        select b.id, b.subject, count(t.id) n
        from books b join toc_entries t on t.book_id = b.id
        where b.status = 'toc_ready'
        group by b.id having count(t.id) >= 20
        order by n desc
    """))).all()
    for r in rows:
        if storage.book_pdf_path(r.id).exists():
            return r.id, r.subject
    raise SystemExit("no toc_ready book with >=20 entries AND a source.pdf on disk")


async def _load_entries(session, book_id: UUID) -> list[TOCEntryExtracted]:
    rows = (await session.execute(text("""
        select chapter_number, chapter_title, section_number, section_title,
               page_start, page_end
        from toc_entries where book_id = :id order by order_index
    """), {"id": str(book_id)})).all()
    return [
        TOCEntryExtracted(
            chapter_number=r.chapter_number,
            chapter_title=r.chapter_title,
            section_number=r.section_number,
            section_title=r.section_title,
            page_start=r.page_start,
            page_end=r.page_end,
        )
        for r in rows
    ]


def _scramble(entries: list[TOCEntryExtracted]) -> list[TOCEntryExtracted]:
    """Replace every title with clearly-unrelated garbage so the list cannot
    possibly match the printed contents page → must read ``mismatch``."""
    junk = [
        "Medieval Cooking Techniques", "Quantum Basketball Strategy",
        "The History of Jazz Saxophone", "Tropical Aquarium Maintenance",
        "Advanced Origami for Beginners", "Volcanic Wine Pairing",
        "Roller Coaster Thermodynamics", "Renaissance Hat Fashion",
    ]
    out: list[TOCEntryExtracted] = []
    for i, e in enumerate(entries):
        out.append(TOCEntryExtracted(
            chapter_number=e.chapter_number,
            chapter_title=junk[i % len(junk)],
            section_number=e.section_number,
            section_title=junk[(i + 3) % len(junk)],
            page_start=e.page_start,
            page_end=e.page_end,
        ))
    return out


async def main() -> int:
    explicit = sys.argv[1] if len(sys.argv) > 1 else None
    async with SessionLocal() as session:
        book_id, subject = await _pick_book(session, explicit)
        entries = await _load_entries(session, book_id)
    pdf_path = storage.book_pdf_path(book_id)
    print(f"book={book_id} subject={subject} entries={len(entries)} pdf={pdf_path.name}")

    common = dict(
        pdf_path=pdf_path, subject=subject, book_id=book_id,
        provider="gemini", model="gemini-2.5-flash", transport="api",
    )

    print("\n[case A] genuine TOC → expect verified")
    a = await agent.validate_toc(entries=entries, **common)
    print(f"  verdict={a.status} confidence={a.confidence} issues={a.issues[:3]}")

    print("\n[case B] scrambled TOC → expect mismatch")
    b = await agent.validate_toc(entries=_scramble(entries), **common)
    print(f"  verdict={b.status} confidence={b.confidence} issues={b.issues[:3]}")

    ok = a.status == "verified" and b.status == "mismatch"
    print(f"\nRESULT: {'PASS ✅' if ok else 'FAIL ❌'} "
          f"(genuine={a.status}, scrambled={b.status})")
    if a.status == "skipped" or b.status == "skipped":
        print("  NOTE: a 'skipped' verdict means the validator could not run "
              "(no window / spawn / parse) — not a discrimination result.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
