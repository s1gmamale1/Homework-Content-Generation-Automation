/**
 * Plain npx-tsx-runnable test for notion-parts.ts helpers.
 * Run: cd web && npx tsx src/lib/notion-parts.test.ts
 */
import assert from "node:assert/strict";
import type { LangAvailability, LangPart, NotionCandidate } from "./types";
import {
  candidateSelectionState,
  partsFor,
  resolveNotionPageId,
  langChipState,
  partForResolution,
  resolveCandidate,
} from "./notion-parts";

const uzMulti: Record<string, LangAvailability> = {
  uz: {
    page_id: "uz-math-1",
    has_textbook: true,
    parts: [
      { page_id: "uz-math-1", title: "Matematika 1-qism", has_textbook: true },
      { page_id: "uz-math-2", title: "Matematika 2-qism", has_textbook: true },
    ],
  },
};
const ruSingle: Record<string, LangAvailability> = {
  uz: { page_id: "uz-a", has_textbook: true, parts: [{ page_id: "uz-a", title: "Algebra", has_textbook: true }] },
  ru: { page_id: "ru-a", has_textbook: true, parts: [{ page_id: "ru-a", title: "Алгебра", has_textbook: true }] },
};
const ruMulti: Record<string, LangAvailability> = {
  ru: {
    page_id: "ru-1",
    has_textbook: true,
    parts: [
      { page_id: "ru-1", title: "Математика 1-часть", has_textbook: true },
      { page_id: "ru-2", title: "Математика 2-часть", has_textbook: true },
    ],
  },
};

// --- resolveNotionPageId: UZ output uses the clicked page, NEVER the map ---
assert.equal(resolveNotionPageId("uz-math-2", "uz", uzMulti), "uz-math-2",
  "UZ output must return the CLICKED page, even for a multi-part subject");
assert.equal(resolveNotionPageId("uz-math-1", "uz", uzMulti), "uz-math-1");
assert.equal(resolveNotionPageId("uz-x", "uz", null), "uz-x");

// --- resolveNotionPageId: cross-language single part → that part ---
assert.equal(resolveNotionPageId("uz-a", "ru", ruSingle), "ru-a");

// --- resolveNotionPageId: cross-language multi/zero part → null (ambiguous) ---
assert.equal(resolveNotionPageId("uz-1", "ru", ruMulti), null,
  "multi-part cross-language is ambiguous → null (caller must not fetch)");
assert.equal(resolveNotionPageId("uz-a", "en", ruSingle), null, "no en parts → null");

// --- partsFor: legacy shape (no `parts`) synthesizes a single part ---
assert.deepEqual(
  partsFor({ page_id: "p", has_textbook: true }).map((x) => x.page_id),
  ["p"], "legacy entry (no parts) → one synthesized part");
assert.deepEqual(partsFor({ page_id: "p", has_textbook: false }), [],
  "legacy entry with no textbook → no parts");
assert.deepEqual(partsFor(undefined), []);

// --- langChipState ---
assert.deepEqual(langChipState("uz", uzMulti, true), { available: true, multiPart: false, partCount: 2 });
assert.deepEqual(langChipState("ru", ruSingle, true), { available: true, multiPart: false, partCount: 1 });
assert.deepEqual(langChipState("ru", ruMulti, true), { available: false, multiPart: true, partCount: 2 });
assert.deepEqual(langChipState("en", ruSingle, true), { available: false, multiPart: false, partCount: 0 });
assert.deepEqual(langChipState("ru", null, false), { available: true, multiPart: false, partCount: 0 });

// --- N2: availability + resolution derive from textbook-having parts, NOT the
// top-level flag / raw part count. Part 1 has no PDF, part 2 does → ONE textbook
// → available, resolves to part 2 (not the top-level first part). ---
const ruMixed: Record<string, LangAvailability> = {
  ru: {
    page_id: "ru-1",
    has_textbook: false,
    parts: [
      { page_id: "ru-1", title: "часть 1", has_textbook: false },
      { page_id: "ru-2", title: "часть 2", has_textbook: true },
    ],
  },
};
assert.deepEqual(langChipState("ru", ruMixed, true), { available: true, multiPart: false, partCount: 1 },
  "chip availability must count only textbook-having parts, not the top-level flag");
assert.equal(resolveNotionPageId("uz-x", "ru", ruMixed), "ru-2",
  "cross-language resolves to the single TEXTBOOK-having part, not the top-level first part");

// --- resolveCandidate: single best-tier candidate auto-resolves, block_id ALWAYS present ---
const singleTextbook: NotionCandidate[] = [
  { page_id: "pg-1", block_id: "blk-1", filename: "Textbook.pdf", rank: 0 },
];
assert.deepEqual(
  resolveCandidate({ page_id: "pg-1", title: "Part 1", has_textbook: true, candidates: singleTextbook }),
  { status: "resolved", page_id: "pg-1", block_id: "blk-1" },
  "single best-tier candidate auto-resolves and always carries block_id",
);

// --- resolveCandidate: textbook (rank 0) + workbook (rank 2) is NOT ambiguous — auto-pick rank 0 ---
const textbookPlusWorkbook: NotionCandidate[] = [
  { page_id: "pg-1", block_id: "blk-tb", filename: "Textbook.pdf", rank: 0 },
  { page_id: "pg-1", block_id: "blk-wb", filename: "Workbook.pdf", rank: 2 },
];
assert.deepEqual(
  resolveCandidate({ page_id: "pg-1", title: "Part 1", has_textbook: true, candidates: textbookPlusWorkbook }),
  { status: "resolved", page_id: "pg-1", block_id: "blk-tb" },
  "rank-0 textbook + rank-2 workbook auto-picks the rank-0 textbook, not ambiguous",
);

// --- resolveCandidate: two candidates tied in the best (lowest) rank tier → ambiguous ---
const tiedBestTier: NotionCandidate[] = [
  { page_id: "pg-1", block_id: "blk-a", filename: "Matematika-A.pdf", rank: 0 },
  { page_id: "pg-1", block_id: "blk-b", filename: "Matematika-B.pdf", rank: 0 },
];
assert.deepEqual(
  resolveCandidate({ page_id: "pg-1", title: "Part 1", has_textbook: true, candidates: tiedBestTier }),
  { status: "ambiguous", candidates: tiedBestTier },
  "two candidates tied in the best rank tier must be exposed for an explicit pick",
);

// --- resolveCandidate: a neutral (rank 1) tie is ambiguous too, even though rank 0 is absent ---
const tiedNeutralTier: NotionCandidate[] = [
  { page_id: "pg-1", block_id: "blk-c", filename: "Doc-C.pdf", rank: 1 },
  { page_id: "pg-1", block_id: "blk-d", filename: "Doc-D.pdf", rank: 1 },
];
assert.deepEqual(
  resolveCandidate({ page_id: "pg-1", title: "Part 1", has_textbook: true, candidates: tiedNeutralTier }),
  { status: "ambiguous", candidates: tiedNeutralTier },
);

// --- resolveCandidate: child-page candidate resolves to the OWNING PART's
// page_id (not the child page's) + the candidate's block_id. A child page's
// direct parent is the SUBJECT page, not the language container, so
// verify_page_ancestry's hop-1 check fails if the child id is ever submitted
// as subject_page_id — block_id alone selects the file across the flattened
// candidate list (BE-19 final-review critical fix). ---
const childPageCandidate: NotionCandidate[] = [
  { page_id: "child-page-99", block_id: "blk-child", filename: "Nested.pdf", rank: 0 },
];
assert.deepEqual(
  resolveCandidate({ page_id: "parent-page-1", title: "Part 1", has_textbook: true, candidates: childPageCandidate }),
  { status: "resolved", page_id: "parent-page-1", block_id: "blk-child" },
  "a child-page candidate resolves to the OWNING PART's page_id, not the child page_id",
);

// --- resolveCandidate: legacy shape (no candidates key) falls back to the part's own
// page_id with an empty block_id, so pre-crawl-refresh data still works ---
assert.deepEqual(
  resolveCandidate({ page_id: "pg-legacy", title: "Part 1", has_textbook: true }),
  { status: "resolved", page_id: "pg-legacy", block_id: "" },
);
assert.deepEqual(
  resolveCandidate({ page_id: "pg-legacy", title: "Part 1", has_textbook: false }),
  { status: "none" },
);

// --- resolveCandidate: no part at all → none ---
assert.deepEqual(resolveCandidate(null), { status: "none" });
assert.deepEqual(resolveCandidate(undefined), { status: "none" });

// --- partForResolution: uz is clicked-page-authoritative — finds the matching part
// by page_id in the uz parts list so its candidates can be inspected ---
const uzPartsMap: Record<string, LangAvailability> = {
  uz: {
    page_id: "uz-math-1",
    has_textbook: true,
    parts: [
      {
        page_id: "uz-math-1",
        title: "Matematika 1-qism",
        has_textbook: true,
        candidates: [{ page_id: "uz-math-1", block_id: "blk-uz-1", filename: "Mat1.pdf", rank: 0 }],
      },
      {
        page_id: "uz-math-2",
        title: "Matematika 2-qism",
        has_textbook: true,
        candidates: [{ page_id: "uz-math-2", block_id: "blk-uz-2", filename: "Mat2.pdf", rank: 0 }],
      },
    ],
  },
};
assert.deepEqual(
  partForResolution("uz-math-2", "uz", uzPartsMap),
  uzPartsMap.uz.parts![1],
  "uz resolution finds the CLICKED page's part (not necessarily the first)",
);
// uz map not loaded yet → defensive synthesized bare part, not a crash
assert.deepEqual(partForResolution("uz-x", "uz", null), {
  page_id: "uz-x",
  title: "",
  has_textbook: true,
});

// --- partForResolution: cross-language single textbook-having part resolves to it ---
const crossLangSingle: Record<string, LangAvailability> = {
  ru: {
    page_id: "ru-a",
    has_textbook: true,
    parts: [
      {
        page_id: "ru-a",
        title: "Алгебра",
        has_textbook: true,
        candidates: [{ page_id: "ru-a", block_id: "blk-ru-a", filename: "Algebra.pdf", rank: 0 }],
      },
    ],
  },
};
assert.deepEqual(partForResolution("uz-a", "ru", crossLangSingle), crossLangSingle.ru.parts![0]);

// --- partForResolution: cross-language multi-part is ambiguous at the PART level → null
// (the pre-existing #86 rule; candidate-level resolution never even runs) ---
assert.equal(partForResolution("uz-1", "ru", ruMulti), null);

// --- End-to-end: resolveCandidate(partForResolution(...)) chains cleanly ---
assert.deepEqual(
  resolveCandidate(partForResolution("uz-math-1", "uz", uzPartsMap)),
  { status: "resolved", page_id: "uz-math-1", block_id: "blk-uz-1" },
);

// --- candidateSelectionState: selection is a stable id reconciled strictly
// against the CURRENT best tier, never a stored candidate snapshot. ---
const initialPickerPart: LangPart = {
  page_id: "picker-part",
  title: "Picker",
  has_textbook: true,
  candidates: [
    { page_id: "picker-part", block_id: "pick-a", filename: "A.pdf", rank: 0 },
    { page_id: "picker-part", block_id: "pick-b", filename: "B.pdf", rank: 0, book_status: "toc_extracting" },
    { page_id: "picker-part", block_id: "workbook", filename: "Workbook.pdf", rank: 2 },
  ],
};
const initialSelection = candidateSelectionState(
  initialPickerPart,
  resolveCandidate(initialPickerPart),
  "pick-b",
);
assert.equal(initialSelection.selected?.block_id, "pick-b");
assert.equal(initialSelection.active?.block_id, "pick-b");
assert.equal(initialSelection.selected?.book_status, "toc_extracting");
assert.equal(initialSelection.needsSelection, false);
assert.equal(initialSelection.invalidated, false);
assert.deepEqual(initialSelection.candidates.map((candidate) => candidate.block_id), ["pick-a", "pick-b"]);

// Same id, fresh object: the current status must replace the old snapshot.
const statusRefreshedPart: LangPart = {
  ...initialPickerPart,
  candidates: initialPickerPart.candidates!.map((candidate) =>
    candidate.block_id === "pick-b"
      ? { ...candidate, book_id: "book-b", book_status: "toc_ready" as const }
      : candidate,
  ),
};
const statusRefreshedSelection = candidateSelectionState(
  statusRefreshedPart,
  resolveCandidate(statusRefreshedPart),
  "pick-b",
);
assert.equal(statusRefreshedSelection.selected?.book_status, "toc_ready");
assert.notEqual(statusRefreshedSelection.selected, initialSelection.selected);

// Removal with a tied tier still present rejects the stale id and requires a
// new choice from the live option list.
const removedPart: LangPart = {
  ...initialPickerPart,
  candidates: [
    { page_id: "picker-part", block_id: "pick-a", filename: "A.pdf", rank: 0 },
    { page_id: "picker-part", block_id: "pick-c", filename: "C.pdf", rank: 0 },
  ],
};
const removedSelection = candidateSelectionState(removedPart, resolveCandidate(removedPart), "pick-b");
assert.equal(removedSelection.selected, null);
assert.equal(removedSelection.invalidated, true);
assert.equal(removedSelection.needsSelection, true);
assert.deepEqual(removedSelection.candidates.map((candidate) => candidate.block_id), ["pick-a", "pick-c"]);

// Re-ranking is also invalidation: presence anywhere on the part is not
// enough when the candidate has fallen out of the current best tier.
const rerankedPart: LangPart = {
  ...initialPickerPart,
  candidates: [
    { page_id: "picker-part", block_id: "pick-a", filename: "A.pdf", rank: 0 },
    { page_id: "picker-part", block_id: "pick-b", filename: "B.pdf", rank: 1 },
    { page_id: "picker-part", block_id: "pick-c", filename: "C.pdf", rank: 0 },
  ],
};
const rerankedSelection = candidateSelectionState(rerankedPart, resolveCandidate(rerankedPart), "pick-b");
assert.equal(rerankedSelection.selected, null);
assert.equal(rerankedSelection.invalidated, true);
assert.equal(rerankedSelection.needsSelection, true);
assert.deepEqual(rerankedSelection.candidates.map((candidate) => candidate.block_id), ["pick-a", "pick-c"]);

// Ambiguity collapsing to one candidate also invalidates the old explicit id;
// callers must not submit it through the stale picker.
const collapsedSelection = candidateSelectionState(
  {
    page_id: "picker-part",
    title: "Picker",
    has_textbook: true,
    candidates: [{ page_id: "picker-part", block_id: "pick-a", filename: "A.pdf", rank: 0 }],
  },
  resolveCandidate({
    page_id: "picker-part",
    title: "Picker",
    has_textbook: true,
    candidates: [{ page_id: "picker-part", block_id: "pick-a", filename: "A.pdf", rank: 0 }],
  }),
  "pick-b",
);
assert.equal(collapsedSelection.selected, null);
assert.equal(collapsedSelection.active?.block_id, "pick-a");
assert.equal(collapsedSelection.invalidated, true);
assert.equal(collapsedSelection.needsSelection, false);
assert.deepEqual(collapsedSelection.candidates, []);

// A part rollup may point at the only linked candidate even when that file is
// lower-ranked. The exact auto-resolved best-tier candidate must govern.
const lowerRankLinkedPart: LangPart = {
  page_id: "picker-part",
  title: "Picker",
  has_textbook: true,
  book_id: "workbook-book",
  book_status: "toc_ready",
  candidates: [
    { page_id: "picker-part", block_id: "textbook", filename: "Textbook.pdf", rank: 0 },
    {
      page_id: "picker-part",
      block_id: "workbook",
      filename: "Workbook.pdf",
      rank: 2,
      book_id: "workbook-book",
      book_status: "toc_ready",
    },
  ],
};
const exactAutoSelection = candidateSelectionState(
  lowerRankLinkedPart,
  resolveCandidate(lowerRankLinkedPart),
  null,
);
assert.equal(exactAutoSelection.active?.block_id, "textbook");
assert.equal(exactAutoSelection.active?.book_id, undefined);

console.log("notion-parts.test.ts: all assertions passed");
