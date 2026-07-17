import type { LangAvailability, LangPart, NotionCandidate, OutputLanguage } from "./types";

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

/** Locate the specific `LangPart` backing the page a prepare call would fetch,
 *  so its `candidates` (BE-19 task 6 file-level disambiguation) can be
 *  inspected. Mirrors `resolveNotionPageId`'s authority rules exactly, but
 *  returns the part object instead of a bare page_id:
 *  - `uz`: the clicked page is authoritative — find it by `page_id` in the uz
 *    parts list. Falls back to a bare synthesized part (no candidates) if the
 *    map hasn't loaded yet or doesn't have this page, so an in-flight
 *    availability fetch never blocks the uz fetch path.
 *  - other language: the single textbook-having part, same ambiguity rule as
 *    `resolveNotionPageId` (zero or >1 → null; the multi-part chip is
 *    disabled in that case, so candidate-level resolution never runs). */
export function partForResolution(
  clickedPageId: string,
  language: OutputLanguage,
  langMap: Record<string, LangAvailability> | null | undefined,
): LangPart | null {
  if (language === "uz") {
    const parts = partsFor(langMap?.uz);
    return (
      parts.find((p) => p.page_id === clickedPageId) ??
      { page_id: clickedPageId, title: "", has_textbook: true }
    );
  }
  const withText = partsFor(langMap?.[language]).filter((p) => p.has_textbook);
  return withText.length === 1 ? withText[0] : null;
}

/** Which file to fetch for a resolved part, from its `candidates`. */
export type CandidateResolution =
  | { status: "resolved"; page_id: string; block_id: string }
  | { status: "ambiguous"; candidates: NotionCandidate[] }
  | { status: "none" };

/** Reconcile a picker selection against the CURRENT best-rank tier.
 *
 * Consumers keep only `selectedBlockId` in state. Candidate objects and
 * options always come from the latest availability response, so a removed or
 * re-ranked candidate is invalidated instead of being submitted from a stale
 * snapshot.
 */
export interface CandidateSelectionState {
  candidates: NotionCandidate[];
  selected: NotionCandidate | null;
  /** Candidate whose system status governs the prepare action: the explicit
   *  current selection for an ambiguous tier, or the exact auto-resolved
   *  candidate for a single best tier. `null` only for none/legacy shapes. */
  active: NotionCandidate | null;
  needsSelection: boolean;
  invalidated: boolean;
}

export function candidateSelectionState(
  part: LangPart | null | undefined,
  resolution: CandidateResolution,
  selectedBlockId: string | null | undefined,
): CandidateSelectionState {
  const candidates = resolution.status === "ambiguous" ? resolution.candidates : [];
  const selected = selectedBlockId
    ? (candidates.find((candidate) => candidate.block_id === selectedBlockId) ?? null)
    : null;
  const active = resolution.status === "resolved" && resolution.block_id
    ? (part?.candidates?.find((candidate) => candidate.block_id === resolution.block_id) ?? null)
    : selected;
  return {
    candidates,
    selected,
    active,
    needsSelection: resolution.status === "ambiguous" && selected === null,
    invalidated: !!selectedBlockId && selected === null,
  };
}

/** Lowest-rank (most authoritative) tier of a part's candidates: 0=textbook,
 *  1=neutral, 2=workbook. Only ties WITHIN this tier are ambiguous — a rank-0
 *  textbook alongside a rank-2 workbook is not, mirroring the backend's tier
 *  logic exactly. */
function bestTier(candidates: NotionCandidate[] | undefined): NotionCandidate[] {
  if (!candidates || candidates.length === 0) return [];
  const minRank = Math.min(...candidates.map((c) => c.rank));
  return candidates.filter((c) => c.rank === minRank);
}

/** Resolve which file a part's prepare call should fetch.
 *  - Exactly one candidate in the best tier → auto-resolve to it, ALWAYS
 *    returning its `block_id` (explicit even when unambiguous — free, and
 *    immunizes against a Notion-side reorder between crawl and prepare).
 *  - >1 candidate tied in the best tier → `ambiguous`; caller renders a
 *    picker (mirrors the backend's `ambiguous_textbook` 422).
 *  - A candidate's `page_id` may be a CHILD page distinct from the owning
 *    part's `page_id` (nested parts) — resolution always returns the OWNING
 *    PART's `page_id`, never the candidate's. The backend's
 *    `verify_page_ancestry` requires the submitted page's DIRECT parent to be
 *    the language container; a child page's parent is the subject page, not
 *    the container, so submitting the child id fails ancestry (BE-19
 *    final-review critical fix). `download_textbook` matches candidates by
 *    `block_id` across the flattened list regardless of which page the PDF
 *    physically lives on, so the part's page_id + the candidate's block_id is
 *    always sufficient to fetch a child-hosted file.
 *  - No `candidates` at all (legacy shape, pre-crawl-refresh) → fall back to
 *    the part's own `page_id` with an empty `block_id` so older data still
 *    resolves; `none` if the part has no textbook. */
export function resolveCandidate(part: LangPart | null | undefined): CandidateResolution {
  if (!part) return { status: "none" };
  const tier = bestTier(part.candidates);
  if (tier.length === 0) {
    return part.has_textbook
      ? { status: "resolved", page_id: part.page_id, block_id: "" }
      : { status: "none" };
  }
  if (tier.length === 1) {
    const c = tier[0];
    return { status: "resolved", page_id: part.page_id, block_id: c.block_id };
  }
  return { status: "ambiguous", candidates: tier };
}
