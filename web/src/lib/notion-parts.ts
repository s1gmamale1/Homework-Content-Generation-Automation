import type { LangAvailability, LangPart, OutputLanguage } from "./types";

/** Parts for a language, tolerating the legacy shape (no `parts`): synthesize a
 *  single part from the top-level page_id when a textbook exists. */
export function partsFor(info: LangAvailability | undefined): LangPart[] {
  if (!info) return [];
  if (info.parts && info.parts.length > 0) return info.parts;
  return info.has_textbook
    ? [{ page_id: info.page_id, title: "", has_textbook: true }]
    : [];
}

/** Which Notion page to fetch for a prepare / from-notion call.
 *
 *  The subject picker is sourced from the UZ ("N - sinf") container, so
 *  `clickedPageId` is always the UZ part the operator explicitly selected.
 *  - `language === "uz"`: the clicked page is authoritative — NEVER overridden
 *    by the app_subject-keyed availability map, which would resolve a multi-part
 *    subject to the wrong part (notion-multipart-subject-clobber-1).
 *  - other language: translate via the map. Exactly one textbook-having part →
 *    that part. Zero or multiple → null (caller must not fetch; 0 = no page,
 *    >1 = ambiguous, surfaced to the operator as a disabled chip). */
export function resolveNotionPageId(
  clickedPageId: string,
  language: OutputLanguage,
  langMap: Record<string, LangAvailability> | null | undefined,
): string | null {
  if (language === "uz") return clickedPageId;
  const withText = partsFor(langMap?.[language]).filter((p) => p.has_textbook);
  return withText.length === 1 ? withText[0].page_id : null;
}

/** Language-chip state for the prepare flow. Availability is derived from the
 *  textbook-having parts, NOT the backward-compat top-level `has_textbook` flag
 *  (which is pinned to the first part and would lie when part 1 has no PDF but a
 *  later part does).
 *  - map not loaded → fail-open (available), so chips aren't wrongly disabled.
 *  - UZ → available iff any textbook part exists (single explicit part per pick).
 *  - other language → available iff exactly one textbook part; >1 is ambiguous in
 *    v1 → disabled with `multiPart` set (locked decision: disable + hint). */
export function langChipState(
  language: OutputLanguage,
  langMap: Record<string, LangAvailability> | null | undefined,
  mapLoaded: boolean,
): { available: boolean; multiPart: boolean; partCount: number } {
  if (!mapLoaded) return { available: true, multiPart: false, partCount: 0 };
  const withText = partsFor(langMap?.[language]).filter((p) => p.has_textbook);
  if (language === "uz") {
    return { available: withText.length > 0, multiPart: false, partCount: withText.length };
  }
  if (withText.length === 0) return { available: false, multiPart: false, partCount: 0 };
  if (withText.length > 1) return { available: false, multiPart: true, partCount: withText.length };
  return { available: true, multiPart: false, partCount: 1 };
}
