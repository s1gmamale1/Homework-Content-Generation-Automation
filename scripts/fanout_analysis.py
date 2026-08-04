"""Measure the REAL concurrent-SDK-call fan-out of the 4 api homeworks, from
agent_usages timestamps. Two numbers feed the max-concurrency model:

  * per_homework_peak  — max overlapping api content calls WITHIN one job
                         (how many concurrent SDK calls one homework needs at peak)
  * observed_4job_peak — max overlapping api content calls across all 4 jobs at
                         once (what 4 concurrent homeworks actually demanded here)

max_concurrent_homeworks(on this PC) ≈ call_concurrency_ceiling / per_homework_peak.

Output: stress_results/fanout.json + printed summary.
Usage: python scripts/fanout_analysis.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

import asyncpg  # noqa: E402

DSN = "postgresql://edu:edu@localhost:5432/edu_copy"
RESULTS = Path("stress_results")
# historical repro — model labels intentionally pinned to the actual models
# these already-completed jobs (fixed UUIDs below) were generated with.
JOBS = {
    "7979bd1f-a19b-48d7-9d30-ab71a969e87c": "gemini-2.5-flash",
    "1ac8ad89-f8ab-4892-8064-774a47234af4": "gemini-2.5-pro",
    "e1d8b811-b23b-4814-9d7d-73b7fdfa6654": "gemini-3.1-pro-preview",
    "1a4f4fa2-6a50-47fd-9d76-14f0f4d27803": "gemini-3.1-flash-lite-preview",
}


def peak_overlap(intervals):
    """Max number of simultaneously-open [start,end] intervals (sweep line)."""
    evts = []
    for s, e in intervals:
        if s is None or e is None:
            continue
        evts.append((s, 1))
        evts.append((e, -1))
    evts.sort(key=lambda x: (x[0], x[1]))  # ends (-1) before starts (+1) at a tie
    cur = peak = 0
    for _, d in evts:
        cur += d
        peak = max(peak, cur)
    return peak


async def main():
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            "SELECT homework_job_id, auth_mode, operation, model_name, started_at, completed_at "
            "FROM agent_usages WHERE homework_job_id = ANY($1::uuid[]) AND auth_mode='api' "
            "ORDER BY started_at",
            list(JOBS.keys()),
        )
    finally:
        await conn.close()

    per_job = {}
    all_intervals = []
    for r in rows:
        jid = str(r["homework_job_id"])
        iv = (r["started_at"], r["completed_at"])
        per_job.setdefault(jid, []).append(iv)
        all_intervals.append(iv)

    out = {"jobs": {}, "per_homework_peak": 0, "observed_4job_peak": 0,
           "total_api_calls": len(rows)}
    for jid, model in JOBS.items():
        ivs = per_job.get(jid, [])
        pk = peak_overlap(ivs)
        out["jobs"][model] = {"api_calls": len(ivs), "peak_concurrent_api_calls": pk}
        out["per_homework_peak"] = max(out["per_homework_peak"], pk)
    out["observed_4job_peak"] = peak_overlap(all_intervals)

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "fanout.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
