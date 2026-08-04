"""Pure builder for the platform's homework-import envelope.

No I/O: the subject map is injected as a dict so the builder stays unit-testable.

Envelope shape is dictated by the platform's ``HomeworkImportIngestSerializer``
(``apps/library/serializers/homework_imports.py`` on ``origin/Akademiya-AI``):

    source        ChoiceField          "hcg"
    source_ref    CharField(max=255)   book id, STRING
    language      ChoiceField
    subject_id    IntegerField(min=1)
    grade         IntegerField(1..11)  INT — a string is a 400
    external_key  CharField(max=255)   job id, STRING
    pack_name     CharField(optional)
    payload       JSONField            non-empty dict

The phase rows live UNDER ``payload`` — the view reads ``payload.get("phases")``
and iterates it as a LIST of objects keyed ``phase_name``, never a dict. Emitting
``phases`` at the request top level (as this builder used to) drops it on the
floor: DRF ignores unknown top-level keys and ``payload`` is required, so the
request 400s before anything is stored.
"""
from __future__ import annotations

import json
from typing import Any

GRADE_MIN = 1
GRADE_MAX = 11


class SubjectMapError(RuntimeError):
    """Malformed subject map, or a subject with no platform id."""


class PayloadError(RuntimeError):
    """The job row cannot produce an envelope the platform serializer accepts."""


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


def coerce_grade(raw: Any) -> int:
    """Return the grade as an int in 1..11, or raise ``PayloadError``.

    The DB column is an int but the row can arrive through a text cast, so a
    digit string is accepted and normalized. ``bool`` is a subclass of ``int``
    and is rejected explicitly; floats are rejected because a silent truncation
    would ship the wrong grade.
    """
    if isinstance(raw, bool):
        raise PayloadError(f"grade must be an int {GRADE_MIN}..{GRADE_MAX}, got {raw!r}")
    if isinstance(raw, int):
        grade = raw
    elif isinstance(raw, str) and raw.strip().lstrip("+").isdigit():
        grade = int(raw.strip())
    else:
        raise PayloadError(f"grade must be an int {GRADE_MIN}..{GRADE_MAX}, got {raw!r}")
    if not (GRADE_MIN <= grade <= GRADE_MAX):
        raise PayloadError(
            f"grade must be an int {GRADE_MIN}..{GRADE_MAX}, got {raw!r}"
        )
    return grade


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
        "grade": coerce_grade(job["grade"]),
        # JSONField, required, must be a non-empty dict. `phases` lives HERE.
        "payload": {"phases": rows},
    }


def structured_pairs(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Every distinct ``(phase_name, content_schema_version)`` authored structurally.

    Ordered by first appearance so operator messages are stable.
    """
    out: list[tuple[str, str]] = []
    for row in (payload.get("payload") or {}).get("phases") or []:
        if row.get("authoring_mode") != "structured":
            continue
        pair = (str(row.get("phase_name")), str(row.get("content_schema_version")))
        if pair not in out:
            out.append(pair)
    return out
