"""Post done homework jobs to the platform's homework-import endpoint.

Server side, LIBRARY_INGEST_TOKEN is a comma-separated ACCEPTANCE LIST, but each
request must present exactly ONE token: the server compares the whole Bearer
value against each entry. So this client reads a singular PLATFORM_INGEST_TOKEN.

--dry-run is the DEFAULT; posting requires --post.

--post FAILS CLOSED on structured phases: before any HTTP POST the CLI asks the
target platform which (phase_name, content_schema_version) pairs it can ingest
natively, and refuses the whole job unless every structured phase in the payload
is covered. The capability endpoint does not exist yet, so TODAY every structured
post is blocked — that is the intended behaviour, not a bug. Markdown-only
payloads are unaffected and never trigger the probe.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.services.platform_payload import (  # noqa: E402
    SubjectMapError, build_ingest_payload, load_subject_map, structured_pairs,
)

INGEST_PATH = "/api/v1/library/homework-imports/ingest"
CAPABILITIES_PATH = "/api/v1/library/homework-imports/capabilities"

_JOB_SQL = """
SELECT j.id::text AS id, j.book_id::text AS book_id, j.subject,
       b.grade, j.output_language
FROM homework_jobs j JOIN books b ON b.id = j.book_id
WHERE j.id::text = :jid AND j.status = 'done'
"""

_PHASE_SQL = """
SELECT phase_name, output_md, content_json, content_schema_version,
       authoring_mode, judge_status, status
FROM phase_outputs WHERE job_id::text = :jid ORDER BY phase_order
"""


class TokenError(RuntimeError):
    """The client token is unusable before any HTTP request is attempted."""


class UnsupportedStructuredPhase(RuntimeError):
    """The target platform cannot ingest a structured phase in this payload.

    Raised BEFORE any POST. Not a warning and there is deliberately no bypass —
    see ``_assert_structured_supported``.
    """


def _fetch_capabilities(base: str, token: str, client=None) -> "dict | None":
    """Return the platform's advertised native-support map, or None.

    None means "we could not establish support" for ANY reason — endpoint
    absent (404), unreachable, non-200, or a body that is not JSON. Every one
    of those is treated identically by the caller: block.
    """
    http = client or httpx.Client(timeout=30)
    try:
        resp = http.get(
            f"{base}{CAPABILITIES_PATH}",
            headers={"Authorization": f"Bearer {token}"},
        )
    except Exception as exc:  # noqa: BLE001 — any transport failure is "unknown"
        print(f"  -> capability probe failed: {type(exc).__name__}: {exc}")
        return None
    if resp.status_code != 200:
        print(f"  -> capability probe: HTTP {resp.status_code} (no native-support info)")
        return None
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  -> capability probe: malformed body ({exc})")
        return None
    if not isinstance(data, dict):
        print("  -> capability probe: body is not a JSON object")
        return None
    return data


def supported_pairs(caps: "dict | None") -> set[tuple[str, str]]:
    """Normalize the capability body into a set of supported pairs.

    Expected shape::

        {"structured_phases": [
            {"phase_name": "practice-rlc", "content_schema_version": "rlc_config@1"},
            ...
        ]}

    Anything that does not parse into that shape contributes NOTHING — a
    malformed or half-understood body must never be read as support.
    """
    out: set[tuple[str, str]] = set()
    if not isinstance(caps, dict):
        return out
    entries = caps.get("structured_phases")
    if not isinstance(entries, list):
        return out
    for e in entries:
        if not isinstance(e, dict):
            continue
        phase = e.get("phase_name")
        version = e.get("content_schema_version")
        if isinstance(phase, str) and isinstance(version, str) and phase and version:
            out.add((phase, version))
    return out


def _assert_structured_supported(base: str, token: str, payload: dict, client=None) -> None:
    """Fail closed: refuse to POST structured phases the platform cannot ingest.

    The ingest endpoint SCHEDULES transformation immediately — it is not passive
    raw staging — and the platform's current markdown parsers DOWNGRADE our RLC
    and DROP our sentence-fill. Posting today would therefore silently lose a
    phase and could carry an incomplete packet into review or publication.

    There is no ``--force``. An operator flag here is exactly the mechanism that
    turns "we know this drops a phase" into "we shipped a packet missing a
    phase"; the correct unblock is the platform gaining native support (and
    advertising it), not a client-side override.
    """
    pairs = structured_pairs(payload)
    if not pairs:
        return  # legacy markdown-only payload — nothing to gate, no probe
    supported = supported_pairs(_fetch_capabilities(base, token, client=client))
    missing = [p for p in pairs if p not in supported]
    if missing:
        listing = "\n".join(f"    - {ph} ({ver})" for ph, ver in missing)
        raise UnsupportedStructuredPhase(
            "refusing to POST: the target platform does not advertise native "
            "ingestion for these structured phases:\n"
            f"{listing}\n"
            "  The ingest endpoint schedules transformation immediately, and the "
            "current markdown parsers downgrade practice-rlc and DROP "
            "practice-sentence — posting would silently lose a phase.\n"
            f"  Unblock: the platform must serve {CAPABILITIES_PATH} listing each "
            "(phase_name, content_schema_version). There is no --force."
        )


def validate_token(raw: str) -> str:
    token = (raw or "").strip()
    if not token:
        raise TokenError("PLATFORM_INGEST_TOKEN is empty")
    if "," in token:
        raise TokenError(
            "PLATFORM_INGEST_TOKEN must be ONE token — the server compares the whole "
            "Bearer value, so a comma-joined list authenticates as neither entry"
        )
    if any(ch.isspace() for ch in token):
        raise TokenError(
            "PLATFORM_INGEST_TOKEN must not contain whitespace — the server splits the "
            "Authorization header and requires exactly two parts"
        )
    return token


def _load_map() -> dict[str, int]:
    path = os.environ.get("PLATFORM_SUBJECT_MAP", "")
    if not path:
        raise SubjectMapError("PLATFORM_SUBJECT_MAP is not set")
    return load_subject_map(Path(path).read_text(encoding="utf-8"))


async def _load_job(jid: str):
    async with SessionLocal() as s:
        job = (await s.execute(text(_JOB_SQL), {"jid": jid})).mappings().first()
        if job is None:
            raise RuntimeError(f"job {jid} not found or not done")
        phases = (await s.execute(text(_PHASE_SQL), {"jid": jid})).mappings().all()
    return dict(job), [dict(p) for p in phases]


def _post(base: str, token: str, payload: dict, client=None) -> int:
    http = client or httpx.Client(timeout=60)
    resp = http.post(
        f"{base}{INGEST_PATH}",
        json=payload,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    print(f"  -> HTTP {resp.status_code} {resp.text[:300]}")
    return 0 if resp.status_code < 300 else 1


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", action="append", default=[], help="job id (repeatable)")
    ap.add_argument("--post", action="store_true", help="actually POST (default is dry-run)")
    ap.add_argument("--check-map", action="store_true", help="print+validate the subject map, exit")
    args = ap.parse_args(argv)

    subject_map = _load_map()
    if args.check_map:
        print(json.dumps(subject_map, indent=2, sort_keys=True))
        return 0

    base = os.environ.get("PLATFORM_BASE_URL", "").rstrip("/")
    if not base:
        raise TokenError("PLATFORM_BASE_URL is not set")
    token = validate_token(os.environ.get("PLATFORM_INGEST_TOKEN", ""))

    rc = 0
    for jid in args.job:
        job, phases = asyncio.run(_load_job(jid))
        payload = build_ingest_payload(job=job, phases=phases, subject_map=subject_map)
        if not args.post:
            n = len(payload["payload"]["phases"])
            print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
            print(f"[dry-run] {jid}: {n} phases — not posted")
            continue
        try:
            _assert_structured_supported(base, token, payload)
        except UnsupportedStructuredPhase as exc:
            print(f"[blocked] {jid}: {exc}", file=sys.stderr)
            rc |= 1
            continue
        rc |= _post(base, token, payload)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
