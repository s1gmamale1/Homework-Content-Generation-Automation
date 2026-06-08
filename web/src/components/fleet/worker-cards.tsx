import type { Worker } from "@/lib/types";
import { CARD } from "@/lib/ui";
import { cn } from "@/lib/utils";

const ONLINE_GREEN = "oklch(0.78 0.10 145)";

function ago(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function WorkerCards({
  data,
}: {
  data?: { workers: Worker[]; online: number; total: number };
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold tracking-tight text-white">Workers</h2>
        {data && (
          <span className="font-mono text-[0.72rem] text-white/45">
            online {data.online} / {data.total}
          </span>
        )}
      </div>

      {!data || data.workers.length === 0 ? (
        <div className={cn(CARD, "text-sm text-white/50")}>
          No workers have checked in yet.
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {data.workers.map((w) => (
            <div key={w.pc_id} className={cn(CARD, "space-y-2")}>
              <div className="flex items-center gap-2">
                <span
                  aria-hidden
                  className={cn(
                    "size-2 shrink-0 rounded-full",
                    !w.online && "bg-white/25",
                  )}
                  style={w.online ? { background: ONLINE_GREEN } : undefined}
                />
                <span className="truncate font-mono text-sm font-medium text-white">
                  {w.pc_id}
                </span>
              </div>
              <div className="flex items-baseline justify-between gap-2 text-xs text-white/50">
                <span className="truncate">{w.status}</span>
                <span className="shrink-0 font-mono text-white/40">
                  {ago(w.last_heartbeat)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
