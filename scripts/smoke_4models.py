"""Pre-flight smoke for the 4 gemini models we are about to drive through
transport=api (SDK / Vertex service-account). Confirms each model is reachable,
returns text, and reports usage — BEFORE we commit to 4 long full-homework jobs.

Run from the repo root (or a worktree) with the app importable + .env present:
    python scripts/smoke_4models.py
Cheap by design: one tiny prompt per model.
"""
from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, ".")

from app.services import api_transport  # noqa: E402

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview",
]
PROMPT = "Reply with exactly one word: OK"


async def one(model: str) -> dict:
    t0 = time.monotonic()
    rc, text, usage, err = await api_transport.generate(
        provider="gemini", model=model, prompt=PROMPT, attachments=[]
    )
    dt = time.monotonic() - t0
    return {
        "model": model,
        "rc": rc,
        "ok": rc == 0 and bool(text.strip()),
        "text": (text or "").strip()[:60],
        "err": (err or "")[:200],
        "secs": round(dt, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


async def main() -> int:
    results = await asyncio.gather(*(one(m) for m in MODELS))
    print(f"{'model':<32} {'ok':<4} {'rc':<3} {'secs':<6} {'p_tok':<7} {'o_tok':<7} text/err")
    print("-" * 100)
    all_ok = True
    for r in results:
        all_ok = all_ok and r["ok"]
        detail = r["text"] if r["ok"] else f"ERR: {r['err']}"
        print(
            f"{r['model']:<32} {str(r['ok']):<4} {r['rc']:<3} {r['secs']:<6} "
            f"{str(r['prompt_tokens']):<7} {str(r['output_tokens']):<7} {detail}"
        )
    print("-" * 100)
    print("ALL_OK" if all_ok else "SOME_FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
