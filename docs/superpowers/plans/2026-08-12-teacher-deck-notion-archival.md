# Plan — Teacher-deck Notion archival (order-independent create-or-adopt)

**Date:** 2026-08-12 · **Branch:** `feat/teacher-material-deck` (PR #135, base `Nggaev-v2`) · **Author:** us (AdxamAxatov)

## Approach & key decisions

**Goal.** A generated **teacher deck** (`kind="teacher_material"`) is archived to Notion the same
way homework is, sharing ONE Lesson Topic page: `Subject → Generated Homeworks → <Lesson Topic> →
{Homework, Teacher Deck}` as siblings, **order-independent** — whichever deliverable is generated
first creates the Lesson Topic; the second adopts it.

- **Create-or-adopt via a persisted lesson-page id.** Today the homework path computes `lesson_id`
  (`notion_archive.py:232`) but never stores it. `find_or_create` is title-idempotent, but
  `resolve_lesson_title` can *suffix* an ambiguous title (`· p.2`, `· {id8}`), so two independent
  archives of the same lesson could land on different suffixed pages. Fix: persist the lesson page
  id on the section (`toc_entries.notion_lesson_page_id`). Whoever archives first stamps it; the
  other reuses it → both siblings guaranteed under one Lesson Topic even for ambiguous titles.
  *(Rejected: rely on title-matching alone — proven fragile by the 2026-08-05 loss that motivated
  `resolve_lesson_title`.)*
- **Reverse the Task-9 skip** at `notion_archive.py:333` (which returns early for every non-homework
  kind) and branch `teacher_material` into a new `_push_teacher_deck_to_notion`; the shared preamble
  (subject-page resolve, `resolve_lesson_title`, token fence, direction guard) stays common.
- **One markdown renderer as the single source of truth.** `render_teacher_deck_markdown(deck)`
  (pure) feeds BOTH consumers: the readable Notion page (existing `blocks.markdown_to_notion_blocks`)
  and the PDF (`markdown`→HTML→WeasyPrint). *(Rejected: two structural renderers — DRY loss;
  rejected: browser `window.print` — client-only, user confirmed server-side PDF.)*
- **Reuse what exists:** file upload is already built (`client.upload_bytes` +
  `blocks.make_file_upload_block`, used by `_leaf_blocks` at `notion_archive.py:193`);
  `_push_with_retry`, `find_or_create`, `page_has_content`, `clear_content_blocks` (idempotent
  replace) all reused. New deps: `weasyprint` + `markdown` (system libs for WeasyPrint verified in
  the acceptance gate).
- **Load-bearing facts verified against code (2026-08-12):** teacher deck's deliverable is
  `content_json` on the `teacher-deck` phase (`jobs.py:672`), NOT `output_md` (that's fidelity
  plain-text), so the teacher path gathers content differently from the homework `phase_md` loop
  (`notion_archive.py:413`); `archive_job` is already invoked for **all** kinds (auto
  `pipeline.py:595`, batch `batch.py:60`, operator `jobs.py:441/482`) — only the `:333` skip blocks
  teacher decks; deck field VALUES are in the book's language, KEYS English; FE viewer
  (`web/src/routes/deck.tsx:80-110`) renders section order cover→passport→objectives→core_idea→
  lesson_map→stages→quiz→answer_key→rubric with Uzbek labels regardless of content language.
- **Dev safety:** every test uses a `MagicMock` Notion client + injected `find_or_create` (pattern
  `tests/services/test_notion_archive.py`) and a scratch DB with an explicit asyncpg
  `DATABASE_URL`. **Zero** real Notion / production writes at any point.

## Collision & ownership (branch-collision gate, re-run 2026-08-12)

- **`app/services/notion_archive.py` — this lane owns it first.** The unbuilt `plan/notion-archive-outbox`
  lane (`docs/superpowers/plans/2026-08-11-durable-notion-archive-outbox.md`) rewrites this file
  wholesale into a leased reconciler + outbox table (migration `0056`), but it is gated behind
  operator-auth + solver `0053` + source-integrity `0054` + pause/defaults `0055` merges and a large
  build — far from landing. This lane is a small additive change on the **current** file, landing
  near-term via PR #135. **Decision (user: "do what's right"):** teacher-deck archival merges first;
  the outbox owner re-incorporates the teacher sibling-push when they rebase (they replace the file
  entirely, so absorbing one `_push_teacher_deck_to_notion` is cheap). **Owed:** a one-line heads-up
  to the outbox owner + a WISHLIST note so they carry teacher archival into the new structure.
- **Migration number — claim `0059`, derive `down_revision` from the real head.** Reserved chain
  across in-flight lanes: `0055` pause/defaults, `0056` outbox, `0057` R24, `0058` extract — all
  unbuilt but reserved. This lane's own Alembic head is `0054_teacher_material_kind` (already on this
  branch). Task 1 takes the next unreserved number **`0059`** and sets `down_revision` to the actual
  current head (`0054_teacher_material_kind`); **re-derive it at Task 1 build time and again after any
  rebase** (do not hardcode a head that a base move invalidates). Record the `0059` reservation in
  `docs/memory/WISHLIST.md`.

## Global constraints

- Homework archival behavior stays **byte-identical** — same pages, same idempotency, same stamps.
  The only homework-side change is that `_push_to_notion` now *also* returns and stamps
  `lesson_id`; the pages it writes must not change.
- `archive_job` remains best-effort (never raises into the pipeline); teacher failures record a skip
  reason, exactly like homework.
- Migration is additive & nullable only (two columns); homework rows untouched.
- Commit per task. Stage only the files each task lists — never `git add -A` (other sessions share
  this branch, esp. `web/`).
- Verify branch `feat/teacher-material-deck` before every commit (`|| exit 1` guard).

---

## Task 1 — Migration + model + repo setters (schema)

**Files:** new `alembic/versions/0059_*.py`, `app/models/toc_entry.py`, `app/repositories/toc_entries.py`, `tests/`

Add two nullable columns to `toc_entries`:
- `notion_lesson_page_id String(128) NULL` — the shared **Lesson Topic** page (parent of both
  Homework and Teacher Deck). Distinct from `notion_homework_page_id` (the *Homework* sub-page).
- `notion_teacher_deck_job_id UUID NULL` — the job whose deck is currently on the Teacher Deck page
  (teacher-side mirror of `notion_archived_job_id`, for auto-replace direction + rollup).

**TDD:**
1. RED: a model test asserting `TOCEntry` has both attributes defaulting `None`; a repo test for two
   new setters `set_notion_lesson_page_id(session, section_id, page_id)` and
   `set_notion_teacher_deck_job(session, section_id, job_id)` (mirror `toc_entries.py:136-153`),
   run under `RUN_DB_INTEGRATION=1` against a **scratch** asyncpg `DATABASE_URL` (127.0.0.1).
2. GREEN: `uv run alembic revision -m "toc_entries teacher-deck notion columns"`, then **rename the
   file to `0059_*`** and set `down_revision` to the real current head (confirm with
   `uv run alembic heads` — expect `0054_teacher_material_kind` on this branch; if a rebase moved it,
   use whatever `heads` reports). Revision id ≤32 chars; `op.add_column` both (nullable), downgrade
   drops both. Add the mapped columns to `toc_entry.py` (after `:33`) and the two setters to the repo.
   Add the `0059` reservation line to `docs/memory/WISHLIST.md`.
3. Verify: `uv run alembic upgrade head` on scratch DB; `uv run alembic downgrade -1` clean.

**Commit:** `feat(db): add notion_lesson_page_id + notion_teacher_deck_job_id to toc_entries (mig 0059)`

---

## Task 2 — `render_teacher_deck_markdown(deck)` pure renderer

**Files:** `app/services/teacher_deck.py` (add function), `tests/services/test_teacher_deck_render.py`

Pure function: `render_teacher_deck_markdown(deck: TeacherDeck) -> str` → complete lesson-plan
markdown. Section order mirrors the FE viewer; bilingual headings (Uzbek label + English gloss);
values emitted verbatim (already localized). Full deck incl. `pair_work` + `conclusion` (the readable
document is more complete than the slide flow, by design).

Structure (headings `##`, one blank line between blocks so `markdown_to_notion_blocks` flushes
paragraphs correctly):

```
# {meta.topic_number}. {meta.topic_title}
{meta.subject_label} · {meta.grade} · {meta.lesson_type} · {meta.duration_min} daqiqa

## Pasport / Passport
- **Fan/sinf:** {passport.fan_sinf}
- **Mavzu:** {passport.mavzu}
- **Dars turi:** {passport.dars_turi}
- **Metod:** {passport.metod}
- **Kerakli vosita:** {passport.kerakli_vosita}
- **Baholash:** {passport.baholash}
- **Usullar / Method:** {", ".join(meta.method)}
- **Materiallar / Materials:** {", ".join(meta.materials)}
[if meta.video_ref:] - **Video:** {meta.video_ref}

## Maqsad / Objectives
- **Bilib oladi:** {objectives.bilib_oladi}
- **Qila oladi:** {objectives.qila_oladi}
- **Tushunadi:** {objectives.tushunadi}

## Asosiy g'oya / Core idea
{core_idea.statement}

{core_idea.elaboration}

## Dars xaritasi / Lesson map
- {item.index}. **{item.title}** — {item.minutes} daqiqa: {item.description}   (each, sorted by index)

## Bosqichlar / Stages
### {stage.index}-bosqich · {stage.title} ({stage.minutes} daqiqa)   (each, sorted by index)
- **O'qituvchi:** {stage.teacher_action}
- **O'quvchi:** {stage.student_action}
- {point.title}: {point.detail}     (per point)
[if stage.screen_text:] blank line then: **Ekran:** {stage.screen_text}

## Test / Quiz
**{q.number}. {q.question}**       (per quiz item)
- A) {opt.text}  … all options
_To'g'ri javob: {q.correct_label} · Yordam: {q.hint}_

## Javoblar kaliti / Answer key
- **{a.number}. ({a.correct_label})** {a.explanation}    (per item)

## Juftlikda ish / Pair work
{pair_work.intro}
- **{task.title}:** {task.prompt}    (per task)

## Yakun / Conclusion
- {question}    (per question)

## Baholash mezoni / Rubric
- **{c.title}** — {c.points} ball: {c.detail}   (per component)

**Jami / Total: {rubric.total} ball**
- {band.range}: {band.grade}    (per band)
```

**TDD:** RED first — a test building a minimal valid `TeacherDeck` (reuse/borrow the fixture from
`tests/` for the teacher-deck phase; if none is importable, construct one inline) asserting: topic
number+title in the H1, every passport field present, every stage's `index` heading present, quiz
options A–D rendered, `correct_label` shown, rubric total line present, and — a **content-loss
guard** — distinctive strings from `pair_work.tasks[0].prompt`, `conclusion.questions[0]`,
`meta.method[0]`, and `meta.materials[0]` all appear (these five are each dropped somewhere — by the
fidelity serializer, or absent from the FE slide flow; the readable page must keep them all).
GREEN: implement. This is transcription — keep it a pure string builder, no I/O.

**Commit:** `feat(deck): render_teacher_deck_markdown — full lesson-plan markdown`

---

## Task 3 — `render_teacher_deck_pdf(deck)` via WeasyPrint

**Files:** `pyproject.toml` (deps), `app/services/teacher_deck.py` (add), `tests/services/test_teacher_deck_pdf.py`

`render_teacher_deck_pdf(deck: TeacherDeck) -> bytes`: `render_teacher_deck_markdown(deck)` →
`markdown.markdown(md, extensions=["extra"])` → wrap in a minimal self-contained HTML doc with a
small print stylesheet (A4, readable margins, heading/table styling) → `weasyprint.HTML(string=…)
.write_pdf()`. Add `weasyprint` and `markdown` to `pyproject.toml` deps; `uv sync`.

**CRITICAL — lazy import.** `import weasyprint` MUST live **inside** `render_teacher_deck_pdf`, never
at module top. `notion_archive.py` imports `teacher_deck`, and `archive_job` runs on every worker
(`pipeline.py:595`); a top-level import turns a missing native lib (pango/cairo — **absent on this
Mac and painful on the Windows workers `pywin32 ; sys_platform=='win32'` implies**) into an
import-time crash on the archival path fleet-wide. The lazy import lets `OSError`/`ImportError`
propagate to the caller (Task 5 catches it and degrades). `markdown` (pure Python) can import at top.

**TDD:** RED — test asserts the return is `bytes`, starts with `%PDF-`, and is `> 1000` in size,
**guarded** `pytest.importorskip("weasyprint")` (+ skip if native libs missing) so the suite stays
green on hosts without pango; the real-PDF proof happens in the Task 8 acceptance on a pango-equipped
head. Also a test asserting the module imports cleanly with `weasyprint` unavailable (monkeypatch the
import to raise) — proving no top-level dependency. GREEN — implement.

**Commit:** `feat(deck): render_teacher_deck_pdf — lazy WeasyPrint HTML→PDF`

---

## Task 4 — Homework path returns `lesson_id` + lesson-page adoption helper

**Files:** `app/services/notion_archive.py`, `tests/services/test_notion_archive.py`

Refactor `_push_to_notion` (`:206-255`) to **also** produce the lesson page id and support adopting a
pre-known one, keeping homework pages byte-identical:
- New param `lesson_page_id: Optional[str] = None`.
- Return type → `tuple[Optional[str], str]` = `(lesson_id, homework_id)`.
- In the create branch (`:230-233`): `container_id = find_or_create(client, subject_page_id,
  CONTAINER_TITLE)[0]`; `lesson_id = lesson_page_id or find_or_create(client, container_id,
  lesson_title)[0]`; `homework_id = find_or_create(client, lesson_id, "Homework")[0]`.
- In the reuse branch (`homework_page_id` set): `homework_id = homework_page_id`; for `lesson_id`,
  prefer `lesson_page_id`, else **backfill** via `client.get_page_parent(homework_page_id)` (already
  exists, `notion/client.py:82`) — the Homework sub-page's parent IS the lesson page. This is what
  reaches the ~3,200 already-archived sections whose `notion_lesson_page_id` is NULL: without it,
  their future teacher decks fall back to fragile title matching. Guard the call in try/except (a
  transient read failure leaves `lesson_id=None`, stamp simply skipped that run — self-heals next
  archive).
- `_push_with_retry` (`:258`) threads `lesson_page_id` through and returns the tuple.

Then update the single homework caller in `archive_job` (`:436-445`) to unpack `(lesson_id,
homework_id)`, pass `lesson_page_id=section.notion_lesson_page_id`, and in the pointer-update session
(`:468`) additionally `set_notion_lesson_page_id(session, section_id, lesson_id)` **when `lesson_id
is not None` and the section didn't already have one**.

**TDD:** RED — extend the existing fake-client tests: assert `_push_to_notion` returns a 2-tuple, that
passing `lesson_page_id="LID"` makes the Homework page a child of `"LID"` (no new lesson
find_or_create call), and that the homework leaf pages written are unchanged from before (same
titles, same order). GREEN — implement.

**Commit:** `refactor(notion): _push_to_notion returns (lesson_id, homework_id); adopt lesson page`

---

## Task 5 — `_push_teacher_deck_to_notion` + retry + blocks

**Files:** `app/services/notion_archive.py`, `tests/services/test_notion_archive.py`

New sibling push, mirroring `_push_to_notion`'s shape:

```python
def _teacher_deck_blocks(client, deck) -> list[dict]:
    md = render_teacher_deck_markdown(deck)
    content = blocks.markdown_to_notion_blocks(md)          # readable page: PRIMARY deliverable
    try:                                                    # ONLY the render is swallowed (Blocker 3)
        pdf = render_teacher_deck_pdf(deck)                 # missing pango/native lib → page-only
    except Exception as exc:  # noqa: BLE001
        log.warning("notion: teacher-deck PDF render failed, writing page without attachment: %s", exc)
        return content
    # Upload is OUTSIDE the try: a transient Notion 429 / network blip must propagate into
    # _push_teacher_with_retry (which exists to retry it), NOT silently degrade to a PDF-less page
    # that the next archive then skips forever via page_has_content. Distinct filename from the FE
    # slide export (df4ee5f, same {grade}-sinf {n}-mavzu {title}) — this is the lesson-plan document.
    fname = f"{deck.meta.grade}-sinf {deck.meta.topic_number}-mavzu {deck.meta.topic_title} — dars ishlanma.pdf"
    upload = client.upload_bytes(pdf, fname, "application/pdf")
    return [blocks.make_file_upload_block(upload, fname), blocks.make_divider(), *content]

def _push_teacher_deck_to_notion(*, client, subject_page_id, lesson_title, deck,
                                 find_or_create=find_or_create, replace=False,
                                 lesson_page_id=None) -> tuple[str, str]:
    container_id = find_or_create(client, subject_page_id, CONTAINER_TITLE)[0]
    lesson_id = lesson_page_id or find_or_create(client, container_id, lesson_title)[0]
    deck_id = find_or_create(client, lesson_id, "Teacher Deck")[0]
    populated = client.page_has_content(deck_id)
    if populated and not replace:
        return lesson_id, deck_id               # idempotent skip
    # Build (render + upload) BEFORE clearing, so a render/upload failure on a force re-archive can
    # never leave the page emptied — clear_content_blocks runs only once the new body is in hand.
    body = _teacher_deck_blocks(client, deck)
    if populated:                                # replace path
        client.clear_content_blocks(deck_id)
    client.append_block_children(deck_id, body)
    return lesson_id, deck_id
```

Plus `_push_teacher_with_retry(*, client, subject_page_id, lesson_title, deck, replace, lesson_page_id)`
mirroring `_push_with_retry` (`:258`) — same backoff, runs in `asyncio.to_thread`. A transient upload
error inside the push therefore gets the same bounded retry as homework.

**TDD:** fake-client tests (`MagicMock`, injected `find_or_create` returning distinct ids per title):
assert the chain creates/adopts `Generated Homeworks → lesson → Teacher Deck`; that the PDF upload +
markdown blocks are appended; that a second call with `page_has_content→True, replace=False` **skips**
(no append); that `replace=True` clears then rewrites; that passing `lesson_page_id` skips the lesson
find_or_create (adoption). Stub `render_teacher_deck_pdf` to return `b"%PDF-stub"` so tests need no
WeasyPrint system libs. **Degrade test:** stub `render_teacher_deck_pdf` to raise `OSError` → assert
the page is still written (markdown blocks appended, `upload_bytes` NOT called, no exception escapes).
**Propagation test:** stub `render` OK but make `client.upload_bytes` raise → assert the exception
**propagates** (NOT swallowed) so `_push_teacher_with_retry` can retry it, and that on a `replace`
call `clear_content_blocks` was NOT called before the failure (build-before-clear ordering).

**Commit:** `feat(notion): _push_teacher_deck_to_notion — Teacher Deck sibling page`

---

## Task 6 — Wire `archive_job`: reverse the skip, branch teacher, stamp columns

**Files:** `app/services/notion_archive.py`, `tests/services/test_notion_archive.py`

Reverse the `:333` early-return and branch the two paths through the **shared** preamble. While
there, **correct the stale comment** at `:333-343`: it blames "no `_PAGES` entry a teacher deck could
match," but subject-page resolution (`_resolve_subject_page_id`) is kind-independent — it keys on
subject/grade/language, which a teacher deck has. The real reason decks were skipped was the missing
`output_md` deliverable; the new branch handles that by reading `content_json` instead.
- Keep the shared setup (`:345-412`): token fence, `already-archived` guard, book/section fetch,
  subject-page resolve, `resolve_lesson_title`, `first_archive`/`prior_job` direction. For the
  teacher path, direction uses `section.notion_teacher_deck_job_id` (not `notion_archived_job_id`),
  and `first_archive_deck = section.notion_teacher_deck_job_id is None`.
- Content gather is **kind-appropriate**:
  - homework → existing `phase_md` loop (`:413`) + `if not phase_md: skip` guard (`:419`).
  - teacher → load the `teacher-deck` phase's `content_json`, validate `TeacherDeck.model_validate`.
    If absent/invalid → `set_notion_skip_reason(…, "no teacher deck content")` and return.
- Push branch:
  - homework → `_push_with_retry` (unchanged, Task 4 signature).
  - teacher → `_push_teacher_with_retry(...)`; on success, in the pointer-update session stamp
    `set_notion_lesson_page_id(section_id, lesson_id)` and, when `first_archive_deck or do_replace`,
    `set_notion_teacher_deck_job(section_id, job_id)`; then `set_notion_archived(job_id, now)`. The
    same TOCTOU token re-check (`:459-467`) wraps both.

**TDD:** fake-client integration tests (scratch DB, `MagicMock` client patched onto
`NotionClientWrapper`, `render_teacher_deck_pdf` stubbed):
1. A `teacher_material` done job with `content_json` archives → creates `Generated Homeworks →
   lesson → Teacher Deck`, stamps `notion_lesson_page_id` + `notion_teacher_deck_job_id` +
   `notion_archived_at`.
2. **Order-independence:** archive a teacher job first (stamps lesson id), THEN a homework job on the
   same section → homework adopts the SAME `notion_lesson_page_id` (Homework sub-page is a child of
   the lesson the teacher created); and the reverse order → teacher adopts the homework-created
   lesson.
3. Idempotent: re-archiving an already-archived teacher job is a no-op (skip on populated page).
4. A teacher job with no `content_json` records the skip reason, doesn't raise.
5. Homework-only regression: an existing homework archival test still passes unchanged.

**Commit:** `feat(notion): archive teacher decks as a Lesson-Topic sibling (order-independent)`

---

## Task 7 — Kind-aware stale rollup (Blocker 2)

**Files:** `app/repositories/batches.py`, `tests/`

`archive_rollup_for_batch` (`:123`) and `done_stale_job_ids` (`:215`) compute `stale` from
`TOCEntry.notion_archived_job_id != job_id`. The teacher path stamps `notion_teacher_deck_job_id`, so
in a `teacher_material` batch any section that ALSO has a homework deck reads its Teacher Deck as
`stale` forever → the force-sweep (`done_stale_job_ids`, `jobs.py` refresh path) would clear-and-
rewrite every deck page each run. *(Note: `archived`/`unarchived` key on `HomeworkJob.notion_archived_at`
— which the teacher path DOES stamp — so those two counts are already correct; only `stale` and the
stale worklist are wrong.)*

Fix: make both functions **kind-aware**. There is no `batches_repo.get` — do NOT invent one. Instead
**join `Batch.kind` into the existing query** (`select(...).join(Batch, Batch.id == batch_id)` or add
`Batch.kind` to the selected columns via the batch filter already present), then in Python pick the
comparison column: when the batch kind is `teacher_material`, compare `TOCEntry.notion_teacher_deck_job_id`
instead of `notion_archived_job_id` — both the `stale` sum in `archive_rollup_for_batch` and the two
`.where(notion_archived_job_id …)` clauses in `done_stale_job_ids`. Batches already fork on `kind`
(`uq_batches_book_id_transport_output_language_kind`), so a batch is single-kind — one column choice
per call, no mixing, no extra round trip.

**TDD:** RED — seed a teacher batch where a section has BOTH `notion_archived_job_id` (an old homework
job) and `notion_teacher_deck_job_id` (this batch's teacher job); assert `stale == 0` (the deck is
current) and `done_stale_job_ids == []`. A homework batch on the same fixture still keys on
`notion_archived_job_id` (unchanged behavior). GREEN — implement. Scratch DB, `RUN_DB_INTEGRATION`.

**Commit:** `fix(batch): kind-aware stale rollup so teacher decks aren't perma-stale`

---

## Task 8 — Acceptance gate + finish

**Acceptance (proof, not theory):**
1. **WeasyPrint on the head:** `brew install pango` (pulls cairo/glib/harfbuzz/fontconfig/
   gdk-pixbuf); re-probe `python3 -c "import weasyprint"` succeeds. This makes the head able to prove
   real PDF. **Rollout decision:** pango is a fleet-wide install step, not a head-only one — the
   degrade path is a *safety net*, not the expected path. Since `archive_job` runs on workers
   (`pipeline.py:595`), a worker without pango ships every deck PDF-less **permanently** (the next
   archive skips on `page_has_content`). So the PDF half of the feature does not truly ship until the
   fleet has pango. See the finish-block OWED item.
2. **Archival end-to-end, fake Notion + scratch DB:** a script/test that runs `archive_job` for a
   real seeded teacher-deck job (content_json from the demo deck) against a `MagicMock`
   `NotionClientWrapper` and asserts the exact page tree + stamps + that `render_teacher_deck_pdf`
   produced a real `%PDF-` byte string (WeasyPrint actually invoked here, now that pango is present).
   **Zero** real Notion calls.
3. **Degrade proof:** re-run the same end-to-end with `render_teacher_deck_pdf` monkeypatched to raise
   → the readable page is still written, `upload_bytes` never called, job stamped archived (a
   pango-less worker still archives).
4. Full suite green: `uv run python -m pytest tests/ -q` (note: the pre-existing
   `credential_max_concurrent_gemini` env-artifact failure from the repo `.env` symlink is unrelated
   — confirm it's the only red and not caused by this work).
5. `cd web && npx tsc -p tsconfig.app.json --noEmit` (no FE change expected, but confirm).

**Finish (same pass, not deferred):**
- Rebase-check: `git fetch origin` + `git log HEAD..origin/Nggaev-v2`; rebase if base moved, re-run
  suite.
- `finishing-a-development-branch` → push to `feat/teacher-material-deck` (PR #135); **do not
  self-merge** (I'm gatekeeper).
- Worklog entry in `docs/memory/MASTER_MEMORY.md` + `INDEX.md` row; close the item in
  `docs/memory/ROADMAP.md`; `git mv` this plan into `docs/superpowers/plans/shipped/`.
- De-stale `docs/HOW_IT_WORKS.md` + `docs/CODE_MAP.md` (Notion archival now covers both kinds) +
  `docs/DATABASE.md` (two new `toc_entries` columns).
- **OWED (operator rollout, record in ROADMAP):** (a) `brew install pango` (or the platform
  equivalent) across the **whole fleet**, so decks archive WITH their PDF — degrade is only a net.
  (b) Any decks that already archived PDF-less on a pango-less worker are recoverable by the head-side
  batch re-archive **force** sweep (`batch.py:56`, runs in the API process = the pango-equipped head)
  over the batch's `done` jobs — NOT `done_stale_job_ids`, which won't surface them (a PDF-less deck
  is current, not stale). (c) the outbox-owner heads-up + WISHLIST note from the Collision section.

**Commit:** `docs: worklog + de-stale for teacher-deck Notion archival`

---

## Risks / open

- **WeasyPrint native libs are absent on this Mac AND painful on Windows workers** (probed: pango,
  gobject, fontconfig, harfbuzz, gdk-pixbuf, cairo all `None`). Resolved by design: lazy import
  (Task 3) + write-page-omit-PDF degrade (Task 5) keeps the fleet from crashing. **Rollout call
  (user "do what's right"):** install pango **fleet-wide** so the PDF actually ships — degrade is a
  safety net, not the plan. The degrade is sticky (a PDF-less page is skipped on the next archive),
  so backfill is via the head-side **force** re-archive sweep, not the stale sweep. NOT swapping the
  engine (user chose WeasyPrint).
- **`notion_archive.py` ownership vs the outbox lane** — resolved: teacher lane first (see Collision
  & ownership). Residual: the outbox owner must re-absorb `_push_teacher_deck_to_notion` on rebase;
  Task 8 files the WISHLIST note + heads-up.
- **Reuse-branch backfill window** — `notion_lesson_page_id` self-heals via `get_page_parent`
  (Task 4) on the next homework archive of a pre-existing section; until then a teacher deck on a
  never-re-archived section falls back to deterministic `resolve_lesson_title`. Narrow window, opens
  only if a TOC re-extract changes the sibling set between the two archives.
- `notion_teacher_deck_job_id` uses the same newer-wins direction guard as homework; an older
  teacher job re-archiving must not clobber a newer deck page (covered by the shared preamble).
