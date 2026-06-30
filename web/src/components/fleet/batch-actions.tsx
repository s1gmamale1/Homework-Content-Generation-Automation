import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, PauseCircle, PlayCircle, RotateCcw, XCircle } from "lucide-react";
import { toast } from "sonner";
import type { BatchSummary } from "@/lib/types";
import { api } from "@/lib/api";
import { batchActionFlags } from "@/lib/monitor-grouping";
import { FRAME_OFF, GHOST_BTN, PRESSABLE } from "@/lib/ui";
import { cn } from "@/lib/utils";

export function BatchActions({ batch }: { batch: BatchSummary }) {
  const qc = useQueryClient();
  const { canPause, isPaused, canCancel, canRetry } = batchActionFlags(batch);

  const pauseMut = useMutation({
    mutationFn: () => api.pauseBatch(batch.batch_id),
    onSuccess: () => {
      toast.success("Batch paused");
      qc.invalidateQueries({ queryKey: ["batches"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Action failed"),
  });

  const unpauseMut = useMutation({
    mutationFn: () => api.unpauseBatch(batch.batch_id),
    onSuccess: () => {
      toast.success("Batch unpaused");
      qc.invalidateQueries({ queryKey: ["batches"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Action failed"),
  });

  const cancelMut = useMutation({
    mutationFn: () => api.cancelBatch(batch.batch_id),
    onSuccess: () => {
      toast.success("Cancelling all pending + running lessons");
      qc.invalidateQueries({ queryKey: ["batches"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Action failed"),
  });

  const retryMut = useMutation({
    mutationFn: () => api.resumeBatch(batch.batch_id),
    onSuccess: () => {
      toast.success("Retrying failed lessons");
      qc.invalidateQueries({ queryKey: ["batches"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Action failed"),
  });

  if (!canPause && !isPaused && !canCancel && !canRetry) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {canPause && (
        <button
          type="button"
          className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "h-7 px-2 text-xs text-amber-300/80 hover:text-amber-200 border-amber-500/30 hover:border-amber-400/50 disabled:opacity-50")}
          disabled={pauseMut.isPending}
          title="Pause this batch (stops new jobs from starting)"
          onClick={() => pauseMut.mutate()}
        >
          {pauseMut.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <PauseCircle className="size-3.5" />
          )}
          Pause
        </button>
      )}

      {isPaused && (
        <button
          type="button"
          className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "h-7 px-2 text-xs text-amber-300/80 hover:text-amber-200 border-amber-500/30 hover:border-amber-400/50 disabled:opacity-50")}
          disabled={unpauseMut.isPending}
          title="Unpause this batch"
          onClick={() => unpauseMut.mutate()}
        >
          {unpauseMut.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <PlayCircle className="size-3.5" />
          )}
          Unpause
        </button>
      )}

      {canCancel && (
        <button
          type="button"
          className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "h-7 px-2 text-xs text-rose-300/80 hover:text-rose-200 border-rose-500/30 hover:border-rose-400/50 disabled:opacity-50")}
          disabled={cancelMut.isPending}
          title="Cancel all pending and running lessons in this batch"
          onClick={() => {
            if (window.confirm("Cancel all pending + running lessons in this batch?")) {
              cancelMut.mutate();
            }
          }}
        >
          {cancelMut.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <XCircle className="size-3.5" />
          )}
          Cancel all
        </button>
      )}

      {canRetry && (
        <button
          type="button"
          className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "h-7 px-2 text-xs disabled:opacity-50")}
          disabled={retryMut.isPending}
          title="Retry all failed/cancelled lessons"
          onClick={() => retryMut.mutate()}
        >
          {retryMut.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RotateCcw className="size-3.5" />
          )}
          Retry failed
        </button>
      )}
    </div>
  );
}
