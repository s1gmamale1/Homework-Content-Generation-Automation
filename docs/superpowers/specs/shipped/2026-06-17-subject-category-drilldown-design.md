# Subject-category drill-down for Library and Fleet

**Date:** 2026-06-17
**Status:** Shipped (Habibullo redesign, rebased onto Nggaev-v2 @ `4d9ffc9`). This
doc was reconciled post-implementation to match what actually shipped — the
component generalised from "subject-only" to an arbitrary **group key**, and the
drill-down became **two levels (grade → subject)** via nested browsers.
**Scope:** Frontend only (`web/`). No backend, API, or DB changes.

## Problem

Both the Library page and the Fleet Tray dump every card into one flat grid.
As the number of books grows this becomes an undifferentiated wall. The user
wants a category layer in front of the cards: pick a subject (Uzbek, Algebra,
History, …), then see only that subject's cards.

## Decisions (locked in brainstorm)

1. **Interaction model — drill-down.** Top level shows a grid of category
   tiles. Clicking a tile reveals that subject's cards with a "back to
   categories" control. (Not filter-pills, not inline accordion.)
2. **Two-level drill-down: grade → subject.** As shipped, the top level groups
   by **grade** (`gradeKey`/`gradeLabel`), and clicking a grade reveals a second
   `CategoryBrowser` grouping that grade's cards by **subject** (`b.subject` /
   `subjectLabel`). The component itself is generic over an arbitrary group key
   (not subject-specific), which is what makes the nesting possible.
3. **Fleet applies to the Tray only.** The Prepare (+) form keeps its
   grade→subject flow untouched. Only the Preparing/Ready/Failed card area is
   wrapped.
4. **Categories derive from present cards**, with count badges — not the full
   26-subject catalog. No empty tiles.
5. **Selection is local component state**, resets on navigation. No URL param
   (deep-linking is a possible later enhancement, out of scope here).

## Architecture

### New shared component — `web/src/components/category-browser.tsx`

One generic, presentational drill-down used by both pages and **nestable** (the
grade→subject UX is just one `CategoryBrowser` whose `renderItems` returns
another). It owns the selection state, the grouping, the tile grid, and the
enter/exit animation; each consumer supplies how to read a **group key** off an
item, how to label it, and how to render that group's cards. As shipped:

```ts
interface CategoryBrowserProps<T> {
  items: T[];
  getGroupKey: (item: T) => string;          // grouping key (e.g. grade, or subject)
  groupLabel: (key: string) => string;       // human label for a key
  renderItems: (items: T[], key: string) => React.ReactNode;
  groupAccent?: (key: string) => [string, string];  // avatar gradient; default slate
  groupBadge?: (key: string) => string;             // avatar text; default label[0]
  countLabel?: (items: T[], key: string) => string; // tile/header caption; default "N item(s)"
  sortGroups?: (a: [string, T[]], b: [string, T[]]) => number;  // default: count desc
  backLabel?: string;                                // detail-view back button; default "Back"
}
```

Behaviour:
- Group `items` by `getGroupKey` → ordered `[key, T[]][]`, sorted by `sortGroups`
  (default descending count) for a stable, sensible grid.
- `selected: string | null` local state.
  - `null` → render the **tile grid** (`groupLabel`/`groupAccent`/`groupBadge` per tile).
  - set → render the **detail view**: `← {backLabel}` control + group header +
    `renderItems(group, selected)`.
- **Auto-fallback:** if `selected` is non-null but no longer present in the
  freshly grouped data (e.g. its last card was removed), reset to `null`. Done
  in a `useEffect` keyed on the grouped keys.
- Transition between grid and detail via `AnimatePresence` (`mode="wait"`),
  a short fade + small vertical slide. Reuse existing motion easing
  (`[0.22, 1, 0.36, 1]`).

The component is purely presentational — no data fetching, no react-query. It
receives already-loaded items.

### Tile visual (top level)

Reuses the existing design language so it feels native:
- Subject-accent gradient avatar (`accentOf(subject)`) with the subject's first
  letter — same treatment as `SubjectAvatar` / Library `BookCard`.
- `subjectLabel(subject)` as the title.
- A count caption (consumer-supplied via `countLabel`, default `N items`).
- A trailing chevron; hover-lift + border-brighten matching current cards.
- Responsive grid: `grid-cols-2 sm:grid-cols-3 lg:grid-cols-4`, staggered
  `fadeUpItem` entrance.

### Detail view (after click)

- Back control: a ghost button `← All subjects` that sets `selected = null`.
- Header: the same accent avatar + label + count for context.
- Body: `renderItems(group, subject)`.

## Consumer integration

### Library — `web/src/routes/library.tsx`

- Keep hero, summary strip, Upload button, loading skeleton, and the
  `books.length === 0` empty state exactly as they are.
- Replace the flat `books.map(...)` grid with **two nested `CategoryBrowser`s** —
  outer by grade, inner by subject (as shipped):
  ```tsx
  <CategoryBrowser
    items={books}
    getGroupKey={(b) => gradeKey(b.grade)}
    groupLabel={gradeLabel}
    groupAccent={gradeAccent}
    groupBadge={gradeBadge}
    sortGroups={compareGradeGroups}
    backLabel="All grades"
    countLabel={(items) => {
      const n = new Set(items.map((b) => b.subject)).size;
      return `${n} subject${n === 1 ? "" : "s"}`;
    }}
    renderItems={(gradeBooks) => (
      // Within a grade, drill down once more by subject.
      <CategoryBrowser
        items={gradeBooks}
        getGroupKey={(b) => b.subject}
        groupLabel={subjectLabel}
        groupAccent={accentOf}
        backLabel="All subjects"
        countLabel={(items) => `${items.length} book${items.length === 1 ? "" : "s"}`}
        renderItems={(group) => (
          <motion.div className={GRID} variants={staggerContainer} initial="hidden" animate="show">
            {group.map((book) => (
              <motion.div key={book.id} variants={fadeUpItem} className="h-full">
                <BookCard book={book} />
              </motion.div>
            ))}
          </motion.div>
        )}
      />
    )}
  />
  ```
- `BookCard`, `StatusBadge`, helpers: unchanged. Grade helpers (`gradeKey`,
  `gradeLabel`, `gradeAccent`, `gradeBadge`, `compareGradeGroups`) live in
  `@/lib/subjects` alongside `subjectLabel`/`accentOf`.

### Fleet Tray — `web/src/components/fleet/launcher.tsx`

- Prepare (Part A) card + the `+` form: **untouched**.
- Tray (Part B): keep the heading and the `trayEmpty` early-out. When not empty,
  wrap the body in the **same nested `CategoryBrowser` pair** as Library — outer
  `getGroupKey={(b) => gradeKey(b.grade)}` (`backLabel="All grades"`), inner
  `getGroupKey={(b) => b.subject}` (`backLabel="All subjects"`), over the union
  of tray-relevant books.
  - inner `renderItems(group)` renders that subject's **Preparing / Ready /
    Failed** sub-sections exactly as today (the existing `LBL` headers +
    `CardGrid` + `PreparingCard`/`ReadyCard`/`FailedCard`), filtered to `group`.
- `ReadyCard` and all launch logic (incl. the per-role provider/model controls
  added later on `Nggaev-v2`): unchanged. Its per-book react-query and expand
  state keep working since it's rendered the same way, just nested under
  grade → subject.

## Edge cases

| Case | Behaviour |
|------|-----------|
| Library empty / tray empty | Existing empty message; no category grid. |
| Selected subject's last card removed | Auto-fall back to tile grid. |
| Single subject present | One tile shown (no auto-skip; predictable). |
| Unknown subject slug | `subjectLabel` falls back to the raw slug, `accentOf` to neutral slate (existing behaviour). |

## Out of scope

- URL deep-linking (`?subject=`).
- Family/grouping hierarchy.
- Any change to the Prepare form, launch flow, backend, or data model.
- Showing empty categories from the full subject catalog.

## Verification

- `npx tsc -p tsconfig.app.json --noEmit` clean.
- Run the app (dev server already used this session): Library shows subject
  tiles → click → that subject's books → back. Fleet Tray shows subject tiles →
  click → Preparing/Ready/Failed for that subject → back. Empty states intact.
- This is frontend-presentational; no automated test harness exists for these
  routes, so the proof is typecheck + manual run (consistent with the repo's
  acceptance bar for UI-only changes).
