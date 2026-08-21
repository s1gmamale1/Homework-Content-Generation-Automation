import { RegenerationProblem } from "@/components/regeneration/regeneration-wizard";
import {
  REGENERATION_APPROVE_NOTE,
  REGENERATION_LAUNCH_LABEL,
  REGENERATION_LAUNCH_SPEND_NOTE,
  REGENERATION_REJECT_CONFIRMATION,
  type RegenerationErrorView,
  api,
  regenerationApprovalGate,
  regenerationCampaignStatusLabel,
  regenerationJudgeCounts,
  regenerationReasonError,
  regenerationSolverStatusLabel,
  regenerationStrandedRelease,
} from "@/lib/api";
import { costComparison, formatUsd } from "@/lib/regeneration-state";
import type { RegenerationCampaignDetail, RegenerationTargetReport } from "@/lib/types";
import { CARD, GLASS_BTN, INPUT_GLASS, PRIMARY_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";
/**
 * The canary lifecycle: launch it, read it, and pass the ONE campaign-level
 * gate. There is no per-lesson publication approval anywhere in this flow.
 *
 * The approve/reject wording is not written here — it comes from
 * `regenerationApprovalGate`, which also decides whether a bulk step exists at
 * all. A campaign whose canary already covers every lesson must never see an
 * empty bulk-release gate, and that rule is unit-tested against the pure
 * function rather than against this markup.
 *
 * The complete revision is deliberately NOT re-rendered here: it is a normal
 * homework job, so the existing job-detail route and download endpoint show the
 * real thing, phase by phase, instead of a second half-faithful viewer.
 *
 * One more campaign-level action lives here, and it is NOT a gate: a campaign
 * whose release never landed can never move again, and the repair is to re-run
 * the same idempotent call that stalled. WHICH call depends on the phase —
 * `approve` after approval, the canary launch before it — because
 * `launch_canary` refuses an approved campaign outright, so one hardcoded
 * mutation would 409 half the failures it claims to fix. The button is
 * labelled as a retry of the step that stalled, renders in states well past
 * `awaiting_canary_approval`, and deliberately offers no second decision:
 * there is nothing new to review, so nothing to decline.
 */
import {
  CircleCheck,
  CircleDollarSign,
  Copy,
  Download,
  ExternalLink,
  RefreshCw,
  Rocket,
  TriangleAlert,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

export function CanaryReview({
  detail,
  onLaunchCanary,
  onApprove,
  onReject,
  launching,
  approving,
  rejecting,
  actionError,
}: {
  detail: RegenerationCampaignDetail;
  onLaunchCanary: () => void;
  onApprove: () => void;
  onReject: (reason: string) => void;
  launching: boolean;
  approving: boolean;
  rejecting: boolean;
  actionError: RegenerationErrorView | null;
}) {
  const [rejectReason, setRejectReason] = useState("");
  const [confirmingReject, setConfirmingReject] = useState(false);

  const gate = regenerationApprovalGate(detail);
  const estimated = detail.estimated_cost_high_usd ?? detail.estimated_cost_low_usd ?? 0;
  const cost = costComparison(estimated, detail.actual_cost.usd);
  const judge = regenerationJudgeCounts(detail.judge_status_counts);
  const solver = Object.entries(detail.solver_status_counts).filter(([, n]) => n > 0);
  const warnings = judge.filter((j) => j.signal.severity === "warning");

  const byTarget = new Map<string, RegenerationTargetReport>(detail.targets.map((t) => [t.id, t]));
  const reasonError = regenerationReasonError(rejectReason);
  const busy = launching || approving || rejecting;

  const canLaunchCanary = detail.status === "draft";
  const atGate = detail.status === "awaiting_canary_approval";
  // Non-null only for a launched, non-terminal campaign with lessons that never
  // got a revision job. Never overlaps `atGate`: after approval the derivation
  // cannot return `awaiting_canary_approval`, and before it a canary stuck at
  // `generating` holds the campaign at `canary_running`
  // (`regeneration_states.roll_up_campaign`).
  const stranded = regenerationStrandedRelease(detail);
  // The recovery re-runs whichever step stalled, so its pending state is that
  // mutation's, not always approve's.
  const strandedPending = stranded?.action === "approve" ? approving : launching;

  return (
    <div className="space-y-4">
      <section className={cn(CARD, "space-y-3")}>
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-white">Canary</h2>
            <p className="mt-1 text-xs leading-5 text-white/50">
              {regenerationCampaignStatusLabel(detail.status)} ·{" "}
              {detail.canary.length > 0
                ? `${detail.canary.length} canary revision${detail.canary.length === 1 ? "" : "s"}`
                : "no canary revision yet"}
            </p>
          </div>
          <span className="font-mono text-[0.68rem] text-white/45">
            canary {detail.canary_size} of {detail.target_count}
          </span>
        </header>

        <div className="flex flex-wrap items-center gap-4 text-xs text-white/60">
          <span className="inline-flex items-center gap-2">
            <CircleDollarSign className="size-4 text-white/40" />
            Estimated{" "}
            {detail.estimated_cost_low_usd != null && detail.estimated_cost_high_usd != null
              ? `${formatUsd(detail.estimated_cost_low_usd)} – ${formatUsd(detail.estimated_cost_high_usd)}`
              : "not recorded"}
          </span>
          <span
            className={cn(
              "inline-flex items-center gap-2",
              cost.direction === "over" ? "text-amber-200/90" : "text-white/60",
            )}
          >
            {cost.text}
          </span>
          <span className="font-mono text-[0.66rem] text-white/35">
            {detail.actual_cost.paid_call_count} paid call
            {detail.actual_cost.paid_call_count === 1 ? "" : "s"} ·{" "}
            {detail.actual_cost.zero_cost_marker_count} free copy marker
            {detail.actual_cost.zero_cost_marker_count === 1 ? "" : "s"} ·{" "}
            {detail.actual_cost.total_tokens.toLocaleString()} tokens
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs text-white/55">
          <span className="inline-flex items-center gap-1">
            <Copy className="size-3.5 text-white/40" />
            {detail.provenance.copied_phase_count} copied
          </span>
          <span className="inline-flex items-center gap-1">
            <RefreshCw className="size-3.5 text-white/40" />
            {detail.provenance.regenerated_phase_count} rebuilt
          </span>
          {solver.length > 0 && (
            <span className="text-[0.66rem] text-white/40">
              solver:{" "}
              {solver
                .map(([status, n]) => `${regenerationSolverStatusLabel(status)} ×${n}`)
                .join(", ")}
            </span>
          )}
        </div>

        {warnings.length > 0 && (
          <div className="space-y-2 rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-amber-100">
              <TriangleAlert className="size-4" />
              {warnings.reduce((n, w) => n + w.count, 0)} judge warning
              {warnings.reduce((n, w) => n + w.count, 0) === 1 ? "" : "s"} — none of these block
              publication
            </div>
            <ul className="space-y-1 text-xs leading-5 text-amber-100/80">
              {warnings.map((w) => (
                <li key={w.status}>
                  <span className="font-medium">
                    {w.signal.label} ×{w.count}.
                  </span>{" "}
                  {w.signal.explanation}
                </li>
              ))}
            </ul>
          </div>
        )}

        {canLaunchCanary && (
          <div className="space-y-2 rounded-xl border border-white/[0.09] bg-white/[0.03] p-3">
            <p className="max-w-[75ch] text-xs leading-5 text-white/55">
              {REGENERATION_LAUNCH_SPEND_NOTE}
            </p>
            <button type="button" className={PRIMARY_BTN} disabled={busy} onClick={onLaunchCanary}>
              <Rocket className="size-4" />
              {launching ? "Starting…" : REGENERATION_LAUNCH_LABEL}
            </button>
          </div>
        )}
      </section>

      {detail.canary.length > 0 && (
        <section className={cn(CARD, "space-y-2")}>
          <h3 className="text-sm font-semibold text-white">Complete canary revisions</h3>
          <p className="max-w-[75ch] text-xs leading-5 text-white/45">
            Each link opens the whole revision — every phase, copied and rebuilt — through the same
            job screen and download the rest of the app uses.
          </p>
          <ul className="space-y-1">
            {detail.canary.map((canary) => {
              const target = byTarget.get(canary.target_id);
              const canaryJudge = regenerationJudgeCounts(canary.judge_status_counts);
              const canarySolver = Object.entries(canary.solver_status_counts).filter(
                ([, n]) => n > 0,
              );
              return (
                <li
                  key={canary.target_id}
                  className="space-y-1 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm text-white/85">
                      {target?.lesson.section_title ?? canary.toc_entry_id}
                    </span>
                    <span className="font-mono text-[0.64rem] text-white/40">
                      {canary.output_language} · {canary.revision_job_status ?? canary.status}
                    </span>
                  </div>
                  <p className="max-w-[75ch] text-xs leading-5 text-white/50">{target?.reason}</p>
                  <div className="flex flex-wrap items-center gap-3 text-[0.68rem] text-white/50">
                    <span className="inline-flex items-center gap-1">
                      <Copy className="size-3" />
                      {canary.copied_phase_count} copied
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <RefreshCw className="size-3" />
                      {canary.regenerated_phase_count} rebuilt
                    </span>
                    {canaryJudge.map((j) => (
                      <span
                        key={j.status}
                        className={cn(
                          "inline-flex items-center gap-1 rounded-lg border px-2 py-0.5",
                          j.signal.severity === "warning"
                            ? "border-amber-300/30 bg-amber-300/[0.09] text-amber-100"
                            : "border-emerald-300/25 bg-emerald-300/[0.07] text-emerald-100/90",
                        )}
                        title={j.signal.explanation}
                      >
                        {j.signal.severity === "warning" ? (
                          <TriangleAlert className="size-3" />
                        ) : (
                          <CircleCheck className="size-3" />
                        )}
                        {j.signal.label} ×{j.count}
                      </span>
                    ))}
                    {canarySolver.map(([status, n]) => (
                      <span key={status} className="text-white/40">
                        solver {regenerationSolverStatusLabel(status)} ×{n}
                      </span>
                    ))}
                  </div>
                  {target?.phase_plan_error && (
                    <p className="max-w-[75ch] text-xs leading-5 text-rose-100/80">
                      This build cannot read the frozen phase plan for this lesson:{" "}
                      {target.phase_plan_error}
                    </p>
                  )}
                  {canary.revision_job_id && (
                    <div className="flex flex-wrap gap-3 text-[0.68rem]">
                      <Link
                        to={`/job/${canary.revision_job_id}`}
                        className="inline-flex items-center gap-1 text-sky-200/80 hover:text-sky-100"
                      >
                        <ExternalLink className="size-3" />
                        Open the full revision
                      </Link>
                      <a
                        href={api.jobDownloadUrl(canary.revision_job_id)}
                        className="inline-flex items-center gap-1 text-sky-200/80 hover:text-sky-100"
                      >
                        <Download className="size-3" />
                        Download it
                      </a>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {actionError && <RegenerationProblem view={actionError} />}

      {atGate && (
        <section className={cn(CARD, "space-y-3")}>
          <h3 className="text-sm font-semibold text-white">Campaign decision</h3>
          <p className="max-w-[75ch] text-xs leading-5 text-white/50">{gate.approveDetail}</p>
          <p className="max-w-[75ch] text-xs leading-5 text-white/50">
            {REGENERATION_APPROVE_NOTE}
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={PRIMARY_BTN}
              disabled={!gate.canApprove || busy}
              onClick={onApprove}
            >
              <CircleCheck className="size-4" />
              {approving ? "Approving…" : gate.approveLabel}
            </button>
            <button
              type="button"
              className={GLASS_BTN}
              disabled={busy}
              onClick={() => setConfirmingReject((v) => !v)}
            >
              {gate.rejectLabel}
            </button>
          </div>

          {confirmingReject && (
            <div className="space-y-2 rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3">
              <p
                id="regeneration-reject-explainer"
                className="max-w-[75ch] text-xs leading-5 text-amber-100/90"
              >
                {REGENERATION_REJECT_CONFIRMATION}
              </p>
              <p className="max-w-[75ch] text-xs leading-5 text-amber-100/70">
                {gate.rejectDetail}
              </p>
              <label
                htmlFor="regeneration-reject-reason"
                className="block text-xs font-medium text-amber-100/90"
              >
                Reason for declining this canary (stored as the audit record)
              </label>
              <input
                id="regeneration-reject-reason"
                type="text"
                aria-label="Reason for declining this canary"
                aria-describedby="regeneration-reject-explainer"
                value={rejectReason}
                placeholder="Why are you declining this canary?"
                onChange={(e) => setRejectReason(e.target.value)}
                className={cn(INPUT_GLASS, "w-full text-sm")}
              />
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className={GLASS_BTN}
                  disabled={reasonError !== null || busy}
                  onClick={() => onReject(rejectReason)}
                >
                  {rejecting ? "Rejecting…" : "Confirm reject"}
                </button>
                {reasonError && <span className="text-xs text-amber-100/80">{reasonError}</span>}
              </div>
            </div>
          )}
        </section>
      )}

      {stranded && (
        <section className={cn(CARD, "space-y-3 border-amber-300/25 bg-amber-300/[0.06]")}>
          <div className="flex items-start gap-2">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-200" />
            <div>
              <h3 className="text-sm font-semibold text-amber-100">{stranded.headline}</h3>
              <p className="mt-1 max-w-[75ch] text-xs leading-5 text-amber-100/80">
                {stranded.detail}
              </p>
            </div>
          </div>
          <ul className="space-y-0.5 pl-6 text-xs leading-5 text-amber-100/75 [list-style:disc]">
            {stranded.rows.map((row) => (
              <li key={row.targetId}>{row.text}</li>
            ))}
          </ul>
          <button
            type="button"
            className={PRIMARY_BTN}
            disabled={busy}
            onClick={stranded.action === "approve" ? onApprove : onLaunchCanary}
          >
            <RefreshCw className="size-4" />
            {strandedPending ? stranded.pendingLabel : stranded.actionLabel}
          </button>
        </section>
      )}
    </div>
  );
}
