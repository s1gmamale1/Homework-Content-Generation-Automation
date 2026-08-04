import assert from "node:assert/strict";
import {
  decideRelaunch,
  formatResumeToast,
  resumeBlockedByRetired,
  retiredResumeNotice,
} from "./retired-resume";

// ─── retired-only: all selected saved sections are retired-stamped ───────
{
  const counts = { new: 0, resumable: 0, retired: 3, empty: 0 };
  assert.equal(resumeBlockedByRetired(counts), true, "retired-only must block Resume");
  assert.deepEqual(decideRelaunch(counts), { kind: "retired_blocks_resume" });
  const notice = retiredResumeNotice(counts);
  assert.ok(notice, "a notice must render when retired > 0");
  assert.match(notice!, /3 saved lessons use a retired model and CANNOT be resumed/);
  assert.match(notice!, /Discard & regenerate will regenerate all selected saved jobs/);
}

// ─── mixed live + retired: retired still blocks Resume outright ──────────
{
  const counts = { new: 1, resumable: 2, retired: 1, empty: 0 };
  assert.equal(resumeBlockedByRetired(counts), true, "even ONE retired must block Resume");
  assert.deepEqual(decideRelaunch(counts), { kind: "retired_blocks_resume" });
  const notice = retiredResumeNotice(counts);
  assert.match(notice!, /^1 saved lesson use a retired model/, "singular noun for count===1");
}

// ─── resumable-only (no retired): existing resume/discard flow unaffected ─
{
  const counts = { new: 0, resumable: 4, retired: 0, empty: 0 };
  assert.equal(resumeBlockedByRetired(counts), false);
  assert.deepEqual(decideRelaunch(counts), { kind: "offer_resume_or_discard" });
  assert.equal(retiredResumeNotice(counts), null);
}

// ─── nothing saved at stake → straight launch ─────────────────────────────
{
  const counts = { new: 5, resumable: 0, retired: 0, empty: 0 };
  assert.deepEqual(decideRelaunch(counts), { kind: "launch_straight" });
  assert.equal(retiredResumeNotice(counts), null);
}

// ─── empty-only (failed with no saved phases, none retired) ──────────────
{
  const counts = { new: 0, resumable: 0, retired: 0, empty: 2 };
  assert.deepEqual(decideRelaunch(counts), { kind: "launch_straight" });
}

// ─── resume toast copy ────────────────────────────────────────────────────
assert.equal(formatResumeToast(5, 0), "Resuming 5 lessons");
assert.equal(formatResumeToast(3, 2), "3 resumed, 2 skipped (retired model)");
assert.equal(formatResumeToast(0, 4), "0 resumed, 4 skipped (retired model)");

console.log("retired-resume.test.ts: all assertions passed");
