/**
 * Pure decision layer for the versioned-homework regeneration shell (Task 4).
 *
 * `npm test` only executes `src/lib/*.test.ts` and there is no DOM renderer, so
 * every operator-facing rule of the regeneration flow — cascade disclosure,
 * exclusion acknowledgement, estimate arithmetic, approval-gate labelling,
 * report bucketing and reason prose — lives here as a pure function. The route
 * and the four components are thin renderers over these functions; a string a
 * component hand-rolls inline is a string the suite cannot see.
 *
 * Everything this module consumes is PLAN-SHAPED FIXTURE DATA. In particular
 * the downstream closure of a phase (`PhaseSelection.autoIncluded`) is supplied
 * by the caller — the Python planner owns `PHASE_DEPS` and this file never
 * recomputes it. Task 10 swaps the fixtures for typed API responses.
 *
 * Vocabulary: this flow says Regenerate / Regenerating / Regeneration. Fleet's
 * first-run label is deliberately not reused anywhere in the regeneration UI.
 */
import { formatPhaseName } from "./utils";

/* ── Types (local to Task 4; no coupling to lib/types.ts until Task 10) ── */

export type RegenerationLanguage = "uz" | "ru";

/** One lesson inside a campaign plan, with the version it will publish as. */
export interface PlanTarget {
  lessonId: string;
  lessonTitle: string;
  language: RegenerationLanguage;
  sourceVersion: number;
  nextVersion: number;
}

/**
 * The operator's phase choice plus the planner's expansion of it.
 * `autoIncluded` is the full downstream closure (it contains `selected`).
 */
export interface PhaseSelection {
  allPhases: string[];
  selected: string[];
  autoIncluded: string[];
  excluded: string[];
  extractionEnabled: boolean;
}

export type CascadeScope = "narrow" | "moderate" | "near_full";

export interface CascadeSummary {
  regenerated: string[];
  regeneratedCount: number;
  totalPhases: number;
  copiedCount: number;
  autoIncludedCount: number;
  excludedCount: number;
  headline: string;
  detail: string;
  scope: CascadeScope;
}

export interface WizardState {
  selectedLessonIds: string[];
  language: RegenerationLanguage;
  selectedPhases: string[];
  excludedPhases: string[];
  extractionEnabled: boolean;
  acknowledgedInconsistency: boolean;
  canarySize: number;
}

export interface ExclusionWarning {
  excluded: string[];
  message: string;
  acknowledgementLabel: string;
}

export interface LaunchGate {
  canLaunch: boolean;
  requiresAcknowledgement: boolean;
  blockedReason: string | null;
}

export interface EstimateInput {
  targets: PlanTarget[];
  phases: PhaseSelection;
  canarySize: number;
  costPerCallLowUsd: number;
  costPerCallHighUsd: number;
}

export interface EstimateSummary {
  targetCount: number;
  canarySize: number;
  regeneratedPhaseCount: number;
  copiedPhaseCount: number;
  autoIncludedPhaseCount: number;
  excludedPhaseCount: number;
  expectedModelCalls: number;
  costLowUsd: number;
  costHighUsd: number;
  costRangeText: string;
  nextVersionText: string;
  safetyNote: string;
}

export type CampaignStatus =
  | "draft"
  | "estimated"
  | "canary_regenerating"
  | "awaiting_approval"
  | "regenerating"
  | "publishing"
  | "completed"
  | "rejected"
  | "abandoned"
  | "failed";

export interface CampaignEstimate {
  costLowUsd: number;
  costHighUsd: number;
  expectedModelCalls: number;
}

export interface Campaign {
  id: string;
  name: string;
  status: CampaignStatus;
  targets: PlanTarget[];
  canarySize: number;
  createdAt: string;
  estimate: CampaignEstimate;
}

export interface ApprovalGate {
  singleTarget: boolean;
  remainingCount: number;
  showsBulkGenerationGate: boolean;
  /** False for a campaign with no lessons — there is nothing to approve. */
  canApprove: boolean;
  approveLabel: string;
  approveDetail: string;
  rejectLabel: string;
  rejectDetail: string;
}

export type PhaseOrigin = "regenerated" | "copied";

export interface PhaseProvenance {
  phase: string;
  origin: PhaseOrigin;
  sourceVersion: number;
}

export type JudgeStatus =
  | "pass"
  | "unavailable"
  | "refused"
  | "major_shipped"
  | "major_regen_failed";

export interface JudgeSignal {
  status: JudgeStatus;
  severity: "ok" | "warning";
  blocksPublication: boolean;
  label: string;
  explanation: string;
}

export interface CostComparison {
  direction: "over" | "under" | "on_target";
  deltaPct: number;
  text: string;
}

export type OutcomeStatus =
  | "published"
  | "publication_pending"
  | "publication_failed"
  | "generation_failed"
  | "abandoned";

export interface TargetOutcome {
  lessonId: string;
  lessonTitle: string;
  language: RegenerationLanguage;
  status: OutcomeStatus;
  publishedVersion: number | null;
  reasonCode: string | null;
}

export interface ReportBucket {
  status: OutcomeStatus;
  label: string;
  count: number;
  targets: TargetOutcome[];
}

export type ReportActionKind = "retry" | "abandon";

export interface ReportAction {
  kind: ReportActionKind;
  label: string;
  enabled: boolean;
  hint: string;
}

/* ── Small shared helpers ────────────────────────────────────────────── */

function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many;
}

/** "$1.60" — the one money format every regeneration surface uses. */
export function formatUsd(n: number): string {
  return `$${n.toFixed(2)}`;
}

/**
 * "1 lesson" / "4 lessons". Exported because components must never hand-roll a
 * pluralised count: an inline `{n} lessons` renders "1 lessons" and no test can
 * see it.
 */
export function lessonCountLabel(count: number): string {
  return `${count} ${plural(count, "lesson", "lessons")}`;
}

/** "A", "A and B", "A, B and C" — for naming phases inside a sentence. */
function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** Turn any internal snake/kebab token into operator prose. */
function humanise(code: string): string {
  const words = code.replace(/[_-]+/g, " ").trim();
  if (!words) return "";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/* ── 1. Phase selection + cascade disclosure ─────────────────────────── */

/**
 * Every phase the plan touches, in flow order. Enabling extraction puts the
 * whole content flow downstream of the new extract, so all phases are included.
 */
export function includedPhases(selection: PhaseSelection): string[] {
  if (selection.extractionEnabled) return [...selection.allPhases];
  const wanted = new Set([...selection.selected, ...selection.autoIncluded]);
  return selection.allPhases.filter((p) => wanted.has(p));
}

/** Included minus the phases the operator explicitly excluded. */
export function regeneratedPhases(selection: PhaseSelection): string[] {
  const excluded = new Set(selection.excluded);
  return includedPhases(selection).filter((p) => !excluded.has(p));
}

function scopeFor(regenerated: number, total: number): CascadeScope {
  if (total <= 0 || regenerated <= 0) return "narrow";
  const ratio = regenerated / total;
  if (ratio >= 0.7) return "near_full";
  if (ratio >= 0.3) return "moderate";
  return "narrow";
}

function cascadeDetail(scope: CascadeScope, regenerated: number, total: number): string {
  const copied = Math.max(0, total - regenerated);
  const rebuilt = `${regenerated} of ${total} phases ${plural(regenerated, "is", "are")} rebuilt`;
  const kept = `${copied} ${plural(copied, "is", "are")} copied forward unchanged`;
  if (scope === "near_full") {
    // Binding requirement: never sell a near-total expansion as a light touch.
    return `${rebuilt} — the dependency graph turns this into a full regeneration, so budget and review it as one.`;
  }
  if (scope === "moderate") {
    return `${rebuilt}; ${kept}.`;
  }
  return `This change stays contained: ${rebuilt} and ${kept}.`;
}

/**
 * The honest expansion of a phase tick: what actually gets rebuilt, what is
 * carried over, and how big the blast radius really is.
 */
export function cascadeDisclosure(selection: PhaseSelection): CascadeSummary {
  const included = includedPhases(selection);
  const regenerated = regeneratedPhases(selection);
  const total = selection.allPhases.length;
  const selected = new Set(selection.selected);
  const scope = scopeFor(regenerated.length, total);
  return {
    regenerated,
    regeneratedCount: regenerated.length,
    totalPhases: total,
    copiedCount: Math.max(0, total - regenerated.length),
    autoIncludedCount: included.filter((p) => !selected.has(p)).length,
    excludedCount: included.filter((p) => selection.excluded.includes(p)).length,
    headline: `Regenerates ${regenerated.length} of ${total} phases`,
    detail: cascadeDetail(scope, regenerated.length, total),
    scope,
  };
}

/** Fresh wizard: nothing picked, extraction OFF, nothing acknowledged. */
export function defaultWizardState(): WizardState {
  return {
    selectedLessonIds: [],
    language: "uz",
    selectedPhases: [],
    excludedPhases: [],
    extractionEnabled: false,
    acknowledgedInconsistency: false,
    canarySize: 1,
  };
}

/**
 * Non-null only when extraction is on — and then it states the real cost.
 *
 * Derived from `cascadeDisclosure` rather than from `allPhases.length`, because
 * an operator can still drop a phase while extraction is on. Reading the count
 * off the flow would print "all 11 phases are regenerated" beside a headline
 * saying "Regenerates 10 of 11 phases"; sharing the source makes that
 * impossible.
 */
export function extractionNotice(selection: PhaseSelection): string | null {
  if (!selection.extractionEnabled) return null;
  const cascade = cascadeDisclosure(selection);
  const head =
    "Re-extracting the source lesson puts every content phase downstream of the new extract, so";
  if (cascade.copiedCount === 0) {
    return `${head} all ${cascade.totalPhases} phases are regenerated and none are carried over. Price this as a full regeneration.`;
  }
  return `${head} ${cascade.regeneratedCount} of ${cascade.totalPhases} phases are regenerated and the ${cascade.copiedCount} you dropped ${plural(cascade.copiedCount, "is", "are")} carried over beside a brand-new extract. Price this as a full regeneration.`;
}

/* ── 2. Exclusion warning + launch gate ──────────────────────────────── */

/**
 * Excluding a phase the graph pulled in leaves old text next to new upstream
 * text. The operator has to be told exactly which phases, and why it matters.
 */
export function exclusionWarning(selection: PhaseSelection): ExclusionWarning | null {
  const included = includedPhases(selection);
  const excluded = included.filter((p) => selection.excluded.includes(p));
  if (excluded.length === 0) return null;
  const names = joinNames(excluded.map(formatPhaseName));
  return {
    excluded,
    message: `${names} ${plural(excluded.length, "depends", "depend")} on phases you are regenerating. Excluding ${plural(excluded.length, "it", "them")} keeps the current text beside newly rebuilt upstream phases, so the published homework may be internally inconsistent.`,
    acknowledgementLabel: "I understand the published homework may be internally inconsistent.",
  };
}

/**
 * The one gate that decides whether the canary may be launched.
 *
 * `targetCount` is required, not optional: wizard step 1 is lesson selection,
 * and without it the gate happily green-lit a launch of zero lessons while the
 * same card read "0 lessons, $0.00 – $0.00".
 */
export function launchGate(
  selection: PhaseSelection,
  acknowledged: boolean,
  targetCount: number,
): LaunchGate {
  const warning = exclusionWarning(selection);
  const requiresAcknowledgement = warning !== null;
  if (targetCount <= 0) {
    return {
      canLaunch: false,
      requiresAcknowledgement,
      blockedReason: "Select at least one lesson to regenerate.",
    };
  }
  if (regeneratedPhases(selection).length === 0) {
    return {
      canLaunch: false,
      requiresAcknowledgement,
      blockedReason: "Select at least one phase to regenerate.",
    };
  }
  if (requiresAcknowledgement && !acknowledged) {
    return {
      canLaunch: false,
      requiresAcknowledgement,
      blockedReason: "Acknowledge the consistency warning before launching this campaign.",
    };
  }
  return { canLaunch: true, requiresAcknowledgement, blockedReason: null };
}

/* ── 3. Estimate ─────────────────────────────────────────────────────── */

/**
 * One generation call plus one judge call per regenerated phase, per lesson.
 * The judge is roughly half of measured spend, so leaving it out of the shown
 * estimate would understate the campaign by about a factor of two. Task 10
 * replaces this local arithmetic with the server planner's number.
 */
const MODEL_CALLS_PER_PHASE = 2;

export const ESTIMATE_SAFETY_NOTE =
  "Creating and estimating a campaign makes no model calls and creates no Notion pages. " +
  "Nothing is spent and nothing is published until you approve the canary.";

/** Honest per-version movement, e.g. "3 lessons move V1 → V2, 1 moves V2 → V3". */
export function nextVersionSummary(targets: PlanTarget[]): string {
  if (targets.length === 0) return "No lessons selected.";
  const groups = new Map<string, { source: number; next: number; count: number }>();
  for (const t of targets) {
    const key = `${t.sourceVersion}>${t.nextVersion}`;
    const found = groups.get(key);
    if (found) found.count += 1;
    else groups.set(key, { source: t.sourceVersion, next: t.nextVersion, count: 1 });
  }
  const ordered = [...groups.values()].sort((a, b) => a.source - b.source || a.next - b.next);
  return ordered
    .map((g, index) => {
      const verb = plural(g.count, "moves", "move");
      const head =
        index === 0
          ? `${g.count} ${plural(g.count, "lesson", "lessons")} ${verb}`
          : `${g.count} ${verb}`;
      return `${head} V${g.source} → V${g.next}`;
    })
    .join(", ");
}

export function estimateSummary(input: EstimateInput): EstimateSummary {
  const cascade = cascadeDisclosure(input.phases);
  const targetCount = input.targets.length;
  const perLesson =
    cascade.regeneratedCount * MODEL_CALLS_PER_PHASE + (input.phases.extractionEnabled ? 1 : 0);
  const expectedModelCalls = targetCount * perLesson;
  const costLowUsd = expectedModelCalls * input.costPerCallLowUsd;
  const costHighUsd = expectedModelCalls * input.costPerCallHighUsd;
  return {
    targetCount,
    canarySize: input.canarySize,
    regeneratedPhaseCount: cascade.regeneratedCount,
    copiedPhaseCount: cascade.copiedCount,
    autoIncludedPhaseCount: cascade.autoIncludedCount,
    excludedPhaseCount: cascade.excludedCount,
    expectedModelCalls,
    costLowUsd,
    costHighUsd,
    costRangeText: `${formatUsd(costLowUsd)} – ${formatUsd(costHighUsd)}`,
    nextVersionText: nextVersionSummary(input.targets),
    safetyNote: ESTIMATE_SAFETY_NOTE,
  };
}

/* ── 4. The single campaign-level approval gate ──────────────────────── */

const NO_BULK_STEP_DETAIL =
  "The canary already covers every lesson in this campaign, so there is " +
  "no separate bulk step — approving publishes the packet you just reviewed.";

/**
 * Exactly one gate per campaign.
 *
 * A ONE-LESSON campaign is its own canary: it can never show a bulk step and
 * never has a remainder, whatever `canarySize` says — a malformed `canarySize:
 * 0` used to push it onto the multi-target branch and offer to "regenerate 1
 * remaining lesson" that does not exist. A MULTI-lesson campaign whose canary
 * covers every target has no remaining generation either, so it takes the same
 * no-bulk path; that second branch is why the predicate is not simply
 * `singleTarget`.
 */
export function approvalGate(campaign: Campaign): ApprovalGate {
  const targetCount = campaign.targets.length;
  const singleTarget = targetCount === 1;
  const remainingCount = singleTarget ? 0 : Math.max(0, targetCount - campaign.canarySize);
  const showsBulkGenerationGate = !singleTarget && remainingCount > 0;

  let approveLabel: string;
  let approveDetail: string;
  if (singleTarget) {
    // The version is read off the target, never hardcoded: a V2 source publishes V3.
    approveLabel = `Approve canary and publish V${campaign.targets[0].nextVersion}`;
    approveDetail = NO_BULK_STEP_DETAIL;
  } else if (targetCount === 0) {
    approveLabel = "Nothing to approve";
    approveDetail =
      "This campaign has no lessons, so there is nothing to publish and " +
      "no separate bulk step.";
  } else if (!showsBulkGenerationGate) {
    approveLabel = `Approve canary and publish ${lessonCountLabel(targetCount)}`;
    approveDetail = NO_BULK_STEP_DETAIL;
  } else {
    approveLabel = `Approve canary and regenerate ${remainingCount} remaining ${plural(
      remainingCount,
      "lesson",
      "lessons",
    )}`;
    approveDetail = `Approving publishes the reviewed canary and releases the remaining ${remainingCount} ${plural(remainingCount, "lesson", "lessons")}. This is the only approval gate in the campaign; there is no per-lesson publication approval.`;
  }

  return {
    singleTarget,
    remainingCount,
    showsBulkGenerationGate,
    canApprove: targetCount > 0,
    approveLabel,
    approveDetail,
    rejectLabel: "Reject campaign",
    rejectDetail:
      "Rejecting discards the canary: no Notion page is created and " +
      "no publication version is consumed.",
  };
}

/* ── 5. Canary review — provenance, judge signals, cost ──────────────── */

export function provenanceLabel(provenance: PhaseProvenance): string {
  return provenance.origin === "regenerated"
    ? "Regenerated"
    : `Copied from V${provenance.sourceVersion}`;
}

/**
 * Soft judge verdicts. These are prominent warnings on the canary, never
 * blockers — a refusing or unreachable judge must not strand a packet.
 */
export const SOFT_JUDGE_STATUSES: JudgeStatus[] = [
  "unavailable",
  "refused",
  "major_shipped",
  "major_regen_failed",
];

const JUDGE_SIGNALS: Record<JudgeStatus, JudgeSignal> = {
  pass: {
    status: "pass",
    severity: "ok",
    blocksPublication: false,
    label: "Judge passed",
    explanation: "The judge graded this phase and found no major problem with it.",
  },
  unavailable: {
    status: "unavailable",
    severity: "warning",
    blocksPublication: false,
    label: "Judge unavailable",
    explanation:
      "The judge could not be reached, so this phase ships ungraded. Read it yourself before approving.",
  },
  refused: {
    status: "refused",
    severity: "warning",
    blocksPublication: false,
    label: "Judge refused to grade",
    explanation:
      "The judge declined to grade this phase. The content is unverified, but publication is not blocked.",
  },
  major_shipped: {
    status: "major_shipped",
    severity: "warning",
    blocksPublication: false,
    label: "Major problem shipped",
    explanation:
      "The judge flagged a major problem and the phase shipped anyway. Read this phase closely before approving.",
  },
  major_regen_failed: {
    status: "major_regen_failed",
    severity: "warning",
    blocksPublication: false,
    label: "Major problem, retry failed",
    explanation:
      "The judge flagged a major problem and the single automatic retry also failed, so the earlier text shipped.",
  },
};

export function judgeSignal(status: JudgeStatus): JudgeSignal {
  return (
    JUDGE_SIGNALS[status] ?? {
      status,
      severity: "warning",
      blocksPublication: false,
      label: humanise(status),
      explanation:
        "The judge reported a verdict this build does not recognise. Review this phase by hand.",
    }
  );
}

/** Estimated versus actual spend, safe when no estimate was recorded. */
export function costComparison(estimatedUsd: number, actualUsd: number): CostComparison {
  if (!(estimatedUsd > 0)) {
    return {
      direction: actualUsd > 0 ? "over" : "on_target",
      deltaPct: 0,
      text: `Actual ${formatUsd(actualUsd)} — no estimate was recorded for this campaign.`,
    };
  }
  const deltaPct = Math.round(((actualUsd - estimatedUsd) / estimatedUsd) * 100);
  if (deltaPct === 0) {
    return {
      direction: "on_target",
      deltaPct,
      text: `Actual ${formatUsd(actualUsd)} — on estimate (${formatUsd(estimatedUsd)}).`,
    };
  }
  const over = deltaPct > 0;
  return {
    direction: over ? "over" : "under",
    deltaPct,
    text: `Actual ${formatUsd(actualUsd)} — ${Math.abs(deltaPct)}% ${
      over ? "above" : "below"
    } the ${formatUsd(estimatedUsd)} estimate.`,
  };
}

/* ── 6. Campaign status vocabulary ───────────────────────────────────── */

export const CAMPAIGN_STATUS_LABELS: Record<CampaignStatus, string> = {
  draft: "Draft",
  estimated: "Estimated",
  canary_regenerating: "Canary regenerating",
  awaiting_approval: "Awaiting approval",
  regenerating: "Regenerating",
  publishing: "Publishing",
  completed: "Completed",
  rejected: "Rejected",
  abandoned: "Abandoned",
  failed: "Failed",
};

export function campaignStatusLabel(status: CampaignStatus): string {
  return CAMPAIGN_STATUS_LABELS[status] ?? humanise(status);
}

/* ── 7. Report buckets, reasons, and (inert) actions ─────────────────── */

export const REPORT_BUCKET_ORDER: OutcomeStatus[] = [
  "published",
  "publication_pending",
  "publication_failed",
  "generation_failed",
  "abandoned",
];

export const REPORT_BUCKET_LABELS: Record<OutcomeStatus, string> = {
  published: "Regenerated and published",
  publication_pending: "Regenerated, publication pending",
  publication_failed: "Regenerated, publication failed",
  generation_failed: "Regeneration failed",
  abandoned: "Abandoned",
};

/** Always five buckets, always in order — a zero bucket still tells a story. */
export function bucketReport(outcomes: TargetOutcome[]): ReportBucket[] {
  return REPORT_BUCKET_ORDER.map((status) => {
    const targets = outcomes.filter((o) => o.status === status);
    return {
      status,
      label: REPORT_BUCKET_LABELS[status],
      count: targets.length,
      targets,
    };
  });
}

const REASON_TEXT: Record<string, string> = {
  publication_queued: "Regenerated successfully and waiting in the publication queue.",
  notion_parent_missing:
    "Publication failed because the parent Notion Lesson Topic page could not be found.",
  notion_rate_limited:
    "Publication failed because Notion rate-limited the workspace; the page was not created.",
  provider_quota_exhausted:
    "Regeneration failed because the model provider's quota for this key was exhausted.",
  judge_auth_failed:
    "Regeneration failed because the judge credential was rejected, so the packet was never graded.",
  operator_abandoned: "An operator abandoned this lesson before it was published.",
  superseded_by_newer_campaign:
    "Abandoned because a newer campaign already regenerated this lesson.",
};

const STATUS_FALLBACK: Record<OutcomeStatus, string> = {
  published: "Published to Notion; the version number was not recorded.",
  publication_pending: "Regenerated successfully and waiting in the publication queue.",
  publication_failed: "Publication to Notion failed and no reason was recorded for it.",
  generation_failed: "Regeneration failed and no reason was recorded for it.",
  abandoned: "This lesson was abandoned before it reached publication.",
};

/**
 * Always operator prose, never an internal token — including for codes this
 * build has never seen, which are spelled out rather than echoed raw.
 */
export function outcomeReason(outcome: TargetOutcome): string {
  // Status first: a lesson that reached Notion reports its version even when a
  // worker left a code from an earlier failed attempt on the row. Letting the
  // code win rendered "Publication failed…" on a published lesson.
  if (outcome.status === "published" && outcome.publishedVersion != null) {
    return `Published to Notion as V${outcome.publishedVersion}.`;
  }
  const code = outcome.reasonCode;
  if (code) {
    const mapped = REASON_TEXT[code];
    if (mapped) return mapped;
    return `${humanise(code)} was reported for this lesson; there is no plain-language explanation mapped for that code yet.`;
  }
  return STATUS_FALLBACK[outcome.status];
}

export const TASK_10_HINT =
  "Retry and abandon are wired up in Task 10; the controls are inert in this shell.";

const RETRY_LABELS: Partial<Record<OutcomeStatus, string>> = {
  publication_failed: "Retry publication",
  generation_failed: "Retry generation",
};

const ACTION_KINDS: Record<OutcomeStatus, ReportActionKind[]> = {
  published: [],
  publication_pending: ["abandon"],
  publication_failed: ["retry", "abandon"],
  generation_failed: ["retry", "abandon"],
  abandoned: [],
};

/** Real positions, real `<button disabled>` in the UI — no wiring until Task 10. */
export function reportActions(outcome: TargetOutcome): ReportAction[] {
  return ACTION_KINDS[outcome.status].map((kind) => ({
    kind,
    label: kind === "retry" ? (RETRY_LABELS[outcome.status] ?? "Retry lesson") : "Abandon lesson",
    enabled: false,
    hint: TASK_10_HINT,
  }));
}
