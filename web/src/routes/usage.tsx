import { useQuery } from "@tanstack/react-query";
import { Activity, Gauge } from "lucide-react";
import { Eyebrow } from "@/components/eyebrow";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { AgentStats, ProviderStatsWindow } from "@/lib/types";
import { cn } from "@/lib/utils";

const PROVIDERS: Array<{ id: string; label: string }> = [
  { id: "claude", label: "Claude" },
  { id: "kimi", label: "Kimi" },
  { id: "codex", label: "Codex" },
  { id: "gemini", label: "Gemini" },
];

const WINDOW_LABELS: Record<string, string> = {
  "1h": "Last hour",
  "24h": "Last 24h",
  "7d": "Last 7 days",
};

export function UsagePage() {
  const { data, isLoading, error } = useQuery<AgentStats>({
    queryKey: ["agent-stats"],
    queryFn: () => api.getAgentStats(),
    // Auto-refresh once a minute. Counts only move when calls land in the
    // window — staleness is fine, snappiness isn't required here.
    refetchInterval: 60_000,
    refetchOnWindowFocus: false,
  });

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <Eyebrow>Dashboard</Eyebrow>
      </div>

      <h1 className="mt-3 flex items-center gap-2.5 text-3xl font-semibold tracking-tight text-(--color-ink)">
        <Gauge className="size-7 text-(--color-accent)" />
        Agent usage
      </h1>
      <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-(--color-ink-soft)">
        Local consumption this app has driven through each provider CLI.
        Caps are configurable in <code className="rounded-(--radius-xs) bg-(--color-canvas) px-1 py-0.5 font-mono text-[0.78rem] text-(--color-ink)">.env</code>{" "}
        (<code className="rounded-(--radius-xs) bg-(--color-canvas) px-1 py-0.5 font-mono text-[0.78rem] text-(--color-ink)">AGENT_LIMIT_*</code>).
        {data?.now && (
          <span className="ml-2 font-mono text-[0.7rem] text-(--color-ink-muted)">
            · synced {formatRelative(data.now)}
          </span>
        )}
      </p>

      {error && (
        <div className="mt-7 rounded-(--radius-md) border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)] px-3 py-2 text-sm text-(--color-error)">
          Failed to load stats: {(error as Error).message}
        </div>
      )}

      {isLoading && (
        <div className="mt-7 grid grid-cols-1 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
            <Skeleton key={i} className="h-[220px] w-full" />
          ))}
        </div>
      )}

      {data && (
        <div className="mt-7 grid grid-cols-1 gap-3">
          {PROVIDERS.map(({ id, label }) => (
            <ProviderCard
              key={id}
              providerLabel={label}
              windows={data.windows}
              perWindow={data.providers[id] ?? {}}
            />
          ))}
        </div>
      )}
    </>
  );
}

function ProviderCard({
  providerLabel,
  windows,
  perWindow,
}: {
  providerLabel: string;
  windows: string[];
  perWindow: Record<string, ProviderStatsWindow>;
}) {
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-(--color-border) px-4 py-3">
        <div className="flex items-center gap-2">
          <Activity className="size-4 text-(--color-ink-muted)" />
          <h2 className="text-base font-semibold tracking-tight text-(--color-ink)">
            {providerLabel}
          </h2>
        </div>
      </div>
      <ul className="flex flex-col">
        {windows.map((win, idx) => {
          const stats = perWindow[win];
          return (
            <li
              key={win}
              className={cn(
                "px-4 py-3",
                idx > 0 && "border-t border-(--color-border)",
              )}
            >
              <WindowRow window={win} stats={stats} />
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function WindowRow({
  window,
  stats,
}: {
  window: string;
  stats: ProviderStatsWindow | undefined;
}) {
  // Defensive: missing window in the response means the backend didn't
  // populate it for this provider — render an empty row that says so.
  if (!stats) {
    return (
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-[0.7rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          {WINDOW_LABELS[window] ?? window}
        </span>
        <span className="text-xs text-(--color-ink-muted)">no data</span>
      </div>
    );
  }

  const {
    calls,
    duration_secs,
    prompt_tokens,
    output_tokens,
    cached_tokens,
    success_pct,
    limit_calls_per_window,
    pct_of_limit,
  } = stats;

  const overrun = pct_of_limit !== null && pct_of_limit > 100;
  const tone = pickTone(pct_of_limit);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-[0.7rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          {WINDOW_LABELS[window] ?? window}
        </span>
        <span className="font-mono text-[0.78rem] text-(--color-ink-soft)">
          {limit_calls_per_window === null ? (
            <>
              <span className="text-(--color-ink)">{calls}</span>
              <span className="text-(--color-ink-muted)"> / —</span>
            </>
          ) : (
            <>
              <span className={cn("text-(--color-ink)", overrun && "text-(--color-error)")}>
                {calls}
              </span>
              <span className="text-(--color-ink-muted)"> / {limit_calls_per_window}</span>
              <span
                className={cn(
                  "ml-2",
                  overrun ? "text-(--color-error)" : "text-(--color-ink-muted)",
                )}
              >
                ({pct_of_limit}%)
              </span>
            </>
          )}
        </span>
      </div>

      {calls === 0 ? (
        <span className="text-xs text-(--color-ink-muted)">no calls</span>
      ) : pct_of_limit === null ? (
        <span className="text-xs text-(--color-ink-muted)">— unmetered</span>
      ) : (
        <ProgressBar pct={pct_of_limit} tone={tone} />
      )}

      {calls > 0 && (
        <p className="font-mono text-[0.68rem] leading-relaxed text-(--color-ink-muted)">
          {duration_secs.toFixed(1)}s · prompt: {formatNum(prompt_tokens)} · cached:{" "}
          {formatNum(cached_tokens)} · output: {formatNum(output_tokens)} · success:{" "}
          {success_pct}%
        </p>
      )}
    </div>
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
  // Visual width capped at 100% even when pct exceeds it. The numbers
  // above the bar (and the red tint) communicate the actual overrun.
  const width = Math.min(pct, 100);
  const fill =
    tone === "over"
      ? "bg-(--color-error)"
      : tone === "hot"
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
