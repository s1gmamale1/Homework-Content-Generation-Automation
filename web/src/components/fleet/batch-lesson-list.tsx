import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ArrowUpRight, Loader2 } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { FRAME_OFF, GHOST_BTN, PRESSABLE } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { colorFor } from "./status";

export function BatchLessonList({
  batchId,
  enabled,
  selectable = false,
  selected,
  onToggle,
}: {
  batchId: string;
  enabled: boolean;
  selectable?: boolean;
  selected?: Set<string>;
  onToggle?: (tocEntryId: string) => void;
}) {
  const jobs = useQuery({
    queryKey: ["batch-jobs", batchId],
    queryFn: () => api.batchJobs(batchId),
    refetchInterval: 3500,
    enabled,
  });
  const qc = useQueryClient();

  const cancel = useMutation({
    mutationFn: (jobId: string) => api.cancelJob(jobId),
    onSuccess: () => {
      toast.success("Cancel requested");
      qc.invalidateQueries({ queryKey: ["batch-jobs", batchId] });
      qc.invalidateQueries({ queryKey: ["batches"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Cancel failed"),
  });
  const retry = useMutation({
    mutationFn: (jobId: string) => api.retryJob(jobId),
    onSuccess: () => {
      toast.success("Retry queued");
      qc.invalidateQueries({ queryKey: ["batch-jobs", batchId] });
      qc.invalidateQueries({ queryKey: ["batches"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Retry failed"),
  });

  if (jobs.isLoading && enabled) {
    return (
      <div className="flex items-center gap-2 py-2 text-sm text-white/45">
        <Loader2 className="size-4 animate-spin" />
        Loading lessons…
      </div>
    );
  }

  const rows = jobs.data ?? [];
  if (rows.length === 0) {
    return (
      <div className="py-2 text-sm text-white/45">
        No lessons in this batch yet.
      </div>
    );
  }

  return (
    <ul className="divide-y divide-white/[0.06]">
      {rows.map((row) => {
        const launched = row.job_id !== null && row.status !== null;
        const canCancel =
          row.status === "pending" || row.status === "running";
        const canRetry = row.status === "failed";
        const isSelected = selected?.has(row.toc_entry_id) ?? false;

        return (
          <li
            key={row.toc_entry_id}
            className={cn(
              "flex items-center gap-3 py-2 text-sm",
              !launched && "opacity-45",
            )}
          >
            {selectable && (
              <input
                type="checkbox"
                checked={isSelected}
                onChange={() => onToggle?.(row.toc_entry_id)}
                className="size-4 shrink-0 accent-[#7c5cff]"
              />
            )}

            <span className="shrink-0 font-mono text-xs text-white/35">
              #{row.order_index}
            </span>
            <span className="min-w-0 flex-1 truncate text-white/80">
              {row.section_title}
            </span>

            {launched ? (
              <span
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[0.7rem] text-white/85"
                style={{ background: `${colorFor(row.status!)}` }}
              >
                <span className="font-medium">{row.status}</span>
                {(row.attempts ?? 0) > 1 && (
                  <span className="text-white/60">· try {row.attempts}</span>
                )}
              </span>
            ) : (
              <span className="shrink-0 rounded-full bg-white/[0.06] px-2 py-0.5 text-[0.7rem] text-white/50">
                {row.toc_class}
              </span>
            )}

            {!selectable && launched && (
              <div className="flex shrink-0 items-center gap-1">
                {canCancel && (
                  <button
                    type="button"
                    onClick={() => cancel.mutate(row.job_id!)}
                    disabled={cancel.isPending}
                    className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "px-2 py-1 text-xs")}
                  >
                    Cancel
                  </button>
                )}
                {canRetry && (
                  <button
                    type="button"
                    onClick={() => retry.mutate(row.job_id!)}
                    disabled={retry.isPending}
                    className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "px-2 py-1 text-xs")}
                  >
                    Retry
                  </button>
                )}
                <Link
                  to={`/job/${row.job_id}`}
                  className={cn(GHOST_BTN, PRESSABLE, FRAME_OFF, "px-2 py-1 text-xs")}
                >
                  Open
                  <ArrowUpRight className="size-3.5" />
                </Link>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
