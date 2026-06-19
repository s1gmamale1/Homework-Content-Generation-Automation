import type { JobStatus } from "@/lib/types";

/** status -> background color (CSS color string). Shared by rollup bar + lesson chips. */
export const STATUS_COLOR: Record<string, string> = {
  done: "oklch(0.78 0.10 145)", // green
  running: "#4d8dff", // blue
  cancelling: "oklch(0.80 0.12 85)", // amber
  pending: "rgba(255,255,255,0.14)", // faint
  cancelled: "rgba(255,255,255,0.30)", // muted
  failed: "oklch(0.70 0.16 25)", // red
  not_started: "rgba(255,255,255,0.06)", // dim track — un-launched book lessons
};

export const STATUS_ORDER: (JobStatus | "not_started")[] = [
  "done",
  "running",
  "cancelling",
  "pending",
  "cancelled",
  "failed",
  "not_started",
];

export function colorFor(status: string): string {
  return STATUS_COLOR[status] ?? "rgba(255,255,255,0.14)";
}
