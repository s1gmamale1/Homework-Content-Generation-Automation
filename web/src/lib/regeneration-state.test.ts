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
  REGENERATION_APPROVE_NOTE,
  REGENERATION_CANCEL_CONFIRMATION,
  REGENERATION_LAUNCH_LABEL,
  REGENERATION_LAUNCH_SPEND_NOTE,
  REGENERATION_LIST_POLL_MS,
  REGENERATION_NO_SPEND_NOTE,
  REGENERATION_POLL_MS,
  REGENERATION_REJECT_CONFIRMATION,
  cascadeFromPlan,
  phaseSelectionFromPlan,
  regenerationApprovalGate,
  regenerationBucketViews,
  regenerationCampaignStatusLabel,
  regenerationListPollMs,
  regenerationPollDecision,
  regenerationPublicationStateLabel,
  regenerationReasonError,
  regenerationTargetActions,
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
  RegenerationPhasePlan as RegenerationApiPhasePlan,
  RegenerationCampaignDetail,
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
      targets: [apiTarget({ status: "generating", bucket: "in_flight", is_canary: true })],
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
      targets: [apiTarget({ status: "generating", bucket: "in_flight" })],
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
  // A running target can still be abandoned, but never retried.
  const running = apiTarget({ status: "generating", bucket: "in_flight" });
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

console.log("OK");
