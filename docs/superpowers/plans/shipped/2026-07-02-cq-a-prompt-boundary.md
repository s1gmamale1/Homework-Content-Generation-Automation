# CQ-A — Prompt-layer content-quality fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the three CQ-A prompt-layer fixes from the 2026-07-01 content audit (ROADMAP R21) — curriculum-boundary leakage, reflection fabricating attempt outcomes, and the L2 bridge ignoring the chosen medium.

**Architecture:** All three are surgical edits to the prompt-assembly path + two prompt/markdown files. The lesson-boundary note rides inside `lesson_context` (mirroring the existing `_inject_grade` pattern at `pipeline.py:34`), so it reaches every content phase through `_build_master_prompt`'s `--- LESSON CONTEXT ---` block with zero call-site surgery. A tiny `toc_entries` repo helper supplies the next lesson's title (successor by `order_index`, same book). The reflection fix and the L2-bridge fix are self-contained edits to `prompts/_general/reflection.md` and `app/services/prompts.py`.

**Tech Stack:** FastAPI + SQLAlchemy async (Postgres), pytest / pytest-asyncio, gemini over Vertex SDK (`transport=api`).

## Approach & key decisions

- **R21.1 injection = via `lesson_context`, covering ALL content phases** (recommended; the alternative — thread a dedicated `next_lesson` param through the 5-function chain `_execute_one_phase → _run_content_phases_parallel → _execute_phase → run_phase_prompt → _build_master_prompt` and gate to just case-based-preview + boss-arena — was rejected: the audit found boundary leaks in memory-check card 9 and boss Q4 too, so the defect is systemic to content phases; the note is ~5 lines of shared context injected once, so broad coverage costs ~nothing and needs no call-site surgery). `lesson_context` is `None` during `extract` and is only populated for the content tail, so extract is untouched. **User was asked to confirm this scope; away-from-keyboard — proceeding with the recommendation, re-confirmable at the single approval gate.**
- **Next-lesson lookup** = a new `toc_entries.get_next_in_book(session, book_id, order_index)` returning the *next teaching lesson* — the smallest `order_index` strictly greater than the current one **whose `section_number IS NOT NULL`** (robust to non-contiguous indices; backed by existing index `ix_toc_entries_book_id_order`). **Skipping NULL-`section_number` rows is load-bearing** (gate R2): production has 214 NULL-section end-matter rows (Упражнения/Ответы/Тестовые); without the filter the note would announce "the next lesson is «Ответы»". Fetched inside the already-open session at `pipeline.run()`'s context-load block (line ~105) so there is no detached-ORM access. Last teaching lesson / no successor → `None` → note is a no-op (mirrors `_inject_grade`'s missing-value guard).
- **R21.5 reflection** = remove the two outcome-fabricating instructions: §2's "concepts the student's answers handled well" (no attempt exists at generation time) becomes neutral self-check structure, and §4's mandate to *name* a "Needs Retry" / "homework not passed" outcome is dropped — the prompt states the app owns pass/redo and emits only the redo *route*. Kuchli/Zaif and Redo structure stay (existing `test_reflection_prompt.py` requires them).
- **`l2-bridge-follows-medium`** = `_LANG_ENGLISH`/`_LANG_RUSSIAN` stay as the **unchanged frozen `uz`-bridge strings** (guaranteeing byte-identity trivially); a new `_l2_rule(target_lang, bridge_medium)` returns that frozen string verbatim for `bridge_medium == "uz"` (early return) and, for `ru`/`en`, derives the variant by **targeted `.replace()`** of the bridge phrases on the frozen base. A rebuild-from-f-string approach was **rejected**: the originals carry an authoring asymmetry (English's governing line reads "in Uzbek", Russian's reads "in formal Uzbek") that an f-string template would silently "fix", breaking byte-identity — the substitution-on-frozen-base approach cannot drift. **Byte-identity is guarded by a frozen-literal test (gate R1), not by comparing the function to itself.** So `LANGUAGE_RULES["english"|"russian"]` and every uz-medium render are unchanged; only ru/en-medium L2 renders change (Russian/English bridge instead of Uzbek).
- **Verified against real code:** `lesson_context` threads pipeline → `run_phase_prompt(lesson_context=)` → `_build_master_prompt` (`agent.py:729`) for every content phase; `_resolve_language_rule` (`prompts.py:103`) already receives `output_language` but ignores it for L2; the audit evidence + job IDs are in `docs/research/2026-07-01-content-quality-audit-g8-math.md`.

**Tech-debt / conflict flagged (pre-flight):** `tests/services/test_prompts_output_language.py::test_l2_subject_ignores_medium_keeps_uzbek_bridge` (line 33) asserts the *old* behavior ("L2 bridge unchanged regardless of medium"). This plan **deliberately reverses that behavior**, so Task 4 rewrites that test to assert bridge-follows-medium + the uz byte-identical invariant. This is an intended plan-vs-test conflict, resolved by the plan.

## Global Constraints

- **Transport for the acceptance smoke: `transport=api` (Vertex SDK) only.** The `cli` path is retired from operational use (CLAUDE.md standing decision 2026-07-01). No cli smoke.
- **Branch:** `cq-a-prompt-boundary` off `origin/Nggaev-v2` (tip `23310a0`); worktree `../HCGA-cqa`. **Commit prefix `cqa:`.** Worklog ID **0109** (re-verify next-free against `docs/memory/INDEX.md` at finish — latest is 0108).
- **Stage only the files each task lists** — never `git add -A` (other sessions commit to this branch, incl. `web/`).
- **Byte-identical-UZ invariant** for the L2-bridge change (Task 4) is mandatory.
- **`extract` phase must stay untouched** — `lesson_context` is `None` there; the boundary note must never reach extract.
- One commit per task; each task ends TDD-green with `uv run python -m pytest <its files> -q`.

---

### Task 1: `toc_entries.get_next_in_book` repo helper

**Files:**
- Modify: `app/repositories/toc_entries.py` (add function after `list_for_book`, ~line 52)
- Test: `tests/integration/test_toc_next_in_book.py` (new; DB-gated, mirrors the repo's other integration tests)

**Interfaces:**
- Produces: `async def get_next_in_book(session: AsyncSession, book_id: UUID, order_index: int) -> TOCEntry | None` — the next **teaching** entry in `book_id` (smallest `order_index` strictly greater than `order_index` **with `section_number IS NOT NULL`**), else `None`. Consumed by Task 2.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_toc_next_in_book.py
import os
import uuid
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="requires RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_get_next_in_book_returns_successor_and_none_at_end():
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import toc_entries as toc_repo
    from app.schemas import TOCEntryExtracted

    async with SessionLocal() as session:
        book = await books_repo.upload_book(
            session,
            filename="t.pdf",
            subject="matematika",
            grade="8",
            content=b"%PDF-1.4 test",
        )
        entries = await toc_repo.bulk_create(
            session,
            book.id,
            [
                TOCEntryExtracted(section_number="17", section_title="Pifagor teoremasi",
                                  page_start=41, page_end=43),
                TOCEntryExtracted(section_number="18", section_title="Pifagor teoremasiga teskari",
                                  page_start=44, page_end=46),
            ],
        )
        await session.commit()

        first, last = entries[0], entries[1]
        nxt = await toc_repo.get_next_in_book(session, book.id, first.order_index)
        assert nxt is not None and nxt.section_title == "Pifagor teoremasiga teskari"
        assert await toc_repo.get_next_in_book(session, book.id, last.order_index) is None


@pytest.mark.asyncio
async def test_get_next_in_book_skips_null_section_end_matter():
    """Gate R2: a NULL-section end-matter row (Ответы/Тестовые) between two
    teaching lessons must be SKIPPED — the successor is the next NUMBERED lesson.
    If the only rows after `first` are NULL-section, return None."""
    from app.db import SessionLocal
    from app.repositories import books as books_repo
    from app.repositories import toc_entries as toc_repo
    from app.schemas import TOCEntryExtracted

    async with SessionLocal() as session:
        book = await books_repo.upload_book(
            session, filename="t2.pdf", subject="matematika", grade="8",
            content=b"%PDF-1.4 test",
        )
        entries = await toc_repo.bulk_create(
            session, book.id,
            [
                TOCEntryExtracted(section_number="17", section_title="Lesson 17",
                                  page_start=41, page_end=43),
                TOCEntryExtracted(section_number=None, section_title="Тестовые задания",
                                  page_start=44, page_end=45),
                TOCEntryExtracted(section_number="18", section_title="Lesson 18",
                                  page_start=46, page_end=48),
                TOCEntryExtracted(section_number=None, section_title="Ответы",
                                  page_start=49, page_end=50),
            ],
        )
        await session.commit()
        first, mid_null, last_lesson, end_null = entries
        # skips the NULL end-matter row, lands on the next numbered lesson
        nxt = await toc_repo.get_next_in_book(session, book.id, first.order_index)
        assert nxt is not None and nxt.section_title == "Lesson 18"
        # only NULL-section rows remain after the last lesson → None
        assert await toc_repo.get_next_in_book(session, book.id, last_lesson.order_index) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_cqa uv run --extra dev python -m pytest tests/integration/test_toc_next_in_book.py -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'get_next_in_book'`.
(If the scratch DB doesn't exist: `createdb -U macmini5 edu_scratch_cqa` then `DATABASE_URL=... RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head`. See the scratch-DB recipe — pin `127.0.0.1`, not `localhost`.)

- [ ] **Step 3: Write minimal implementation**

```python
# app/repositories/toc_entries.py — add after list_for_book()
async def get_next_in_book(
    session: AsyncSession, book_id: UUID, order_index: int
) -> TOCEntry | None:
    """Return the next TEACHING lesson in reading order — the smallest order_index
    strictly greater than `order_index` within the same book whose section_number
    is not NULL — or None when there is no later numbered lesson. Uses `> order_index`
    (not `+1`) so a non-contiguous index sequence still resolves the true successor,
    and skips NULL-section end-matter rows (Упражнения/Ответы/Тестовые — 214 such
    rows in production) so the boundary note never announces "next lesson = «Ответы»".
    Backed by ix_toc_entries_book_id_order."""
    stmt = (
        select(TOCEntry)
        .where(
            TOCEntry.book_id == book_id,
            TOCEntry.order_index > order_index,
            TOCEntry.section_number.isnot(None),
        )
        .order_by(TOCEntry.order_index)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_cqa uv run --extra dev python -m pytest tests/integration/test_toc_next_in_book.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add app/repositories/toc_entries.py tests/integration/test_toc_next_in_book.py
git commit -m "cqa: add toc_entries.get_next_in_book (next-lesson successor lookup)"
```

---

### Task 2: pipeline injects the curriculum-boundary note into `lesson_context`

**Files:**
- Modify: `app/services/pipeline.py` — add `_inject_lesson_boundary` helper (after `_inject_grade`, ~line 42); fetch the next-lesson title in the context-load block (~line 105) and thread it to a `next_lesson_title` local; call the helper right after the existing `_inject_grade` call (line 291).
- Test: `tests/services/test_pipeline_boundary.py` (new; pure-function unit test — no DB)

**Interfaces:**
- Consumes: `toc_repo.get_next_in_book` (Task 1).
- Produces: `def _inject_lesson_boundary(lesson_context: Optional[str], next_lesson_title: Optional[str]) -> Optional[str]` — prepends a `CURRICULUM BOUNDARY:` block naming `next_lesson_title`; no-op when either arg is falsy.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_pipeline_boundary.py
from app.services.pipeline import _inject_lesson_boundary, _inject_grade


def test_boundary_note_names_next_lesson_and_forbids_its_concepts():
    out = _inject_lesson_boundary("EXTRACT BODY", "Pifagor teoremasiga teskari")
    assert "Pifagor teoremasiga teskari" in out
    assert "EXTRACT BODY" in out
    low = out.lower()
    assert "next lesson" in low
    # must forbid reaching into the next lesson's natural completions
    assert "converse" in low
    assert "criteria" in low
    assert "generaliz" in low  # generalization / generalisation


def test_boundary_note_is_noop_without_a_successor():
    assert _inject_lesson_boundary("EXTRACT BODY", None) == "EXTRACT BODY"
    assert _inject_lesson_boundary("EXTRACT BODY", "") == "EXTRACT BODY"


def test_boundary_note_is_noop_when_context_missing():
    assert _inject_lesson_boundary(None, "Next Lesson") is None


def test_boundary_composes_after_grade_injection():
    ctx = _inject_grade("EXTRACT BODY", "8")
    ctx = _inject_lesson_boundary(ctx, "Next Lesson")
    assert "Student grade level: 8" in ctx
    assert "Next Lesson" in ctx
    assert "EXTRACT BODY" in ctx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_pipeline_boundary.py -q`
Expected: FAIL — `ImportError: cannot import name '_inject_lesson_boundary'`.

- [ ] **Step 3: Write minimal implementation**

Add the helper after `_inject_grade` (`app/services/pipeline.py`, ~line 42):

```python
def _inject_lesson_boundary(
    lesson_context: Optional[str], next_lesson_title: Optional[str]
) -> Optional[str]:
    """Prepend a curriculum-boundary note naming the NEXT lesson so content
    phases stop at this lesson's edge instead of reaching for the concept's
    natural completion (the audit's #1 defect: Pythagorean converse, parallelogram
    criteria, 'asymptote' — all next-lesson material). Rides inside lesson_context
    so every content phase sees it via _build_master_prompt's LESSON CONTEXT block;
    extract is unaffected (its lesson_context is None). No-op when there is no
    successor (last lesson) or no context."""
    if not next_lesson_title or lesson_context is None:
        return lesson_context
    note = (
        "CURRICULUM BOUNDARY:\n"
        f"The NEXT lesson in this textbook is: «{next_lesson_title}».\n"
        "Teach and test ONLY the CURRENT lesson's concepts. Do NOT use, teach, "
        "hint at, or build any question on the next lesson's material — including "
        "the converse or inverse of this lesson's theorem/rule, its recognition "
        "criteria (alomatlari), or any generalization the next lesson introduces. "
        "If a natural 'next step' of this concept belongs to the next lesson, stop "
        "at this lesson's boundary."
    )
    return f"{note}\n\n{lesson_context}"
```

In `run()`'s context-load block (inside `async with SessionLocal() as session:` around `pipeline.py:105`, where `section` is fetched), fetch and capture the successor title while the session is open — e.g. right after `section_data = {...}` (~line 170):

```python
            _next = await toc_repo.get_next_in_book(session, book_id, section.order_index)
            next_lesson_title: Optional[str] = _next.section_title if _next else None
```

Then, immediately after the existing grade injection (`pipeline.py:291`):

```python
        lesson_context = _inject_grade(lesson_context, book_grade)
        lesson_context = _inject_lesson_boundary(lesson_context, next_lesson_title)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_pipeline_boundary.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py tests/services/test_pipeline_boundary.py
git commit -m "cqa: inject next-lesson curriculum-boundary note into lesson_context (R21.1)"
```

---

### Task 3: reflection prompt stops fabricating attempt outcomes (R21.5)

**Files:**
- Modify: `prompts/_general/reflection.md` (§2 "Strong & Weak Points" and §4 "Redo Route")
- Test: `tests/services/test_reflection_prompt.py` (extend with negative assertions)

**Interfaces:** none (markdown prompt + assertions).

- [ ] **Step 1: Write the failing test** — append to `tests/services/test_reflection_prompt.py`:

```python
def test_reflection_does_not_pre_assert_attempt_outcomes():
    text = _REFLECTION.read_text(encoding="utf-8")
    low = text.lower()
    # The app owns pass/fail; the prompt must NOT name a not-passed outcome, and
    # must NOT ask the model to report how the student's answers performed
    # (there is no attempt at generation time — that produced the audit's
    # fabricated "Needs Retry / ikkilanishlar kuzatildi" narratives).
    assert "needs retry" not in low, "reflection still names a not-passed outcome"
    assert "homework not passed" not in low, "reflection still pre-asserts a fail outcome"
    assert "handled well" not in low, "reflection still asks to report the student's performance"
    # Structure must remain (app fills it after the real attempt):
    assert "kuchli" in low and "zaif" in low and "redo" in low
    assert "app" in low  # states the app owns pass/redo
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_reflection_prompt.py -q`
Expected: FAIL on `"needs retry" not in low` (and `"handled well"`), which are present today.

- [ ] **Step 3: Edit the prompt.** In `prompts/_general/reflection.md`:

Replace §2 body (lines 30-33) with neutral, no-attempt-assumed structure:

```markdown
## 2. Strong & Weak Points

Structure the app fills in **after** the student's attempt — do NOT assert how the
student performed (there is no attempt yet when this is generated):
- **Kuchli tomonlar:** name 1–2 concepts from THIS lesson that the Case-Based Preview /
  Boss Arena treated as core — the ones a confident student should have handled.
- **Zaif tomonlar:** name 1–2 concepts from THIS lesson that are the most error-prone /
  worth re-checking (name them; do not invent a score or a result).
```

Replace §4's "Retake rule / Pass-retake terminology" bullets (lines 50-54) with:

```markdown
- Retake rule: **"Xuddi shu tushunchalar, lekin xuddi shu savollar emas"** (same
  concepts, not the same questions).
- The student **app**, not this output, sets the score and decides pass/redo. Do NOT
  state or imply an outcome ("Needs Retry", "not passed", "completed") — describe the
  redo route conditionally ("if the app marks a redo, return to …") and stop there.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_reflection_prompt.py -q`
Expected: PASS (both the legacy conformance test and the new one).

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/reflection.md tests/services/test_reflection_prompt.py
git commit -m "cqa: reflection emits neutral structure, no pre-asserted attempt outcome (R21.5)"
```

---

### Task 4: L2 bridge language follows the chosen medium (`l2-bridge-follows-medium`)

**Files:**
- Modify: `app/services/prompts.py` — convert `_LANG_ENGLISH`/`_LANG_RUSSIAN` into `_l2_rule(target_lang, bridge_medium)` output; teach `_resolve_language_rule` to pass `output_language` as the bridge.
- Test: rewrite `tests/services/test_prompts_output_language.py::test_l2_subject_ignores_medium_keeps_uzbek_bridge` → `test_l2_bridge_follows_medium`; add a byte-identical-uz guard.

**Interfaces:**
- Produces: `def _l2_rule(target_lang: str, bridge_medium: str) -> str` — the L2 rule for `target_lang` ("english"/"russian") with scaffolding in `bridge_medium` ("uz"/"en"/"ru", default "uz"). `_l2_rule(t, "uz")` is byte-identical to today's static block.

- [ ] **Step 1: Write the failing test** — replace lines 33-38 of `tests/services/test_prompts_output_language.py`.

  **First**, copy the CURRENT (pre-change) `_LANG_ENGLISH` and `_LANG_RUSSIAN` literal blocks **verbatim** out of `app/services/prompts.py` (they are still the originals at task start) into the test file as `_FROZEN_LANG_ENGLISH` / `_FROZEN_LANG_RUSSIAN`, keeping the exact implicit-string-concatenation form. These frozen copies live in the test and are INDEPENDENT of the module, so any drift in the module's wording makes the test bite (gate R1 — the old `== LANGUAGE_RULES["english"]` was a tautology because the module reassigns that name from the same function).

```python
# Frozen copies of the pre-change L2 blocks (uz bridge). Copied verbatim from
# app/services/prompts.py @ origin/Nggaev-v2. Do NOT edit to match a code change —
# if the builder drifts, THIS is the ground truth and the test must fail.
_FROZEN_LANG_ENGLISH = (
    "This is an English (L2) lesson for native-Uzbek learners.\n"
    "Governing principle: the thing being LEARNED is in English; everything that "
    "HELPS them learn it is in Uzbek (\"Siz\").\n"
    "- In English: the target vocabulary, example sentences, passages/texts, "
    "collocations, grammar items, and anything the learner must read or produce.\n"
    "- In formal Uzbek (\"Siz\"): all scaffolding — task instructions, framing, "
    "hints, explanations, feedback, and the DPE/reasoning prompts (the UZ bridge).\n"
    "- CEFR (A1–B1+): if the source shows a grade, level the English via "
    "G5→A1, G6→A1+, G7→A2, G8→A2, G9→A2+, G10→B1, G11→B1+ (the Uzbek national "
    "curriculum keeps A2 across the G5–9 band; B1 only after G9); otherwise infer "
    "the level from the source's own complexity (default to A2 if truly "
    "indeterminate). CEFR controls sentence length, tenses, and vocabulary range — "
    "never exceed the level (no B1 vocabulary in an A1/G5 lesson)."
)
_FROZEN_LANG_RUSSIAN = (
    "This is a Russian (L2) lesson for native-Uzbek learners.\n"
    "Governing principle: the thing being LEARNED is in Russian; everything that "
    "HELPS them learn it is in formal Uzbek (\"Siz\").\n"
    "- In Russian: the target vocabulary, example sentences, passages/texts, "
    "collocations, grammar items, and anything the learner must read or produce.\n"
    "- In formal Uzbek (\"Siz\"): all scaffolding — task instructions, framing, "
    "hints, explanations, feedback, and the DPE/reasoning prompts (the UZ bridge).\n"
    "- Level the Russian to the lesson's own complexity and the source's grade; "
    "never exceed what the source uses (no advanced constructions in an early "
    "lesson). Preserve every term, example, and form exactly as in the source; "
    "translate idiomatically into Uzbek, never word-for-word."
)


def test_l2_uz_bridge_is_byte_identical_to_frozen_legacy():
    # RED-provable: change a character in the module's builder/base → this fails.
    assert prompts._l2_rule("english", "uz") == _FROZEN_LANG_ENGLISH
    assert prompts._l2_rule("russian", "uz") == _FROZEN_LANG_RUSSIAN


def test_l2_bridge_follows_medium():
    # l2-bridge-follows-medium: the L2 TARGET stays english/russian, but the
    # scaffolding BRIDGE follows output_language.
    uz_body = prompts.get_prompt("english", "flashcards", output_language="uz")
    ru_body = prompts.get_prompt("english", "flashcards", output_language="ru")
    en_body = prompts.get_prompt("russian", "flashcards", output_language="en")

    # uz medium: the frozen uz-bridge block is present unchanged
    assert _FROZEN_LANG_ENGLISH in uz_body
    assert 'formal Uzbek ("Siz")' in uz_body

    # ru medium (english class): bridge becomes Russian, uz-bridge phrasing gone
    assert "formal Russian" in ru_body
    assert 'formal Uzbek ("Siz")' not in ru_body
    assert "Uzbek (\"Siz\")" not in ru_body   # the bare governing-line bridge too
    # still an L2 english rule, NOT the ru MEDIUM rule for non-L2 subjects
    assert prompts.MEDIUM_RULES["ru"] not in ru_body

    # en medium (russian class): bridge becomes English
    assert "formal English" in en_body
    assert 'formal Uzbek ("Siz")' not in en_body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/services/test_prompts_output_language.py -q`
Expected: FAIL — `ru_body` still contains the Uzbek bridge (behavior not yet changed) and `_l2_rule` doesn't exist.

- [ ] **Step 3: Implement in `app/services/prompts.py`.** **Leave the `_LANG_ENGLISH` and `_LANG_RUSSIAN` string literals exactly as they are** (lines 32-60) — they remain the frozen `uz`-bridge base. Add the bridge maps + a substitution builder immediately after them:

```python
# Scaffolding-bridge phrasing, keyed by output_language (the medium).
_BRIDGE_CLAUSE = {
    "uz": 'formal Uzbek ("Siz")',
    "en": "formal English",
    "ru": 'formal Russian («Вы»)',
}
_BRIDGE_NAME = {"uz": "Uzbek", "en": "English", "ru": "Russian"}
_L2_BASE = {"english": _LANG_ENGLISH, "russian": _LANG_RUSSIAN}


def _l2_rule(target_lang: str, bridge_medium: str) -> str:
    """L2 target-language rule with the scaffolding BRIDGE in `bridge_medium`.
    target_lang in {"english","russian"}; bridge_medium in {"uz","en","ru"}.

    For "uz" (or any unknown medium) returns the frozen base VERBATIM, so the
    uz path is byte-identical to the legacy block by construction. For "en"/"ru"
    it substitutes the bridge phrases on that frozen base — it never rebuilds the
    text, so it cannot silently "fix" the base's authoring asymmetry (English's
    governing line says "in Uzbek", Russian's says "in formal Uzbek")."""
    base = _L2_BASE[target_lang]
    if bridge_medium == "uz" or bridge_medium not in _BRIDGE_CLAUSE:
        return base
    bridge = _BRIDGE_CLAUSE[bridge_medium]
    name = _BRIDGE_NAME[bridge_medium]
    # Order matters: replace the "formal Uzbek (…)" phrase first (scaffolding line
    # + Russian's governing line), then the bare "Uzbek (…)" (English's governing
    # line). "native-Uzbek learners" / "the Uzbek national curriculum" don't match
    # either pattern and are left intact.
    out = base.replace('formal Uzbek ("Siz")', bridge).replace('Uzbek ("Siz")', bridge)
    out = out.replace("(the UZ bridge)", f"(the {name} bridge)")
    out = out.replace("translate idiomatically into Uzbek",
                      f"translate idiomatically into {name}")
    return out
```

> **Note:** `LANGUAGE_RULES` (line 62) still references `_LANG_ENGLISH`/`_LANG_RUSSIAN` directly — since those are unchanged, `LANGUAGE_RULES["english"|"russian"]` keep their exact legacy value. No edit to the `LANGUAGE_RULES` dict.

Then teach `_resolve_language_rule` (line 103) to use the medium as the bridge:

```python
def _resolve_language_rule(subject: str, output_language: str) -> str:
    """L2 language-class subjects (English/Russian) keep their L2 TARGET regardless
    of medium, but their scaffolding BRIDGE follows the chosen medium
    (l2-bridge-follows-medium). Every other subject renders in the chosen medium
    (uz/en/ru), defaulting uz."""
    sd = subjects.REGISTRY.get(subject)
    if sd and sd.language in ("english", "russian"):
        return _l2_rule(sd.language, output_language)
    return MEDIUM_RULES.get(output_language, MEDIUM_RULES["uz"])
```

Leave `LANGUAGE_RULES` as-is (it now holds the `uz`-bridge blocks via the reassigned `_LANG_ENGLISH`/`_LANG_RUSSIAN`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/services/test_prompts_output_language.py tests/services/test_prompts_resolver.py -q`
Expected: PASS (all — including the unchanged `test_uz_default_is_byte_identical_to_legacy`).

- [ ] **Step 5: Commit**

```bash
git add app/services/prompts.py tests/services/test_prompts_output_language.py
git commit -m "cqa: L2 scaffolding bridge follows output medium, uz byte-identical (l2-bridge-follows-medium)"
```

---

### Task 5: Full suite + real generation acceptance smoke (transport=api)

**Files:**
- Create: `scripts/cqa_prompt_smoke.py` (in-process real-model smoke; no server)

**Interfaces:** none (acceptance artifact).

- [ ] **Step 1: Full unit/integration suite green**

Run: `uv run python -m pytest tests/ -q`
Expected: green (the canonical bar is the run WITHOUT `RUN_DB_INTEGRATION` — the DB-integration suite has ~13 known pre-existing isolation failures; the new Task-1 DB test is run under the flag against the scratch DB separately).

- [ ] **Step 2: Write the acceptance smoke script**

`scripts/cqa_prompt_smoke.py` — over `transport=api` (Vertex), in-process. **DB = the `.env` `DATABASE_URL` (production `edu_copy`, where book `860e86aa` lives).** Do NOT hardcode `edu_homework` (old/empty). The script must define the exact boundary-note markers once and assert none of them echo into any generated output:

```python
# markers the boundary note injects — must NEVER appear in student-facing output
_NOTE_MARKERS = ("CURRICULUM BOUNDARY", "The NEXT lesson in this textbook")
```

1. **Boundary:** load the Pythagoras lesson (book `860e86aa`, §17 "Pifagor teoremasi…") from `edu_copy`, resolve its successor via `get_next_in_book`, build `lesson_context` through `_inject_grade` + `_inject_lesson_boundary`, run `agent.run_phase_prompt` for `boss-arena`, print the output. **Pass = (a) boss-arena does NOT introduce the converse / "verify a right angle from side lengths" (§18 material); AND (b) none of `_NOTE_MARKERS` appear verbatim in the output (the note is context, not content to echo).**
2. **Reflection:** run `reflection` for the same lesson; **pass = no pre-asserted "Needs Retry"/"not passed" outcome, no fabricated performance narrative, AND no `_NOTE_MARKERS` echo.**
3. **L2:** render an `english`-subject phase with `output_language="ru"`; **pass = scaffolding is Russian, target stays English.** (If the boundary note is also present in an L2 run, `_NOTE_MARKERS` must not echo either.)

- [ ] **Step 3: Run the smoke (controller runs at the gate; user-authorized single-lesson calls per the CQ-A acceptance spec)**

Run: `uv run python -m scripts.cqa_prompt_smoke` (uses `.env` `DATABASE_URL` = `edu_copy`)
Expected: all checks pass; paste into the PR body — the boss-arena excerpt proving no converse **and no note echo**, the reflection excerpt proving no pre-asserted outcome, and the L2 render proving the Russian bridge.

- [ ] **Step 4: Commit**

```bash
git add scripts/cqa_prompt_smoke.py
git commit -m "cqa: acceptance smoke — boundary + reflection + L2 bridge over transport=api"
```

---

## Finish (after all tasks green + final whole-branch review)

1. `git fetch origin` → `git log HEAD..origin/Nggaev-v2`; if the base moved, **rebase onto `origin/Nggaev-v2`**, resolve conflicts (expect append-only clashes in `MASTER_MEMORY.md`/`INDEX.md`), re-run `uv run python -m pytest tests/ -q`.
2. Open PR titled **`[CQ-A] Prompt-layer content-quality fixes (boundary + reflection + L2 bridge)`** → gatekeeper merges (no self-merge).
3. Worklog **0109** in `docs/memory/MASTER_MEMORY.md` + INDEX row (re-verify 0109 free at finish).
4. Close CQ-A items in `docs/memory/REMEDIATION_CLUSTERS.md` (Cluster 10 CQ-A) + R21 sub-items 1/5 + `l2-bridge-follows-medium` in `docs/memory/ROADMAP.md`.
5. `git mv docs/superpowers/plans/2026-07-02-cq-a-prompt-boundary.md docs/superpowers/plans/shipped/`.
6. De-stale reference docs touched: `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` (note the boundary-note injection + L2-bridge-follows-medium) if they describe the prompt-assembly path.

## Self-Review (author)

- **Spec coverage:** R21.1 (Task 1+2), R21.5 (Task 3), l2-bridge (Task 4), real api smoke (Task 5) — all three CQ-A items + acceptance covered.
- **Placeholder scan:** none — every step has real code/commands.
- **Type consistency:** `get_next_in_book(session, book_id, order_index) -> TOCEntry | None` produced in Task 1, consumed in Task 2; `_inject_lesson_boundary(Optional[str], Optional[str]) -> Optional[str]` matches `_inject_grade`'s shape; `_l2_rule(str, str) -> str` used by `_resolve_language_rule`.
- **Flagged conflict:** Task 4 rewrites `test_l2_subject_ignores_medium_keeps_uzbek_bridge` (enforces the old behavior we reverse) — intentional, resolved by the plan.
