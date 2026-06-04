# Notion Lesson-Page Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Notion archive write each lesson's `Homework` into the matching human-built lesson page when one uniquely matches by content words, and otherwise into an app-owned `Generated Lessons` container — fully automatic, autonomy-safe.

**Architecture:** A new pure module `app/services/notion/lesson_match.py` tokenizes lesson titles to content-word sets and returns the unique human page whose words are a superset of the app lesson's words (or `None`). `notion_archive._push_to_notion` calls it: a hit adopts that page; a miss falls back to `find_or_create(subject, "Generated Lessons") → find_or_create(container, lesson_title)`. Everything from `Homework` down (`_HOMEWORK_LAYOUT`) is untouched.

**Tech Stack:** Python 3.13, pytest (DB-free unit tests — `tests/conftest.py` wires no database). Run tests with the project venv: `.\.venv\Scripts\python.exe -m pytest ...` (the `uv` command is NOT on PATH in this environment).

---

## Conventions for this plan

- **Run tests** with `.\.venv\Scripts\python.exe -m pytest ...`.
- **Commit messages** must be plain ASCII (this environment mangles non-ASCII in `git commit -m`). Code/test/prompt *files* may contain non-ASCII (they are UTF-8).
- **Stage only the files each task lists** — other sessions may commit to this branch; never `git add -A`.
- The known pre-existing red test `tests/services/test_config_notion.py::test_notion_defaults_disabled` (local `.env` leak) is out of scope; the full suite is "green" with that one exception.

## File structure

- **Create `app/services/notion/lesson_match.py`** — one responsibility: title → content-word set, and "unique superset match or None". Pure, no I/O. Depends only on `page_creator._normalize` (same subpackage; `page_creator` imports only `client`, so no import cycle with `notion_archive`).
- **Modify `app/services/notion_archive.py::_push_to_notion`** — replace the single `find_or_create(client, subject_page_id, lesson_title)` (`:133`) with the match-or-container branch. Nothing else in the function changes.
- **Create `tests/services/test_lesson_match.py`** — pure unit tests for the matcher.
- **Modify `tests/services/test_notion_archive.py`** — add matched-path + fallback-path tests; update the three existing `_push_to_notion` tests for the new container layer.

---

## Task 1: `lesson_match` module — tokenizer + unique-superset matcher

**Files:**
- Create: `app/services/notion/lesson_match.py`
- Test: `tests/services/test_lesson_match.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_lesson_match.py`:

```python
from app.services.notion.lesson_match import tokenize, match_lesson, CONTAINER_TITLE


def test_tokenize_strips_markers_leaders_numbers_ellipsis():
    # verbatim history title: U+2026 ellipsis glyphs + trailing page number "6",
    # the "1-mavzu." prefix, and punctuation must all drop.
    t = tokenize("1-mavzu. German qabilalari va Rim imperiyasi…………………6")
    assert t == frozenset({"german", "qabilalari", "va", "rim", "imperiyasi"})


def test_tokenize_strips_ascii_dot_leader():
    # algebra-style ASCII dot leader + page number.
    t = tokenize("1. Sonli ifodalar ....57")
    assert t == frozenset({"sonli", "ifodalar"})


def test_tokenize_folds_apostrophes_not_diacritics():
    # Uzbek apostrophe variants inside words fold away (bo'lim -> bolim); the
    # marker "bo'lim" is then dropped. Diacritics are NOT folded (none here).
    assert tokenize("2-bo'lim Algebraik ifodalar") == frozenset({"algebraik", "ifodalar"})


def test_history_adopts_unique_match():
    human = [{"id": "h1", "title": "1-mavzu. German qabilalari va Rim imperiyasi…………………6"}]
    assert match_lesson("1 German qabilalari va Rim imperiyasi", human) == "h1"


def test_kimyo_adopts_identical_words():
    human = [{"id": "k1", "title": "1-§ Dastlabki kimyoviy tushuncha va qonunlar"}]
    assert match_lesson("1-§ Dastlabki kimyoviy tushuncha va qonunlar", human) == "k1"


def test_algebra_falls_back_different_words():
    human = [{"id": "a1", "title": "1. Yig'indining kvadrati va ayirmaning kvadrati ....57"}]
    assert match_lesson("1 Sonli ifodalar", human) is None


def test_ambiguous_two_supersets_falls_back():
    human = [
        {"id": "p1", "title": "Sulfat kislota"},
        {"id": "p2", "title": "Sulfat kislota xossalari"},
    ]
    assert match_lesson("Sulfat kislota", human) is None


def test_short_title_skips_matching():
    human = [{"id": "h1", "title": "1-mavzu. Kirish darsi"}]
    assert match_lesson("1 Kirish", human) is None  # only {"kirish"} -> < 2 content words


def test_subset_not_equality_still_matches():
    human = [{"id": "h1", "title": "Fotosintez jarayoni va bosqichlari"}]
    assert match_lesson("Fotosintez jarayoni", human) == "h1"


def test_container_page_excluded_from_candidates():
    human = [
        {"id": "c", "title": CONTAINER_TITLE},
        {"id": "h1", "title": "German qabilalari va Rim imperiyasi"},
    ]
    assert match_lesson("German qabilalari va Rim imperiyasi", human) == "h1"


def test_no_human_pages_falls_back():
    assert match_lesson("German qabilalari va Rim imperiyasi", []) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_lesson_match.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.notion.lesson_match'`.

- [ ] **Step 3: Write the implementation**

Create `app/services/notion/lesson_match.py`:

```python
"""Match an app-extracted lesson title to a human-built Notion lesson page by
content words. Conservative: only a UNIQUE superset match adopts a human page;
anything ambiguous, short, or unmatched returns None (the caller then creates an
app-owned page). No stopword list and no stemming in v1 — a near-miss safely
falls back rather than risking a wrong adoption."""

from __future__ import annotations

import re

from .page_creator import _normalize

CONTAINER_TITLE = "Generated Lessons"
_MIN_CONTENT_WORDS = 2

# Structural prefix words to drop, compared AFTER _fold (lowercase + apostrophes
# stripped, so "bo'lim" -> "bolim").
_MARKER_WORDS = frozenset({"mavzu", "bob", "bolim", "paragraf"})

# Runs of letters of any script (Latin incl. Uzbek, Cyrillic). Digits, ASCII dot
# leaders, the U+2026 ellipsis glyph, the section sign, and punctuation are all
# non-letters, so this regex drops every one of them.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Apostrophe variants Uzbek uses inside words (o', g'). Stripping them folds
# "bo'lim" -> "bolim". This intentionally does NOT fold diacritics.
_APOSTROPHES = "'‘’ʻ`"


def _fold(s: str) -> str:
    return s.lower().translate({ord(c): None for c in _APOSTROPHES})


def tokenize(title: str) -> frozenset[str]:
    """Lowercase content-word set: drops numbers, the section sign, punctuation,
    page-number leaders (ASCII dot runs AND U+2026 ellipsis glyphs), and the
    structural marker words."""
    words = _WORD_RE.findall(_fold(title))
    return frozenset(w for w in words if w not in _MARKER_WORDS)


def match_lesson(app_title: str, human_pages: list[dict]) -> str | None:
    """Return the id of the UNIQUE human page whose content words are a superset
    of the app lesson's content words, else None. ``human_pages`` are
    ``{"id", "title"}`` dicts as returned by ``NotionClientWrapper.get_child_pages``.
    The app's own ``Generated Lessons`` container is excluded from candidates."""
    app_words = tokenize(app_title)
    if len(app_words) < _MIN_CONTENT_WORDS:
        return None
    container_norm = _normalize(CONTAINER_TITLE)
    candidates = [
        p["id"]
        for p in human_pages
        if _normalize(p["title"]) != container_norm
        and app_words <= tokenize(p["title"])
    ]
    return candidates[0] if len(candidates) == 1 else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_lesson_match.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/notion/lesson_match.py tests/services/test_lesson_match.py
git commit -m "feat(notion): lesson_match - unique content-word superset matcher"
```

---

## Task 2: Wire matcher into `_push_to_notion` (adopt vs container fallback)

**Files:**
- Modify: `app/services/notion_archive.py` (imports near top; `_push_to_notion:133`)
- Test: `tests/services/test_notion_archive.py`

**Context:** `_push_to_notion` currently does `lesson_id, _ = find_or_create(client, subject_page_id, lesson_title)` (`:133`). It receives `client` (a `NotionClientWrapper`, real or MagicMock) and an injectable `find_or_create`. `client.get_child_pages(parent_id)` returns a list of `{"id","title","type"}` dicts. The three existing tests call `_push_to_notion` with a `MagicMock` client and a `MagicMock` `find_or_create`; they currently do not set `client.get_child_pages`, so this task must set it on each (an unset MagicMock attribute returns a non-iterable Mock and would break `match_lesson`).

- [ ] **Step 1: Write the failing tests (new matched + fallback paths)**

Append to `tests/services/test_notion_archive.py`:

```python
def test_push_adopts_matching_human_page():
    """A unique content-word match writes Homework INSIDE the human lesson page —
    no 'Generated Lessons' container, no lesson find_or_create."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_child_pages.return_value = [
        {"id": "human_lesson", "title": "1-mavzu. German qabilalari va Rim imperiyasi…………………6"}
    ]
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", True))
    na._push_to_notion(
        client=client, subject_page_id="subj",
        lesson_title="1 German qabilalari va Rim imperiyasi",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    titles = [c.args[2] for c in na_find.call_args_list]
    assert "Generated Lessons" not in titles
    assert titles[0] == "Homework"
    # Homework was created under the ADOPTED human page, not a new app page
    assert na_find.call_args_list[0].args[1] == "human_lesson"


def test_push_falls_back_to_container_when_no_match():
    """No content-word match → Subject ▸ Generated Lessons ▸ <lesson> ▸ Homework."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_child_pages.return_value = [
        {"id": "a1", "title": "1. Yig'indining kvadrati va ayirmaning kvadrati ....57"}
    ]
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", True))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="1 Sonli ifodalar",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    titles = [c.args[2] for c in na_find.call_args_list]
    assert titles[:3] == ["Generated Lessons", "1 Sonli ifodalar", "Homework"]
    assert na_find.call_args_list[0].args[1] == "subj"                 # container under subject
    assert na_find.call_args_list[1].args[1] == "id::Generated Lessons"  # lesson under container
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_notion_archive.py -k "adopts or falls_back" -v`
Expected: FAIL — today's code ignores `get_child_pages` and always `find_or_create`s the lesson under the subject, so `titles[0]` is the lesson title (matched test) and `"Generated Lessons"` never appears (fallback test).

- [ ] **Step 3: Implement the match-or-container branch**

In `app/services/notion_archive.py`, add the import near the other `notion` imports (after the `from app.services.notion.page_creator import find_or_create` line):

```python
from app.services.notion.lesson_match import match_lesson, CONTAINER_TITLE
```

Replace line 133 (`lesson_id, _ = find_or_create(client, subject_page_id, lesson_title)`) with:

```python
    human_pages = client.get_child_pages(subject_page_id)
    hit = match_lesson(lesson_title, human_pages)
    if hit is not None:
        lesson_id = hit
    else:
        container_id, _ = find_or_create(client, subject_page_id, CONTAINER_TITLE)
        lesson_id, _ = find_or_create(client, container_id, lesson_title)
```

Leave `homework_id, _ = find_or_create(client, lesson_id, "Homework")` (`:134`) and everything below unchanged.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_notion_archive.py -k "adopts or falls_back" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Update the three existing `_push_to_notion` tests for the container layer**

The existing tests now hit the fallback path (no `get_child_pages` stub → must set one) and gain a `Generated Lessons` step before the lesson title. In `tests/services/test_notion_archive.py`:

In `test_push_builds_grouped_structure`, after `client.page_has_content.return_value = False` add:

```python
    client.get_child_pages.return_value = []   # no human pages → A2 container fallback
```

and change the expected `titles` assertion to prepend the container:

```python
    assert titles == [
        "Generated Lessons",
        "1-§ x", "Homework",
        "Case-Based Preview",
        "Flashcards",
        "Gamified Practices",
        "Real-Life Challenge", "Error Detection", "TicTacToe",
        "Boss Arena",
        "Reflection",
    ]
```

(The `upload_bytes.call_count == 8` and `append_block_children.call_count == 7` assertions are unchanged — the container adds neither an upload nor a content append.)

In `test_flashcards_page_attachments_at_top_then_content`, after `client.page_has_content.return_value = False` add:

```python
    client.get_child_pages.return_value = []
```

In `test_push_skips_pages_already_populated`, after `client.page_has_content.return_value = True` add:

```python
    client.get_child_pages.return_value = []
```

(`append_block_children.assert_not_called()` and `upload_bytes.assert_not_called()` still hold — every leaf is skipped as already-populated; the container/lesson `find_or_create`s create no content.)

- [ ] **Step 6: Run the full archive test file**

Run: `.\.venv\Scripts\python.exe -m pytest tests/services/test_notion_archive.py -v`
Expected: PASS (all tests — the 3 updated + 2 new + any others).

- [ ] **Step 7: Run the whole backend suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS except the single known pre-existing red `tests/services/test_config_notion.py::test_notion_defaults_disabled`. No other failures.

- [ ] **Step 8: Commit**

```bash
git add app/services/notion_archive.py tests/services/test_notion_archive.py
git commit -m "feat(notion): adopt matching human lesson page, else Generated Lessons container"
```

---

## Self-Review

**1. Spec coverage** — every spec requirement maps to a task:
- Tokenizer (drop numbers/§/punct/marker words/page-leaders incl. **U+2026 ellipsis** and ASCII dots) → Task 1 `tokenize` + `test_tokenize_*` (verbatim history title test included, reviewer note 1).
- Subset + unique + ≥2-content-word guard → Task 1 `match_lesson` + tests (`subset_not_equality`, `ambiguous`, `short_title`).
- Container excluded from candidates, compared via `_normalize` on **both** sides → Task 1 `match_lesson` + `test_container_page_excluded` (reviewer note 4).
- Adopt-in-human-page vs `Generated Lessons` fallback → Task 2 branch + `test_push_adopts_matching_human_page` / `test_push_falls_back_to_container_when_no_match`.
- `_HOMEWORK_LAYOUT` preserved verbatim → Task 2 changes only `:133`; existing grouped-structure test still asserts the full downstream sequence.
- Idempotency (app pages nest inside the container, never scanned as subject children) → structurally guaranteed by the branch; the fallback test shows the container as the lesson's parent.
- No diacritic folding promised (reviewer note 2) → `_fold` docstring + `test_tokenize_folds_apostrophes_not_diacritics`.
- No stopword list, `va`-as-content-word risk (reviewer note 3) → module docstring note; contained by subset+unique (history matches *because* `va` is on both sides).
- Pagination safe (reviewer note 5) → no code needed; `client.get_child_pages` already loops the cursor via `get_block_children`. Documented here, not re-implemented.

**2. Placeholder scan** — no TBD/TODO; every code step shows complete code; every test shows real assertions.

**3. Type consistency** — `tokenize(str) -> frozenset[str]`, `match_lesson(str, list[dict]) -> str | None`, and `CONTAINER_TITLE` are defined once in Task 1 and used identically in Task 2's import and tests. `human_pages` dicts use `{"id","title"}` consistently in the matcher, its tests, and the `_push_to_notion` wiring (which passes `client.get_child_pages(...)` straight through).
