"""CQ-E golden-eval harness.

Scores generated homework packets against the audit's 6-dimension rubric
(`docs/research/2026-07-01-content-quality-audit-g8-math.md`), diffs against
frozen baselines, and gates prompt/model-change PRs on no-regression.

This module is intentionally standalone / offline: it reads packets via
`phase_repo.list_for_job` (read-only) and the golden-set manifest committed
under `tests/golden/`. It does not touch pipeline/worker/schema code.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPO_ROOT / "tests" / "golden" / "manifest.json"

_DIMENSIONS = (
    "boundary",
    "answer_key",
    "broken_question",
    "language",
    "reflection",
    "extract_fidelity",
)


@dataclass(frozen=True)
class GoldenEntry:
    """One audited golden-set packet (a job whose 11 phases were human-scored)."""

    job_id: str
    book_id: str
    subject: str
    grade: str
    language: str
    source_pages: str
    audit_verdict: dict[str, str]
    source_pdf_pages: str = ""
    reflection_evidence: str = ""


def load_golden_set(manifest_path: pathlib.Path | None = None) -> list[GoldenEntry]:
    """Load the frozen golden set from `tests/golden/manifest.json`."""
    path = manifest_path or _MANIFEST_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries: list[GoldenEntry] = []
    for row in raw:
        entries.append(
            GoldenEntry(
                job_id=row["job_id"],
                book_id=row["book_id"],
                subject=row["subject"],
                grade=row["grade"],
                language=row["language"],
                source_pages=row["source_pages"],
                audit_verdict=dict(row["audit_verdict"]),
                source_pdf_pages=row.get("source_pdf_pages", ""),
                reflection_evidence=row.get("reflection_evidence", ""),
            )
        )
    return entries
