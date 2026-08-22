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
 *
 * The UI consumes this type directly. There is no second wizard-shaped draft,
 * so persistence, narrowing and API payloads cannot drift into parallel models.
 */
import { type RegenerationScopeState, clampCanarySize } from "./api";
import {
  type LaunchDefaults,
  REGENERATION_OUTPUT_LANGUAGES,
  type RegenerationEligibleSource,
  type RegenerationOutputLanguage,
} from "./types";

export const REGENERATION_DRAFT_KEY = "hcga.regeneration.draft.v1";

/** Bumped whenever a field changes meaning. A draft written by any other
 *  version is discarded whole — half-reading fields whose meaning moved
 *  restores a draft nobody composed. */
const SCHEMA_VERSION = 1;

/** The lowest version a draft may CARRY. The first version the database ever
 *  allocates is 2 — `reserve_publication_version` computes `max(highest or 1,
 *  1) + 1` for a fresh lineage, and the publisher refuses `< 2` outright
 *  (`publication_version must be >= 2`) because logical V1 is the existing
 *  `Homework` page and owns no version row. A manual 2 is therefore a legal
 *  operator choice, and persistence may not quietly round it up. */
const MIN_PUBLICATION_VERSION = 2;

/** What a NEW draft opens on, and the floor the automatic display uses: a
 *  regeneration of an already-published lesson lands on V3 or above, so 3 is
 *  the number to show before any lesson has been picked. NOT the persistence
 *  floor — see `MIN_PUBLICATION_VERSION`. */
const INITIAL_PUBLICATION_VERSION = 3;

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
  /** Exact campaign-wide destination version, frozen by create. */
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
  "Your saved regeneration draft was written by a different version of this screen, so it was " +
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
    // Initialized once from the operator-controlled launch default at the
    // route boundary; a restored or edited choice is never overwritten.
    model: null,
    publicationVersion: INITIAL_PUBLICATION_VERSION,
    publicationVersionMode: "automatic",
    destinationOverrides: [],
  };
}

/** Apply the operator-controlled launch default only while the draft has no
 * explicit model. A restored or newly edited choice is authoritative. */
export function initializeDraftModel(
  draft: GuidedRegenerationDraft,
  defaults: Pick<LaunchDefaults, "content_provider" | "content_model">,
): GuidedRegenerationDraft {
  if (draft.model !== null || !defaults.content_provider || !defaults.content_model) return draft;
  return {
    ...draft,
    provider: defaults.content_provider,
    model: defaults.content_model,
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

/**
 * Fall back to a full rebuild ONLY when the selection is one the server would
 * refuse.
 *
 * The whole rule is `_PhaseSelectionIn._validate_phases`: *"if not
 * self.selected_phases and not self.refresh_extraction"* → "phase selection is
 * empty — pick at least one phase, or set refresh_extraction=true". Both
 * halves matter. An empty phase list WITH the extract refresh on is the
 * legitimate extract-only campaign — "re-read the textbook, regenerate
 * nothing" — and rewriting that to a full rebuild would launch an 11-phase
 * regeneration of every selected lesson that nobody asked for, at real
 * per-lesson cost. Only an empty list with the extract refresh OFF is
 * unlaunchable, and for that one the honest reading is the full rebuild the
 * wizard opens on.
 *
 * BOTH entry points enforce it. Pruning is what usually empties the list, but
 * a decode can produce the same state (a saved `selectedPhases` that is not an
 * array), and pruning only runs once `/eligible` and the manifest have
 * resolved — a failed fetch would otherwise leave the wizard holding a
 * selection the server cannot accept.
 */
function modeForPhases(
  mode: RegenerationMode,
  selectedPhases: string[],
  refreshExtraction: boolean,
): RegenerationMode {
  return mode === "selective" && selectedPhases.length === 0 && !refreshExtraction ? "full" : mode;
}

/** A saved version is believed whenever the server would accept it. Anything
 *  else — a float, a string, a 1 — falls back to what a new draft opens on. */
function decodePublicationVersion(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= MIN_PUBLICATION_VERSION
    ? value
    : INITIAL_PUBLICATION_VERSION;
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
  const selectedPhases = textList(raw.selectedPhases);
  const refreshExtraction = flag(raw.refreshExtraction, fallback.refreshExtraction);
  const mode = modeForPhases(
    known(REGENERATION_MODES, raw.mode, fallback.mode),
    selectedPhases,
    refreshExtraction,
  );
  return {
    schemaVersion: SCHEMA_VERSION,
    step: known(WIZARD_STEPS, raw.step, fallback.step),
    mode,
    subjectFilter: nullableText(raw.subjectFilter),
    gradeFilter: nullableText(raw.gradeFilter),
    bookId: nullableText(raw.bookId),
    language: known(REGENERATION_OUTPUT_LANGUAGES, raw.language, fallback.language),
    selectedTocEntryIds,
    selectedPhases: mode === "full" ? [] : selectedPhases,
    excludedPhases: mode === "full" ? [] : textList(raw.excludedPhases),
    acknowledged: mode === "full" ? false : flag(raw.acknowledged, fallback.acknowledged),
    // Clamped on the way in as well as on the way out: `canary_size` is POSTed
    // from state, and the server's refusal is a bare `ge=1`/`le=target_count`
    // validation payload rather than anything an operator can act on.
    canarySize: clampCanarySize(
      typeof raw.canarySize === "number" ? raw.canarySize : Number.NaN,
      selectedTocEntryIds.length,
    ),
    refreshExtraction,
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
  /** Exactly what `regenerationSelectablePhases(plan.canonical_phases)`
   *  returned — never the raw `canonical_phases`, which leads with `extract`.
   *  An `extract` that survives pruning ends up in `selected_phases`, where
   *  `build_phase_plan` raises `UnknownPhaseError` and the operator sees a
   *  422 on a chip the screen invited them to click. */
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
 * `bookId`, `subjectFilter` and `gradeFilter` are deliberately NOT reconciled
 * here even though a book can be deleted: narrowing belongs to
 * `regenerationNarrowScope`, and clearing a book here would have to clear the
 * lessons and the phase list with it — discarding more of the draft than the
 * operator actually lost. A book that vanished surfaces as an empty lesson
 * list, which the picker already renders as itself.
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
  // Built once and as a Set: a selection runs to hundreds of lessons, and
  // every surviving override is tested against it below.
  const selectedTocEntryIdSet = new Set(selectedTocEntryIds);
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
      mode: modeForPhases(draft.mode, selectedPhases, draft.refreshExtraction),
      // Consent to one exact set of skipped phases. A restored draft has not
      // been shown the set it would be consenting to.
      acknowledged: false,
      canarySize: clampCanarySize(draft.canarySize, selectedTocEntryIds.length),
      model: modelIsOffered ? draft.model : null,
      // An override names one lesson's publication destination inside ONE
      // language's Notion container, so it belongs to the draft only while
      // BOTH halves of that pair still hold: the lesson is still ticked after
      // pruning (which has already dropped every lesson that stopped being
      // eligible, so eligibility needs no second test), and its language is
      // still the draft's. An untargeted lesson keeps its eligibility, which
      // is why the first half reads the SELECTION and not `eligible`.
      //
      // The language half is not redundant with the first, and the reason is
      // the re-tick. `regenerationNarrowScope` clears `selectedTocEntryIds` on
      // a book change AND on a language change, but it is generic over
      // `RegenerationScopeState`, which declares no `destinationOverrides` —
      // so it cannot clear these. For a book change that is the end of it: the
      // lessons belonged to the old book and cannot come back. A language
      // change is different, because one book carries every language — the
      // operator re-ticks the SAME `tocEntryId` under the new language, the id
      // is legitimately back in the selection, and the override stranded by
      // the previous language rides along naming a page in the previous
      // language's container. Only the equality test catches that, so deleting
      // it because "narrowing already cleared the selection" reintroduces the
      // bug this filter was fixed for.
      //
      // The server revalidates every override against the reviewed language
      // container. This filter is the browser half of that same rule: it keeps
      // a known-stale page choice out of the next destination review request.
      destinationOverrides: draft.destinationOverrides.filter(
        (override) =>
          selectedTocEntryIdSet.has(override.tocEntryId) &&
          override.outputLanguage === draft.language,
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
  draft: Pick<GuidedRegenerationDraft, "mode" | "selectedPhases">,
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
 * already on V3 pulls the whole campaign to V4, and the AUTOMATIC display
 * floors at V3 — the number this wizard opens on. That floor is not a limit on
 * what a campaign may publish: editing the number flips the mode to `manual`,
 * and from then on the exact figure the operator typed is what is persisted
 * and shown — down to the persistence floor of V2, the first version the
 * database ever allocates — whatever the sources do underneath it.
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
    INITIAL_PUBLICATION_VERSION,
  );
}
