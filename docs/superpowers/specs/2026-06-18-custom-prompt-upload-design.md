# Custom prompt upload on the section page

**Date:** 2026-06-18
**Status:** Approved (brainstorm) — pending plan
**Scope:** Let a user upload a `.md` file on the section page whose text is appended
to every content phase prompt for that one generation run. Stored on the job row for
provenance; never written to `prompts/`.

## Problem

The per-phase prompts in `prompts/_general/*.md` are fixed. A user who wants to steer
a single generation run (e.g. "emphasise vocabulary X", "use this worked example
style", "avoid topic Y") has no way to inject ad-hoc guidance without editing the
committed prompt files. The ask: on
`/book/:bookId/section/:sectionId`, add an upload control for a `.md` file. The system
loads it and uses it for that run, but does **not** persist it to the `prompts/`
directory (the "docs").

## Decisions (locked in brainstorm)

1. **Effect on generation:** the uploaded markdown is *appended* as extra instructions
   to each existing content phase prompt — the built-in prompts still run; the custom
   text steers all of them. (Not a replacement, not per-phase targeting, not a
   one-shot run-level note.)
2. **Persistence:** stored on the job row (`homework_jobs.custom_prompt`, new nullable
   `Text` column). Used for that generation run and recorded for provenance; **never**
   written to `prompts/`. This is the natural fit for the background-worker
   architecture (the worker reads the job row; there is no live request context).
3. **Upload mechanism:** the browser reads the `.md` text via the File API and sends it
   inline in the existing generate request body. No file lands on the server disk — no
   multipart endpoint, no temp file.
4. **Which phases get the appended text — content phases only.** The append happens
   where `base_phase_prompt = get_prompt(...)` is built (`pipeline.py:702`). That branch
   already excludes:
   - **`extract`** — pinned to the cheap gemini extractor; produces a flat factual
     lesson summary that every other phase depends on. Steering instructions here would
     corrupt the summary. (`extract` takes the other branch at `pipeline.py:588`, so it
     is naturally excluded — we simply do not add the append to the extract path.)
   - **judge** (`phase_judge.py`) — the LLM grader. It evaluates output; it must not
     take authoring instructions. (Left untouched — we do not thread `custom_prompt`
     into the judge call.)
   So the appended text reaches exactly the student-content phases:
   `case-based-preview`, `flashcards`, `memory-check`, `practice-rlc`,
   `practice-error-detection`, the subject game, `boss-arena`, `reflection`.

## Data flow (mirrors the existing `transport` field exactly)

```
section.tsx  (FileReader.readAsText → React state)
  → api.generate({ ..., custom_prompt })
  → POST /api/v1/books/:book/sections/:section/generate   body.custom_prompt
  → validate length (≤ 20 000 chars) → 400 if exceeded
  → jobs_repo.create(custom_prompt=...)
  → homework_jobs.custom_prompt           (new nullable Text column)
  → pipeline.run() reads job.custom_prompt
  → threaded through _run_tail / _run_wave → _run_phase
  → appended to base_phase_prompt at pipeline.py:702 (content phases only)
```

`transport` is the precedent for every hop: `GenerateRequest.transport` →
`jobs.py` `generate` → `jobs_repo.create(transport=...)` →
`HomeworkJob.transport` column → `pipeline.run()` `getattr(job, "transport", ...)`
→ threaded as a kwarg through `_run_tail`/`_run_wave`/`_run_phase`. `custom_prompt`
follows the identical path, so each change has a working template a few lines away.

## Components

### Backend

| File | Change |
|------|--------|
| `alembic/versions/<new>.py` | New revision: add nullable `custom_prompt TEXT` to `homework_jobs`. No server default (NULL = "no custom prompt"). |
| `app/models/homework_job.py` | `custom_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)`. |
| `app/schemas/job.py` | `GenerateRequest.custom_prompt: str \| None = None`. (Not added to `JobOut` — out of scope to surface it back.) |
| `app/api/v1/jobs.py` | In `generate`: validate length (`> 20_000` chars → `HTTPException(400, ...)`); pass `custom_prompt=body.custom_prompt` to `jobs_repo.create`. |
| `app/repositories/jobs.py` | `create(...)` gains `custom_prompt: str \| None = None` and sets it on the row. |
| `app/services/pipeline.py` | `run()` reads `custom_prompt = getattr(job, "custom_prompt", None)`; thread it as a kwarg through `_run_tail` → `_run_wave` → `_run_phase` (alongside `transport`). In `_run_phase`, in the `else` (non-extract) branch, append it to `base_phase_prompt`. |

**Append format** (in `_run_phase`, content branch only):

```python
base_phase_prompt = get_prompt(subject, phase_name)
if custom_prompt:
    base_phase_prompt = (
        base_phase_prompt
        + "\n\n## Additional instructions (user-provided)\n"
        + custom_prompt
    )
```

A clear delimiter heading keeps the model from blending the custom text into the
built-in policy; it is presented as supplementary guidance, not a replacement.

### Frontend

| File | Change |
|------|--------|
| `web/src/routes/section.tsx` | New "Custom prompt (optional)" card (its own `<section className={CARD}>`, placed between `AgentPicker` and `ActionPanel`). A file input `accept=".md,.markdown,text/markdown"`; on select, `FileReader.readAsText` → store `{ filename, text }` in component state. Show the filename + a remove/clear button when set. Pass `custom_prompt: customPrompt?.text ?? null` into `handleGenerate`'s `api.generate(...)` call (both Generate and Regenerate paths use the same `handleGenerate`). |
| `web/src/lib/api.ts` | `generate` opts gain `custom_prompt?: string \| null`; default `null`; include in the JSON body. |

No `types.ts` change needed (we do not display the prompt back). No new component file —
the card is small and local to `section.tsx`, matching the existing inline
`AgentPicker`/`ActionPanel` pattern in that file.

## Error handling

- **Oversize prompt:** server returns `400` with a clear message; the FE surfaces it via
  the existing `toast.error(msg)` path in `handleGenerate`'s `catch`.
- **Non-text / unreadable file:** `FileReader.onerror` → `toast.error`; state stays
  cleared. We do not parse or validate markdown structure — any text is accepted (the
  20 000-char cap is the only gate).
- **Empty file:** treated as no custom prompt (`text.trim() === ""` → send `null`).

## Known interaction (documented, not solved)

Natural-key idempotency (`force=false`, `jobs_repo.find_active_for_section`) returns an
existing active job for the section and **ignores** a newly-supplied `custom_prompt`.
Consequence: a custom prompt only takes effect when a **fresh job is created** — i.e.
the first Generate for a section, or **Regenerate** (`force=true`) when a job already
exists. We do not rework idempotency to key on `custom_prompt`. This matches the
existing UI: a section with a finished/running job shows Regenerate, which is the
correct affordance for "run again with my new instructions".

## Testing

- **Schema:** `GenerateRequest` accepts and round-trips `custom_prompt`; default `None`.
- **Endpoint:** `custom_prompt` over the cap → `400`; within cap → persisted on the job
  row (assert via `jobs_repo`/DB read). Existing generate tests still pass with the
  field absent (backwards compatible).
- **Repo:** `create(custom_prompt=...)` writes the column; `create()` without it → NULL.
- **Pipeline unit:** `_run_phase` with `custom_prompt` set appends the delimiter +
  text to the content-phase prompt; with `phase_name="extract"` the prompt is
  **unchanged** (extract branch never appends). With `custom_prompt=None`, the prompt is
  byte-identical to today (regression guard).
- **FE typecheck:** `npx tsc -p tsconfig.app.json --noEmit` clean after the `api.ts` +
  `section.tsx` changes.
- **Acceptance (real CLI smoke):** one real `gemini`/`claude` generate of a section with
  a short custom prompt (e.g. "Always include one extra worked example."), confirming
  the appended instruction visibly influences a content phase's output and the extract
  summary is unaffected.

## Out of scope (YAGNI)

- Per-phase targeting (a custom prompt for just `flashcards`).
- Saving / reusing prompt presets or a prompt library.
- Server-side file storage or a multipart upload endpoint.
- Displaying the custom prompt back in the preview/job UI.
- Re-keying idempotency on `custom_prompt`.
- Applying custom prompts at the batch level (`/jobs/batch`) — section page only.
