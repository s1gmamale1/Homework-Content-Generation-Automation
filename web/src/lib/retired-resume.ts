/**
 * FE consent logic for the retired-model reactivation guard (backend:
 * app/services/job_reactivation.py). gemini-2.5 (pro/flash/flash-lite) was
 * retired 2026-08-03 — resuming a saved job pinned to one of those models
 * would reuse its pinned provider/model verbatim and call a dead model, so
 * the backend refuses (409) rather than silently resuming or recreating it.
 *
 * The batch-launch preview (`POST /jobs/batch` with `preview: true`) reports
 * FOUR disjoint counts over the sections a launch would target: `new`,
 * `resumable` (saved, live model), `retired` (saved, retired-stamped model —
 * NEVER also counted `resumable`), and `empty`. This module turns those
 * counts into the plain-language notice and the resume/discard routing
 * decision the relaunch dialog needs — pure, so it's testable without a DOM.
 */

export interface RelaunchPreviewCounts {
  new: number;
  resumable: number;
  retired: number;
  empty: number;
}

/**
 * Plain-language explanation of why saved lessons can't be resumed. `null`
 * when nothing retired is in play (no notice needed).
 */
export function retiredResumeNotice(counts: RelaunchPreviewCounts): string | null {
  if (counts.retired <= 0) return null;
  const noun = counts.retired === 1 ? "lesson" : "lessons";
  return (
    `${counts.retired} saved ${noun} use a retired model and CANNOT be resumed; ` +
    `choosing Discard & regenerate will regenerate all selected saved jobs.`
  );
}

/** Resume must be blocked outright the moment ANY selected saved lesson is
 *  retired-stamped — a partial resume (live lessons only) would silently
 *  leave the retired ones stuck, and the backend's relaunch-resume path
 *  refuses the whole request in that case anyway (409). */
export function resumeBlockedByRetired(counts: RelaunchPreviewCounts): boolean {
  return counts.retired > 0;
}

/** What the primary Launch button's preview click should do next. Pure
 *  routing decision — the caller supplies the actual confirm()/mutate() side
 *  effects for each outcome. */
export type RelaunchDecision =
  | { kind: "launch_straight" }
  | { kind: "offer_resume_or_discard" }
  | { kind: "retired_blocks_resume" };

export function decideRelaunch(counts: RelaunchPreviewCounts): RelaunchDecision {
  if (resumeBlockedByRetired(counts)) return { kind: "retired_blocks_resume" };
  if (counts.resumable > 0) return { kind: "offer_resume_or_discard" };
  return { kind: "launch_straight" };
}

/** Honest toast copy for a batch resume action (POST /jobs/batch/{id}/resume)
 *  — surfaces jobs skipped for being retired-stamped instead of silently
 *  reporting only the resumed count. */
export function formatResumeToast(resumed: number, skippedRetiredCount: number): string {
  if (skippedRetiredCount <= 0) return `Resuming ${resumed} lessons`;
  return `${resumed} resumed, ${skippedRetiredCount} skipped (retired model)`;
}
