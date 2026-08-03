# Structured `content_json` generation — Design Spec (Pass 1: RLC + sentence-fill)

**Date:** 2026-08-03 · **Rev 5** (gate rounds 1–5 folded)
**HCGA branch:** Nggaev-v2 · work in worktree `../HCGA-content-json`
**Platform base:** `origin/Akademiya-AI` @ `2cf98fb` — **not** the local `Nggaev` checkout, which is
**668 commits behind** (verified).

> **Correction to rev 2:** rev 2 claimed the validator bodies were byte-identical on both branches.
> That was **false**. The active branch uses `is_int()` (which rejects `bool`, since `bool` subclasses
> `int`) where the stale local uses `isinstance(..., int)`. The active file's own docstring names the
> hazard: *"`reasoning.min_chars` — `true` becomes a 1-character minimum."* Schema alignment still
> holds because strict Pydantic integers were already planned, but **all contract reading must be
> done against `origin/Akademiya-AI`.**

**Status:** Draft for re-gate

## Goal

Generate the platform's canonical `content_json` **directly**, instead of emitting markdown the
platform reverse-engineers. Pass 1 covers `practice-rlc` and `practice-sentence`.

Markdown becomes a **rendered artifact derived from the JSON**, so the judge, solver, `content_lint`,
teaching audit, Notion archival and the operator console keep working unchanged.

## Why

Measured over 33,979 live phase outputs, **22.8% fail the platform's markdown parsers** and are
silently downgraded or dropped (`practice-rlc` 55.3%, `practice-tictactoe` 49.3%,
`practice-sentence` 46.2%). The platform's own `parse_rlc` explains why:

> "The strict 5-step `rlc_config` … is not reliably recoverable from CHB markdown (**corpus: 2/31
> clean**) … it upgrades to a real game in Phase 2c."

Markdown cannot reliably express a fixed 5-step ordered structure with an enumerated role. A schema
can.

## Three-repository lane

| repo | change |
|---|---|
| **HCGA** (here) | schemas, structured prompts, renderers, `PhaseArtifact`, migration, **payload builder + ingest CLI** |
| **Platform** (`Akademiya-AI`) | `homework_transform.py` native path **+** `emission.py` (`TRANSFORMER_VERSION_CHB` bump) **+** strict projection **+ semantic scrubbing** **+** `min_chars` bound **+** tests |
| **Mobile** | `configToSteps()` must pass `minChars` |

### Mobile `min_chars` parity defect (blocking for RLC)

`configToSteps()` drops `min_chars`; mobile falls back to `?? 10` while the server grades against
the configured/default 80. Fix: `minChars: s.min_chars` + a mobile/server parity test. May land as
its own PR; if it does not land, RLC ships with a known grading-parity defect and is not done.

## Canonical contract

**Authority: `apps/library/models/validators.py` on `origin/Akademiya-AI`, plus the mobile
TypeScript consumers.** NOT `_homework_phase_parsers.py` (legacy, materially disagrees).

### `rlc_config`

- `expert_role` ∈ 12-value enum: `fire_inspector`, `structural_engineer`, `business_consultant`,
  `medical_diagnostician`, `agronomist`, `teacher`, `lawyer`, `city_planner`, `epidemiologist`,
  `ethicist`, `historian`, `general`
- `steps` — **exactly 5** in `RLC_STEP_ORDER`: `decision`, `info_request`, `final_decision`,
  `concept_select`, `reasoning`
- `decision` / `info_request` / `final_decision` — `options` ≥2, **exactly one** `is_correct`
- `concept_select` — `concept_chips` ≥2, **exactly one** `is_correct`
- `reasoning` — `min_chars` int, **bounded 20–1000** (see below)

**Mobile-consumable shape is also required** (the validator permits far less than mobile needs):
config `id`/`title`/`intro`; per-step `id`/`title`/`prompt`; per-option and per-chip `id`/`label`.
A config that passes their validator but omits these renders as a blank game — our schema must
reject it.

#### `min_chars` must be bounded (grading bypass)

The platform validator checks only `is_int(min_chars)` — **no range**. `grade_rlc` credits the
reasoning step when the length test passes, so **`min_chars = -1` makes an empty answer score**, and
propagating it to mobile preserves the bypass.

Bound it in **both** the Pydantic model and the platform's validation/projection: **20 ≤ min_chars ≤
1000**. Test negative, zero, boolean, and excessive values.

### `sentence_fill_config`

- `items` non-empty; each item carries an explicit `id`
- `passage` with **1–6** `___` blanks; `answers` length **equal to blank count**
- **`mode` is restricted to `"word_bank"` in Pass 1** (producer schema *and* native projection)
- `word_bank` must contain every answer

#### Why `free_recall` is excluded (mobile blocker)

On active mobile `origin/main@f761c5a`, `SentenceFill.tsx`:

- `normalizeConfigItems()` maps `id`/`passage`/`answers: []`/`bank`/… and **never reads `mode`**
- the UI renders **word-bank chips only** — there is **no `TextInput` anywhere in the file**

So `mode="free_recall"` with no bank is **impossible to complete**. Emitting it would ship a dead
exercise. A genuine free-recall implementation is a mobile lane, not this one.

#### Answers and bank entries must be normalized-unique per item

`usedWords` consumes a chip once used (`disabled = isUsed || !bankActive`), so **duplicate answer
values across blanks make a valid-looking exercise uncompletable** — the second blank has no chip
left. The platform validator does not catch this: it checks only `items` non-empty and
`len(answers) == blanks`, with no uniqueness or non-empty-string checks.

Our schema therefore requires, per item: answers normalized-unique, bank entries normalized-unique,
and every string non-empty.

**Normalization is defined exactly as mobile's `norm()`** — otherwise our uniqueness check and the
runtime's chip-consumption disagree:

```ts
function norm(s: string): string { return s.trim().toLowerCase().replace(/\s+/g, " ") }
```

i.e. trim → lowercase → collapse internal whitespace to single spaces.

### Schema modelling rules

Strict recursive Pydantic: `extra="forbid"`, strict integers (rejects `bool`), non-empty unique IDs.
**Persist `model_dump()`, never the raw model response.**

## Architecture

```
structured authoring prompt (per phase)
   → model → content_json ──[pydantic strict + 1 retry]
        └─→ PhaseArtifact { output_md, content_json, authoring_mode,
                            content_schema_version, renderer_version }
                 ├─→ phase_outputs.*  (persisted once, after the final accepted generation)
                 └─→ output_md = render_md(phase, cfg)
                          └→ judge · solver · content_lint · teaching_audit · Notion · console

payload builder → POST /api/v1/library/homework-imports/ingest
   → HomeworkImport.raw_payload → transform_chb native path → native
```

### Atomicity

Both judge regen and solver regen replace markdown wholesale (`output_md, tin, tout, produced_by =
r_md, …` — verified in both). So **every** path returns a complete `PhaseArtifact`: initial, judge
regen, solver regen, markdown fallback. Persisted only after the final accepted generation;
`phase_repo.create_or_reset` clears every structured field.

### Answer-leak containment — projection is NOT sufficient

Two distinct leaks:

1. **Unknown keys.** Platform validators ignore extras and redactors strip only known fields, so a
   model-invented `answer_key` could survive. → producer `extra="forbid"` + `model_dump()`, and the
   platform native path **strictly projects** to known fields.

2. **Semantic leaks inside known fields.** Redaction is key-based only —
   `RLC_OPTION_LEAK_KEYS = ("is_correct", "consequence")`, `RLC_CHIP_LEAK_KEYS = ("is_correct",)`,
   `RLC_REASONING_LEAK_KEYS = ("acceptable_keywords",)`. **None touch `label`, `title` or `prompt`.**
   So `{"label": "Paris (correct answer)", "is_correct": true}` redacts to
   `{"label": "Paris (correct answer)"}` — the answer survives in plain text.

   The markdown path is protected here by `scrub_answers()` / `scrub_option()`; **the native path
   would bypass that entirely.** So the native path must apply deterministic sanitization — or
   rejection — of answer markers in every student-visible field.

   **Unknown answer dialects must produce `needs_review`, never a silent publish.**
   Tests must cover RLC option labels, chip labels, step titles/prompts, and sentence passages.

#### Scrub-then-revalidate — the exact sequence

"Sanitize or reject" is ambiguous, and scrubbing is destructive: `{"label": "Correct answer: Paris"}`
can scrub to an empty or damaged label, and **the platform validator would still accept it** — it
checks only counts (`len(steps)`, `len(opts)`, `len(chips)`), never label/title/prompt content.

The native path must run, in this order:

1. **Project** strictly to known fields.
2. **Scrub** every student-visible string.
3. **Record** scrub spans and any unknown dialect encountered.
4. **Revalidate** the *sanitized* object against the full native + mobile contract (including our
   non-empty and normalized-uniqueness rules, which the platform validator does not enforce).
5. If sanitization produced empty, duplicate or otherwise invalid data — or an unknown dialect
   remains — **fall back and mark `structured_invalid` / `needs_review`**.
6. **Publish natively only when the post-scrub object is still valid.**

Regression required: a label consisting **entirely** of an answer marker (scrubs to empty) must
route to `needs_review`, not publish as a blank chip.

**Post-scrub uniqueness applies to RLC too, not only sentence answers/banks.** Within each step,
option labels must be normalized-unique and non-empty, and chip labels likewise — scrubbing can
collapse two distinct options into the same string (or into nothing), producing an unanswerable
step that the platform validator would still accept (it counts options, never inspects labels).

### Schema versioning

The native path accepts **only exact `(phase, version)` pairs** — `practice-rlc` + `rlc_config@1`,
`practice-sentence` + `sentence_fill_config@1`. Unknown version or invalid structure ⇒ fall back and
report `structured_invalid` → `needs_review`. Never silently `native`.

### Provenance

`authoring_mode ∈ { structured, markdown_fallback, markdown_builtin, markdown_custom,
markdown_legacy }`, enforced by a DB constraint. `markdown_builtin` = newly generated unsupported
phase; `markdown_custom` = custom uploaded prompt; `markdown_legacy` = pre-migration/NULL only.

### Fallback layering

Catch the schema/render failure **inside the provider `run_fn`** so it never reaches
`_run_with_failover`, which does "classify → retry same" (verified). Auth, 429, slot-saturation,
timeout and network errors **escape normally**.

**Worst case is four model calls** (structured + schema retry, then markdown fallback which can
itself retry an empty response). "Never worse than today" is scoped to **import compatibility
only** — cost and latency are strictly worse on the fallback path.

### `prompt_hash` — provenance only

Resumption skips a done phase on **status + non-empty markdown** (`if phase_name in _done_md`); it
never compares `prompt_hash` (verified). **Decision: preserve never-pay-twice.**
`content_schema_version` / `renderer_version` are recorded as row provenance; version-aware
regeneration is out of scope. (The hash still gates cross-job *extract* reuse — unchanged.)

### Persistence

One alembic migration adds to `phase_outputs`: `content_json` (JSONB), `authoring_mode` (text + DB
constraint), `content_schema_version` (text), `renderer_version` (text) — all nullable; existing
rows read as `markdown_legacy`. No backfill.

**Numbering:** head is 0048; the model-config lane plans 0049 (not yet built). Ours must follow it —
re-check the head at implementation time.

## Export — locked

**Two components, both committed (no untracked scripts):**

1. **Pure payload builder** (`app/services/platform_payload.py`) — job → complete ingest envelope.
   Pure and unit-testable, no I/O.
2. **Operator CLI** (`scripts/ingest_to_platform.py`) — posts to
   `POST {PLATFORM_BASE_URL}/api/v1/library/homework-imports/ingest`.
   **`--dry-run` is the default**; posting requires an explicit flag.

#### Token semantics — server list, client singular

These are **not** the same variable, and conflating them breaks authentication.

The server splits `LIBRARY_INGEST_TOKEN` on commas into an *acceptance list*, but compares the
**entire** presented Bearer value against each entry:

```python
presented = parts[1]
return any(hmac.compare_digest(presented, token) for token in configured)
```

So a client reading a comma-joined value and sending `Authorization: Bearer old,new` matches
**neither** `old` nor `new` — auth fails.

| side | variable | semantics |
|---|---|---|
| Platform server | `LIBRARY_INGEST_TOKEN` | comma-separated **acceptance list** (rotation window) |
| HCGA client | **`PLATFORM_INGEST_TOKEN`** | **exactly one token** |

Client guards, enforced **before any HTTP request**: reject a token containing a comma, reject blank
— and reject **whitespace**, because the server does `parts = header.split()` and requires
`len(parts) == 2`, so an embedded space fails too.

`PLATFORM_BASE_URL` is a separate required setting. An admin JWT remains an alternative server-side
auth path but is not used by this CLI.

**Rotation test:** server configured with `old,new`; a client presenting **only** `new` succeeds.

**Envelope fields:** `source="hcg"`, `source_ref = book.id`, `external_key = job.id`, `language`,
platform `subject_id` + `grade`, and `phases[]` as a **list**:

```json
{"phase_name": "practice-rlc", "output_md": "...", "content_json": {},
 "content_schema_version": "rlc_config@1", "authoring_mode": "structured",
 "judge_status": "ok"}
```

**Subject mapping — fully specified:**

- **Lives in** one config file, path from env (`PLATFORM_SUBJECT_MAP`), JSON.
- **Keyed by canonical HCGA subject code** (`app/services/subjects.py` — e.g. `history`,
  `math-algebra`), value = platform `subject_id` (positive int).
- **Environment-specific** — platform IDs differ per deployment, so the map is config, never
  committed constants.
- **Injected into the pure builder** as a plain dict argument; the builder does no file or env I/O
  (keeps it unit-testable).
- **Fails before any HTTP request** on a missing key, malformed JSON, or a non-positive/non-integer
  ID.
- **Operator-inspectable** — the CLI exposes a `--check-map` mode that prints the resolved mapping
  and validates every entry without posting.
- **UUIDs are serialized explicitly as strings** (`source_ref`, `external_key`) — never raw UUID
  objects.

Only `status='done'` jobs and `done`, non-empty phase rows export.

`scripts/export_homeworks.py` (untracked, emits `phases` as a **dict** where the platform iterates a
**list**) is superseded and not part of this design.

## Testing

`parse(render(cfg)) == cfg` is **not** a valid invariant — the markdown parsers deliberately derive
defaults, drop answer-bearing sections and downgrade unsupported shapes. Two independent gates:

1. **Structured gate** — `content_json` passes the platform's live write validators, redactors,
   graders and the mobile contract.
2. **Markdown gate** — `render_md` output stays accepted by every current markdown consumer.

Plus:
- **Unit** — schema round-trip; renderer golden files; `extra="forbid"` rejects unknown keys.
- **`min_chars` bounds** — negative, zero, boolean, and excessive values all rejected.
- **Semantic scrub** — planted answer markers in RLC option/chip labels, step titles/prompts and
  sentence passages never reach the student payload; an unknown dialect yields `needs_review`.
- **Scrub-then-revalidate** — a label consisting entirely of an answer marker scrubs to empty and
  must route to `needs_review`, never publish.
- **Sentence-fill guards** — `mode != "word_bank"` rejected; duplicate answers or duplicate bank
  entries rejected; empty strings rejected.
- **Projection** — a planted extra `answer_key` never reaches the student payload.
- **Atomicity** — judge-regen and solver-regen produce a consistent artifact (RED-proved by
  mutating one path to return a bare string).
- **Fallback typing** — a schema failure falls back; a 429/auth error does **not** (RED-proved).
- **Version pinning** — unknown `content_schema_version` ⇒ `structured_invalid` + `needs_review`.
- **Payload builder** — envelope shape, subject-ID mapping error on miss, done-only filtering.
- **Token guards** — a `PLATFORM_INGEST_TOKEN` containing a comma, whitespace, or blank is rejected
  before any HTTP call; rotation test (server `old,new`, client presents only `new`) succeeds.
- **Post-scrub uniqueness** — RLC option/chip labels that collapse to duplicates or empty after
  scrubbing route to `needs_review`.
- **Mobile parity** — `minChars` from config is enforced client-side and matches server grading.
- **Acceptance** — one real lesson × 2 phases over `transport=api`, then the full platform path:
  ingest → sanitization → transform → publish validation → student-facing redaction. Expect
  `native` for both phases, and assert the precise (achievable) privacy property below.

### The privacy property, stated correctly

"No answer text in the student payload" is **impossible by construction** — a word-bank exercise
must expose its answer vocabulary as selectable chips, and RLC must render every option including
the correct one. The correct assertion is:

> No **hidden answer arrays**, **correctness flags** (`is_correct`), **consequences**,
> **acceptable keywords**, or **correctness-identifying markers** in visible text reach the student
> payload.

Word-bank entries and unlabeled options are expected and correct.

## Out of scope

- The ~34k existing phase outputs (no migration — user decision).
- `practice-tictactoe` — `ttt_config` absent from `PRACTICE_ARC_GAME_KINDS`; mobile `Ttt.tsx` takes
  no config (static `buildItems(t)`). Needs a React Native rewrite — separate lane.
- `case-based-preview` — pass 2, pending validation against `validate_cbp_config`.
- The remaining 7 phases.
- Version-aware regeneration of already-done phases.
