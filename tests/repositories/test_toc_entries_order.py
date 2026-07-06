"""bulk_create must assign order_index in READING order (page_start), not the
extractor model's emission order.

The extractor LLM emits mundarija entries in whatever order it read them —
two-column contents pages come back interleaved. Hit live 2026-07-06: G10
algebra (2-BOB rows before 1-BOB) and G5 matematika part 2, where the scramble
also corrupted seam page_ends. order_index feeds get_next_in_book (the
curriculum-boundary note) and the FE listing, so it must follow the book.
"""

from uuid import uuid4

import pytest

from app.repositories.toc_entries import bulk_create
from app.schemas.toc import TOCEntryExtracted


class _FakeSession:
    def add(self, row):
        pass

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_bulk_create_assigns_order_index_by_page_start():
    entries = [
        TOCEntryExtracted(section_title="2-BOB intro", page_start=50, page_end=60),
        TOCEntryExtracted(section_title="1-BOB intro", page_start=3, page_end=10),
        TOCEntryExtracted(section_title="1-BOB lesson", page_start=11, page_end=20),
    ]
    rows = await bulk_create(_FakeSession(), uuid4(), entries)
    by_index = sorted(rows, key=lambda r: r.order_index)
    assert [r.section_title for r in by_index] == [
        "1-BOB intro",
        "1-BOB lesson",
        "2-BOB intro",
    ]
    assert [r.order_index for r in by_index] == [0, 1, 2]


@pytest.mark.asyncio
async def test_bulk_create_ties_and_missing_pages_keep_emission_order():
    entries = [
        TOCEntryExtracted(section_title="no-page A", page_start=None),
        TOCEntryExtracted(section_title="p5 first", page_start=5, page_end=5),
        TOCEntryExtracted(section_title="p5 second", page_start=5, page_end=6),
        TOCEntryExtracted(section_title="no-page B", page_start=None),
        TOCEntryExtracted(section_title="p3", page_start=3, page_end=4),
    ]
    rows = await bulk_create(_FakeSession(), uuid4(), entries)
    by_index = sorted(rows, key=lambda r: r.order_index)
    # Paged entries in page order (stable on ties); page-less entries keep
    # their relative emission order after all paged ones.
    assert [r.section_title for r in by_index] == [
        "p3",
        "p5 first",
        "p5 second",
        "no-page A",
        "no-page B",
    ]
