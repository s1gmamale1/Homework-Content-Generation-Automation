import { CampaignReport } from "@/components/regeneration/campaign-report";
import { CanaryReview } from "@/components/regeneration/canary-review";
import type { RegenerationActionKind, RegenerationErrorView } from "@/lib/api";
import type {
  RegenerationCampaignDetail,
  RegenerationTargetReport,
  RegenerationWaveFailure,
} from "@/lib/types";

export function CanaryStep({
  campaign,
  releasedFailures,
  retryAuditByTarget,
  pendingByTarget,
  onLaunchCanary,
  onApprove,
  onReject,
  onTargetAction,
  onCancel,
  launching,
  approving,
  rejecting,
  cancelling,
  actionError,
  targetError,
}: {
  campaign: RegenerationCampaignDetail;
  releasedFailures: RegenerationWaveFailure[];
  retryAuditByTarget: Record<string, string>;
  pendingByTarget: Record<string, RegenerationActionKind | null>;
  onLaunchCanary: () => void;
  onApprove: () => void;
  onReject: (reason: string) => Promise<unknown>;
  onTargetAction: (
    kind: RegenerationActionKind,
    target: RegenerationTargetReport,
    reason: string,
  ) => Promise<unknown>;
  onCancel: (reason: string) => Promise<unknown>;
  launching: boolean;
  approving: boolean;
  rejecting: boolean;
  cancelling: boolean;
  actionError: RegenerationErrorView | null;
  targetError: { targetId: string; view: RegenerationErrorView } | null;
}) {
  return (
    <div className="space-y-4">
      <CanaryReview
        key={`canary-${campaign.id}`}
        detail={campaign}
        onLaunchCanary={onLaunchCanary}
        onApprove={onApprove}
        onReject={onReject}
        launching={launching}
        approving={approving}
        rejecting={rejecting}
        actionError={actionError}
      />
      <CampaignReport
        key={`report-${campaign.id}`}
        detail={campaign}
        releasedFailures={releasedFailures}
        retryAuditByTarget={retryAuditByTarget}
        pendingByTarget={pendingByTarget}
        onAction={onTargetAction}
        onCancelCampaign={onCancel}
        cancelling={cancelling}
        actionError={actionError}
        targetError={targetError}
      />
    </div>
  );
}
