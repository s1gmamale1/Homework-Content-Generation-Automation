import json, pathlib
from app.services import golden_eval

_MANIFEST = pathlib.Path(__file__).resolve().parents[2] / "tests" / "golden" / "manifest.json"
_DIMS = {"boundary", "answer_key", "broken_question", "language", "reflection", "extract_fidelity"}


def test_manifest_has_five_audit_entries():
    entries = golden_eval.load_golden_set()
    assert len(entries) == 5
    assert {e.job_id[:8] for e in entries} == {
        "3ca0da6f", "8f734563", "263d99c5", "9504ad94", "1122356a"}


def test_every_entry_scores_all_six_dimensions():
    for e in golden_eval.load_golden_set():
        assert set(e.audit_verdict) == _DIMS
        assert all(v in ("flag", "pass") for v in e.audit_verdict.values())


def test_source_fixture_exists_and_names_the_lesson():
    # each committed source fixture must be non-trivial real text
    root = pathlib.Path(__file__).resolve().parents[2] / "tests" / "golden" / "sources"
    for e in golden_eval.load_golden_set():
        f = root / f"{e.job_id[:8]}.txt"
        assert f.is_file() and len(f.read_text(encoding="utf-8")) > 500
