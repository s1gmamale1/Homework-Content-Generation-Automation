# Plan — gemini vision over Vertex API (scanned-book TOC + lesson-extract) — `api-vision-1`

**Goal:** let scanned / image-only books extract over **Vertex API** instead of force-downgrading to the gemini **CLI** (which hits `initOauthClient` and fails on an all-Vertex machine). Today TOC + lesson-extract for scanned books fall into the **vision** branch, which hardcodes `transport="cli"`. This closes the gap the `EXTRACT_TOC_TRANSPORT=api` fix left open (it only covered text books).

**Branch:** `api-vision-1` (worktree `../HCGA-api-vision`, cut off `origin/Nggaev-v2` @ `5c4d448`). **Worklog:** **0094** (0093 is the last block — verify at finish). **Backend, no migration.** Commit prefix `api-vision:`.

## Approach & key decisions

- **The only real blocker is `api_transport` rejecting attachments.** Everything downstream already carries them: `_spawn → _spawn_once` passes `attachments=attachments` straight into `api_transport.generate(...)` (`agent.py:489-491`), and `_auth_env` grants the Vertex SA creds at that same spawn (`agent.py:496`). So a `transport="api"` spawn **with** a window PDF already reaches `api_transport.generate` today — and dies at its `NotImplementedError("api transport is text-only in v1")` (`api_transport.py:33-34`). Fix = teach the **gemini** SDK path to build multimodal `Part`s, then stop the two vision call sites from force-downgrading **gemini+api** to cli. **No new env plumbing, no hand-built env** — just let `transport="api"` flow.
- **gemini only; claude vision stays cli (out of scope).** Keep the attachment guard, but move it so it blocks **only** claude. `claude-api` vision is a separate effort.
- **SDK shape VERIFIED against the installed `google-genai 2.8.0`** (not assumed): `types.Part.from_bytes(*, data: bytes, mime_type: str)` builds an inline-data Part; `client.aio.models.generate_content(model=model, contents=[prompt, *parts])` is the multimodal call. mime by file suffix (`.pdf`→`application/pdf`, `.png`→`image/png`, `.jpg/.jpeg`→`image/jpeg`), default `application/pdf` — **windows are always PDF subsets today** (`_toc_source_pdf`/`_subset_pdf`), so PDF is the live case; suffix detection is defensive per the spec gotcha.
- **Scope is the VISION branches only.** The `extract_toc` **text** branch's `keep_pdf` (drops the PDF under api, relies on the local TOC text — `agent.py:1395-1402`) is **left unchanged**: text books extract correctly from local text, and attaching a ≤20 MB PDF inline under api would be a needless token-cost regression. Inline size is a non-issue for vision because the window is a small front+back (TOC) / page-window (lesson) subset, well under Vertex inline limits.
- **`auth_mode` must follow the real transport.** `summarize_lesson_vision` hardcodes `auth_mode="cli"` on its usage row (`agent.py:2014`); once gemini+api vision is real, the row must record `auth_mode="api"` or the `$`/attribution is wrong. Same for the `transport` passed to `_spawn` (`:1993`).
- **Verified facts (tip `5c4d448`):** `api_transport.generate` raises on attachments `api_transport.py:33-34`; `_gemini(model, prompt)` `:81-98`; `_gemini_client()` Vertex-SA branch `:43-56`. `_spawn_once` api branch forwards attachments `agent.py:486-491`. `extract_toc(*, provider, model, pdf_path, subject, book_id, transport="cli")` `:1297`; vision branch forces `transport="cli"` at `:1376`. `summarize_lesson_vision(*, provider, model, pdf_path, …)` has **no** `transport` param, hardcodes `transport="cli"` at `:1993` + `auth_mode="cli"` at `:2014`; its sole caller is `pipeline.py:925` where `extract_provider`/`extract_model`/`extract_transport` are in scope (the api-force warning is `pipeline.py:921-924`).

---

## Task 1 — `api_transport`: multimodal `Part`s for gemini (TDD, mock SDK)

**RED** — add to `tests/services/test_api_transport.py` (create if absent):
- `test_generate_gemini_accepts_attachments`: monkeypatch `api_transport._gemini_client` to a stub whose `aio.models.generate_content` is an async mock capturing `contents`; call `await generate(provider="gemini", model="gemini-2.5-flash", prompt="hi", attachments=[tmp_pdf])` where `tmp_pdf` is a `tmp_path` file with `b"%PDF-1.4 x"`; assert it does NOT raise, and `contents` is `["hi", <Part>]` with the Part's `inline_data.mime_type == "application/pdf"` and the file bytes.
- `test_generate_gemini_no_attachments_unchanged`: `attachments=[]` → `contents` is the bare `"hi"` string (no list-wrapping), result unchanged.
- `test_generate_claude_still_rejects_attachments`: `generate(provider="claude", model="claude-…", prompt="hi", attachments=[tmp_pdf])` still raises `NotImplementedError`.
- `test_mime_for_suffix`: `_mime_for` → `application/pdf` for `.pdf`, `image/png` for `.png`, `image/jpeg` for `.jpg`/`.jpeg`, `application/pdf` default.

**GREEN** — in `app/services/api_transport.py`:
- Reorder `generate` so the attachment guard blocks only claude:
  ```python
  if not model:
      raise ValueError(f"{provider} api requires an explicit model")
  if provider == "gemini":
      return await _gemini(model, prompt, attachments)
  if attachments:
      raise NotImplementedError("api transport is text-only for claude in v1")
  if provider == "claude":
      return await _claude(model, prompt)
  raise ValueError(f"api transport not supported for provider {provider!r}")
  ```
- Add the mime helper:
  ```python
  def _mime_for(path: Path) -> str:
      ext = path.suffix.lower()
      if ext in (".jpg", ".jpeg"):
          return "image/jpeg"
      if ext == ".png":
          return "image/png"
      return "application/pdf"   # the only current case (window subsets are PDFs)
  ```
- Make `_gemini` multimodal (default keeps text-only behavior):
  ```python
  async def _gemini(model: str, prompt: str, attachments: "list[Path] | tuple" = ()) -> tuple[int, str, dict, str]:
      client = _gemini_client()
      contents = prompt
      if attachments:
          from google.genai import types
          parts = [types.Part.from_bytes(data=Path(a).read_bytes(), mime_type=_mime_for(Path(a)))
                   for a in attachments]
          contents = [prompt, *parts]
      try:
          resp = await client.aio.models.generate_content(model=model, contents=contents)
      except Exception as exc:  # noqa: BLE001
          return 1, "", dict(_EMPTY_USAGE), str(exc)
      # ... usage/finish/text parsing UNCHANGED ...
  ```
  Update the module docstring (`:5`) from "Text-only in v1." → "Text + gemini multimodal (PDF/image attachments via Vertex); claude stays text-only."

**Commands:** `uv run python -m pytest tests/services/test_api_transport.py -q`
**Commit:** `api-vision: gemini api transport accepts PDF/image attachments (multimodal Parts)`

---

## Task 2 — `extract_toc` vision branch: keep api for gemini (TDD)

**RED** — in `tests/services/` (extend the agent/toc test module): drive `extract_toc` into the vision branch and assert the transport it spawns with.
- Monkeypatch `agent._extract_toc_source_text` → returns sparse text so `toc_text_usable` is False (vision branch); `agent._toc_source_pdf` → a fake window Path; `agent._spawn` → an async mock capturing the `transport` kwarg and returning a valid `ExtractedTOC` JSON tuple; `agent._record_usage`/`record_*` as needed.
- `test_extract_toc_vision_keeps_api_for_gemini`: call with `provider="gemini", transport="api"` → captured spawn `transport == "api"` and `attachments == [window]`.
- `test_extract_toc_vision_forces_cli_for_claude`: `provider="claude", transport="api"` → spawn `transport == "cli"`.
- `test_extract_toc_vision_forces_cli_when_cli`: `provider="gemini", transport="cli"` → spawn `transport == "cli"`.

**GREEN** — in `app/services/agent.py`, the vision branch (`:1376`):
```python
            attachment_preamble = prov.format_attachments([window])
            attachments = [window]
            if not (transport == "api" and provider == "gemini"):
                transport = "cli"   # vision needs attachments; api PDF-attach only for gemini
            toc_mode = "vision_toc"
```
(The text branch's `keep_pdf` at `:1395-1402` is intentionally untouched.)

**Commands:** `uv run python -m pytest tests/services/test_agent.py -q` (or the toc test module)
**Commit:** `api-vision: extract_toc vision uses Vertex api for gemini (not forced cli)`

---

## Task 3 — `summarize_lesson_vision` + pipeline: api for gemini scanned lesson-extract (TDD)

**RED** —
- In the agent test module: `summarize_lesson_vision` gains `transport: str = "cli"`. Monkeypatch `agent._spawn` (capture `transport`) + `agent._record_usage` (capture `auth_mode`) + `agent._subset_pdf` (fake window). `test_vision_spawns_with_given_transport`: call with `transport="api"` → spawn `transport=="api"` AND recorded `auth_mode=="api"`; default (`"cli"`) → both `"cli"`.
- In `tests/services/test_pipeline*.py` (or a focused test): assert the scanned-lesson branch passes `transport="api"` to `summarize_lesson_vision` when `extract_provider=="gemini"` and `extract_transport=="api"`, and `"cli"` (with the warning) when `extract_provider=="claude"` and `extract_transport=="api"`.

**GREEN** —
- `app/services/agent.py` `summarize_lesson_vision`: add `transport: str = "cli"` to the signature; use it at the `_spawn(..., transport=transport)` call (`:1993`) and `auth_mode=transport` on the `_record_usage` (`:2014`); update the docstring (`:1945-1947`) — vision is cli **except gemini+api**, which attaches the window over Vertex.
- `app/services/pipeline.py` (`:921-929`): replace the unconditional api→cli force:
  ```python
  vision_transport = "api" if (extract_provider == "gemini" and extract_transport == "api") else "cli"
  if extract_transport == "api" and vision_transport == "cli":
      logger.info("lesson.extract: scanned PDF → forcing cli for vision "
                  "(only gemini api can attach); requested=api")
  out_md, tin, tout = await agent.summarize_lesson_vision(
      provider=extract_provider, model=extract_model, pdf_path=pdf_path,
      section_title=section["title"], section_number=section["number"],
      page_start=ps, page_end=pe, homework_job_id=job_id, phase_output_id=po_id,
      transport=vision_transport,
  )
  ```

**Commands:** `uv run python -m pytest tests/services/test_agent.py tests/services/test_pipeline*.py -q`
**Commit:** `api-vision: scanned lesson-extract uses Vertex api for gemini (summarize_lesson_vision transport)`

---

## Task 4 — Acceptance (real Vertex, minimal tokens — generation-affecting)

Vertex SA creds are present locally (`GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT`/`LOCATION`, no `GEMINI_API_KEY`), so the controller runs the **binding SDK proof** here — cheaply, per the no-mass-gen rule (one tiny call, not a homework):
- `scripts/smoke_api_vision.py`: carve a 1–2 page window from any PDF under `var/books/*/source.pdf` via `agent._subset_pdf` (or generate a 1-page PDF); call `await api_transport._gemini("gemini-2.5-flash", "Reply with one word describing this page.", attachments=[window])`; assert `rc == 0`, non-empty `text`, and that it went over **Vertex** (no `GEMINI_API_KEY`). Proves the multimodal SDK path + Vertex auth end-to-end with minimal tokens. RAN by controller; record the output in the worklog.
- **Binding full confirmation (gatekeeper/user, all-Vertex repro):** revert `.env` to `EXTRACT_PROVIDER=gemini` + `EXTRACT_TOC_TRANSPORT=api`, re-extract the failing scanned book **`e020d51f`** → expect a valid `ExtractedTOC`, **no `initOauthClient`**, and an `agent_usages` row with `auth_mode='api'`. Note this in the PR as the acceptance to run before relying on it.

---

## Finish (controller)

- Full suite green: `uv run python -m pytest tests/ -q` (the 5 notion-503 env fails are pre-existing — confirm count unchanged).
- **Rebase-check:** `git fetch origin` → `git log HEAD..origin/Nggaev-v2`; rebase onto `origin/Nggaev-v2` if it moved, re-run suite. Expect trivial append conflicts in `MASTER_MEMORY.md`/`INDEX.md` only.
- **Worklog 0094** (verify next-free against live tip) + INDEX row. WISHLIST: close `api-vision-1` if listed; strike the "scanned books out of scope" caveat on the `EXTRACT_TOC_TRANSPORT` item.
- **De-stale:** the `api_transport` "text-only in v1" docstring (done in Task 1); the TOC / PDF-handling caveats in `docs/CODE_MAP.md` (`api_transport`/`notion_fetch`/`agent` vision lines), `docs/HOW_IT_WORKS.md`, `README.md` ("Gemini CLI rejects files > 20 MB" / "vision is cli-only" notes — now gemini vision can go over Vertex api).
- **Stage only this branch's files** (`api_transport.py`, `agent.py`, `pipeline.py`, the test files, `scripts/smoke_api_vision.py`, plan, memory/doc files) — never `git add -A`.
- `git mv` plan → `docs/superpowers/plans/shipped/`.
- PR titled `[api-vision-1] gemini vision over Vertex (scanned TOC + lesson-extract)` to the gatekeeper. **No self-merge.**

## Out of scope

claude-api vision (the claude attachment guard stays); the `extract_toc` text-branch `keep_pdf` (text books rely on local text — unchanged); raw-image attachments beyond the suffix helper (no caller produces them today).
