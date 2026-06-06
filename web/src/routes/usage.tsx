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
        role="img"
        aria-label={`${Math.round(pct)}% of call cap`}
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
