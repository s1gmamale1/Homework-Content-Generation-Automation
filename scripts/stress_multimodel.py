"""Per-model concurrency stress for transport=api gemini (SDK/Vertex SA), to find
the MAX concurrent-call ceiling of EACH model the user runs — then derive the
max concurrent *jobs* on this one PC. Money-safe: thinking disabled + output
capped so every probe is ~1-2 tokens; early-stop the instant a model rate-limits.

Modes:
  calibrate         one cheap call per model; prints tokens + whether thinking-off works
  ramp [maxN]       concurrency ramp per model (default), writes stress_results/multimodel.json

Job-level translation (measured from the 4 real homeworks):
  ~10 gemini calls/job, peak 3 concurrent/job, ~2 calls/min/job.
  max_jobs = min( concurrency_ceiling / 3 , sustained_calls_per_min / 2 ).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from statistics import median
from pathlib import Path

sys.path.insert(0, ".")
import app.config  # noqa: F401,E402  (loads .env into os.environ)
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview",
]
PROMPT = "Reply with exactly one word: OK"
LEVELS = [2, 4, 8, 16, 24, 32, 48, 64]
RESULTS = Path("stress_results")


def _client():
    proj = os.environ["GOOGLE_CLOUD_PROJECT"]
    loc = os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
    return genai.Client(vertexai=True, project=proj, location=loc)


def _cfg(think_off: bool):
    if think_off:
        return types.GenerateContentConfig(
            max_output_tokens=16,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
    return types.GenerateContentConfig(max_output_tokens=16)


def _classify(err: str) -> str:
    e = (err or "").lower()
    if "429" in e or "resource_exhausted" in e or ("rate" in e and "limit" in e) or "quota" in e:
        return "RATE_LIMIT"
    if "503" in e or "unavailable" in e or "overloaded" in e:
        return "UNAVAILABLE"
    if "500" in e or "internal" in e:
        return "INTERNAL"
    if "deadline" in e or "timeout" in e:
        return "TIMEOUT"
    if "403" in e or "401" in e or "permission" in e:
        return "AUTH"
    return "OTHER" if err else "OK"


async def _one(client, model, cfg):
    t0 = time.monotonic()
    try:
        r = await client.aio.models.generate_content(model=model, contents=PROMPT, config=cfg)
        um = getattr(r, "usage_metadata", None)
        out = (getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)
        return {"ok": True, "secs": time.monotonic() - t0, "out": out, "err_class": "OK", "err": ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "secs": time.monotonic() - t0, "out": 0,
                "err_class": _classify(str(exc)), "err": str(exc)[:200]}


async def calibrate():
    client = _client()
    print(f"{'model':<32}{'think_off ok':<13}{'out_tok':<9}{'secs':<7}status")
    cfgmap = {}
    for m in MODELS:
        r = await _one(client, m, _cfg(True))
        if not r["ok"] and ("thinking" in r["err"].lower() or "invalid" in r["err"].lower() or "budget" in r["err"].lower()):
            r2 = await _one(client, m, _cfg(False))
            cfgmap[m] = False
            print(f"{m:<32}{'NO(fallback)':<13}{str(r2['out']):<9}{r2['secs']:<7.2f}{'OK' if r2['ok'] else r2['err'][:60]}")
        else:
            cfgmap[m] = True
            print(f"{m:<32}{'yes':<13}{str(r['out']):<9}{r['secs']:<7.2f}{'OK' if r['ok'] else r['err'][:60]}")
    (RESULTS).mkdir(exist_ok=True)
    (RESULTS / "cfgmap.json").write_text(json.dumps(cfgmap), encoding="utf-8")
    return cfgmap


async def run_level(client, model, cfg, n):
    t0 = time.monotonic()
    calls = await asyncio.gather(*(_one(client, model, cfg) for _ in range(n)))
    wall = time.monotonic() - t0
    oks = [c for c in calls if c["ok"]]
    lat = sorted(c["secs"] for c in calls)
    return {
        "N": n, "ok": len(oks), "total": n,
        "success_rate": round(len(oks) / n, 3),
        "p50_s": round(median(lat), 2), "p95_s": round(lat[int(n * 0.95) - 1], 2),
        "rps": round(n / wall, 1), "rpm": round(n / wall * 60),
        "out_tok_sum": sum(c["out"] for c in calls),
        "errors": dict(Counter(c["err_class"] for c in calls if c["err_class"] != "OK")),
    }


async def ramp(max_n):
    cfgmap = json.loads((RESULTS / "cfgmap.json").read_text()) if (RESULTS / "cfgmap.json").exists() else await calibrate()
    client = _client()
    levels = [n for n in LEVELS if n <= max_n]
    out = {}
    total_out_tok = 0
    for m in MODELS:
        cfg = _cfg(cfgmap.get(m, True))
        print(f"\n=== {m} (think_off={cfgmap.get(m, True)}) ===")
        print(f"{'N':>4}{'succ':>7}{'p50':>6}{'p95':>6}{'rps':>6}{'rpm':>7}  errors")
        rows = []
        for n in levels:
            r = await run_level(client, m, cfg, n)
            total_out_tok += r["out_tok_sum"]
            rows.append(r)
            print(f"{r['N']:>4}{r['success_rate']:>7}{r['p50_s']:>6}{r['p95_s']:>6}{r['rps']:>6}{r['rpm']:>7}  {r['errors']}")
            if r["success_rate"] < 0.8:
                print(f"  >> ceiling at N={n} (success {r['success_rate']})")
                break
        out[m] = rows
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "multimodel.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nTotal probe output tokens across all models: {total_out_tok}")
    print(f"Wrote {RESULTS/'multimodel.json'}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "ramp"
    if mode == "calibrate":
        asyncio.run(calibrate())
    else:
        asyncio.run(ramp(int(sys.argv[2]) if len(sys.argv) > 2 else max(LEVELS)))
