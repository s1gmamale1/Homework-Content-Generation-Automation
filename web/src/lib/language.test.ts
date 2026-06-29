/**
 * Plain npx-tsx-runnable test for language.ts helpers.
 * Run: cd web && npx tsx src/lib/language.test.ts
 */
import assert from "node:assert/strict";
import { LANG_LABEL, langBadge, langAccent } from "./language";

// --- LANG_LABEL ---
assert.equal(LANG_LABEL.uz, "O‘zbek", "LANG_LABEL.uz should be O‘zbek");
assert.equal(LANG_LABEL.ru, "Русский", "LANG_LABEL.ru should be Русский");
assert.equal(LANG_LABEL.en, "English", "LANG_LABEL.en should be English");

// --- langBadge ---
const badgeUz = langBadge("uz");
const badgeRu = langBadge("ru");
const badgeEn = langBadge("en");

assert.ok(typeof badgeUz === "string" && badgeUz.length > 0, "langBadge('uz') should return non-empty string");
assert.ok(typeof badgeRu === "string" && badgeRu.length > 0, "langBadge('ru') should return non-empty string");
assert.ok(typeof badgeEn === "string" && badgeEn.length > 0, "langBadge('en') should return non-empty string");

// Each language should produce a distinct badge string (different accent colors)
assert.notEqual(badgeUz, badgeRu, "uz and ru badges should differ");
assert.notEqual(badgeRu, badgeEn, "ru and en badges should differ");
assert.notEqual(badgeUz, badgeEn, "uz and en badges should differ");

// Badge strings should contain expected Tailwind structural classes
assert.ok(badgeUz.includes("rounded"), "langBadge should include rounded class");
assert.ok(badgeUz.includes("font-mono"), "langBadge should include font-mono class");
assert.ok(badgeUz.includes("tracking"), "langBadge should include tracking class");

// --- langAccent ---
const accentUz = langAccent("uz");
const accentRu = langAccent("ru");
const accentEn = langAccent("en");

assert.ok(typeof accentUz === "string" && accentUz.length > 0, "langAccent('uz') should return non-empty string");
assert.ok(typeof accentRu === "string" && accentRu.length > 0, "langAccent('ru') should return non-empty string");
assert.ok(typeof accentEn === "string" && accentEn.length > 0, "langAccent('en') should return non-empty string");

// Accents should be distinct per language
assert.notEqual(accentUz, accentRu, "uz and ru accents should differ");
assert.notEqual(accentRu, accentEn, "ru and en accents should differ");
assert.notEqual(accentUz, accentEn, "uz and en accents should differ");

console.log("language.test.ts OK");
