/**
 * Plain npx-tsx-runnable test for prepare-status.ts (worklog 0144 task 5).
 * Run: cd web && npx tsx src/lib/prepare-status.test.ts
 */
import assert from "node:assert/strict";
import type { AvailableLanguages, LangPart } from "./types";
import { hasMidFlightBook, partPrepareStatus } from "./prepare-status";

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
    "3 job(s) reference this TOC — delete affected sections first",
  );
}

// --- redo-blocked, singular count — same "job(s)" phrasing as the backend's
// 409 (toc_retry_blocked_by_jobs) so operators see a familiar shape ---
{
  const part: LangPart = {
    page_id: "p", title: "A", has_textbook: true,
    book_id: "b", book_status: "toc_ready", toc_total: 1, toc_ready_at: null,
    redo_blocked_by_jobs: 1, prepared: true,
  };
  const s = partPrepareStatus(part);
  if (s.panel.kind !== "prepared") throw new Error("expected prepared panel");
  assert.equal(s.panel.redo.disabledReason, "1 job(s) reference this TOC — delete affected sections first");
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

console.log("prepare-status.test.ts: all assertions passed");
