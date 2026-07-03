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

**First-pass model:** `max_concurrent_homeworks ≈ ceiling / fanout ≈ (50…64)/3 ≈ 16–21` flash-tier
homeworks, API-bound. **Refined below — this clean number does NOT generalize.**

### Multi-model refinement (all 4 production models, 2 runs each)
`scripts/stress_multimodel.py` ramped `2.5-flash`, `2.5-pro`, `3.1-pro-preview`, `3.1-flash-lite-preview`
(thinking off + output capped → 1–12 tok/probe). The "clean ceiling" picture broke:

- **The limit is a per-model *rolling RPM quota*, bursty and partly shared — not a fixed concurrency
  wall.** `3.1-flash-lite` ran clean to N=64 in run 1, then 429'd at **N=2** in run 2 (preceding ramps
  drained the minute's budget). Same model, 10× different cutoff minutes apart.
- **Robust cross-run ranking (derived safe concurrent *jobs*, ÷3 fan-out):** `2.5-flash` **~2–3**
  (tightest, 429s from N≈6–8); `2.5-pro` **~16–20** (0 errors to N=64 both runs); `3.1-pro-preview`
  **~10–16** (high tolerance but slow, p95 up to 20–160 s); `3.1-flash-lite` **~2–15** (unstable).
- **The PC is never the bottleneck; the per-model Vertex quota is, and it fluctuates.** No single
  stable "max jobs" holds across models or minute-to-minute. The authoritative number is the Vertex
  console quota (`aiplatform.googleapis.com`, per-model RPM + concurrent, per region).
- **Practical:** `~4` concurrent jobs is safe for any model (the `worker_concurrency` default); pin to
  `2.5-pro` for `~16–20`; request a quota increase to go higher. Raw: `multimodel_run{1,2}.json`.

Stress test total cost: **<$0.02** (flash-lite ramp 796 calls = $0.0009; multi-model ramps ~2.6–3k tok
each run). Hard rule honored: never mass-generate homeworks to probe limits.

## Harness (in `scripts/`)
- `smoke_4models.py` — reachability/auth smoke for the 4 models via the SDK.
- `save_homeworks.py` — export a job's phase_outputs + priced usage to `~/Documents`.
- `stress_concurrency.py` — single-model concurrency ramp (writes `stress_results/{levels.csv,raw.json}`).
- `stress_multimodel.py` — per-model ramp across all 4 models (thinking off; writes `multimodel.json`).
- `fanout_analysis.py` — per-homework + N-job concurrent SDK-call fan-out from `agent_usages`.
- `diag_claim.py` — print runtime worker `CAPABILITIES` + dry-run the claim gate per pending job.

## ADDENDUM 2026-07-03 — production-model rerun: the June ceilings are OBSOLETE on the current key

Reran the same probe (2 back-to-back runs, N=2..64, minimal-token, thinking off, ~$0.01 total)
against the **October production trio** on the current Fleet-assigned SA key/project:

| Model (role) | Run 1 | Run 2 (drained-budget) | Peak observed |
|---|---|---|---|
| `gemini-3-flash-preview` (content) | 100% to N=64 | 100% to N=64 | 2,509 rpm |
| `gemini-2.5-flash` (judge+extract) | 100% to N=64 | 100% to N=64 | 3,400 rpm |
| `gemini-3.1-pro-preview` (solver) | 100% to N=64 | 100% to N=64 | 1,748 rpm |

- **Zero rate-limit errors anywhere** — including `2.5-flash`, which was the June study's
  tightest bucket (429s from N≈6–8). Either this project's quotas differ from the June
  key's, or Google raised preview-tier quotas. Both runs clean back-to-back rules out the
  rolling-minute flakiness that plagued June.
- **Derived ceiling: ≥64 concurrent calls per model bucket** on ONE key → ÷3 fan-out ≈
  **≥21 concurrent homeworks**, and content/judge/solver draw from **separate per-model
  buckets**, so a mixed-role pipeline spreads load further.
- **Honest limits of the probe:** minimal-token calls barely touch the TPM (tokens/min)
  dimension of quota; real homework calls hold 30–300 s and burn ~90k tokens each. No RPM
  ceiling found ≤64; TPM under real load remains unverified → BULK should still ramp
  gradually in its first hour and lean on the shipped reactive 429 backoff.
- **Planning consequence:** `worker_concurrency=4` is very conservative for the bulk run;
  ~16–20 per worker looks safe on RPM evidence, TPM-permitting. Raw: session scratchpad
  `stress_prod/multimodel_run{1,2}.json` (probe copy of `scripts/stress_multimodel.py`
  with the production model list).
