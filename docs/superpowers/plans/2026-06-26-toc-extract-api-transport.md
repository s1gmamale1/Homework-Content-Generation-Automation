# TOC extraction over Vertex API (no gemini-CLI OAuth) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an all-Vertex operator extract a textbook TOC over `transport=api` (Vertex SDK) instead of the gemini CLI, so text-PDF uploads stop failing with `FatalCancellationError: Authentication cancelled by user (initOauthClient)`.

**Architecture:** Add an `EXTRACT_TOC_TRANSPORT` setting (`cli` default), thread it into `toc_extractor.run → agent.extract_toc`, and in `extract_toc`'s text-usable branch drop the PDF attachment when transport is `api` (the SDK path is text-only and the local TOC text already rides in as `lesson_context`). The scanned/sparse vision branch keeps its unconditional `cli` override. No DB migration.

**Tech Stack:** FastAPI, pydantic-settings, pytest / pytest-asyncio, google-genai (Vertex), the existing `app/services/api_transport.py` SDK path.

---

## Approach & key decisions

- **Chosen approach:** mirror the proven `summarize_lesson` local-text→api pattern. The text path already exists — `extract_toc` reads front/back page text locally into `lesson_context` (`agent.py:1378-1396`) and already drops the attachment for non-gemini / oversize. It just never ran over `transport="api"`. Route the text-usable TOC through Vertex by (a) a new transport setting and (b) dropping the attachment for `api`.
- **Why dropping the attachment is required, not cosmetic:** `api_transport.generate` raises `NotImplementedError("api transport is text-only in v1")` for any non-empty `attachments` (`api_transport.py:33-34`). So for a gemini text-usable book (`keep_pdf=True` today → `attachments=[pdf]`) an api call would crash. The minimal fix is to AND `transport != "api"` into the existing `keep_pdf` condition; the existing `if not keep_pdf` block then zeroes `attachments` and `attachment_preamble`, and `lesson_context` (the local TOC text) is unaffected.
- **Rejected — a separate api code path in `extract_toc`:** unnecessary. `_spawn(..., transport=...)` already routes `api` → `api_transport.generate` for gemini/claude (`agent.py:486-491`); we only need to feed it `transport` and an empty attachment list.
- **Vision/scanned stays cli:** the vision branch sets `transport = "cli"` unconditionally (`agent.py:1376`) because OCR needs the attachment and api is text-only. A scanned book launched with `EXTRACT_TOC_TRANSPORT=api` must therefore still fall back to cli — this override is load-bearing and is asserted by a test. Scanned-over-api closure is out of scope (would need an api image path).
- **Default `cli` (backward-compat):** existing CLI fleets are unaffected; only the all-Vertex operator sets `EXTRACT_TOC_TRANSPORT=api`. Mirror the `_blank_extract_provider_to_default` validator (`config.py:197-211`) and additionally reject any value other than `cli`/`api` loudly (a typo'd value silently running cli is the exact footgun this operator would hit).
- **Verified facts (tip `d4271eb`):** `extract_toc(... transport: str = "cli")` `agent.py:1304`; text-usable branch + `keep_pdf` `agent.py:1378-1396`; vision forces cli `:1376`; dispatch `await _spawn(... transport=transport)` `:1431-1437`; `_spawn` api route `:486-491`; api text-only guard `api_transport.py:33-34`; `toc_extractor.py:53` calls `extract_toc` with no `transport`. No migration; touches `config.py` + `toc_extractor.py` + `agent.py` (+ tests).

---

### Task 1: `EXTRACT_TOC_TRANSPORT` setting + validator

**Files:**
- Modify: `app/config.py:194-211` (add field + validator next to `extract_provider`/`extract_model`)
- Test: `tests/test_config_extract_toc_transport.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_extract_toc_transport.py`:

```python
"""EXTRACT_TOC_TRANSPORT: default cli, blank→cli, cli|api only (loud on junk)."""
import pytest

from app.config import Settings


def test_default_is_cli():
    s = Settings(_env_file=None)
    assert s.extract_toc_transport == "cli"


def test_blank_normalises_to_cli():
    s = Settings(_env_file=None, extract_toc_transport="  ")
    assert s.extract_toc_transport == "cli"


def test_api_is_accepted():
    s = Settings(_env_file=None, extract_toc_transport="api")
    assert s.extract_toc_transport == "api"


def test_invalid_value_raises():
    with pytest.raises(ValueError):
        Settings(_env_file=None, extract_toc_transport="apii")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run python -m pytest tests/test_config_extract_toc_transport.py -q`
Expected: FAIL — `AttributeError`/`ValidationError` (field doesn't exist yet).

- [ ] **Step 3: Add the field + validator**

In `app/config.py`, immediately after the `_blank_extract_provider_to_default` validator (ends line 211), add:

```python
    extract_toc_transport: str = "cli"

    @field_validator("extract_toc_transport", mode="before")
    @classmethod
    def _blank_toc_transport_to_default(cls, v: object) -> object:
        """Normalise EXTRACT_TOC_TRANSPORT: blank/whitespace → "cli" (a bare
        ``EXTRACT_TOC_TRANSPORT=`` in .env passes "" not the field default), and
        reject anything other than cli|api LOUDLY — a typo'd value silently
        running cli is exactly the footgun the all-Vertex operator would hit.
        Default cli keeps existing CLI fleets unchanged; only an all-Vertex
        operator (no gemini OAuth) sets api to route TOC extraction over Vertex."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return "cli"
        if isinstance(v, str) and v.strip() in ("cli", "api"):
            return v.strip()
        raise ValueError(f"EXTRACT_TOC_TRANSPORT must be 'cli' or 'api', got {v!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_config_extract_toc_transport.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config_extract_toc_transport.py
git commit -m "extract-toc-api: add EXTRACT_TOC_TRANSPORT setting (cli default)"
```

---

### Task 2: drop the PDF attachment for `api` in the text-usable branch

**Files:**
- Modify: `app/services/agent.py:1393` (the `keep_pdf` line)
- Test: `tests/services/test_extract_toc_api.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/services/test_extract_toc_api.py`:

```python
"""extract_toc over transport='api' (Vertex SDK, text-only):
- text-usable book → PDF attachment dropped, transport threaded as 'api'
- scanned/sparse book → still vision + the unconditional cli override holds
(api_transport.generate raises NotImplementedError on any attachment, so an
api text-usable call MUST send attachments=[].)"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from app.services import agent

_SPAWN_OK = (
    0,
    '{"entries": [{"chapter_number": "I", "chapter_title": "C", '
    '"section_number": "1", "section_title": "T", '
    '"page_start": 5, "page_end": 7}]}',
    {"prompt_tokens": 1, "output_tokens": 1, "cached_tokens": 0,
     "total_tokens": 2, "raw": {}},
    "",
)


def _make_pdf(tmp_path: Path, n_pages: int = 40) -> Path:
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    out = tmp_path / "book.pdf"
    with out.open("wb") as fh:
        writer.write(fh)
    return out


def _patch(monkeypatch, captured: dict):
    async def _fake_spawn(*, provider, model, prompt, attachments, transport):
        captured["attachments"] = list(attachments)
        captured["transport"] = transport
        captured["prompt"] = prompt
        return _SPAWN_OK

    async def _fake_record(*args, **kwargs):
        pass

    monkeypatch.setattr(agent, "_spawn", _fake_spawn)
    monkeypatch.setattr(agent, "_record_usage", _fake_record)


@pytest.mark.asyncio
async def test_api_text_usable_drops_pdf_attachment(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    dense = "1-§ Kirish — 5\n2-§ Boshqa mavzu — 9\n" * 250  # not sparse
    monkeypatch.setattr(
        agent, "_extract_toc_source_text",
        lambda p: (dense, {"pages_read": 20, "chars": len(dense)}),
    )
    captured: dict = {}
    _patch(monkeypatch, captured)

    toc = await agent.extract_toc(
        provider="gemini", model="gemini-2.5-flash", pdf_path=pdf,
        subject="math", book_id=uuid4(), transport="api",
    )

    assert len(toc.entries) == 1
    # api is text-only: the PDF must NOT be attached (else api_transport raises)
    assert captured["attachments"] == []
    # transport threaded through to the spawn
    assert captured["transport"] == "api"
    # the local TOC text still rides in as lesson_context
    assert "1-§ Kirish — 5" in captured["prompt"]


@pytest.mark.asyncio
async def test_cli_text_usable_still_attaches_pdf(tmp_path, monkeypatch):
    """Regression guard: the cli path is unchanged — gemini keep_pdf still attaches."""
    pdf = _make_pdf(tmp_path)
    dense = "1-§ Kirish — 5\n2-§ Boshqa mavzu — 9\n" * 250
    monkeypatch.setattr(
        agent, "_extract_toc_source_text",
        lambda p: (dense, {"pages_read": 20, "chars": len(dense)}),
    )
    captured: dict = {}
    _patch(monkeypatch, captured)

    await agent.extract_toc(
        provider="gemini", model="gemini-2.5-flash", pdf_path=pdf,
        subject="math", book_id=uuid4(), transport="cli",
    )

    assert captured["attachments"] == [pdf]
    assert captured["transport"] == "cli"


@pytest.mark.asyncio
async def test_api_scanned_book_overrides_back_to_cli(tmp_path, monkeypatch):
    """A scanned/sparse book launched api must still vision-OCR via cli — the
    unconditional cli override in the vision branch holds even when api is asked."""
    pdf = _make_pdf(tmp_path)
    monkeypatch.setattr(
        agent, "_extract_toc_source_text",
        lambda p: ("@WM " * 30, {"pages_read": 27, "chars": 120}),  # sparse junk
    )
    captured: dict = {}
    _patch(monkeypatch, captured)

    await agent.extract_toc(
        provider="gemini", model="gemini-2.5-flash", pdf_path=pdf,
        subject="math", book_id=uuid4(), transport="api",
    )

    # vision attaches a bounded window AND forces cli despite the api request
    assert len(captured["attachments"]) == 1
    assert Path(captured["attachments"][0]).name.startswith("toc_window_")
    assert captured["transport"] == "cli"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_extract_toc_api.py -q`
Expected: `test_api_text_usable_drops_pdf_attachment` FAILS (`attachments == [pdf]`, not `[]`). The other two PASS already (they assert current behavior) — that's fine; the first test is the RED proof.

- [ ] **Step 3: Make the change**

In `app/services/agent.py`, the text-usable branch builds `keep_pdf` at line 1393:

```python
        keep_pdf = provider == "gemini" and pdf_size <= _GEMINI_PDF_MAX_BYTES
```

Replace with:

```python
        # api transport is text-only (api_transport raises on any attachment):
        # drop the PDF and rely on the local TOC text in lesson_context.
        keep_pdf = (
            transport != "api"
            and provider == "gemini"
            and pdf_size <= _GEMINI_PDF_MAX_BYTES
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run python -m pytest tests/services/test_extract_toc_api.py tests/services/test_extract_toc_vision.py -q`
Expected: PASS (all — new file 3/3 and the existing vision suite still green).

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_extract_toc_api.py
git commit -m "extract-toc-api: drop PDF attachment for api text-usable TOC (text-only SDK)"
```

---

### Task 3: thread the setting through `toc_extractor.run`

**Files:**
- Modify: `app/services/toc_extractor.py:53-59` (pass `transport=`)
- Test: `tests/services/test_toc_extractor.py` (add one test)

- [ ] **Step 1: Write the failing test**

Append to `tests/services/test_toc_extractor.py`:

```python
@pytest.mark.asyncio
async def test_passes_extract_toc_transport_setting(monkeypatch):
    """toc_extractor.run must forward settings.extract_toc_transport into
    agent.extract_toc so an all-Vertex operator's api choice actually reaches
    the spawn (else the CLI OAuth path is always used)."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(toc_extractor.settings, "extract_toc_transport", "api")
    seen: dict = {}

    async def fake_extract_toc(**kw):
        seen.update(kw)
        return SimpleNamespace(entries=[SimpleNamespace(section_title="L1")])

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", fake_extract_toc)
    monkeypatch.setattr(
        toc_extractor.TOCEntryOut, "model_validate",
        classmethod(lambda cls, r: SimpleNamespace(model_dump=lambda mode=None: {})),
    )

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    assert seen.get("transport") == "api"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_toc_extractor.py::test_passes_extract_toc_transport_setting -q`
Expected: FAIL — `seen.get("transport")` is `None` (run doesn't pass `transport`).

- [ ] **Step 3: Make the change**

In `app/services/toc_extractor.py`, the `agent.extract_toc(...)` call (lines 53-59) — add the `transport` kwarg:

```python
        extracted = await agent.extract_toc(
            provider=settings.extract_provider,
            model=settings.extract_model,
            pdf_path=file_path,
            subject=subject,
            book_id=book_id,
            transport=settings.extract_toc_transport,
        )
```

Also extend the existing log line (`toc_extractor.py:48-51`) to surface the chosen transport:

```python
        log.info(
            f"[book {book_id}] extracting TOC via agent "
            f"({settings.extract_provider} / {settings.extract_model}) "
            f"transport={settings.extract_toc_transport}"
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_toc_extractor.py -q`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add app/services/toc_extractor.py tests/services/test_toc_extractor.py
git commit -m "extract-toc-api: thread EXTRACT_TOC_TRANSPORT into toc_extractor.run"
```

---

### Task 4: acceptance smoke (real Vertex) + Finish

**Files:**
- Create: `scripts/smoke_toc_api.py`
- Modify: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `README.md`, `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` (de-stale the TOC-extraction + transport descriptions), `.env.example` (if present — document `EXTRACT_TOC_TRANSPORT`)
- Move: this plan → `docs/superpowers/plans/shipped/`

- [ ] **Step 1: Write the acceptance smoke**

Create `scripts/smoke_toc_api.py` — re-extract the previously-failing text book over Vertex, asserting a valid TOC with NO gemini-CLI spawn / NO OAuth. The bug IS the OAuth call, so the binding proof is its absence.

```python
"""Acceptance smoke: TOC extraction over transport='api' (Vertex), no gemini CLI.
Run with Vertex creds in env (GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT):
    EXTRACT_TOC_TRANSPORT=api uv run python -m scripts.smoke_toc_api <book_id>
Re-extracts the on-disk PDF for <book_id> straight through agent.extract_toc.
Pass = a non-empty ExtractedTOC came back via the api path (auth_mode=api) with
no initOauthClient. One real call (a single TOC read) — within the no-mass-gen rule."""
import asyncio
import sys
from uuid import UUID

from app.config import settings
from app.services import agent
from app.services.storage import book_pdf_path


async def _main(book_id: str):
    assert settings.extract_toc_transport == "api", (
        "set EXTRACT_TOC_TRANSPORT=api for this smoke")
    pdf = book_pdf_path(UUID(book_id))
    assert pdf.exists(), f"no PDF on disk at {pdf}"
    toc = await agent.extract_toc(
        provider=settings.extract_provider,
        model=settings.extract_model,
        pdf_path=pdf,
        subject="smoke",
        book_id=UUID(book_id),
        transport="api",
    )
    assert toc.entries, "api TOC extraction returned 0 entries"
    print(f"SMOKE PASS: {len(toc.entries)} entries via transport=api (Vertex). "
          f"First: {toc.entries[0].section_title!r}")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1]))
```

- [ ] **Step 2: Run the acceptance smoke (real, required — auth/generation-affecting)**

The controller runs this against the failing text book (`860e86aa-…`) with Vertex creds and `EXTRACT_TOC_TRANSPORT=api`:

Run: `EXTRACT_TOC_TRANSPORT=api uv run python -m scripts.smoke_toc_api 860e86aa-...`
Expected: `SMOKE PASS: <n> entries via transport=api (Vertex).`
Then grep the run log to confirm the bug is gone: NO `initOauthClient`, and the `agent_usages` row for this `toc.extract` has `auth_mode='api'`.
If the exact book id is unavailable, run against any on-disk text PDF book id — the binding assertion is "api path returns a TOC with no OAuth," which any text book proves.

- [ ] **Step 3: Run the full suite**

Run: `cd /Users/macmini5/Documents/HCGA-toc-api && uv sync --extra dev && uv run python -m pytest tests/ -q`
Expected: green (the worktree venv may need `uv sync --extra dev` first).

- [ ] **Step 4: Rebase-check before finishing**

```bash
git fetch origin
git log HEAD..origin/Nggaev-v2 --oneline   # if non-empty, rebase onto origin/Nggaev-v2 and re-run the suite
```

- [ ] **Step 5: Finish — worklog + INDEX + plan move + de-stale docs**

- Add a worklog entry to `docs/memory/MASTER_MEMORY.md` (verify next-free number at finish — `0090` is the current highest, so likely `0091`) + a row in `docs/memory/INDEX.md`.
- De-stale the live-system reference docs that describe TOC extraction / transport: the PDF-handling caveat about gemini OAuth and the "Transport toggle" / extract-pin sections in `README.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`. Document `EXTRACT_TOC_TRANSPORT` (cli default, api for all-Vertex operators; scanned books still need cli) in `.env.example` if that file exists.
- `git mv docs/superpowers/plans/2026-06-26-toc-extract-api-transport.md docs/superpowers/plans/shipped/`
- Commit each with staged files only (never `git add -A`).

- [ ] **Step 6: Open the PR to the gatekeeper (no self-merge)**

Push `extract-toc-api` and open a PR targeting `Nggaev-v2`; route it back to the gate for review + merge.
