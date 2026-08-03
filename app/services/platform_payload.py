"""Pure builder for the platform's homework-import envelope.

No I/O: the subject map is injected as a dict so the builder stays unit-testable.
The platform iterates ``payload["phases"]`` as a LIST of objects keyed
``phase_name`` — never a dict.
"""
from __future__ import annotations

import json
from typing import Any


class SubjectMapError(RuntimeError):
    """Malformed subject map, or a subject with no platform id."""


def load_subject_map(raw: str) -> dict[str, int]:
    """Parse the PLATFORM_SUBJECT_MAP JSON: canonical HCGA subject -> platform id."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SubjectMapError(f"subject map is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SubjectMapError("subject map must be a JSON object")
    out: dict[str, int] = {}
    for key, value in data.items():
        # bool is a subclass of int — reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SubjectMapError(f"subject '{key}': platform id must be a positive int")
        out[str(key)] = value
    return out


_ENVELOPE_KEYS = (
    "phase_name", "output_md", "content_json",
    "content_schema_version", "authoring_mode", "judge_status",
)


def build_ingest_payload(
    *, job: dict, phases: list[dict], subject_map: dict[str, int]
) -> dict[str, Any]:
    """Build the complete ingest envelope for one done job."""
    subject = job["subject"]
    if subject not in subject_map:
        raise SubjectMapError(
            f"no platform subject_id mapped for HCGA subject '{subject}'"
        )
    rows = [
        {k: p.get(k) for k in _ENVELOPE_KEYS}
        for p in phases
        if p.get("phase_name") != "extract"
        and p.get("status") == "done"
        and (p.get("output_md") or "").strip()
    ]
    return {
        "source": "hcg",
        "source_ref": str(job["book_id"]),
        "external_key": str(job["id"]),
        "language": job["output_language"],
        "subject_id": subject_map[subject],
        "grade": str(job["grade"]),
        "phases": rows,
    }
