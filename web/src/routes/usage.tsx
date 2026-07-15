import { useQuery } from "@tanstack/react-query";
import { Clock3, Database, PhoneCall, Rocket, ShieldCheck } from "lucide-react";
import { motion } from "motion/react";
import { useMemo, useState, type ReactNode } from "react";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { Skeleton } from "@/components/ui/skeleton";
import { fadeUpItem, staggerContainer } from "@/lib/motion";
import { api } from "@/lib/api";
import type {
  AgentStats,
  ProviderModelStat,
  ProviderStatsWindow,
  UsageSeries,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const PROVIDER_LABELS: Record<string, string> = {
  claude: "Claude",
  gemini: "Gemini",
  kimi: "Kimi",
  codex: "Codex",
  opencode: "Opencode",
  clodex: "Clodex",
};
const PROVIDER_ORDER = ["claude", "gemini", "clodex", "kimi", "codex", "opencode"];
const WINDOW_LABELS: Record<string, string> = {
  "1h": "Last hour",
  "24h": "Last 24h",
  "7d": "Last 7 days",
};

// Per-provider signature gradient [from, to].
const ACCENTS: Record<string, [string, string]> = {
  claude: ["#ff9466", "#ff5f7f"],
  gemini: ["#64a8ff", "#4ee8d5"],
  kimi: ["#c18cff", "#8268ff"],
  codex: ["#57e4a5", "#3bd6d0"],
  opencode: ["#f6d365", "#fda085"],
  clodex: ["#79d7ff", "#a276ff"],
};
function accentOf(id: string): [string, string] {
  return ACCENTS[id] ?? ["#8aa0c6", "#5f6f93"];
}

/** Pay-per-token API spend for a provider/window. cli rows are always $0, so
 *  this is the sum of `cost_usd` over the api transport entries. */
function apiCostOf(stats: ProviderStatsWindow | undefined): number {
  if (!stats?.transports) return 0;
  return stats.transports
    .filter((t) => t.auth_mode === "api")
    .reduce((sum, t) => sum + t.cost_usd, 0);
}

/** API call count for a provider/window (the billed transport). */
function apiCallsOf(stats: ProviderStatsWindow | undefined): number {
  if (!stats?.transports) return 0;
  return stats.transports
    .filter((t) => t.auth_mode === "api")
    .reduce((sum, t) => sum + t.calls, 0);
}

function formatUsd(n: number): string {
  if (n === 0) return "$0";
  if (n < 0.01) return "<$0.01";
  return `$${n.toFixed(2)}`;
}

function orderedProviders(providers: Record<string, unknown>): string[] {
  const keys = Object.keys(providers);
  const known = PROVIDER_ORDER.filter((p) => keys.includes(p));
  const extra = keys.filter((p) => !PROVIDER_ORDER.includes(p)).sort();
  return [...known, ...extra];
}

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

  const heroId = useMemo(() => {
    if (!data) return null;
    let best: string | null = null;
    let max = -1;
    for (const id of ids) {
      const c = data.providers[id]?.[selectedWindow]?.calls ?? 0;
      if (c > max) {
        max = c;
        best = id;
      }
    }
    return best;
  }, [data, ids, selectedWindow]);

  const [chosen, setChosen] = useState<string | null>(null);
  const activeId = chosen ?? heroId;

  const summary = useMemo(() => {
    if (!data) return null;
    let calls = 0;
    let cap = 0;
    let hasCap = false;
    let weightedSuccess = 0;
    let duration = 0;
    let totalTokens = 0;
    for (const id of ids) {
      const s = data.providers[id]?.[selectedWindow];
      if (!s) continue;
      calls += s.calls;
      weightedSuccess += s.calls * s.success_pct;
      duration += s.duration_secs;
      totalTokens += s.prompt_tokens + s.cached_tokens + s.output_tokens;
      if (s.limit_calls_per_window !== null) {
        hasCap = true;
        cap += s.limit_calls_per_window;
      }
    }
    return {
      calls,
      cap: hasCap ? cap : null,
      avgSuccess: calls > 0 ? Math.round((weightedSuccess / calls) * 10) / 10 : null,
      duration,
      totalTokens,
    };
  }, [data, ids, selectedWindow]);

  const series: UsageSeries | undefined = data?.series?.[selectedWindow];
  const activeStats = activeId ? data?.providers[activeId]?.[selectedWindow] : undefined;

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10 space-y-7">
        {/* Hero */}
        <header className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-start gap-4">
            <span className="grid size-14 shrink-0 place-items-center rounded-2xl border border-white/[0.12] bg-gradient-to-br from-[#7c5cff]/40 to-[#4d9bff]/30 shadow-[0_18px_40px_-18px_rgba(124,92,255,0.8)]">
              <Rocket className="size-7 text-white" />
            </span>
            <div>
              <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-[2.75rem]">
                Agent usage
              </h1>
              <p className="mt-2 max-w-[58ch] text-sm leading-6 text-white/55">
                Monitor local provider CLI usage, call caps, and token consumption
                across models in real time.
              </p>
            </div>
          </div>

          {data && (
            <div className="flex shrink-0 items-center gap-3">
              <div className="inline-flex rounded-2xl border border-white/[0.1] bg-white/[0.04] p-1">
                {windows.map((w) => (
                  <button
                    key={w}
                    type="button"
                    onClick={() => setPicked(w)}
                    className={cn(
                      "rounded-xl px-3.5 py-2 text-[0.8rem] font-medium transition-all",
                      w === selectedWindow
                        ? "bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] text-white shadow-[0_10px_26px_-12px_rgba(99,102,241,0.9)]"
                        : "text-white/55 hover:bg-white/[0.06] hover:text-white",
                    )}
                  >
                    {WINDOW_LABELS[w] ?? w}
                  </button>
                ))}
              </div>
              {data.now && (
                <span className="inline-flex items-center gap-2 rounded-2xl border border-white/[0.1] bg-white/[0.04] px-3.5 py-2 text-[0.78rem] text-white/65">
                  <span className="size-2 rounded-full bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.6)]" />
                  Synced {formatRelative(data.now)}
                </span>
              )}
            </div>
          )}
        </header>

        {error && (
          <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            Failed to load stats: {(error as Error).message}
          </div>
        )}

        {isLoading && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
              <Skeleton key={i} className="h-[132px] w-full rounded-2xl" />
            ))}
          </div>
        )}

        {/* Summary cards */}
        {summary && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <SummaryCard
              icon={<PhoneCall className="size-5" />}
              tint="#4d9bff"
              label="Total Calls"
              value={`${summary.calls}${summary.cap !== null ? ` / ${summary.cap}` : ""}`}
              caption={
                summary.cap !== null
                  ? `${pctOf(summary.calls, summary.cap)}% of total cap`
                  : "no shared cap configured"
              }
              series={series?.calls}
            />
            <SummaryCard
              icon={<ShieldCheck className="size-5" />}
              tint="#34d399"
              label="Success Rate"
              value={summary.avgSuccess === null ? "—" : `${summary.avgSuccess}%`}
              caption="Weighted by calls"
              series={series?.success_pct}
            />
            <SummaryCard
              icon={<Clock3 className="size-5" />}
              tint="#a78bfa"
              label="Total Runtime"
              value={formatDuration(summary.duration)}
              caption="Combined duration"
              series={series?.duration_secs}
            />
            <SummaryCard
              icon={<Database className="size-5" />}
              tint="#fb923c"
              label="Total Tokens"
              value={formatNum(summary.totalTokens)}
              caption="Input + Cached + Output"
              series={series?.tokens}
            />
          </div>
        )}

        {/* Provider overview */}
        {data && (
          <section className="space-y-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h2 className="text-xl font-semibold tracking-tight text-white">
                  Provider Overview
                </h2>
                <p className="text-sm text-white/50">
                  Expand a provider to see model-level breakdown and token details.
                </p>
              </div>
              <span className="inline-flex items-center gap-2 rounded-xl border border-white/[0.1] bg-white/[0.05] px-3.5 py-2 font-mono text-[0.72rem] uppercase tracking-[0.12em] text-white/65">
                All providers · {ids.length}
              </span>
            </div>

            <motion.div
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5"
              variants={staggerContainer}
              initial="hidden"
              animate="show"
            >
              {ids.map((id) => (
                <motion.div key={id} variants={fadeUpItem} className="h-full">
                  <ProviderCard
                    providerId={id}
                    label={PROVIDER_LABELS[id] ?? id}
                    stats={data.providers[id]?.[selectedWindow]}
                    active={id === activeId}
                    onSelect={() => setChosen(id)}
                  />
                </motion.div>
              ))}
            </motion.div>

            {activeId && activeStats && (
              <DetailPanel
                providerId={activeId}
                label={PROVIDER_LABELS[activeId] ?? activeId}
                stats={activeStats}
                windowKey={selectedWindow}
              />
            )}
          </section>
        )}

        <footer className="rounded-2xl border border-white/[0.08] bg-white/[0.03] px-4 py-3 text-center font-mono text-[0.72rem] text-white/45">
          Input = tokens read from the prompt · Cached = reused from context ·
          Output = tokens generated by the model
        </footer>
      </div>
    </div>
  );
}

/* ── Summary card with real sparkline ───────────────────────────────── */

function SummaryCard({
  icon,
  tint,
  label,
  value,
  caption,
  series,
}: {
  icon: ReactNode;
  tint: string;
  label: string;
  value: string;
  caption: string;
  series?: number[];
}) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/[0.09] bg-white/[0.045] p-4 shadow-[0_18px_50px_-34px_rgba(0,0,0,0.9)] backdrop-blur-xl">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <span
              className="grid size-9 place-items-center rounded-xl"
              style={{ background: `${tint}22`, color: tint, border: `1px solid ${tint}33` }}
            >
              {icon}
            </span>
            <span className="text-sm font-medium text-white/70">{label}</span>
          </div>
          <div className="mt-3 font-mono text-3xl font-bold tabular-nums tracking-tight text-white">
            {value}
          </div>
          <p className="mt-1 text-xs text-white/45">{caption}</p>
        </div>
        <Sparkline data={series} color={tint} />
      </div>
    </div>
  );
}

function Sparkline({ data, color }: { data?: number[]; color: string }) {
  const pts = data ?? [];
  if (pts.length < 2) return <div className="h-12 w-[120px] shrink-0" />;

  const w = 120;
  const h = 48;
  const max = Math.max(...pts, 1);
  const min = Math.min(...pts, 0);
  const span = max - min || 1;
  const step = w / (pts.length - 1);
  const coords = pts.map((v, i) => {
    const x = i * step;
    const y = h - 4 - ((v - min) / span) * (h - 10);
    return [x, y] as const;
  });
  const line = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const area = `${line} L${w} ${h} L0 ${h} Z`;
  const gid = `spk-${color.replace("#", "")}`;

  return (
    <svg width={w} height={h} className="shrink-0" aria-hidden>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.35" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ── Compact provider card ──────────────────────────────────────────── */

function ProviderCard({
  providerId,
  label,
  stats,
  active,
  onSelect,
}: {
  providerId: string;
  label: string;
  stats: ProviderStatsWindow | undefined;
  active: boolean;
  onSelect: () => void;
}) {
  const [from, to] = accentOf(providerId);
  const calls = stats?.calls ?? 0;
  const cap = stats?.limit_calls_per_window ?? null;
  const pct = stats?.pct_of_limit ?? null;
  const idle = calls === 0;
  const tokensMissing =
    calls > 0 &&
    (stats?.prompt_tokens ?? 0) === 0 &&
    (stats?.output_tokens ?? 0) === 0 &&
    (stats?.cached_tokens ?? 0) === 0;
  const showRing = cap !== null && calls > 0;
  const apiCost = apiCostOf(stats);
  const apiCalls = apiCallsOf(stats);

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "group relative h-full overflow-hidden rounded-2xl border p-4 text-left shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl transition-all hover:-translate-y-0.5",
        active
          ? "border-[#5b8dff]/70 bg-white/[0.07] shadow-[0_0_0_1px_rgba(91,141,255,0.35),0_22px_55px_-32px_rgba(91,141,255,0.7)]"
          : "border-white/[0.09] bg-white/[0.04] hover:border-white/[0.16] hover:bg-white/[0.06]",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className="grid size-8 shrink-0 place-items-center rounded-xl text-xs font-bold text-[#16131f]"
            style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
          >
            {label.charAt(0)}
          </span>
          <span className="truncate text-sm font-semibold text-white">{label}</span>
        </div>
        <span
          className={cn(
            "shrink-0 rounded-md px-2 py-0.5 text-[0.62rem] font-semibold uppercase tracking-wide",
            idle
              ? "bg-white/[0.07] text-white/40"
              : "bg-emerald-400/15 text-emerald-300",
          )}
        >
          {idle ? "Idle" : "Active"}
        </span>
      </div>

      <div className="mt-4 flex items-center gap-3">
        {showRing ? (
          <Ring pct={pct ?? 0} from={from} to={to} size={84} calls={calls} cap={cap} />
        ) : (
          <div className="grid size-20 shrink-0 place-items-center rounded-full border border-white/[0.08] bg-black/20 text-center">
            <span className="font-mono text-base font-bold text-white">{calls}</span>
          </div>
        )}
        <div className="min-w-0 flex-1 space-y-1 font-mono text-[0.72rem]">
          {tokensMissing ? (
            <span className="italic text-white/40">tokens not reported</span>
          ) : (
            <>
              <Row label="Input" value={formatNum(stats?.prompt_tokens ?? 0)} muted={idle} />
              <Row label="Cached" value={formatNum(stats?.cached_tokens ?? 0)} muted={idle} />
              <Row label="Output" value={formatNum(stats?.output_tokens ?? 0)} muted={idle} />
            </>
          )}
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between gap-2 border-t border-white/[0.07] pt-2.5 text-[0.72rem]">
        {idle ? (
          <span className="text-white/40">No activity</span>
        ) : (
          <span className="text-white/55">
            <span className="text-emerald-300">{stats!.success_pct}% success</span>
            <span className="px-1.5 text-white/25">·</span>
            {formatDuration(stats!.duration_secs)}
          </span>
        )}
        {apiCalls > 0 && (
          <span
            className="shrink-0 rounded-md border border-amber-400/30 bg-amber-400/15 px-1.5 py-0.5 font-mono text-[0.66rem] font-semibold text-amber-300"
            title={`${apiCalls} API call${apiCalls === 1 ? "" : "s"} (pay-per-token)`}
          >
            {formatUsd(apiCost)}
          </span>
        )}
      </div>
    </button>
  );
}

function Row({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-white/45">{label}</span>
      <span className={cn("tabular-nums", muted ? "text-white/40" : "text-white/85")}>{value}</span>
    </div>
  );
}

/* ── Expanded detail panel ──────────────────────────────────────────── */

function DetailPanel({
  providerId,
  label,
  stats,
  windowKey,
}: {
  providerId: string;
  label: string;
  stats: ProviderStatsWindow;
  windowKey: string;
}) {
  const [from, to] = accentOf(providerId);
  const cap = stats.limit_calls_per_window;
  const pct = stats.pct_of_limit ?? 0;
  const models = stats.models ?? [];
  const tokensMissing =
    stats.calls > 0 &&
    stats.prompt_tokens === 0 &&
    stats.output_tokens === 0 &&
    stats.cached_tokens === 0;

  return (
    <div className="overflow-hidden rounded-[1.5rem] border border-white/[0.1] bg-white/[0.04] shadow-[0_28px_80px_-50px_rgba(0,0,0,0.95)] backdrop-blur-2xl">
      <div className="grid grid-cols-1 gap-6 p-5 lg:grid-cols-[340px_1fr] sm:p-6">
        {/* Left: ring + detail list */}
        <div>
          <div className="flex items-center gap-3">
            <span
              className="grid size-10 place-items-center rounded-2xl text-sm font-bold text-[#16131f]"
              style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
            >
              {label.charAt(0)}
            </span>
            <span className="text-lg font-semibold text-white">{label}</span>
            <span className="ml-1 rounded-md bg-emerald-400/15 px-2 py-0.5 text-[0.62rem] font-semibold uppercase tracking-wide text-emerald-300">
              {stats.calls > 0 ? "Active" : "Idle"}
            </span>
          </div>

          <div className="mt-5 flex items-center gap-5">
            <Ring
              pct={cap !== null ? pct : 0}
              from={from}
              to={to}
              size={132}
              calls={stats.calls}
              cap={cap}
              big
            />
            <dl className="min-w-0 flex-1 space-y-2.5 text-sm">
              <DetailRow term="Calls" value={`${stats.calls}${cap !== null ? ` / ${cap}` : ""}`} />
              <div>
                <div className="flex items-center justify-between">
                  <dt className="text-white/50">Success</dt>
                  <dd className="font-mono text-white">{stats.success_pct}%</dd>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full rounded-full bg-emerald-400"
                    style={{ width: `${Math.min(stats.success_pct, 100)}%` }}
                  />
                </div>
              </div>
              <DetailRow term="Runtime" value={formatDuration(stats.duration_secs)} />
              <DetailRow
                term="Limit"
                value={cap !== null ? `${cap} / ${windowKey}` : "unmetered"}
              />
              <DetailRow
                term="API spend"
                value={`${formatUsd(apiCostOf(stats))}${
                  apiCallsOf(stats) > 0 ? ` · ${apiCallsOf(stats)} api calls` : ""
                }`}
              />
            </dl>
          </div>
        </div>

        {/* Right: model breakdown */}
        <div className="min-w-0">
          <div className="mb-3 flex items-center gap-2">
            <h3 className="text-base font-semibold text-white">Model Breakdown</h3>
            <span className="rounded-md bg-white/[0.07] px-2 py-0.5 font-mono text-[0.64rem] uppercase tracking-[0.12em] text-white/55">
              {models.length} model{models.length === 1 ? "" : "s"}
            </span>
          </div>
          {models.length === 0 ? (
            <p className="rounded-xl border border-white/[0.08] bg-black/20 px-4 py-6 text-center text-sm text-white/45">
              No model activity in this window.
            </p>
          ) : (
            <ModelTable models={models} tokensMissing={tokensMissing} />
          )}
        </div>
      </div>
    </div>
  );
}

function DetailRow({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-white/50">{term}</dt>
      <dd className="font-mono text-white">{value}</dd>
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
    <div className="overflow-x-auto rounded-xl border border-white/[0.08] bg-black/20">
      <div className="min-w-[640px]">
        <div className="grid grid-cols-[1.6fr_0.9fr_0.9fr_0.9fr_0.6fr_1.1fr] gap-3 border-b border-white/[0.07] px-4 py-2.5 font-mono text-[0.62rem] uppercase tracking-[0.1em] text-white/45">
          <span>Model</span>
          <span className="text-right">Input</span>
          <span className="text-right">Cached</span>
          <span className="text-right">Output</span>
          <span className="text-right">Calls</span>
          <span>Success</span>
        </div>
        {models.map((m) => (
          <div
            key={m.model_name}
            className="grid grid-cols-[1.6fr_0.9fr_0.9fr_0.9fr_0.6fr_1.1fr] items-center gap-3 border-t border-white/[0.05] px-4 py-2.5 transition-colors hover:bg-white/[0.03]"
          >
            <span className="truncate font-mono text-[0.8rem] text-white/85">{m.model_name}</span>
            <span className="text-right font-mono text-[0.8rem] tabular-nums text-white/70">
              {tokensMissing ? "—" : formatNum(m.prompt_tokens)}
            </span>
            <span className="text-right font-mono text-[0.8rem] tabular-nums text-white/70">
              {tokensMissing ? "—" : formatNum(m.cached_tokens)}
            </span>
            <span className="text-right font-mono text-[0.8rem] tabular-nums text-white/70">
              {tokensMissing ? "—" : formatNum(m.output_tokens)}
            </span>
            <span className="text-right font-mono text-[0.8rem] tabular-nums text-white/70">
              {m.calls}
            </span>
            <span className="flex items-center gap-2">
              <span className="font-mono text-[0.76rem] tabular-nums text-white/70">
                {m.success_pct}%
              </span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
                <span
                  className="block h-full rounded-full bg-emerald-400"
                  style={{ width: `${Math.min(m.success_pct, 100)}%` }}
                />
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Activity ring ──────────────────────────────────────────────────── */

function Ring({
  pct,
  from,
  to,
  size,
  calls,
  cap,
  big,
}: {
  pct: number;
  from: string;
  to: string;
  size: number;
  calls: number;
  cap: number | null;
  big?: boolean;
}) {
  const stroke = big ? 11 : 8;
  const r = (size - stroke) / 2 - 1;
  const c = 2 * Math.PI * r;
  const off = c * (1 - Math.min(pct, 100) / 100);
  const center = size / 2;
  const gid = `ring-${from.replace("#", "")}-${size}`;
  const lineStroke = pct > 100 ? "#fb7185" : pct >= 80 ? "#fbbf24" : `url(#${gid})`;

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={cap !== null ? `${Math.round(pct)}% of call cap` : `${calls} calls`}
        style={{ transform: "rotate(-90deg)", transformOrigin: "center" }}
      >
        <circle cx={center} cy={center} r={r} fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth={stroke} />
        <circle
          cx={center}
          cy={center}
          r={r}
          fill="none"
          stroke={lineStroke}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={off}
        />
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={from} />
            <stop offset="1" stopColor={to} />
          </linearGradient>
        </defs>
      </svg>
      <div className="absolute inset-0 grid place-items-center px-1 text-center">
        <div>
          <div className={cn("font-bold leading-none tabular-nums text-white", big ? "text-3xl" : "text-base")}>
            {Math.round(pct)}%
          </div>
          <div
            className={cn(
              "mt-1 whitespace-nowrap font-mono leading-none tabular-nums text-white/50",
              big ? "text-[0.66rem] uppercase tracking-[0.1em]" : "text-[0.5rem]",
            )}
          >
            {cap !== null ? `${calls}/${cap}` : `${calls} calls`}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── helpers ────────────────────────────────────────────────────────── */

function pctOf(n: number, d: number): number {
  return d > 0 ? Math.round((100 * n) / d * 10) / 10 : 0;
}

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatDuration(secs: number): string {
  if (secs >= 3600) {
    const h = Math.floor(secs / 3600);
    const m = Math.round((secs % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
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
