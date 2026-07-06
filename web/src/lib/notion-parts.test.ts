/**
 * Plain npx-tsx-runnable test for notion-parts.ts helpers.
 * Run: cd web && npx tsx src/lib/notion-parts.test.ts
 */
import assert from "node:assert/strict";
import type { LangAvailability } from "./types";
import { partsFor, resolveNotionPageId, langChipState } from "./notion-parts";

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

console.log("notion-parts.test.ts: all assertions passed");
