# Usage Page Redesign + Per-Model Breakdown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign `/usage` to clearly show every provider's calls-vs-cap, token usage, and a per-model drill-down, across selectable time windows.

**Architecture:** Add one backend aggregation (`stats_by_provider_model`) and nest a `models[]` array under each provider/window in `/agent/stats`; rewrite the React page to a window-tabs + dynamic-provider-rows layout that expands into per-model sub-rows. Calls-per-window stays the only cap; tokens are informational with clearer Input/Cached/Output labels.

**Tech Stack:** FastAPI, SQLAlchemy (async), Pydantic, pytest, React/TS/Vite, Tailwind.

**Spec:** `docs/superpowers/specs/2026-06-06-usage-page-redesign-design.md`

**Standing rules:** Stage only the files each task lists. pytest via `.\.venv\Scripts\python.exe -m pytest`. tsc via the PowerShell tool (`Set-Location web; npx tsc -p tsconfig.app.json --noEmit`). No migration, no DB change. No generation impact → no CLI smoke.

---

## File Structure

- **Modify** `app/repositories/agent_usage.py` — add `stats_by_provider_model` (mirror of `stats_by_provider`, grouped by provider+model).
- **Modify** `app/api/v1/jobs.py` `get_agent_stats` — nest `models[]` per window (compute per-model `success_pct`); fix stale "four CLIs" comment.
- **Modify** `web/src/lib/types.ts` — `ProviderModelStat` + `models[]` on `ProviderStatsWindow`.
- **Rewrite** `web/src/routes/usage.tsx` — window tabs, dynamic providers, per-model expand, Input/Cached/Output labels + legend.
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

## Task 4: Rewrite `web/src/routes/usage.tsx`

**Files:**
- Rewrite: `web/src/routes/usage.tsx`

- [ ] **Step 1: Replace the entire file with the new layout**

```tsx
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Gauge } from "lucide-react";
import { useState } from "react";
import { Eyebrow } from "@/components/eyebrow";
import { Card } from "@/components/ui/card";
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

// Known providers in a stable order, then any unknown provider the API returns
// (so a future provider appears with no code change).
function orderedProviders(providers: Record<string, unknown>): string[] {
  const keys = Object.keys(providers);
  const known = PROVIDER_ORDER.filter((p) => keys.includes(p));
  const extra = keys.filter((p) => !PROVIDER_ORDER.includes(p)).sort();
  return [...known, ...extra];
}

const CODE = "rounded-(--radius-xs) bg-(--color-canvas) px-1 py-0.5 font-mono text-[0.78rem] text-(--color-ink)";

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

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <Eyebrow>Dashboard</Eyebrow>
      </div>

      <h1 className="mt-3 flex items-center gap-2.5 text-3xl font-semibold tracking-tight text-(--color-ink)">
        <Gauge className="size-7 text-(--color-accent)" />
        Agent usage
      </h1>
      <p className="mt-2 max-w-[64ch] text-sm leading-relaxed text-(--color-ink-soft)">
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
        <div className="mt-7 flex flex-col gap-2.5">
          {Array.from({ length: 5 }).map((_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
            <Skeleton key={i} className="h-[64px] w-full" />
          ))}
        </div>
      )}

      {data && (
        <>
          <div className="mt-6 flex gap-1.5">
            {windows.map((w) => (
              <button
                key={w}
                type="button"
                onClick={() => setPicked(w)}
                className={cn(
                  "rounded-(--radius-sm) px-3 py-1.5 font-mono text-[0.7rem] uppercase tracking-[0.12em] transition-colors",
                  w === selectedWindow
                    ? "bg-(--color-accent) text-[oklch(0.18_0.04_55)]"
                    : "text-(--color-ink-muted) hover:text-(--color-ink)",
                )}
              >
                {WINDOW_LABELS[w] ?? w}
              </button>
            ))}
          </div>

          <div className="mt-4 flex flex-col gap-2.5">
            {orderedProviders(data.providers).map((id) => (
              <ProviderRow
                key={id}
                providerId={id}
                label={PROVIDER_LABELS[id] ?? id}
                stats={data.providers[id]?.[selectedWindow]}
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}

function ProviderRow({
  providerId,
  label,
  stats,
}: {
  providerId: string;
  label: string;
  stats: ProviderStatsWindow | undefined;
}) {
  const [open, setOpen] = useState(false);

  const calls = stats?.calls ?? 0;
  const cap = stats?.limit_calls_per_window ?? null;
  const pct = stats?.pct_of_limit ?? null;
  const models = stats?.models ?? [];
  const overrun = pct !== null && pct > 100;
  const canExpand = models.length > 0;
  const tokensMissing =
    calls > 0 &&
    (stats?.prompt_tokens ?? 0) === 0 &&
    (stats?.output_tokens ?? 0) === 0 &&
    (stats?.cached_tokens ?? 0) === 0;

  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        disabled={!canExpand}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left disabled:cursor-default"
      >
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 text-(--color-ink-muted) transition-transform",
            !canExpand && "opacity-20",
            open && "rotate-180",
          )}
        />
        <span
          className="size-2 shrink-0 rounded-full"
          style={{ background: dotColor(providerId) }}
        />
        <span className="min-w-[88px] font-semibold text-(--color-ink)">{label}</span>

        <div className="min-w-0 flex-1 px-2">
          {calls === 0 ? (
            <span className="text-xs text-(--color-ink-muted)">no calls in this window</span>
          ) : cap === null ? (
            <span className="text-xs text-(--color-ink-muted)">unmetered (no cap set)</span>
          ) : (
            <ProgressBar pct={pct ?? 0} tone={pickTone(pct)} />
          )}
          {calls > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[0.68rem] text-(--color-ink-muted)">
              {tokensMissing ? (
                <span className="italic">tokens not reported by this CLI</span>
              ) : (
                <>
                  <span>Input <b className="text-(--color-ink-soft)">{formatNum(stats!.prompt_tokens)}</b></span>
                  <span>Cached <b className="text-(--color-ink-soft)">{formatNum(stats!.cached_tokens)}</b></span>
                  <span>Output <b className="text-(--color-ink-soft)">{formatNum(stats!.output_tokens)}</b></span>
                </>
              )}
              {canExpand && (
                <span>· {models.length} model{models.length > 1 ? "s" : ""}</span>
              )}
            </div>
          )}
        </div>

        <div className="shrink-0 text-right">
          {cap === null ? (
            <span className="font-mono text-sm text-(--color-ink-muted)">{calls} / —</span>
          ) : (
            <span className="font-mono text-sm">
              <span className={cn("text-(--color-ink)", overrun && "text-(--color-error)")}>{calls}</span>
              <span className="text-(--color-ink-muted)"> / {cap}</span>
              {pct !== null && (
                <span className={cn("ml-1.5", overrun ? "text-(--color-error)" : "text-(--color-ink-muted)")}>
                  ({pct}%)
                </span>
              )}
            </span>
          )}
          {calls > 0 && (
            <div className="mt-0.5 font-mono text-[0.66rem] text-(--color-ink-muted)">
              {stats!.duration_secs.toFixed(1)}s · {stats!.success_pct}% ok
            </div>
          )}
        </div>
      </button>

      {open && canExpand && (
        <ul className="bg-(--color-canvas)">
          {models.map((m) => (
            <ModelRow key={m.model_name} m={m} tokensMissing={tokensMissing} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function ModelRow({ m, tokensMissing }: { m: ProviderModelStat; tokensMissing: boolean }) {
  return (
    <li className="flex items-center justify-between gap-3 border-t border-(--color-border) px-4 py-2.5 pl-10">
      <span className="min-w-[160px] font-mono text-[0.72rem] font-medium text-(--color-ink-soft)">
        {m.model_name}
      </span>
      <span className="min-w-0 flex-1 px-3 font-mono text-[0.66rem] text-(--color-ink-muted)">
        {!tokensMissing && (
          <>
            Input <b className="text-(--color-ink-soft)">{formatNum(m.prompt_tokens)}</b> · Cached{" "}
            <b className="text-(--color-ink-soft)">{formatNum(m.cached_tokens)}</b> · Output{" "}
            <b className="text-(--color-ink-soft)">{formatNum(m.output_tokens)}</b> ·{" "}
          </>
        )}
        {m.duration_secs.toFixed(1)}s · {m.success_pct}% ok
      </span>
      <span className="shrink-0 font-mono text-[0.72rem] text-(--color-ink-muted)">
        {m.calls} call{m.calls > 1 ? "s" : ""}
      </span>
    </li>
  );
}

type Tone = "ok" | "warn" | "hot" | "over";

function pickTone(pct: number | null): Tone {
  if (pct === null) return "ok";
  if (pct > 100) return "over";
  if (pct >= 80) return "hot";
  if (pct >= 50) return "warn";
  return "ok";
}

function ProgressBar({ pct, tone }: { pct: number; tone: Tone }) {
  const width = Math.min(pct, 100);
  const fill =
    tone === "over" || tone === "hot"
      ? "bg-(--color-error)"
      : tone === "warn"
        ? "bg-(--color-accent)"
        : "bg-(--color-success)";
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className="h-1.5 w-full overflow-hidden rounded-full bg-(--color-canvas)"
    >
      <div
        className={cn("h-full rounded-full transition-all duration-500", fill)}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

function dotColor(id: string): string {
  switch (id) {
    case "claude":
      return "#e0603a";
    case "gemini":
      return "#4a90d9";
    case "kimi":
      return "#9b6cd9";
    default:
      return "var(--color-ink-muted)";
  }
}

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
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

- [ ] **Step 2: Typecheck**

Run (PowerShell tool): `Set-Location 'C:\Users\Recruiter\Desktop\Homework-Content-Generation-Automation\web'; npx tsc -p tsconfig.app.json --noEmit`
Expected: no output (clean).

- [ ] **Step 3: Build sanity**

Run (PowerShell tool): `Set-Location 'C:\Users\Recruiter\Desktop\Homework-Content-Generation-Automation\web'; npm run build`
Expected: build succeeds, writes `web/dist/`.

- [ ] **Step 4: Commit**

```bash
git add web/src/routes/usage.tsx
git commit -m "feat(web): redesign usage page — window tabs, dynamic providers, per-model drill-down"
```

---

## Finish

- [ ] Full suite: `.\.venv\Scripts\python.exe -m pytest tests/ -q` — expect green except the one pre-existing `test_notion_defaults_disabled` red.
- [ ] tsc clean + `npm run build` succeeds (done in Task 4).
- [ ] **Manual verify** (server restart needed to serve `models[]`): restart `uvicorn`, open `/usage` — confirm: all 5 providers listed incl. opencode; window tabs switch data; a provider with >1 model expands to per-model rows; Kimi shows the "tokens not reported" note; Input/Cached/Output legend present; codex (no calls) / unmetered states read cleanly.
- [ ] `superpowers:finishing-a-development-branch` (user decides push; usually push to Nggaev-v2).
- [ ] Worklog entry in `docs/memory/MASTER_MEMORY.md` + INDEX row.
