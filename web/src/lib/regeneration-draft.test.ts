/**
 * Codec tests for the persisted guided-regeneration draft (Task 5).
 *
 * `npm test` runs `node --import tsx --test src/lib/*.test.ts`: no DOM, no
 * React renderer, no TanStack Query — and therefore no `window.localStorage`.
 * That absence is the reason the codec takes its storage as an ARGUMENT, and
 * it is what makes every rule below testable here: the draft is a plain value,
 * the storage is a three-method stub, and both halves of the trip are pure.
 *
 * Two contract facts drive most of the assertions.
 *
 * **Nothing this codec does may throw.** The draft is read at mount and
 * written on every keystroke of a wizard that has real money at the end of it.
 * A `SecurityError` from a blocked storage partition, a quota refusal in
 * Safari's private mode, or a half-written key left by a crashed tab must each
 * degrade to "start from a blank draft, and say so" — never to a white screen.
 *
 * **What is restored is operator INPUT, never derived state.** Estimates,
 * phase plans and destination checks are recomputed from the server on the
 * next mount; a lesson that stopped being eligible, a phase that left the
 * subject's flow and a model the manifest retired are pruned out on the way
 * back in, and the exclusion acknowledgement — consent to one exact set of
 * skipped phases — never survives a restore.
 */
import assert from "node:assert";
import { test } from "node:test";
import { regenerationSelectablePhases } from "./api";
import {
  type DraftStorage,
  type GuidedRegenerationDraft,
  REGENERATION_DRAFT_CLEAR_WARNING,
  REGENERATION_DRAFT_KEY,
  REGENERATION_DRAFT_SAVE_WARNING,
  REGENERATION_DRAFT_STALE_WARNING,
  REGENERATION_DRAFT_UNREADABLE_WARNING,
  clearRegenerationDraft,
  defaultGuidedRegenerationDraft,
  displayedPublicationVersion,
  effectiveSelectedPhases,
  loadRegenerationDraft,
  pruneRegenerationDraft,
  saveRegenerationDraft,
} from "./regeneration-draft";

/* ────────────────────────────────────────────────────────────────────
 * stubs and fixtures
 * ──────────────────────────────────────────────────────────────────── */

/** A `Storage`-shaped stand-in for `window.localStorage`. */
function memoryStorage(seed: Record<string, string> = {}): DraftStorage {
  const items = new Map(Object.entries(seed));
  return {
    getItem: (key) => items.get(key) ?? null,
    setItem: (key, value) => {
      items.set(key, value);
    },
    removeItem: (key) => {
      items.delete(key);
    },
  };
}

/** Storage that refuses. Every browser refusal this codec can meet arrives as
 *  a throw from one of these three methods, never as a return value. */
function refusingStorage(over: Partial<DraftStorage>): DraftStorage {
  const base = memoryStorage();
  return { ...base, ...over };
}

/** The server's own `canonical_phases`: `("extract", *flow_for(subject))`. */
const SERVER_CANONICAL_PHASES = [
  "extract",
  "case-based-preview",
  "flashcards",
  "memory-check",
  "practice-rlc",
  "practice-error-detection",
  "practice-memory-match",
  "practice-tictactoe",
  "practice-jigsaw",
  "practice-sentence",
  "boss-arena",
  "reflection",
];

/** A draft mid-compose: two lessons ticked, a phase list, an acknowledged
 *  exclusion, a pinned model. Exactly the thing a reload must not lose. */
const savedDraft: GuidedRegenerationDraft = {
  ...defaultGuidedRegenerationDraft(),
  step: "review",
  mode: "selective",
  selectedTocEntryIds: ["kept", "gone"],
  selectedPhases: ["reflection", "practice-abacus"],
  excludedPhases: ["reflection", "practice-abacus"],
  acknowledged: true,
  canarySize: 2,
  provider: "gemini",
  model: "gemini-3.6-flash",
  destinationOverrides: [
    { tocEntryId: "kept", outputLanguage: "uz", notionLessonPageId: "page-kept" },
    { tocEntryId: "gone", outputLanguage: "uz", notionLessonPageId: "page-gone" },
  ],
};

/* ════════════════════════════════════════════════════════════════════
 * 1. The default draft
 * ════════════════════════════════════════════════════════════════════ */

test("default draft is V3 full rebuild with extraction off", () => {
  const draft = defaultGuidedRegenerationDraft();
  assert.equal(draft.mode, "full");
  assert.equal(draft.publicationVersion, 3);
  assert.equal(draft.refreshExtraction, false);
  assert.equal(draft.step, "lessons");
});

test("the default draft picks no model and nothing to publish over", () => {
  const draft = defaultGuidedRegenerationDraft();
  // The server refuses a campaign with no content model precisely so nobody
  // freezes a whole campaign onto whatever happens to be first in a manifest.
  assert.strictEqual(draft.model, null);
  assert.strictEqual(draft.publicationVersionMode, "automatic");
  assert.deepStrictEqual(draft.destinationOverrides, []);
  assert.deepStrictEqual(draft.selectedTocEntryIds, []);
  assert.strictEqual(draft.acknowledged, false);
  assert.strictEqual(draft.canarySize, 1);
  assert.strictEqual(draft.schemaVersion, 1);
});

/* ════════════════════════════════════════════════════════════════════
 * 2. Round trip — the whole point of persisting anything
 * ════════════════════════════════════════════════════════════════════ */

test("a saved draft comes back with every operator choice intact", () => {
  const storage = memoryStorage();
  assert.strictEqual(saveRegenerationDraft(storage, savedDraft).warning, null);

  const { draft, warning } = loadRegenerationDraft(storage);
  assert.strictEqual(warning, null);
  assert.deepStrictEqual(draft, savedDraft);
});

test("the draft is stored under the versioned key, as JSON", () => {
  const storage = memoryStorage();
  saveRegenerationDraft(storage, savedDraft);
  const raw = storage.getItem(REGENERATION_DRAFT_KEY);
  assert.ok(raw, `nothing was written to ${REGENERATION_DRAFT_KEY}`);
  assert.strictEqual((JSON.parse(raw) as { schemaVersion: unknown }).schemaVersion, 1);
});

test("an empty storage yields the default draft with nothing to report", () => {
  const { draft, warning } = loadRegenerationDraft(memoryStorage());
  assert.strictEqual(warning, null);
  assert.deepStrictEqual(draft, defaultGuidedRegenerationDraft());
});

test("clearing the draft leaves the next load on a blank draft", () => {
  const storage = memoryStorage();
  saveRegenerationDraft(storage, savedDraft);
  assert.strictEqual(clearRegenerationDraft(storage).warning, null);
  assert.strictEqual(storage.getItem(REGENERATION_DRAFT_KEY), null);
  assert.deepStrictEqual(loadRegenerationDraft(storage).draft, defaultGuidedRegenerationDraft());
});

/* ════════════════════════════════════════════════════════════════════
 * 3. Every way reading a draft can fail
 * ════════════════════════════════════════════════════════════════════ */

test("corrupt JSON yields the default draft and a warning, never a throw", () => {
  const storage = memoryStorage({ [REGENERATION_DRAFT_KEY]: '{"schemaVersion":1,"step":' });
  const { draft, warning } = loadRegenerationDraft(storage);
  assert.deepStrictEqual(draft, defaultGuidedRegenerationDraft());
  assert.strictEqual(warning, REGENERATION_DRAFT_UNREADABLE_WARNING);
});

test("a saved draft that is not an object yields the default draft", () => {
  for (const raw of ["null", "[]", '"draft"', "7", "true"]) {
    const { draft, warning } = loadRegenerationDraft(
      memoryStorage({ [REGENERATION_DRAFT_KEY]: raw }),
    );
    assert.deepStrictEqual(draft, defaultGuidedRegenerationDraft(), `survived ${raw}`);
    assert.strictEqual(warning, REGENERATION_DRAFT_UNREADABLE_WARNING, `no warning for ${raw}`);
  }
});

test("an unknown schema version is discarded rather than half-read", () => {
  const stale = JSON.stringify({ ...savedDraft, schemaVersion: 2 });
  const { draft, warning } = loadRegenerationDraft(
    memoryStorage({ [REGENERATION_DRAFT_KEY]: stale }),
  );
  assert.deepStrictEqual(draft, defaultGuidedRegenerationDraft());
  assert.strictEqual(warning, REGENERATION_DRAFT_STALE_WARNING);
  // A version 1 codec cannot know what a version 2 field means; reading the
  // fields it recognises would restore a draft nobody composed.
  assert.deepStrictEqual(draft.selectedTocEntryIds, []);
});

test("a storage read exception yields the default draft and a warning", () => {
  const storage = refusingStorage({
    getItem: () => {
      throw new Error("SecurityError: access to storage is not allowed");
    },
  });
  const { draft, warning } = loadRegenerationDraft(storage);
  assert.deepStrictEqual(draft, defaultGuidedRegenerationDraft());
  assert.strictEqual(warning, REGENERATION_DRAFT_UNREADABLE_WARNING);
});

test("a draft with impossible field values decodes to safe defaults", () => {
  const nonsense = JSON.stringify({
    schemaVersion: 1,
    step: "somewhere-else",
    mode: "partial",
    language: "fr",
    subjectFilter: 12,
    gradeFilter: {},
    bookId: [],
    selectedTocEntryIds: ["ok", 5, null, "ok"],
    selectedPhases: "reflection",
    excludedPhases: null,
    acknowledged: "yes",
    canarySize: "many",
    refreshExtraction: 1,
    provider: null,
    model: 42,
    publicationVersion: -8,
    publicationVersionMode: "guessed",
    destinationOverrides: [
      { tocEntryId: "ok", outputLanguage: "uz", notionLessonPageId: "page" },
      { tocEntryId: "", outputLanguage: "uz", notionLessonPageId: "page" },
      { tocEntryId: "ok", outputLanguage: "fr", notionLessonPageId: "page" },
      { tocEntryId: "ok", outputLanguage: "uz", notionLessonPageId: "" },
      "not-an-override",
    ],
  });
  const fallback = defaultGuidedRegenerationDraft();
  const { draft, warning } = loadRegenerationDraft(
    memoryStorage({ [REGENERATION_DRAFT_KEY]: nonsense }),
  );

  // A recognisable draft is not discarded wholesale — each field that cannot
  // be believed falls back on its own.
  assert.strictEqual(warning, null);
  assert.strictEqual(draft.step, fallback.step);
  assert.strictEqual(draft.mode, fallback.mode);
  assert.strictEqual(draft.language, fallback.language);
  assert.strictEqual(draft.subjectFilter, null);
  assert.strictEqual(draft.gradeFilter, null);
  assert.strictEqual(draft.bookId, null);
  // Non-strings dropped, and a duplicate id can never double-count a lesson.
  assert.deepStrictEqual(draft.selectedTocEntryIds, ["ok"]);
  assert.deepStrictEqual(draft.selectedPhases, []);
  assert.deepStrictEqual(draft.excludedPhases, []);
  assert.strictEqual(draft.acknowledged, false);
  assert.strictEqual(draft.refreshExtraction, false);
  assert.strictEqual(draft.provider, fallback.provider);
  assert.strictEqual(draft.model, null);
  assert.strictEqual(draft.publicationVersion, 3);
  assert.strictEqual(draft.publicationVersionMode, "automatic");
  assert.deepStrictEqual(draft.destinationOverrides, [
    { tocEntryId: "ok", outputLanguage: "uz", notionLessonPageId: "page" },
  ]);
});

test("a decoded canary can never exceed the lessons the draft still carries", () => {
  const oversized = JSON.stringify({
    ...savedDraft,
    selectedTocEntryIds: ["a", "b"],
    canarySize: 9,
  });
  const { draft } = loadRegenerationDraft(memoryStorage({ [REGENERATION_DRAFT_KEY]: oversized }));
  // `canary_size` is POSTed from state and the server's refusal is a bare
  // `le=target_count` validation payload, so the stored number is clamped.
  assert.strictEqual(draft.canarySize, 2);

  const empty = JSON.stringify({ ...savedDraft, selectedTocEntryIds: [], canarySize: 0 });
  assert.strictEqual(
    loadRegenerationDraft(memoryStorage({ [REGENERATION_DRAFT_KEY]: empty })).draft.canarySize,
    1,
  );
});

/* ════════════════════════════════════════════════════════════════════
 * 4. Every way writing a draft can fail
 * ════════════════════════════════════════════════════════════════════ */

test("a storage write exception returns a warning instead of throwing", () => {
  const storage = refusingStorage({
    setItem: () => {
      throw new Error("QuotaExceededError: the quota has been exceeded");
    },
  });
  const result = saveRegenerationDraft(storage, savedDraft);
  assert.strictEqual(result.warning, REGENERATION_DRAFT_SAVE_WARNING);
});

test("a storage clear exception returns a warning instead of throwing", () => {
  const storage = refusingStorage({
    removeItem: () => {
      throw new Error("SecurityError: access to storage is not allowed");
    },
  });
  assert.strictEqual(clearRegenerationDraft(storage).warning, REGENERATION_DRAFT_CLEAR_WARNING);
});

/* ════════════════════════════════════════════════════════════════════
 * 5. Pruning a restored draft back onto what still exists
 * ════════════════════════════════════════════════════════════════════ */

test("restoring a draft resets acknowledgement and prunes stale lessons", () => {
  const restored = pruneRegenerationDraft(savedDraft, {
    eligibleTocEntryIds: new Set(["kept"]),
    validModelRefs: new Set(["gemini/gemini-3.6-flash"]),
    validPhaseNames: new Set(["reflection"]),
  });
  assert.deepEqual(restored.draft.selectedTocEntryIds, ["kept"]);
  assert.equal(restored.draft.acknowledged, false);
  assert.equal(restored.removedLessonCount, 1);
});

test("pruning drops phases that left the subject's flow", () => {
  const restored = pruneRegenerationDraft(savedDraft, {
    eligibleTocEntryIds: new Set(["kept", "gone"]),
    validModelRefs: new Set(["gemini/gemini-3.6-flash"]),
    validPhaseNames: new Set(["reflection"]),
  });
  // Requested AND excluded: a phase name the server would refuse as unknown
  // has to leave both lists, not just the one the operator ticked.
  assert.deepStrictEqual(restored.draft.selectedPhases, ["reflection"]);
  assert.deepStrictEqual(restored.draft.excludedPhases, ["reflection"]);
  assert.strictEqual(restored.removedLessonCount, 0);
});

test("pruning drops destination overrides for lessons that are gone", () => {
  const restored = pruneRegenerationDraft(savedDraft, {
    eligibleTocEntryIds: new Set(["kept"]),
    validModelRefs: new Set(["gemini/gemini-3.6-flash"]),
    validPhaseNames: new Set(["reflection"]),
  });
  // An override names a publication destination for one lesson; the lesson it
  // names no longer has a lineage to publish.
  assert.deepStrictEqual(restored.draft.destinationOverrides, [
    { tocEntryId: "kept", outputLanguage: "uz", notionLessonPageId: "page-kept" },
  ]);
});

test("pruning clamps the canary down to the lessons that survived", () => {
  const restored = pruneRegenerationDraft(
    { ...savedDraft, selectedTocEntryIds: ["kept", "gone", "also-gone"], canarySize: 3 },
    {
      eligibleTocEntryIds: new Set(["kept"]),
      validModelRefs: new Set(["gemini/gemini-3.6-flash"]),
      validPhaseNames: new Set(["reflection"]),
    },
  );
  assert.strictEqual(restored.removedLessonCount, 2);
  assert.strictEqual(restored.draft.canarySize, 1);
});

test("pruning clears a model the manifest no longer offers", () => {
  const inputs = {
    eligibleTocEntryIds: new Set(["kept", "gone"]),
    validPhaseNames: new Set(["reflection"]),
  };
  const retired = pruneRegenerationDraft(savedDraft, {
    ...inputs,
    validModelRefs: new Set(["gemini/gemini-3.5-flash"]),
  });
  // Retired models 404 on the real API and are refused on every reactivation
  // path; a draft may not carry one silently back into a launch.
  assert.strictEqual(retired.draft.model, null);
  assert.strictEqual(retired.draft.provider, "gemini");

  const offered = pruneRegenerationDraft(savedDraft, {
    ...inputs,
    validModelRefs: new Set(["gemini/gemini-3.6-flash"]),
  });
  assert.strictEqual(offered.draft.model, "gemini-3.6-flash");
});

test("a selective draft pruned down to no phases falls back to full mode", () => {
  const restored = pruneRegenerationDraft(savedDraft, {
    eligibleTocEntryIds: new Set(["kept"]),
    validModelRefs: new Set(["gemini/gemini-3.6-flash"]),
    validPhaseNames: new Set(["memory-check"]),
  });
  // Selective-with-nothing-selected is a campaign the server refuses outright;
  // the honest reading of "every phase I picked is gone" is a full rebuild.
  assert.deepStrictEqual(restored.draft.selectedPhases, []);
  assert.strictEqual(restored.draft.mode, "full");
});

test("pruning leaves a draft that is still entirely valid alone", () => {
  const inputs = {
    eligibleTocEntryIds: new Set(["kept", "gone"]),
    validModelRefs: new Set(["gemini/gemini-3.6-flash"]),
    validPhaseNames: new Set(["reflection", "practice-abacus"]),
  };
  const restored = pruneRegenerationDraft(savedDraft, inputs);
  assert.strictEqual(restored.removedLessonCount, 0);
  // Acknowledgement is the one exception: it is consent to one exact exclusion
  // set, and a restored draft has not been shown that set yet.
  assert.deepStrictEqual(restored.draft, { ...savedDraft, acknowledged: false });
});

/* ════════════════════════════════════════════════════════════════════
 * 6. Which phases a draft actually regenerates
 * ════════════════════════════════════════════════════════════════════ */

test("a full-mode draft regenerates every phase it is handed", () => {
  const selectable = regenerationSelectablePhases(SERVER_CANONICAL_PHASES);
  const full = { ...savedDraft, mode: "full" as const, selectedPhases: ["reflection"] };
  assert.deepStrictEqual(effectiveSelectedPhases(full, selectable), selectable);
});

test("a selective draft regenerates exactly its own ticked phases", () => {
  const selectable = regenerationSelectablePhases(SERVER_CANONICAL_PHASES);
  const selective = { ...savedDraft, mode: "selective" as const, selectedPhases: ["flashcards"] };
  assert.deepStrictEqual(effectiveSelectedPhases(selective, selectable), ["flashcards"]);
});

test("a full-mode draft never asks the server to regenerate the extract", () => {
  // `canonical_phases` is `("extract", *flow_for(subject))`, and
  // `build_phase_plan(selected_phases=["extract"])` raises `UnknownPhaseError`.
  // Stripping it stays `regenerationSelectablePhases`' job — this pins the
  // composed seam, which is where a 422 would actually reach an operator.
  const composed = effectiveSelectedPhases(
    { ...savedDraft, mode: "full" },
    regenerationSelectablePhases(SERVER_CANONICAL_PHASES),
  );
  assert.ok(!composed.includes("extract"), `extract leaked: ${composed.join(", ")}`);
  assert.strictEqual(composed.length, SERVER_CANONICAL_PHASES.length - 1);
});

test("the effective phase list is never an alias of the draft's own array", () => {
  const selective = { ...savedDraft, mode: "selective" as const, selectedPhases: ["flashcards"] };
  effectiveSelectedPhases(selective, []).push("boss-arena");
  assert.deepStrictEqual(selective.selectedPhases, ["flashcards"]);
});

/* ════════════════════════════════════════════════════════════════════
 * 7. The publication version an operator sees
 * ════════════════════════════════════════════════════════════════════ */

test("an automatic draft shows the highest next version its lessons expect", () => {
  const automatic = { ...savedDraft, publicationVersionMode: "automatic" as const };
  assert.strictEqual(
    displayedPublicationVersion(automatic, [
      { next_expected_version: 3 },
      { next_expected_version: 5 },
      { next_expected_version: 4 },
    ]),
    5,
  );
});

test("an automatic draft never shows a version below V3", () => {
  const automatic = { ...savedDraft, publicationVersionMode: "automatic" as const };
  // Regeneration publishes V3 upward; nothing selected still reads as V3.
  assert.strictEqual(displayedPublicationVersion(automatic, []), 3);
  assert.strictEqual(displayedPublicationVersion(automatic, [{ next_expected_version: 2 }]), 3);
});

test("a manual draft shows the exact version the operator typed", () => {
  const manual = {
    ...savedDraft,
    publicationVersion: 7,
    publicationVersionMode: "manual" as const,
  };
  // Editing pins the number: the sources moving underneath it must not.
  assert.strictEqual(displayedPublicationVersion(manual, [{ next_expected_version: 4 }]), 7);
  assert.strictEqual(loadRoundTrip(manual).publicationVersion, 7);
  assert.strictEqual(loadRoundTrip(manual).publicationVersionMode, "manual");
});

/** Save then load, for the assertions that care about what SURVIVES. */
function loadRoundTrip(draft: GuidedRegenerationDraft): GuidedRegenerationDraft {
  const storage = memoryStorage();
  saveRegenerationDraft(storage, draft);
  return loadRegenerationDraft(storage).draft;
}
