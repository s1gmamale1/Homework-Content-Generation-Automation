# tests/api/test_phase_zip.py
import io
import zipfile
from types import SimpleNamespace

from app.api.v1.jobs import _phase_zip


def test_phase_zip_one_md_per_done_phase():
    phases = [
        SimpleNamespace(phase_order=2, phase_name="flashcards", status="done", output_md="# F"),
        SimpleNamespace(phase_order=1, phase_name="case-based-preview", status="done", output_md="# C"),
        SimpleNamespace(phase_order=3, phase_name="boss-arena", status="failed", output_md=None),
        SimpleNamespace(phase_order=0, phase_name="extract", status="done", output_md="summary"),
    ]
    data = _phase_zip(phases)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(zf.namelist())
    # extract excluded (internal); failed/empty excluded; ordered, zero-padded names
    assert names == ["01-case-based-preview.md", "02-flashcards.md"]
