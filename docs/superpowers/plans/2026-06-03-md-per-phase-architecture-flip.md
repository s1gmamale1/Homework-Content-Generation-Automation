# Markdown-Per-Phase — Architecture Flip (Effort A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every content phase emit one markdown file (no JSON/schema/assembly); per-phase md in `phase_outputs.output_md` is the deliverable, rendered to Notion as one sub-page per phase, validated by a deterministic warn-only checker.

**Architecture:** Strip the structured layer. Phases run free-text (`run_phase_prompt` → `output_md`); a pure validator records warnings on the phase row; Notion archive builds one sub-page per phase; the console and download read `phase_outputs`. The `*_json`/`assembled_md` columns and the synth/assembly code are removed last, after all readers have switched.

**Tech Stack:** FastAPI, SQLAlchemy + Alembic (Postgres/JSONB), pytest/pytest-asyncio, React + Vite + TanStack Query + react-markdown, notion-client.

**Spec:** `docs/superpowers/specs/2026-06-03-md-per-phase-generation-design.md`

**Ordering rule (from the spec's sequencing constraints):** T1 (additive column) is non-destructive and lands first. Notion (T4), download (T5), and frontend (T6) switch to per-phase data **before** the pipeline flip (T7) stops writing `assembled_md`/`*_json`. The destructive column-drop migration (T9) and code teardown (T10) are last. Between T6 and T7 the system still writes the old columns; readers ignore them and read `phase_outputs.output_md` (which is the `_synth_md_for_structured` summary pre-flip, raw model md post-flip) — no broken window.

**Commands:**
- Backend tests: `uv run python -m pytest tests/ -q` (single: `uv run python -m pytest tests/path::test -v`)
- Migration: `uv run alembic upgrade head` / `uv run alembic downgrade -1`
- Frontend gate: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
- On Windows the test runner is `& ".\.venv\Scripts\python.exe" -m pytest …` (uv is not on PATH in this shell).

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `app/models/phase_output.py` | + `validation_warnings` JSONB column | T1 |
| `app/repositories/phase_outputs.py` | `set_status`/`create_or_reset` carry warnings | T1 |
| `alembic/versions/0017_phase_validation_warnings.py` | additive column migration | T1 |
| `app/services/phase_validator.py` (new) | deterministic per-phase markdown validator | T2 |
| `app/services/notion/blocks.py` | markdown image → callout/external-image block | T3 |
| `app/services/notion_archive.py` | one Notion sub-page per phase | T4 |
| `app/api/v1/jobs.py` | download = zip of per-phase `.md` | T5 |
| `app/schemas/job.py` | `PhaseOut.validation_warnings` | T6 |
| `web/src/lib/types.ts` | `PhaseOut.validation_warnings` | T6 |
| `web/src/routes/preview.tsx` | render each phase's `output_md` + warnings | T6 |
| `web/src/routes/job.tsx` | `DonePanel` counts from phases | T6 |
| `web/src/lib/flow-v2-phases.tsx` + `components/flow-v2/*` | deleted | T6 |
| `app/services/pipeline.py` | md-only `_execute_phase`; validator; no assembly | T7 |
| `prompts/_general/*.md` | minimal JSON→md output instruction | T8 |
| `alembic/versions/0018_drop_structured_columns.py` + `app/models/homework_job.py` | drop `assembled_md` + `*_json` | T9 |
| pipeline/agent/job_artifacts/schemas removals | dead-code teardown | T10 |

---

## Task 1: Add `validation_warnings` to phase rows (additive, non-destructive)

**Files:**
- Modify: `app/models/phase_output.py`
- Modify: `app/repositories/phase_outputs.py:96-123` (`set_status`), `:63-74` (`create_or_reset` reset block)
- Create: `alembic/versions/0017_phase_validation_warnings.py`
- Test: `tests/repositories/test_phase_validation_warnings.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/repositories/test_phase_validation_warnings.py
import asyncio
from uuid import uuid4

from app.models import PhaseOutput
from app.repositories import phase_outputs as phase_repo


def test_set_status_persists_validation_warnings(db_session_factory):
    async def run():
        async with db_session_factory() as s:
            job_id = await _make_job(s)  # helper from conftest creating a HomeworkJob
            po = await phase_repo.create_or_reset(
                s, job_id=job_id, phase_name="flashcards", phase_order=1,
                prompt_hash="h", model_name="m",
            )
            await s.commit()
            await phase_repo.set_status(
                s, po.id, "done", validation_warnings=["missing top-level heading"],
            )
            await s.commit()
            got = await s.get(PhaseOutput, po.id)
            assert got.validation_warnings == ["missing top-level heading"]

    asyncio.run(run())


def test_create_or_reset_clears_validation_warnings(db_session_factory):
    async def run():
        async with db_session_factory() as s:
            job_id = await _make_job(s)
            po = await phase_repo.create_or_reset(
                s, job_id=job_id, phase_name="flashcards", phase_order=1,
                prompt_hash="h", model_name="m",
            )
            await phase_repo.set_status(s, po.id, "done", validation_warnings=["w"])
            await s.commit()
            po2 = await phase_repo.create_or_reset(
                s, job_id=job_id, phase_name="flashcards", phase_order=1,
                prompt_hash="h2", model_name="m",
            )
            await s.commit()
            assert po2.id == po.id
            assert po2.validation_warnings is None

    asyncio.run(run())
```

> **Scene-setting:** `tests/` has a Postgres-backed async session fixture used by other repo tests (see `tests/repositories/test_notion_repo_methods.py` for the fixture name in this repo and a `_make_job` helper pattern). Reuse the existing fixture/helper names rather than inventing new ones; if a `_make_job` helper does not already exist in `conftest.py`, add a minimal one that inserts a `HomeworkJob` with required FKs (book + toc_entry) — mirror how `test_notion_repo_methods.py` seeds rows.

- [ ] **Step 2: Run test to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/repositories/test_phase_validation_warnings.py -q`
Expected: FAIL — `TypeError: set_status() got an unexpected keyword argument 'validation_warnings'` (and `AttributeError: validation_warnings` on the model).

- [ ] **Step 3: Add the model column**

In `app/models/phase_output.py`, add the JSONB import and the column after `error_message` (line 25):

```python
from sqlalchemy.dialects.postgresql import JSONB   # add to imports
```
```python
    # Deterministic validator output for this phase's markdown (list[str]).
    # Warn-only — never blocks generation. Surfaced per-phase in the console.
    validation_warnings: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 4: Thread it through the repo**

In `app/repositories/phase_outputs.py` `set_status` signature (after `error_message` param) add:

```python
    validation_warnings: Optional[list] = None,
```
and in the body (after the `error_message` block, line ~123):

```python
    if validation_warnings is not None:
        po.validation_warnings = validation_warnings
```

In `create_or_reset`, inside the `if existing is not None:` reset block (after `existing.completed_at = None`, line 73) add:

```python
        existing.validation_warnings = None
```

- [ ] **Step 5: Create the additive migration**

```python
# alembic/versions/0017_phase_validation_warnings.py
"""phase_outputs.validation_warnings

Revision ID: d1f4a9b3c7e2
Revises: c9e3f1a07b62
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1f4a9b3c7e2"
down_revision: Union[str, Sequence[str], None] = "c9e3f1a07b62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "phase_outputs",
        sa.Column("validation_warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("phase_outputs", "validation_warnings")
```

- [ ] **Step 6: Apply migration + run tests**

Run: `uv run alembic upgrade head` then `& ".\.venv\Scripts\python.exe" -m pytest tests/repositories/test_phase_validation_warnings.py -q`
Expected: migration OK; both tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/models/phase_output.py app/repositories/phase_outputs.py alembic/versions/0017_phase_validation_warnings.py tests/repositories/test_phase_validation_warnings.py
git commit -m "feat(phases): add validation_warnings column + repo plumbing"
```

---

## Task 2: Deterministic validator module

**Files:**
- Create: `app/services/phase_validator.py`
- Test: `tests/services/test_phase_validator.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_phase_validator.py
from app.services import phase_validator as pv


def test_empty_output_warns():
    assert pv.validate("flashcards", "   \n  ") == ["empty output"]


def test_missing_top_heading_warns():
    out = pv.validate("flashcards", "some body text\n\nmore text")
    assert "missing top-level heading (`# `)" in out


def test_well_formed_markdown_no_warnings():
    md = "# Flashcards\n\nsome body\n"
    assert pv.validate("flashcards", md) == []


def test_placeholder_image_is_allowed():
    md = "# Case\n\n![placeholder: lab bench — image gen required](placeholder)\n"
    assert pv.validate("case-based-preview", md) == []


def test_broken_image_target_warns():
    md = "# Case\n\n![scene](scene.png)\n"
    out = pv.validate("case-based-preview", md)
    assert any("non-resolving image target" in w for w in out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_phase_validator.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.phase_validator`.

- [ ] **Step 3: Implement the module**

```python
# app/services/phase_validator.py
"""Deterministic, warn-only validator for per-phase markdown.

Pure functions, no LLM, no I/O. Effort A ships the framework + a starter set
of common rules (non-empty body, a top-level heading, well-formed visuals).
Effort B authors each phase's full rule list in RULES alongside its prompt
rewrite. Warnings never block generation — they are recorded on the phase row
and surfaced in the operator console.
"""

from __future__ import annotations

import re
from typing import Callable

# A rule takes the phase markdown and returns a warning string, or None.
Rule = Callable[[str], "str | None"]

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")
_PLACEHOLDER_TARGET = "placeholder"


def _non_empty(md: str) -> str | None:
    return "empty output" if not md.strip() else None


def _has_top_heading(md: str) -> str | None:
    for line in md.splitlines():
        if line.lstrip().startswith("# "):
            return None
    return "missing top-level heading (`# `)"


def _visuals_resolve(md: str) -> str | None:
    """A markdown image must be an inline http(s) URL or the `placeholder`
    sentinel (raster the model deliberately did not generate). Anything else
    is a real broken link."""
    for target in _IMAGE_RE.findall(md):
        t = target.strip()
        if t == _PLACEHOLDER_TARGET:
            continue
        if t.startswith(("http://", "https://")):
            continue
        return f"non-resolving image target: {target!r} (use an http(s) URL or the `placeholder` sentinel)"
    return None


# Common rules run for every phase. Empty body short-circuits the rest.
_COMMON: list[Rule] = [_has_top_heading, _visuals_resolve]

# Per-phase extra rules — populated in Effort B (e.g. CBP checkpoint/learning-block counts).
RULES: dict[str, list[Rule]] = {}


def validate(phase_name: str, md: str, *, subject: str = "") -> list[str]:
    empty = _non_empty(md)
    if empty:
        return [empty]
    warnings: list[str] = []
    for rule in (*_COMMON, *RULES.get(phase_name, [])):
        w = rule(md)
        if w:
            warnings.append(w)
    return warnings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_phase_validator.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/phase_validator.py tests/services/test_phase_validator.py
git commit -m "feat(validator): deterministic warn-only phase markdown validator"
```

---

## Task 3: Notion blocks — markdown image → callout / external image

**Files:**
- Modify: `app/services/notion/blocks.py` (add `make_callout`, image handling in `markdown_to_notion_blocks`)
- Test: `tests/services/test_notion_blocks.py` (extend existing)

- [ ] **Step 1: Write the failing test** (append to `tests/services/test_notion_blocks.py`)

```python
from app.services.notion import blocks


def test_placeholder_image_becomes_callout():
    out = blocks.markdown_to_notion_blocks("![a lab bench — image gen required](placeholder)")
    assert len(out) == 1
    assert out[0]["type"] == "callout"
    text = out[0]["callout"]["rich_text"][0]["text"]["content"]
    assert "a lab bench" in text


def test_non_http_image_becomes_callout():
    out = blocks.markdown_to_notion_blocks("![scene](scene.png)")
    assert out[0]["type"] == "callout"


def test_http_image_becomes_external_image_block():
    out = blocks.markdown_to_notion_blocks("![x](https://example.com/i.png)")
    assert out[0]["type"] == "image"
    assert out[0]["image"]["external"]["url"] == "https://example.com/i.png"
```

- [ ] **Step 2: Run to verify failure**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_notion_blocks.py -q`
Expected: FAIL — placeholder/image lines currently fall through to a paragraph block (`type == "paragraph"`).

- [ ] **Step 3: Implement**

Add a callout builder near `make_divider` in `app/services/notion/blocks.py`:

```python
def make_callout(text: str, emoji: str = "🖼️") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": text[:_MAX_SEG]}}],
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def make_external_image(url: str) -> dict:
    return {"object": "block", "type": "image", "image": {"type": "external", "external": {"url": url}}}


_IMAGE_LINE_RE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<url>[^)]*)\)$")
```

In `markdown_to_notion_blocks`, add an image branch **before** the heading match (after the `---` divider branch, line ~108):

```python
        img = _IMAGE_LINE_RE.match(stripped)
        if img:
            _flush()
            url = img.group("url").strip()
            alt = img.group("alt").strip()
            if url.startswith(("http://", "https://")):
                out.append(make_external_image(url))
            else:
                # placeholder / non-resolving target → carry the description as a
                # callout (never an image block with an unresolvable URL).
                out.append(make_callout(alt or "visual placeholder"))
            continue
```

- [ ] **Step 4: Run to verify pass**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_notion_blocks.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/notion/blocks.py tests/services/test_notion_blocks.py
git commit -m "feat(notion): render markdown images as callout (placeholder) / external image"
```

---

## Task 4: Notion archive — one sub-page per phase

**Files:**
- Modify: `app/services/notion_archive.py` (`archive_job`, `_push_to_notion`, add `PHASE_TITLES`)
- Test: `tests/services/test_notion_archive.py` (rewrite the two `_push_to_notion` tests)

- [ ] **Step 1: Write the failing test** — replace `test_push_skips_write_when_page_already_populated` and `test_push_writes_blocks_and_attachments_when_empty` in `tests/services/test_notion_archive.py` with:

```python
def test_push_creates_one_subpage_per_phase():
    client = MagicMock()
    client.page_has_content.return_value = False  # each new subpage empty
    client.upload_bytes.return_value = "upl_x"
    # find_or_create returns (page_id, created) — Homework + 2 phase subpages
    na_find = MagicMock(side_effect=[("hw_1", True), ("p_cbp", True), ("p_fc", True)])
    phases = [
        ("Case-Based Preview", "case-based-preview", "# Case\n\nbody"),
        ("Flashcards", "flashcards", "# Flashcards\n\nbody"),
    ]
    na._push_to_notion(
        client=client, subject_page_id="subj_1", lesson_title="1.1 Burchaklar",
        phases=phases, find_or_create=na_find,
    )
    # 1 Homework + 2 phase subpages
    assert na_find.call_count == 3
    # one .md upload + one append per phase
    assert client.upload_bytes.call_count == 2
    assert client.append_block_children.call_count == 2


def test_push_skips_phase_subpage_already_populated():
    client = MagicMock()
    client.page_has_content.return_value = True   # already populated → skip writes
    na_find = MagicMock(side_effect=[("hw_1", False), ("p_cbp", False)])
    na._push_to_notion(
        client=client, subject_page_id="subj_1", lesson_title="1.1",
        phases=[("Case-Based Preview", "case-based-preview", "# Case")],
        find_or_create=na_find,
    )
    client.append_block_children.assert_not_called()
    client.upload_bytes.assert_not_called()
```

> Keep the existing `test_resolve_subject_page_id_*` and `test_lesson_title_from_section` tests unchanged.

- [ ] **Step 2: Run to verify failure**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_notion_archive.py -q`
Expected: FAIL — `_push_to_notion` has no `phases=` parameter.

- [ ] **Step 3: Implement `_push_to_notion`** — replace the existing function (notion_archive.py:47-72) with:

```python
PHASE_TITLES: dict[str, str] = {
    "case-based-preview": "Case-Based Preview",
    "flashcards": "Flashcards",
    "memory-check": "Memory Check",
    "practice-rlc": "Real-Life Challenge",
    "practice-error-detection": "Error Detection",
    "practice-memory-match": "Memory Matching",
    "practice-tictactoe": "TicTacToe",
    "practice-jigsaw": "Jigsaw Matching",
    "practice-sentence": "Sentence Filling",
    "boss-arena": "Boss Arena",
    "reflection": "Reflection",
}


def _push_to_notion(
    *,
    client: NotionClientWrapper,
    subject_page_id: str,
    lesson_title: str,
    phases: list[tuple[str, str, str]],  # (display_title, phase_name, md)
    find_or_create: Callable = find_or_create,  # injectable for tests
) -> str:
    """Synchronous Notion I/O. Creates the lesson → Homework page, then one
    sub-page per phase (rendered md blocks + that phase's .md attached).
    Idempotent: a phase sub-page that already has content is left untouched.
    Returns the Homework page id."""
    lesson_id, _ = find_or_create(client, subject_page_id, lesson_title)
    homework_id, _ = find_or_create(client, lesson_id, "Homework")

    for display_title, phase_name, md in phases:
        page_id, _ = find_or_create(client, homework_id, display_title)
        if client.page_has_content(page_id):
            log.info("notion: phase page %s (%s) already populated — skipping", page_id, phase_name)
            continue
        body = blocks.markdown_to_notion_blocks(md)
        upload = client.upload_bytes(md.encode("utf-8"), f"{phase_name}.md", "text/markdown")
        body.append(blocks.make_divider())
        body.append(blocks.make_file_upload_block(upload, f"{phase_name}.md"))
        client.append_block_children(page_id, body)
    return homework_id
```

- [ ] **Step 4: Implement `archive_job` phase loading** — in `app/services/notion_archive.py`, replace the block that builds `content_json_bytes` / `assembled_md` (lines ~104-110) and the `asyncio.to_thread` call (lines ~113-121) with phase loading + the new signature:

```python
            section_id = section.id
            lesson_title = _lesson_title(section.section_number, section.section_title)
            ordered = sorted(
                (p for p in await phase_repo.list_for_job(session, job_id)
                 if p.status == "done" and p.phase_name != "extract" and (p.output_md or "").strip()),
                key=lambda p: p.phase_order,
            )
            phases = [
                (PHASE_TITLES.get(p.phase_name, p.phase_name), p.phase_name, p.output_md or "")
                for p in ordered
            ]
        # session closed — do NOT hold a DB connection during the Notion push
        if not phases:
            log.info("notion: job %s has no completed phase outputs — skipping", job_id)
            return

        client = NotionClientWrapper(api_key=settings.notion_api_key)
        homework_id = await asyncio.to_thread(
            _push_to_notion,
            client=client,
            subject_page_id=subject_page_id,
            lesson_title=lesson_title,
            phases=phases,
        )
```

Add the import at the top of the file: `from app.repositories import phase_outputs as phase_repo`. Remove the now-unused `from app.services.job_artifacts import build_content_json` and the `import json` if it is no longer referenced.

- [ ] **Step 5: Run tests**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_notion_archive.py -q`
Expected: all pass (4: 2 resolver/title + 2 new push tests).

- [ ] **Step 6: Commit**

```bash
git add app/services/notion_archive.py tests/services/test_notion_archive.py
git commit -m "feat(notion): archive one sub-page per phase (md blocks + .md attachment)"
```

---

## Task 5: Download = zip of per-phase markdown

**Files:**
- Modify: `app/api/v1/jobs.py` (`download` endpoint; add pure `_phase_zip` helper; drop `structured_artifacts` import + `?format=md`)
- Test: `tests/api/test_phase_zip.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_phase_zip.py
import io
import zipfile
from types import SimpleNamespace

from app.api.v1.jobs import _phase_zip


def test_phase_zip_one_md_per_done_phase():
    phases = [
        SimpleNamespace(phase_order=2, phase_name="flashcards", status="done", output_md="# F"),
        SimpleNamespace(phase_order=1, phase_name="case-based-preview", status="done", output_md="# C"),
        SimpleNamespace(phase_order=3, phase_name="boss-arena", status="failed", output_md=None),
        SimpleNamespace(phase_order=0, phase_name="extract", status="done", output_md="summary"),
    ]
    data = _phase_zip(phases)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(zf.namelist())
    # extract excluded; failed/empty excluded; ordered, zero-padded names
    assert names == ["00-extract.md", "01-case-based-preview.md", "02-flashcards.md"] or \
           names == ["01-case-based-preview.md", "02-flashcards.md"]
```

> Decision for the implementer: **exclude `extract`** from the download (it's an internal lesson summary, not student-facing) — so the asserted set is `["01-case-based-preview.md", "02-flashcards.md"]`. Pick that branch and delete the `or` line.

- [ ] **Step 2: Run to verify failure**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/api/test_phase_zip.py -q`
Expected: FAIL — `ImportError: cannot import name '_phase_zip'`.

- [ ] **Step 3: Implement the helper + rewrite the endpoint**

Add near the top of `app/api/v1/jobs.py` (after imports):

```python
def _phase_zip(phase_outputs) -> bytes:
    """Zip one `<NN>-<phase>.md` per completed, non-extract phase that has md."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(phase_outputs, key=lambda p: p.phase_order):
            if p.phase_name == "extract" or p.status != "done" or not (p.output_md or "").strip():
                continue
            zf.writestr(f"{p.phase_order:02d}-{p.phase_name}.md", p.output_md)
    return buf.getvalue()
```

Replace the whole `download` endpoint (jobs.py:281-316) with:

```python
@router.get("/jobs/{job_id}/download")
async def download(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Download the homework as a zip of one markdown file per phase."""
    job = await jobs_repo.get_with_phases(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "done":
        raise HTTPException(404, "homework not ready")
    data = _phase_zip(job.phase_outputs)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="homework-{job_id}.zip"'},
    )
```

Remove `from app.services.job_artifacts import structured_artifacts` (jobs.py:23) and the now-unused `from fastapi.responses import PlainTextResponse` if `PlainTextResponse` is no longer referenced (grep the file first; keep `StreamingResponse`).

- [ ] **Step 4: Run tests**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/api/test_phase_zip.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/jobs.py tests/api/test_phase_zip.py
git commit -m "feat(download): zip per-phase markdown; drop ?format=md and structured json"
```

---

## Task 6: Frontend — render per-phase markdown + warnings; delete structured views

**Files:**
- Modify: `app/schemas/job.py` (`PhaseOut` + `validation_warnings`)
- Modify: `web/src/lib/types.ts` (`PhaseOut.validation_warnings`)
- Modify: `web/src/routes/preview.tsx` (per-phase md renderer)
- Modify: `web/src/routes/job.tsx` (`DonePanel` counts from phases)
- Delete: `web/src/lib/flow-v2-phases.tsx`, `web/src/components/flow-v2/*.tsx`
- Verify: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`

> No FE test framework in this repo (per CLAUDE.md). The gate is `tsc --noEmit` + `npm run build`, both must exit 0.

- [ ] **Step 1: Expose `validation_warnings` on the API**

In `app/schemas/job.py` `PhaseOut`, after `error_message` (line 19) add:

```python
    validation_warnings: Optional[list[str]] = None
```

(Leave `JobOut.*_json` and `assembled_md` for now — harmless until the teardown task; the FE stops reading them in this task.)

- [ ] **Step 2: Mirror it in the TS types**

In `web/src/lib/types.ts` `PhaseOut` (after `error_message`, line 61) add:

```ts
  validation_warnings: string[] | null;
```

- [ ] **Step 3: Rewrite `preview.tsx` to render per-phase markdown**

Replace the entire file body's render logic so it no longer imports `flow-v2-phases`, `FlowV2Preview`, `LegacyPreview`, or any `components/flow-v2/*`. Keep `MD_COMPONENTS`, `ReactMarkdown`, `remarkGfm`, `rehypeRaw`. New body:

```tsx
const PHASE_TITLES: Record<string, string> = {
  "case-based-preview": "Case-Based Preview",
  flashcards: "Flashcards",
  "memory-check": "Memory Check",
  "practice-rlc": "Real-Life Challenge",
  "practice-error-detection": "Error Detection",
  "practice-memory-match": "Memory Matching",
  "practice-tictactoe": "TicTacToe",
  "practice-jigsaw": "Jigsaw Matching",
  "practice-sentence": "Sentence Filling",
  "boss-arena": "Boss Arena",
  reflection: "Reflection",
};

function PhasesPreview({ job }: { job: Job }) {
  const phases = job.phases
    .filter((p) => p.phase_name !== "extract" && p.status === "done" && p.output_md)
    .sort((a, b) => a.phase_order - b.phase_order);

  return (
    <article className="mt-8 flex flex-col gap-10">
      {phases.map((p) => (
        <section key={p.phase_name}>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold tracking-tight text-(--color-ink)">
              {PHASE_TITLES[p.phase_name] ?? p.phase_name}
            </h2>
            {p.validation_warnings && p.validation_warnings.length > 0 && (
              <span className="rounded-(--radius-xs) bg-(--color-warn-soft,#fef3c7) px-2 py-0.5 text-[0.7rem] font-medium text-(--color-warn,#92400e)">
                ⚠ {p.validation_warnings.length}
              </span>
            )}
          </div>
          {p.validation_warnings && p.validation_warnings.length > 0 && (
            <ul className="mb-3 list-disc pl-5 text-xs text-(--color-ink-muted)">
              {p.validation_warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          <div className="rounded-(--radius-lg) border border-(--color-border) bg-(--color-elevated) p-5 leading-relaxed text-(--color-ink-soft)">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]} components={MD_COMPONENTS}>
              {p.output_md ?? ""}
            </ReactMarkdown>
          </div>
        </section>
      ))}
    </article>
  );
}
```

In `PreviewPage`, change the readiness gate from `!job.assembled_md` to a phases check, and the render call to `<PhasesPreview job={job} />`:

```tsx
  if (error || !job || job.status !== "done") {
    // ...existing "Not ready" block unchanged...
  }
  // ...header unchanged...
  return ( /* ...header... */ <PhasesPreview job={job} /> /* ...*/ );
```

Delete the now-unused imports (`isFlowV2`, `FLOW_V2_PHASES`, `DIVISION_ORDER`, `PhaseBoundary`, `SourceMapView`, `FlashcardDeck`, `BossFight`, `GameCard`, `MemorySprint`, `ReadingExperience`, and the `LegacyPreview`/`FlowV2Preview` functions).

- [ ] **Step 4: Rewrite `DonePanel` counts in `job.tsx`**

Replace the `counts`/`stats` `useMemo` block (job.tsx:287-323) with phase-derived counts:

```tsx
  const stats = useMemo(() => {
    const done = (job?.phases ?? []).filter(
      (p) => p.phase_name !== "extract" && p.status === "done",
    );
    const warnings = done.reduce((n, p) => n + (p.validation_warnings?.length ?? 0), 0);
    return [
      { label: "phases", value: done.length },
      { label: "warnings", value: warnings },
    ].filter((s) => s.value > 0 || s.label === "phases");
  }, [job]);
```

(The `stats.map` render below stays; it already iterates `{label, value}`.)

- [ ] **Step 5: Delete the structured view modules**

```bash
git rm web/src/lib/flow-v2-phases.tsx web/src/components/flow-v2/boss-arena.tsx web/src/components/flow-v2/case-based-preview.tsx web/src/components/flow-v2/cbp-mode-game.tsx web/src/components/flow-v2/memory-check.tsx web/src/components/flow-v2/practice-error-detection.tsx web/src/components/flow-v2/practice-rlc.tsx web/src/components/flow-v2/source-map.tsx
```

> Keep `web/src/components/flow-v2/parts.tsx` and `phase-boundary.tsx` only if still imported elsewhere; grep first (`rg "flow-v2/parts|phase-boundary" web/src`). If nothing imports them, `git rm` them too.

- [ ] **Step 6: Typecheck + build**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: tsc exit 0, build writes `web/dist/`. Fix any dangling imports the compiler flags (unused `*_json` types in `types.ts` can stay until teardown — they don't break the build).

- [ ] **Step 7: Commit**

```bash
git add app/schemas/job.py web/src/lib/types.ts web/src/routes/preview.tsx web/src/routes/job.tsx
git commit -m "feat(web): render per-phase markdown + warnings; remove structured flow-v2 views"
```

---

## Task 7: Pipeline flip — md-only generation, validator, no assembly

**Files:**
- Modify: `app/services/pipeline.py` (`_execute_phase`, the content-phase loop, the job-done assembly block, source-map call)

> **No unit test is feasible here** — `_execute_phase` drives a live CLI subprocess. Per CLAUDE.md, the proof for generation changes is the **real CLI smoke in Task 11**. This task is a structural edit; verify by `import` + full suite staying green (the structured-path tests that no longer apply are removed in Task 10).

- [ ] **Step 1: Route every non-extract phase through the free-text path**

In `_execute_phase` (pipeline.py:974-1010), delete the `elif phase_name in agent.STRUCTURED_PHASE_SCHEMAS:` branch entirely so the structure becomes:

```python
        if phase_name == "extract":
            # ...unchanged extract block...
            parsed_struct = None
        else:
            phase_prompt = get_prompt(subject, phase_name)
            output_md, tin, tout = await agent.run_phase_prompt(
                provider=provider, model=model, phase_prompt=phase_prompt,
                attachments=[pdf_path] if attach_file else [],
                lesson_context=lesson_context or "", prior_outputs=prior_outputs,
                difficulty=difficulty, phase_name=phase_name,
                max_output_tokens=max_output_tokens_for(phase_name),
                homework_job_id=job_id, phase_output_id=po_id,
                source_map_digest="",  # source map dropped — see Task 9/10
            )
            parsed_struct = None
```

- [ ] **Step 2: Validate the markdown and persist warnings**

Immediately before the `set_status(..., "done", ...)` call (pipeline.py:1021-1029), compute warnings for non-extract phases and pass them:

```python
    warnings = (
        phase_validator.validate(phase_name, output_md, subject=subject)
        if phase_name != "extract" else []
    )
    if warnings:
        logger.warning(f"[job {job_id}] {phase_name} validation warnings: {warnings}")
    async with SessionLocal() as session:
        await phase_repo.set_status(
            session, po_id, "done", completed_at=_utcnow(),
            output_md=output_md, tokens_input=tin, tokens_output=tout,
            validation_warnings=warnings or None,
        )
        await session.commit()
```

Add `from app.services import phase_validator` to the imports at the top of `pipeline.py`.

- [ ] **Step 3: Remove the dead structured-output handling in the content loop**

In the content-phase completion loop (pipeline.py:835-856), delete both `if parsed_struct is not None ...` blocks (the source-fidelity `_unknown_concept_ids` warning **and** the `_JSON_COLUMN_SETTERS` write) — `parsed_struct` is now always `None`. Keep `prior_outputs[phase_name] = output_md` (line 829).

- [ ] **Step 4: Stop assembling at job-done**

Replace the assembly block (pipeline.py:583-593) with a plain done-mark (no `assembled_md`):

```python
        # No assembly — per-phase markdown in phase_outputs is the deliverable.
        async with SessionLocal() as session:
            await jobs_repo.set_status(session, job_id, "done", completed_at=_utcnow())
            await session.commit()
```

(`jobs_repo.set_status` already accepts an optional `assembled_md`; simply stop passing it. Leave the function signature alone — it's cleaned in Task 10 only if you choose.)

- [ ] **Step 5: Stop computing the source map**

Find the `extract_source_map` call (grep `extract_source_map` in `pipeline.py`, ~lines 512-535) and remove it along with the `source_map_ids` / `source_map_digest` locals; pass `source_map_digest=""` wherever `_execute_phase` is launched (the scheduler call at pipeline.py:793). The `_unknown_concept_ids` import/helper becomes dead — leave it for Task 10.

- [ ] **Step 6: Sanity-import + full suite**

Run: `& ".\.venv\Scripts\python.exe" -c "import app.services.pipeline"` then `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q`
Expected: import OK. Some structured-path tests may now fail — if they assert `*_json` population or `assembled_md`, they are superseded; note them and delete/adjust in Task 10. Tests unrelated to the structured layer stay green.

- [ ] **Step 7: Commit**

```bash
git add app/services/pipeline.py
git commit -m "feat(pipeline): md-only phases + validator; drop assembly + source map"
```

---

## Task 8: Minimal prompt JSON→md conversion

**Files:**
- Modify: `prompts/_general/case-based-preview.md`, `flashcards.md`, `memory-check.md`, `practice-rlc.md`, `practice-error-detection.md`, `practice-memory-match.md`, `practice-tictactoe.md`, `practice-jigsaw.md`, `practice-sentence.md`, `boss-arena.md` (reflection.md is already markdown — leave it)

> This is the minimal conversion so the md-only pipeline emits clean markdown (the faithful Infra-spec rewrite is Effort B). **No unit test** — verified by the Task 11 smoke. Restart the server to reload prompts (they cache at startup).

- [ ] **Step 1: For each prompt, replace the JSON/schema output instruction with a markdown one**

In each file, find the output-format section (e.g. `case-based-preview.md:75-76` shows a JSON object; `flashcards.md:24,34` references JSON; `practice-error-detection.md:60`, `boss-arena.md:54`, `practice-rlc.md:64` reference inline-SVG-in-JSON). Replace the "respond with JSON / this schema" wording with:

```markdown
## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections described above, in order. For visuals: emit
inline `<svg>` for diagrams; for a photo/raster that you would otherwise need to
generate, emit `![placeholder: <short description> — image gen required](placeholder)`
— never fabricate an image and never invent an image URL.
```

Preserve the existing pedagogical/structural guidance in each prompt (the section list, counts, forbidden rules); only the *output-format* instruction changes. Remove any literal JSON example blocks.

- [ ] **Step 2: Confirm the prompts still load**

Run: `& ".\.venv\Scripts\python.exe" -c "from app.services.prompts import get_prompt; [get_prompt('physics', p) for p in ['case-based-preview','flashcards','memory-check','practice-rlc','practice-error-detection','practice-tictactoe','boss-arena','reflection']]; print('ok')"`
Expected: `ok` (no template/KeyError).

- [ ] **Step 3: Commit**

```bash
git add prompts/_general/
git commit -m "feat(prompts): minimal JSON->markdown output conversion (Effort A)"
```

---

## Task 9: Destructive migration — drop `assembled_md` + `*_json`

**Files:**
- Create: `alembic/versions/0018_drop_structured_columns.py`
- Modify: `app/models/homework_job.py` (remove the 15 dropped columns)
- Test: existing suite must import the model cleanly; migration round-trips.

- [ ] **Step 1: Write the migration**

```python
# alembic/versions/0018_drop_structured_columns.py
"""drop assembled_md + structured *_json columns

Revision ID: e2a5b8c4f1d9
Revises: d1f4a9b3c7e2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2a5b8c4f1d9"
down_revision: Union[str, Sequence[str], None] = "d1f4a9b3c7e2"
branch_labels = None
depends_on = None

_COLS = [
    "assembled_md", "games_json", "flashcards_json", "final_challenge_json",
    "memory_sprint_json", "reading_json", "source_map_json", "boss_arena_json",
    "cbp_json", "memory_check_json", "practice_rlc_json",
    "practice_error_detection_json", "practice_memory_match_json",
    "practice_tictactoe_json", "practice_jigsaw_json", "practice_sentence_json",
]


def upgrade() -> None:
    for c in _COLS:
        op.drop_column("homework_jobs", c)


def downgrade() -> None:
    op.add_column("homework_jobs", sa.Column("assembled_md", sa.Text(), nullable=True))
    for c in _COLS:
        if c == "assembled_md":
            continue
        op.add_column("homework_jobs", sa.Column(c, postgresql.JSONB(astext_type=sa.Text()), nullable=True))
```

- [ ] **Step 2: Remove the columns from the model**

In `app/models/homework_job.py`, delete lines 30-70 (the `assembled_md` field and all `*_json` fields). Keep `started_at`/`completed_at`/`notion_archived_at` and everything below.

- [ ] **Step 3: Apply + round-trip + import**

Run:
```
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
& ".\.venv\Scripts\python.exe" -c "import app.models; print('ok')"
```
Expected: up/down/up clean; model imports.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/0018_drop_structured_columns.py app/models/homework_job.py
git commit -m "feat(db): drop assembled_md + structured *_json columns"
```

---

## Task 10: Teardown — remove dead structured code

**Files (delete or trim once nothing imports them):**
- `app/services/pipeline.py`: `_assemble`, `_render_homework_md`, `_synth_md_for_structured`, `_JSON_COLUMN_SETTERS`, `_unknown_concept_ids`, the `_LEARNING_PHASES`/`_PRACTICE_PHASES` assembly tables.
- `app/services/agent.py`: `STRUCTURED_PHASE_SCHEMAS`, `run_phase_prompt_structured`, `extract_source_map` (+ their `__all__` entries) **iff** unused after the flip.
- `app/services/job_artifacts.py`: `structured_artifacts`, `build_content_json` (delete the module if nothing else remains).
- `app/repositories/jobs.py`: the `set_*_json` setters + the `assembled_md` arg path in `set_status`.
- `app/schemas/job.py`: remove the `*_json` + `assembled_md` fields from `JobOut`.
- `web/src/lib/types.ts`: remove the `*_json` + `assembled_md` fields from `Job` and the now-unused interfaces (`GamesPack`, `FinalChallenge`, `SourceMap`, `CaseBasedPreview`, `CbpModeGame`, etc.) if unreferenced.
- `app/schemas/`: delete phase modules no longer imported (`flow_v2.py`, `games.py`, `final_challenge.py`, `memory_sprint.py`, `reading.py`, `boss_arena.py`, `practice_games.py`, `flashcards.py`, `memory_check.py`) — only those with **no** remaining importers.
- `tests/`: delete/adjust tests asserting `*_json` population, `assembled_md`, `_synth_md_for_structured`, `structured_artifacts`, or `extract_source_map`.

- [ ] **Step 1: Find every importer before deleting**

Run (repeat per symbol): `rg "structured_artifacts|build_content_json|_synth_md_for_structured|_JSON_COLUMN_SETTERS|STRUCTURED_PHASE_SCHEMAS|run_phase_prompt_structured|extract_source_map|assembled_md|set_.*_json|_unknown_concept_ids" app/ tests/ web/src`
Delete each symbol + its importers/tests in one pass so nothing dangles.

- [ ] **Step 2: Backend green**

Run: `& ".\.venv\Scripts\python.exe" -c "import main" && & ".\.venv\Scripts\python.exe" -m pytest tests/ -q`
Expected: app imports; full suite green.

- [ ] **Step 3: Frontend green**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: tsc 0, build OK.

- [ ] **Step 4: Confirm no references remain**

Run: `rg "assembled_md|_json\b|structured_artifacts|STRUCTURED_PHASE_SCHEMAS" app/ web/src | rg -v "validation_warnings|node_modules"`
Expected: only intentional matches (none of the removed symbols).

- [ ] **Step 5: Commit**

```bash
git add -A app/ web/src tests/
git commit -m "refactor: remove dead structured-layer code (synth/assembly/json columns)"
```

> **Scope note:** this is the one task that touches many files; stage deliberately and re-run `git status` before committing (other sessions may share `web/`).

---

## Task 11: Acceptance — real CLI smoke + full green

**No code.** Proof that the flow works end-to-end (per CLAUDE.md acceptance gate). Judge the *flow*, not prose quality (prompts are Effort-A-minimal).

- [ ] **Step 1: Backend + frontend suites green**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q` and `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: all green.

- [ ] **Step 2: Real generation smoke**

Start the server (`uv run uvicorn main:app --host 0.0.0.0 --port 8000`), generate one section on `claude` for a known-good book, and confirm:
- each non-extract phase has a `phase_outputs.output_md` (markdown, not JSON) and a `validation_warnings` value (list or null);
- the preview page renders each phase's markdown with any warnings strip;
- the download `.zip` contains one `<NN>-<phase>.md` per phase;
- with `NOTION_ENABLED=true` + a mapped subject, the Homework page gains **one sub-page per phase** (md blocks + attached `.md`), placeholders rendered as callouts.

- [ ] **Step 3: Worklog**

Add a worklog entry to `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md`; note Effort B (prompt reshape) as the next spec.

---

## Self-review

**Spec coverage:** generation md-only (T7) ✓ · per-phase storage (T1, existing `output_md`) ✓ · validator framework + warn-only + console surfacing (T2, T7, T6) ✓ · Notion sub-page per phase (T4) ✓ · visual placeholder handling — Notion callout (T3) + validator rule (T2) + prompt convention (T8) ✓ · console render (T6) ✓ · download zip (T5) ✓ · drop columns/migration (T9) ✓ · removals (T10) ✓ · sequencing constraints (T1 early; T4/T5/T6 before T7; T9/T10 last) ✓ · `?format=md` dropped (T5) ✓ · Source Map dropped (T7 stops computing, T9/T10 remove) ✓ · acceptance smoke (T11) ✓.

**Placeholder scan:** no TBD/TODO. Live-agent (T7) and FE (T6) tasks have no unit tests **by necessity** (no live-agent harness / no FE test framework) — both are explicitly verified by suite-import + `tsc`/build + the T11 CLI smoke, consistent with CLAUDE.md.

**Type consistency:** `validation_warnings` is `list[str]`/JSONB end-to-end (model T1 ↔ `set_status` T1 ↔ `PhaseOut` T6 ↔ TS `PhaseOut` T6). `_phase_zip` (T5) and `_push_to_notion(phases=...)` (T4) take the same `(order/name/status/output_md)` phase shape. Migration revisions chain: `c9e3f1a07b62 → d1f4a9b3c7e2 (T1) → e2a5b8c4f1d9 (T9)`.
