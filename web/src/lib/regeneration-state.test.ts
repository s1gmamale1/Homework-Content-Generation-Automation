/**
 * Pure-logic tests for the versioned-homework regeneration shell (Task 4).
 *
 * `npm test` runs `node --import tsx --test src/lib/*.test.ts`, so ONLY files
 * under src/lib are executed and there is no React renderer available. Every
 * rule the shell has to obey therefore lives as a pure function in
 * `regeneration-state.ts` and is exercised here; the route/components are thin
 * renderers over these functions. A few structural guards at the bottom read
 * the component sources as text — that is the only way to prove "the flag
 * removes the route and the nav item" without a DOM.
 *
 * All data here is FIXTURE data. Task 10 replaces it with typed API responses.
 */
import assert from "node:assert";
import { readFileSync } from "node:fs";
import {
  ApiError,
  REGENERATION_APPROVE_NOTE,
  REGENERATION_CANCEL_CONFIRMATION,
  REGENERATION_DETAIL_IDLE,
  REGENERATION_DETAIL_LOADING,
  REGENERATION_LAUNCH_LABEL,
  REGENERATION_LAUNCH_SPEND_NOTE,
  REGENERATION_LIST_EMPTY,
  REGENERATION_LIST_LOADING,
  REGENERATION_LIST_POLL_MS,
  REGENERATION_NO_SPEND_NOTE,
  REGENERATION_PICK_BOOK_HINT,
  REGENERATION_PLAN_LOADING,
  REGENERATION_PLAN_NONE,
  REGENERATION_POLL_MS,
  REGENERATION_READ_RETRY_LABEL,
  REGENERATION_REJECT_CONFIRMATION,
  REGENERATION_SOURCES_EMPTY,
  REGENERATION_SOURCES_LOADING,
  cascadeFromPlan,
  clampCanarySize,
  mergeReleasedFailures,
  phaseSelectionFromPlan,
  regenerationApprovalGate,
  regenerationBookFacets,
  regenerationBookOptions,
  regenerationBucketViews,
  regenerationCampaignListView,
  regenerationCampaignStatusLabel,
  regenerationDetailView,
  regenerationEligibleQuery,
  regenerationErrorView,
  regenerationIneligibleLine,
  regenerationKeyedLines,
  regenerationListPollMs,
  regenerationMutationView,
  regenerationNarrowScope,
  regenerationPlanStepView,
  regenerationPollDecision,
  regenerationPublicationStateLabel,
  regenerationReasonError,
  regenerationReleasedFailureLines,
  regenerationRetryAudit,
  regenerationSolverStatusLabel,
  regenerationSourceRow,
  regenerationSourcesView,
  regenerationStrandedRelease,
  regenerationTargetActions,
  regenerationToggleLesson,
} from "./api";
import {
  IS_REGENERATION_ENABLED,
  REGENERATION_NAV_LABEL,
  REGENERATION_ROUTE_PATH,
  isRegenerationEnabled,
} from "./regeneration-feature";
import {
  CAMPAIGN_STATUS_LABELS,
  ESTIMATE_SAFETY_NOTE,
  REPORT_BUCKET_LABELS,
  REPORT_BUCKET_ORDER,
  SOFT_JUDGE_STATUSES,
  TASK_10_HINT,
  approvalGate,
  bucketReport,
  campaignStatusLabel,
  cascadeDisclosure,
  costComparison,
  defaultWizardState,
  estimateSummary,
  exclusionWarning,
  extractionNotice,
  formatUsd,
  includedPhases,
  judgeSignal,
  launchGate,
  lessonCountLabel,
  nextVersionSummary,
  outcomeReason,
  provenanceLabel,
  regeneratedPhases,
  reportActions,
} from "./regeneration-state";
import type { Campaign, PhaseSelection, PlanTarget, TargetOutcome } from "./regeneration-state";
import type {
  Book,
  RegenerationPhasePlan as RegenerationApiPhasePlan,
  RegenerationCampaignDetail,
  RegenerationEligibleSource,
  RegenerationTargetReport,
} from "./types";

/* ────────────────────────────────────────────────────────────────────
 * Fixtures
 * ──────────────────────────────────────────────────────────────────── */

/** The real 11 content phases (app/services/flows.py `flow_for`). */
const CONTENT_PHASES = [
  "case-based-preview",
  "flashcards",
  "memory-check",
  "practice-rlc",
  "practice-error-detection",
  "practice-memory-match",
  "practice-tictactoe",
  "practice-jigsaw",
  "practice-sentence",
  "boss-arena",
  "reflection",
];

/**
 * Measured downstream closures (inclusive of the selected phase) from the
 * Python planner. Fixture data ONLY — the TS side never recomputes PHASE_DEPS.
 */
const CASCADE_FROM_FLASHCARDS = [
  "flashcards",
  "memory-check",
  "practice-rlc",
  "practice-error-detection",
  "practice-memory-match",
  "practice-tictactoe",
  "practice-jigsaw",
  "practice-sentence",
  "boss-arena",
  "reflection",
];
const CASCADE_FROM_MEMORY_CHECK = [
  "memory-check",
  "practice-error-detection",
  "practice-memory-match",
  "boss-arena",
  "reflection",
];
const CASCADE_FROM_BOSS_ARENA = ["boss-arena", "reflection"];
const CASCADE_FROM_REFLECTION = ["reflection"];

function selection(over: Partial<PhaseSelection> = {}): PhaseSelection {
  return {
    allPhases: CONTENT_PHASES,
    selected: ["flashcards"],
    autoIncluded: CASCADE_FROM_FLASHCARDS,
    excluded: [],
    extractionEnabled: false,
    ...over,
  };
}

function target(over: Partial<PlanTarget> = {}): PlanTarget {
  return {
    lessonId: "t-1",
    lessonTitle: "1-mavzu. Hujayra tuzilishi",
    language: "uz",
    sourceVersion: 1,
    nextVersion: 2,
    ...over,
  };
}

/* ────────────────────────────────────────────────────────────────────
 * 1. Cascade disclosure — "Regenerates X of Y phases"
 * ──────────────────────────────────────────────────────────────────── */

// The four measured cascades must render the real expansion, not the
// operator's tick count.
const flashcards = cascadeDisclosure(selection());
assert.strictEqual(flashcards.regeneratedCount, 10);
assert.strictEqual(flashcards.totalPhases, 11);
assert.strictEqual(flashcards.copiedCount, 1);
assert.strictEqual(flashcards.headline, "Regenerates 10 of 11 phases");
assert.strictEqual(flashcards.scope, "near_full");

const memoryCheck = cascadeDisclosure(
  selection({ selected: ["memory-check"], autoIncluded: CASCADE_FROM_MEMORY_CHECK }),
);
assert.strictEqual(memoryCheck.headline, "Regenerates 5 of 11 phases");
assert.strictEqual(memoryCheck.scope, "moderate");

const bossArena = cascadeDisclosure(
  selection({ selected: ["boss-arena"], autoIncluded: CASCADE_FROM_BOSS_ARENA }),
);
assert.strictEqual(bossArena.headline, "Regenerates 2 of 11 phases");
assert.strictEqual(bossArena.scope, "narrow");

const reflection = cascadeDisclosure(
  selection({ selected: ["reflection"], autoIncluded: CASCADE_FROM_REFLECTION }),
);
assert.strictEqual(reflection.headline, "Regenerates 1 of 11 phases");
assert.strictEqual(reflection.scope, "narrow");
assert.strictEqual(reflection.copiedCount, 10);

// The binding requirement: a near-full cascade must NEVER be described as
// cheap, small, isolated or contained.
const CHEAP_WORDS = /\b(cheap|cheaply|inexpensive|isolated|contained|small|minor|just)\b/i;
assert.ok(
  !CHEAP_WORDS.test(flashcards.detail),
  `near-full cascade must not be described as cheap/isolated, got: ${flashcards.detail}`,
);
assert.ok(
  /full regeneration/i.test(flashcards.detail),
  `near-full cascade must name the real consequence, got: ${flashcards.detail}`,
);
// A genuinely narrow cascade may say so.
assert.ok(/contained/i.test(reflection.detail));

// The auto-included set is what the operator did NOT tick but gets anyway.
assert.strictEqual(flashcards.autoIncludedCount, 9);
assert.strictEqual(reflection.autoIncludedCount, 0);

// Included/regenerated ordering follows the flow order, not selection order.
assert.deepStrictEqual(
  includedPhases(selection({ selected: ["reflection", "flashcards"] })),
  CASCADE_FROM_FLASHCARDS,
);

/* ────────────────────────────────────────────────────────────────────
 * 2. Extraction — off by default, and near-full when on
 * ──────────────────────────────────────────────────────────────────── */

assert.strictEqual(defaultWizardState().extractionEnabled, false);
assert.strictEqual(defaultWizardState().acknowledgedInconsistency, false);
assert.deepStrictEqual(defaultWizardState().selectedPhases, []);

assert.strictEqual(extractionNotice(selection()), null);

const withExtract = selection({ extractionEnabled: true });
// Extraction makes every content phase downstream of the new extract.
assert.deepStrictEqual(regeneratedPhases(withExtract), CONTENT_PHASES);
assert.strictEqual(cascadeDisclosure(withExtract).headline, "Regenerates 11 of 11 phases");
assert.strictEqual(cascadeDisclosure(withExtract).scope, "near_full");
const notice = extractionNotice(withExtract);
assert.ok(notice !== null);
assert.ok(/11/.test(notice), `extraction notice must state the real phase count: ${notice}`);
assert.ok(!CHEAP_WORDS.test(notice), `extraction notice must not downplay cost: ${notice}`);

// Extraction ON *and* a phase dropped: the notice must not claim "all 11" while
// the headline says 10. Both read from the same cascade.
const extractMinusOne = selection({ extractionEnabled: true, excluded: ["reflection"] });
const partialCascade = cascadeDisclosure(extractMinusOne);
assert.strictEqual(partialCascade.headline, "Regenerates 10 of 11 phases");
const partialNotice = extractionNotice(extractMinusOne);
assert.ok(partialNotice !== null);
assert.ok(
  /10 of 11 phases/.test(partialNotice),
  `extraction notice must agree with the cascade headline: ${partialNotice}`,
);
assert.ok(
  !/all 11/.test(partialNotice),
  `extraction notice must not claim a full rebuild when a phase was dropped: ${partialNotice}`,
);
assert.ok(!CHEAP_WORDS.test(partialNotice), partialNotice);

/* ────────────────────────────────────────────────────────────────────
 * 3. Exclusion warning + acknowledgement gate
 * ──────────────────────────────────────────────────────────────────── */

// No exclusions ⇒ no warning, launch allowed without an acknowledgement.
assert.strictEqual(exclusionWarning(selection()), null);
const cleanGate = launchGate(selection(), false, 3);
assert.strictEqual(cleanGate.canLaunch, true);
assert.strictEqual(cleanGate.requiresAcknowledgement, false);
assert.strictEqual(cleanGate.blockedReason, null);

const excluding = selection({ excluded: ["boss-arena", "reflection"] });
const warning = exclusionWarning(excluding);
assert.ok(warning !== null, "excluding an auto-included phase must raise a warning");
assert.deepStrictEqual(warning.excluded, ["boss-arena", "reflection"]);
// It must identify each excluded phase by a readable name…
assert.ok(/Boss Arena/.test(warning.message), warning.message);
assert.ok(/Reflection/.test(warning.message), warning.message);
// …and explain the consequence.
assert.ok(/inconsistent/i.test(warning.message), warning.message);

// Excluded phases really are dropped from the regenerated set.
assert.strictEqual(cascadeDisclosure(excluding).regeneratedCount, 8);
assert.strictEqual(cascadeDisclosure(excluding).excludedCount, 2);
assert.ok(!regeneratedPhases(excluding).includes("boss-arena"));

// Un-acknowledged ⇒ blocked, with a reason an operator can act on.
const blocked = launchGate(excluding, false, 3);
assert.strictEqual(blocked.canLaunch, false);
assert.strictEqual(blocked.requiresAcknowledgement, true);
assert.ok(typeof blocked.blockedReason === "string" && blocked.blockedReason.length > 10);
assert.ok(/acknowledge/i.test(blocked.blockedReason!), blocked.blockedReason!);

// Acknowledged ⇒ allowed.
const acked = launchGate(excluding, true, 3);
assert.strictEqual(acked.canLaunch, true);
assert.strictEqual(acked.blockedReason, null);
assert.strictEqual(acked.requiresAcknowledgement, true);

// An empty selection can never launch, acknowledged or not.
const empty = launchGate(selection({ selected: [], autoIncluded: [] }), true, 3);
assert.strictEqual(empty.canLaunch, false);
assert.ok(/phase/i.test(empty.blockedReason!), empty.blockedReason!);

// …and neither can a selection with zero lessons, however many phases are
// ticked. Wizard step 1 is lesson selection; without this the shell offered a
// live "Launch canary" button beside "0 lessons, $0.00 - $0.00".
const noLessons = launchGate(selection(), true, 0);
assert.strictEqual(noLessons.canLaunch, false);
assert.ok(/lesson/i.test(noLessons.blockedReason!), noLessons.blockedReason!);
// The lesson check fires even when an acknowledgement is also outstanding.
const noLessonsUnacked = launchGate(excluding, false, 0);
assert.strictEqual(noLessonsUnacked.canLaunch, false);
assert.strictEqual(noLessonsUnacked.requiresAcknowledgement, true);

/* ────────────────────────────────────────────────────────────────────
 * 4. Estimate — counts, calls, cost range, versions, and the safety note
 * ──────────────────────────────────────────────────────────────────── */

const multiTargets: PlanTarget[] = [
  target({ lessonId: "t-1" }),
  target({ lessonId: "t-2", lessonTitle: "2-mavzu. Fotosintez" }),
  target({ lessonId: "t-3", lessonTitle: "3-mavzu. Nafas olish", language: "ru" }),
  // already at V2 — this one moves to V3, proving the version is never hardcoded.
  target({ lessonId: "t-4", lessonTitle: "4-mavzu. Genetika", sourceVersion: 2, nextVersion: 3 }),
];

const est = estimateSummary({
  targets: multiTargets,
  phases: selection(),
  canarySize: 2,
  costPerCallLowUsd: 0.02,
  costPerCallHighUsd: 0.05,
});
assert.strictEqual(est.targetCount, 4);
assert.strictEqual(est.canarySize, 2);
assert.strictEqual(est.regeneratedPhaseCount, 10);
assert.strictEqual(est.copiedPhaseCount, 1);
assert.strictEqual(est.autoIncludedPhaseCount, 9);
assert.strictEqual(est.excludedPhaseCount, 0);
// One generation call + one judge call per regenerated phase, per lesson.
assert.strictEqual(est.expectedModelCalls, 4 * 10 * 2);
assert.strictEqual(est.costLowUsd, 80 * 0.02);
assert.strictEqual(est.costHighUsd, 80 * 0.05);
assert.strictEqual(est.costRangeText, "$1.60 – $4.00");

// Extraction adds one extract call per lesson on top.
const estExtract = estimateSummary({
  targets: multiTargets,
  phases: withExtract,
  canarySize: 2,
  costPerCallLowUsd: 0.02,
  costPerCallHighUsd: 0.05,
});
assert.strictEqual(estExtract.expectedModelCalls, 4 * (11 * 2 + 1));

// A campaign with no lessons costs nothing and says so, with no NaN anywhere.
const estNone = estimateSummary({
  targets: [],
  phases: selection(),
  canarySize: 1,
  costPerCallLowUsd: 0.02,
  costPerCallHighUsd: 0.05,
});
assert.strictEqual(estNone.targetCount, 0);
assert.strictEqual(estNone.expectedModelCalls, 0);
assert.strictEqual(estNone.costLowUsd, 0);
assert.strictEqual(estNone.costRangeText, "$0.00 – $0.00");
assert.ok(!/NaN|Infinity/.test(estNone.costRangeText), estNone.costRangeText);
assert.strictEqual(estNone.nextVersionText, "No lessons selected.");

// Mixed source versions must be reported honestly.
assert.strictEqual(nextVersionSummary(multiTargets), "3 lessons move V1 → V2, 1 moves V2 → V3");
assert.strictEqual(nextVersionSummary([target()]), "1 lesson moves V1 → V2");
assert.strictEqual(
  nextVersionSummary([target({ sourceVersion: 2, nextVersion: 3 })]),
  "1 lesson moves V2 → V3",
);

// Estimating must be inert, and the UI has to say so.
assert.ok(/no model calls/i.test(ESTIMATE_SAFETY_NOTE), ESTIMATE_SAFETY_NOTE);
assert.ok(/no Notion pages?/i.test(ESTIMATE_SAFETY_NOTE), ESTIMATE_SAFETY_NOTE);

/* ────────────────────────────────────────────────────────────────────
 * 5. Approval gate — one campaign-level gate, no empty bulk step
 * ──────────────────────────────────────────────────────────────────── */

function campaign(over: Partial<Campaign> = {}): Campaign {
  return {
    id: "c-1",
    name: "Biology 8 · UZ · prompt refresh",
    status: "awaiting_approval",
    targets: multiTargets,
    canarySize: 2,
    createdAt: "2026-08-20T09:00:00Z",
    estimate: { costLowUsd: 1.6, costHighUsd: 4.0, expectedModelCalls: 80 },
    ...over,
  };
}

// One target: that lesson IS the canary. Exact label required by the brief.
const single = approvalGate(campaign({ targets: [target()], canarySize: 1 }));
assert.strictEqual(single.singleTarget, true);
assert.strictEqual(single.approveLabel, "Approve canary and publish V2");
assert.strictEqual(single.showsBulkGenerationGate, false);
assert.strictEqual(single.canApprove, true);
assert.strictEqual(single.remainingCount, 0);
assert.ok(/no separate bulk/i.test(single.approveDetail), single.approveDetail);

// A malformed canarySize must NOT push a one-lesson campaign onto the
// multi-target branch: that lesson IS the canary, so there is no remainder and
// no bulk step to offer, whatever the plan says.
const singleZeroCanary = approvalGate(campaign({ targets: [target()], canarySize: 0 }));
assert.strictEqual(singleZeroCanary.singleTarget, true);
assert.strictEqual(singleZeroCanary.remainingCount, 0);
assert.strictEqual(
  singleZeroCanary.showsBulkGenerationGate,
  false,
  "a one-lesson campaign can never render a bulk-generation gate",
);
assert.strictEqual(singleZeroCanary.approveLabel, "Approve canary and publish V2");

// A campaign with no lessons must not offer to publish "0 lessons".
const noTargets = approvalGate(campaign({ targets: [], canarySize: 1 }));
assert.strictEqual(noTargets.singleTarget, false);
assert.strictEqual(noTargets.remainingCount, 0);
assert.strictEqual(noTargets.showsBulkGenerationGate, false);
assert.strictEqual(noTargets.approveLabel, "Nothing to approve");
// …and the button that carries that label must not be clickable.
assert.strictEqual(noTargets.canApprove, false);
assert.ok(!/\b0 lessons\b/.test(noTargets.approveLabel), noTargets.approveLabel);

// The version is derived, never hardcoded: a V2 source publishes V3.
const singleV3 = approvalGate(
  campaign({ targets: [target({ sourceVersion: 2, nextVersion: 3 })], canarySize: 1 }),
);
assert.strictEqual(singleV3.approveLabel, "Approve canary and publish V3");

// Multi-target with real remainder: a distinct label naming the remainder.
const multi = approvalGate(campaign());
assert.strictEqual(multi.singleTarget, false);
assert.strictEqual(multi.remainingCount, 2);
assert.strictEqual(multi.showsBulkGenerationGate, true);
assert.strictEqual(multi.approveLabel, "Approve canary and regenerate 2 remaining lessons");
assert.notStrictEqual(multi.approveLabel, single.approveLabel);

// Split-branch case: MULTI target but the canary already covers everything.
// There is no remaining generation, so the bulk gate must not render — this is
// the same rule as the single-target case and a same-branch test cannot see it.
const multiNoRemainder = approvalGate(campaign({ canarySize: 4 }));
assert.strictEqual(multiNoRemainder.singleTarget, false);
assert.strictEqual(multiNoRemainder.remainingCount, 0);
assert.strictEqual(
  multiNoRemainder.showsBulkGenerationGate,
  false,
  "a campaign whose canary covers every target must not render an empty bulk gate",
);
assert.strictEqual(multiNoRemainder.approveLabel, "Approve canary and publish 4 lessons");

// Rejecting is always safe and must say so.
assert.strictEqual(multi.rejectLabel, "Reject campaign");
assert.ok(/no Notion page/i.test(multi.rejectDetail), multi.rejectDetail);
assert.ok(/no publication version/i.test(multi.rejectDetail), multi.rejectDetail);

/* ────────────────────────────────────────────────────────────────────
 * 6. Canary review — provenance, soft judge statuses, cost compare
 * ──────────────────────────────────────────────────────────────────── */

assert.strictEqual(
  provenanceLabel({ phase: "flashcards", origin: "regenerated", sourceVersion: 1 }),
  "Regenerated",
);
assert.strictEqual(
  provenanceLabel({ phase: "case-based-preview", origin: "copied", sourceVersion: 1 }),
  "Copied from V1",
);

// The four soft judge statuses are prominent WARNINGS, never blockers.
assert.deepStrictEqual(SOFT_JUDGE_STATUSES, [
  "unavailable",
  "refused",
  "major_shipped",
  "major_regen_failed",
]);
for (const status of SOFT_JUDGE_STATUSES) {
  const signal = judgeSignal(status);
  assert.strictEqual(signal.severity, "warning", `${status} must be a warning`);
  assert.strictEqual(signal.blocksPublication, false, `${status} must NOT block publication`);
  // Operator-facing, not an internal code.
  assert.ok(!signal.label.includes("_"), `${status} label leaks a code: ${signal.label}`);
  assert.ok(signal.explanation.length > 20, `${status} needs an explanation`);
}
assert.strictEqual(judgeSignal("pass").severity, "ok");
assert.strictEqual(judgeSignal("pass").blocksPublication, false);

// Estimated vs actual cost.
const overBudget = costComparison(0.35, 0.42);
assert.strictEqual(overBudget.direction, "over");
assert.strictEqual(overBudget.deltaPct, 20);
assert.ok(/\$0\.42/.test(overBudget.text) && /\$0\.35/.test(overBudget.text), overBudget.text);
assert.strictEqual(costComparison(0.4, 0.2).direction, "under");
assert.strictEqual(costComparison(0.4, 0.4).direction, "on_target");
assert.ok(/on estimate/i.test(costComparison(0.4, 0.4).text));
// No estimate recorded must not produce "NaN%" or a divide-by-zero.
assert.ok(!/NaN|Infinity/.test(costComparison(0, 0.12).text), costComparison(0, 0.12).text);

/* ────────────────────────────────────────────────────────────────────
 * 7. Report buckets + human-readable reasons + disabled actions
 * ──────────────────────────────────────────────────────────────────── */

assert.deepStrictEqual(REPORT_BUCKET_ORDER, [
  "published",
  "publication_pending",
  "publication_failed",
  "generation_failed",
  "abandoned",
]);

const outcomes: TargetOutcome[] = [
  {
    lessonId: "t-1",
    lessonTitle: "1-mavzu. Hujayra tuzilishi",
    language: "uz",
    status: "published",
    publishedVersion: 2,
    reasonCode: null,
  },
  {
    lessonId: "t-2",
    lessonTitle: "2-mavzu. Fotosintez",
    language: "uz",
    status: "publication_pending",
    publishedVersion: null,
    reasonCode: "publication_queued",
  },
  {
    lessonId: "t-3",
    lessonTitle: "3-mavzu. Nafas olish",
    language: "ru",
    status: "publication_failed",
    publishedVersion: null,
    reasonCode: "notion_parent_missing",
  },
  {
    lessonId: "t-4",
    lessonTitle: "4-mavzu. Genetika",
    language: "uz",
    status: "generation_failed",
    publishedVersion: null,
    reasonCode: "provider_quota_exhausted",
  },
  {
    lessonId: "t-5",
    lessonTitle: "5-mavzu. Evolyutsiya",
    language: "uz",
    status: "abandoned",
    publishedVersion: null,
    reasonCode: "operator_abandoned",
  },
  {
    // An unmapped code must STILL render readable prose, not the raw token.
    lessonId: "t-6",
    lessonTitle: "6-mavzu. Ekologiya",
    language: "ru",
    status: "generation_failed",
    publishedVersion: null,
    reasonCode: "some_unmapped_worker_code",
  },
];

const buckets = bucketReport(outcomes);
// Always all five, always in the documented order, so a zero bucket still shows.
assert.strictEqual(buckets.length, 5);
assert.deepStrictEqual(
  buckets.map((b) => b.status),
  REPORT_BUCKET_ORDER,
);
assert.deepStrictEqual(
  buckets.map((b) => b.count),
  [1, 1, 1, 2, 1],
);
assert.strictEqual(buckets[0].label, REPORT_BUCKET_LABELS.published);
assert.strictEqual(buckets[3].targets[1].lessonId, "t-6");
// Every bucket label is operator prose, not a status code.
for (const b of buckets) {
  assert.ok(!b.label.includes("_"), `bucket label leaks a code: ${b.label}`);
}
// A campaign with no outcomes still renders five empty buckets.
assert.deepStrictEqual(
  bucketReport([]).map((b) => b.count),
  [0, 0, 0, 0, 0],
);

// Reasons are ALWAYS human-readable — never a bare internal status code.
for (const o of outcomes) {
  const reason = outcomeReason(o);
  assert.ok(reason.length > 20, `${o.lessonId}: reason too short: ${reason}`);
  assert.ok(reason.endsWith("."), `${o.lessonId}: reason must be a sentence: ${reason}`);
  assert.ok(!reason.includes("_"), `${o.lessonId}: reason leaks a code: ${reason}`);
  assert.notStrictEqual(reason, o.reasonCode);
}
assert.ok(/Notion/.test(outcomeReason(outcomes[2])), outcomeReason(outcomes[2]));
assert.ok(/quota/i.test(outcomeReason(outcomes[3])), outcomeReason(outcomes[3]));
assert.ok(/V2/.test(outcomeReason(outcomes[0])), outcomeReason(outcomes[0]));
// A published lesson carrying a stale code from an earlier attempt still reads
// as published — status is the ground truth, the code is not.
assert.strictEqual(
  outcomeReason({
    lessonId: "t-7",
    lessonTitle: "7-mavzu. Irsiyat",
    language: "uz",
    status: "published",
    publishedVersion: 2,
    reasonCode: "notion_rate_limited",
  }),
  "Published to Notion as V2.",
);

// The unmapped code is humanised, not echoed.
const unmapped = outcomeReason(outcomes[5]);
assert.ok(/Some unmapped worker code/i.test(unmapped), unmapped);

// Retry / abandon sit at their real positions but are inert until Task 10.
assert.deepStrictEqual(
  reportActions(outcomes[0]).map((a) => a.kind),
  [],
);
assert.deepStrictEqual(
  reportActions(outcomes[4]).map((a) => a.kind),
  [],
);
assert.deepStrictEqual(
  reportActions(outcomes[1]).map((a) => a.kind),
  ["abandon"],
);
assert.deepStrictEqual(
  reportActions(outcomes[2]).map((a) => a.kind),
  ["retry", "abandon"],
);
assert.deepStrictEqual(
  reportActions(outcomes[3]).map((a) => a.kind),
  ["retry", "abandon"],
);
assert.strictEqual(reportActions(outcomes[2])[0].label, "Retry publication");
assert.strictEqual(reportActions(outcomes[3])[0].label, "Retry generation");
for (const o of outcomes) {
  for (const action of reportActions(o)) {
    assert.strictEqual(action.enabled, false, `${action.kind} must be disabled in the shell`);
    assert.strictEqual(action.hint, TASK_10_HINT);
  }
}
assert.ok(/Task 10/.test(TASK_10_HINT), TASK_10_HINT);

/* ────────────────────────────────────────────────────────────────────
 * 7b. Shared label helpers the components must not re-implement
 * ──────────────────────────────────────────────────────────────────── */

// An inline `{n} lessons` renders "1 lessons"; the helper is the only place
// this string is built, so a component can be structurally held to it below.
assert.strictEqual(lessonCountLabel(0), "0 lessons");
assert.strictEqual(lessonCountLabel(1), "1 lesson");
assert.strictEqual(lessonCountLabel(4), "4 lessons");
assert.strictEqual(formatUsd(0), "$0.00");
assert.strictEqual(formatUsd(1.6), "$1.60");
assert.strictEqual(formatUsd(0.4251), "$0.43");

/* ────────────────────────────────────────────────────────────────────
 * 8. Vocabulary — "Regenerating", never Fleet's "Generating"
 * ──────────────────────────────────────────────────────────────────── */

assert.strictEqual(campaignStatusLabel("regenerating"), "Regenerating");
assert.strictEqual(campaignStatusLabel("canary_regenerating"), "Canary regenerating");
for (const [status, label] of Object.entries(CAMPAIGN_STATUS_LABELS)) {
  assert.ok(!label.includes("_"), `status label leaks a code: ${status} → ${label}`);
  assert.ok(
    !/\bGenerating\b/.test(label),
    `regeneration must never reuse Fleet's "Generating" label: ${status} → ${label}`,
  );
}

/* ────────────────────────────────────────────────────────────────────
 * 9. Feature flag
 * ──────────────────────────────────────────────────────────────────── */

// Default off: absent, empty, and any non-"1" value are all off.
assert.strictEqual(isRegenerationEnabled({}), false);
assert.strictEqual(isRegenerationEnabled({ VITE_REGENERATION_ENABLED: "" }), false);
assert.strictEqual(isRegenerationEnabled({ VITE_REGENERATION_ENABLED: "0" }), false);
assert.strictEqual(isRegenerationEnabled({ VITE_REGENERATION_ENABLED: "true" }), false);
assert.strictEqual(isRegenerationEnabled({ VITE_REGENERATION_ENABLED: "1" }), true);
// Importing under `node --import tsx --test` must not throw on import.meta.env.
assert.strictEqual(typeof IS_REGENERATION_ENABLED, "boolean");
assert.strictEqual(IS_REGENERATION_ENABLED, false);
assert.strictEqual(REGENERATION_ROUTE_PATH, "/regeneration");
assert.strictEqual(REGENERATION_NAV_LABEL, "Regeneration");

/* ────────────────────────────────────────────────────────────────────
 * 10. Structural guards on the JSX we cannot render here
 * ──────────────────────────────────────────────────────────────────── */

function source(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), "utf8");
}

const appSrc = source("../App.tsx");
const layoutSrc = source("../components/layout.tsx");

// The flag must remove the ROUTE and the NAV ITEM, not just their contents.
for (const [name, src] of [
  ["App.tsx", appSrc],
  ["layout.tsx", layoutSrc],
] as const) {
  assert.ok(
    src.includes("IS_REGENERATION_ENABLED"),
    `${name} must gate on IS_REGENERATION_ENABLED`,
  );
  assert.ok(
    /IS_REGENERATION_ENABLED\s*&&/.test(src),
    `${name} must guard with \`IS_REGENERATION_ENABLED &&\`, not an early return`,
  );
  assert.ok(
    src.includes("regeneration-feature"),
    `${name} must import the flag from @/lib/regeneration-feature`,
  );
}
// The regeneration route/nav belongs only in the non-viewer branch, so the
// existing viewer split must survive.
assert.ok(appSrc.includes("IS_VIEWER"), "App.tsx must keep the viewer branch");
assert.ok(layoutSrc.includes("IS_VIEWER"), "layout.tsx must keep the viewer branch");
// The viewer bundle is dashboard-only: /regeneration must not be reachable there.
const viewerBranch = appSrc.slice(appSrc.indexOf("IS_VIEWER ? ("), appSrc.indexOf(") : ("));
assert.ok(
  viewerBranch.length > 0 && !viewerBranch.includes("/regeneration"),
  "the viewer branch must not register the regeneration route",
);

// No component may hand-roll a string the tests cannot see, and none may use
// Fleet's "Generating" vocabulary.
const SHELL_FILES = [
  "./regeneration-state.ts",
  "../routes/regeneration.tsx",
  "../components/regeneration/regeneration-wizard.tsx",
  "../components/regeneration/campaign-list.tsx",
  "../components/regeneration/canary-review.tsx",
  "../components/regeneration/campaign-report.tsx",
];
for (const rel of SHELL_FILES) {
  const src = source(rel);
  assert.ok(
    !/\bGenerating\b/.test(src),
    `${rel} uses Fleet's "Generating" vocabulary; this flow says "Regenerating"`,
  );
}

// Task 10 INVERTS the Task 4 fixture rule. The route and the four components
// must now read the real Task 9 shapes; `regeneration-state.ts` is deliberately
// left uncoupled because it is the pure decision layer and takes whatever data
// its caller hands it.
const WIRED_FILES = SHELL_FILES.filter((rel) => rel !== "./regeneration-state.ts");
for (const rel of WIRED_FILES) {
  const src = source(rel);
  assert.ok(
    /from "@\/lib\/api"/.test(src) || /from "@\/lib\/types"/.test(src),
    `${rel} must consume the typed Task 9 API, not local fixtures`,
  );
  assert.ok(
    !/\bFIXTURE_/.test(src),
    `${rel} still declares Task 4 fixture data; server state is authoritative`,
  );
  assert.ok(!/TASK_10_HINT/.test(src), `${rel} still shows the Task 4 "wired up in Task 10" hint`);
}

// The components must render the pure decisions, not re-derive them inline.
const canarySrc = source("../components/regeneration/canary-review.tsx");
assert.ok(
  canarySrc.includes("regenerationApprovalGate"),
  "canary-review must render regenerationApprovalGate()",
);
assert.ok(
  /disabled=\{!gate\.canApprove/.test(canarySrc),
  "canary-review must not offer a clickable approve button for an empty campaign",
);
assert.ok(
  !canarySrc.includes("Approve canary and publish V"),
  "canary-review must not hardcode the approval label — it comes from the approval gate",
);
assert.ok(
  canarySrc.includes("REGENERATION_LAUNCH_LABEL"),
  'canary-review must use the shared "Generate canary" label',
);
const wizardSrc = source("../components/regeneration/regeneration-wizard.tsx");
assert.ok(wizardSrc.includes("cascadeFromPlan"), "wizard must render the server's phase plan");
assert.ok(
  !/Regenerates \$\{|Regenerates \d/.test(wizardSrc),
  "wizard must not hand-roll the cascade headline",
);
assert.ok(
  wizardSrc.includes("REGENERATION_NO_SPEND_NOTE"),
  "the create/estimate steps must state that nothing is spent and nothing is published",
);
// campaign-list had no guard here, which is exactly where a hand-rolled
// "{n} lessons" (rendering "1 lessons") slipped through review.
const listSrc = source("../components/regeneration/campaign-list.tsx");
assert.ok(
  listSrc.includes("regenerationCampaignStatusLabel"),
  "campaign-list must render regenerationCampaignStatusLabel()",
);
assert.ok(listSrc.includes("lessonCountLabel"), "campaign-list must render lessonCountLabel()");
assert.ok(!/lessons/.test(listSrc), "campaign-list must not hand-roll a pluralised lesson count");
assert.ok(
  !/toFixed/.test(listSrc),
  "campaign-list must format money through formatUsd(), not its own helper",
);
const reportSrc = source("../components/regeneration/campaign-report.tsx");
assert.ok(
  reportSrc.includes("regenerationBucketViews"),
  "report must render every bucket through regenerationBucketViews()",
);
assert.ok(
  reportSrc.includes("regenerationTargetActions"),
  "report must render regenerationTargetActions()",
);
assert.ok(
  reportSrc.includes("target.reason"),
  "report must render the server's plain-language reason for every target",
);
assert.ok(
  reportSrc.includes("disabled"),
  "report retry/abandon buttons must carry the disabled attribute",
);
const routeSrc = source("../routes/regeneration.tsx");
assert.ok(
  routeSrc.includes("regenerationPollDecision"),
  "the route must decide polling through regenerationPollDecision()",
);
assert.ok(
  routeSrc.includes("invalidateQueries"),
  "mutations must invalidate the campaign queries they change",
);

/* ────────────────────────────────────────────────────────────────────
 * 11. Task 9 fixtures — the exact response shapes the API returns
 * ──────────────────────────────────────────────────────────────────── */

const CAMPAIGN_ID = "6f1c1d4e-9a2b-4c3d-8e5f-0a1b2c3d4e5f";
/** A real revision job. Its presence is what separates a lesson that is
 *  genuinely regenerating from one the server left behind (§26). */
const RUNNING_JOB_ID = "cafe0000-0000-4000-8000-00000000beef";

/** One row of `GET /eligible` — a finished homework job that may be a source. */
const ELIGIBLE_SOURCE: RegenerationEligibleSource = {
  toc_entry_id: "99998888-7777-6666-5555-444433332222",
  output_language: "uz",
  source_job_id: "job-1",
  book_id: "aaaabbbb-cccc-dddd-eeee-ffff00001111",
  subject: "biology",
  grade: "8",
  source_publication_version: 1,
  next_expected_version: 2,
  source_is_revision: false,
  section_number: "1",
  section_title: "Kirish",
  chapter_title: "Hujayra",
  order_index: 1,
  has_notion_lesson_page: true,
};

function apiTarget(over: Partial<RegenerationTargetReport> = {}): RegenerationTargetReport {
  return {
    id: "11112222-3333-4444-5555-666677778888",
    campaign_id: CAMPAIGN_ID,
    toc_entry_id: "99998888-7777-6666-5555-444433332222",
    output_language: "uz",
    is_canary: false,
    status: "planned",
    bucket: "in_flight",
    publication_state: "not_started",
    is_terminal: false,
    action_required: false,
    reason: "planned; no revision job has been created yet.",
    source_job_id: "aaaabbbb-cccc-dddd-eeee-ffff00001111",
    source_publication_version: 1,
    source_note: null,
    revision_job_id: null,
    revision_job_status: null,
    revision_job_scheduled_at: null,
    content_path: null,
    download_path: null,
    publication_version: null,
    notion_page_id: null,
    notion_page_url: null,
    publication_released_at: null,
    publication_attempts: 0,
    publication_next_attempt_at: null,
    publication_last_error: null,
    delivery_error: null,
    terminal_at: null,
    terminal_reason: null,
    abandon_requested_at: null,
    abandon_requested_reason: null,
    lesson: {
      book_id: "aaaabbbb-cccc-dddd-eeee-ffff00001111",
      order_index: 1,
      section_number: "1",
      section_title: "1-mavzu. Hujayra tuzilishi",
      chapter_title: "Hujayra",
    },
    phase_plan: null,
    phase_plan_error: null,
    judge_status_counts: {},
    solver_status_counts: {},
    copied_phase_count: 0,
    regenerated_phase_count: 0,
    created_at: "2026-08-20T09:00:00Z",
    updated_at: "2026-08-20T09:00:00Z",
    ...over,
  };
}

function apiDetail(over: Partial<RegenerationCampaignDetail> = {}): RegenerationCampaignDetail {
  const targets = over.targets ?? [apiTarget()];
  const statusCounts: Record<string, number> = {};
  for (const t of targets) statusCounts[t.status] = (statusCounts[t.status] ?? 0) + 1;
  return {
    id: CAMPAIGN_ID,
    status: "draft",
    is_terminal: false,
    attention_required: false,
    target_count: targets.length,
    status_counts: statusCounts,
    bucket_counts: {},
    canary_size: 1,
    refresh_extraction: false,
    exclusion_acknowledged: false,
    requested_phases: ["flashcards"],
    excluded_phases: [],
    app_git_revision: "7209a4e",
    estimated_cost_low_usd: 1.2,
    estimated_cost_high_usd: 3,
    canary_launched_at: null,
    approved_at: null,
    rejected_at: null,
    cancel_requested_at: null,
    completed_at: null,
    rejected_reason: null,
    cancel_requested_reason: null,
    created_at: "2026-08-20T09:00:00Z",
    updated_at: "2026-08-20T09:00:00Z",
    selection_spec: {},
    launch_contract: {},
    solver_enabled_observed: true,
    buckets: {},
    canary: [],
    actual_cost: {
      usd: 0,
      call_count: 0,
      paid_call_count: 0,
      zero_cost_marker_count: 0,
      failed_call_count: 0,
      excluded_row_count: 0,
      revision_job_count: 0,
      prompt_tokens: 0,
      output_tokens: 0,
      cached_tokens: 0,
      cache_creation_tokens: 0,
      total_tokens: 0,
    },
    judge_status_counts: {},
    solver_status_counts: {},
    provenance: { copied_phase_count: 0, regenerated_phase_count: 0, phase_row_count: 0 },
    release_schedule: {
      job_count: 0,
      wave_count: 0,
      final_offset_seconds: 0,
      first_scheduled_at: null,
      last_scheduled_at: null,
      source: "persisted homework_jobs.scheduled_at",
    },
    warnings: [],
    rollup_error: null,
    released_failures: [],
    ...over,
    targets,
  };
}

/* ────────────────────────────────────────────────────────────────────
 * 12. Polling — only while something is actually moving
 * ──────────────────────────────────────────────────────────────────── */

// Nothing loaded: nothing to poll.
assert.strictEqual(regenerationPollDecision(undefined).shouldPoll, false);
assert.strictEqual(regenerationPollDecision(undefined).intervalMs, false);

// Canary generation.
{
  const decision = regenerationPollDecision(
    apiDetail({
      status: "canary_running",
      canary_launched_at: "2026-08-20T09:30:00Z",
      targets: [
        apiTarget({
          status: "generating",
          bucket: "in_flight",
          is_canary: true,
          revision_job_id: RUNNING_JOB_ID,
        }),
      ],
    }),
  );
  assert.strictEqual(decision.shouldPoll, true);
  assert.strictEqual(decision.intervalMs, REGENERATION_POLL_MS);
  assert.ok(decision.activity.some((a) => /regenerating/i.test(a)));
}

// Bulk generation.
assert.strictEqual(
  regenerationPollDecision(
    apiDetail({
      status: "bulk_running",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [
        apiTarget({ status: "generating", bucket: "in_flight", revision_job_id: RUNNING_JOB_ID }),
      ],
    }),
  ).shouldPoll,
  true,
);

// Publication queued and publication in flight.
for (const status of ["publication_pending", "publishing"] as const) {
  const decision = regenerationPollDecision(
    apiDetail({
      status: "approved",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [
        apiTarget({
          status,
          bucket: status === "publishing" ? "in_flight" : "publication_pending",
          publication_state: status === "publishing" ? "publishing" : "queued",
          revision_job_id: "cafe0000-0000-4000-8000-000000000001",
        }),
      ],
    }),
  );
  assert.strictEqual(decision.shouldPoll, true, `${status} must keep polling`);
}

// Automatic backoff and a due automatic retry both keep polling: the publisher
// moves these WITHOUT an operator, so a frozen report would read as stuck.
for (const state of ["backing_off", "retry_due"] as const) {
  const decision = regenerationPollDecision(
    apiDetail({
      status: "attention_required",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [
        apiTarget({
          status: "publication_failed",
          bucket: "publication_failed",
          publication_state: state,
          publication_attempts: 2,
          publication_next_attempt_at: "2026-08-20T11:00:00Z",
          revision_job_id: "cafe0000-0000-4000-8000-000000000001",
        }),
      ],
    }),
  );
  assert.strictEqual(decision.shouldPoll, true, `${state} must keep polling`);
  assert.ok(decision.activity.some((a) => /retry/i.test(a)));
}

// Terminal campaign: stop.
for (const status of [
  "completed",
  "completed_with_abandonments",
  "rejected",
  "cancelled",
] as const) {
  const decision = regenerationPollDecision(
    apiDetail({
      status,
      is_terminal: true,
      targets: [
        apiTarget({
          status: "published",
          bucket: "published",
          is_terminal: true,
          publication_state: "published",
        }),
      ],
    }),
  );
  assert.strictEqual(decision.shouldPoll, false, `${status} must stop polling`);
  assert.strictEqual(decision.intervalMs, false);
  assert.ok(decision.reason.length > 0);
}

// Action-required: a parked publication needs a human, so nothing moves.
{
  const decision = regenerationPollDecision(
    apiDetail({
      status: "attention_required",
      attention_required: true,
      approved_at: "2026-08-20T10:00:00Z",
      targets: [
        apiTarget({
          status: "publication_failed",
          bucket: "publication_failed",
          publication_state: "action_required",
          action_required: true,
          publication_attempts: 5,
          revision_job_id: "cafe0000-0000-4000-8000-000000000001",
        }),
      ],
    }),
  );
  assert.strictEqual(decision.shouldPoll, false);
  assert.match(decision.reason, /retry|abandon/i);
}

// Generation failed is likewise parked on a human.
assert.strictEqual(
  regenerationPollDecision(
    apiDetail({
      status: "attention_required",
      attention_required: true,
      targets: [
        apiTarget({
          status: "generation_failed",
          bucket: "generation_failed",
          action_required: true,
        }),
      ],
    }),
  ).shouldPoll,
  false,
);

// The canary gate is a HUMAN gate — polling it burns requests forever.
{
  const decision = regenerationPollDecision(
    apiDetail({
      status: "awaiting_canary_approval",
      canary_launched_at: "2026-08-20T09:30:00Z",
      targets: [
        apiTarget({
          status: "awaiting_canary_approval",
          bucket: "in_flight",
          is_canary: true,
          revision_job_id: "cafe0000-0000-4000-8000-000000000001",
        }),
      ],
    }),
  );
  assert.strictEqual(decision.shouldPoll, false);
  assert.match(decision.reason, /review|approve/i);
}

// A draft has never generated anything.
assert.strictEqual(regenerationPollDecision(apiDetail({ status: "draft" })).shouldPoll, false);

// Approved but nothing released: polling would spin forever, so stop and say
// what recovers it (approve is idempotent).
{
  const decision = regenerationPollDecision(
    apiDetail({
      status: "approved",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [apiTarget({ status: "planned", bucket: "in_flight", revision_job_id: null })],
    }),
  );
  assert.strictEqual(decision.shouldPoll, false);
  assert.match(decision.reason, /approve/i);
}

// The list poll follows the same rule: quiet campaigns do not need a ticker.
assert.strictEqual(regenerationListPollMs([]), false);
assert.strictEqual(
  regenerationListPollMs([apiDetail({ status: "canary_running" })]),
  REGENERATION_LIST_POLL_MS,
);
assert.strictEqual(
  regenerationListPollMs([apiDetail({ status: "completed", is_terminal: true })]),
  false,
);

/* ────────────────────────────────────────────────────────────────────
 * 13. Report buckets — all six, always, in plain language
 * ──────────────────────────────────────────────────────────────────── */

{
  const detail = apiDetail({
    status: "attention_required",
    targets: [
      apiTarget({
        id: "t-published",
        status: "published",
        bucket: "published",
        is_terminal: true,
        publication_state: "published",
        publication_version: 2,
        notion_page_id: "abc123",
        notion_page_url: "https://www.notion.so/abc123",
        reason: "published as Homework V2.",
      }),
      apiTarget({
        id: "t-pending",
        status: "publication_pending",
        bucket: "publication_pending",
        publication_state: "queued",
        reason: "generated and queued for automatic publication.",
      }),
      apiTarget({
        id: "t-failed",
        status: "publication_failed",
        bucket: "publication_failed",
        publication_state: "action_required",
        action_required: true,
        publication_attempts: 5,
        publication_last_error: "VersionPageCollision: Homework V2 exists without a marker",
        delivery_error: "VersionPageCollision: Homework V2 exists without a marker",
        reason:
          "delivery failed after 5 attempt(s) and there is NO AUTOMATIC RETRY left — an " +
          "operator must retry publication or abandon this target. Last error: " +
          "VersionPageCollision: Homework V2 exists without a marker",
      }),
      apiTarget({
        id: "t-genfail",
        status: "generation_failed",
        bucket: "generation_failed",
        action_required: true,
        reason:
          "generation failed: solver mismatch_blocked on boss-arena. Retry generation or " +
          "abandon this target — it holds the lesson's active lineage until then.",
      }),
      apiTarget({
        id: "t-abandoned",
        status: "abandoned",
        bucket: "abandoned",
        is_terminal: true,
        publication_state: "abandoned",
        publication_version: 3,
        terminal_reason: "page collision could not be cleaned up",
        delivery_error: "VersionPageCollision",
        reason:
          "abandoned: page collision could not be cleaned up. No Notion page was deleted and " +
          "version V3 stays consumed. Last delivery error: VersionPageCollision",
      }),
      apiTarget({ id: "t-inflight", status: "generating", bucket: "in_flight" }),
    ],
  });

  const views = regenerationBucketViews(detail);
  assert.strictEqual(views.length, 6, "every bucket is rendered, including empty ones");
  assert.deepStrictEqual(
    views.map((v) => v.bucket),
    [
      "published",
      "publication_pending",
      "publication_failed",
      "generation_failed",
      "abandoned",
      "in_flight",
    ],
  );
  for (const view of views) {
    assert.strictEqual(view.count, 1, `${view.bucket} should hold exactly one fixture target`);
    assert.ok(!view.label.includes("_"), `bucket label leaks a code: ${view.label}`);
    assert.ok(view.description.length > 0, `${view.bucket} has no plain-language description`);
    assert.ok(
      !/\bGenerating\b/.test(view.label) && !/\bGenerating\b/.test(view.description),
      `${view.bucket} reuses Fleet's "Generating" vocabulary`,
    );
  }
  // An empty bucket still renders, with a count of zero.
  const empty = regenerationBucketViews(apiDetail({ targets: [] }));
  assert.strictEqual(empty.length, 6);
  assert.deepStrictEqual(
    empty.map((v) => v.count),
    [0, 0, 0, 0, 0, 0],
  );
}

/* ────────────────────────────────────────────────────────────────────
 * 14. Publication state and campaign status vocabulary
 * ──────────────────────────────────────────────────────────────────── */

{
  const STATES = [
    "published",
    "abandoned",
    "publishing",
    "queued",
    "backing_off",
    "retry_due",
    "action_required",
    "not_started",
  ] as const;
  const labels = new Set<string>();
  for (const state of STATES) {
    const label = regenerationPublicationStateLabel(state);
    assert.ok(label.length > 0, `${state} has no label`);
    assert.ok(!label.includes("_"), `publication state label leaks a code: ${label}`);
    labels.add(label);
  }
  // The three publication_failed shapes are three DIFFERENT situations; giving
  // them one label hides every row that needs a human.
  assert.strictEqual(labels.size, STATES.length, "each publication state needs its own words");
  assert.match(regenerationPublicationStateLabel("backing_off"), /automatic/i);
  assert.match(regenerationPublicationStateLabel("retry_due"), /due|shortly|next/i);
  assert.match(regenerationPublicationStateLabel("action_required"), /you|operator|no automatic/i);
}

{
  const STATUSES = [
    "draft",
    "canary_running",
    "awaiting_canary_approval",
    "approved",
    "bulk_running",
    "attention_required",
    "completed",
    "completed_with_abandonments",
    "rejected",
    "cancelled",
  ] as const;
  for (const status of STATUSES) {
    const label = regenerationCampaignStatusLabel(status);
    assert.ok(label.length > 0, `${status} has no label`);
    assert.ok(!label.includes("_"), `campaign status label leaks a code: ${status} → ${label}`);
    assert.ok(
      !/\bGenerating\b/.test(label),
      `regeneration must never reuse Fleet's "Generating" label: ${status} → ${label}`,
    );
  }
  assert.match(regenerationCampaignStatusLabel("completed_with_abandonments"), /abandon/i);
}

/* ────────────────────────────────────────────────────────────────────
 * 15. One campaign-level gate, from real API data
 * ──────────────────────────────────────────────────────────────────── */

{
  // ONE target: one approval action, no bulk step, version read off the row.
  const single = apiDetail({
    status: "awaiting_canary_approval",
    canary_size: 1,
    targets: [
      apiTarget({
        is_canary: true,
        status: "awaiting_canary_approval",
        bucket: "in_flight",
        source_publication_version: 1,
      }),
    ],
  });
  const gate = regenerationApprovalGate(single);
  assert.strictEqual(gate.singleTarget, true);
  assert.strictEqual(gate.showsBulkGenerationGate, false);
  assert.strictEqual(gate.remainingCount, 0);
  assert.strictEqual(gate.approveLabel, "Approve canary and publish V2");
  assert.ok(gate.canApprove);

  // Parity with the Task 4 gate for the same campaign expressed as fixtures:
  // one rule, two shapes, so the two can never drift apart.
  const equivalent: Campaign = {
    id: single.id,
    name: "single",
    status: "awaiting_approval",
    canarySize: 1,
    createdAt: "2026-08-20T09:00:00Z",
    estimate: { costLowUsd: 1.2, costHighUsd: 3, expectedModelCalls: 0 },
    targets: [
      {
        lessonId: "t-1",
        lessonTitle: "1-mavzu. Hujayra tuzilishi",
        language: "uz",
        sourceVersion: 1,
        nextVersion: 2,
      },
    ],
  };
  assert.deepStrictEqual(gate, approvalGate(equivalent));
}

{
  // A V2 source publishes V3 — the label is never hardcoded to V2.
  const gate = regenerationApprovalGate(
    apiDetail({
      status: "awaiting_canary_approval",
      canary_size: 1,
      targets: [apiTarget({ is_canary: true, source_publication_version: 2 })],
    }),
  );
  assert.strictEqual(gate.approveLabel, "Approve canary and publish V3");
}

{
  // Multi-target with a remainder: the bulk step exists and is described.
  const gate = regenerationApprovalGate(
    apiDetail({
      status: "awaiting_canary_approval",
      canary_size: 1,
      targets: [
        apiTarget({ id: "a", is_canary: true }),
        apiTarget({ id: "b" }),
        apiTarget({ id: "c" }),
      ],
    }),
  );
  assert.strictEqual(gate.singleTarget, false);
  assert.strictEqual(gate.showsBulkGenerationGate, true);
  assert.strictEqual(gate.remainingCount, 2);
  assert.match(gate.approveLabel, /2 remaining lessons/);
}

{
  // The canary already covers every lesson: no empty bulk gate, ever.
  const gate = regenerationApprovalGate(
    apiDetail({
      status: "awaiting_canary_approval",
      canary_size: 3,
      targets: [
        apiTarget({ id: "a", is_canary: true }),
        apiTarget({ id: "b", is_canary: true }),
        apiTarget({ id: "c", is_canary: true }),
      ],
    }),
  );
  assert.strictEqual(gate.showsBulkGenerationGate, false);
  assert.strictEqual(gate.remainingCount, 0);
}

// Approval copy: remaining lessons regenerate AND successful versions publish,
// automatically, with no second gate.
assert.match(REGENERATION_APPROVE_NOTE, /remaining/i);
assert.match(REGENERATION_APPROVE_NOTE, /automatic/i);
assert.match(REGENERATION_APPROVE_NOTE, /publish/i);
assert.match(REGENERATION_APPROVE_NOTE, /only|no per-lesson/i);

/* ────────────────────────────────────────────────────────────────────
 * 16. Per-target actions, pending state, and their promises
 * ──────────────────────────────────────────────────────────────────── */

{
  const genFailed = apiTarget({ status: "generation_failed", bucket: "generation_failed" });
  const kinds = regenerationTargetActions(genFailed).map((a) => a.kind);
  assert.deepStrictEqual(kinds, ["retry-generation", "abandon"]);
  assert.ok(regenerationTargetActions(genFailed).every((a) => a.enabled));
}

{
  const pubFailed = apiTarget({
    status: "publication_failed",
    bucket: "publication_failed",
    publication_state: "action_required",
  });
  const actions = regenerationTargetActions(pubFailed);
  assert.deepStrictEqual(
    actions.map((a) => a.kind),
    ["retry-publication", "abandon"],
  );
  const retry = actions[0];
  // The one promise an operator needs before clicking a retry on a $-per-call
  // system: this path never re-runs the model.
  assert.ok(
    retry.detail.includes("No Gemini call"),
    "publication retry must say, literally, that there is no Gemini call",
  );
  const abandon = actions[1];
  assert.strictEqual(abandon.requiresReason, true);
  assert.match(abandon.detail, /no Notion page is deleted/i);
  assert.match(abandon.detail, /reserved/i);
  assert.match(abandon.detail, /unused/i);
}

{
  // A terminal target offers nothing.
  const terminal = [
    apiTarget({ status: "published", bucket: "published", is_terminal: true }),
    apiTarget({ status: "abandoned", bucket: "abandoned", is_terminal: true }),
  ];
  for (const target of terminal) {
    assert.deepStrictEqual(regenerationTargetActions(target), [], target.status);
  }
}

{
  // A running target can still be abandoned, but never retried. Running means
  // it HAS a revision job — the jobless shape is a different state (§26).
  const running = apiTarget({
    status: "generating",
    bucket: "in_flight",
    revision_job_id: RUNNING_JOB_ID,
  });
  assert.deepStrictEqual(
    regenerationTargetActions(running).map((a) => a.kind),
    ["abandon"],
  );
}

{
  // Duplicate submits are disabled while a mutation is in flight — the button
  // is the guard, backend idempotency is the safety net.
  const pubFailed = apiTarget({
    status: "publication_failed",
    bucket: "publication_failed",
    publication_state: "action_required",
  });
  const pending = regenerationTargetActions(pubFailed, { pendingKind: "retry-publication" });
  assert.ok(
    pending.every((a) => !a.enabled),
    "no action on a target may be clickable while one of its mutations is pending",
  );
  assert.ok(pending.every((a) => (a.disabledReason ?? "").length > 0));
}

{
  // Nothing is actionable on a finished campaign.
  const pubFailed = apiTarget({
    status: "publication_failed",
    bucket: "publication_failed",
    publication_state: "action_required",
  });
  const done = regenerationTargetActions(pubFailed, { campaignTerminal: true });
  assert.ok(done.every((a) => !a.enabled));
}

// A blank abandon reason is refused before it reaches the API.
assert.ok(regenerationReasonError("") !== null);
assert.ok(regenerationReasonError("   ") !== null);
assert.strictEqual(regenerationReasonError("page collision, cleaning up by hand"), null);

// Reject and cancel both have to say what they do NOT do.
for (const [name, copy] of [
  ["reject", REGENERATION_REJECT_CONFIRMATION],
  ["cancel", REGENERATION_CANCEL_CONFIRMATION],
] as const) {
  assert.match(
    copy,
    /no existing Notion version is deleted/i,
    `${name} must say it deletes nothing`,
  );
}
assert.match(REGENERATION_REJECT_CONFIRMATION, /no publication version is consumed/i);
assert.match(REGENERATION_CANCEL_CONFIRMATION, /already published|stay published/i);

// Create/phase-plan/estimate are visibly free.
assert.match(REGENERATION_NO_SPEND_NOTE, /no model call/i);
assert.match(REGENERATION_NO_SPEND_NOTE, /no Notion page/i);
// And the launch is visibly not free.
assert.strictEqual(REGENERATION_LAUNCH_LABEL, "Generate canary");
assert.match(REGENERATION_LAUNCH_SPEND_NOTE, /spend|cost/i);
assert.match(REGENERATION_LAUNCH_SPEND_NOTE, /publish/i);

/* ────────────────────────────────────────────────────────────────────
 * 17. The cascade headline is the SERVER's plan, rendered
 * ──────────────────────────────────────────────────────────────────── */

/** `canonical_phases` starts with `extract` and partitions the whole snapshot. */
const CANONICAL = ["extract", ...CONTENT_PHASES];

function apiPlan(over: Partial<RegenerationApiPhasePlan> = {}): RegenerationApiPhasePlan {
  return {
    subject: "biology",
    canonical_phases: CANONICAL,
    selected_phases: [],
    auto_included_phases: [],
    regenerated_phases: [],
    copied_phases: CANONICAL,
    excluded_affected_phases: [],
    broken_dependency_edges: [],
    refresh_extraction: false,
    regenerated_phase_count: 0,
    copied_phase_count: CANONICAL.length,
    acknowledgement_required: false,
    acknowledgement_message: null,
    ...over,
  };
}

{
  // flashcards → 10 content phases; extract and case-based-preview are copied.
  const regenerated = CASCADE_FROM_FLASHCARDS;
  const plan = apiPlan({
    selected_phases: ["flashcards"],
    auto_included_phases: regenerated.filter((p) => p !== "flashcards"),
    regenerated_phases: regenerated,
    copied_phases: CANONICAL.filter((p) => !regenerated.includes(p)),
    regenerated_phase_count: regenerated.length,
    copied_phase_count: CANONICAL.length - regenerated.length,
  });
  const cascade = cascadeFromPlan(plan);
  assert.strictEqual(cascade.headline, "Regenerates 10 of 12 phases");
  // The client re-derivation must agree with the server's own counts, or the
  // headline is describing a plan the backend did not make.
  assert.strictEqual(cascade.regeneratedCount, plan.regenerated_phase_count);
  assert.strictEqual(cascade.copiedCount, plan.copied_phase_count);
  assert.strictEqual(cascade.scope, "near_full");
}

{
  // reflection regenerates only itself.
  const plan = apiPlan({
    selected_phases: ["reflection"],
    regenerated_phases: ["reflection"],
    copied_phases: CANONICAL.filter((p) => p !== "reflection"),
    regenerated_phase_count: 1,
    copied_phase_count: CANONICAL.length - 1,
  });
  const cascade = cascadeFromPlan(plan);
  assert.strictEqual(cascade.headline, "Regenerates 1 of 12 phases");
  assert.strictEqual(cascade.regeneratedCount, plan.regenerated_phase_count);
  assert.strictEqual(cascade.scope, "narrow");
}

{
  // Extraction on: the extract row itself is regenerated and every content
  // phase comes with it, so the count must include `extract`.
  const plan = apiPlan({
    selected_phases: [],
    auto_included_phases: CONTENT_PHASES,
    regenerated_phases: CANONICAL,
    copied_phases: [],
    refresh_extraction: true,
    regenerated_phase_count: CANONICAL.length,
    copied_phase_count: 0,
  });
  const cascade = cascadeFromPlan(plan);
  assert.strictEqual(cascade.regeneratedCount, plan.regenerated_phase_count);
  assert.strictEqual(cascade.copiedCount, 0);
  assert.strictEqual(cascade.headline, "Regenerates 12 of 12 phases");
}

{
  // An exclusion shrinks the regenerated set on BOTH sides of the wire.
  const regenerated = CASCADE_FROM_FLASHCARDS.filter((p) => p !== "reflection");
  const plan = apiPlan({
    selected_phases: ["flashcards"],
    auto_included_phases: CASCADE_FROM_FLASHCARDS.filter((p) => p !== "flashcards"),
    excluded_affected_phases: ["reflection"],
    regenerated_phases: regenerated,
    copied_phases: CANONICAL.filter((p) => !regenerated.includes(p)),
    regenerated_phase_count: regenerated.length,
    copied_phase_count: CANONICAL.length - regenerated.length,
    broken_dependency_edges: [{ upstream: "boss-arena", downstream: "reflection" }],
    acknowledgement_required: true,
    acknowledgement_message:
      "excluding these phases leaves them authored against an older upstream output",
  });
  const cascade = cascadeFromPlan(plan);
  assert.strictEqual(cascade.regeneratedCount, plan.regenerated_phase_count);
  assert.strictEqual(cascade.copiedCount, plan.copied_phase_count);
  // The warning names the excluded phase in operator words.
  const warning = exclusionWarning(phaseSelectionFromPlan(plan));
  assert.ok(warning !== null);
  assert.match(warning.message, /Reflection/);
}

/* ────────────────────────────────────────────────────────────────────
 * 18. Stranded release — approved, but the release never happened
 *
 * `approve` stamps `approved_at` in one transaction and creates the wave in
 * another, and `_prepare_wave` moves a target out of `planned` BEFORE its job
 * exists. So a target still `planned` with no `revision_job_id` on an approved
 * campaign means the release transaction never committed for it: nothing on
 * the server will ever start it, and the reconciler only walks jobs, so it
 * cannot repair a target that has none. The campaign nevertheless rolls up to
 * `bulk_running` (`planned` is an in-flight target status), which is exactly
 * the state that used to poll forever behind "still releasing revision jobs".
 * ──────────────────────────────────────────────────────────────────── */

const STRANDED_TARGET = apiTarget({
  id: "stranded-1",
  status: "planned",
  bucket: "in_flight",
  revision_job_id: null,
});

{
  // The exact review fixture: bulk_running + approved_at + a planned target
  // with no revision job. This must NOT poll.
  const detail = apiDetail({
    status: "bulk_running",
    approved_at: "2026-08-20T10:00:00Z",
    targets: [STRANDED_TARGET],
  });
  const decision = regenerationPollDecision(detail);
  assert.strictEqual(decision.shouldPoll, false, "a stranded release must stop polling");
  assert.strictEqual(decision.intervalMs, false);
  assert.ok(
    !/still releasing revision jobs/.test(decision.reason),
    "bulk_running must not be reported as active work when the release never landed",
  );
  assert.match(decision.reason, /releas/i);
  assert.match(decision.reason, /approv/i);
}

{
  // A PARTIAL release is the realistic shape of this failure, and the two
  // halves need different things: the lesson that started still moves on its
  // own, so the report must keep refreshing, while the stranded lesson needs a
  // human. Stopping the poll here froze the moving lesson's report; hiding the
  // stranded lesson behind it lost the recovery. Both are reported. The full
  // mixture — publication work, parked lessons, the note itself — is section 24.
  const decision = regenerationPollDecision(
    apiDetail({
      status: "bulk_running",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [
        STRANDED_TARGET,
        apiTarget({ id: "moving", status: "generating", revision_job_id: RUNNING_JOB_ID }),
      ],
    }),
  );
  assert.strictEqual(decision.shouldPoll, true);
  assert.match(decision.reason, /releas/i);
}

{
  // Normal bulk polling is untouched: a planned target that HAS its job is
  // simply scheduled by the stagger and will start on its own.
  const decision = regenerationPollDecision(
    apiDetail({
      status: "bulk_running",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [
        apiTarget({
          status: "planned",
          revision_job_id: "cafe0000-0000-4000-8000-000000000009",
          revision_job_scheduled_at: "2026-08-20T10:05:00Z",
        }),
      ],
    }),
  );
  assert.strictEqual(decision.shouldPoll, true, "a scheduled revision still moves on its own");
  assert.strictEqual(decision.intervalMs, REGENERATION_POLL_MS);
}

{
  // The pure predicate behind both the poll stop and the recovery action.
  const stranded = regenerationStrandedRelease(
    apiDetail({
      status: "bulk_running",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [STRANDED_TARGET],
    }),
  );
  assert.ok(stranded !== null, "an approved campaign with an unreleased target is stranded");
  assert.strictEqual(stranded.count, 1);
  assert.deepStrictEqual(stranded.targetIds, ["stranded-1"]);
  // M-6: the lesson is named, not its UUID.
  assert.ok(stranded.lines.some((line) => line.includes("1-mavzu. Hujayra tuzilishi")));
  assert.ok(!stranded.lines.some((line) => line.includes("stranded-1")));
  // It is a retry of the release, never a second approval.
  assert.ok(
    !/approve/i.test(stranded.actionLabel),
    `the recovery action must not read as an approval: ${stranded.actionLabel}`,
  );
  assert.match(stranded.actionLabel, /releas/i);
  assert.match(stranded.detail, /idempotent|creates nothing twice|nothing twice/i);
  assert.match(stranded.detail, /publish/i);
  assert.match(stranded.headline, /1 lesson/);
}

{
  // Two lessons can legitimately carry the SAME title in one campaign — two
  // books, or a repeated "Kirish" — so the rendered list must key on the
  // target id. Keying on the text would collide and drop a row.
  const stranded = regenerationStrandedRelease(
    apiDetail({
      status: "bulk_running",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [
        apiTarget({ id: "twin-a", status: "planned", revision_job_id: null }),
        apiTarget({ id: "twin-b", status: "planned", revision_job_id: null }),
      ],
    }),
  );
  assert.ok(stranded !== null);
  assert.strictEqual(stranded.count, 2);
  assert.strictEqual(stranded.lines[0], stranded.lines[1], "the fixture really is a duplicate");
  assert.deepStrictEqual(
    stranded.rows.map((r) => r.targetId),
    ["twin-a", "twin-b"],
  );
  assert.strictEqual(new Set(stranded.rows.map((r) => r.targetId)).size, 2, "keys must be unique");
  assert.deepStrictEqual(
    stranded.rows.map((r) => r.text),
    stranded.lines,
    "rows and lines must say the same thing",
  );
  assert.match(stranded.headline, /2 lessons were/);
}

// Not stranded: nothing is approved yet, the job exists, the campaign is
// finished, or the operator already asked to stop.
{
  const withJob = apiTarget({ revision_job_id: "cafe0000-0000-4000-8000-000000000009" });
  assert.strictEqual(regenerationStrandedRelease(undefined), null);
  assert.strictEqual(
    regenerationStrandedRelease(apiDetail({ status: "draft", targets: [STRANDED_TARGET] })),
    null,
    "an unapproved campaign has nothing to re-release",
  );
  assert.strictEqual(
    regenerationStrandedRelease(
      apiDetail({
        status: "bulk_running",
        approved_at: "2026-08-20T10:00:00Z",
        targets: [withJob],
      }),
    ),
    null,
  );
  assert.strictEqual(
    regenerationStrandedRelease(
      apiDetail({
        status: "cancelled",
        is_terminal: true,
        approved_at: "2026-08-20T10:00:00Z",
        targets: [STRANDED_TARGET],
      }),
    ),
    null,
    "a finished campaign can no longer be released",
  );
  assert.strictEqual(
    regenerationStrandedRelease(
      apiDetail({
        status: "attention_required",
        approved_at: "2026-08-20T10:00:00Z",
        cancel_requested_at: "2026-08-20T10:30:00Z",
        targets: [STRANDED_TARGET],
      }),
    ),
    null,
    "re-releasing a cancelling campaign would fight the cancellation",
  );
  assert.strictEqual(
    regenerationStrandedRelease(
      apiDetail({
        status: "bulk_running",
        approved_at: "2026-08-20T10:00:00Z",
        targets: [
          apiTarget({
            status: "planned",
            revision_job_id: null,
            abandon_requested_at: "2026-08-20T10:30:00Z",
          }),
        ],
      }),
    ),
    null,
    "the release skips a target the operator asked to abandon, so it is not recoverable",
  );
}

/* ────────────────────────────────────────────────────────────────────
 * 19. Release failures survive the refresh that clears them (M-1, M-6)
 * ──────────────────────────────────────────────────────────────────── */

{
  const failure = {
    target_id: "stranded-1",
    source_job_id: null,
    reason: "revision job could not be created: incomplete snapshot",
    current_status: "generation_failed" as const,
  };
  // GET /campaigns/{id} never carries released_failures, so a plain refetch
  // would erase the only record of the wave that failed.
  assert.deepStrictEqual(mergeReleasedFailures([], [failure]), [failure]);
  assert.deepStrictEqual(mergeReleasedFailures(undefined, undefined), []);
  // The server's own copy wins when both have the same target.
  const server = { ...failure, reason: "server copy" };
  assert.deepStrictEqual(mergeReleasedFailures([server], [failure]), [server]);
  // Two different targets are both kept.
  const other = { ...failure, target_id: "other-1", reason: "second" };
  assert.strictEqual(mergeReleasedFailures([server], [other]).length, 2);

  // M-6: the lines name lessons, and fall back to the id only when the report
  // does not carry that target.
  const detail = apiDetail({
    status: "bulk_running",
    approved_at: "2026-08-20T10:00:00Z",
    targets: [STRANDED_TARGET],
  });
  const lines = regenerationReleasedFailureLines(detail, [failure, other]);
  assert.strictEqual(lines.length, 2);
  assert.ok(lines[0].text.includes("1-mavzu. Hujayra tuzilishi"));
  assert.ok(!lines[0].text.includes("stranded-1"));
  assert.ok(lines[0].text.includes("incomplete snapshot"));
  // The read-back status is rendered as words, never as a raw token.
  assert.ok(!lines[0].text.includes("generation_failed"));
  assert.ok(lines[1].text.includes("other-1"), "an unknown target still identifies itself");
  assert.strictEqual(lines[0].targetId, "stranded-1");
}

/* ────────────────────────────────────────────────────────────────────
 * 20. Retry keeps the audit context the backend cleared (M-2)
 * ──────────────────────────────────────────────────────────────────── */

{
  const audit = regenerationRetryAudit({
    target: apiTarget({ status: "publication_pending", publication_attempts: 0 }),
    campaign_id: CAMPAIGN_ID,
    campaign_status: "bulk_running",
    released_failures: [],
    previous_publication_error: "VersionPageCollision: Homework V2 exists without a marker",
    previous_publication_attempts: 3,
    previous_publication_next_attempt_at: "2026-08-20T11:00:00Z",
  });
  assert.ok(audit !== null, "a retry that cleared a real error must say what it cleared");
  assert.ok(audit.includes("VersionPageCollision"));
  assert.match(audit, /3/);
  assert.match(audit, /attempt/i);
  // Nothing preserved (retry-generation, or a first attempt) — nothing to say.
  assert.strictEqual(
    regenerationRetryAudit({
      target: apiTarget(),
      campaign_id: CAMPAIGN_ID,
      campaign_status: "bulk_running",
      released_failures: [],
      previous_publication_error: null,
      previous_publication_attempts: null,
      previous_publication_next_attempt_at: null,
    }),
    null,
  );
  assert.strictEqual(regenerationRetryAudit(null), null);
  // A campaign whose row vanished answers "unknown" — the renderer must cope.
  assert.ok(!regenerationCampaignStatusLabel("unknown").includes("_"));
  assert.ok(regenerationCampaignStatusLabel("unknown").length > 0);
}

/* ────────────────────────────────────────────────────────────────────
 * 21. Canary size is clamped in STATE, not only on screen (M-3)
 * ──────────────────────────────────────────────────────────────────── */

assert.strictEqual(clampCanarySize(5, 3), 3, "a canary can never exceed the campaign");
assert.strictEqual(clampCanarySize(0, 3), 1);
assert.strictEqual(clampCanarySize(-2, 3), 1);
assert.strictEqual(clampCanarySize(2, 3), 2);
assert.strictEqual(clampCanarySize(2.7, 3), 2, "canary_size is an integer count");
assert.strictEqual(clampCanarySize(Number.NaN, 3), 1);
assert.strictEqual(clampCanarySize(4, 0), 1, "with no lessons the canary is still a legal 1");

/* ────────────────────────────────────────────────────────────────────
 * 22. Bounded, unambiguous lesson discovery (I-3)
 * ──────────────────────────────────────────────────────────────────── */

function apiBook(over: Partial<Book> = {}): Book {
  return {
    id: "aaaabbbb-cccc-dddd-eeee-ffff00001111",
    subject: "biology" as Book["subject"],
    grade: "8",
    original_filename: "biologiya_8_sinf.pdf",
    source_language: "uz",
    status: "toc_ready" as Book["status"],
    error_message: null,
    gemini_file_expires_at: null,
    file_size_bytes: 1024,
    created_at: "2026-08-01T00:00:00Z",
    toc: null,
    ...over,
  };
}

{
  // The eligible query is bounded by ONE book and is disabled until there is
  // one: `/eligible` unfiltered walks every lesson lineage in the database.
  const blocked = regenerationEligibleQuery(null);
  assert.strictEqual(blocked.enabled, false);
  assert.deepStrictEqual(blocked.filters.bookIds, []);
  assert.ok((blocked.blockedReason ?? "").length > 0);
  assert.strictEqual(regenerationEligibleQuery("").enabled, false, "an empty id is not a book");

  const ready = regenerationEligibleQuery("aaaabbbb-cccc-dddd-eeee-ffff00001111");
  assert.strictEqual(ready.enabled, true);
  assert.deepStrictEqual(ready.filters.bookIds, ["aaaabbbb-cccc-dddd-eeee-ffff00001111"]);
  assert.strictEqual(ready.blockedReason, null);
}

{
  const books = [
    apiBook(),
    apiBook({ id: "book-2", grade: "9", original_filename: "biologiya_9_sinf.pdf" }),
    apiBook({ id: "book-3", subject: "english" as Book["subject"], grade: "8" }),
    // Real rows in this database are missing a grade, and a title can be blank.
    apiBook({ id: "book-4", grade: null, original_filename: "" }),
  ];
  const all = regenerationBookOptions(books);
  assert.strictEqual(all.length, 4);
  const untitled = all.find((b) => b.id === "book-4");
  assert.ok(untitled !== undefined);
  assert.ok(untitled.title.length > 0, "a book with no filename still identifies itself");
  assert.ok(
    untitled.gradeLabel.length > 0,
    "a book with no grade says so rather than showing null",
  );
  assert.ok(!untitled.gradeLabel.includes("null"));
  assert.ok(untitled.label.includes(untitled.title));

  // Subject and grade narrowing, over the ~246-row books list rather than
  // over every lesson lineage.
  assert.strictEqual(regenerationBookOptions(books, { subject: "biology" }).length, 3);
  assert.strictEqual(regenerationBookOptions(books, { subject: "biology", grade: "8" }).length, 1);
  assert.strictEqual(regenerationBookOptions(undefined).length, 0);

  const facets = regenerationBookFacets(books, { subject: "biology" });
  assert.deepStrictEqual(facets.subjects.map((s) => s.value).sort(), ["biology", "english"]);
  assert.ok(facets.subjects.every((s) => !s.label.includes("_") && s.label.length > 0));
  // Grades are scoped to the chosen subject, and the missing-grade bucket is
  // offered rather than silently dropping those books.
  assert.deepStrictEqual(facets.grades.map((g) => g.value).sort(), ["", "8", "9"]);
  assert.strictEqual(facets.subjects.find((s) => s.value === "biology")?.count, 3);
}

{
  // Narrowing must clear whatever it invalidates, and nothing else.
  const scope = {
    subjectFilter: "biology" as string | null,
    gradeFilter: "8" as string | null,
    bookId: "book-1" as string | null,
    language: "uz" as const,
    selectedTocEntryIds: ["toc-1", "toc-2"],
    selectedPhases: ["flashcards"],
    excludedPhases: ["reflection"],
    acknowledged: true,
    canarySize: 2,
    provider: "gemini",
  };

  const bySubject = regenerationNarrowScope(scope, { subjectFilter: "english" });
  assert.strictEqual(bySubject.subjectFilter, "english");
  assert.strictEqual(bySubject.gradeFilter, null, "a grade from another subject is meaningless");
  assert.strictEqual(bySubject.bookId, null);
  assert.deepStrictEqual(bySubject.selectedTocEntryIds, []);
  assert.deepStrictEqual(bySubject.selectedPhases, [], "the phase flow is per subject");
  assert.deepStrictEqual(bySubject.excludedPhases, []);
  assert.strictEqual(bySubject.acknowledged, false);
  assert.strictEqual(bySubject.canarySize, 1);
  assert.strictEqual(bySubject.provider, "gemini", "unrelated draft fields survive");

  const byGrade = regenerationNarrowScope(scope, { gradeFilter: "9" });
  assert.strictEqual(byGrade.bookId, null);
  assert.deepStrictEqual(byGrade.selectedTocEntryIds, []);
  assert.deepStrictEqual(byGrade.selectedPhases, ["flashcards"], "same subject, same flow");

  const byBook = regenerationNarrowScope(scope, { bookId: "book-2" });
  assert.strictEqual(byBook.bookId, "book-2");
  assert.deepStrictEqual(byBook.selectedTocEntryIds, [], "lessons belong to the old book");
  assert.deepStrictEqual(byBook.selectedPhases, [], "another book can be another subject");
  assert.strictEqual(byBook.acknowledged, false);

  const byLanguage = regenerationNarrowScope(scope, { language: "ru" });
  assert.strictEqual(byLanguage.language, "ru");
  assert.strictEqual(byLanguage.bookId, "book-1", "a book carries every language");
  assert.deepStrictEqual(byLanguage.selectedTocEntryIds, []);

  // Re-picking the same value changes nothing at all.
  assert.deepStrictEqual(regenerationNarrowScope(scope, { bookId: "book-1" }), scope);
  assert.deepStrictEqual(regenerationNarrowScope(scope, {}), scope);
}

{
  // Every duplicate-title lesson must be distinguishable on screen: two
  // "Kirish" rows from different books/chapters/languages are a real shape in
  // this database.
  const source1: RegenerationEligibleSource = {
    toc_entry_id: "toc-1",
    output_language: "uz",
    source_job_id: "job-1",
    book_id: "aaaabbbb-cccc-dddd-eeee-ffff00001111",
    subject: "biology",
    grade: "8",
    source_publication_version: 1,
    next_expected_version: 2,
    source_is_revision: false,
    section_number: "1",
    section_title: "Kirish",
    chapter_title: "Hujayra",
    order_index: 1,
    has_notion_lesson_page: true,
  };
  const row = regenerationSourceRow(source1, regenerationBookOptions([apiBook()])[0]);
  assert.strictEqual(row.headline, "1. Kirish", "the section number disambiguates the title");
  for (const needle of [
    "Kirish", // section title
    "Hujayra", // chapter title
    "Biology", // subject, as a label
    "Grade 8", // grade, in words
    "biologiya_8_sinf.pdf", // book identity
    "Uzbek", // output language
    "V1", // source version
    "V2", // the version this will publish as
  ]) {
    assert.ok(
      row.searchText.includes(needle),
      `a lesson row must show ${needle} so duplicate titles cannot be confused`,
    );
  }
  assert.strictEqual(row.key, "toc-1:uz");
  assert.strictEqual(row.noPageWarning, null);

  // A second "Kirish", other book, other chapter, other language: every line
  // that distinguishes them differs.
  const source2: RegenerationEligibleSource = {
    ...source1,
    toc_entry_id: "toc-2",
    output_language: "ru",
    book_id: "book-2",
    grade: "9",
    section_number: null,
    chapter_title: "Введение",
    source_publication_version: 2,
    next_expected_version: 3,
    source_is_revision: true,
    has_notion_lesson_page: false,
  };
  const other = regenerationSourceRow(
    source2,
    regenerationBookOptions([
      apiBook({ id: "book-2", grade: "9", original_filename: "b9.pdf" }),
    ])[0],
  );
  assert.notStrictEqual(other.key, row.key);
  assert.notStrictEqual(other.contextLine, row.contextLine);
  assert.notStrictEqual(other.bookLine, row.bookLine);
  assert.ok(other.searchText.includes("V2"));
  assert.ok(other.searchText.includes("V3"));
  assert.ok(other.searchText.includes("Russian"));
  assert.ok((other.noPageWarning ?? "").length > 0, "no Notion page yet must stay visible");
  // A source that is itself a revision is stated, not implied.
  assert.match(other.contextLine, /regenerat|revision/i);

  // A book the list never returned still renders an honest row.
  const orphan = regenerationSourceRow(source1, undefined);
  assert.ok(orphan.bookLine.length > 0);
  assert.ok(!orphan.bookLine.includes("undefined"));
}

/* ────────────────────────────────────────────────────────────────────
 * 23. Structural guards for the surfaces no DOM test can reach
 * ──────────────────────────────────────────────────────────────────── */

{
  // I-3: the page must never ask for every lineage in the database on mount.
  assert.ok(
    !/listRegenerationEligible\(\s*\)/.test(routeSrc),
    "the route must never call /eligible unfiltered",
  );
  assert.ok(
    routeSrc.includes("regenerationEligibleQuery"),
    "the eligible query must be gated by regenerationEligibleQuery()",
  );
  assert.ok(routeSrc.includes("api.listBooks"), "book selection is the bounded first step");
  assert.ok(
    /enabled:\s*eligibleQuery\.enabled/.test(routeSrc),
    "the eligible query must be disabled until a book is picked",
  );
  assert.ok(
    routeSrc.includes("mergeReleasedFailures"),
    "M-1: mutation-only release failures must survive the next poll",
  );
  assert.ok(
    routeSrc.includes("regenerationRetryAudit"),
    "M-2: the retry must surface the audit context the backend cleared",
  );

  // I-2: the recovery action renders for the stranded state, reuses approve,
  // and never offers Reject as though the canary were still under review.
  assert.ok(
    canarySrc.includes("regenerationStrandedRelease"),
    "canary-review must render the stranded-release recovery action",
  );
  const strandedAt = canarySrc.indexOf("stranded && (");
  assert.ok(strandedAt > 0, "the recovery section must be gated on the stranded predicate");
  const strandedBlock = canarySrc.slice(strandedAt);
  assert.ok(
    !/eject/i.test(strandedBlock),
    "the recovery section must not offer Reject: this is not the canary gate",
  );
  assert.ok(
    strandedBlock.includes("onApprove"),
    "the recovery action must reuse the idempotent approve mutation",
  );
  assert.ok(
    strandedBlock.includes("stranded.actionLabel"),
    "the recovery button must use the retry-the-release label, not an approval label",
  );
  assert.ok(
    canarySrc.indexOf("atGate &&") < strandedAt,
    "the canary gate stays first; the recovery section is a separate, later block",
  );

  // I-3 rendering: identity comes from the tested helper, not from inline JSX.
  assert.ok(
    wizardSrc.includes("regenerationSourceRow"),
    "wizard lesson rows must render the tested identity helper",
  );
  assert.ok(
    wizardSrc.includes("regenerationBookOptions"),
    "wizard must narrow books before asking for lessons",
  );
  assert.ok(
    wizardSrc.includes("regenerationNarrowScope"),
    "changing subject/grade/book must clear stale selections through the tested helper",
  );
  assert.ok(
    wizardSrc.includes("clampCanarySize"),
    "M-3: the canary size must be clamped in state before it is posted",
  );

  // M-4: every free-text reason input needs a name a screen reader can read.
  for (const rel of [
    "../components/regeneration/canary-review.tsx",
    "../components/regeneration/campaign-report.tsx",
    "../components/regeneration/regeneration-wizard.tsx",
  ]) {
    const src = source(rel);
    for (const tag of src.match(/<input[\s\S]*?\/>/g) ?? []) {
      if (!/type="text"/.test(tag)) continue;
      assert.ok(
        /aria-label=/.test(tag),
        `${rel} has an unlabelled reason input: ${tag.slice(0, 90)}`,
      );
    }
  }

  // M-5: a campaign row that vanished answers "unknown"; the type must say so.
  assert.ok(
    /campaign_status:\s*RegenerationCampaignStatus \| "unknown"/.test(source("./types.ts")),
    'TargetActionOut.campaign_status must admit the backend\'s "unknown"',
  );
}

/* ────────────────────────────────────────────────────────────────────
 * 24. Honest failure states (re-review I-1, I-2, I-3 and the minors)
 *
 * Every branch below is a lie the shell told before this section existed: a
 * failed campaign list read as "no campaigns yet", a campaign that had not
 * loaded yet read as "nothing selected", and one campaign's refusal was
 * rendered against a different campaign entirely.
 * ──────────────────────────────────────────────────────────────────── */

/* ── I-1: the campaign list distinguishes error, loading and empty ──── */
{
  const loading = regenerationCampaignListView({
    campaigns: undefined,
    isLoading: true,
    error: null,
  });
  assert.strictEqual(loading.mode, "loading");
  assert.strictEqual(loading.message, REGENERATION_LIST_LOADING);
  assert.strictEqual(loading.error, null);

  // Genuinely empty: the server answered, with nothing in it.
  const empty = regenerationCampaignListView({ campaigns: [], isLoading: false, error: null });
  assert.strictEqual(empty.mode, "empty");
  assert.strictEqual(empty.message, REGENERATION_LIST_EMPTY);
  assert.strictEqual(empty.error, null);

  // A FAILED list is not an empty list. This is the whole finding: a 500 used
  // to render as "No regeneration campaigns yet."
  const boom = new ApiError(500, "the rollup blew up", {
    error: "server_error",
    message: "the rollup blew up",
  });
  const failed = regenerationCampaignListView({
    campaigns: undefined,
    isLoading: false,
    error: boom,
  });
  assert.strictEqual(failed.mode, "error");
  assert.strictEqual(failed.message, null, "a failed list must not claim there are no campaigns");
  assert.ok(failed.error !== null);
  assert.strictEqual(failed.error?.message, "the rollup blew up");

  // The feature-off 404 must be readable from the PRIMARY list — the campaigns
  // query is unconditional, so no book has to be picked to see it.
  const off = regenerationCampaignListView({
    campaigns: undefined,
    isLoading: false,
    error: new ApiError(404, "Not Found", "Not Found"),
  });
  assert.strictEqual(off.mode, "error");
  assert.strictEqual(off.message, null);
  assert.match(off.error?.title ?? "", /switched off/i);
  assert.match(off.error?.message ?? "", /REGENERATION_ENABLED/);
  assert.ok(!/404/.test(off.error?.message ?? ""), "no bare status code for an operator");

  // Rows the cache still holds stay readable BESIDE the error.
  const stale = regenerationCampaignListView({
    campaigns: [apiDetail()],
    isLoading: false,
    error: boom,
  });
  assert.strictEqual(stale.mode, "error");
  assert.strictEqual(stale.campaigns.length, 1, "a failed refresh must not hide known campaigns");

  // A background refresh over existing rows is neither empty nor loading prose.
  const refreshing = regenerationCampaignListView({
    campaigns: [apiDetail()],
    isLoading: true,
    error: null,
  });
  assert.strictEqual(refreshing.mode, "list");
  assert.strictEqual(refreshing.message, null);
}

/* ── minor 3: 404 prose — switched off vs genuinely missing ────────── */
{
  // FastAPI's flag-off guard raises the literal string "Not Found".
  assert.match(
    regenerationErrorView(new ApiError(404, "Not Found", "Not Found")).title,
    /switched off/i,
  );

  // A campaign that was deleted raises its own sentence, and it must survive.
  const missing = "regeneration campaign 6f1c1d4e-9a2b-4c3d-8e5f-0a1b2c3d4e5f not found";
  const gone = regenerationErrorView(new ApiError(404, missing, missing));
  assert.ok(
    !/switched off/i.test(gone.title),
    "a deleted campaign must not be reported as a disabled feature",
  );
  assert.strictEqual(gone.status, 404);
  assert.strictEqual(gone.message, missing, "the server's own not-found sentence must render");

  const missingTarget = "regeneration target 11112222-3333-4444-5555-666677778888 not found";
  const goneTarget = regenerationErrorView(new ApiError(404, missingTarget, missingTarget));
  assert.ok(!/switched off/i.test(goneTarget.title));
  assert.strictEqual(goneTarget.message, missingTarget);
}

/* ── I-2: the detail pane never says "pick one" while one is loading ── */
{
  const idle = regenerationDetailView({ selectedId: null, data: undefined, error: null });
  assert.strictEqual(idle.mode, "idle");
  assert.strictEqual(idle.message, REGENERATION_DETAIL_IDLE);
  assert.strictEqual(idle.detail, null);

  // Selected, first load in flight: a loading state, NOT the idle prose.
  const first = regenerationDetailView({ selectedId: CAMPAIGN_ID, data: undefined, error: null });
  assert.strictEqual(first.mode, "loading");
  assert.strictEqual(first.message, REGENERATION_DETAIL_LOADING);
  assert.notStrictEqual(
    first.message,
    REGENERATION_DETAIL_IDLE,
    'a campaign that is loading must never be reported as "pick a campaign"',
  );

  // Selected, first load failed: the refusal, not the idle prose.
  const failed = regenerationDetailView({
    selectedId: CAMPAIGN_ID,
    data: undefined,
    error: new ApiError(409, "moved on", {
      error: "illegal_campaign_state",
      message: "moved on",
    }),
  });
  assert.strictEqual(failed.mode, "error");
  assert.strictEqual(failed.message, null);
  assert.strictEqual(failed.detail, null);
  assert.ok(failed.error !== null);
  assert.strictEqual(failed.error?.message, "moved on");

  const ready = regenerationDetailView({
    selectedId: CAMPAIGN_ID,
    data: apiDetail(),
    error: null,
  });
  assert.strictEqual(ready.mode, "ready");
  assert.strictEqual(ready.detail?.id, CAMPAIGN_ID);
  assert.strictEqual(ready.error, null);

  // Previous data plus a failed refresh: keep the report AND state the failure.
  const refreshFailed = regenerationDetailView({
    selectedId: CAMPAIGN_ID,
    data: apiDetail(),
    error: new ApiError(500, "boom"),
  });
  assert.strictEqual(refreshFailed.mode, "ready");
  assert.ok(refreshFailed.detail !== null, "cached data stays readable through a failed refresh");
  assert.ok(refreshFailed.error !== null, "and the failed refresh is still reported");

  // Cached data for ANOTHER campaign is not this campaign's report.
  const other = regenerationDetailView({
    selectedId: "aaaa1111-2222-3333-4444-555566667777",
    data: apiDetail(),
    error: null,
  });
  assert.strictEqual(other.mode, "loading");
  assert.strictEqual(other.detail, null, "one campaign's report must never render under another");
}

/* ── I-3: a mutation error belongs to the campaign/target that caused it ── */
{
  const OTHER_CAMPAIGN = "aaaa1111-2222-3333-4444-555566667777";
  const refusal = new ApiError(409, "not from here", {
    error: "illegal_campaign_state",
    message: "not from here",
  });

  // launchCanary / approve are called with the campaign id itself.
  const ownsCampaign = (id: string) => id === CAMPAIGN_ID;
  const mine = regenerationMutationView(
    { error: refusal, variables: CAMPAIGN_ID, isPending: false },
    ownsCampaign,
  );
  assert.ok(mine.error !== null, "the campaign that failed must show its own refusal");
  assert.strictEqual(mine.error?.message, "not from here");
  const theirs = regenerationMutationView(
    { error: refusal, variables: OTHER_CAMPAIGN, isPending: false },
    ownsCampaign,
  );
  assert.strictEqual(theirs.error, null, "campaign B must not show campaign A's refusal");

  // reject / cancel carry {campaignId, reason}.
  const ownsReason = (v: { campaignId: string }) => v.campaignId === CAMPAIGN_ID;
  assert.ok(
    regenerationMutationView(
      { error: refusal, variables: { campaignId: CAMPAIGN_ID, reason: "wrong tone" } },
      ownsReason,
    ).error !== null,
  );
  assert.strictEqual(
    regenerationMutationView(
      { error: refusal, variables: { campaignId: OTHER_CAMPAIGN, reason: "wrong tone" } },
      ownsReason,
    ).error,
    null,
  );

  // Target actions carry {kind, target, reason} — the preflight/illegal-state
  // refusal belongs to ONE lesson row.
  const failing = apiTarget({ id: "t-failing" });
  const targetVars = { kind: "retry-publication" as const, target: failing, reason: "" };
  assert.ok(
    regenerationMutationView(
      { error: refusal, variables: targetVars },
      (v) => v.target.id === "t-failing",
    ).error !== null,
  );
  assert.strictEqual(
    regenerationMutationView(
      { error: refusal, variables: targetVars },
      (v) => v.target.id === "t-other",
    ).error,
    null,
    "a target refusal must not render on a different lesson",
  );
  // And it does not leak onto a different campaign's report either.
  assert.strictEqual(
    regenerationMutationView(
      { error: refusal, variables: targetVars },
      (v) => v.target.campaign_id === OTHER_CAMPAIGN,
    ).error,
    null,
  );

  // Pending is scoped the same way: campaign B must not render "Approving…"
  // because campaign A is mid-flight.
  assert.strictEqual(
    regenerationMutationView(
      { error: null, variables: OTHER_CAMPAIGN, isPending: true },
      ownsCampaign,
    ).pending,
    false,
  );
  assert.strictEqual(
    regenerationMutationView({ error: null, variables: CAMPAIGN_ID, isPending: true }, ownsCampaign)
      .pending,
    true,
  );

  // A mutation that never ran owns nothing at all.
  assert.deepStrictEqual(
    regenerationMutationView(
      { error: refusal, variables: undefined, isPending: false },
      () => true,
    ),
    { pending: false, error: null },
  );
}

/* ── minor 1: a stranded release beside real work keeps polling ─────── */
{
  // Stranded ALONE: nothing self-moving is left, so stop and say what fixes it.
  const alone = regenerationPollDecision(
    apiDetail({
      status: "bulk_running",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [STRANDED_TARGET],
    }),
  );
  assert.strictEqual(alone.shouldPoll, false);
  assert.strictEqual(alone.intervalMs, false);
  assert.ok(alone.strandedNote !== null);

  // Stranded BESIDE a lesson that really is regenerating: the moving lesson
  // still moves, so the report must keep refreshing — and must say both things.
  const mixed = regenerationPollDecision(
    apiDetail({
      status: "bulk_running",
      approved_at: "2026-08-20T10:00:00Z",
      targets: [
        STRANDED_TARGET,
        apiTarget({ id: "moving", status: "generating", revision_job_id: RUNNING_JOB_ID }),
      ],
    }),
  );
  assert.strictEqual(mixed.shouldPoll, true, "real in-flight work must keep the report refreshing");
  assert.strictEqual(mixed.intervalMs, REGENERATION_POLL_MS);
  assert.ok(
    mixed.activity.some((a) => /regenerating/i.test(a)),
    "the lesson that is moving must be named as activity",
  );
  assert.match(mixed.reason, /approved but never/i, "the stranded lesson must still be mentioned");
  assert.ok(mixed.strandedNote !== null);
  assert.match(mixed.strandedNote ?? "", /Retry the release/);
  assert.ok(
    !/still releasing revision jobs/.test(mixed.reason),
    "a campaign with a stranded lesson is not 'still releasing'",
  );

  // Same for publication work, which the publisher moves without an operator.
  assert.strictEqual(
    regenerationPollDecision(
      apiDetail({
        status: "bulk_running",
        approved_at: "2026-08-20T10:00:00Z",
        targets: [
          STRANDED_TARGET,
          apiTarget({
            id: "pub",
            status: "publishing",
            bucket: "in_flight",
            publication_state: "publishing",
            revision_job_id: "cafe0000-0000-4000-8000-000000000001",
          }),
        ],
      }),
    ).shouldPoll,
    true,
  );

  // A lesson parked on a human is NOT self-moving work, so the stranded stop
  // still wins there.
  assert.strictEqual(
    regenerationPollDecision(
      apiDetail({
        status: "bulk_running",
        approved_at: "2026-08-20T10:00:00Z",
        targets: [
          STRANDED_TARGET,
          apiTarget({
            id: "parked",
            status: "generation_failed",
            bucket: "generation_failed",
            action_required: true,
          }),
        ],
      }),
    ).shouldPoll,
    false,
  );

  // No stranded lesson, no note.
  assert.strictEqual(regenerationPollDecision(apiDetail({ status: "draft" })).strandedNote, null);
}

/* ── minor 2: the 10s list tick stops for a stranded bulk campaign ──── */
{
  const strandedSummary = apiDetail({
    status: "bulk_running",
    approved_at: "2026-08-20T10:00:00Z",
    targets: [STRANDED_TARGET],
  });
  assert.strictEqual(
    regenerationListPollMs([strandedSummary]),
    false,
    "an approved bulk campaign with only unreleased lessons cannot be fixed by a ticker",
  );

  for (const active of ["generating", "publication_pending", "publishing"] as const) {
    assert.strictEqual(
      regenerationListPollMs([
        apiDetail({
          status: "bulk_running",
          approved_at: "2026-08-20T10:00:00Z",
          targets: [STRANDED_TARGET, apiTarget({ id: "moving", status: active })],
        }),
      ]),
      REGENERATION_LIST_POLL_MS,
      `${active} work must keep the list ticking`,
    );
  }

  // Never approved: not stranded, and the release may still be landing.
  assert.strictEqual(
    regenerationListPollMs([
      apiDetail({ status: "bulk_running", approved_at: null, targets: [STRANDED_TARGET] }),
    ]),
    REGENERATION_LIST_POLL_MS,
  );
  // The canary phase is untouched by this rule.
  assert.strictEqual(
    regenerationListPollMs([apiDetail({ status: "canary_running" })]),
    REGENERATION_LIST_POLL_MS,
  );
}

/* ── minor 7: list keys survive duplicate text ─────────────────────── */
{
  const rows = regenerationKeyedLines(["Kirish", "Kirish", "Boshqa"]);
  assert.strictEqual(rows.length, 3, "a duplicate line must not be dropped");
  assert.strictEqual(new Set(rows.map((r) => r.key)).size, 3, "duplicate text, distinct keys");
  assert.deepStrictEqual(
    rows.map((r) => r.text),
    ["Kirish", "Kirish", "Boshqa"],
  );
  // Stable for the same input, so React does not remount every refresh.
  assert.deepStrictEqual(regenerationKeyedLines(["a", "b"]), regenerationKeyedLines(["a", "b"]));
  assert.deepStrictEqual(regenerationKeyedLines(undefined), []);
  assert.deepStrictEqual(regenerationKeyedLines([]), []);
}

/* ── minor 6: deselecting a lesson re-clamps the STORED canary size ─── */
{
  const scope = {
    subjectFilter: null,
    gradeFilter: null,
    bookId: "book-1",
    language: "uz" as const,
    selectedTocEntryIds: ["a", "b", "c"],
    selectedPhases: ["flashcards"],
    excludedPhases: ["reflection"],
    acknowledged: true,
    canarySize: 3,
    refreshExtraction: true,
  };

  const shrunk = regenerationToggleLesson(scope, "c");
  assert.deepStrictEqual(shrunk.selectedTocEntryIds, ["a", "b"]);
  assert.strictEqual(shrunk.canarySize, 2, "the stored canary must shrink with the selection");
  // Everything the operator composed elsewhere survives.
  assert.strictEqual(shrunk.refreshExtraction, true);
  assert.deepStrictEqual(shrunk.selectedPhases, ["flashcards"]);
  assert.deepStrictEqual(shrunk.excludedPhases, ["reflection"]);
  assert.strictEqual(shrunk.acknowledged, true);
  assert.strictEqual(shrunk.bookId, "book-1");

  // Re-selecting does not re-inflate a canary the operator sized down.
  const grown = regenerationToggleLesson(shrunk, "c");
  assert.deepStrictEqual(grown.selectedTocEntryIds, ["a", "b", "c"]);
  assert.strictEqual(grown.canarySize, 2);

  // Emptying the selection floors at 1 — the server refuses canary_size < 1.
  const none = regenerationToggleLesson(regenerationToggleLesson(shrunk, "b"), "a");
  assert.deepStrictEqual(none.selectedTocEntryIds, []);
  assert.strictEqual(none.canarySize, 1);

  // Pure: the caller's state object is never mutated.
  assert.deepStrictEqual(scope.selectedTocEntryIds, ["a", "b", "c"]);
  assert.strictEqual(scope.canarySize, 3);
}

/* ────────────────────────────────────────────────────────────────────
 * 25. Structural guards for the honest-failure wiring
 * ──────────────────────────────────────────────────────────────────── */

// I-1 — the list decides through the tested view and owns no prose.
assert.ok(
  listSrc.includes("regenerationCampaignListView"),
  "campaign-list must decide loading/empty/error through the tested view",
);
assert.ok(
  !listSrc.includes("No regeneration campaigns yet"),
  "the empty-state sentence belongs to the tested view, not to JSX",
);
assert.ok(
  listSrc.includes("RegenerationProblem"),
  "a failed campaign list must render through the shared error block",
);
assert.ok(
  /error=\{campaigns\.error\}/.test(routeSrc),
  "the route must hand the campaign list its query error",
);

// I-2 — the idle prose is gated on the SELECTION, never on missing data.
assert.ok(
  routeSrc.includes("regenerationDetailView"),
  "the route must decide the detail pane through the tested view",
);
assert.ok(!/\{!selected &&/.test(routeSrc), 'the idle prose must not be gated on "no data yet"');
assert.ok(
  !routeSrc.includes("Pick a campaign"),
  "the idle prose belongs to the tested view, not to JSX",
);

// I-3 — no mutation error is chained across campaigns or targets any more.
assert.ok(
  routeSrc.includes("regenerationMutationView"),
  "campaign and target mutations must be scoped by their own variables",
);
for (const chained of [
  /canaryMut\.error/,
  /approveMut\.error/,
  /rejectMut\.error/,
  /cancelMut\.error/,
  /targetMut\.error/,
  /canaryMut\.isPending/,
  /approveMut\.isPending/,
  /rejectMut\.isPending/,
  /cancelMut\.isPending/,
]) {
  assert.ok(
    !chained.test(routeSrc),
    `the route reads ${chained.source} directly; it must go through the scoped view`,
  );
}
assert.ok(
  reportSrc.includes("targetError"),
  "a target refusal must render on the lesson row whose variables produced it",
);

// minor 4 — a frozen lineage disappears from the eligible list.
assert.ok(
  /invalidateQueries\(\{\s*queryKey:\s*ELIGIBLE_KEY\s*\}\)/.test(routeSrc),
  "creating a campaign must invalidate the eligible lineages it just froze",
);

// minor 5 — a failed model manifest is a visible problem, not a dead end.
assert.ok(
  wizardSrc.includes("manifestError"),
  "the model step must surface a failed model manifest",
);
assert.ok(/manifestError=\{/.test(routeSrc), "the route must hand the wizard the manifest error");

// minor 6 — the lesson checkbox goes through the clamping helper.
assert.ok(
  wizardSrc.includes("regenerationToggleLesson"),
  "deselecting a lesson must re-clamp the stored canary size",
);

// minor 7 — no FREE-TEXT list may be keyed on its own text. Phase names are
// excluded on purpose: `canonical_phases` / `auto_included_phases` are the
// planner's identifiers for one subject's flow and cannot repeat, so
// `key={phase}` is a real id. Validation details, estimate notes and rollup
// warnings carry no id at all and genuinely do repeat.
for (const rel of WIRED_FILES) {
  const src = source(rel);
  for (const bad of [/key=\{line\}/, /key=\{note\}/, /key=\{warning\}/]) {
    assert.ok(
      !bad.test(src),
      `${rel} keys a list on its own text (${bad.source}); duplicate text drops a row`,
    );
  }
}
for (const rel of [
  "../components/regeneration/campaign-report.tsx",
  "../components/regeneration/regeneration-wizard.tsx",
]) {
  assert.ok(
    source(rel).includes("regenerationKeyedLines"),
    `${rel} must key text-only lists through the tested helper`,
  );
}

// The accepted residuals stay accepted, and stay documented: this product
// exposes no operator identity and no build SHA in its frontend contract.
assert.ok(
  /actor: ""/.test(routeSrc) && /app_git_revision: null/.test(routeSrc),
  "the blank actor and null revision are deliberate; they must stay visible in one place",
);

/* ────────────────────────────────────────────────────────────────────
 * 26. EVERY jobless target is recoverable (re-review I-1)
 *
 * The release is not one transaction. `approve_canary` stamps `approved_at`,
 * commits, moves the targets to `generating` in a SECOND transaction, commits
 * again, and only then creates the revision jobs — each in its own session
 * (`regeneration_campaign._prepare_wave` / `_create_wave`). `launch_canary`
 * has the same seam for the canary targets. Dying anywhere in that sequence
 * leaves a target with no `revision_job_id` at `planned` OR at `generating`,
 * and NOTHING on the server repairs it: the reconciler walks revision jobs,
 * and this target has none.
 *
 * The first fix only knew about `planned`, so the `generating` half of the
 * same crash was invisible: it was counted as a lesson that is regenerating,
 * the report polled it forever, and no control offered to start it.
 *
 * Both halves are recoverable, and WHICH recovery depends on the phase, which
 * is why the launch stamps are part of the predicate:
 *   • approved  (`approved_at`)                      → re-run approve
 *   • canary    (`canary_launched_at`, not approved) → re-run the canary launch
 * `launch_canary` REFUSES an approved campaign ("the bulk wave is released by
 * approve_canary, not by a relaunch"), so offering the wrong one would be a
 * guaranteed 409. Both accept a jobless `generating` target:
 * `_CREATABLE_TARGET_STATUSES = ("planned", "generating")`.
 * ──────────────────────────────────────────────────────────────────── */

const APPROVED_AT = "2026-08-20T10:00:00Z";
const CANARY_LAUNCHED_AT = "2026-08-20T09:30:00Z";

/** Approved, and one lesson was moved to `generating` with no job behind it. */
const JOBLESS_GENERATING = apiTarget({
  id: "jobless-generating",
  status: "generating",
  bucket: "in_flight",
  revision_job_id: null,
});

{
  // BULK, planned — the shape the first fix already knew.
  const planned = regenerationStrandedRelease(
    apiDetail({ status: "bulk_running", approved_at: APPROVED_AT, targets: [STRANDED_TARGET] }),
  );
  assert.ok(planned !== null);
  assert.strictEqual(planned.phase, "bulk");
  assert.strictEqual(planned.action, "approve");

  // BULK, generating — the same crash, one commit later. This used to read as
  // a lesson that was busy regenerating.
  const generating = regenerationStrandedRelease(
    apiDetail({
      status: "bulk_running",
      approved_at: APPROVED_AT,
      targets: [JOBLESS_GENERATING],
    }),
  );
  assert.ok(generating !== null, "a jobless `generating` target is stranded too");
  assert.strictEqual(generating.phase, "bulk");
  assert.strictEqual(generating.action, "approve");
  assert.strictEqual(generating.count, 1);
  assert.deepStrictEqual(generating.targetIds, ["jobless-generating"]);
  assert.ok(generating.lines.some((l) => l.includes("1-mavzu. Hujayra tuzilishi")));
  assert.ok(!generating.lines.some((l) => l.includes("jobless-generating")));
  assert.match(generating.actionLabel, /releas/i);
  assert.ok(!/approve/i.test(generating.actionLabel));

  // Both halves of one broken release are recovered by ONE action.
  const both = regenerationStrandedRelease(
    apiDetail({
      status: "bulk_running",
      approved_at: APPROVED_AT,
      targets: [STRANDED_TARGET, JOBLESS_GENERATING],
    }),
  );
  assert.strictEqual(both?.count, 2);
  assert.strictEqual(both?.action, "approve");
}

{
  // CANARY phase: launched, never approved. `launch_canary` is the repair and
  // approve would be refused, so the action must be the canary launch and the
  // copy must not read as an approval or a rejection.
  const canary = regenerationStrandedRelease(
    apiDetail({
      status: "canary_running",
      canary_launched_at: CANARY_LAUNCHED_AT,
      approved_at: null,
      targets: [{ ...JOBLESS_GENERATING, is_canary: true }],
    }),
  );
  assert.ok(canary !== null, "a canary that never got its revision job is stranded");
  assert.strictEqual(canary.phase, "canary");
  assert.strictEqual(canary.action, "launch-canary");
  assert.strictEqual(canary.count, 1);
  assert.match(canary.actionLabel, /retry/i);
  assert.match(canary.actionLabel, /canary/i);
  assert.ok(
    !/approve|reject/i.test(canary.actionLabel),
    `the canary recovery must not borrow the gate's vocabulary: ${canary.actionLabel}`,
  );
  assert.ok(
    !/reject/i.test(canary.detail),
    "there is nothing to decline: no canary was ever generated",
  );
  assert.match(canary.detail, /idempotent|nothing twice/i);
  assert.match(canary.pollNote, /canary/i);
  assert.match(canary.pollReason, /canary/i);
  assert.ok(!/approved but never/i.test(canary.headline), canary.headline);
}

{
  // The canary phase's OTHER lessons are jobless BY DESIGN — they wait at the
  // approval gate. Calling them stranded would fire this warning on every
  // healthy multi-lesson campaign.
  const healthy = regenerationStrandedRelease(
    apiDetail({
      status: "canary_running",
      canary_launched_at: CANARY_LAUNCHED_AT,
      approved_at: null,
      targets: [
        apiTarget({
          id: "canary",
          is_canary: true,
          status: "generating",
          revision_job_id: RUNNING_JOB_ID,
        }),
        apiTarget({ id: "waiting-1", status: "planned", revision_job_id: null }),
        apiTarget({ id: "waiting-2", status: "planned", revision_job_id: null }),
      ],
    }),
  );
  assert.strictEqual(
    healthy,
    null,
    "before approval the bulk lessons have no job because nobody has approved them yet",
  );
}

{
  // A lesson that HAS its job is not stranded, in either phase.
  for (const detail of [
    apiDetail({
      status: "bulk_running",
      approved_at: APPROVED_AT,
      targets: [{ ...JOBLESS_GENERATING, revision_job_id: RUNNING_JOB_ID }],
    }),
    apiDetail({
      status: "canary_running",
      canary_launched_at: CANARY_LAUNCHED_AT,
      targets: [{ ...JOBLESS_GENERATING, is_canary: true, revision_job_id: RUNNING_JOB_ID }],
    }),
  ]) {
    assert.strictEqual(regenerationStrandedRelease(detail), null);
  }

  // An UNTOUCHED draft is not a broken release: nothing has been launched, so
  // no lesson was ever promised a job. Warning here would fire on every new
  // campaign before its first click.
  assert.strictEqual(
    regenerationStrandedRelease(
      apiDetail({
        status: "draft",
        canary_launched_at: null,
        approved_at: null,
        // The canary rows are the ones the pre-approval scan looks at, so the
        // fixture has to carry them: without a canary here the scoping alone
        // would answer null and the launch-context guard would never be
        // exercised (it was deletable with this suite still green).
        targets: [
          { ...STRANDED_TARGET, is_canary: true },
          { ...JOBLESS_GENERATING, is_canary: true },
        ],
      }),
    ),
    null,
    "a campaign that was never launched has nothing to re-release",
  );

  // Terminal, rejected, cancelling and abandon-intent all still refuse to
  // offer a campaign action — re-releasing would fight the decision that was
  // already taken, and the wave skips an abandoning target anyway.
  const terminalShapes = [
    apiDetail({
      status: "cancelled",
      is_terminal: true,
      approved_at: APPROVED_AT,
      targets: [JOBLESS_GENERATING],
    }),
    apiDetail({
      status: "rejected",
      is_terminal: true,
      canary_launched_at: CANARY_LAUNCHED_AT,
      rejected_at: "2026-08-20T09:45:00Z",
      targets: [{ ...JOBLESS_GENERATING, is_canary: true }],
    }),
    apiDetail({
      status: "attention_required",
      canary_launched_at: CANARY_LAUNCHED_AT,
      rejected_at: "2026-08-20T09:45:00Z",
      targets: [{ ...JOBLESS_GENERATING, is_canary: true }],
    }),
    apiDetail({
      status: "attention_required",
      approved_at: APPROVED_AT,
      cancel_requested_at: "2026-08-20T10:30:00Z",
      targets: [JOBLESS_GENERATING],
    }),
    apiDetail({
      status: "bulk_running",
      approved_at: APPROVED_AT,
      targets: [{ ...JOBLESS_GENERATING, abandon_requested_at: "2026-08-20T10:30:00Z" }],
    }),
    apiDetail({
      status: "canary_running",
      canary_launched_at: CANARY_LAUNCHED_AT,
      targets: [{ ...JOBLESS_GENERATING, is_canary: true, is_terminal: true, status: "abandoned" }],
    }),
  ];
  for (const detail of terminalShapes) {
    assert.strictEqual(
      regenerationStrandedRelease(detail),
      null,
      `${detail.status} must offer no campaign-level recovery`,
    );
  }
}

/* ── the poll must not count a jobless `generating` as self-moving ──── */
{
  // ALONE: the only non-terminal lesson can never start, so refreshing is
  // request burn. This polled forever before, behind "1 lesson regenerating".
  const alone = regenerationPollDecision(
    apiDetail({
      status: "bulk_running",
      approved_at: APPROVED_AT,
      targets: [JOBLESS_GENERATING],
    }),
  );
  assert.strictEqual(alone.shouldPoll, false, "nothing will start a jobless lesson");
  assert.strictEqual(alone.intervalMs, false);
  assert.deepStrictEqual(alone.activity, [], "a lesson with no job is not activity");
  assert.ok(alone.strandedNote !== null);
  assert.ok(
    !/still releasing revision jobs/.test(alone.reason),
    "a campaign whose release never landed is not 'still releasing'",
  );

  // The canary half of the same rule.
  const canaryAlone = regenerationPollDecision(
    apiDetail({
      status: "canary_running",
      canary_launched_at: CANARY_LAUNCHED_AT,
      targets: [{ ...JOBLESS_GENERATING, is_canary: true }],
    }),
  );
  assert.strictEqual(canaryAlone.shouldPoll, false);
  assert.match(canaryAlone.reason, /canary/i);

  // MIXED: a real job beside a jobless one. The real one still moves on its
  // own, so the report keeps refreshing — and still names the stranded lesson.
  const mixed = regenerationPollDecision(
    apiDetail({
      status: "bulk_running",
      approved_at: APPROVED_AT,
      targets: [
        JOBLESS_GENERATING,
        apiTarget({ id: "real", status: "generating", revision_job_id: RUNNING_JOB_ID }),
      ],
    }),
  );
  assert.strictEqual(mixed.shouldPoll, true, "the lesson that really is running must be followed");
  assert.strictEqual(mixed.intervalMs, REGENERATION_POLL_MS);
  assert.ok(mixed.activity.some((a) => /1 lesson regenerating/.test(a)));
  assert.ok(
    !mixed.activity.some((a) => /2 lessons regenerating/.test(a)),
    "the jobless lesson must not be counted as one that is regenerating",
  );
  assert.ok(mixed.strandedNote !== null, "and the stranded lesson is still reported");

  // Publication elsewhere keeps the report alive for the same reason.
  assert.strictEqual(
    regenerationPollDecision(
      apiDetail({
        status: "bulk_running",
        approved_at: APPROVED_AT,
        targets: [
          JOBLESS_GENERATING,
          apiTarget({
            id: "pub",
            status: "publishing",
            publication_state: "publishing",
            revision_job_id: RUNNING_JOB_ID,
          }),
        ],
      }),
    ).shouldPoll,
    true,
  );
}

/* ── the lesson's OWN row offers the retry the backend accepts ──────── */
{
  // `retry_generation` accepts `generating` and, with no job, finishes the
  // creation (`_create_wave([target_id], contract)`). So the row action is
  // real, not a hopeful button.
  const actions = regenerationTargetActions(JOBLESS_GENERATING);
  assert.deepStrictEqual(
    actions.map((a) => a.kind),
    ["retry-generation", "abandon"],
    "a lesson that was never given a job must be startable from its own row",
  );
  const retry = actions[0];
  assert.ok(retry.enabled);
  assert.strictEqual(retry.requiresReason, false);
  assert.ok(
    /never|no revision job/i.test(retry.detail),
    `the copy must say what actually happened: ${retry.detail}`,
  );

  // A lesson that IS running keeps offering nothing but abandon: retrying a
  // job already in flight is a no-op, and the button would be a lie.
  assert.deepStrictEqual(
    regenerationTargetActions({ ...JOBLESS_GENERATING, revision_job_id: RUNNING_JOB_ID }).map(
      (a) => a.kind,
    ),
    ["abandon"],
  );
  // A terminal or abandoning row is unchanged.
  assert.deepStrictEqual(
    regenerationTargetActions({ ...JOBLESS_GENERATING, is_terminal: true }),
    [],
  );
  // And the pending / terminal-campaign guards still cover the new action.
  assert.ok(
    regenerationTargetActions(JOBLESS_GENERATING, { campaignTerminal: true }).every(
      (a) => !a.enabled,
    ),
  );
  assert.ok(
    regenerationTargetActions(JOBLESS_GENERATING, { pendingKind: "retry-generation" }).every(
      (a) => !a.enabled,
    ),
  );
}

/* ── the campaign LIST keeps its cheap, deliberately imprecise poll ─── */
// The summary carries no `revision_job_id` — `status_counts` is all there
// is — so the list cannot tell a jobless `generating` from a running one and
// deliberately does not try. It keeps ticking; opening the campaign is what
// shows the truth, because the DETAIL poll can see the jobs. Erring this way
// costs one wasted 10s tick; erring the other way would freeze the list for
// every campaign that really is generating.
assert.strictEqual(
  regenerationListPollMs([
    apiDetail({
      status: "bulk_running",
      approved_at: APPROVED_AT,
      targets: [JOBLESS_GENERATING],
    }),
  ]),
  REGENERATION_LIST_POLL_MS,
  "the list poll is intentionally cheap; the detail screen owns the truth",
);

/* ────────────────────────────────────────────────────────────────────
 * 27. The eligible-lessons read reports its failure at its OWN step (I-2)
 *
 * `/eligible` failing is a fact about step 2, where the lessons are picked.
 * It was routed into step 3's `planError`, so a 500 on the lesson read
 * appeared under "Pick the phases to rebuild" while step 2 calmly stated that
 * this book has no regenerable lessons — a claim about data that never
 * arrived.
 * ──────────────────────────────────────────────────────────────────── */

{
  const source1 = ELIGIBLE_SOURCE;
  const boom = regenerationErrorView(
    new ApiError(500, "eligible blew up", { error: "server_error", message: "eligible blew up" }),
  );

  // No book picked: the query is deliberately off. That is not a failure and
  // not an empty answer.
  const blocked = regenerationSourcesView({
    sources: [],
    isLoading: false,
    error: null,
    blockedReason: REGENERATION_PICK_BOOK_HINT,
  });
  assert.strictEqual(blocked.mode, "blocked");
  assert.strictEqual(blocked.message, REGENERATION_PICK_BOOK_HINT);
  assert.strictEqual(blocked.error, null);

  const loading = regenerationSourcesView({
    sources: [],
    isLoading: true,
    error: null,
    blockedReason: null,
  });
  assert.strictEqual(loading.mode, "loading");
  assert.strictEqual(loading.message, REGENERATION_SOURCES_LOADING);
  assert.strictEqual(loading.error, null);

  // Genuinely empty: the server answered, with nothing in it.
  const empty = regenerationSourcesView({
    sources: [],
    isLoading: false,
    error: null,
    blockedReason: null,
  });
  assert.strictEqual(empty.mode, "empty");
  assert.strictEqual(empty.message, REGENERATION_SOURCES_EMPTY);

  // THE FINDING: a failed read is not an empty book.
  const failed = regenerationSourcesView({
    sources: [],
    isLoading: false,
    error: boom,
    blockedReason: null,
  });
  assert.strictEqual(failed.mode, "error");
  assert.strictEqual(
    failed.message,
    null,
    "a failed lesson read must not claim the book has no regenerable lessons",
  );
  assert.strictEqual(failed.error, boom);
  assert.notStrictEqual(failed.message, REGENERATION_SOURCES_EMPTY);

  // A failed refresh over rows the cache still holds keeps the rows.
  const stale = regenerationSourcesView({
    sources: [source1],
    isLoading: false,
    error: boom,
    blockedReason: null,
  });
  assert.strictEqual(stale.mode, "error");
  assert.strictEqual(stale.sources.length, 1, "a failed refresh must not hide known lessons");
  assert.strictEqual(stale.message, null);

  // A background refresh over existing rows is neither empty nor loading prose.
  const list = regenerationSourcesView({
    sources: [source1],
    isLoading: true,
    error: null,
    blockedReason: null,
  });
  assert.strictEqual(list.mode, "list");
  assert.strictEqual(list.message, null);
  assert.strictEqual(list.error, null);
  assert.deepStrictEqual(
    regenerationSourcesView({
      sources: undefined,
      isLoading: false,
      error: null,
      blockedReason: null,
    }).sources,
    [],
  );
}

/* ────────────────────────────────────────────────────────────────────
 * 28. The cheap minors, as behaviour
 * ──────────────────────────────────────────────────────────────────── */

/* ── phase-selection prose separates "nothing picked" from "not here yet" ── */
{
  const plan: RegenerationApiPhasePlan = {
    subject: "biology",
    canonical_phases: ["extract", "flashcards"],
    selected_phases: ["flashcards"],
    auto_included_phases: [],
    regenerated_phases: ["flashcards"],
    copied_phases: ["extract"],
    excluded_affected_phases: [],
    broken_dependency_edges: [],
    refresh_extraction: false,
    regenerated_phase_count: 1,
    copied_phase_count: 1,
    acknowledgement_required: false,
    acknowledgement_message: null,
  };
  const boom = regenerationErrorView(new ApiError(500, "plan blew up"));

  const ready = regenerationPlanStepView({
    plan,
    hasSelection: true,
    isLoading: false,
    error: null,
  });
  assert.strictEqual(ready.mode, "ready");
  assert.strictEqual(ready.message, null);

  const none = regenerationPlanStepView({
    plan: null,
    hasSelection: false,
    isLoading: false,
    error: null,
  });
  assert.strictEqual(none.mode, "none");
  assert.strictEqual(none.message, REGENERATION_PLAN_NONE);

  const loading = regenerationPlanStepView({
    plan: null,
    hasSelection: true,
    isLoading: true,
    error: null,
  });
  assert.strictEqual(loading.mode, "loading");
  assert.strictEqual(loading.message, REGENERATION_PLAN_LOADING);
  assert.notStrictEqual(
    loading.message,
    REGENERATION_PLAN_NONE,
    "a plan that is still being computed must not read as an empty selection",
  );

  const failed = regenerationPlanStepView({
    plan: null,
    hasSelection: true,
    isLoading: false,
    error: boom,
  });
  assert.strictEqual(failed.mode, "error");
  assert.strictEqual(failed.message, null, "a failed plan must not read as an empty selection");

  // A selection whose plan has not arrived and is not (yet) marked fetching is
  // still work in progress, never "nothing selected".
  assert.strictEqual(
    regenerationPlanStepView({ plan: null, hasSelection: true, isLoading: false, error: null })
      .mode,
    "loading",
  );
}

/* ── an ineligible lineage says what the id IS ─────────────────────── */
{
  const line = regenerationIneligibleLine({
    toc_entry_id: "99998888-7777-6666-5555-444433332222",
    output_language: "ru",
    reasons: ["no_completed_source", "incomplete_snapshot"],
    detail: "the source job has no flashcards phase",
  });
  assert.ok(line.startsWith("Lesson id "), `a bare UUID must be introduced as one: ${line}`);
  assert.ok(line.includes("99998888-7777-6666-5555-444433332222"));
  assert.ok(line.includes("Russian"), "the language is a word, not a code");
  assert.ok(!line.includes("_"), `no internal token may reach the screen: ${line}`);
  assert.ok(line.includes("the source job has no flashcards phase"));
  // A lineage with no detail still reads as a sentence.
  const terse = regenerationIneligibleLine({
    toc_entry_id: "abc",
    output_language: "uz",
    reasons: ["active_lineage_conflict"],
    detail: "",
  });
  assert.ok(terse.startsWith("Lesson id abc"));
  assert.ok(!terse.endsWith("— "), terse);
}

/* ── solver verdicts are prose, like every other status on screen ──── */
assert.strictEqual(regenerationSolverStatusLabel("mismatch_regen"), "Mismatch regen");
assert.strictEqual(regenerationSolverStatusLabel("solver-unavailable"), "Solver unavailable");
for (const raw of ["match", "mismatch_shipped", "solver_disabled"]) {
  const label = regenerationSolverStatusLabel(raw);
  assert.ok(!label.includes("_"), `${raw} reached the screen as a token: ${label}`);
  assert.ok(label.length > 0);
}
// An unknown verdict is spelled out rather than dropped.
assert.ok(regenerationSolverStatusLabel("brand_new_verdict").length > 0);

/* ── a failed read offers a way to run it again ────────────────────── */
assert.ok(REGENERATION_READ_RETRY_LABEL.length > 0);
assert.ok(
  !/refresh the page|reload/i.test(REGENERATION_READ_RETRY_LABEL),
  "the retry must re-run the read, not ask for a browser reload",
);

/* ────────────────────────────────────────────────────────────────────
 * 29. Structural guards for the Task-10 correction
 * ──────────────────────────────────────────────────────────────────── */

// I-1 — the recovery renders for BOTH phases and calls the endpoint each one
// actually accepts. `launch_canary` refuses an approved campaign, so a single
// hardcoded `onApprove` would 409 the whole canary half. The assertions are
// scoped to the recovery block and spelled out in full: `stranded.action`
// alone is a substring of `stranded.actionLabel`, which every build already
// has, so it could never fail.
{
  const at = canarySrc.indexOf("stranded && (");
  assert.ok(at > 0, "the recovery section must still be gated on the stranded predicate");
  const block = canarySrc.slice(at);
  assert.ok(
    /stranded\.action === "approve"/.test(block),
    "the recovery must dispatch on the phase-specific action, not on one mutation",
  );
  assert.ok(
    block.includes("onLaunchCanary"),
    "the canary-phase recovery must re-run the canary launch",
  );
  assert.ok(block.includes("onApprove"), "the approved-phase recovery must re-run approve");
  assert.ok(
    !/eject/i.test(block),
    "the recovery section must not offer Reject: this is not the canary gate",
  );
}
assert.ok(
  reportSrc.includes("regenerationTargetActions"),
  "the row-level retry stays the tested helper's decision",
);

// I-2 — the eligible failure belongs to step 2, and only to step 2.
assert.ok(
  /sourcesError=\{/.test(routeSrc),
  "the route must hand the wizard the eligible-read error under its own name",
);
assert.ok(
  !/planError=\{[^}]*eligible\.error/.test(routeSrc),
  "a failed lesson read must not be reported as a failed phase plan",
);
assert.ok(
  /regenerationSourcesView\(\{/.test(wizardSrc),
  "step 2 must decide blocked/loading/error/empty through the tested view",
);
assert.ok(
  !wizardSrc.includes("No lesson in this book"),
  "the empty-book sentence belongs to the tested view, not to JSX",
);

// minor 3 — a mutable fleet default must not be able to make a campaign
// impossible. `inherit` resolves against `launch_defaults.<role>_transport`
// (`resolve_role_transport_default`), and a `cli` there refuses every
// regeneration campaign with `non_api_transport` and no UI lever to fix it.
for (const role of ["extract", "judge", "solver"]) {
  assert.ok(
    new RegExp(`${role}_transport: "api"`).test(routeSrc),
    `${role}_transport must be pinned to api, not left to a mutable default`,
  );
  assert.ok(
    !new RegExp(`${role}_transport: "inherit"`).test(routeSrc),
    `${role}_transport: "inherit" re-reads launch_defaults at creation time`,
  );
}
assert.ok(/transport: "api"/.test(routeSrc), "the content transport stays api");

// minor 2 — no raw solver token on any surface.
for (const src of [canarySrc, reportSrc]) {
  assert.ok(
    /regenerationSolverStatusLabel\(/.test(src),
    "solver verdicts must render through the tested label helper",
  );
}

// minors 4 and 5 — both strings live in the tested layer.
assert.ok(/regenerationPlanStepView\(\{/.test(wizardSrc));
assert.ok(/regenerationIneligibleLine\(/.test(wizardSrc));
assert.ok(
  !wizardSrc.includes("Nothing selected yet"),
  "the step-4 prose belongs to the tested view, not to JSX",
);

// minor 6 — a failed read is recoverable without reloading the app, and the
// retry rides the ONE shared error block rather than a fourth local copy.
assert.ok(
  /\{onRetry && \(/.test(wizardSrc),
  "RegenerationProblem must RENDER the retry, not merely accept the prop",
);
assert.ok(
  /onRetry=\{onRetry\}/.test(listSrc),
  "a failed campaign-list read must offer to run itself again",
);
assert.ok(
  /onRetry=\{[^}]*campaigns\.refetch/.test(routeSrc),
  "the campaign-list retry must re-run the campaigns query",
);
assert.ok(
  /onRetry=\{[^}]*detail\.refetch/.test(routeSrc),
  "the campaign-detail retry must re-run the detail query",
);

console.log("OK");
