# Usage Page Redesign + Per-Model Breakdown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign `/usage` to clearly show every provider's calls-vs-cap, token usage, and a per-model drill-down, across selectable time windows.

**Architecture:** Add one backend aggregation (`stats_by_provider_model`) and nest a `models[]` array under each provider/window in `/agent/stats`; rebuild the React page as an Apple-style "Control Center" — a grid of frosted-glass provider tiles with activity-ring headroom, per-provider accent colors, and a full-width hero tile (per-model table) for the busiest provider. Calls-per-window stays the only cap; tokens are informational with clear Input/Cached/Output labels.

**Tech Stack:** FastAPI, SQLAlchemy (async), Pydantic, pytest, React/TS/Vite, Tailwind v4. Reuses the app's Geist / Geist Mono fonts and `--color-*` tokens.

**Spec:** `docs/superpowers/specs/2026-06-06-usage-page-redesign-design.md`

**Standing rules:** Stage only the files each task lists. pytest via `.\.venv\Scripts\python.exe -m pytest`. tsc via the PowerShell tool (`Set-Location web; npx tsc -p tsconfig.app.json --noEmit`). No migration, no DB change. No generation impact → no CLI smoke.

---

## File Structure

- **Modify** `app/repositories/agent_usage.py` — add `stats_by_provider_model` (mirror of `stats_by_provider`, grouped by provider+model).
- **Modify** `app/api/v1/jobs.py` `get_agent_stats` — nest `models[]` per window (compute per-model `success_pct`); fix stale "four CLIs" comment.
- **Modify** `web/src/lib/types.ts` — `ProviderModelStat` + `models[]` on `ProviderStatsWindow`.
- **Modify** `web/src/styles/globals.css` — add a `tile-rise` keyframe + `.animate-tile-rise` utility (staggered tile entrance).
- **Rewrite** `web/src/routes/usage.tsx` — Control Center grid: glass tiles, activity rings, per-provider accents, hero tile + per-model table.
- **Tests:** `tests/services/test_agent_usage_models.py` (repo), `tests/api/test_agent_stats_models.py` (endpoint).

---

## Task 1: `stats_by_provider_model` repo aggregation

**Files:**
- Modify: `app/repositories/agent_usage.py` (add after `stats_by_provider`, ~line 182)
- Test: `tests/services/test_agent_usage_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_agent_usage_models.py
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.repositories import agent_usage as au


def test_stats_by_provider_model_groups_and_sums_duration():
    # First execute() -> the GROUP BY aggregate rows; second -> (provider, model, duration) rows.
    agg = [
        SimpleNamespace(provider="claude", model_name="claude-opus-4-8", calls=2,
                        prompt_tokens=900, output_tokens=320, cached_tokens=300, success_count=2),
        SimpleNamespace(provider="claude", model_name="claude-haiku-4-5", calls=1,
                        prompt_tokens=300, output_tokens=90, cached_tokens=80, success_count=1),
    ]
    dur = [
        ("claude", "claude-opus-4-8", "1.5s"),
        ("claude", "claude-opus-4-8", "500ms"),
        ("claude", "claude-haiku-4-5", "2s"),
    ]
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: agg),
        SimpleNamespace(all=lambda: dur),
    ]))
    out = asyncio.run(au.stats_by_provider_model(session, since=datetime.now(timezone.utc)))

    opus = next(r for r in out if r["model_name"] == "claude-opus-4-8")
    assert opus["calls"] == 2
    assert opus["prompt_tokens"] == 900 and opus["output_tokens"] == 320 and opus["cached_tokens"] == 300
    assert opus["success_count"] == 2
    assert opus["duration_secs"] == 2.0   # 1.5s + 500ms
    haiku = next(r for r in out if r["model_name"] == "claude-haiku-4-5")
    assert haiku["duration_secs"] == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_agent_usage_models.py -q`
Expected: FAIL (`AttributeError: module 'app.repositories.agent_usage' has no attribute 'stats_by_provider_model'`).

- [ ] **Step 3: Implement the function**

Add to `app/repositories/agent_usage.py` immediately after `stats_by_provider` (the `case`, `func`, `select` imports already exist at the top of the file):

```python
async def stats_by_provider_model(
    session: AsyncSession,
    *,
    since: datetime,
) -> list[dict]:
    """Like `stats_by_provider`, but one row per (provider, model_name).

    Returns dicts with: provider, model_name (may be None for provider-default),
    calls, duration_secs, prompt_tokens, output_tokens, cached_tokens,
    success_count. Duration is summed in Python keyed by (provider, model_name),
    mirroring `stats_by_provider`.
    """
    stmt = (
        select(
            AgentUsage.provider.label("provider"),
            AgentUsage.model_name.label("model_name"),
            func.count().label("calls"),
            func.coalesce(func.sum(AgentUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(AgentUsage.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(AgentUsage.cached_tokens), 0).label("cached_tokens"),
            func.coalesce(
                func.sum(case((AgentUsage.success.is_(True), 1), else_=0)), 0
            ).label("success_count"),
        )
        .where(AgentUsage.started_at >= since)
        .group_by(AgentUsage.provider, AgentUsage.model_name)
    )
    rows = (await session.execute(stmt)).all()

    dur_stmt = (
        select(AgentUsage.provider, AgentUsage.model_name, AgentUsage.duration)
        .where(AgentUsage.started_at >= since)
        .where(AgentUsage.duration.is_not(None))
    )
    dur_rows = (await session.execute(dur_stmt)).all()
    duration_by: dict[tuple, float] = {}
    for provider, model_name, duration in dur_rows:
        key = (provider, model_name)
        duration_by[key] = duration_by.get(key, 0.0) + _parse_duration_seconds(duration)

    return [
        {
            "provider": r.provider,
            "model_name": r.model_name,
            "calls": int(r.calls),
            "duration_secs": round(duration_by.get((r.provider, r.model_name), 0.0), 1),
            "prompt_tokens": int(r.prompt_tokens),
            "output_tokens": int(r.output_tokens),
            "cached_tokens": int(r.cached_tokens),
            "success_count": int(r.success_count),
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_agent_usage_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/agent_usage.py tests/services/test_agent_usage_models.py
git commit -m "feat(usage): stats_by_provider_model aggregation"
```

---

## Task 2: Nest `models[]` in `/agent/stats` + compute per-model success_pct + comment fix

**Files:**
- Modify: `app/api/v1/jobs.py` (`get_agent_stats`, lines 382-458)
- Test: `tests/api/test_agent_stats_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_agent_stats_models.py
import asyncio
from unittest.mock import AsyncMock, patch

import app.api.v1.jobs as jobs_mod


def test_get_agent_stats_nests_models_with_computed_success_pct():
    prov_rows = [{
        "provider": "claude", "calls": 3, "duration_secs": 2.0,
        "prompt_tokens": 1200, "output_tokens": 410, "cached_tokens": 380,
        "success_count": 3,
    }]
    model_rows = [
        {"provider": "claude", "model_name": "claude-opus-4-8", "calls": 4,
         "duration_secs": 1.5, "prompt_tokens": 900, "output_tokens": 320,
         "cached_tokens": 300, "success_count": 3},
        {"provider": "claude", "model_name": None, "calls": 1,
         "duration_secs": 0.5, "prompt_tokens": 10, "output_tokens": 5,
         "cached_tokens": 0, "success_count": 1},
    ]
    with patch.object(jobs_mod.agent_usage_repo, "stats_by_provider",
                      AsyncMock(return_value=prov_rows)), \
         patch.object(jobs_mod.agent_usage_repo, "stats_by_provider_model",
                      AsyncMock(return_value=model_rows)):
        resp = asyncio.run(jobs_mod.get_agent_stats(session=None))

    claude_24h = resp["providers"]["claude"]["24h"]
    assert "models" in claude_24h
    names = {m["model_name"] for m in claude_24h["models"]}
    assert "claude-opus-4-8" in names
    assert "(default)" in names   # NULL model_name bucketed
    opus = next(m for m in claude_24h["models"] if m["model_name"] == "claude-opus-4-8")
    assert opus["success_pct"] == 75.0   # 3/4, computed from success_count
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_agent_stats_models.py -q`
Expected: FAIL (`KeyError: 'models'`).

- [ ] **Step 3: Implement — fetch + nest models, compute success_pct**

In `app/api/v1/jobs.py`, inside `get_agent_stats`'s window loop, after the line `by_provider = {row["provider"]: row for row in rows}` (currently `:419`), add the per-model fetch + shaping:

```python
        model_rows = await agent_usage_repo.stats_by_provider_model(session, since=since)
        models_by_provider: dict[str, list[dict]] = {}
        for mr in model_rows:
            m_calls = int(mr["calls"])
            models_by_provider.setdefault(mr["provider"], []).append({
                "model_name": mr["model_name"] or "(default)",
                "calls": m_calls,
                "duration_secs": round(float(mr["duration_secs"]), 1),
                "prompt_tokens": int(mr["prompt_tokens"]),
                "output_tokens": int(mr["output_tokens"]),
                "cached_tokens": int(mr["cached_tokens"]),
                "success_pct": (
                    round(100.0 * int(mr["success_count"]) / m_calls, 1)
                    if m_calls > 0 else 0.0
                ),
            })
```

Then add `"models"` to the per-window dict (the `providers[provider][window_label] = {...}` block at `:441`), as the last key:

```python
                "pct_of_limit": pct_of_limit,
                "models": models_by_provider.get(provider, []),
            }
```

- [ ] **Step 4: Fix the stale comment**

In the same file, update the comment at `:383-384`:

```python
# Per-provider rolling stats over fixed windows. Surfaces local consumption
# (calls + duration + tokens) issued by THIS app — the five CLIs (claude,
# kimi, codex, gemini, opencode) don't expose real quota APIs in headless
```

- [ ] **Step 5: Run test to verify it passes (and existing API tests)**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_agent_stats_models.py tests/api -q`
Expected: PASS (new test + existing API tests green).

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/jobs.py tests/api/test_agent_stats_models.py
git commit -m "feat(usage): nest per-model breakdown in /agent/stats"
```

---

## Task 3: Frontend types — `ProviderModelStat` + `models[]`

**Files:**
- Modify: `web/src/lib/types.ts` (lines 102-117)

- [ ] **Step 1: Add the type and field**

Replace the `ProviderStatsWindow` interface block (`:103-112`) with:

```typescript
export interface ProviderModelStat {
  model_name: string;
  calls: number;
  duration_secs: number;
  prompt_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  success_pct: number;
}
export interface ProviderStatsWindow {
  calls: number;
  duration_secs: number;
  prompt_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  success_pct: number;
  limit_calls_per_window: number | null;
  pct_of_limit: number | null;
  models: ProviderModelStat[];
}
```

- [ ] **Step 2: Typecheck**

Run (PowerShell tool): `Set-Location 'C:\Users\Recruiter\Desktop\Homework-Content-Generation-Automation\web'; npx tsc -p tsconfig.app.json --noEmit`
Expected: clean. The old `usage.tsx` only *reads* fields off `ProviderStatsWindow`, so adding the required `models` field doesn't break it (no FE code constructs that type as a literal).

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/types.ts
git commit -m "feat(usage): ProviderModelStat type + models[] on ProviderStatsWindow"
```

---

## Task 4: Control Center rebuild of `web/src/routes/usage.tsx`

**Files:**
- Modify: `web/src/styles/globals.css` (append a keyframe + utility)
- Rewrite: `web/src/routes/usage.tsx`

- [ ] **Step 1: Add the tile-rise animation to `globals.css`**

Append to the end of `web/src/styles/globals.css`:

```css
@keyframes tile-rise {
  from { opacity: 0; transform: translateY(14px); }
  to   { opacity: 1; transform: none; }
}
.animate-tile-rise {
  animation: tile-rise 0.55s cubic-bezier(0.2, 0.7, 0.2, 1) both;
}
@media (prefers-reduced-motion: reduce) {
  .animate-tile-rise { animation: none; }
}
```

- [ ] **Step 2: Replace the entire `web/src/routes/usage.tsx` with the Control Center implementation**

```tsx
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Gauge } from "lucide-react";
import { useMemo, useState } from "react";
import { Eyebrow } from "@/components/eyebrow";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { AgentStats, ProviderModelStat, ProviderStatsWindow } from "@/lib/types";
import { cn } from "@/lib/utils";

const PROVIDER_LABELS: Record<string, string> = {
  claude: "Claude",
  gemini: "Gemini",
  kimi: "Kimi",
  codex: "Codex",
  opencode: "Opencode",
};
const PROVIDER_ORDER = ["claude", "gemini", "kimi", "codex", "opencode"];
const WINDOW_LABELS: Record<string, string> = {
  "1h": "Last hour",
  "24h": "Last 24h",
  "7d": "Last 7 days",
};
// Per-provider signature gradient [from, to].
const ACCENTS: Record<string, [string, string]> = {
  claude: ["#ff7a4d", "#ff4d6d"],
  gemini: ["#4d9bff", "#42e8e0"],
  kimi: ["#b07bff", "#7a5cff"],
  codex: ["#46e0a0", "#2bd4c4"],
};
function accentOf(id: string): [string, string] {
  return ACCENTS[id] ?? ["oklch(0.60 0.01 270)", "oklch(0.48 0.01 270)"];
}

// Known providers in a stable order, then any unknown the API returns.
function orderedProviders(providers: Record<string, unknown>): string[] {
  const keys = Object.keys(providers);
  const known = PROVIDER_ORDER.filter((p) => keys.includes(p));
  const extra = keys.filter((p) => !PROVIDER_ORDER.includes(p)).sort();
  return [...known, ...extra];
}

const CODE =
  "rounded-(--radius-xs) bg-(--color-canvas) px-1 py-0.5 font-mono text-[0.78rem] text-(--color-ink)";

export function UsagePage() {
  const { data, isLoading, error } = useQuery<AgentStats>({
    queryKey: ["agent-stats"],
    queryFn: () => api.getAgentStats(),
    refetchInterval: 60_000,
    refetchOnWindowFocus: false,
  });

  const windows = data?.windows ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const selectedWindow =
    picked ?? (windows.includes("24h") ? "24h" : windows[0]) ?? "24h";

  const ids = useMemo(() => (data ? orderedProviders(data.providers) : []), [data]);

  // Busiest provider in the selected window starts expanded as the hero tile.
  const heroId = useMemo(() => {
    if (!data) return null;
    let best: string | null = null;
    let max = 0;
    for (const id of ids) {
      const c = data.providers[id]?.[selectedWindow]?.calls ?? 0;
      if (c > max) {
        max = c;
        best = id;
      }
    }
    return best;
  }, [data, ids, selectedWindow]);

  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const isOpen = (id: string) => overrides[id] ?? id === heroId;
  const toggle = (id: string) =>
    setOverrides((o) => ({ ...o, [id]: !(o[id] ?? id === heroId) }));

  return (
    <div className="relative">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-12 z-0 h-[440px]"
        style={{
          background:
            "radial-gradient(40% 60% at 12% 0%, oklch(0.6 0.18 30 / 14%), transparent 70%), radial-gradient(40% 55% at 90% 6%, oklch(0.65 0.16 250 / 12%), transparent 70%), radial-gradient(40% 50% at 65% 100%, oklch(0.6 0.17 300 / 10%), transparent 70%)",
        }}
      />
      <div className="relative z-10">
        <Eyebrow>Dashboard</Eyebrow>
        <h1 className="mt-3 flex items-center gap-2.5 text-3xl font-semibold tracking-tight text-(--color-ink)">
          <Gauge className="size-7 text-(--color-accent)" />
          Agent usage
        </h1>
        <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-(--color-ink-soft)">
          Local consumption this app has driven through each provider CLI, against the
          per-window call caps in <code className={CODE}>.env</code>{" "}
          (<code className={CODE}>AGENT_LIMIT_*</code>).
          {data?.now && (
            <span className="ml-2 font-mono text-[0.7rem] text-(--color-ink-muted)">
              · synced {formatRelative(data.now)}
            </span>
          )}
        </p>
        <p className="mt-1.5 font-mono text-[0.68rem] text-(--color-ink-muted)">
          Input = tokens read · Cached = reused input (cheaper) · Output = tokens generated
        </p>

        {error && (
          <div className="mt-7 rounded-(--radius-md) border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)] px-3 py-2 text-sm text-(--color-error)">
            Failed to load stats: {(error as Error).message}
          </div>
        )}

        {isLoading && (
          <div className="mt-7 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
              <Skeleton key={i} className="h-[190px] w-full rounded-3xl" />
            ))}
          </div>
        )}

        {data && (
          <>
            <div className="mt-6 inline-flex gap-1 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-1">
              {windows.map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => setPicked(w)}
                  className={cn(
                    "rounded-(--radius-sm) px-3.5 py-1.5 font-mono text-[0.7rem] uppercase tracking-[0.12em] transition-colors",
                    w === selectedWindow
                      ? "bg-(--color-ink) font-semibold text-(--color-canvas)"
                      : "text-(--color-ink-muted) hover:text-(--color-ink)",
                  )}
                >
                  {WINDOW_LABELS[w] ?? w}
                </button>
              ))}
            </div>

            <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
              {ids.map((id, i) => (
                <ProviderTile
                  key={id}
                  index={i}
                  providerId={id}
                  label={PROVIDER_LABELS[id] ?? id}
                  stats={data.providers[id]?.[selectedWindow]}
                  open={isOpen(id)}
                  onToggle={() => toggle(id)}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ProviderTile({
  index,
  providerId,
  label,
  stats,
  open,
  onToggle,
}: {
  index: number;
  providerId: string;
  label: string;
  stats: ProviderStatsWindow | undefined;
  open: boolean;
  onToggle: () => void;
}) {
  const [from, to] = accentOf(providerId);
  const calls = stats?.calls ?? 0;
  const cap = stats?.limit_calls_per_window ?? null;
  const pct = stats?.pct_of_limit ?? null;
  const models = stats?.models ?? [];
  const tokensMissing =
    calls > 0 &&
    (stats?.prompt_tokens ?? 0) === 0 &&
    (stats?.output_tokens ?? 0) === 0 &&
    (stats?.cached_tokens ?? 0) === 0;
  const showRing = cap !== null && calls > 0;
  const canExpand = models.length > 0;
  const dim = calls === 0;
  const expanded = open && canExpand;

  return (
    <div
      className={cn(
        "animate-tile-rise relative overflow-hidden rounded-3xl border border-white/10 p-5 shadow-2xl backdrop-blur-xl transition-transform hover:-translate-y-0.5",
        expanded && "sm:col-span-2",
        dim && "opacity-60",
      )}
      style={{ animationDelay: `${index * 60}ms`, background: "oklch(0.24 0.012 265 / 50%)" }}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -right-14 -top-16 size-48 rounded-full opacity-50 blur-3xl"
        style={{ background: `radial-gradient(circle, ${from}, ${to})` }}
      />

      <div className={cn("relative", expanded && "sm:grid sm:grid-cols-[300px_1fr] sm:gap-7")}>
        <div>
          <div className="flex items-center gap-2.5">
            <span
              className="grid size-7 place-items-center rounded-lg text-[0.7rem] font-bold text-[oklch(0.16_0.02_270)]"
              style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
            >
              {label.charAt(0)}
            </span>
            <span className="text-base font-semibold text-(--color-ink)">{label}</span>
            {canExpand && (
              <button
                type="button"
                onClick={onToggle}
                className="ml-auto inline-flex items-center gap-1 rounded-lg border border-(--color-border) bg-(--color-elevated) px-2.5 py-1 font-mono text-[0.68rem] text-(--color-ink-soft) transition-colors hover:text-(--color-ink)"
              >
                {models.length} model{models.length > 1 ? "s" : ""}
                <ChevronDown className={cn("size-3 transition-transform", expanded && "rotate-180")} />
              </button>
            )}
          </div>

          <div className="mt-4 flex items-center gap-4">
            {showRing && (
              <ActivityRing pct={pct ?? 0} from={from} to={to} gradId={`ring-${providerId}`} />
            )}
            <div>
              <div className="font-mono text-3xl font-bold tabular-nums tracking-tight text-(--color-ink)">
                {calls}
                <span className="text-xl font-medium text-(--color-ink-muted)"> / {cap ?? "—"}</span>
              </div>
              <div className="mt-1 text-[0.7rem] font-medium text-(--color-ink-muted)">
                {dim
                  ? "no calls in this window"
                  : cap === null
                    ? "unmetered (no cap set)"
                    : "calls this window"}
              </div>
              {calls > 0 && (
                <div className="mt-2 font-mono text-[0.7rem] text-(--color-ink-soft)">
                  <span className="text-(--color-success)">{stats!.success_pct}% ok</span>
                  {" · "}
                  {formatDuration(stats!.duration_secs)}
                </div>
              )}
            </div>
          </div>

          {calls > 0 &&
            (tokensMissing ? (
              <p className="mt-4 text-[0.78rem] italic text-(--color-ink-muted)">
                Tokens not reported by this CLI — calls &amp; duration only.
              </p>
            ) : (
              <div className="mt-4 flex gap-2">
                <TokenPill label="Input" value={stats!.prompt_tokens} />
                <TokenPill label="Cached" value={stats!.cached_tokens} />
                <TokenPill label="Output" value={stats!.output_tokens} />
              </div>
            ))}
        </div>

        {expanded && (
          <div className="mt-5 sm:mt-0">
            <ModelTable models={models} tokensMissing={tokensMissing} />
          </div>
        )}
      </div>
    </div>
  );
}

function ActivityRing({
  pct,
  from,
  to,
  gradId,
}: {
  pct: number;
  from: string;
  to: string;
  gradId: string;
}) {
  const r = 34;
  const c = 2 * Math.PI * r;
  const off = c * (1 - Math.min(pct, 100) / 100);
  const stroke =
    pct > 100 ? "var(--color-error)" : pct >= 80 ? "var(--color-accent)" : `url(#${gradId})`;
  return (
    <div className="relative size-[88px] shrink-0">
      <svg
        width="88"
        height="88"
        viewBox="0 0 88 88"
        style={{ transform: "rotate(-90deg)", transformOrigin: "center" }}
      >
        <circle cx="44" cy="44" r={r} fill="none" stroke="oklch(1 0 0 / 8%)" strokeWidth="9" />
        <circle
          cx="44"
          cy="44"
          r={r}
          fill="none"
          stroke={stroke}
          strokeWidth="9"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={off}
        />
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={from} />
            <stop offset="1" stopColor={to} />
          </linearGradient>
        </defs>
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        <div>
          <div className="text-lg font-bold leading-none tabular-nums text-(--color-ink)">
            {Math.round(pct)}%
          </div>
          <div className="mt-1 font-mono text-[0.55rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            of cap
          </div>
        </div>
      </div>
    </div>
  );
}

function TokenPill({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex-1 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2">
      <div className="font-mono text-[0.6rem] uppercase tracking-[0.13em] text-(--color-ink-muted)">
        {label}
      </div>
      <div className="mt-1 font-mono text-sm font-semibold tabular-nums text-(--color-ink)">
        {formatNum(value)}
      </div>
    </div>
  );
}

function ModelTable({
  models,
  tokensMissing,
}: {
  models: ProviderModelStat[];
  tokensMissing: boolean;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-1">
      <div className="grid grid-cols-[1.4fr_0.8fr_0.8fr_0.8fr_0.8fr] gap-3 px-3 pb-2 pt-3 font-mono text-[0.58rem] uppercase tracking-[0.12em] text-(--color-ink-muted)">
        <span>Model</span>
        <span className="text-right">Input</span>
        <span className="text-right">Cached</span>
        <span className="text-right">Output</span>
        <span className="text-right">Calls</span>
      </div>
      {models.map((m) => (
        <div
          key={m.model_name}
          className="grid grid-cols-[1.4fr_0.8fr_0.8fr_0.8fr_0.8fr] items-center gap-3 border-t border-white/[0.06] px-3 py-2.5"
        >
          <span className="truncate font-mono text-[0.72rem] font-medium text-(--color-ink-soft)">
            {m.model_name}
          </span>
          <span className="text-right font-mono text-[0.72rem] tabular-nums text-(--color-ink-soft)">
            {tokensMissing ? "—" : formatNum(m.prompt_tokens)}
          </span>
          <span className="text-right font-mono text-[0.72rem] tabular-nums text-(--color-ink-soft)">
            {tokensMissing ? "—" : formatNum(m.cached_tokens)}
          </span>
          <span className="text-right font-mono text-[0.72rem] tabular-nums text-(--color-ink-soft)">
            {tokensMissing ? "—" : formatNum(m.output_tokens)}
          </span>
          <span className="text-right font-mono text-[0.72rem] tabular-nums text-(--color-ink-muted)">
            {m.calls} · {m.success_pct}%
          </span>
        </div>
      ))}
    </div>
  );
}

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatDuration(secs: number): string {
  if (secs >= 3600) return `${(secs / 3600).toFixed(1)}h`;
  if (secs >= 60) return `${Math.round(secs / 60)}m`;
  return `${secs.toFixed(1)}s`;
}

function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = Date.now() - t;
  const s = Math.max(0, Math.floor(diff / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(iso).toLocaleString();
}
```

- [ ] **Step 3: Typecheck**

Run (PowerShell tool): `Set-Location 'C:\Users\Recruiter\Desktop\Homework-Content-Generation-Automation\web'; npx tsc -p tsconfig.app.json --noEmit`
Expected: no output (clean).

- [ ] **Step 4: Build sanity (catches any bad Tailwind arbitrary value)**

Run (PowerShell tool): `Set-Location 'C:\Users\Recruiter\Desktop\Homework-Content-Generation-Automation\web'; npm run build`
Expected: build succeeds, writes `web/dist/`.

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/usage.tsx web/src/styles/globals.css
git commit -m "feat(web): Control Center usage page — glass tiles, activity rings, per-model hero"
```

---

## Finish

- [ ] Full suite: `.\.venv\Scripts\python.exe -m pytest tests/ -q` — expect green except the one pre-existing `test_notion_defaults_disabled` red.
- [ ] tsc clean + `npm run build` succeeds (done in Task 4).
- [ ] **Manual verify** (server restart needed to serve `models[]`): restart `uvicorn`, open `/usage` — confirm: all 5 providers as glass tiles incl. opencode; segmented window control switches the data; the busiest provider auto-expands as a full-width hero tile with its per-model table; activity rings show `%` and tone-shift to amber ≥80% / red >100%; Kimi shows the "tokens not reported" note; Input/Cached/Output pills + legend present; codex (no calls) / opencode (unmetered) tiles dim cleanly; hover-lift + staggered entrance work.
- [ ] `superpowers:finishing-a-development-branch` (user decides push; usually push to Nggaev-v2).
- [ ] Worklog entry in `docs/memory/MASTER_MEMORY.md` + INDEX row.
