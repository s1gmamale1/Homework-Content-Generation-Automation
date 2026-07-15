import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { Worker } from "@/lib/types";
import { api } from "@/lib/api";
import { ONLINE_GREEN, ago } from "@/lib/host-liveness";
import { CARD, GHOST_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";

export function WorkerCards({
  data,
}: {
  data?: { workers: Worker[]; online: number; total: number; version_floor?: number | null };
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
          <span className="flex items-baseline gap-2">
            <span className="font-mono text-[0.72rem] text-white/45">
              online {data.online} / {data.total}
            </span>
            {data.version_floor != null && (
              <span className="font-mono text-[0.72rem] text-white/45">
                floor v{data.version_floor}
              </span>
            )}
          </span>
        )}
      </div>

      {!data || data.workers.length === 0 ? (
        <div className={cn(CARD, "text-sm text-white/50")}>
          No workers have checked in yet.
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {data.workers.map((w) => {
            const isDraining = w.status === "draining";
            const pendingDrain = drainMut.isPending && drainMut.variables === w.pc_id;
            const pendingUndrain = undrainMut.isPending && undrainMut.variables === w.pc_id;
            const isPending = pendingDrain || pendingUndrain;

            const ver = w.capabilities?.code_version ?? null;
            const sha = w.capabilities?.git_sha ?? null;
            const floor = data?.version_floor ?? null;
            const isStale = floor != null && (ver == null || ver < floor);

            return (
              <div
                key={w.pc_id}
                className={cn(
                  "flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-1.5",
                  isDraining && "border-amber-500/20 bg-amber-500/[0.04]",
                )}
              >
                {/* Status dot */}
                <span
                  aria-hidden
                  className={cn("size-2 shrink-0 rounded-full", !w.online && "bg-white/25")}
                  style={w.online ? { background: ONLINE_GREEN } : undefined}
                />

                {/* pc_id */}
                <span className="font-mono text-[0.75rem] font-medium text-white">
                  {w.pc_id}
                </span>

                {/* Vintage: code version + git sha */}
                {(ver != null || sha) && (
                  <span className="font-mono text-[0.68rem] text-white/40">
                    {ver != null ? `v${ver}` : "v?"}{sha ? ` @${sha}` : ""}
                  </span>
                )}

                {/* STALE chip — worker's code_version is below the fleet floor */}
                {isStale && (
                  <span className="rounded-md border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 font-mono text-[0.65rem] font-semibold text-red-300">
                    STALE{ver != null && floor != null ? ` ${ver} < ${floor}` : ""}
                  </span>
                )}

                {/* Status / draining tag */}
                {isDraining ? (
                  <span className="rounded border border-amber-500/30 bg-amber-500/[0.08] px-1 py-0.5 text-[0.62rem] font-medium text-amber-300">
                    draining
                  </span>
                ) : (
                  <span className="text-[0.7rem] text-white/40">{w.status}</span>
                )}

                {/* Age */}
                <span className="font-mono text-[0.62rem] text-white/30">
                  {ago(w.last_heartbeat)}
                </span>

                {/* Drain / Undrain button — only for online workers */}
                {w.online && (
                  isDraining ? (
                    <button
                      className={cn(GHOST_BTN, "h-6 px-1.5 text-[0.68rem] text-amber-300/80 hover:text-amber-200 disabled:opacity-50")}
                      disabled={isPending}
                      onClick={() => undrainMut.mutate(w.pc_id)}
                    >
                      {pendingUndrain ? "…" : "Undrain"}
                    </button>
                  ) : (
                    <button
                      className={cn(GHOST_BTN, "h-6 px-1.5 text-[0.68rem] disabled:opacity-50")}
                      disabled={isPending}
                      onClick={() => drainMut.mutate(w.pc_id)}
                    >
                      {pendingDrain ? "…" : "Drain"}
                    </button>
                  )
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
