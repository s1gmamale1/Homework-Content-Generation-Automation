/**
 * The guided regeneration wizard's DRAFT — what the operator has chosen so
 * far, and the only thing on that screen that outlives a reload.
 *
 * Composing a campaign is long work: a book, a language, a set of lessons, a
 * phase plan, a model, a publication version and any destination the operator
 * had to correct by hand. Losing it to an accidental refresh means doing all
 * of it again, so it is written to `localStorage` under a VERSIONED key and
 * read back defensively.
 *
 * Three rules hold this together.
 *
 * **Operator input only.** Estimates, phase plans, destination-check results
 * and campaign digests are server truth: they are refetched on the next mount
 * and have no field here. Persisting a derived number is how a screen ends up
 * showing a price that was never quoted for the draft on screen.
 *
 * **Nothing here throws.** Storage can refuse — a blocked partition throws
 * `SecurityError` on read, Safari's private mode throws on write, a crashed
 * tab leaves half a key behind. Every one of those degrades to "start blank,
 * and say so"; none of them may take down a wizard with money at the end of
 * it. Decoding is field by field for the same reason: a saved draft is
 * untrusted input, not a `GuidedRegenerationDraft` waiting for a cast.
 *
 * **What comes back is pruned against what still exists.** A lesson can lose
 * its eligibility, a phase can leave a subject's flow and a model can be
 * retired while a draft sits in a closed tab. `pruneRegenerationDraft` drops
 * each of those, and always clears the exclusion acknowledgement — that is
 * consent to one exact set of skipped phases, and a restored draft has not
 * been shown the set it would be consenting to.
 *
 * Reusing `RegenerationScopeState`'s own `subjectFilter`/`gradeFilter`/`bookId`
 * names is deliberate: `regenerationNarrowScope` and
 * `regenerationToggleLesson` stay the ONE authority for what a scope change
 * clears, and this module never restates their rules.
 */
import { type RegenerationScopeState, clampCanarySize } from "./api";
import {
  REGENERATION_OUTPUT_LANGUAGES,
  type RegenerationEligibleSource,
  type RegenerationOutputLanguage,
} from "./types";

export const REGENERATION_DRAFT_KEY = "hcga.regeneration.draft.v1";

/** Bumped whenever a field changes meaning. A draft written by any other
 *  version is discarded whole — half-reading fields whose meaning moved
 *  restores a draft nobody composed. */
const SCHEMA_VERSION = 1;

/** Regeneration publishes V3 upward: a source homework is V1 or V2, so the
 *  first version this wizard can ever produce is 3. */
const MIN_PUBLICATION_VERSION = 3;

export type RegenerationWizardStep = "lessons" | "content" | "review" | "canary";
export type RegenerationMode = "full" | "selective";

const WIZARD_STEPS: RegenerationWizardStep[] = ["lessons", "content", "review", "canary"];
const REGENERATION_MODES: RegenerationMode[] = ["full", "selective"];
const PUBLICATION_VERSION_MODES: ("automatic" | "manual")[] = ["automatic", "manual"];

/** One lesson published somewhere other than where the resolver would put it. */
export interface DestinationOverrideDraft {
  tocEntryId: string;
  outputLanguage: RegenerationOutputLanguage;
  notionLessonPageId: string;
}

export interface GuidedRegenerationDraft extends RegenerationScopeState {
  schemaVersion: 1;
  step: RegenerationWizardStep;
  mode: RegenerationMode;
  refreshExtraction: boolean;
  provider: string;
  model: string | null;
  publicationVersion: number;
  publicationVersionMode: "automatic" | "manual";
  destinationOverrides: DestinationOverrideDraft[];
}

/* ── storage, injected ────────────────────────────────────────────────── */

/** The three `Storage` methods this codec uses. `window.localStorage`
 *  satisfies it; so does a stub, which is what makes the codec testable in a
 *  DOM-less `node --test` run. */
export interface DraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface RegenerationDraftLoad {
  draft: GuidedRegenerationDraft;
  /** Prose for the operator, or null when there was nothing to say. */
  warning: string | null;
}

export interface RegenerationDraftWrite {
  warning: string | null;
}

export const REGENERATION_DRAFT_UNREADABLE_WARNING =
  "Your saved regeneration draft could not be read, so this wizard started from a blank draft.";

export const REGENERATION_DRAFT_STALE_WARNING =
  "Your saved regeneration draft was written by an older version of this screen, so it was " +
  "discarded and this wizard started from a blank draft.";

export const REGENERATION_DRAFT_SAVE_WARNING =
  "This browser would not save your regeneration draft, so these choices will not survive a reload.";

export const REGENERATION_DRAFT_CLEAR_WARNING =
  "Your saved regeneration draft could not be cleared, so it may reappear the next time you open " +
  "this screen.";

export function defaultGuidedRegenerationDraft(): GuidedRegenerationDraft {
  return {
    schemaVersion: SCHEMA_VERSION,
    step: "lessons",
    // A full rebuild is the honest default: it is what "regenerate this
    // lesson" means before anyone has opted into a narrower plan.
    mode: "full",
    subjectFilter: null,
    gradeFilter: null,
    bookId: null,
    language: "uz",
    selectedTocEntryIds: [],
    selectedPhases: [],
    excludedPhases: [],
    acknowledged: false,
    canarySize: 1,
    // The extract is the expensive whole-PDF read; re-running it is a separate,
    // deliberate choice.
    refreshExtraction: false,
    provider: "gemini",
    // Never defaulted for the operator: the server refuses a campaign with no
    // content model precisely so nobody freezes a campaign onto whatever
    // happens to be first in the manifest.
    model: null,
    publicationVersion: MIN_PUBLICATION_VERSION,
    publicationVersionMode: "automatic",
    destinationOverrides: [],
  };
}

/* ── decoding untrusted JSON ──────────────────────────────────────────── */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Keep a saved value only when it is one this build knows. No cast: the match
 *  comes out of the known-values list, so it is typed by construction. */
function known<T extends string>(allowed: readonly T[], value: unknown, fallback: T): T {
  if (typeof value !== "string") return fallback;
  return allowed.find((candidate) => candidate === value) ?? fallback;
}

/** Optional text. A number, an object or a blank string all mean "not chosen". */
function nullableText(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function text(value: unknown, fallback: string): string {
  return nullableText(value) ?? fallback;
}

function flag(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

/** A list of ids or phase names. Non-strings are dropped and duplicates
 *  collapse — a repeated toc id would double-count a lesson in the target
 *  count, the estimate and the canary. */
function textList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const strings = value.filter((item): item is string => typeof item === "string" && item !== "");
  return [...new Set(strings)];
}

function decodePublicationVersion(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= MIN_PUBLICATION_VERSION
    ? value
    : MIN_PUBLICATION_VERSION;
}

/** An override with no lesson, no destination page or a language this product
 *  does not publish cannot be honoured, and there is nothing to repair it
 *  into — so the row is dropped rather than half-restored. */
function decodeDestinationOverrides(value: unknown): DestinationOverrideDraft[] {
  if (!Array.isArray(value)) return [];
  const decoded: DestinationOverrideDraft[] = [];
  for (const row of value) {
    if (!isRecord(row)) continue;
    const tocEntryId = nullableText(row.tocEntryId);
    const notionLessonPageId = nullableText(row.notionLessonPageId);
    const rawLanguage = row.outputLanguage;
    if (!tocEntryId || !notionLessonPageId || typeof rawLanguage !== "string") continue;
    const outputLanguage = REGENERATION_OUTPUT_LANGUAGES.find(
      (candidate) => candidate === rawLanguage,
    );
    if (!outputLanguage) continue;
    decoded.push({ tocEntryId, outputLanguage, notionLessonPageId });
  }
  return decoded;
}

function decodeDraft(raw: Record<string, unknown>): GuidedRegenerationDraft {
  const fallback = defaultGuidedRegenerationDraft();
  const selectedTocEntryIds = textList(raw.selectedTocEntryIds);
  return {
    schemaVersion: SCHEMA_VERSION,
    step: known(WIZARD_STEPS, raw.step, fallback.step),
    mode: known(REGENERATION_MODES, raw.mode, fallback.mode),
    subjectFilter: nullableText(raw.subjectFilter),
    gradeFilter: nullableText(raw.gradeFilter),
    bookId: nullableText(raw.bookId),
    language: known(REGENERATION_OUTPUT_LANGUAGES, raw.language, fallback.language),
    selectedTocEntryIds,
    selectedPhases: textList(raw.selectedPhases),
    excludedPhases: textList(raw.excludedPhases),
    acknowledged: flag(raw.acknowledged, fallback.acknowledged),
    // Clamped on the way in as well as on the way out: `canary_size` is POSTed
    // from state, and the server's refusal is a bare `ge=1`/`le=target_count`
    // validation payload rather than anything an operator can act on.
    canarySize: clampCanarySize(
      typeof raw.canarySize === "number" ? raw.canarySize : Number.NaN,
      selectedTocEntryIds.length,
    ),
    refreshExtraction: flag(raw.refreshExtraction, fallback.refreshExtraction),
    provider: text(raw.provider, fallback.provider),
    model: nullableText(raw.model),
    publicationVersion: decodePublicationVersion(raw.publicationVersion),
    publicationVersionMode: known(
      PUBLICATION_VERSION_MODES,
      raw.publicationVersionMode,
      fallback.publicationVersionMode,
    ),
    destinationOverrides: decodeDestinationOverrides(raw.destinationOverrides),
  };
}

/* ── the three storage calls ──────────────────────────────────────────── */

export function loadRegenerationDraft(storage: DraftStorage): RegenerationDraftLoad {
  const blank = defaultGuidedRegenerationDraft();
  let raw: string | null;
  try {
    raw = storage.getItem(REGENERATION_DRAFT_KEY);
  } catch {
    return { draft: blank, warning: REGENERATION_DRAFT_UNREADABLE_WARNING };
  }
  if (raw === null) return { draft: blank, warning: null };

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { draft: blank, warning: REGENERATION_DRAFT_UNREADABLE_WARNING };
  }
  if (!isRecord(parsed)) return { draft: blank, warning: REGENERATION_DRAFT_UNREADABLE_WARNING };
  if (parsed.schemaVersion !== SCHEMA_VERSION) {
    return { draft: blank, warning: REGENERATION_DRAFT_STALE_WARNING };
  }
  return { draft: decodeDraft(parsed), warning: null };
}

export function saveRegenerationDraft(
  storage: DraftStorage,
  draft: GuidedRegenerationDraft,
): RegenerationDraftWrite {
  try {
    storage.setItem(REGENERATION_DRAFT_KEY, JSON.stringify(draft));
    return { warning: null };
  } catch {
    return { warning: REGENERATION_DRAFT_SAVE_WARNING };
  }
}

export function clearRegenerationDraft(storage: DraftStorage): RegenerationDraftWrite {
  try {
    storage.removeItem(REGENERATION_DRAFT_KEY);
    return { warning: null };
  } catch {
    // Reported rather than swallowed: a clear that failed means a launched
    // draft walks back in on the next visit.
    return { warning: REGENERATION_DRAFT_CLEAR_WARNING };
  }
}

/* ── pruning a restored draft onto what still exists ──────────────────── */

export interface RegenerationDraftPruneInputs {
  /** Lessons `/eligible` still returns for this book and language. */
  eligibleTocEntryIds: ReadonlySet<string>;
  /** `provider/model` pairs the manifest still offers. */
  validModelRefs: ReadonlySet<string>;
  /** Phase names in this subject's own flow. */
  validPhaseNames: ReadonlySet<string>;
}

export interface RegenerationDraftPruneResult {
  draft: GuidedRegenerationDraft;
  /** How many ticked lessons stopped being regenerable while the draft sat. */
  removedLessonCount: number;
}

/**
 * Reconcile a restored draft with what the server still offers.
 *
 * Only the model, not the provider, is checked against `validModelRefs`: a
 * retired MODEL is a real event (`RETIRED_GEMINI_MODELS` 404 on the live API
 * and are refused on every reactivation path), and clearing it forces the
 * re-pick the server would demand anyway. The provider survives so the model
 * picker opens where the operator left it.
 */
export function pruneRegenerationDraft(
  draft: GuidedRegenerationDraft,
  inputs: RegenerationDraftPruneInputs,
): RegenerationDraftPruneResult {
  const selectedTocEntryIds = draft.selectedTocEntryIds.filter((id) =>
    inputs.eligibleTocEntryIds.has(id),
  );
  const selectedPhases = draft.selectedPhases.filter((phase) => inputs.validPhaseNames.has(phase));
  const excludedPhases = draft.excludedPhases.filter((phase) => inputs.validPhaseNames.has(phase));
  const modelIsOffered =
    draft.model !== null && inputs.validModelRefs.has(`${draft.provider}/${draft.model}`);

  return {
    draft: {
      ...draft,
      selectedTocEntryIds,
      selectedPhases,
      excludedPhases,
      // Selective with nothing left to select is a campaign the server refuses
      // outright; the honest reading of "every phase I picked is gone" is a
      // full rebuild, which is also what the wizard opens on.
      mode: draft.mode === "selective" && selectedPhases.length === 0 ? "full" : draft.mode,
      // Consent to one exact set of skipped phases. A restored draft has not
      // been shown the set it would be consenting to.
      acknowledged: false,
      canarySize: clampCanarySize(draft.canarySize, selectedTocEntryIds.length),
      model: modelIsOffered ? draft.model : null,
      // An override names a publication destination for one lesson; a lesson
      // with no regenerable lineage has nothing left to publish.
      destinationOverrides: draft.destinationOverrides.filter((override) =>
        inputs.eligibleTocEntryIds.has(override.tocEntryId),
      ),
    },
    removedLessonCount: draft.selectedTocEntryIds.length - selectedTocEntryIds.length,
  };
}

/* ── what the draft actually asks for ─────────────────────────────────── */

/**
 * The phases this draft regenerates.
 *
 * Callers pass `regenerationSelectablePhases(plan.canonical_phases)`, NEVER
 * the raw `canonical_phases`: that array is `("extract", *flow_for(subject))`,
 * and `build_phase_plan(selected_phases=["extract"])` raises
 * `UnknownPhaseError`. Stripping the extract stays that one function's job —
 * the extract has its own switch, `refreshExtraction`.
 */
export function effectiveSelectedPhases(
  draft: GuidedRegenerationDraft,
  canonicalPhases: string[],
): string[] {
  return draft.mode === "full" ? [...canonicalPhases] : [...draft.selectedPhases];
}

/** Just the field the version floor reads — a real `RegenerationEligibleSource`
 *  satisfies it, and nothing has to invent the other thirteen. */
export type PublicationVersionSource = Pick<RegenerationEligibleSource, "next_expected_version">;

/**
 * The publication version to SHOW, which is derived and therefore never stored
 * on its own.
 *
 * While the mode is `automatic` it tracks the selected lessons: one lesson
 * already on V3 pulls the whole campaign to V4, and a campaign can never
 * publish below V3. Editing the number is what flips the mode to `manual`, and
 * from then on the exact figure the operator typed is what is persisted and
 * shown, whatever the sources do underneath it.
 */
export function displayedPublicationVersion(
  draft: GuidedRegenerationDraft,
  selectedSources: readonly PublicationVersionSource[],
): number {
  if (draft.publicationVersionMode === "manual") return draft.publicationVersion;
  return selectedSources.reduce(
    (highest, source) =>
      Number.isFinite(source.next_expected_version)
        ? Math.max(highest, source.next_expected_version)
        : highest,
    MIN_PUBLICATION_VERSION,
  );
}
