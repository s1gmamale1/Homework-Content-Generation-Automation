"""Post done homework jobs to the platform's homework-import endpoint.

Server side, LIBRARY_INGEST_TOKEN is a comma-separated ACCEPTANCE LIST, but each
request must present exactly ONE token: the server compares the whole Bearer
value against each entry. So this client reads a singular PLATFORM_INGEST_TOKEN.

--dry-run is the DEFAULT; posting requires --post.
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
    SubjectMapError, build_ingest_payload, load_subject_map,
)

INGEST_PATH = "/api/v1/library/homework-imports/ingest"

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
        result = _load_job(jid)
        # ``_load_job`` is async in production; tests may monkeypatch it with
        # a plain sync stub that already returns ``(job, phases)`` — accept
        # both shapes rather than forcing every test through the event loop.
        job, phases = asyncio.run(result) if asyncio.iscoroutine(result) else result
        payload = build_ingest_payload(job=job, phases=phases, subject_map=subject_map)
        if not args.post:
            print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
            print(f"[dry-run] {jid}: {len(payload['phases'])} phases — not posted")
            continue
        rc |= _post(base, token, payload)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
