# Effort B — Prompt Rewrite to Infra Specs (markdown, family-aware)

**Status:** Design approved — ready for writing-plans.
**Date:** 2026-06-04
**Branch:** Nggaev-v2

## Goal

Rewrite the `prompts/_general/*.md` phase prompts to be faithful to the
`docs/Infra_prompts/` specs, in a markdown-emitting, family-aware form — and purge
the dead JSON-schema vocabulary that Effort A left behind.

## Background / motivation

Effort A flipped generation so every phase emits one markdown file (no JSON, no
schema, no assembly). It bolted a "Respond in Markdown only" instruction onto each
prompt but did **not** rewrite the prompt bodies — so today every spec'd prompt is
internally contradictory: it says "markdown only" while still speaking in JSON
field-names (`options: null`, `eval_mode`, `min_chars`, `source_concept_ids`,
`interaction_payload`, `chips[]`, …). The `docs/Infra_prompts/` specs (written
earlier, in a prompt-like voice the team only later recognized) are the intended
source of truth for phase content, and they vary by **subject family**, which the
current single `_general` prompt set does not express.

Effort B makes the prompts match the specs, family-aware, markdown-native.

## Decisions locked (during brainstorm)

1. **Tiered scope** — deep rewrite of the two family-split phases (CBP, Flashcards);
   a calibrated pass on the rest (severity set by the drift audit below). Not a
   uniform 11-file rewrite.
2. **Pedagogical fidelity** — port each spec's *content rules, structure, family
   deltas, anti-leak/sourcing rules* into a markdown-emitting prompt. **Drop** the
   spec's JSON data shape, scoring/retry mechanics, and the five interactive
   flashcard games — those are runtime/student-app concerns and `web/` is an
   operator content console, not a student app (out of scope).
3. **Author a humanities CBP variant** — CBP ships specs for sciences/math/languages
   but no humanities file; history is a live subject, so we author the 4th variant
   by extrapolating from the 3 CBP specs + the existing humanities **flashcard**
   spec (which gives the humanities visual policy).
4. **Option A — dict-backed `{{FAMILY_RULES}}` token** in `prompts.py`, mirroring the
   existing `LANGUAGE_RULES` pattern (family blocks as named string constants, not
   separate files).
5. **HEAVY-4 games stay compact** — the memory-match / tictactoe / jigsaw / sentence
   specs are written as full Case-Based Preview cases (3 checkpoints + learning
   blocks + DPE + consequence). Our pipeline already has a dedicated CBP phase, so we
   deliberately **do not** balloon these four into CBP cases; we keep them compact and
   port only their content rules. Following the spec literally would give every packet
   5 CBP-shaped phases — redundant and bloated.

## Architecture

### Injection mechanism (one new token)

`get_prompt(subject, phase_name)` already does literal token-replace for `{{SUBJECT}}`
and `{{LANGUAGE_RULES}}`. We add exactly one token, `{{FAMILY_RULES}}`, resolved the
same way:

```
subject ──► _SUBJECT_FAMILY ──► family key ──► FAMILY_RULES[phase][family] (else _default)
```

- **`_SUBJECT_FAMILY`** (new constant in `prompts.py`):
  - `biology`, `kimyo-g7-11`, `physics` → `"sciences"`
  - `math-algebra`, `geometriya-g7-11` → `"math"`
  - `english` → `"languages"`
  - `history` → `"humanities"`
- **`FAMILY_RULES`** (new nested constant): `{phase_name: {family: block_str, "_default": block_str}}`.
  Only `"case-based-preview"` and `"flashcards"` have entries. Each carries all four
  family blocks plus a `_default`.
- In `get_prompt`, after the `{{LANGUAGE_RULES}}` replace, add a `{{FAMILY_RULES}}`
  replace that fires **only when the placeholder is present** in the body. The nine
  phases without the token are untouched by the mechanism (byte-identical output for
  them, pre/post change).

### Critical invariant — no cross-family leak

Mirrors the existing `_resolve_model` leak-guard. A subject with no entry in
`_SUBJECT_FAMILY`, or a phase with no block for the resolved family, must fall to the
phase's own `_default` block — it must **never** silently inject another family's
rules. Unit-tested.

### What does NOT change

CLI router, pipeline, validator wiring, Notion archive, output format (already
markdown), DB schema. Prompts are pure content: editing them takes effect on server
restart (startup prompt cache). Zero migration.

## Components (files touched)

### Code — the only code change

**`app/services/prompts.py`**
- Add `_SUBJECT_FAMILY` map.
- Add `FAMILY_RULES` nested dict of named block constants (CBP ×4 families + `_default`;
  Flashcards ×4 families + `_default`).
- Extend `get_prompt` with the `{{FAMILY_RULES}}` replacement (placeholder-gated,
  `_default` fallback, leak-guard).

### Prompt files (pure content, no migration)

| Tier | Files | Action |
|---|---|---|
| **1 — deep, family-aware** | `_general/case-based-preview.md`, `_general/flashcards.md` | rewrite to spec as **base + `{{FAMILY_RULES}}` block**; 4 family blocks each live in `prompts.py` (humanities CBP authored) |
| **2a — light** | `_general/memory-check.md`, `practice-rlc.md`, `practice-error-detection.md`, `boss-arena.md` | strip dead JSON vocab; add omitted spec rules |
| **2b — compact + clean** | `_general/practice-memory-match.md`, `practice-tictactoe.md`, `practice-jigsaw.md`, `practice-sentence.md` | strip dead JSON vocab; port content rules; stay compact (no CBP-case balloon) |
| **2c — polish** | `_general/reflection.md` | `#` output title, explicit markdown-only, all-sections gate |

### Drift audit (basis for the tier severities)

| Phase | Severity | Why |
|---|---|---|
| case-based-preview | TIER 1 | family-split rewrite; strip schema talk; visual policy into family block |
| flashcards | TIER 1 | family-split rewrite; canonical `type` enum; per-family card atomisation |
| memory-check | LIGHT | dead JSON vocab; distractor-misconception depth; "all 3" vs "≥2 of 3" bug |
| practice-rlc | LIGHT | dead JSON vocab; Strip Test rule omitted |
| practice-error-detection | LIGHT | dead JSON vocab; Strip Test + real-mistake emphasis |
| boss-arena | LIGHT | minor JSON vocab; weak-skill adaptation rule missing |
| practice-memory-match | COMPACT+CLEAN | spec is full CBP case; keep compact, port rules, strip JSON vocab |
| practice-tictactoe | COMPACT+CLEAN | same |
| practice-jigsaw | COMPACT+CLEAN | same |
| practice-sentence | COMPACT+CLEAN | same |
| reflection | POLISH | output starts at `##` (validator flags); add `#`, markdown-only, gate |

## Content rules & bug-fixes (baked into the rewrites)

- **Dead-vocabulary purge** (all 8 spec'd prompts): remove every JSON field-name
  reference; prompts speak in markdown sections, not JSON fields.
- **CBP**: drop `source_concept_ids` / `eval_mode` / `min_chars` schema talk; keep the
  3-checkpoint → learning-block → DPE → final-simulation pedagogy; visual policy moves
  into the family block:
  - sciences → IMAGE default (real labs/organisms/phenomena), SVG for particle/process
  - math → SVG default (figures, fraction bars, graphs, state diagrams)
  - languages → IMAGE default (communication settings/dialogue)
  - humanities → SVG default (timelines, causal chains, labelled maps), IMAGE for
    portraits/monuments/artifacts; causal claims trace to textbook; no anachronistic
    state names
- **Flashcards `type` enum bug**: reconcile to one canonical set across base + all
  family blocks; per-family card atomisation examples live in the family block.
- **memory-check bug**: "all 3 kinds" (≈L35) vs self-check "≥2 of 3" (≈L63) →
  reconcile to **≥2 of 3** in both places.
- **reflection `#` bug**: the prompt never instructs a top-level `#` in the *output*,
  so outputs start at `##` and the validator's `_has_top_heading` flags every
  reflection. Fix: instruct a `#` reflection title in the output.
- **Spec rules to re-add**: Strip Test (rlc, error-detection), weak-skill adaptation
  (boss-arena), distractor-misconception depth (memory-check).

## Out of scope

- JSON output of any kind.
- The five interactive flashcard games (Match/Write/Learn/Test/Memory Sprint) and
  their scoring/retry mechanics.
- Any student-play data shape; `web/` stays an operator console.
- Per-phase validator `RULES` content — those derive from the finalized prompts and
  come in a later effort. The Uzbek language contract (WS5) is likewise separate.
- The pre-existing red test `test_notion_defaults_disabled` (local `.env` leak,
  unrelated).

## Testing & verification

- **Unit (`tests/services/test_prompts_resolver.py`)**:
  - `{{FAMILY_RULES}}` resolves the correct block per subject for sciences / math /
    languages / humanities, for both CBP and Flashcards.
  - `_default` fallback for a subject not in `_SUBJECT_FAMILY`.
  - **Leak-guard**: an unmapped subject / a phase without a family block never injects
    another family's block (mirror the `_resolve_model` no-leak test).
  - Phases without the token are byte-identical pre/post change.
- **Coverage (`tests/services/test_prompt_coverage.py`)**:
  - Every `{{FAMILY_RULES}}` placeholder has a block for all four families + `_default`.
  - No unreplaced `{{...}}` token survives `get_prompt` for any (subject, phase) pair.
- **No-regression**: full suite green (`uv run python -m pytest tests/ -q`); the known
  pre-existing red stays out of scope.
- **Acceptance smoke** (CLAUDE.md requires a real CLI run for generation-affecting
  work): real CLI generation of **CBP + Flashcards** on a sciences book (kimyo) **and**
  a humanities book (history, to exercise the authored variant) — confirm markdown
  output, correct family visual policy, and zero JSON-field leakage.

## Risks / notes

- **Authored humanities CBP** is an extrapolation, not a verbatim spec port — flag it
  in the worklog so it can be reviewed against real history output during the smoke.
- **Deliberate spec divergence** on the HEAVY-4 games: we knowingly do not follow the
  v1.1 "Case-Based Preview Interaction Mode" framing. Record the rationale (avoid 5
  CBP-shaped phases per packet) so a future reader doesn't "fix" it back.
- **`prompts.py` size**: Option A puts ~8 multiline family blocks in the module. Keep
  each block injection-sized (not full-spec-sized) and grouped as named constants so
  the file stays readable.
