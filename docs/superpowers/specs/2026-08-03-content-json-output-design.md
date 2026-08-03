# Structured `content_json` generation — Design Spec (Pass 1: RLC + sentence-fill)

**Date:** 2026-08-03 · **Rev 2** (gate corrections 1–9 folded)
**HCGA branch:** Nggaev-v2
**Platform base:** `origin/Akademiya-AI` @ `2cf98fb` — **not** the local `Nggaev` checkout, which is
**668 commits behind** (verified). The RLC/sentence-fill validator bodies are byte-identical on both,
so the contract below is valid; only line numbers moved.
**Status:** Draft for re-gate

## Goal

Generate the platform's canonical `content_json` **directly**, instead of emitting markdown the
platform reverse-engineers. Pass 1 covers `practice-rlc` and `practice-sentence`.

Markdown does not go away: it becomes a **rendered artifact derived from the JSON**, so the judge,
solver, `content_lint`, teaching audit, Notion archival and the operator console keep working
unchanged.

## Why

Measured over 33,979 live phase outputs, **22.8% fail the platform's markdown parsers** and are
silently downgraded or dropped (`practice-rlc` 55.3%, `practice-tictactoe` 49.3%,
`practice-sentence` 46.2%).

This is a representational limit, not parser quality. The platform's own `parse_rlc`:

> "The strict 5-step `rlc_config` … is not reliably recoverable from CHB markdown (**corpus: 2/31
> clean**). Emitting an invalid one would fail the publish validator, so Phase 1 folds RLC to
> scrubbed study text … it upgrades to a real game in Phase 2c."

Markdown cannot reliably express a fixed 5-step ordered structure with an enumerated role. A schema
can.

## This is a three-repository lane

| repo | change |
|---|---|
| **HCGA** (here) | schemas, structured prompts, renderers, `PhaseArtifact`, migration, exporter |
| **Platform** (`Akademiya-AI`) | `homework_transform.py` native path **+** `emission.py` (`TRANSFORMER_VERSION_CHB` bump — its comment mandates a bump on any parser change) **+** strict projection **+** tests |
| **Mobile** | `configToSteps()` must pass `minChars` (see below) |

The mobile fix may land as its own PR; if it does not land, RLC ships with a known grading-parity
defect and must not be called done.

### Mobile `min_chars` parity defect (blocking for RLC)

`configToSteps()` maps `id/kind/title/prompt/options/chips/keywords` and **drops `min_chars`**.
Mobile then falls back to `const minChars = current.minChars ?? 10` — so a student may submit a
10-character reasoning answer that the server grades against the configured/default **80**.
Fix: add `minChars: s.min_chars`, plus a mobile/server parity test.

## Canonical contract

**Schema authority is `apps/library/models/validators.py` on `origin/Akademiya-AI`, plus the mobile
TypeScript consumers.** NOT `_homework_phase_parsers.py`, which is legacy and materially disagrees.

### `rlc_config`

- `expert_role` ∈ 12-value enum: `fire_inspector`, `structural_engineer`, `business_consultant`,
  `medical_diagnostician`, `agronomist`, `teacher`, `lawyer`, `city_planner`, `epidemiologist`,
  `ethicist`, `historian`, `general`
- `steps` — **exactly 5**, in `RLC_STEP_ORDER`: `decision`, `info_request`, `final_decision`,
  `concept_select`, `reasoning`
- `decision` / `info_request` / `final_decision` — `options` ≥2, **exactly one** `is_correct`
- `concept_select` — `concept_chips` ≥2, **exactly one** `is_correct`
- `reasoning` — `min_chars` int (server default 80)

**Beyond the validator, the mobile-consumable shape is required** (the validator permits far less
than mobile needs to render): config `id`/`title`/`intro`; per-step `id`/`title`/`prompt`;
per-option and per-chip `id`/`label`. A config that validates but omits these renders as a blank
game — it must be rejected by our schema, not theirs.

### `sentence_fill_config`

- `items` non-empty; each item carries an explicit `id` and explicit `mode`
- `passage` with **1–6** `___` blanks; `answers` length **equal to blank count**
- `mode: "word_bank"` → `word_bank` must contain every answer

### Schema modelling rules

Strict recursive Pydantic models: `extra="forbid"`, strict integers, non-empty and unique IDs.
**Persist `model_dump()`, never the raw model response** — this is what guarantees no unknown key
survives.

## Architecture

```
structured authoring prompt (per phase)
   → model → content_json ──[pydantic strict + 1 retry]
        └─→ PhaseArtifact { output_md, content_json, authoring_mode,
                            content_schema_version, renderer_version }
                 ├─→ phase_outputs.*  (persisted once, after the final accepted generation)
                 └─→ output_md = render_md(phase, cfg)
                          └→ judge · solver · content_lint · teaching_audit · Notion · console

export → payload.phases[] = [ {phase_name, output_md, content_json,
                               content_schema_version, authoring_mode, judge_status} ]
   → HomeworkImport.raw_payload → transform_chb native path → native
```

### Atomicity — every path returns one artifact

Both judge regen and solver regen currently replace markdown wholesale
(`output_md, tin, tout, produced_by = r_md, r_tin, r_tout, r_prod` — verified in both places). If
`content_json` were persisted independently, a regenerated markdown would survive beside a stale
JSON.

So **every** path returns a complete `PhaseArtifact`: initial generation, judge regen, solver regen,
markdown fallback. Persisted only after the final accepted generation.
`phase_repo.create_or_reset` clears every structured field.

### Answer-leak containment (security)

Platform validators **ignore unknown keys** and redactors strip only **known** fields — so a
model-invented `answer_key` extra could survive into the student payload.

Two independent defences:
1. **Producer** — `extra="forbid"` + persist `model_dump()`.
2. **Platform** — the native path **strictly normalizes/projects** the config to known fields
   before emission, with a **planted-extra-field redaction test** proving an injected
   `answer_key` never reaches the student payload.

### Schema versioning

The native path accepts **only exact `(phase, version)` pairs** — e.g. `practice-rlc` +
`rlc_config@1`. An unknown version or a structurally invalid payload must **fall back and report
`structured_invalid` → `needs_review`**. It must never silently become `native`.

### Provenance

`authoring_mode ∈ { structured, markdown_fallback, markdown_builtin, markdown_custom,
markdown_legacy }`, enforced by a DB constraint.

- `markdown_builtin` — newly generated phase with no structured support yet
- `markdown_custom` — custom uploaded prompt (stays markdown-authored; structured-custom is out of scope)
- `markdown_legacy` — pre-migration / NULL rows **only**

### Fallback layering

The schema/render failure must be caught **inside the provider `run_fn`**, so it never reaches
`_run_with_failover`, which classifies and retries (verified: "classify → retry same"). Auth, 429,
slot-saturation, timeout and network errors must **escape normally** and keep their existing
retry/failover semantics.

**Worst case is four model calls**, not three: structured attempt + schema retry, then markdown
fallback which can itself retry an empty response. The "never worse than today" claim is scoped to
**import compatibility only** — cost and latency are strictly worse on the fallback path.

### `prompt_hash` — provenance only

Resumption skips any done phase on **status + non-empty markdown** (`if phase_name in _done_md`);
it does **not** compare `prompt_hash` (verified). So including schema/renderer versions in the hash
does **not** invalidate saved work.

**Decision: preserve never-pay-twice semantics.** `content_schema_version` and `renderer_version`
are recorded as provenance on the row; version-aware regeneration is explicitly out of scope. (The
hash does still gate cross-job *extract* reuse — unchanged.)

### Persistence

One alembic migration adds to `phase_outputs`: `content_json` (JSONB), `authoring_mode` (text, DB
constraint), `content_schema_version` (text), `renderer_version` (text) — all nullable; existing
rows read as `markdown_legacy`. No backfill.

**Numbering:** head is currently 0048; the model-config lane plans 0049 (not yet built). Ours must
follow it — re-check the head at implementation time rather than hard-coding a number.

## Export

Unresolved and must be locked before implementation. `scripts/export_homeworks.py` is **untracked**
and emits `phases` as a **dict** `{phase_name: output_md}`, while `transform_chb` iterates a **list**
of `{phase_name, …}` — incompatible. No committed HCGA path posts the platform's complete ingest
envelope.

Task 1 of the plan is to lock **one committed exporter/endpoint**, specifying: `source_ref`,
`external_key`, `language`, subject/grade mapping, authentication, and the rule that **only `done`
jobs and `done`, non-empty phase rows** export.

## Testing

`parse(render(cfg)) == cfg` is **not** a valid invariant — the markdown parsers deliberately derive
defaults, drop answer-bearing sections and downgrade unsupported shapes. Two independent gates:

1. **Structured gate** — `content_json` passes the platform's live write validators, redactors,
   graders and the mobile contract.
2. **Markdown gate** — `render_md` output stays accepted by every current markdown consumer.

Plus:
- **Unit** — schema round-trip; renderer golden files; `extra="forbid"` rejects unknown keys.
- **Atomicity** — judge-regen and solver-regen produce a consistent artifact (RED-proved by
  mutating one path to return a bare string).
- **Fallback typing** — a schema failure falls back; a 429/auth error does **not** (RED-proved).
- **Version pinning** — an unknown `content_schema_version` yields `structured_invalid` +
  `needs_review`, never `native`.
- **Redaction** — planted extra `answer_key` never reaches the student payload.
- **Mobile parity** — `minChars` from config is enforced client-side and matches server grading.
- **Acceptance** — one real lesson × 2 phases over `transport=api`, then the full platform path:
  ingest → sanitization → transform → publish validation → student-facing redaction. Expect
  `native` for both phases with the answer key absent from the student payload.

## Out of scope

- The ~34k existing phase outputs (no migration — user decision).
- `practice-tictactoe` — `ttt_config` absent from `PRACTICE_ARC_GAME_KINDS`, and mobile `Ttt.tsx`
  takes no config at all (static `buildItems(t)`). Needs a React Native rewrite — separate lane.
- `case-based-preview` — pass 2, pending validation against `validate_cbp_config`.
- The remaining 7 phases.
- Version-aware regeneration of already-done phases.
