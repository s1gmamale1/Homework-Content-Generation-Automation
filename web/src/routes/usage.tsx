import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Database,
  Gauge,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Eyebrow } from "@/components/eyebrow";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type {
  AgentStats,
  ProviderModelStat,
  ProviderStatsWindow,
} from "@/lib/types";
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

const ACCENTS: Record<string, [string, string]> = {
  claude: ["#ff9466", "#ff5f7f"],
  gemini: ["#64a8ff", "#4ee8d5"],
  kimi: ["#c18cff", "#8268ff"],
  codex: ["#57e4a5", "#3bd6d0"],
  opencode: ["#f6d365", "#fda085"],
};

function accentOf(id: string): [string, string] {
  return ACCENTS[id] ?? ["oklch(0.72 0.03 270)", "oklch(0.58 0.04 270)"];
}

function orderedProviders(providers: Record<string, unknown>): string[] {
  const keys = Object.keys(providers);
  const known = PROVIDER_ORDER.filter((p) => keys.includes(p));
  const extra = keys.filter((p) => !PROVIDER_ORDER.includes(p)).sort();
  return [...known, ...extra];
}

const CODE =
  "rounded-(--radius-xs) border border-white/[0.08] bg-white/[0.07] px-1.5 py-0.5 font-mono text-[0.78rem] text-(--color-ink)";

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

  const ids = useMemo(
    () => (data ? orderedProviders(data.providers) : []),
    [data],
  );

  const heroId = useMemo(() => {
    if (!data) return null;

    let best: string | null = null;
    let max = 0;

    for (const id of ids) {
      const calls = data.providers[id]?.[selectedWindow]?.calls ?? 0;

      if (calls > max) {
        max = calls;
        best = id;
      }
    }

    return best;
  }, [data, ids, selectedWindow]);

  const summary = useMemo(() => {
    if (!data) return null;

    let calls = 0;
    let cap = 0;
    let hasCap = false;
    let weightedSuccess = 0;
    let duration = 0;
    let totalTokens = 0;

    for (const id of ids) {
      const stats = data.providers[id]?.[selectedWindow];
      if (!stats) continue;

      calls += stats.calls;
      weightedSuccess += stats.calls * stats.success_pct;
      duration += stats.duration_secs;
      totalTokens +=
        stats.prompt_tokens + stats.cached_tokens + stats.output_tokens;

      if (stats.limit_calls_per_window !== null) {
        hasCap = true;
        cap += stats.limit_calls_per_window;
      }
    }

    return {
      calls,
      cap: hasCap ? cap : null,
      avgSuccess: calls > 0 ? Math.round(weightedSuccess / calls) : null,
      duration,
      totalTokens,
    };
  }, [data, ids, selectedWindow]);

  const [overrides, setOverrides] = useState<Record<string, boolean>>({});

  const isOpen = (id: string) => overrides[id] ?? id === heroId;

  const toggle = (id: string) =>
    setOverrides((current) => ({
      ...current,
      [id]: !(current[id] ?? id === heroId),
    }));

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          background:
            "radial-gradient(70% 52% at 8% -8%, oklch(0.55 0.12 235 / 0.30), transparent 62%), radial-gradient(60% 50% at 92% 0%, oklch(0.58 0.14 310 / 0.22), transparent 62%), radial-gradient(70% 60% at 52% 110%, oklch(0.46 0.11 190 / 0.22), transparent 66%), linear-gradient(135deg, oklch(0.20 0.018 255), oklch(0.155 0.016 265) 48%, oklch(0.18 0.018 235))",
        }}
      />

      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0 opacity-[0.055] mix-blend-soft-light"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
          backgroundSize: "140px 140px",
        }}
      />

      <div className="relative z-10 space-y-6">
        <section className="relative overflow-hidden rounded-[2rem] border border-white/[0.09] bg-white/[0.055] p-5 shadow-[0_24px_80px_-45px_rgba(0,0,0,0.85)] backdrop-blur-2xl sm:p-7">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-white/30 to-transparent"
          />

          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <Eyebrow>Dashboard</Eyebrow>

              <h1 className="mt-3 flex items-center gap-3 text-3xl font-semibold tracking-tight text-(--color-ink) sm:text-4xl">
                <span className="grid size-11 place-items-center rounded-2xl border border-white/[0.1] bg-white/[0.07] shadow-inner shadow-white/[0.04]">
                  <Gauge className="size-6 text-(--color-accent)" />
                </span>
                Agent usage
              </h1>

              <p className="mt-3 max-w-[68ch] text-sm leading-6 text-(--color-ink-soft)">
                A clearer view of local provider CLI consumption, call caps from{" "}
                <code className={CODE}>.env</code>, and model-level token usage
                from <code className={CODE}>AGENT_LIMIT_*</code>.
              </p>

              <p className="mt-3 font-mono text-[0.72rem] uppercase tracking-[0.13em] text-(--color-ink-muted)">
                Input = tokens read · Cached = reused input · Output =
                generated tokens
              </p>
            </div>

            {data && (
              <div className="flex flex-col gap-3 lg:items-end">
                <div
                  className="inline-flex rounded-2xl border border-white/[0.1] bg-black/[0.16] p-1 shadow-inner shadow-black/20"
                  role="tablist"
                  aria-label="Usage time window"
                >
                  {windows.map((w) => (
                    <button
                      key={w}
                      type="button"
                      onClick={() => setPicked(w)}
                      className={cn(
                        "rounded-xl px-3.5 py-2 font-mono text-[0.68rem] uppercase tracking-[0.12em] transition-all",
                        w === selectedWindow
                          ? "bg-white text-[oklch(0.18_0.02_260)] shadow-[0_10px_24px_-14px_rgba(255,255,255,0.8)]"
                          : "text-(--color-ink-muted) hover:bg-white/[0.07] hover:text-(--color-ink)",
                      )}
                    >
                      {WINDOW_LABELS[w] ?? w}
                    </button>
                  ))}
                </div>

                {data.now && (
                  <span className="font-mono text-[0.7rem] text-(--color-ink-muted)">
                    Synced {formatRelative(data.now)}
                  </span>
                )}
              </div>
            )}
          </div>

          {summary && (
            <div className="mt-7 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <SummaryCard
                icon={<Activity className="size-4" />}
                label="Total calls"
                value={`${summary.calls}${
                  summary.cap !== null ? ` / ${summary.cap}` : ""
                }`}
                caption={
                  summary.cap !== null
                    ? "used from the selected cap"
                    : "no shared cap configured"
                }
              />
              <SummaryCard
                icon={<CheckCircle2 className="size-4" />}
                label="Success rate"
                value={
                  summary.avgSuccess === null ? "—" : `${summary.avgSuccess}%`
                }
                caption="weighted by provider calls"
              />
              <SummaryCard
                icon={<Clock3 className="size-4" />}
                label="Runtime"
                value={formatDuration(summary.duration)}
                caption="combined CLI duration"
              />
              <SummaryCard
                icon={<Database className="size-4" />}
                label="Tokens"
                value={formatNum(summary.totalTokens)}
                caption="input, cached and output"
              />
            </div>
          )}
        </section>

        {error && (
          <div className="rounded-2xl border border-[oklch(0.70_0.16_25_/_35%)] bg-[oklch(0.70_0.16_25_/_10%)] px-4 py-3 text-sm text-(--color-error) backdrop-blur-xl">
            Failed to load stats: {(error as Error).message}
          </div>
        )}

        {isLoading && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton
                // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
                key={i}
                className="h-[220px] w-full rounded-[1.75rem]"
              />
            ))}
          </div>
        )}

        {data && (
          <section className="space-y-4">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold tracking-tight text-(--color-ink)">
                  Provider overview
                </h2>
                <p className="text-sm text-(--color-ink-muted)">
                  Expand a provider to see the model breakdown and token split.
                </p>
              </div>

              <span className="font-mono text-[0.68rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                {ids.length} providers
              </span>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
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
          </section>
        )}
      </div>
    </div>
  );
}

function SummaryCard({
  icon,
  label,
  value,
  caption,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  caption: string;
}) {
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-black/[0.16] p-4 shadow-inner shadow-white/[0.025]">
      <div className="flex items-center gap-2 font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
        <span className="grid size-7 place-items-center rounded-lg bg-white/[0.07] text-(--color-accent)">
          {icon}
        </span>
        {label}
      </div>

      <div className="mt-3 font-mono text-2xl font-semibold tabular-nums tracking-tight text-(--color-ink)">
        {value}
      </div>

      <p className="mt-1 text-xs text-(--color-ink-muted)">{caption}</p>
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
        "animate-tile-rise group relative overflow-hidden rounded-[1.75rem] border border-white/[0.09] p-5 shadow-[0_22px_60px_-38px_rgba(0,0,0,0.95)] backdrop-blur-2xl transition-all duration-300 hover:-translate-y-0.5 hover:border-white/[0.16] hover:bg-white/[0.075]",
        expanded && "lg:col-span-2",
        dim && "opacity-70",
      )}
      style={{
        animationDelay: `${index * 70}ms`,
        background:
          "linear-gradient(145deg, rgba(255,255,255,0.075), rgba(255,255,255,0.035))",
      }}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-8 top-0 h-px opacity-80"
        style={{
          background: `linear-gradient(90deg, transparent, ${from}, ${to}, transparent)`,
        }}
      />

      <div
        aria-hidden
        className="pointer-events-none absolute -right-20 -top-24 size-56 rounded-full opacity-[0.18] blur-3xl transition-opacity group-hover:opacity-[0.26]"
        style={{ background: `radial-gradient(circle, ${from}, ${to})` }}
      />

      <div
        className={cn(
          "relative",
          expanded && "lg:grid lg:grid-cols-[330px_1fr] lg:gap-7",
        )}
      >
        <div>
          <div className="flex items-center gap-3">
            <span
              className="grid size-10 place-items-center rounded-2xl text-sm font-bold text-[oklch(0.16_0.02_270)] shadow-[0_12px_30px_-18px_rgba(255,255,255,0.7)]"
              style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
            >
              {label.charAt(0)}
            </span>

            <div className="min-w-0">
              <div className="truncate text-base font-semibold text-(--color-ink)">
                {label}
              </div>

              <div className="mt-0.5 font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                {dim ? "Idle" : cap === null ? "Unmetered" : "Tracked limit"}
              </div>
            </div>

            {canExpand && (
              <button
                type="button"
                onClick={onToggle}
                className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-white/[0.1] bg-white/[0.055] px-3 py-1.5 font-mono text-[0.68rem] text-(--color-ink-soft) transition-all hover:border-white/[0.18] hover:bg-white/[0.09] hover:text-(--color-ink)"
              >
                {models.length} model{models.length > 1 ? "s" : ""}
                <ChevronDown
                  className={cn(
                    "size-3 transition-transform",
                    expanded && "rotate-180",
                  )}
                />
              </button>
            )}
          </div>

          <div className="mt-6 flex flex-col gap-5 sm:flex-row sm:items-center">
            {showRing ? (
              <ActivityRing
                pct={pct ?? 0}
                from={from}
                to={to}
                gradId={`ring-${providerId}`}
              />
            ) : (
              <div className="grid size-[94px] shrink-0 place-items-center rounded-full border border-white/[0.08] bg-black/[0.14] text-center shadow-inner shadow-black/30">
                <div>
                  <div className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                    {calls > 0 ? "Live" : "Idle"}
                  </div>
                  <div className="mt-1 text-lg font-semibold text-(--color-ink)">
                    {calls}
                  </div>
                </div>
              </div>
            )}

            <div className="min-w-0 flex-1">
              <div className="font-mono text-4xl font-bold tabular-nums tracking-tight text-(--color-ink)">
                {calls}
                <span className="text-2xl font-medium text-(--color-ink-muted)">
                  {" "}
                  / {cap ?? "—"}
                </span>
              </div>

              <div className="mt-1 text-sm font-medium text-(--color-ink-muted)">
                {dim
                  ? "No calls in this window"
                  : cap === null
                    ? "No cap set for this provider"
                    : "Calls used in the selected window"}
              </div>

              {calls > 0 && (
                <div className="mt-3 inline-flex rounded-full border border-white/[0.08] bg-black/[0.13] px-3 py-1 font-mono text-[0.7rem] text-(--color-ink-soft)">
                  <span className="text-(--color-success)">
                    {stats!.success_pct}% ok
                  </span>
                  <span className="px-2 text-(--color-ink-faint)">/</span>
                  {formatDuration(stats!.duration_secs)} runtime
                </div>
              )}
            </div>
          </div>

          {calls > 0 &&
            (tokensMissing ? (
              <p className="mt-5 rounded-2xl border border-white/[0.08] bg-black/[0.12] px-3 py-2 text-[0.78rem] italic text-(--color-ink-muted)">
                Tokens are not reported by this CLI — calls and duration are
                shown only.
              </p>
            ) : (
              <div className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-3">
                <TokenPill label="Input" value={stats!.prompt_tokens} />
                <TokenPill label="Cached" value={stats!.cached_tokens} />
                <TokenPill label="Output" value={stats!.output_tokens} />
              </div>
            ))}
        </div>

        {expanded && (
          <div className="mt-6 lg:mt-0">
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
  const r = 36;
  const c = 2 * Math.PI * r;
  const off = c * (1 - Math.min(pct, 100) / 100);

  const stroke =
    pct > 100
      ? "var(--color-error)"
      : pct >= 80
        ? "var(--color-accent)"
        : `url(#${gradId})`;

  return (
    <div className="relative size-[94px] shrink-0 rounded-full bg-black/[0.14] shadow-inner shadow-black/30">
      <svg
        width="94"
        height="94"
        viewBox="0 0 94 94"
        role="img"
        aria-label={`${Math.round(pct)}% of call cap`}
        style={{ transform: "rotate(-90deg)", transformOrigin: "center" }}
      >
        <circle
          cx="47"
          cy="47"
          r={r}
          fill="none"
          stroke="oklch(1 0 0 / 10%)"
          strokeWidth="9"
        />

        <circle
          cx="47"
          cy="47"
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

          <div className="mt-1 font-mono text-[0.58rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            of cap
          </div>
        </div>
      </div>
    </div>
  );
}

function TokenPill({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-black/[0.14] px-3 py-3 shadow-inner shadow-white/[0.02]">
      <div className="font-mono text-[0.64rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
        {label}
      </div>

      <div className="mt-1 font-mono text-lg font-semibold tabular-nums text-(--color-ink)">
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
    <div className="overflow-hidden rounded-[1.35rem] border border-white/[0.09] bg-black/[0.13] shadow-inner shadow-white/[0.02]">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold text-(--color-ink)">
            Model breakdown
          </h3>
          <p className="mt-0.5 text-xs text-(--color-ink-muted)">
            Usage split for this provider.
          </p>
        </div>

        <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          {models.length} rows
        </span>
      </div>

      <div className="overflow-x-auto">
        <div className="min-w-[650px]">
          <div className="grid grid-cols-[1.55fr_0.8fr_0.8fr_0.8fr_0.8fr] gap-3 px-4 pb-2 pt-3 font-mono text-[0.62rem] uppercase tracking-[0.13em] text-(--color-ink-muted)">
            <span>Model</span>
            <span className="text-right">Input</span>
            <span className="text-right">Cached</span>
            <span className="text-right">Output</span>
            <span className="text-right">Calls</span>
          </div>

          {models.map((m) => (
            <div
              key={m.model_name}
              className="grid grid-cols-[1.55fr_0.8fr_0.8fr_0.8fr_0.8fr] items-center gap-3 border-t border-white/[0.06] px-4 py-3 transition-colors hover:bg-white/[0.04]"
            >
              <span className="truncate font-mono text-[0.8rem] font-medium text-(--color-ink-soft)">
                {m.model_name}
              </span>

              <span className="text-right font-mono text-[0.8rem] tabular-nums text-(--color-ink-soft)">
                {tokensMissing ? "—" : formatNum(m.prompt_tokens)}
              </span>

              <span className="text-right font-mono text-[0.8rem] tabular-nums text-(--color-ink-soft)">
                {tokensMissing ? "—" : formatNum(m.cached_tokens)}
              </span>

              <span className="text-right font-mono text-[0.8rem] tabular-nums text-(--color-ink-soft)">
                {tokensMissing ? "—" : formatNum(m.output_tokens)}
              </span>

              <span className="text-right font-mono text-[0.8rem] tabular-nums text-(--color-ink-muted)">
                {m.calls} · {m.success_pct}%
              </span>
            </div>
          ))}
        </div>
      </div>
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
