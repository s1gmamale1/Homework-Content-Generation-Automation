# Usage page redesign + per-model breakdown — Design

**Date:** 2026-06-06
**Branch:** Nggaev-v2
**Status:** design locked (interactive brainstorm w/ visual companion; user-approved direction)

## Problem

The `/usage` page is hard to read. It stacks all three windows (1h/24h/7d) inside each provider card with a dense single-line mono footer (`2.1s · prompt: X · cached: Y · output: Z · success: N%`), making it hard to (a) compare providers, (b) see token usage at a glance, and (c) understand what the numbers mean. It also **hardcodes 4 providers** (`claude/kimi/codex/gemini`) so the registered 5th, `opencode`, never appears — even though `/agent/stats` already returns it (the endpoint iterates `providers.PROVIDERS.keys()`, which includes `OPENCODE`). Finally, the token labels (`prompt`/`cached`/`output`) are unclear to the operator.

## Goals

1. Make per-provider usage scannable: each provider's call headroom (vs its cap), token usage, duration, and success, for a chosen window.
2. Add a **per-model breakdown** — drill each provider into the models it actually used in that window, with each model's calls/tokens/duration/success.
3. Show **every registered provider dynamically** (so `opencode` and any future provider appear with no code change).
4. Make the token figures self-explanatory (clear labels + a one-line legend).

## Non-goals (explicitly out of scope)

- **No $ cost / cost estimation.** No price table, no money math. (User-confirmed: real billed cost is unobtainable from the CLIs; Claude is a flat Max plan; Kimi reports 0 tokens.)
- **No token budgets/limits.** The cap stays **calls-per-window** (`AGENT_LIMIT_<provider>_<window>` in `.env`). Tokens are informational only.
- **No totals/health additions** (all-time totals, avg latency, last-error) this round.
- **No real provider-quota fetching** (CLIs don't expose it headless).

## Decisions (locked, with adjustable defaults)

- **Limit basis:** calls-per-window (existing). Headroom bar = `pct_of_limit` (calls/cap). Unmetered (cap 0/null) → no bar, "unmetered" note.
- **Layout ("Control Center"):** Apple-style segmented window control (`1h | 24h | 7d`) + a **2-column grid of frosted-glass provider tiles** over an ambient gradient-mesh background. Each tile is keyed to a per-provider signature color (gradient badge + soft corner glow) and shows an **Apple activity-ring** for calls-vs-cap headroom (% in the ring center), big tabular `calls / cap`, and three **Input/Cached/Output** stat pills. The **busiest provider in the selected window auto-expands to a full-width "hero" tile** with its per-model table beside the ring; other tiles are compact and expand in place (full-width, `grid-column: 1 / -1`) on click. Degraded tiles (no calls / unmetered) dim.
- **Fonts:** reuse the app's existing **Geist / Geist Mono** (`--font-sans` / `--font-mono`, already loaded in `index.html`) — do NOT introduce new web fonts; that would clash with the rest of the console.
- **Per-provider accent palette** (new page-local constants; gradient `[from, to]`): claude `#ff7a4d→#ff4d6d`, gemini `#4d9bff→#42e8e0`, kimi `#b07bff→#7a5cff`, codex `#46e0a0→#2bd4c4`, opencode/unknown `slate (--color-ink-muted)`.
- **Activity-ring color:** the provider's brand gradient normally; **tone overrides** when near/over cap — amber (`--color-accent`) at ≥80%, red (`--color-error`) at >100% — so headroom danger still reads.
- **Default window:** `24h` (more populated than 1h, less noisy). Adjustable.
- **Token labels:** rename chips to **Input** (`prompt_tokens`), **Cached** (`cached_tokens`), **Output** (`output_tokens`), with a one-line legend under the page intro: *"Input = tokens read · Cached = reused input (cheaper) · Output = tokens generated."* Tooltips optional.
- **Color thresholds:** reuse existing `pickTone` — `ok` <50%, `warn` 50–79%, `hot` ≥80%, `over` >100% (red). Unchanged.
- **Kimi:** when a provider's window tokens are all 0 AND it had calls, show *"tokens not reported by this CLI"* instead of `0/0/0`.
- **Degraded states:** `calls===0` → "no calls in this window"; unmetered → "unmetered (no cap set)".
- **Collapsed rows** show "· N models"; the model sub-rows appear on expand.
- **NULL `model_name`** (provider default) is bucketed under the label `(default)`.

## Backend change (small — per-model aggregation only)

**`app/repositories/agent_usage.py`** — add `stats_by_provider_model(session, *, since) -> list[dict]`: same query as `stats_by_provider` but `GROUP BY provider, model_name`, returning rows with `provider, model_name, calls, prompt_tokens, output_tokens, cached_tokens, success_count`. Duration is summed in Python keyed by `(provider, model_name)`, mirroring the existing `_parse_duration_seconds` approach. `model_name` NULL is preserved as-is (FE maps to `(default)`).

**`app/api/v1/jobs.py` `get_agent_stats`** — for each window, also call `stats_by_provider_model` and attach a `models` array to each provider's window object. Provider-level totals (calls, cap, `pct_of_limit`, token sums, `success_pct`) are unchanged — they remain the sum across that provider's models. Each model entry:
```json
{ "model_name": "claude-opus-4-8", "calls": 30, "prompt_tokens": 900000,
  "output_tokens": 320000, "cached_tokens": 300000, "duration_secs": 1540.0,
  "success_pct": 99.0 }
```
No model-level cap/`pct_of_limit` (the cap is per-provider). Providers with no calls in a window get `models: []`.

Two implementation details:
- `stats_by_provider_model` returns `success_count` (not a pct); `get_agent_stats` computes each model's `success_pct = round(100 * success_count / calls, 1)` (calls>0 else 0.0), mirroring the existing provider-level math — do **not** pass `success_count` straight through.
- While editing `get_agent_stats`, fix its stale comment (`jobs.py:383-384`) that still says "the four CLIs (claude, kimi, codex, gemini)" → five, incl. `opencode` (doc-currency; same staleness already fixed in CLAUDE.md).

**Response shape (per provider per window) becomes:**
```
calls, duration_secs, prompt_tokens, output_tokens, cached_tokens,
success_pct, limit_calls_per_window, pct_of_limit, models[]
```

## Frontend redesign

**`web/src/lib/types.ts`** — add `ProviderModelStat` (the model entry above) and `models: ProviderModelStat[]` to `ProviderStatsWindow`.

**`web/src/routes/usage.tsx`** — rewrite to the Control Center design:
- **Ambient background:** a fixed, behind-content layer of soft radial gradient glows (page-local; doesn't touch the app shell).
- **Segmented window control** from `data.windows` (`1h | 24h | 7d`); local `selectedWindow` state (default `24h` if present, else first). Active segment = light fill / dark text.
- **Providers rendered dynamically:** iterate `Object.keys(data.providers)` in display order — preferred `["claude","gemini","kimi","codex","opencode"]` then any unknown appended — so a new provider appears automatically.
- **`ActivityRing`** subcomponent: SVG ring, `r=34`, circumference `2πr`, `stroke-dashoffset = C·(1 − pct/100)`; stroke = provider gradient, overridden to amber ≥80% / red >100%; `%` + "of cap" in the center. Unmetered/no-calls → no ring.
- **Provider tile:** frosted glass (`backdrop-blur`, hairline border, soft shadow, hover-lift, staggered load), gradient badge + corner glow in the provider color; ring + big tabular `calls / cap` + `{success}% ok · {duration}` meta; three **Input/Cached/Output** stat pills (or the Kimi "tokens not reported" note); a "N models" chip when `models.length`.
- **Hero + expand:** the provider with the **max calls** in the selected window starts expanded as a full-width tile (`grid-column: 1 / -1`) showing the **per-model table** (Model · Input · Cached · Output · Calls·success) beside the ring; any tile toggles expand on click; expanded = full width. If no provider has calls, all tiles stay compact.
- **Legend** line defining Input/Cached/Output; keep the `.env / AGENT_LIMIT_*` caption and `synced {relative}` stamp.
- Loading (tile skeletons) / error states preserved.
- Uses the app's `--color-*` tokens + `--font-sans`/`--font-mono` (Geist); per-provider gradients via inline style from the accent palette.

## Data flow

```
agent_usages rows ──► stats_by_provider (per-provider totals, unchanged)
                  └─► stats_by_provider_model (NEW: per provider+model)
                         │
get_agent_stats nests models[] under each provider/window
                         │
/usage page: window tabs → dynamic provider rows → expand → model sub-rows
```

## Error handling / edge cases

- Window with no rows for a provider → provider totals zero, `models: []`, row shows "no calls".
- Unmetered provider (cap 0) → no bar, "unmetered" note; model sub-rows still render if calls exist.
- All-zero tokens with calls>0 (Kimi) → "tokens not reported by this CLI"; model rows likewise omit token chips.
- `model_name` NULL → `(default)` bucket.
- Unknown/new provider in the API response → rendered (appended after the known order).

## Testing

DB-free, matching suite conventions.
- **Backend unit (`tests/.../test_agent_usage_stats.py` or extend existing):** `stats_by_provider_model` groups by `(provider, model_name)` and sums tokens/calls; duration parsed/summed per `(provider, model)`; NULL model preserved. Drive with an in-memory list or mocked session rows per the existing aggregation-test pattern.
- **API shape test:** `get_agent_stats` response includes `models[]` under a provider/window with the right per-model sums (mock `stats_by_provider` + `stats_by_provider_model`).
- **FE:** `tsc -p tsconfig.app.json --noEmit` clean; the page is presentational (no new FE unit test framework introduced).
- No CLI smoke (does not touch generation).

## Rollout

Pure additive: new repo fn + endpoint field + FE rewrite. No migration, no DB change. Restart server to serve `models[]`. Worklog on completion.
