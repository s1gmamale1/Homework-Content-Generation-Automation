# Notion multi-part subject clobber — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preparing a specific Notion textbook part (e.g. UZ "Matematika 1-qism") must fetch *that* part's PDF, never a sibling part's — closing WISHLIST `notion-multipart-subject-clobber-1`.

**Architecture:** Backend `available_languages` stops collapsing same-subject parts (returns a `parts` list per language, keeping backward-compat `page_id`/`has_textbook`). Both FE prepare paths route page-id resolution + chip availability through one pure, unit-tested helper: the UZ-sourced clicked page is authoritative for UZ output; cross-language switches translate via the map only when exactly one part exists.

**Tech Stack:** FastAPI + pytest (backend), React/TS + `npx tsx` assert-scripts (FE pure helpers), `tsc -b` / `vite build` (FE compile gate).

---

## Approach & key decisions

- **Root cause (verified against code):** the subject picker in both `web/src/components/fleet/launcher.tsx` and `web/src/routes/upload.tsx` is populated from `notion_fetch.list_subjects` → the UZ `N - sinf` container (`_SINF_RE`), so the clicked `page_id` is **always a specific UZ part**. But `notion_fetch.available_languages` keys its per-language map by `app_subject` (`result.setdefault(app_subject, {})[lang] = …`, `notion_fetch.py:191`) — **last part wins**. Both prepare paths then do `map[lang]?.page_id ?? clickedPage` (`launcher.tsx:124`, `upload.tsx:126`), overriding the correctly-clicked UZ part with the map's last-part page_id **even when `lang === "uz"`**. Result: prepare part-1 → downloads part-2 → dedup returns the existing part-2 book → no extraction. Wrong-textbook class.
- **Chosen fix (both halves):** (1) Backend — make `available_languages` collision-safe by adding a `parts: [{page_id,title,has_textbook}]` list per language while **keeping** top-level `page_id` (= first part) + `has_textbook`, so every existing consumer and test keeps working and the FE can now *see* multi-part instead of a silent collapse. (2) FE — the clicked UZ page is authoritative for UZ output (never overridden by the map); cross-language switches use the map only, and only when the target language has exactly one part.
- **Second consumer folded in (not in the WISHLIST):** `upload.tsx:126` has the identical bug. It's the same fix / same class; shipping launcher-only would knowingly leave the direct-upload path broken. Both consumers share the new helper (DRY).
- **Multi-part cross-language decision (locked with user 2026-07-06): Disable + hint.** When the operator switches OUTPUT language to one with >1 textbook part, v1 cannot know which part corresponds → the chip is disabled with a tooltip ("N Russian parts — pick the specific part directly or upload the PDF"). Zero wrong-book risk; a part-picker is a future WISHLIST item. Same-language UZ picks and single-part cross-language switches are always resolved correctly.
- **Testability:** FE has no component-test harness — pure `lib/` helpers are unit-tested via `npx tsx`. The buggy resolution + availability logic is extracted into `web/src/lib/notion-parts.ts` and unit-tested there; components become thin callers. `tsc -b` + `vite build` are the compile gate for the wiring.
- **Rejected — change `page_id` semantics / drop it:** breaks all existing consumers and `test_notion_lang_crawl.py` assertions (`result["math-algebra"]["uz"]["page_id"] == "uz-alg"`). Additive `parts` is lower-risk and equally correct (top-level `page_id` is only *read* in the unambiguous single-part cross-language case).
- **No migration. Notion READS only (no writes). No paid LLM calls.** Acceptance = mocked two-part fixture (backend + helper) proving part-1 click → part-1 page_id, plus `tsc`/`build` clean. Live prepare of the real G6 part-1 is the post-merge verify (user-gated, GK2 drives).
- **Collision map (flag at gate):** Lane A also edits `launcher.tsx` but in the ReadyCard launch-config region (~L685+) — disjoint from this lane's prepare region (~L96–138) + step-3 chips (~L287–332). Lane B touches dedup-hit FE feedback; this lane edits `prepare.mutationFn` (page-id resolution), **not** `prepare.onSuccess` (the toast), so even a same-mutation edit is likely mergeable. Second-to-merge rebases; both are append/region-disjoint. No shared migration.

---

## Task 1: Backend — `available_languages` returns collision-safe `parts`

**Files:**
- Modify: `app/services/notion_fetch.py:170-195` (`available_languages`)
- Test: `tests/services/test_notion_lang_crawl.py` (add a multi-part class)

- [ ] **Step 1: Write the failing test**

Add to `tests/services/test_notion_lang_crawl.py` (end of file):

```python
# ---------------------------------------------------------------------------
# available_languages — multi-part subject must NOT clobber (notion-multipart)
# Two UZ pages both map to math (Matematika 1-qism / 2-qism); the per-language
# entry must expose BOTH via `parts`, and top-level page_id = the FIRST part.
# ---------------------------------------------------------------------------

class TestAvailableLanguagesMultiPart:
    def _make_client(self):
        children = {
            GRADE_ID: [{"id": UZ_CONTAINER_ID, "title": "9 - sinf"}],
            UZ_CONTAINER_ID: [
                {"id": "uz-math-1", "title": "Matematika 1-qism"},
                {"id": "uz-math-2", "title": "Matematika 2-qism"},
            ],
        }
        blocks = {
            "uz-math-1": [_pdf_block("http://cdn/math-1.pdf")],
            "uz-math-2": [_pdf_block("http://cdn/math-2.pdf")],
        }
        return _client(children, blocks)

    def test_both_parts_present_in_parts_list(self):
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        # both titles map to the same app_subject → one entry, two parts
        (app_subject,) = list(result.keys())
        parts = result[app_subject]["uz"]["parts"]
        page_ids = {p["page_id"] for p in parts}
        assert page_ids == {"uz-math-1", "uz-math-2"}, (
            f"multi-part subject collapsed — expected both parts, got {page_ids}"
        )

    def test_part_titles_preserved(self):
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        (app_subject,) = list(result.keys())
        titles = {p["title"] for p in result[app_subject]["uz"]["parts"]}
        assert titles == {"Matematika 1-qism", "Matematika 2-qism"}

    def test_top_level_page_id_is_first_part(self):
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        (app_subject,) = list(result.keys())
        entry = result[app_subject]["uz"]
        assert entry["page_id"] == "uz-math-1", "top-level page_id must be the first part (backward-compat)"
        assert entry["has_textbook"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/macmini5/Documents/HCGA-multipart && uv run python -m pytest tests/services/test_notion_lang_crawl.py::TestAvailableLanguagesMultiPart -q`
Expected: FAIL with `KeyError: 'parts'` (current shape has no `parts` key).

- [ ] **Step 3: Implement `parts` accumulation**

Replace the body of `available_languages` (`app/services/notion_fetch.py:183-195`) with:

```python
    result: dict[str, dict[str, dict]] = {}
    for lang in ("uz", "ru", "en"):
        for entry in _subjects_under(client, grade_page_id, _LANG_CONTAINER_RE[lang], lang):
            app_subject = entry["app_subject"]
            if app_subject is None:
                continue
            if not entry["has_textbook"]:
                continue
            lang_map = result.setdefault(app_subject, {})
            # Multi-part subjects (e.g. "Matematika 1-qism"/"2-qism") share an
            # app_subject. Accumulate every part in `parts` instead of letting the
            # last page clobber the first — the FE resolves the correct part from
            # this list (notion-multipart-subject-clobber-1). Top-level page_id /
            # has_textbook are kept (page_id = the FIRST part) for backward-compat.
            slot = lang_map.setdefault(
                lang, {"page_id": entry["page_id"], "has_textbook": True, "parts": []}
            )
            slot["parts"].append({
                "page_id": entry["page_id"],
                "title": entry["notion_title"],
                "has_textbook": entry["has_textbook"],
            })
    return result
```

Also update the docstring (`notion_fetch.py:170-182`) to mention `parts`: change the shape line to
`` ``{app_subject: {lang: {"page_id": <first part>, "has_textbook": …, "parts": [{page_id,title,has_textbook}, …]}}}`` `` and add a sentence: "Same-subject parts (e.g. multi-volume textbooks) are preserved in `parts`, not collapsed."

- [ ] **Step 4: Run the new test + the full lang-crawl file**

Run: `cd /Users/macmini5/Documents/HCGA-multipart && uv run python -m pytest tests/services/test_notion_lang_crawl.py -q`
Expected: PASS (new class green; all pre-existing tests — `test_lang_entry_contains_page_id_and_has_textbook` etc. — still green, since single-part `page_id`/`has_textbook` are unchanged).

- [ ] **Step 5: Commit**

```bash
cd /Users/macmini5/Documents/HCGA-multipart
git add app/services/notion_fetch.py tests/services/test_notion_lang_crawl.py
git commit -m "nmp: available_languages preserves multi-part subjects via parts list"
```

---

## Task 2: Backend endpoint shape test (regression guard for the API contract)

**Files:**
- Test: `tests/api/test_books_language_payload.py` (add one endpoint-level test)

- [ ] **Step 1: Write the failing/guarding test**

Read the existing `test_available_languages_returns_per_subject_dict` in `tests/api/test_books_language_payload.py:89-107` first (it patches `notion_fetch.available_languages` with a fixed return). Add a sibling test asserting the endpoint passes `parts` through unchanged:

```python
    def test_available_languages_passes_parts_through(self):
        fake = {
            "math-algebra": {
                "uz": {
                    "page_id": "uz-math-1",
                    "has_textbook": True,
                    "parts": [
                        {"page_id": "uz-math-1", "title": "Matematika 1-qism", "has_textbook": True},
                        {"page_id": "uz-math-2", "title": "Matematika 2-qism", "has_textbook": True},
                    ],
                }
            }
        }
        with patch("app.api.v1.notion.settings.notion_api_key", "k"), \
             patch("app.api.v1.notion.NotionClientWrapper"), \
             patch("app.api.v1.notion.notion_fetch.available_languages",
                   return_value=fake):
            r = c.get("/api/v1/notion/grades/g9/available-languages")
        assert r.status_code == 200
        parts = r.json()["math-algebra"]["uz"]["parts"]
        assert [p["page_id"] for p in parts] == ["uz-math-1", "uz-math-2"]
```

(Match the exact patch targets / client fixture name `c` used by the existing tests in that file — copy their `with patch(...)` header verbatim; the snippet above mirrors `test_available_languages_returns_per_subject_dict`.)

- [ ] **Step 2: Run it**

Run: `cd /Users/macmini5/Documents/HCGA-multipart && uv run python -m pytest tests/api/test_books_language_payload.py -q`
Expected: PASS immediately (the endpoint is a pass-through of the service return; this test locks that the `parts` field is not stripped by FastAPI serialization). If it FAILS, the endpoint's `-> dict` return annotation is fine — investigate before proceeding.

- [ ] **Step 3: Commit**

```bash
cd /Users/macmini5/Documents/HCGA-multipart
git add tests/api/test_books_language_payload.py
git commit -m "nmp: guard that /available-languages passes parts through"
```

---

## Task 3: FE pure helper `notion-parts.ts` + types (TDD via npx tsx)

**Files:**
- Modify: `web/src/lib/types.ts` (add `LangPart`, `LangAvailability`, `AvailableLanguages`)
- Create: `web/src/lib/notion-parts.ts`
- Create: `web/src/lib/notion-parts.test.ts`
- Modify: `web/src/lib/api.ts:351-360` (`fetchAvailableLanguages` return type)

- [ ] **Step 1: Add shared types to `web/src/lib/types.ts`**

Append near the other Notion types (after `NotionSubject`, around `types.ts:121`):

```ts
/** One textbook part under a subject/language (multi-volume subjects have >1). */
export interface LangPart {
  page_id: string;
  title: string;
  has_textbook: boolean;
}

/** Per-language availability for a subject. `page_id`/`has_textbook` are the
 *  first part (backward-compat); `parts` lists every part (may be absent on
 *  legacy responses). */
export interface LangAvailability {
  page_id: string;
  has_textbook: boolean;
  parts?: LangPart[];
}

/** `available-languages` response: app_subject → lang → availability. */
export type AvailableLanguages = Record<string, Record<string, LangAvailability>>;
```

- [ ] **Step 2: Write the failing helper test**

Create `web/src/lib/notion-parts.test.ts`:

```ts
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
// This is the core bug: clicking part-1 must fetch part-1, not the map's last part.
assert.equal(resolveNotionPageId("uz-math-2", "uz", uzMulti), "uz-math-2",
  "UZ output must return the CLICKED page, even for a multi-part subject");
assert.equal(resolveNotionPageId("uz-math-1", "uz", uzMulti), "uz-math-1");
// null map (not loaded) → still trust the click for UZ
assert.equal(resolveNotionPageId("uz-x", "uz", null), "uz-x");

// --- resolveNotionPageId: cross-language single part → that part ---
assert.equal(resolveNotionPageId("uz-a", "ru", ruSingle), "ru-a");

// --- resolveNotionPageId: cross-language multi/zero part → null (ambiguous) ---
assert.equal(resolveNotionPageId("uz-1", "ru", ruMulti), null,
  "multi-part cross-language is ambiguous → null (caller must not fetch)");
assert.equal(resolveNotionPageId("uz-a", "en", ruSingle), null,
  "no en parts → null");

// --- partsFor: legacy shape (no `parts`) synthesizes a single part ---
assert.deepEqual(
  partsFor({ page_id: "p", has_textbook: true }).map((x) => x.page_id),
  ["p"], "legacy entry (no parts) → one synthesized part");
assert.deepEqual(partsFor({ page_id: "p", has_textbook: false }), [],
  "legacy entry with no textbook → no parts");
assert.deepEqual(partsFor(undefined), []);

// --- langChipState: UZ always available when its own textbook exists ---
assert.deepEqual(langChipState("uz", uzMulti, true), { available: true, multiPart: false, partCount: 2 });
// cross-language single part → available
assert.deepEqual(langChipState("ru", ruSingle, true), { available: true, multiPart: false, partCount: 1 });
// cross-language multi part → DISABLED + multiPart flag (the locked decision)
assert.deepEqual(langChipState("ru", ruMulti, true), { available: false, multiPart: true, partCount: 2 });
// absent language → unavailable
assert.deepEqual(langChipState("en", ruSingle, true), { available: false, multiPart: false, partCount: 0 });
// map not loaded → fail-open available
assert.deepEqual(langChipState("ru", null, false), { available: true, multiPart: false, partCount: 0 });

console.log("notion-parts.test.ts: all assertions passed");
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd /Users/macmini5/Documents/HCGA-multipart/web && npx tsx src/lib/notion-parts.test.ts`
Expected: FAIL — `Cannot find module './notion-parts'` (helper not written yet).

- [ ] **Step 4: Implement the helper**

Create `web/src/lib/notion-parts.ts`:

```ts
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
 *  - other language: translate via the map. Exactly one part → that part.
 *    Zero or multiple parts → null (caller must not fetch; 0 = no page,
 *    >1 = ambiguous, surfaced to the operator as a disabled chip). */
export function resolveNotionPageId(
  clickedPageId: string,
  language: OutputLanguage,
  langMap: Record<string, LangAvailability> | null | undefined,
): string | null {
  if (language === "uz") return clickedPageId;
  const parts = partsFor(langMap?.[language]);
  return parts.length === 1 ? parts[0].page_id : null;
}

/** Language-chip state for the prepare flow.
 *  - map not loaded → fail-open (available), so chips aren't wrongly disabled.
 *  - UZ → available iff its own textbook exists (single explicit part per pick).
 *  - other language → available iff exactly one part; >1 parts is ambiguous in
 *    v1 → disabled with `multiPart` set (locked decision: disable + hint). */
export function langChipState(
  language: OutputLanguage,
  langMap: Record<string, LangAvailability> | null | undefined,
  mapLoaded: boolean,
): { available: boolean; multiPart: boolean; partCount: number } {
  if (!mapLoaded) return { available: true, multiPart: false, partCount: 0 };
  if (language === "uz") {
    const uz = langMap?.uz;
    return { available: !!uz?.has_textbook, multiPart: false, partCount: partsFor(uz).length };
  }
  const parts = partsFor(langMap?.[language]);
  if (parts.length === 0) return { available: false, multiPart: false, partCount: 0 };
  if (parts.length > 1) return { available: false, multiPart: true, partCount: parts.length };
  return { available: true, multiPart: false, partCount: 1 };
}
```

- [ ] **Step 5: Run the helper test (green) + point api.ts at the shared type**

Run: `cd /Users/macmini5/Documents/HCGA-multipart/web && npx tsx src/lib/notion-parts.test.ts`
Expected: `notion-parts.test.ts: all assertions passed`.

Then update `web/src/lib/api.ts:351-360` — import `AvailableLanguages` from `./types` and change the method signature:

```ts
  /** Fetch available UZ/RU/EN language containers for each subject in a grade.
   *  Returns { [app_subject]: { [lang]: { page_id, has_textbook, parts? } } }. */
  async fetchAvailableLanguages(gradePageId: string): Promise<AvailableLanguages> {
    const res = await authFetch(
      `/api/v1/notion/grades/${encodeURIComponent(gradePageId)}/available-languages`,
    );
    return unwrap<AvailableLanguages>(res);
  },
```

(Add `AvailableLanguages` to the existing `import type { … } from "@/lib/types"` / `"./types"` block in api.ts — check the file's existing import style and match it.)

- [ ] **Step 6: Typecheck**

Run: `cd /Users/macmini5/Documents/HCGA-multipart/web && npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
cd /Users/macmini5/Documents/HCGA-multipart
git add web/src/lib/types.ts web/src/lib/notion-parts.ts web/src/lib/notion-parts.test.ts web/src/lib/api.ts
git commit -m "nmp: pure notion-parts helper (page-id resolution + chip state) + shared types"
```

---

## Task 4: Wire `launcher.tsx` prepare path through the helper

**Files:**
- Modify: `web/src/components/fleet/launcher.tsx` — imports, `subjectLangMap` type (~L96), prepare `mutationFn` (~L119-138), step-3 chip loop (~L287-332)

- [ ] **Step 1: Import the helper + types**

Add to the imports (with the other `@/lib/*` imports): `import { resolveNotionPageId, langChipState } from "@/lib/notion-parts";` and add `AvailableLanguages` to the `@/lib/types` import block (used to type `subjectLangMap` if needed — the inference from `availLangsQ.data` already carries it once api.ts is typed, so this may be unnecessary; only add if tsc asks).

- [ ] **Step 2: Fix the prepare `mutationFn` (the clobber)**

Replace the body of `mutationFn` (`launcher.tsx:120-126`):

```tsx
    mutationFn: (v: { subjectPageId: string; grade: string; language: OutputLanguage }) => {
      // The subject picker is UZ-sourced, so `subjectPageId` is the UZ part the
      // operator clicked. resolveNotionPageId keeps that click authoritative for
      // UZ output and translates to another language only when a single part
      // exists (notion-multipart-subject-clobber-1). null = ambiguous/absent —
      // the chip is disabled for that case, so this is a defensive guard.
      const pageId = resolveNotionPageId(v.subjectPageId, v.language, subjectLangMap);
      if (pageId == null) {
        return Promise.reject(
          new Error("This language has multiple textbook parts — pick a specific part or upload the PDF directly."),
        );
      }
      return api.fetchBookFromNotion(pageId, v.grade, v.language !== "uz" ? v.language : undefined);
    },
```

- [ ] **Step 3: Fix the step-3 chip availability (disable + hint on multi-part)**

Replace the per-lang chip computation (`launcher.tsx:292-302`) — the `info`/`mapLoaded`/`available`/`tooltip` block — with:

```tsx
                const mapLoaded = availLangsQ.data != null;
                const { available, multiPart, partCount } = langChipState(lang, subjectLangMap, mapLoaded);
                const selected = prepLang === lang;
                const tooltip = multiPart
                  ? `${partCount} ${LANG_LABEL[lang]} textbook parts in Notion — pick the specific part from that language's subject list, or upload the PDF directly.`
                  : !available && lang === "en"
                    ? "No English page yet — create an English page (with the textbook) in Notion, or upload the PDF directly."
                    : !available
                      ? `No ${LANG_LABEL[lang]} textbook available in Notion for this subject.`
                      : undefined;
```

(Remove the now-dead `const info = subjectLangMap?.[lang];` line — `langChipState` reads the map internally. Keep the rest of the chip JSX unchanged; it already reads `available`/`selected`/`tooltip`.)

- [ ] **Step 4: Typecheck + build**

Run: `cd /Users/macmini5/Documents/HCGA-multipart/web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: tsc clean; `vite build` writes `web/dist/` with no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/macmini5/Documents/HCGA-multipart
git add web/src/components/fleet/launcher.tsx
git commit -m "nmp: launcher prepare trusts the clicked UZ part; disable multi-part cross-language"
```

---

## Task 5: Wire `upload.tsx` prepare path through the helper (same fix, second consumer)

**Files:**
- Modify: `web/src/routes/upload.tsx` — imports, `availLangs` state type (~L55), `pickSubject` page-id resolution (~L126), chip loop (~L438-447)

- [ ] **Step 1: Import the helper + replace the local map type**

Add `import { resolveNotionPageId, langChipState } from "@/lib/notion-parts";` and `AvailableLanguages` to the `@/lib/types` import. Change the `availLangs` state type (`upload.tsx:55`) from the inline `Record<string, Record<string, { page_id: string; has_textbook: boolean }>> | null` to `AvailableLanguages | null`.

- [ ] **Step 2: Fix `pickSubject` page-id resolution + guard**

Replace `upload.tsx:126`:

```tsx
      // resolveNotionPageId keeps the clicked UZ page authoritative for UZ output
      // and translates cross-language only when a single part exists
      // (notion-multipart-subject-clobber-1). Multi-part chips are disabled below,
      // so null here is a defensive guard.
      const pageId = resolveNotionPageId(s.page_id, language, langMap);
      if (pageId == null) {
        toast.error("This language has multiple textbook parts — pick a specific part or upload the PDF directly.");
        setBusy(false);
        setPendingSubjectId(null);
        return;
      }
```

Where `langMap` is the subject's map — add `const langMap = s.app_subject ? (availLangs?.[s.app_subject] ?? null) : null;` at the top of `pickSubject` if not already in scope (the chip loop computes its own `langMap` at L413; `pickSubject` needs its own local since it's a separate function — verify scope and add the `const` inside `pickSubject`).

- [ ] **Step 3: Fix the chip availability (disable + hint on multi-part)**

Replace `upload.tsx:439-447` (the `info`/`mapLoaded`/`available`/`tooltip` block) with:

```tsx
                                      const mapLoaded = availLangs != null;
                                      const { available, multiPart, partCount } = langChipState(lang, langMap, mapLoaded);
                                      const tooltip = multiPart
                                        ? `${partCount} ${LANG_LABEL[lang]} textbook parts in Notion — pick the specific part from that language's subject list, or upload the PDF directly.`
                                        : !available && lang === "en"
                                          ? "No English page yet — create an English page (with the textbook) in Notion, or upload the PDF directly."
                                          : !available
                                            ? `No ${LANG_LABEL[lang]} textbook available in Notion for this subject.`
                                            : undefined;
```

(Remove the now-dead `const info = langMap?.[lang];` line. The chip JSX already reads `available`/`tooltip`.)

- [ ] **Step 4: Typecheck + build**

Run: `cd /Users/macmini5/Documents/HCGA-multipart/web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: tsc clean; build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /Users/macmini5/Documents/HCGA-multipart
git add web/src/routes/upload.tsx
git commit -m "nmp: upload prepare trusts the clicked UZ part; disable multi-part cross-language"
```

---

## Task 6: Full suite + FE gate + finish

- [ ] **Step 1: Backend suite**

Run: `cd /Users/macmini5/Documents/HCGA-multipart && uv run python -m pytest tests/ -q`
Expected: green (the 2 pre-existing `tests/services/test_failover_api.py` failover reds are the only known failures — confirm they match the base, everything else passes).

- [ ] **Step 2: FE helper test + typecheck + build (final)**

Run: `cd /Users/macmini5/Documents/HCGA-multipart/web && npx tsx src/lib/notion-parts.test.ts && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: helper assertions pass; tsc clean; build OK.

- [ ] **Step 3: Finish (worklog 0123 + docs + plan move + rebase-check)**

- Worklog entry in `docs/memory/MASTER_MEMORY.md` (0123) + row in `docs/memory/INDEX.md`.
- Remove the `notion-multipart-subject-clobber-1` line from `docs/memory/WISHLIST.md`; note the deferred **cross-language part-picker** as a new WISHLIST line.
- De-stale `docs/CODE_MAP.md` / `docs/HOW_IT_WORKS.md` where they describe `available_languages` / the Notion prepare flow (mention `parts` + clicked-page-authoritative resolution).
- `git mv docs/superpowers/plans/2026-07-06-notion-multipart-clobber.md docs/superpowers/plans/shipped/`.
- Rebase-check: `git fetch origin && git log HEAD..origin/Nggaev-v2` — if the base moved, rebase onto `origin/Nggaev-v2`, resolve any `launcher.tsx`/`content_lint`-style collisions, and re-run Steps 1–2 before opening the PR.
- Open the PR to `Nggaev-v2` for GK2 (note in the body: **workers cache prompts/config at startup — no restart needed for this change since it's Notion-fetch + FE only; but the running server's FE bundle must be rebuilt/redeployed to pick up the launcher/upload fix**). Do NOT self-merge — GK2 gates.
