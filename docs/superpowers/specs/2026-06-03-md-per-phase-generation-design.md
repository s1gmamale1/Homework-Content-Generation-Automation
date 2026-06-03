# Markdown-Per-Phase Generation — Design Spec (Effort A: Architecture Flip)

**Date:** 2026-06-03
**Branch:** Nggaev-v2
**Status:** Draft for user review

## Goal

Make every content phase produce **one markdown file** as its entire output. Remove
all structured-JSON generation, schema validation, and packet assembly. The per-phase
`.md` files are the deliverable. Notion receives one sub-page per phase (rendered md +
the `.md` attached). A deterministic, warn-only validator checks each phase's markdown
against its spec rules and surfaces warnings in the operator console.

This spec covers **Effort A — the architecture flip** (the plumbing). A separate
follow-on spec (**Effort B — prompt reshape**) rewrites the 8 phase prompts to match the
`docs/Infra_prompts` specifications and authors each phase's full validator rule set.
Effort A converts the prompts only minimally (JSON-output → markdown-output) so the new
flow is provable end-to-end; Effort B does the faithful content rewrite.

## Why

The current pipeline forces 7 of 8 content phases through Pydantic JSON-mode
(`STRUCTURED_PHASE_SCHEMAS`), persists each to a `*_json` column, assembles a combined
`assembled_md` packet plus a `content.json`, and renders bespoke structured cards in the
console. The `Infra_prompts` specs were authored as ready-to-use prompts whose natural
output is **markdown**, and the operator console's job is **review before shipping to
Notion** — which markdown serves directly. The structured layer is now overhead: it adds
schema-drift failures, a synth-to-md step, per-phase columns, and frontend components,
all to reconstruct what the model can just write as markdown.

## Current state (verified)

- **Phases (general flow):** `case-based-preview, flashcards, memory-check, practice-rlc,
  practice-error-detection, <subject game>, boss-arena, reflection` (`flows.py:33-42`).
  Subject game chosen by `SUBJECT_GAME` (`flows.py:23-31`).
- **Structured generation:** `agent.STRUCTURED_PHASE_SCHEMAS` (`agent.py:117-134`) maps 13
  phase names to Pydantic classes; `pipeline._execute_phase` (`pipeline.py:974-993`) calls
  `run_phase_prompt_structured` for those, then `_synth_md_for_structured` to make a
  human-readable `output_md`. Only `reflection` already takes the free-text md path.
- **Persistence:** `_JSON_COLUMN_SETTERS` (`pipeline.py:333-349`) writes each parsed phase
  to a `*_json` column on `homework_jobs` (`homework_job.py:34-70`). Per-phase markdown
  already persists in `phase_outputs.output_md` (`phase_output.py:21`).
- **Assembly:** `_assemble` / `_render_homework_md` build `job.assembled_md`
  (`homework_job.py:30`).
- **Artifacts:** `job_artifacts.structured_artifacts` / `build_content_json` produce the
  Notion `content.json` and the download zip's `*.json` files.
- **Notion:** `notion_archive.archive_job` creates lesson → `Homework` page, renders
  `assembled_md` to blocks, attaches `homework.md` + `content.json`.
  `page_creator.find_or_create(client, parent_id, title)` creates/reuses a child page —
  the same primitive works for phase sub-pages.
- **Console:** `preview.tsx` gates on `assembled_md`, then `isFlowV2(job)` →
  `FlowV2Preview` renders structured cards from `*_json` columns (`flow-v2-phases.tsx`),
  else `LegacyPreview` renders `assembled_md` as markdown (`ReactMarkdown` + `MD_COMPONENTS`,
  with `rehypeRaw` for inline SVG).
- **Download:** `jobs.py:281` `download` endpoint — `?format=md` returns `assembled_md`;
  default zips `homework.md` + `structured_artifacts` `*.json`. Gates on
  `job.assembled_md is None`.

## Target architecture

```
extract → [phase_1 … phase_N]  (DAG-parallel, unchanged scheduler)
              │
              ├─ each phase: run_phase_prompt (free-text)  → markdown
              ├─ deterministic validator(phase, md)        → warnings[]
              └─ persist: phase_outputs.output_md + .validation_warnings
                          (NO *_json columns, NO assembly)

job done →
  Console preview:  render each phase's output_md as markdown, in flow order,
                    with a per-phase warnings strip + job-level warning count
  Download .zip:    one <NN>-<phase>.md file per phase
  Notion archive:   lesson → Homework page → one sub-page per phase
                    (rendered md blocks + that phase's .md attached)
```

### Components & changes

**1. Pipeline (`pipeline.py`)**
- `_execute_phase`: every content phase takes the free-text path
  (`run_phase_prompt` → `output_md`). Delete the `STRUCTURED_PHASE_SCHEMAS` branch usage,
  `_synth_md_for_structured`, and the `_JSON_COLUMN_SETTERS` write block.
- After a phase's md is produced, run the validator and store warnings on the
  `phase_outputs` row.
- Remove `_assemble` / `_render_homework_md` and the `assembled_md` write. Job
  completion no longer assembles; it marks `done` once all phases succeed.

**2. Validator (new module `app/services/phase_validator.py`)**
- `validate(phase_name: str, md: str, *, subject: str) -> list[str]` — returns warning
  strings. Pure, deterministic, no LLM, no I/O.
- A per-phase **rule registry**: `RULES: dict[str, list[Rule]]` where each `Rule` is a
  small callable `(md, ctx) -> str | None`. Rules are derived from each phase's
  `Infra_prompts` "Forbidden" list, required structure, and counts.
- **Mechanism is text/heading/regex level** (not a full markdown AST): required `##`/`###`
  headings present, required counts (e.g. CBP `### Checkpoint` ×3, `### Learning Block`
  ×2, a Decision-Process section, a Final-Simulation section), forbidden substrings, and
  mixed-apostrophe detection (both `'`/`ʻ` and `'` present).
- **Warn-only:** warnings never block or regenerate; the phase always saves. (Individual
  rules can be promoted to blocking later — out of scope here.)
- Effort A ships the **framework + a starter rule set** (presence of the phase's top
  heading + non-empty body). Effort B authors each phase's full rule list alongside its
  prompt rewrite.

**3. Storage**
- Add `phase_outputs.validation_warnings: JSONB nullable` (list of strings).
- Migration drops `homework_jobs.assembled_md` and all `*_json` columns
  (`games_json, flashcards_json, final_challenge_json, memory_sprint_json, reading_json,
  source_map_json, boss_arena_json, cbp_json, memory_check_json, practice_*_json`).
  `source_map_json` is also dropped (the Source Map view goes away with the structured
  layer; the extract summary remains a normal phase/section).

**4. Notion (`notion_archive.py` + `notion/`)**
- `_push_to_notion`: after `find_or_create(... "Homework")`, for each phase in flow order
  `find_or_create(client, homework_id, <Phase Title>)`, and into that sub-page write
  `markdown_to_notion_blocks(phase_md)` + attach `<phase>.md` via `upload_bytes` +
  `make_file_upload_block`. Idempotency: skip a sub-page that already
  `page_has_content`. Drop `content.json` and the single-page assembled render.
- Phase title mapping: a small `PHASE_TITLES` dict (`case-based-preview → "Case-Based
  Preview"`, etc.).
- **Placeholder handling (REQUIRED):** the markdown→blocks renderer MUST convert a
  non-resolving image — `![placeholder: … ](placeholder)` (target literally `placeholder`,
  or any non-`http(s)` URL) — into a **callout/text block** carrying the alt text, never
  an image block (an unresolvable image URL errors or renders broken in Notion). See
  "Visual handling" below.

**5. Console (`web/src/`)**
- `preview.tsx`: drop `assembled_md` gate (gate on "job done + has phase outputs");
  render each phase's `output_md` as markdown (reuse `MD_COMPONENTS` + `ReactMarkdown` +
  `rehypeRaw`) in flow order, each under its phase title, with a **warnings strip**
  (from `validation_warnings`) and a job-level warning count.
- Job phase data must be exposed to the frontend: extend the job/phase API payload with
  per-phase `{phase_name, phase_order, output_md, validation_warnings}`.
- Delete `flow-v2-phases.tsx`, `FlowV2Preview`, the `components/flow-v2/*` structured
  views, `LegacyPreview`'s structured splicing, and the now-unused `*_json` types.
- `DonePanel` (`job.tsx`): counts derived from phase outputs / warnings instead of
  `*_json` columns.

**6. Download (`jobs.py`)**
- `download`: gate on "done + phase outputs exist". Default zip = one
  `<phase_order>-<phase_name>.md` per phase from `phase_outputs.output_md`. Drop the
  `*.json` members and `structured_artifacts` import. `?format=md` → either remove, or
  return the phases concatenated with `## <title>` headers (decide in plan; lean: keep a
  concatenated convenience md built on the fly, not persisted).

**7. Removals (dead after the above)**
- `agent.STRUCTURED_PHASE_SCHEMAS` and `run_phase_prompt_structured` (if unused elsewhere).
- `pipeline._JSON_COLUMN_SETTERS`, `_synth_md_for_structured`, `_assemble`,
  `_render_homework_md`.
- `job_artifacts.structured_artifacts` / `build_content_json`.
- `jobs_repo.set_*_json` setters; the `*_json` columns.
- `app/schemas/` phase modules no longer referenced (flashcards, memory_check,
  boss_arena, practice_games, reading, games, final_challenge, memory_sprint, flow_v2);
  remove once nothing imports them.

### Visual handling (images & SVG) — REQUIRED

The `Infra_prompts` specs are explicit (and this is a must-have): the model does **not**
generate raster artwork. It either emits an inline diagram or leaves a placeholder.

- **Convention (adopted verbatim from the specs):**
  - **Diagrams (structural/numeric — figures, vectors, maps, cross-sections):** emit
    **inline `<svg>`** in the markdown.
  - **Raster/photo (real scenes, organisms, apparatus, portraits):** emit a placeholder
    markdown image, never a fabricated picture:
    `![placeholder: <description> — image gen required](placeholder)`
    (the `— SVG required` variant is used when even an SVG can't be produced).
- **Console (`preview.tsx`):** `ReactMarkdown` + `rehypeRaw` already renders inline
  `<svg>`. A `![…](placeholder)` image naturally renders as its alt text — acceptable;
  optionally style it as a dim "visual placeholder" chip so reviewers spot it.
- **Notion (`notion/blocks.py`):** the renderer MUST detect a placeholder/non-resolving
  image (target `placeholder` or any non-`http(s)` URL) and emit a **callout or text
  block** with the alt text — NOT an `image` block. (Inline `<svg>` keeps its existing
  behaviour — rendered as escaped text per the accepted R9 limit.)
- **Validator rules (deterministic):**
  - A well-formed `![placeholder: … ](placeholder)` is **valid and expected** — never warn
    on it.
  - **Warn** on a malformed/real-broken image: an image whose URL is empty or non-`http(s)`
    but is *not* the `placeholder` sentinel (catches accidental broken links).
  - (Effort B, per-phase) optionally **warn** when a phase whose spec expects a visual has
    neither an inline `<svg>` nor a placeholder.

### Data flow (one phase)

1. Scheduler launches phase when deps met (unchanged `PHASE_DEPS`).
2. `run_phase_prompt(provider, model, phase_prompt, lesson_context, prior_outputs, …)` →
   `output_md`.
3. `warnings = phase_validator.validate(phase_name, output_md, subject=…)`.
4. `phase_repo` writes `output_md` + `validation_warnings` on the phase row.
5. On all-phases-success → job `done`; pipeline calls `notion_archive.archive_job`.

### Notion layout (chosen)

```
📄 <section title>            (lesson page, find_or_create under subject page)
 └─ 📄 Homework
      ├─ 📄 Case-Based Preview   (md blocks + 📎 case-based-preview.md)
      ├─ 📄 Flashcards           (md blocks + 📎 flashcards.md)
      ├─ 📄 Memory Check         (…)
      ├─ 📄 Real-Life Challenge
      ├─ 📄 Error Detection
      ├─ 📄 <subject game>
      ├─ 📄 Boss Arena
      └─ 📄 Reflection
```

## Testing strategy

- **Validator:** unit tests per rule (missing heading → warning; present → none; mixed
  apostrophes flagged; counts). Pure functions, trivial to TDD.
- **Pipeline:** test that a structured-free phase persists `output_md` + warnings and that
  no `*_json` write occurs; assembly no longer runs.
- **Notion:** unit-test `_push_to_notion` with a mocked client — asserts one
  `find_or_create` per phase under Homework, one `upload_bytes` + file block per phase,
  idempotent skip when a sub-page already has content. (Mirrors existing
  `test_notion_archive.py` style.)
- **Download:** zip contains one `.md` per phase, named `<order>-<phase>.md`.
- **Migration:** `alembic upgrade head` then `downgrade` round-trips.
- **Acceptance (CLI smoke):** one real generation on `claude`, confirm N md phases
  persisted + a live Notion push creating the sub-page tree (per CLAUDE.md acceptance
  gate). Full suite green.

## Decomposition

- **Effort A (this spec):** architecture flip — pipeline md-only, validator framework +
  warnings surfacing, storage migration, Notion sub-pages, console/download rework,
  removals, minimal prompt JSON→md conversion.
- **Effort B (separate spec):** rewrite the 8 `prompts/_general/*.md` to the
  `Infra_prompts` specs (CBP, Flashcards, Quizlet-style Memory Check, the 7 Gamified
  Practices, Boss Arena, Reflection) + author each phase's full deterministic validator
  rule set + the Uzbek Foundation language rules. **Adopt the visual convention in every
  prompt** — inline `<svg>` for diagrams, `![placeholder: … — image gen required](placeholder)`
  for raster (replacing the current `[Diagram: …]` bracket-note convention). Per-phase,
  smoke-tested.

## Risks & open items

- **Register rules (`sen`/`san`) risk false positives** (substrings inside Uzbek words).
  Start with safe rules (required sections, counts, mixed apostrophes, exact forbidden
  phrases); defer fuzzy register checks to Effort B with careful word-boundary handling.
- **Dropping columns is destructive.** Existing jobs lose their structured render; they
  still have `phase_outputs.output_md`. Acceptable (dev data); migration is one-way for
  data but reversible in schema.
- **`?format=md` semantics** with no assembled packet — resolve in the plan (concatenate
  on the fly vs drop).
- **Frontend payload size:** sending every phase's `output_md` inline is fine for a
  single-job review view (already how `assembled_md` was sent).

## Out of scope

- The full prompt content rewrite (Effort B).
- Any CLI/LLM semantic judge (explicitly deferred; deterministic-only for now).
- Blocking/regenerate-on-failure validation (warn-only only).
- Notion Phase 2 (pull), unrelated WISHLIST items.
