"""On-disk storage paths for book artifacts.

Single source of truth for where a book's PDF lives, so the write path
(`app.api.v1.books.upload_book`) and every read path (TOC extraction, the
pipeline's per-phase re-attach) agree. Honors `settings.var_dir` — the base
directory knob — so pointing `VAR_DIR` at a shared/network volume mounted on
every worker is the simplest multi-PC PDF-distribution option (ROADMAP R13).
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from app.config import settings


def book_dir(book_id: UUID | str) -> Path:
    """Directory holding one book's on-disk artifacts: ``<var_dir>/books/<id>``."""
    return Path(settings.var_dir) / "books" / str(book_id)


def book_pdf_path(book_id: UUID | str) -> Path:
    """Deterministic path to a book's source PDF: ``<var_dir>/books/<id>/source.pdf``."""
    return book_dir(book_id) / "source.pdf"


def sa_key_dir() -> Path:
    """Directory holding uploaded SA-key JSONs: ``<var_dir>/sa_keys``."""
    return Path(settings.var_dir) / "sa_keys"


def sa_key_path(key_id: UUID | str) -> Path:
    """On-disk path to one uploaded SA key: ``<var_dir>/sa_keys/<id>.json``."""
    return sa_key_dir() / f"{key_id}.json"


def sa_key_active_path() -> Path:
    """The single key a worker has currently applied: ``<var_dir>/sa_keys/active.json``.
    GOOGLE_APPLICATION_CREDENTIALS points at the resolved absolute form of this."""
    return sa_key_dir() / "active.json"
