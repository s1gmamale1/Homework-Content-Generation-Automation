# Gemini `transport=api` (SDK/Vertex) — 4-model run + single-PC max concurrency

**2026-06-16, Mac mini, Vertex service-account auth.** Companion artifacts (homeworks, raw CSV/JSON)
were saved to `~/Documents/gemini-4model-biology-g9-fungi/`. This note keeps the reusable findings in-repo.

## What was run
- **4 homeworks**, identical lesson (biology g9 §6 «Zamburug'lar dunyosi»), one per gemini model
  (`2.5-flash`, `2.5-pro`, `3.1-pro-preview`, `3.1-flash-lite-preview`), all `transport=api`.
- A **money-safe concurrency stress probe** (cheap minimal-token SDK calls — NOT extra homeworks).

## Operational gotcha (reusable) — why api jobs wouldn't get claimed
On a worker with **gemini Vertex creds but no `ANTHROPIC_API_KEY`**, a `transport=api` job launched with
default role transports sat **pending forever**. Cause: `claim_next_job` ANDs per-role capability, and
under an api job the **judge** (`judge_provider=claude`) and **extract** roles inherit `transport=api`:

- `judge_transport=inherit` → api → needs `judge_api_ok` (claude) → **False here**.
- `extract_transport=inherit` → api → needs `extract_api_ok`; `.env` pins `EXTRACT_PROVIDER=claude` → **False**.
- Also: **`api_transport` is text-only**, so extract (which attaches the PDF) *can't* run via api on any
  provider — it must be cli regardless.

**Fix when launching api jobs on a gemini-only worker:** set `extract_transport=cli` and
`judge_transport=cli`. Content phases still run on the gemini SDK/Vertex api; extract+judge use the
local claude CLI ($0 marginal, PDF-native). Diagnose claim-eligibility with `scripts/diag_claim.py`.

## 4-model comparison (content = gemini SDK/Vertex api)
| Model | Wall (s) | API in-tok | API out-tok | API $ |
|---|--:|--:|--:|--:|
| gemini-2.5-flash | 473 | 50,664 | 39,459 | $0.114 |
| gemini-2.5-pro | 423 | 52,647 | 36,139 | $0.427 |
| gemini-3.1-pro-preview | 682 | 60,000 | 48,382 | $0.701 |
| gemini-3.1-flash-lite-preview | 457 | 50,994 | 12,963 | $0.032 |

Total real api spend **$1.27** (4 homeworks, content only; cli extract+judge = $0 marginal).
Pro tier burns far more output (thinking tokens) → 4–20× the flash cost. flash-lite is cheapest by a wide margin.

## Max concurrency on one PC (api/SDK path)
Method: ramp concurrent minimal-token `gemini-2.5-flash-lite` SDK calls, N=1..64, 2 runs.

- **100% success through N=48** both runs; **N=64 is the knee** (run 1: 29/128 rate-limited; run 2: clean).
- **Throughput plateaus ~10–11 req/s** from N≥24 → server-side rate limit, not client.
- **Host never strained: ~25% CPU, ≤270 MB RSS at N=64.** The PC is *not* the bottleneck; the **Vertex
  per-model quota** is (soft ceiling ~50–64 concurrent flash-tier calls).
- **Per-homework peak fan-out = 3** concurrent SDK calls (DAG wave). 4 concurrent homeworks peaked at 7.

**Model:** `max_concurrent_homeworks ≈ ceiling / fanout ≈ (50…64)/3 ≈ 16–21` flash-tier homeworks,
**API-bound**. Lower for pro tier (smaller quota). The app's `worker_concurrency` (default 4) is the
practical cap until raised; one Vertex project = one shared quota pool.

Stress test total cost: **$0.0009** (796 calls). Hard rule honored: never mass-generate homeworks to probe limits.

## Harness (in `scripts/`)
- `smoke_4models.py` — reachability/auth smoke for the 4 models via the SDK.
- `save_homeworks.py` — export a job's phase_outputs + priced usage to `~/Documents`.
- `stress_concurrency.py` — concurrency ramp (writes `stress_results/{levels.csv,raw.json}`).
- `fanout_analysis.py` — per-homework + N-job concurrent SDK-call fan-out from `agent_usages`.
- `diag_claim.py` — print runtime worker `CAPABILITIES` + dry-run the claim gate per pending job.
