# Subject-category drill-down for Library and Fleet

**Date:** 2026-06-17
**Status:** Approved (design) — pending spec review
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
2. **Category = each subject.** One category per subject slug
   (`subjectLabel`), e.g. Uzbek / Algebra / History — not broader families.
3. **Fleet applies to the Tray only.** The Prepare (+) form keeps its
   grade→subject flow untouched. Only the Preparing/Ready/Failed card area is
   wrapped.
4. **Categories derive from present cards**, with count badges — not the full
   26-subject catalog. No empty tiles.
5. **Selection is local component state**, resets on navigation. No URL param
   (deep-linking is a possible later enhancement, out of scope here).

## Architecture

### New shared component — `web/src/components/category-browser.tsx`

One generic, presentational drill-down used by both pages. It owns the
selection state, the grouping, the tile grid, and the enter/exit animation;
each consumer supplies how to read a subject off an item and how to render that
subject's cards.

```ts
interface CategoryBrowserProps<T> {
  items: T[];
  getSubject: (item: T) => string;
  renderItems: (items: T[], subject: string) => React.ReactNode;
  /** Optional override for the per-tile count caption (default: "N items"). */
  countLabel?: (items: T[], subject: string) => string;
}
```

Behaviour:
- `groupBySubject(items, getSubject)` → ordered `[subject, T[]][]`. Order:
  by descending count, then alphabetical by label, for a stable, sensible grid.
- `selected: string | null` local state.
  - `null` → render the **tile grid**.
  - set → render the **detail view**: back control + subject header +
    `renderItems(group, selected)`.
- **Auto-fallback:** if `selected` is non-null but no longer present in the
  freshly grouped data (e.g. its last card was removed), reset to `null`. Done
  in a `useEffect` keyed on the grouped subjects.
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
- Replace **only** the flat `books.map(...)` grid (current lines ~143–156)
  with:
  ```tsx
  <CategoryBrowser
    items={books}
    getSubject={(b) => b.subject}
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
  ```
- `BookCard`, `StatusBadge`, helpers: unchanged.

### Fleet Tray — `web/src/components/fleet/launcher.tsx`

- Prepare (Part A) card + the `+` form: **untouched**.
- Tray (Part B): keep the heading and the `trayEmpty` early-out. When not
  empty, wrap the body in `CategoryBrowser` over the union of tray-relevant
  books (`preparing ∪ ready ∪ failed`).
  - `getSubject={(b) => b.subject}`.
  - `countLabel` summarises status, e.g. `2 ready · 1 preparing` (fallback to
    `N items` if only one bucket).
  - `renderItems(group)` renders that subject's **Preparing / Ready / Failed**
    sub-sections exactly as today (the existing `LBL` headers + `CardGrid` +
    `PreparingCard`/`ReadyCard`/`FailedCard`), filtered to `group`.
- `ReadyCard` and all launch logic: unchanged. Its per-book react-query and
  expand state keep working since it's rendered the same way, just nested under
  a subject.

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
