import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { Worker } from "@/lib/types";
import { api } from "@/lib/api";
import { CARD, GHOST_BTN } from "@/lib/ui";
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
  const qc = useQueryClient();

  const drainMut = useMutation({
    mutationFn: (pcId: string) => api.drainWorker(pcId),
    onSuccess: () => {
      toast.success("Worker draining — will stop claiming after current jobs");
      qc.invalidateQueries({ queryKey: ["workers"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Drain failed"),
  });

  const undrainMut = useMutation({
    mutationFn: (pcId: string) => api.undrainWorker(pcId),
    onSuccess: () => {
      toast.success("Worker back online");
      qc.invalidateQueries({ queryKey: ["workers"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Undrain failed"),
  });

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
          {data.workers.map((w) => {
            const isDraining = w.status === "draining";
            const pendingDrain = drainMut.isPending && drainMut.variables === w.pc_id;
            const pendingUndrain = undrainMut.isPending && undrainMut.variables === w.pc_id;
            const isPending = pendingDrain || pendingUndrain;

            return (
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
                  {isDraining ? (
                    <span className="inline-flex items-center rounded border border-amber-500/30 bg-amber-500/[0.08] px-1.5 py-0.5 text-[0.7rem] font-medium text-amber-300">
                      draining
                    </span>
                  ) : (
                    <span className="truncate">{w.status}</span>
                  )}
                  <span className="shrink-0 font-mono text-white/40">
                    {ago(w.last_heartbeat)}
                  </span>
                </div>
                {w.online && (
                  <div className="flex justify-end">
                    {isDraining ? (
                      <button
                        className={cn(GHOST_BTN, "h-7 px-2 text-xs text-amber-300/80 hover:text-amber-200 disabled:opacity-50")}
                        disabled={isPending}
                        onClick={() => undrainMut.mutate(w.pc_id)}
                      >
                        {pendingUndrain ? "…" : "Undrain"}
                      </button>
                    ) : (
                      <button
                        className={cn(GHOST_BTN, "h-7 px-2 text-xs disabled:opacity-50")}
                        disabled={isPending}
                        onClick={() => drainMut.mutate(w.pc_id)}
                      >
                        {pendingDrain ? "…" : "Drain"}
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
