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
  // Fixtures only in this task — no API/type coupling until Task 10.
  assert.ok(!/from "@\/lib\/api"/.test(src), `${rel} must not import api.ts in Task 4`);
  assert.ok(!/from "@\/lib\/types"/.test(src), `${rel} must not import types.ts in Task 4`);
}

// The components must render the pure decisions, not re-derive them inline.
const canarySrc = source("../components/regeneration/canary-review.tsx");
assert.ok(canarySrc.includes("approvalGate"), "canary-review must render approvalGate()");
assert.ok(
  /disabled=\{!gate\.canApprove\}/.test(canarySrc),
  "canary-review must not offer a clickable approve button for an empty campaign",
);
assert.ok(
  !canarySrc.includes("Approve canary and publish V"),
  "canary-review must not hardcode the approval label — it comes from approvalGate()",
);
const wizardSrc = source("../components/regeneration/regeneration-wizard.tsx");
assert.ok(wizardSrc.includes("cascadeDisclosure"), "wizard must render cascadeDisclosure()");
assert.ok(
  !/Regenerates \$\{|Regenerates \d/.test(wizardSrc),
  "wizard must not hand-roll the cascade headline",
);
// campaign-list had no guard here, which is exactly where a hand-rolled
// "{n} lessons" (rendering "1 lessons") slipped through review.
const listSrc = source("../components/regeneration/campaign-list.tsx");
assert.ok(
  listSrc.includes("campaignStatusLabel"),
  "campaign-list must render campaignStatusLabel()",
);
assert.ok(listSrc.includes("lessonCountLabel"), "campaign-list must render lessonCountLabel()");
assert.ok(!/lessons/.test(listSrc), "campaign-list must not hand-roll a pluralised lesson count");
assert.ok(
  !/toFixed/.test(listSrc),
  "campaign-list must format money through formatUsd(), not its own helper",
);
const reportSrc = source("../components/regeneration/campaign-report.tsx");
assert.ok(reportSrc.includes("bucketReport"), "report must render bucketReport()");
assert.ok(reportSrc.includes("outcomeReason"), "report must render outcomeReason()");
assert.ok(
  reportSrc.includes("disabled"),
  "report retry/abandon buttons must carry the disabled attribute",
);

console.log("OK");
