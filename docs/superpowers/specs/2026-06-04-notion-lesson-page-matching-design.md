# Notion Lesson-Page Matching — Word-Match Adoption with A2 Fallback

**Status:** Design approved — ready for writing-plans.
**Date:** 2026-06-04
**Branch:** Nggaev-v2

## Goal

Make the Notion archive land each lesson's `Homework` in the right place
**without any human in the loop** (the system is moving to fully autonomous
generation). Where a human-built lesson page already exists for that lesson, the
app should write `Homework` **inside** it (co-located with Adrian's
Text/Video/Images/Quizlet). Where none exists — the common case at scale — the app
creates and owns its own lesson page under a dedicated container. Matching must be
**conservative**: it may only touch a human page on an unambiguous match, and must
never write `Homework` into the wrong lesson.

## Background / the real problem

`notion_archive._push_to_notion` currently does
`find_or_create(subject_page_id, lesson_title)` (`notion_archive.py:133`), and
`find_or_create` (`page_creator.py:19-27`) matches a child page only by
**exact normalized title** (`_normalize` = strip trailing `(N)` + lowercase,
`page_creator.py:14-16`). Verified against the live workspace + DB, the app's
TOC-extracted lesson titles and the human-built Notion lesson pages diverge by
subject:

| Subject | App emits (`section_number` + title) | Human Notion page title | Exact-match today |
|---|---|---|---|
| Kimyo g8 | `1-§ Dastlabki kimyoviy tushuncha va qonunlar` | `1-§ Dastlabki kimyoviy tushuncha va qonunlar` | ✅ adopts (by luck) |
| History g7 | `1 German qabilalari va Rim imperiyasi` | `1-mavzu. German qabilalari va Rim imperiyasi…………………6` | ❌ creates parallel |
| Algebra g7 | `1 Sonli ifodalar` | `1. Yig'indining kvadrati va ayirmaning kvadrati ....57` | ❌ creates parallel |

So today's behavior is **accidental and inconsistent**: it co-locates only when a
human happened to type the app's exact format (kimyo), and creates a parallel page
otherwise (history, algebra). That is neither uniform nor autonomy-safe.

**Key insight (drove the design):** the words usually coincide even when the marks
differ. History's words are *identical* once `1-mavzu.` and the `…6` page-leader are
stripped. Algebra's words genuinely differ — because the human's algebra page is a
*different lesson* (a mid-book topic), not the app's lesson 1 — so declining to
match algebra and falling back is the **correct** outcome, not a failure.

## Decisions locked (during brainstorm)

1. **No human operator.** The system is going fully autonomous; any design needing a
   person to confirm a match is out. Matching is automatic or it falls back.
2. **Word-match adoption, A2 fallback.** Match the app lesson to a human page by
   **content words**; a unique hit adopts that page; anything else falls back to an
   app-owned container.
3. **Strictness = subset + unique.** The app lesson's content-word set must be a
   **subset** of a human page's content-word set, and **exactly one** human page may
   qualify. (Chosen over exact-equality — too brittle against the human page's extra
   marker words — and over a Jaccard threshold — too prone to a confident-but-wrong
   adoption with no human to catch it.)
4. **Fallback container name = `Generated Lessons`.**
5. **Homework subtree is preserved verbatim.** This change only swaps *where the
   `Homework` page hangs*. `_HOMEWORK_LAYOUT` (Case-Based Preview · Flashcards
   [flashcards + Memory Check inline] · Gamified Practices [the games incl. Memory
   Match] · Boss Arena · Reflection) and all leaf/container/attachments-at-top logic
   are untouched.
6. **No migration, no schema change.** Already-archived lessons stay where they are.

## Architecture

### The hybrid tree

```
Subject page (e.g. "Tarix (Jahon Tarixi)")
├─ 1-mavzu. German…6          ← Adrian's page; app word-MATCHES → Homework written INSIDE
│   └─ Homework ▸ <_HOMEWORK_LAYOUT>
├─ <other Adrian lessons>      ← untouched when no app lesson uniquely matches
└─ Generated Lessons          ← app-owned container (one per subject page), UNMATCHED only
    └─ 1 Sonli ifodalar        ← app lesson page (algebra, no human match)
        └─ Homework ▸ <_HOMEWORK_LAYOUT>
```

Matched lessons co-locate with the human template; unmatched lessons live in the
app's own namespace where they cannot collide with human pages.

### The matching algorithm

Runs against the subject page's **direct child pages**, excluding the
`Generated Lessons` container itself.

1. **Tokenize** a title → set of lowercase **content words**. Drop: pure-number
   tokens, `§`, punctuation, page-number leaders (runs of dots + trailing digits),
   and known **marker words** (`mavzu`, `bob`, `bo'lim` / `bolim`, `paragraf`, `§`).
2. **Short-title guard:** if the app lesson yields **< 2** content words, skip
   matching entirely → A2 fallback. (Prevents a bare `Kirish` / `Takrorlash`
   latching onto the wrong page.)
3. **Candidates:** every human page whose content-word set is a **superset** of the
   app lesson's content-word set (`app_words ⊆ human_words`).
4. **Adopt** iff there is **exactly one** candidate → that page becomes the lesson
   page; write `Homework` inside it.
5. **Else (0 or ≥ 2 candidates)** → **A2**: `find_or_create(subject, "Generated
   Lessons")` → `find_or_create(container, app_lesson_title)` → `Homework`.

### Why it is autonomy-safe

The only path that writes into a human page is a **unique subset** hit. Any
ambiguity (≥2 supersets), any morphological near-miss (`kvadrat` vs `kvadrati` —
no stemming in v1), and any short title all resolve to "make the app's own page."
The rule errs toward a parallel app page, never toward a wrong adoption — the failure
mode autonomy cannot tolerate.

### Idempotency (verified against the flow)

- **Matched re-run:** the word-match re-finds the same unique human page; its
  `Homework` sub-page already exists (`find_or_create`); populated leaves skip via
  the existing `page_has_content` guard.
- **Fallback re-run:** still no human match (the app's own pages live *inside* the
  container, so they never appear in the subject's direct-child scan) → container →
  existing app lesson page reused.

## Components

- **New `app/services/notion/lesson_match.py`** — pure, no I/O:
  - `tokenize(title: str) -> frozenset[str]` — content-word extraction (drops
    numbers, `§`, punctuation, page-leaders, marker words).
  - `match_lesson(app_title: str, human_pages: list[dict]) -> str | None` — returns
    the unique matching page id, or `None` (fall back). `human_pages` are
    `{"id", "title"}` dicts as returned by `client.get_child_pages`.
  - Constants: `_MARKER_WORDS`, `_MIN_CONTENT_WORDS = 2`, `CONTAINER_TITLE =
    "Generated Lessons"`.
- **`app/services/notion_archive.py::_push_to_notion`** — replace the single
  `find_or_create(subject_page_id, lesson_title)` (`:133`) with:
  1. `human_pages = client.get_child_pages(subject_page_id)` (already available via
     the wrapper).
  2. `hit = match_lesson(lesson_title, [p for p in human_pages if normalized title
     != CONTAINER_TITLE])`.
  3. If `hit`: `lesson_id = hit`. Else: `container_id, _ =
     find_or_create(subject_page_id, CONTAINER_TITLE)`; `lesson_id, _ =
     find_or_create(container_id, lesson_title)`.
  4. Everything below (`find_or_create(lesson_id, "Homework")` and the
     `_HOMEWORK_LAYOUT` walk) is unchanged.
  - The container is excluded from the human-page candidate list so it can never
    word-match itself.

## Data flow

1. `archive_job` resolves the subject page id (history split logic unchanged) and
   builds `phase_md`; calls `_push_to_notion`.
2. `_push_to_notion` fetches the subject's direct children, runs `match_lesson`.
3. Unique hit → `Homework` written inside the human page. No hit / ambiguous →
   `Generated Lessons` container → app lesson page → `Homework`.
4. `_HOMEWORK_LAYOUT` populates the homework leaves/containers exactly as today
   (attachments at top, populated-skip, Gamified Practices container, etc.).

## Testing strategy

- **`tests/services/test_lesson_match.py`** (new, pure unit — DB-free):
  - **History adopt:** `match_lesson("1 German qabilalari va Rim imperiyasi",
    [{"id":"h","title":"1-mavzu. German qabilalari va Rim imperiyasi…………………6"}])`
    → `"h"`.
  - **Kimyo adopt:** identical-words case → the human page id.
  - **Algebra fallback:** `match_lesson("1 Sonli ifodalar", [{"title":"1.
    Yig'indining kvadrati va ayirmaning kvadrati ....57"}])` → `None`.
  - **Ambiguity fallback:** two human pages both supersetting the app words →
    `None`.
  - **Short-title guard:** app title with <2 content words (`"1 Kirish"`) → `None`
    even if a `Kirish` page exists.
  - **Subset, not equality:** app words ⊊ human words (human has an extra
    descriptive word) → still matches (unique).
  - **Tokenizer units:** marker words / `§` / page-leaders / numbers are dropped;
    apostrophe/diacritic folding consistent with `_fold` usage elsewhere.
- **`tests/services/test_notion_archive.py`** (extend existing):
  - **Matched path:** stub `client.get_child_pages` to return a human page that
    matches → `_push_to_notion` uses it as the lesson parent (no `Generated Lessons`
    created); the `find_or_create` title sequence starts at `"Homework"` under the
    human page.
  - **Fallback path:** stub children that do NOT match → the sequence is
    `["Generated Lessons", <app lesson title>, "Homework", …]`.
  - The existing grouped-structure / attachments-at-top / populated-skip tests carry
    over unchanged (the `_HOMEWORK_LAYOUT` walk is untouched).

## Out of scope

- Stemming / fuzzy morphology (Uzbek agglutination) — v1 relies on safe A2 fallback
  for near-misses.
- Migrating already-archived lessons (e.g. the kimyo-g8 job that landed in Adrian's
  page) — they stay put; only new archives use this logic.
- Any change to `_HOMEWORK_LAYOUT`, the phase set, attachments handling, or the
  history subject-page split — all unchanged.
- A persisted `toc_entry → notion_page_id` mapping table — not needed; the match is
  recomputed deterministically each archive and is idempotent.

## Risks / notes

- **False-positive adoption** is the worst outcome (homework in the wrong lesson,
  silently, under autonomy). The subset+unique+min-words rule is deliberately
  conservative to make this near-impossible; the accepted cost is occasional
  parallel app pages for genuine-but-unrecognized matches (morphological variants).
- **Marker-word list** (`mavzu`, `bob`, `bo'lim`, `paragraf`, `§`) is the starting
  set; it is content-only and easy to extend if a new book introduces another
  structural prefix. Unknown markers degrade gracefully to A2 fallback, never to a
  wrong match.
- **Human page organization assumption:** lessons are direct children of the subject
  page (verified true in the live workspace). If a future book nests lessons deeper,
  the scan would miss them and fall back to A2 — safe, but flagged.
