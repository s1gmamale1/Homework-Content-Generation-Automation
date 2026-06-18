# Plan — Scanned-TOC vision fallback (read a scanned book's contents page via vision)

Branch `feat/scanned-toc-vision` (off `origin/Nggaev-v2` @ `7b6ec26`, which has the PR #23 extract-robustness merge). Backend-only. The upstream sibling of PR #23 (worklog 0070): #23 fixed the *lesson* extract for scanned books; this fixes the *TOC* extract so a fully-scanned book gets past upload at all.

## Approach & key decisions

**Problem (verified by live probe, not theory).** A fully-scanned book (algebra-g9 `a0173601`, 240 pages, ~71 chars/page watermark-only) fails at upload with **0 TOC entries** → `toc_extractor.run` raises → `status="failed"`. But a **real, complete, vision-readable TOC exists** — at the **back** of the book. A claude-cli probe over the last 15 pages transcribed it cleanly (`1-§ Kvadrat funksiyaning ta'rifi — 5`, `2-§ y=x² funksiya — 7`, … with page numbers); the front 12 pages returned "NO TOC HERE". So the book is **usable** — `extract_toc` just never lets a vision model see the contents page.

**Why it fails today (real code, `agent.extract_toc` 1157–1215).** Two coupled reasons:
1. The PDF is attached for **gemini ≤20MB only** (`keep_pdf = provider == "gemini" and pdf_size <= _GEMINI_PDF_MAX_BYTES`). claude/others never get it → they see only the watermark text excerpt.
2. The watermark text is **non-empty**, so `has_local_toc_text = bool(toc_source_text)` is `True` → it's fed to the model as "here is the TOC text", and the prompt then tells the model to return `{"entries": []}` if that text has no readable TOC. The junk text actively steers a `[]`.

**Chosen approach — a `too_sparse` → vision-subset fallback in `extract_toc`, mirroring PR #23.** When the TOC source text is unusable (empty OR `extract_text_is_too_sparse`), **drop the junk text excerpt** and attach a **bounded front+back page-window PDF** (where a "Mundarija" prints — front OR back) so the extract provider's vision OCRs it. The normal text-TOC path (dense excerpt) is **100% unchanged**.

**Key decisions (load-bearing, verified against code):**
1. **Reuse PR #23 building blocks** — `extract_text_is_too_sparse(text, n_pages)` (`agent.py:1052`), `PdfReader/PdfWriter` (top-level since #23), the `_subset_pdf` idiom. Trigger = `not has_local_toc_text or extract_text_is_too_sparse(toc_source_text, toc_source_meta["pages_read"])` (both `pages_read` and `chars` are ints in the meta, verified `agent.py:1127-1128`). For the scanned book: ~1.3K chars ÷ ~27 pages ≈ 48/pg < 300 → fires; a real text TOC is dense → doesn't.
2. **Front + back window, bounded & size-safe** (gatekeeper-approved over auto-widen — YAGNI; textbooks print contents only at front/back, never mid-book). A bounded subset works for **>20MB** scans too (the gemini whole-PDF attach can't). The probe proved the back-window case.
3. **Generous + configurable + asymmetric window via NEW config knobs** (gatekeeper refinement #1) — `extract_toc_front_pages=12`, `extract_toc_back_pages=20`. Back is larger because the back-TOC is the no-front-matter-offset case and a real Mundarija is only 2–4 pages, so 20 is comfortable margin. **NOTE on `_TOC_TAIL_PAGES` (`agent.py:106`, =15):** this is the *pre-existing text tail-scan* constant (NOT from PR #23 — earlier mis-stated). It is **not reused** here precisely because refinement #1 wants the vision window **configurable + asymmetric**, which a hardcoded constant can't be; the remedy for a too-narrow case must be "bump an env var + re-extract", i.e. config not code. The two are deliberately separate concerns (text-scan budget vs vision-window size).
4. **Forced cli is automatic for TOC.** `extract_toc` is called from `toc_extractor` at upload with no job → `transport="cli"` default and the `_auth_env` cli baseline; api never reaches TOC (and couldn't attach anyway). The vision branch sets `transport="cli"` explicitly for clarity, but it's already cli.
5. **Provider** stays `settings.extract_provider` (gemini, vision-capable, reads PDFs natively via cli). No pin change.
6. **Actionable loud-fail** (gatekeeper refinement #2) — when 0 entries still result, `toc_extractor`'s message must name the cause **and** the remedy: scanned/sparse TOC, no contents found in the front+back window → **widen `extract_toc_front_pages`/`extract_toc_back_pages` and re-extract.** This is the cheaper insurance that replaces auto-widen: a one-shot-gate miss becomes a self-diagnosing operator action.

**New config** (`app/config.py`, after the PR #23 `extract_window_*`/`extract_min_chars_per_page` block):
```python
extract_toc_front_pages: int = 12   # vision-TOC: front pages to attach when the text excerpt is too sparse
extract_toc_back_pages: int = 20    # vision-TOC: back pages (a "Mundarija" often prints at the back; larger margin)
```

---

## Task 1 — front+back TOC-window PDF helper + config knobs

**Files:** `app/config.py` (2 knobs); `app/services/agent.py` (new `_toc_source_pdf`). **Test:** `tests/services/test_toc_window.py` (new).

Add the two config fields (above). Add a helper near `_subset_pdf` (~`agent.py:1368`):
```python
def _toc_source_pdf(pdf_path: Path, front_pages: int, back_pages: int) -> Optional[Path]:
    """Write a bounded TOC-search PDF: the first ``front_pages`` + last ``back_pages``
    pages of ``pdf_path`` (deduped, in order) into a temp PDF. Returns its path, or
    ``None`` on any problem (caller falls back / fails loud). Bounded so it works for
    >20MB scans where the whole-PDF attach is rejected."""
```
- `reader = PdfReader(...)`, `n = len(reader.pages)`.
- 0-based index set: `range(0, min(front_pages, n))` ∪ `range(max(0, n - back_pages), n)` → **sorted unique** (handles overlap on small books).
- `PdfWriter`; add those pages in order; empty → `None`; write to `tempfile.mkstemp(suffix=".pdf", prefix="toc_window_")`; exception → warn + `None`.

**TDD (real pypdf PDFs via `PdfWriter().add_blank_page`):**
1. `test_toc_window_front_plus_back` — 40-pg PDF, `_toc_source_pdf(p, 12, 20)` → **32** pages.
2. `test_toc_window_dedupes_on_small_pdf` — 10-pg PDF, `_toc_source_pdf(p, 12, 20)` → **10** (no dup, no error).
3. `test_toc_window_none_on_zero` — `_toc_source_pdf(p, 0, 0)` → `None` (empty writer).
4. `test_toc_config_knobs` — `settings.extract_toc_front_pages == 12`, `extract_toc_back_pages == 20` (add to `test_config_extract_robustness.py`).

**Commit:** `feat(toc): bounded front+back TOC-window PDF helper + config knobs`

---

## Task 2 — route scanned/sparse-text TOCs to a vision-attach branch

**File:** `app/services/agent.py` (`extract_toc`, the 1191–1215 region). **Test:** `tests/services/test_extract_toc_vision.py` (new).

Read `extract_toc` 1157–1226 first. Replace the attachment-decision block. Today:
```python
    lesson_context = None
    attachment_preamble = prov.format_attachments([pdf_path])
    attachments = [pdf_path]
    if has_local_toc_text:
        lesson_context = ("Locally extracted text ... " f"{toc_source_text}")
    # R6 gemini keep_pdf / not keep_pdf → attachments = [] ...
```
New shape (insert the usability decision; keep the existing text path as the `else`):
```python
    toc_text_usable = has_local_toc_text and not extract_text_is_too_sparse(
        toc_source_text, toc_source_meta.get("pages_read", 0)
    )
    if not toc_text_usable:
        # Scanned / sparse-text TOC: the local excerpt is watermark junk that would
        # steer the model to return []. Drop it and vision-attach a bounded front+back
        # page-window so the provider OCRs the printed contents page (front OR back).
        window = _toc_source_pdf(
            pdf_path, settings.extract_toc_front_pages, settings.extract_toc_back_pages
        )
        if window is None:
            # cannot build a window → fall back to today's behavior (text/none),
            # which will 0-entry → loud actionable fail in toc_extractor.
            lesson_context = None
            attachment_preamble, attachments = "", []
        else:
            logger.info(
                f"agent.toc | sparse/scanned text ({toc_source_meta.get('chars')} chars / "
                f"{toc_source_meta.get('pages_read')} pages) → vision-attach front "
                f"{settings.extract_toc_front_pages} + back {settings.extract_toc_back_pages} pages"
            )
            lesson_context = None
            attachment_preamble = prov.format_attachments([window])
            attachments = [window]
            transport = "cli"   # vision needs attachments; api is text-only
    else:
        # ── existing text-TOC path, UNCHANGED ──
        lesson_context = ("Locally extracted text ... " f"{toc_source_text}")
        attachment_preamble = prov.format_attachments([pdf_path])
        attachments = [pdf_path]
        try: pdf_size = pdf_path.stat().st_size
        except OSError: pdf_size = _GEMINI_PDF_MAX_BYTES + 1
        keep_pdf = provider == "gemini" and pdf_size <= _GEMINI_PDF_MAX_BYTES
        if not keep_pdf:
            attachment_preamble, attachments = "", []
```
- **Temp cleanup:** the `window` temp PDF must be `unlink()`ed in a `finally` after the attempt loop (mirror `summarize_lesson_vision`/`extract_lesson_context`; never unlink `pdf_path`). Track it in a local and clean at function end.
- `source` in `usage_extra` (line 1267): `"vision_toc"` when the window branch ran, else existing `"local_pdf_text"`/`"attachment"`.
- Do **not** touch the prompt, the schema (`ExtractedTOC`), or the 2-attempt parse loop.

**TDD (monkeypatch `agent._spawn` async to capture kwargs + return a valid `ExtractedTOC` JSON; monkeypatch `_extract_toc_source_text` to control the text; build a real PDF so `_toc_source_pdf` makes a temp):**
1. `test_sparse_text_routes_to_vision_window` — `_extract_toc_source_text` → `("@WM "*30, {"pages_read":27,"chars":120})` (sparse) → assert `_spawn` got `attachments=[<a real toc_window_*.pdf>]`, the prompt has **no** watermark excerpt in `lesson_context`, and the temp is unlinked after.
2. `test_dense_text_keeps_text_path` — dense TOC text → assert the existing path (lesson_context carries the excerpt; gemini keep_pdf attaches `pdf_path`, not a window).
3. `test_window_none_falls_back_clean` — monkeypatch `_toc_source_pdf` → `None` → `attachments == []`, no crash.
4. `test_vision_branch_marks_source` — sparse path records `source="vision_toc"` in the usage extra.

**Commit:** `feat(toc): vision-attach front+back window when TOC text is scanned/sparse`

---

## Task 3 — actionable loud-fail on 0 entries

**File:** `app/services/toc_extractor.py` (the `if not extracted.entries: raise` at 70–74). **Test:** `tests/services/test_toc_extractor.py` (extend).

Replace the message so it names the cause **and** the remedy:
```python
        if not extracted.entries:
            raise RuntimeError(
                "TOC extraction found 0 lessons. Likely a scanned/image-only PDF whose "
                "contents page is outside the scanned vision window, or an unparseable "
                "table of contents. If the book is scanned, widen "
                "extract_toc_front_pages / extract_toc_back_pages and re-extract."
            )
```

**TDD:** extend the existing 0-entry test to assert the message contains `"extract_toc_front_pages"` and `"re-extract"` (so the operator-action signal is locked).

**Commit:** `feat(toc): actionable 0-entry failure message (name cause + widen remedy)`

---

## Task 4 — real-CLI acceptance + finish

**Acceptance gate (real CLI, in-process, no server, `$0`, zero DB writes — stub `_record_usage`):** call `agent.extract_toc` against the real scanned book `a0173601` (verify it's still in the DB/on disk at smoke time; else any scanned book) and prove it now returns **real entries** read from the back-of-book contents via vision. Because gemini-cli OAuth has been unavailable on this Mac (gemini runs via api here, which can't attach), run the smoke with **`provider="claude"`** — the probe already showed claude reads this book's back-TOC cleanly; the path is provider-agnostic, production pins gemini. Capture the entry count + a few titles/pages. **Fact over theory — actually run it.**

**Finish (same commit set, do not defer):**
- **Rebase** onto the current `origin/Nggaev-v2` tip first (it moved past `7b6ec26` during dev).
- Full suite: `uv run python -m pytest tests/ -q` (green; the 5 notion-router tests need `NOTION_API_KEY` in a bare worktree — env artifact, not a regression).
- Worklog **0071** (verify it's the next free number at finish — base has moved; #23 took 0070, later PRs took more) in `docs/memory/MASTER_MEMORY.md` + `docs/memory/INDEX.md` row.
- **Close WISHLIST line 79** (the scanned-TOC half) — move to the worklog; it's the last open piece of the scanned-book story.
- `git mv docs/superpowers/plans/2026-06-17-scanned-toc-vision.md docs/superpowers/plans/shipped/`.
- De-stale `docs/HOW_IT_WORKS.md` (the "fully scanned book whose TOC itself is an image can still come back empty" line — now handled) + `docs/CODE_MAP.md` (`extract_toc` note).
- `finishing-a-development-branch` → open PR to `Nggaev-v2`.

**Commit:** `docs(memory): worklog 0071 — scanned-TOC vision fallback; ship plan`

---

## Self-review

- **Coverage:** new helper, the vision-branch dispatch (both arms), and the fail message each have failing-first tests; acceptance proves real vision OCR of the back-TOC.
- **Type consistency:** `_toc_source_pdf` returns `Optional[Path]` like `_subset_pdf`; config knobs are `int`; `extract_toc` still returns `ExtractedTOC`.
- **Scope discipline:** stage only each task's files; never `git add -A`. The dense-text TOC path is untouched (asserted by `test_dense_text_keeps_text_path`); the gemini keep_pdf augment for normal books stays.
- **No api regression:** TOC is always cli (no job); the vision branch only ever runs at upload. The forced `transport="cli"` is belt-and-suspenders.
- **Honest scope:** this closes the scanned-**TOC** half (the last open piece). A scanned book whose contents page falls outside a 12+20 window now fails *actionably* (widen the knobs) rather than silently — the deliberate YAGNI boundary.
