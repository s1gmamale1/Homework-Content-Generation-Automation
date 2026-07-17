/**
 * Plain npx-tsx-runnable test for prepare-status.ts (worklog 0144 task 5).
 * Run: cd web && npx tsx src/lib/prepare-status.test.ts
 */
import assert from "node:assert/strict";
import type { AvailableLanguages, LangPart, NotionCandidate } from "./types";
import {
  candidatePrepareStatus,
  hasMidFlightBook,
  partPrepareStatus,
  proceedBlockedTooltip,
  resolvedPrepareStatus,
} from "./prepare-status";

// --- no_textbook: no part at all, or a part with has_textbook: false ---
{
  const s = partPrepareStatus(null);
  assert.equal(s.chip.kind, "no_textbook");
  assert.equal(s.chip.label, "NO TEXTBOOK");
  assert.equal(s.chip.colorFamily, "amber");
  assert.equal(s.chip.pulse, false);
  assert.deepEqual(s.actions, {
    useExisting: false, redo: false, review: false, retry: false, proceed: false,
  });
}
{
  const part: LangPart = { page_id: "p", title: "", has_textbook: false };
  const s = partPrepareStatus(part);
  assert.equal(s.chip.kind, "no_textbook");
  assert.equal(s.actions.proceed, false);
}

// --- textbook_ready: has_textbook true, NOT linked to any book ---
{
  const part: LangPart = { page_id: "p", title: "Algebra", has_textbook: true };
  const s = partPrepareStatus(part);
  assert.equal(s.chip.kind, "textbook_ready");
  assert.equal(s.chip.label, "TEXTBOOK READY");
  assert.equal(s.chip.colorFamily, "emerald");
  assert.deepEqual(s.actions, {
    useExisting: false, redo: false, review: false, retry: false, proceed: true,
  });
}
// two-linked-candidates edge case: backend omits the part-level rollup when
// >1 candidate is linked — book_id/book_status stay absent on the part even
// though it "has_textbook". Falls back to textbook_ready (conservative —
// never lies about a specific book), per-candidate detail is out of scope here.
{
  const part: LangPart = {
    page_id: "p", title: "Algebra", has_textbook: true,
    candidates: [
      { page_id: "p", block_id: "b1", filename: "A.pdf", rank: 0, book_id: "bk-1", book_status: "toc_ready" },
      { page_id: "p", block_id: "b2", filename: "B.pdf", rank: 0, book_id: "bk-2", book_status: "toc_ready" },
    ],
  };
  const s = partPrepareStatus(part);
  assert.equal(s.chip.kind, "textbook_ready");
}

// --- prepared: linked book in toc_ready ---
{
  const part: LangPart = {
    page_id: "p", title: "Algebra", has_textbook: true,
    book_id: "book-1", book_status: "toc_ready",
    toc_total: 12, toc_ready_at: "2026-07-01T00:00:00Z", redo_blocked_by_jobs: 0,
    prepared: true,
  };
  const s = partPrepareStatus(part);
  assert.equal(s.chip.kind, "prepared");
  assert.equal(s.chip.label, "PREPARED · 12 lessons");
  assert.equal(s.chip.colorFamily, "emerald");
  assert.equal(s.chip.pulse, false);
  assert.deepEqual(s.panel, {
    kind: "prepared",
    bookId: "book-1",
    lessons: 12,
    preparedAt: "2026-07-01T00:00:00Z",
    redo: { enabled: true, disabledReason: null },
  });
  assert.deepEqual(s.actions, {
    useExisting: true, redo: true, review: false, retry: false, proceed: false,
  });
}

// --- prepared, but redo blocked by referencing jobs ---
{
  const part: LangPart = {
    page_id: "p", title: "Algebra", has_textbook: true,
    book_id: "book-1", book_status: "toc_ready",
    toc_total: 5, toc_ready_at: null, redo_blocked_by_jobs: 3,
    prepared: true,
  };
  const s = partPrepareStatus(part);
  assert.equal(s.chip.kind, "prepared");
  assert.equal(s.actions.redo, false);
  assert.equal(s.actions.useExisting, true);
  if (s.panel.kind !== "prepared") throw new Error("expected prepared panel");
  assert.equal(s.panel.redo.enabled, false);
  assert.equal(
    s.panel.redo.disabledReason,
    "3 homework job(s) reference this book's sections — delete the affected sections first",
  );
}

// --- redo-blocked, singular count — VERBATIM match with the backend's 409
// (toc_retry_blocked_by_jobs `message` field) so an operator sees the same
// wording whether it comes from this synthesized status or a race-condition
// 409 from the retry call itself ---
{
  const part: LangPart = {
    page_id: "p", title: "A", has_textbook: true,
    book_id: "b", book_status: "toc_ready", toc_total: 1, toc_ready_at: null,
    redo_blocked_by_jobs: 1, prepared: true,
  };
  const s = partPrepareStatus(part);
  if (s.panel.kind !== "prepared") throw new Error("expected prepared panel");
  assert.equal(
    s.panel.redo.disabledReason,
    "1 homework job(s) reference this book's sections — delete the affected sections first",
  );
}

// --- preparing: toc_extracting ---
{
  const part: LangPart = {
    page_id: "p", title: "A", has_textbook: true,
    book_id: "book-2", book_status: "toc_extracting",
  };
  const s = partPrepareStatus(part);
  assert.equal(s.chip.kind, "preparing");
  assert.equal(s.chip.label, "PREPARING");
  assert.equal(s.chip.colorFamily, "blue");
  assert.equal(s.chip.pulse, true);
  assert.deepEqual(s.panel, { kind: "preparing", bookId: "book-2" });
  assert.deepEqual(s.actions, {
    useExisting: false, redo: false, review: false, retry: false, proceed: false,
  });
}

// --- preparing: uploading (defensive — a linked book can be caught mid-upload) ---
{
  const part: LangPart = {
    page_id: "p", title: "A", has_textbook: true,
    book_id: "book-3", book_status: "uploading",
  };
  const s = partPrepareStatus(part);
  assert.equal(s.chip.kind, "preparing");
}

// --- needs_review: toc_review ---
{
  const part: LangPart = {
    page_id: "p", title: "A", has_textbook: true,
    book_id: "book-4", book_status: "toc_review",
  };
  const s = partPrepareStatus(part);
  assert.equal(s.chip.kind, "needs_review");
  assert.equal(s.chip.label, "NEEDS REVIEW");
  assert.equal(s.chip.colorFamily, "amber");
  assert.deepEqual(s.panel, { kind: "needs_review", bookId: "book-4" });
  assert.deepEqual(s.actions, {
    useExisting: false, redo: false, review: true, retry: false, proceed: false,
  });
}

// --- failed ---
{
  const part: LangPart = {
    page_id: "p", title: "A", has_textbook: true,
    book_id: "book-5", book_status: "failed",
  };
  const s = partPrepareStatus(part);
  assert.equal(s.chip.kind, "failed");
  assert.equal(s.chip.label, "FAILED");
  assert.equal(s.chip.colorFamily, "red");
  assert.deepEqual(s.panel, { kind: "failed", bookId: "book-5" });
  assert.deepEqual(s.actions, {
    useExisting: false, redo: false, review: false, retry: true, proceed: false,
  });
}

// --- hasMidFlightBook: recursively scans an AvailableLanguages tree for any
// linked part sitting in a non-steady book_status (drives the poll gate) ---
{
  assert.equal(hasMidFlightBook(null), false);
  assert.equal(hasMidFlightBook(undefined), false);
  assert.equal(hasMidFlightBook({}), false);

  const steady: AvailableLanguages = {
    math: { uz: { page_id: "p", has_textbook: true, parts: [
      { page_id: "p", title: "A", has_textbook: true, book_id: "b", book_status: "toc_ready" },
    ] } },
  };
  assert.equal(hasMidFlightBook(steady), false);

  const midFlight: AvailableLanguages = {
    math: { uz: { page_id: "p", has_textbook: true, parts: [
      { page_id: "p", title: "A", has_textbook: true, book_id: "b", book_status: "toc_extracting" },
    ] } },
  };
  assert.equal(hasMidFlightBook(midFlight), true);

  const uploading: AvailableLanguages = {
    math: { ru: { page_id: "p2", has_textbook: true, parts: [
      { page_id: "p2", title: "B", has_textbook: true, book_id: "b2", book_status: "uploading" },
    ] } },
  };
  assert.equal(hasMidFlightBook(uploading), true);

  const unlinked: AvailableLanguages = {
    math: { uz: { page_id: "p", has_textbook: true, parts: [
      { page_id: "p", title: "A", has_textbook: true },
    ] } },
  };
  assert.equal(hasMidFlightBook(unlinked), false);
}

// --- candidatePrepareStatus: a single file-level candidate (BE-19 task 6),
// same chip/panel/actions shape as partPrepareStatus, but with no
// has_textbook flag of its own (a candidate IS a textbook file by
// construction) — PR #99 gate finding 3 ---
{
  // no candidate at all → no_textbook, proceed false
  const s = candidatePrepareStatus(null);
  assert.equal(s.chip.kind, "no_textbook");
  assert.equal(s.actions.proceed, false);
}
{
  // linked, toc_ready candidate → PREPARED panel, proceed false (must not
  // let the primary Prepare button re-fire /from-notion on it)
  const candidate: NotionCandidate = {
    page_id: "p", block_id: "b1", filename: "A.pdf", rank: 0,
    book_id: "book-1", book_status: "toc_ready",
    toc_total: 9, toc_ready_at: "2026-07-01T00:00:00Z", redo_blocked_by_jobs: 0,
  };
  const s = candidatePrepareStatus(candidate);
  assert.equal(s.chip.kind, "prepared");
  assert.equal(s.chip.label, "PREPARED · 9 lessons");
  assert.equal(s.actions.proceed, false);
  if (s.panel.kind !== "prepared") throw new Error("expected prepared panel");
  assert.equal(s.panel.bookId, "book-1");
}
{
  // unlinked candidate (no book_id/book_status) → textbook_ready, proceed true
  const candidate: NotionCandidate = {
    page_id: "p", block_id: "b2", filename: "B.pdf", rank: 0,
  };
  const s = candidatePrepareStatus(candidate);
  assert.equal(s.chip.kind, "textbook_ready");
  assert.equal(s.actions.proceed, true);
}
{
  // a preparing candidate also blocks proceed
  const candidate: NotionCandidate = {
    page_id: "p", block_id: "b3", filename: "C.pdf", rank: 0,
    book_id: "book-2", book_status: "toc_extracting",
  };
  const s = candidatePrepareStatus(candidate);
  assert.equal(s.chip.kind, "preparing");
  assert.equal(s.actions.proceed, false);
}

// --- resolvedPrepareStatus: the (part, selectedCandidate) resolution the
// primary Prepare button and the panel both key off — a selected candidate
// governs over the part-level rollup ---
{
  // no selection → falls back to the part's own status
  const part: LangPart = { page_id: "p", title: "Algebra", has_textbook: true };
  const s = resolvedPrepareStatus(part, null);
  assert.equal(s.chip.kind, "textbook_ready");
  assert.equal(s.actions.proceed, true);
}
{
  // two-linked part (no part-level rollup) + a SELECTED linked candidate →
  // the selection's own PREPARED status governs, not the part's
  // textbook_ready fallback (closes finding 3)
  const part: LangPart = {
    page_id: "p", title: "Algebra", has_textbook: true,
    candidates: [
      { page_id: "p", block_id: "b1", filename: "A.pdf", rank: 0, book_id: "bk-1", book_status: "toc_ready" },
      { page_id: "p", block_id: "b2", filename: "B.pdf", rank: 0, book_id: "bk-2", book_status: "toc_ready" },
    ],
  };
  // sanity: the un-selected part status is still the conservative fallback
  assert.equal(partPrepareStatus(part).chip.kind, "textbook_ready");
  const selected = part.candidates![0];
  const s = resolvedPrepareStatus(part, selected);
  assert.equal(s.chip.kind, "prepared");
  assert.equal(s.actions.proceed, false);
}
{
  // same two-linked part, but the SELECTED candidate is the unlinked one →
  // proceeds normally
  const part: LangPart = {
    page_id: "p", title: "Algebra", has_textbook: true,
    candidates: [
      { page_id: "p", block_id: "b1", filename: "A.pdf", rank: 0, book_id: "bk-1", book_status: "toc_ready" },
      { page_id: "p", block_id: "b2", filename: "B.pdf", rank: 0 },
    ],
  };
  const selected = part.candidates![1];
  const s = resolvedPrepareStatus(part, selected);
  assert.equal(s.chip.kind, "textbook_ready");
  assert.equal(s.actions.proceed, true);
}

// --- proceedBlockedTooltip: the shared tooltip text for the primary
// Prepare button, one per linked non-proceed chip kind ---
{
  assert.equal(
    proceedBlockedTooltip(partPrepareStatus({
      page_id: "p", title: "A", has_textbook: true,
      book_id: "b", book_status: "toc_ready", toc_total: 1, toc_ready_at: null,
      redo_blocked_by_jobs: 0, prepared: true,
    })),
    "Already prepared — use the panel above to open it or redo extraction",
  );
  assert.equal(
    proceedBlockedTooltip(partPrepareStatus({
      page_id: "p", title: "A", has_textbook: true,
      book_id: "b", book_status: "toc_extracting",
    })),
    "Preparation in progress",
  );
  assert.equal(
    proceedBlockedTooltip(partPrepareStatus({
      page_id: "p", title: "A", has_textbook: true,
      book_id: "b", book_status: "toc_review",
    })),
    "Needs review — open it from the panel",
  );
  assert.equal(
    proceedBlockedTooltip(partPrepareStatus({
      page_id: "p", title: "A", has_textbook: true,
      book_id: "b", book_status: "failed",
    })),
    "Preparation failed — retry from the panel",
  );
  // proceed: true → no tooltip
  assert.equal(
    proceedBlockedTooltip(partPrepareStatus({ page_id: "p", title: "A", has_textbook: true })),
    undefined,
  );
}

console.log("prepare-status.test.ts: all assertions passed");
