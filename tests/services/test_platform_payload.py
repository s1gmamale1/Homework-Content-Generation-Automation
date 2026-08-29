import hashlib
import json

import pytest
from app.services import platform_payload as pp


def test_load_subject_map_rejects_non_positive_and_malformed():
    assert pp.load_subject_map('{"history": 7}') == {"history": 7}
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": 0}')
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": -3}')
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": "7"}')
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map('{"history": true}')   # bool is not a valid id
    with pytest.raises(pp.SubjectMapError):
        pp.load_subject_map("not json")


def _job():
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "book_id": "22222222-2222-2222-2222-222222222222",
        "subject": "history",
        "grade": "8",
        "output_language": "ru",
    }


def _phase(**kw):
    base = {
        "phase_name": "practice-rlc",
        "output_md": "# x",
        "content_json": {"a": 1},
        "content_schema_version": "rlc_config@1",
        "authoring_mode": "structured",
        "judge_status": "ok",
        "status": "done",
    }
    base.update(kw)
    return base


def test_build_payload_shape_and_string_uuids():
    out = pp.build_ingest_payload(
        job=_job(), phases=[_phase()], subject_map={"history": 7}
    )
    assert out["source"] == "hcg"
    assert out["source_ref"] == "22222222-2222-2222-2222-222222222222"
    assert out["external_key"] == "11111111-1111-1111-1111-111111111111"
    assert isinstance(out["source_ref"], str) and isinstance(out["external_key"], str)
    assert out["language"] == "ru"
    assert out["subject_id"] == 7
    assert out["grade"] == "8"
    assert isinstance(out["phases"], list)          # LIST, not dict
    assert out["phases"][0]["phase_name"] == "practice-rlc"


def test_build_payload_missing_subject_mapping_is_hard_error():
    with pytest.raises(pp.SubjectMapError):
        pp.build_ingest_payload(job=_job(), phases=[_phase()], subject_map={"biology": 3})


def test_build_payload_excludes_extract_and_non_done_and_empty():
    phases = [
        _phase(phase_name="extract"),
        _phase(phase_name="practice-sentence", status="failed"),
        _phase(phase_name="flashcards", output_md=""),
        _phase(),
    ]
    out = pp.build_ingest_payload(job=_job(), phases=phases, subject_map={"history": 7})
    assert [p["phase_name"] for p in out["phases"]] == ["practice-rlc"]


def test_build_notion_envelope_has_versioned_schema_and_canonical_artifact_digest():
    phases = [
        _phase(),
        _phase(phase_name="extract"),
        _phase(phase_name="teacher-pack"),
        _phase(status="failed"),
    ]

    out = pp.build_notion_envelope(job=_job(), phases=phases)

    assert set(out) == {
        "schema", "source", "source_ref", "external_key", "language", "grade",
        "phases", "artifact_digest",
    }
    assert out["schema"] == "hcg-notion-envelope@1"
    assert out["source"] == "hcg"
    assert out["source_ref"] == "22222222-2222-2222-2222-222222222222"
    assert out["external_key"] == "11111111-1111-1111-1111-111111111111"
    assert out["language"] == "ru"
    assert out["grade"] == "8"
    assert [p["phase_name"] for p in out["phases"]] == ["practice-rlc"]

    artifact = {k: v for k, v in out.items() if k != "artifact_digest"}
    canonical = json.dumps(
        artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert out["artifact_digest"] == {
        "algorithm": "sha256",
        "canonicalization": "json-sort-keys-utf8",
        "value": hashlib.sha256(canonical).hexdigest(),
    }


def test_notion_manifest_does_not_change_direct_ingest_contract():
    direct = pp.build_ingest_payload(
        job=_job(), phases=[_phase()], subject_map={"history": 7}
    )

    assert set(direct) == {
        "source", "source_ref", "external_key", "language", "subject_id", "grade", "phases"
    }
    assert "schema" not in direct
    assert "artifact_digest" not in direct
