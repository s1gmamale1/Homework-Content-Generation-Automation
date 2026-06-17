"""Max-concurrency stress probe for transport=api gemini (SDK / Vertex SA),
on ONE PC — WITHOUT spending real money on full homeworks (hard user rule:
never mass-generate homeworks). It fires CHEAP minimal-token concurrent SDK
calls (tiny prompt, ~1-token reply, cheapest model gemini-2.5-flash-lite) and
ramps concurrency to find where the API/box stops scaling: 429/RESOURCE_EXHAUSTED
onset, latency-vs-N, throughput plateau, CPU%/RSS.

The homework-level ceiling is then MODELLED elsewhere (report) as
  max_concurrent_homeworks ≈ call_concurrency_ceiling / peak_per_homework_fanout
using the real 4-job fan-out — never by launching extra homeworks.

Output: stress_results/levels.csv (per-level summary) + raw.json (per-call).
Usage: python scripts/stress_concurrency.py [maxN]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import median

sys.path.insert(0, ".")

from app.services import api_transport  # noqa: E402

try:
    import psutil  # noqa: E402
    _PROC = psutil.Process(os.getpid())
    _HAVE_PS = True
except Exception:
    _HAVE_PS = False

MODEL = "gemini-2.5-flash-lite"      # cheapest priced model ($0.10/$0.40 per Mtok)
PROMPT = "Reply with exactly one word: OK"
LEVELS = [1, 2, 4, 8, 16, 24, 32, 48, 64]
ROUNDS = 2                            # rounds per level (stability vs cost)
RESULTS = Path("stress_results")


def _classify(err: str) -> str:
    e = (err or "").lower()
    if "429" in e or "resource_exhausted" in e or "rate" in e and "limit" in e:
        return "RATE_LIMIT"
    if "503" in e or "unavailable" in e:
        return "UNAVAILABLE"
    if "500" in e or "internal" in e:
        return "INTERNAL"
    if "deadline" in e or "timeout" in e:
        return "TIMEOUT"
    if "permission" in e or "403" in e or "401" in e:
        return "AUTH"
    return "OTHER" if err else "OK"


async def probe(i: int) -> dict:
    t0 = time.monotonic()
    try:
        rc, text, usage, err = await api_transport.generate(
            provider="gemini", model=MODEL, prompt=PROMPT, attachments=[]
        )
    except Exception as exc:  # defensive — should be caught inside
        rc, text, usage, err = 1, "", {}, str(exc)
    dt = time.monotonic() - t0
    ok = rc == 0 and bool((text or "").strip())
    return {
        "i": i, "ok": ok, "secs": round(dt, 3),
        "err_class": "OK" if ok else _classify(err),
        "err": (err or "")[:160] if not ok else "",
        "out_tokens": usage.get("output_tokens"),
    }


async def _sampler(stop: asyncio.Event, peak: dict):
    """Sample CPU% and RSS while a level runs."""
    if not _HAVE_PS:
        return
    _PROC.cpu_percent(None)  # prime
    while not stop.is_set():
        cpu = psutil.cpu_percent(interval=None)
        rss = _PROC.memory_info().rss / (1024 * 1024)
        peak["cpu"] = max(peak.get("cpu", 0.0), cpu)
        peak["rss"] = max(peak.get("rss", 0.0), rss)
        await asyncio.sleep(0.3)


async def run_level(n: int) -> dict:
    calls, durs = [], []
    for _ in range(ROUNDS):
        peak = {}
        stop = asyncio.Event()
        samp = asyncio.create_task(_sampler(stop, peak))
        t0 = time.monotonic()
        batch = await asyncio.gather(*(probe(i) for i in range(n)))
        wall = time.monotonic() - t0
        stop.set()
        await samp
        calls.extend(batch)
        durs.append(wall)
    oks = [c for c in calls if c["ok"]]
    lat = sorted(c["secs"] for c in calls)
    classes = Counter(c["err_class"] for c in calls)
    wall = max(durs)  # representative batch wall (slowest round)
    return {
        "N": n, "total_calls": len(calls), "ok": len(oks),
        "success_rate": round(len(oks) / len(calls), 3),
        "p50_s": round(median(lat), 3) if lat else None,
        "p95_s": round(lat[int(len(lat) * 0.95) - 1], 3) if lat else None,
        "max_s": round(lat[-1], 3) if lat else None,
        "throughput_rps": round(n / (sum(durs) / len(durs)), 1) if durs else None,
        "peak_cpu_pct": round(peak.get("cpu", 0.0), 1),
        "peak_rss_mb": round(peak.get("rss", 0.0), 1),
        "errors": dict(classes),
        "_raw": calls,
    }


async def main():
    max_n = int(sys.argv[1]) if len(sys.argv) > 1 else max(LEVELS)
    levels = [n for n in LEVELS if n <= max_n]
    RESULTS.mkdir(exist_ok=True)
    print(f"psutil sampling: {_HAVE_PS} | model={MODEL} | rounds/level={ROUNDS}")
    print(f"{'N':>4} {'ok/tot':>9} {'succ':>6} {'p50':>7} {'p95':>7} {'rps':>7} {'cpu%':>6} {'rssMB':>7}  errors")
    summaries, raw = [], []
    for n in levels:
        r = await run_level(n)
        raw.append({"N": n, "calls": r.pop("_raw")})
        summaries.append(r)
        print(f"{r['N']:>4} {str(r['ok'])+'/'+str(r['total_calls']):>9} "
              f"{r['success_rate']:>6} {str(r['p50_s']):>7} {str(r['p95_s']):>7} "
              f"{str(r['throughput_rps']):>7} {r['peak_cpu_pct']:>6} {r['peak_rss_mb']:>7}  "
              f"{ {k:v for k,v in r['errors'].items() if k!='OK'} }")
        # Early stop: sustained failure means we found the ceiling.
        if r["success_rate"] < 0.8:
            print(f"  >> success_rate {r['success_rate']} < 0.8 at N={n} — ceiling found, stopping ramp.")
            break

    # CSV
    cols = ["N", "total_calls", "ok", "success_rate", "p50_s", "p95_s", "max_s",
            "throughput_rps", "peak_cpu_pct", "peak_rss_mb"]
    csv = [",".join(cols)]
    for r in summaries:
        csv.append(",".join(str(r[c]) for c in cols))
    (RESULTS / "levels.csv").write_text("\n".join(csv) + "\n", encoding="utf-8")
    (RESULTS / "raw.json").write_text(json.dumps({"summaries": summaries, "raw": raw}, indent=2), encoding="utf-8")
    print(f"\nWrote {RESULTS/'levels.csv'} and {RESULTS/'raw.json'}")


if __name__ == "__main__":
    asyncio.run(main())
