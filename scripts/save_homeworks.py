"""Export the 4 gemini-model homeworks (transport=api / SDK-Vertex content) to
~/Documents for side-by-side comparison. Per model: one .md per content phase +
a combined packet. Plus an INDEX.md comparing wall-clock, tokens, and REAL api $
(cli extract/judge rows are $0 marginal — subscription — so cost is the
auth_mode='api' rows only, priced via app.services.pricing).

Usage: python scripts/save_homeworks.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, ".")

import asyncpg  # noqa: E402

from app.services import pricing  # noqa: E402

DSN = "postgresql://edu:edu@localhost:5432/edu_copy"
OUT = Path.home() / "Documents" / "gemini-4model-biology-g9-fungi"

# historical repro — model labels intentionally pinned to the actual models
# these already-completed jobs (fixed UUIDs below) were generated with.
JOBS = [
    ("7979bd1f-a19b-48d7-9d30-ab71a969e87c", "gemini-2.5-flash"),
    ("1ac8ad89-f8ab-4892-8064-774a47234af4", "gemini-2.5-pro"),
    ("e1d8b811-b23b-4814-9d7d-73b7fdfa6654", "gemini-3.1-pro-preview"),
    ("1a4f4fa2-6a50-47fd-9d76-14f0f4d27803", "gemini-3.1-flash-lite-preview"),
]


async def export_one(conn, job_id: str, model: str) -> dict:
    job = await conn.fetchrow(
        "SELECT status, started_at, completed_at, subject FROM homework_jobs WHERE id=$1", job_id
    )
    phases = await conn.fetch(
        "SELECT phase_order, phase_name, status, output_md, tokens_input, tokens_output "
        "FROM phase_outputs WHERE job_id=$1 ORDER BY phase_order",
        job_id,
    )
    usages = await conn.fetch(
        "SELECT auth_mode, provider, model_name, operation, prompt_tokens, output_tokens, "
        "cached_tokens, total_tokens, success FROM agent_usages WHERE homework_job_id=$1",
        job_id,
    )

    model_dir = OUT / model
    model_dir.mkdir(parents=True, exist_ok=True)
    combined = [f"# {model} — biology g9 · «Zamburug'lar dunyosi» (Fungi)\n",
                f"_transport: content=api(SDK/Vertex), extract+judge=cli · job {job_id}_\n"]
    written = 0
    for p in phases:
        if p["phase_name"] == "extract" or p["status"] != "done" or not (p["output_md"] or "").strip():
            continue
        fname = f"{p['phase_order']:02d}-{p['phase_name']}.md"
        (model_dir / fname).write_text(p["output_md"], encoding="utf-8")
        combined.append(f"\n\n---\n\n## {p['phase_order']:02d} · {p['phase_name']}\n\n{p['output_md']}")
        written += 1
    (model_dir / "_COMBINED.md").write_text("\n".join(combined), encoding="utf-8")

    # Real api $ = priced auth_mode='api' rows only (cli rows are $0 marginal).
    api_cost = 0.0
    api_in = api_out = cli_in = cli_out = 0
    for u in usages:
        usage = {
            "prompt_tokens": u["prompt_tokens"], "output_tokens": u["output_tokens"],
            "cached_tokens": u["cached_tokens"], "total_tokens": u["total_tokens"],
        }
        if u["auth_mode"] == "api":
            api_cost += pricing.cost_usd(u["provider"], u["model_name"], usage)
            api_in += u["prompt_tokens"] or 0
            api_out += u["output_tokens"] or 0
        else:
            cli_in += u["prompt_tokens"] or 0
            cli_out += u["output_tokens"] or 0

    secs = None
    if job["started_at"] and job["completed_at"]:
        secs = round((job["completed_at"] - job["started_at"]).total_seconds(), 1)
    return {
        "model": model, "job_id": job_id, "status": job["status"],
        "phases_written": written, "wall_secs": secs,
        "api_in": api_in, "api_out": api_out, "cli_in": cli_in, "cli_out": cli_out,
        "api_cost_usd": round(api_cost, 5), "n_usage_rows": len(usages),
    }


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    conn = await asyncpg.connect(DSN)
    try:
        rows = [await export_one(conn, jid, m) for jid, m in JOBS]
    finally:
        await conn.close()

    lines = [
        "# Gemini 4-model homework comparison — biology g9 · «Zamburug'lar dunyosi» (Fungi kingdom)",
        "",
        "**Lesson:** biology grade 9, section 6 (pp. 19–24). Book `9c0e5362`.",
        "**Transport:** homework **content** generated via **gemini SDK / Vertex service-account** (`transport=api`).",
        "Extract (whole-PDF read) + LLM judge run via the local **claude CLI** ($0 marginal) — the SDK api path is",
        "text-only so it cannot take the PDF attachment, and this Mac has no `ANTHROPIC_API_KEY`.",
        "",
        "| Model | Status | Phases | Wall (s) | API in-tok | API out-tok | **API $** |",
        "|---|---|---|---|---|---|---|",
    ]
    total_cost = 0.0
    for r in rows:
        total_cost += r["api_cost_usd"]
        lines.append(
            f"| `{r['model']}` | {r['status']} | {r['phases_written']} | {r['wall_secs']} | "
            f"{r['api_in']:,} | {r['api_out']:,} | ${r['api_cost_usd']:.5f} |"
        )
    lines += ["", f"**Total real API spend (4 homeworks): ${total_cost:.4f}**",
              "", "_cli extract+judge token totals excluded from $ (subscription, $0 marginal)._", ""]
    (OUT / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved to: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
