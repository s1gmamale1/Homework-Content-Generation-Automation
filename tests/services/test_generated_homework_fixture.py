import hashlib
import json
from pathlib import Path


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "generated_homework_current.json"


def test_generated_homework_fixture_declares_valid_canonical_digest():
    envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert envelope["schema"] == "hcg-notion-envelope@1"
    assert envelope["source"] == "hcg"
    assert envelope["phases"]
    artifact = {k: v for k, v in envelope.items() if k != "artifact_digest"}
    canonical = json.dumps(
        artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert envelope["artifact_digest"] == {
        "algorithm": "sha256",
        "canonicalization": "json-sort-keys-utf8",
        "value": hashlib.sha256(canonical).hexdigest(),
    }
