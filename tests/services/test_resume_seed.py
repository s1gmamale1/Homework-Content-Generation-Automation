# tests/services/test_resume_seed.py
from types import SimpleNamespace

from app.services.pipeline import _done_phase_md, _pending_phases


def _row(name, status, md):
    return SimpleNamespace(phase_name=name, status=status, output_md=md)


def test_done_phase_md_filters_done_with_output():
    rows = [
        _row("extract", "done", "summary"),
        _row("case-based-preview", "done", "# C"),
        _row("flashcards", "failed", None),
        _row("boss-arena", "done", "   "),   # whitespace-only → not resumable
    ]
    assert _done_phase_md(rows) == {"extract": "summary", "case-based-preview": "# C"}


def test_pending_excludes_already_present():
    content = ["case-based-preview", "flashcards", "boss-arena"]
    prior = {"case-based-preview": "# C"}     # done content phase pre-injected
    assert _pending_phases(content, prior) == {"flashcards", "boss-arena"}
