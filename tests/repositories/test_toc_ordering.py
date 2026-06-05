"""Regression guard: the book.toc_entries relationship must be ordered.

GET /books/{id} (and the SSE replay) serialize the TOC via the
`book.toc_entries` relationship, not the ordered `list_for_book` repo query.
Without an `order_by` on the relationship, SQLAlchemy emits no ORDER BY, so
Postgres returns rows in arbitrary heap order -- which drifts the moment any
row is updated (e.g. notion_homework_page_id at archive time, or a TOC edit),
reshuffling the section list on refresh. Pin it to order_index.
"""

from app.models import Book


def test_toc_entries_relationship_orders_by_order_index():
    order_by = Book.__mapper__.relationships["toc_entries"].order_by
    assert order_by, "toc_entries relationship must declare order_by (else GET reshuffles)"
    assert [col.name for col in order_by] == ["order_index"]
