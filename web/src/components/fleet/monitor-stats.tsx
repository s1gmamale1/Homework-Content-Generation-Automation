import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Server,
} from "lucide-react";
import type { ReactNode } from "react";
import type { BatchSummary } from "@/lib/types";
import type { StatusFilter } from "@/lib/monitor-filters";
import { CARD } from "@/lib/ui";
import { cn } from "@/lib/utils";

const GREEN = "oklch(0.78 0.10 145)";
const BLUE = "#4d8dff";
const RED = "oklch(0.70 0.16 25)";

/** Sum one rollup key across every batch. */
function sumKey(batches: BatchSummary[], key: string): number {
  return batches.reduce((acc, b) => acc + (b.rollup[key as never] ?? 0), 0);
}

function StatTile({
  icon,
  label,
  value,
  sub,
  accent,
  alert,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  value: string | number;
  sub?: string;
  accent: string;
  alert?: boolean;
  onClick?: () => void;
}) {
  const inner = (
    <>
      {/* faint accent wash in the corner */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-6 -top-6 size-20 rounded-full opacity-25 blur-2xl"
        style={{ background: accent }}
      />
      <div className="flex items-center gap-2 text-[0.72rem] font-medium uppercase tracking-wide text-white/45">
        <span style={{ color: accent }}>{icon}</span>
        {label}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span
          className="text-3xl font-semibold tabular-nums text-white"
          style={alert ? { color: accent } : undefined}
        >
          {value}
        </span>
        {sub && (
          <span className="font-mono text-xs text-white/40">{sub}</span>
        )}
      </div>
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          CARD,
          "relative overflow-hidden p-4 text-left cursor-pointer transition-colors hover:bg-white/[0.07] w-full",
          alert && "ring-1 ring-[oklch(0.70_0.16_25_/_0.45)]",
        )}
      >
        {inner}
      </button>
    );
  }

  return (
    <div
      className={cn(
        CARD,
        "relative overflow-hidden p-4",
        alert && "ring-1 ring-[oklch(0.70_0.16_25_/_0.45)]",
      )}
    >
      {inner}
    </div>
  );
}

export function MonitorStats({
  batches,
  workers,
  onFilter,
}: {
  batches?: BatchSummary[];
  workers?: { online: number; total: number };
  onFilter?: (f: StatusFilter) => void;
}) {
  const bs = batches ?? [];

  const inProgress =
    sumKey(bs, "running") + sumKey(bs, "pending") + sumKey(bs, "cancelling");
  const done = sumKey(bs, "done");
  const failed = sumKey(bs, "failed");
  const total = bs.reduce(
    (acc, b) =>
      acc + Object.values(b.rollup).reduce((a, n) => a + (n ?? 0), 0),
    0,
  );
  const pct = total ? Math.round((done / total) * 100) : 0;

  const online = workers?.online ?? 0;
  const totalWorkers = workers?.total ?? 0;
  const allUp = totalWorkers > 0 && online === totalWorkers;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatTile
        icon={<Activity className="size-4" />}
        label="In progress"
        value={inProgress}
        sub={inProgress ? "lessons running" : "idle"}
        accent={BLUE}
        onClick={onFilter ? () => onFilter("running") : undefined}
      />
      <StatTile
        icon={<CheckCircle2 className="size-4" />}
        label="Completed"
        value={done}
        sub={total ? `${pct}% of ${total}` : "—"}
        accent={GREEN}
        onClick={onFilter ? () => onFilter("complete") : undefined}
      />
      <StatTile
        icon={<AlertTriangle className="size-4" />}
        label="Needs attention"
        value={failed}
        sub={failed ? "failed lessons" : "all clear"}
        accent={RED}
        alert={failed > 0}
        onClick={onFilter ? () => onFilter("attention") : undefined}
      />
      <StatTile
        icon={<Server className="size-4" />}
        label="Hosts"
        value={`${online} / ${totalWorkers}`}
        sub={allUp ? "all online" : online ? "partial" : "offline"}
        accent={allUp ? GREEN : BLUE}
      />
    </div>
  );
}
