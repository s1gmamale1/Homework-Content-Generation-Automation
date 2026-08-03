# Structured `content_json` generation — Design Spec (Pass 1: RLC + sentence-fill)

**Date:** 2026-08-03
**Branch:** Nggaev-v2
**Status:** Draft for user review

## Goal

Generate the platform's canonical `content_json` **directly**, instead of emitting markdown that
the platform reverse-engineers. Pass 1 covers two phases — `practice-rlc` and `practice-sentence` —
and includes the single platform-side change needed for them to be consumed.

Markdown does not go away: it becomes a **rendered artifact derived from the JSON**, so the judge,
solver, `content_lint`, teaching audit, Notion archival and the operator console keep working
unchanged.

## Why

The platform ingests our markdown through a lossy adapter layer (`chb_*` services + a 769-line
parser). Measured over 33,979 live phase outputs, **22.8% fail to parse** and are silently
downgraded or dropped. Worst offenders: `practice-rlc` 55.3%, `practice-tictactoe` 49.3%,
`practice-sentence` 46.2%.

This is not an accident of parser quality — it is a representational limit. The platform's own
`parse_rlc` says so:

> "The strict 5-step `rlc_config` … is not reliably recoverable from CHB markdown (**corpus: 2/31
> clean**). Emitting an invalid one would fail the publish validator, so Phase 1 folds RLC to
> scrubbed study text … it upgrades to a real game in Phase 2c."

Markdown cannot reliably express a fixed 5-step ordered structure with an enumerated role. A schema
can, trivially.

## Canonical contract — authority and shapes

**Schema authority is `apps/library/models/validators.py` + the mobile TypeScript consumers.**
NOT `_homework_phase_parsers.py`, which is legacy and materially disagrees with the live validators.

### `rlc_config` (`validate_rlc_config`, validators.py:368)

- `expert_role` ∈ 12-value enum: `fire_inspector`, `structural_engineer`, `business_consultant`,
  `medical_diagnostician`, `agronomist`, `teacher`, `lawyer`, `city_planner`, `epidemiologist`,
  `ethicist`, `historian`, `general`
- `steps` — **exactly 5**, in this exact order (`RLC_STEP_ORDER`):
  1. `decision` — `options` ≥2, **exactly one** `is_correct`
  2. `info_request` — same option rule
  3. `final_decision` — same option rule
  4. `concept_select` — `concept_chips` ≥2, **exactly one** `is_correct`
  5. `reasoning` — `min_chars` int (default 80)

Mobile already renders this: `RealLifeChallenge.tsx` takes a `config` prop and builds steps via
`configToSteps()`, falling back to a static case only when config is absent. **No mobile work.**

### `sentence_fill_config` (`validate_sentence_fill_config`, validators.py:440)

- `items` — non-empty
- each item: `passage` containing **1–6** `___` blanks; `answers` length **equal to blank count**
- optional `mode: "word_bank"` → `word_bank` must contain every answer

Mobile `SentenceFill.tsx` already normalizes "static OR API config". **No mobile work.**

## Architecture

```
structured authoring prompt (per phase)
   → model → content_json ──[pydantic schema + 1 retry]
        │
        └─→ PhaseArtifact { output_md, content_json, authoring_mode,
                            schema_version, renderer_version }
                 ├─→ phase_outputs.content_json (+ mode/version columns)
                 └─→ phase_outputs.output_md  ← render_md(phase, cfg)
                          └→ judge · solver · content_lint · teaching_audit
                             · Notion · console   (all unchanged)

export → payload.phases[] = [ {phase_name, output_md, content_json,
                               content_schema_version, authoring_mode, judge_status} ]
   → HomeworkImport.raw_payload → transform_chb reads per-phase content_json → native
```

### Atomicity — every path returns one artifact

The pipeline currently **replaces `output_md` wholesale** after judge regeneration
(`pipeline.py`: `output_md, tin, tout, produced_by = r_md, r_tin, r_tout, r_prod`) and again after
solver regeneration. If `content_json` were persisted independently, a regenerated markdown would
survive alongside a stale JSON — the "source of truth" would be a lie.

Therefore **every generation path returns a complete `PhaseArtifact`**: initial generation, judge
regeneration, solver regeneration, and markdown fallback. The artifact is persisted **only after
the final accepted generation**, and `phase_repo.create_or_reset` clears every structured field.

### Persistence

One alembic migration adds to `phase_outputs`: `content_json` (JSONB, nullable),
`authoring_mode` (text), `content_schema_version` (text), `renderer_version` (text). All nullable —
existing rows read as `markdown_legacy`. No backfill.

### Prompt separation

One prompt cannot both demand JSON output and serve as the markdown grading contract.

- **Markdown evaluation contract** — the existing `prompts/_general/<phase>.md`, unchanged. Judge,
  solver, lint and the fallback path keep using it.
- **Structured authoring prompt** — a new sibling that describes the same pedagogy but requires
  JSON conforming to the schema. Used only on the structured path.
- **`prompt_hash` includes schema_version and renderer_version**, so a schema or renderer change
  invalidates cached/reused outputs.
- **Custom uploaded prompts stay markdown-authored** (`authoring_mode = markdown_custom`). A
  structured-custom contract is explicitly out of scope.

### Typed fallback and provenance

`content_json IS NULL` does **not** mean "fell back" — it is also true for ~34k historical rows,
unsupported phases, and custom prompts. So provenance is explicit:

`authoring_mode ∈ { structured, markdown_fallback, markdown_custom, markdown_legacy }`

Fallback triggers **only** on a typed schema/render-conformance failure. It must NOT trigger on
auth errors, 429s, credential-slot saturation, timeouts or network failures — those keep their
existing retry/failover semantics. This requires a distinct exception type from the generic
`RuntimeError` that `run_phase` raises after validation exhaustion.

**Scope of the "never worse" claim:** import compatibility only. A fallback costs up to three model
calls, so it is strictly worse in cost and latency — that is an accepted trade, not a free win.

## Platform change (in scope, one file)

`apps/library/services/homework_transform.py` — `_collect_phases` currently reads only
`phase_name`, `output_md`, `judge_status`. It gains a per-phase `content_json` read; when present
and valid for that phase's kind, the transform uses it directly and records outcome `native`,
bypassing `chb_practice` parsing. When absent, behaviour is byte-identical to today.

`chb_practice.parse_rlc`'s designed downgrade stays as the fallback for markdown-only payloads.

## Export contract

Our current exporter emits `"phases": {phase_name: output_md}` — a **dict**. `transform_chb`
iterates a **list** of objects keyed `phase_name`. These are incompatible; the real
platform-ingest exporter must be identified (or written) rather than assumed. Target shape:

```json
{"phase_name": "practice-rlc", "output_md": "...", "content_json": {},
 "content_schema_version": "rlc_config@1", "authoring_mode": "structured",
 "judge_status": "ok"}
```

## Testing

Round-trip equality (`parse(render(cfg)) == cfg`) is **not** a valid invariant — the markdown
parsers deliberately derive defaults, drop answer-bearing sections and downgrade unsupported
shapes. Two independent gates instead:

1. **Structured gate** — our `content_json` passes the platform's live write validators,
   redactors, graders and the mobile contract.
2. **Markdown gate** — `render_md` output remains accepted by every current markdown consumer
   (`content_lint`, judge, solver, teaching audit, Notion block rendering).

Plus:
- **Unit** — schema round-trip; renderer golden files.
- **Atomicity** — judge-regen and solver-regen paths produce a consistent artifact (RED-proved by
  mutating one path to return a bare string).
- **Fallback typing** — a schema failure falls back; a 429/auth error does **not** (RED-proved).
- **Acceptance** — one real lesson × 2 phases over `transport=api`, then the full platform path:
  ingest → sanitization → transform → publish validation → student-facing redaction. Expected
  outcome `native` for both phases, with the answer key absent from the student-facing payload.

## Out of scope

- The ~34k existing phase outputs (no migration — user decision).
- `practice-tictactoe`: `ttt_config` is absent from `PRACTICE_ARC_GAME_KINDS` and mobile
  `Ttt.tsx` takes no config (static `buildItems(t)`). Needs a React Native rewrite — separate lane.
- `case-based-preview`: deferred to pass 2 pending validation against `validate_cbp_config`.
- The remaining 7 phases.
