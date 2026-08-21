import { RegenerationProblem } from "@/components/regeneration/regeneration-wizard";
import {
  REGENERATION_CANCEL_CONFIRMATION,
  type RegenerationActionKind,
  type RegenerationErrorView,
  api,
  regenerationBucketViews,
  regenerationKeyedLines,
  regenerationNextVersion,
  regenerationPublicationStateLabel,
  regenerationReasonError,
  regenerationReleasedFailureLines,
  regenerationTargetActions,
} from "@/lib/api";
import type {
  RegenerationBucket,
  RegenerationCampaignDetail,
  RegenerationTargetReport,
  RegenerationWaveFailure,
} from "@/lib/types";
import { CARD, GHOST_BTN, GLASS_BTN, INPUT_GLASS } from "@/lib/ui";
import { cn } from "@/lib/utils";
/**
 * Campaign outcome report.
 *
 * All six buckets, always in the documented order, even when empty — a report
 * that silently drops a bucket tells an operator the campaign is smaller than
 * it is. Every row carries the API's own `reason` sentence rather than an
 * internal status token, and the three shapes of a failed publication (the
 * publisher will retry / the retry is due / the automatic budget is gone) are
 * three visibly different situations, because only the third needs a human.
 *
 * Retry and abandon sit at their real positions and are genuinely wired. Both
 * are idempotent server-side; the disabled state exists so the screen does not
 * pretend a click did nothing.
 *
 * Two things on this screen do NOT come from the report, because the report
 * cannot carry them: the lessons a release could not start (`released_failures`
 * exists only on a mutation response) and what a publication retry cleared
 * (`previous_publication_*`, captured by the API before it wiped the error).
 * The route holds both and passes them in; rendering only what a refetch
 * returns would silently destroy both.
 */
import { Ban, Download, ExternalLink, RotateCcw, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

const BUCKET_TONE: Record<RegenerationBucket, string> = {
  published: "border-emerald-300/25 bg-emerald-300/[0.07] text-emerald-100",
  publication_pending: "border-sky-300/25 bg-sky-300/[0.07] text-sky-100",
  publication_failed: "border-amber-300/25 bg-amber-300/[0.08] text-amber-100",
  generation_failed: "border-rose-300/25 bg-rose-300/[0.08] text-rose-100",
  abandoned: "border-white/[0.12] bg-white/[0.04] text-white/60",
  in_flight: "border-white/[0.12] bg-white/[0.04] text-white/70",
};

function TargetRow({
  target,
  pendingKind,
  campaignTerminal,
  retryAudit,
  actionError,
  onAction,
}: {
  target: RegenerationTargetReport;
  pendingKind: RegenerationActionKind | null;
  campaignTerminal: boolean;
  /** What the last retry from this session cleared. Mutation-only: it is gone
   *  from the server the moment the retry succeeds. */
  retryAudit: string | null;
  /** Non-null ONLY when the failed target mutation named THIS lesson in its
   *  variables. A refusal rendered campaign-wide reads as though every row
   *  failed, and survived a change of selection attached to the wrong one. */
  actionError: RegenerationErrorView | null;
  onAction: (
    kind: RegenerationActionKind,
    target: RegenerationTargetReport,
    reason: string,
  ) => void;
}) {
  const [openKind, setOpenKind] = useState<RegenerationActionKind | null>(null);
  const [reason, setReason] = useState("");

  const actions = regenerationTargetActions(target, { pendingKind, campaignTerminal });
  const reasonError = regenerationReasonError(reason);
  const version = regenerationNextVersion(target);
  const open = actions.find((a) => a.kind === openKind) ?? null;

  return (
    <li className="space-y-1 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm text-white/85">
          {target.lesson.section_number ? `${target.lesson.section_number}. ` : ""}
          {target.lesson.section_title ?? target.toc_entry_id}
        </span>
        {target.is_canary && (
          <span className="rounded-lg border border-white/[0.1] px-2 py-0.5 font-mono text-[0.6rem] text-white/45">
            canary
          </span>
        )}
        <span className="font-mono text-[0.64rem] text-white/35">
          {target.output_language}
          {version != null && ` · V${version}`}
        </span>
      </div>

      <p className="max-w-[80ch] text-xs leading-5 text-white/55">{target.reason}</p>

      <div className="flex flex-wrap items-center gap-2 text-[0.66rem] text-white/45">
        <span className="rounded-lg border border-white/[0.1] bg-white/[0.03] px-2 py-0.5">
          {regenerationPublicationStateLabel(target.publication_state)}
        </span>
        {target.publication_attempts > 0 && (
          <span className="font-mono">
            {target.publication_attempts} delivery attempt
            {target.publication_attempts === 1 ? "" : "s"}
          </span>
        )}
        {target.copied_phase_count + target.regenerated_phase_count > 0 && (
          <span className="font-mono">
            {target.regenerated_phase_count} rebuilt · {target.copied_phase_count} copied
          </span>
        )}
        {Object.entries(target.judge_status_counts)
          .filter(([, n]) => n > 0)
          .map(([status, n]) => (
            <span key={status} className="font-mono">
              judge {status} ×{n}
            </span>
          ))}
        {Object.entries(target.solver_status_counts)
          .filter(([, n]) => n > 0)
          .map(([status, n]) => (
            <span key={status} className="font-mono">
              solver {status} ×{n}
            </span>
          ))}
      </div>

      {target.delivery_error && target.status !== "publication_failed" && (
        <p className="max-w-[80ch] text-xs leading-5 text-amber-100/70">
          Last delivery error: {target.delivery_error}
        </p>
      )}
      {retryAudit && <p className="max-w-[80ch] text-xs leading-5 text-white/45">{retryAudit}</p>}
      {actionError && <RegenerationProblem view={actionError} />}
      {target.source_note && (
        <p className="max-w-[80ch] text-xs leading-5 text-white/40">{target.source_note}</p>
      )}
      {target.phase_plan_error && (
        <p className="max-w-[80ch] text-xs leading-5 text-rose-100/80">
          This build cannot read the frozen phase plan: {target.phase_plan_error}
        </p>
      )}

      <div className="flex flex-wrap gap-3 text-[0.68rem]">
        {target.notion_page_url && (
          <a
            href={target.notion_page_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-emerald-200/80 hover:text-emerald-100"
          >
            <ExternalLink className="size-3" />
            Open the published page
          </a>
        )}
        {target.revision_job_id && (
          <>
            <Link
              to={`/job/${target.revision_job_id}`}
              className="inline-flex items-center gap-1 text-sky-200/80 hover:text-sky-100"
            >
              <ExternalLink className="size-3" />
              Open the revision
            </Link>
            <a
              href={api.jobDownloadUrl(target.revision_job_id)}
              className="inline-flex items-center gap-1 text-sky-200/80 hover:text-sky-100"
            >
              <Download className="size-3" />
              Download it
            </a>
          </>
        )}
      </div>

      {actions.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {actions.map((action) => (
            <button
              key={action.kind}
              type="button"
              disabled={!action.enabled}
              title={action.disabledReason ?? action.detail}
              onClick={() => {
                setOpenKind(openKind === action.kind ? null : action.kind);
                setReason("");
              }}
              className={cn(GHOST_BTN, "px-2 py-1 text-xs")}
            >
              {action.kind === "abandon" ? (
                <Ban className="size-3.5" />
              ) : (
                <RotateCcw className="size-3.5" />
              )}
              {action.label}
            </button>
          ))}
        </div>
      )}

      {open && (
        <div className="space-y-2 rounded-xl border border-white/[0.09] bg-white/[0.03] p-3">
          <p
            id={`regeneration-abandon-explainer-${target.id}`}
            className="max-w-[80ch] text-xs leading-5 text-white/60"
          >
            {open.detail}
          </p>
          {open.requiresReason && (
            <>
              <label
                htmlFor={`regeneration-abandon-reason-${target.id}`}
                className="block text-xs font-medium text-white/70"
              >
                Reason for abandoning this lesson (stored as the audit record)
              </label>
              <input
                id={`regeneration-abandon-reason-${target.id}`}
                type="text"
                aria-label="Reason for abandoning this lesson"
                aria-describedby={`regeneration-abandon-explainer-${target.id}`}
                value={reason}
                placeholder="Why are you abandoning this lesson?"
                onChange={(e) => setReason(e.target.value)}
                className={cn(INPUT_GLASS, "w-full text-sm")}
              />
            </>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={cn(GHOST_BTN, "px-2 py-1 text-xs")}
              disabled={!open.enabled || (open.requiresReason && reasonError !== null)}
              onClick={() => {
                onAction(open.kind, target, reason);
                setOpenKind(null);
              }}
            >
              Confirm · {open.label}
            </button>
            {open.requiresReason && reasonError && (
              <span className="text-xs text-amber-100/80">{reasonError}</span>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

export function CampaignReport({
  detail,
  releasedFailures,
  retryAuditByTarget,
  pendingByTarget,
  onAction,
  onCancelCampaign,
  cancelling,
  actionError,
  targetError,
}: {
  detail: RegenerationCampaignDetail;
  /** The report's own list MERGED with the ones only a mutation reported, so
   *  a poll cannot quietly erase them. */
  releasedFailures: RegenerationWaveFailure[];
  retryAuditByTarget: Record<string, string>;
  pendingByTarget: Record<string, RegenerationActionKind | null>;
  onAction: (
    kind: RegenerationActionKind,
    target: RegenerationTargetReport,
    reason: string,
  ) => void;
  onCancelCampaign: (reason: string) => void;
  cancelling: boolean;
  /** The CAMPAIGN-level refusal — today only cancel. */
  actionError: RegenerationErrorView | null;
  /** The per-target refusal, carrying the id its own mutation variables named. */
  targetError: { targetId: string; view: RegenerationErrorView } | null;
}) {
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const cancelReasonError = regenerationReasonError(cancelReason);

  const buckets = regenerationBucketViews(detail);
  // Names, not UUIDs: WaveFailureOut carries ids and the titles live on the
  // report's targets, so the two are joined before anything is rendered.
  const failureLines = regenerationReleasedFailureLines(detail, releasedFailures);

  return (
    <section className={cn(CARD, "space-y-3")}>
      <header>
        <h2 className="text-sm font-semibold text-white">Campaign report</h2>
        <p className="mt-1 max-w-[75ch] text-xs leading-5 text-white/45">
          Every lesson lands in exactly one bucket with a plain-language reason. Empty buckets stay
          visible so nothing quietly disappears.
        </p>
      </header>

      {regenerationKeyedLines(detail.warnings).map((row) => (
        <p
          key={row.key}
          className="max-w-[80ch] rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3 text-xs leading-5 text-amber-100/90"
        >
          {row.text}
        </p>
      ))}
      {detail.rollup_error && (
        <p className="max-w-[80ch] rounded-xl border border-rose-300/25 bg-rose-300/[0.07] p-3 text-xs leading-5 text-rose-100/90">
          The campaign rollup could not run on this read, so the counts below may lag by one
          refresh: {detail.rollup_error}
        </p>
      )}
      {failureLines.length > 0 && (
        <div className="space-y-1 rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3 text-xs leading-5 text-amber-100/90">
          <div className="flex items-start gap-2 font-semibold">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>
              The release reached {failureLines.length} lesson
              {failureLines.length === 1 ? "" : "s"} it could not start
            </span>
          </div>
          <ul className="space-y-0.5">
            {failureLines.map((line) => (
              <li key={line.targetId}>{line.text}</li>
            ))}
          </ul>
          <p>
            Everything else in the wave started normally; these rows are in the report below with
            their own retry and abandon controls. This list is kept from the release that reported
            it — the campaign report itself does not carry it — and clears when a later release for
            this campaign succeeds.
          </p>
        </div>
      )}

      {actionError && <RegenerationProblem view={actionError} />}

      <div className="space-y-3">
        {buckets.map((bucket) => (
          <div key={bucket.bucket} className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "rounded-lg border px-2 py-1 text-[0.68rem] font-medium",
                  BUCKET_TONE[bucket.bucket],
                )}
              >
                {bucket.label}
              </span>
              <span className="font-mono text-[0.66rem] text-white/35">{bucket.count}</span>
            </div>
            <p className="max-w-[80ch] pl-1 text-[0.68rem] leading-5 text-white/35">
              {bucket.description}
            </p>

            {bucket.count === 0 ? (
              <p className="pl-1 text-xs text-white/30">None.</p>
            ) : (
              <ul className="space-y-1">
                {bucket.targets.map((target) => (
                  <TargetRow
                    key={target.id}
                    target={target}
                    pendingKind={pendingByTarget[target.id] ?? null}
                    campaignTerminal={detail.is_terminal}
                    retryAudit={retryAuditByTarget[target.id] ?? null}
                    actionError={targetError?.targetId === target.id ? targetError.view : null}
                    onAction={onAction}
                  />
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>

      {!detail.is_terminal && (
        <div className="space-y-2 border-t border-white/[0.07] pt-3">
          <button
            type="button"
            className={GLASS_BTN}
            disabled={cancelling}
            onClick={() => setConfirmingCancel((v) => !v)}
          >
            Cancel campaign
          </button>
          {confirmingCancel && (
            <div className="space-y-2 rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3">
              <p
                id="regeneration-cancel-explainer"
                className="max-w-[80ch] text-xs leading-5 text-amber-100/90"
              >
                {REGENERATION_CANCEL_CONFIRMATION}
              </p>
              <label
                htmlFor="regeneration-cancel-reason"
                className="block text-xs font-medium text-amber-100/90"
              >
                Reason for stopping this campaign (stored as the audit record)
              </label>
              <input
                id="regeneration-cancel-reason"
                type="text"
                aria-label="Reason for stopping this campaign"
                aria-describedby="regeneration-cancel-explainer"
                value={cancelReason}
                placeholder="Why are you stopping this campaign?"
                onChange={(e) => setCancelReason(e.target.value)}
                className={cn(INPUT_GLASS, "w-full text-sm")}
              />
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className={GLASS_BTN}
                  disabled={cancelReasonError !== null || cancelling}
                  onClick={() => {
                    onCancelCampaign(cancelReason);
                    setConfirmingCancel(false);
                  }}
                >
                  {cancelling ? "Cancelling…" : "Confirm cancel"}
                </button>
                {cancelReasonError && (
                  <span className="text-xs text-amber-100/80">{cancelReasonError}</span>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
