# SVG → described-placeholder — design spec

**Date:** 2026-06-06
**Status:** awaiting user review
**Branch:** Nggaev-v2

## Problem

Almost every generated homework embeds inline `<svg>` diagrams. Root cause is
prompt design, not a code bug — SVG is presented as *the* visual mechanism across
the whole prompt stack, with no "default to none" gate, so the model adds a
diagram nearly every time it judges one "would help" (which is almost always).

Three reinforcing layers drive it:
1. The "## Output format" footer in 10 of 11 `prompts/_general/*.md` says
   *"For visuals: emit inline `<svg>` for diagrams."* (ungated, every phase).
2. `prompts.py` `FAMILY_RULES` — 10 family blocks (5 CBP + 5 flashcards) whose
   "Visual policy" paragraphs actively push SVG (*"Math defaults to SVG," "every
   figure, angle, or construction is SVG"*).
3. `agent.py` injects an ~80-line `_SVG_RULES` styling block into 9 phases
   (`_SVG_PHASES`), normalising SVG as expected output.

## Decision (locked with user)

The AI must **never emit inline `<svg>` or raster image markup.** For *any* visual
— diagram or photo — it emits a single **described placeholder** that says what the
visual should depict, in enough detail that a downstream image-gen step or a human
can produce it blind.

**Placeholder format (approved):**
```
![visual: <diagram|photo> — <what to depict, with every label/value/axis> — image gen required](placeholder)
```
- `<diagram|photo>` — names the medium.
- `<what to depict …>` — the subject plus **every** label, value, axis, and part,
  so the description is self-sufficient.
- `](placeholder)` — fixed sentinel `src` the frontend matches on to render a card.

**`visual_svg` field — N/A.** Confirmed there is no structured `visual_svg` field in
current code (Effort A removed the per-phase structured schemas; all content phases
are markdown / `output_md`). The original "repurpose it" decision is moot. No schema
change, no DB migration.

## Surfaces to change

### 1. `prompts/_general/*.md` — output-format footer (10 files)
boss-arena, case-based-preview, flashcards, memory-check, practice-error-detection,
practice-jigsaw, practice-memory-match, practice-rlc, practice-sentence,
practice-tictactoe (reflection.md has no footer — skip).

Replace the footer line
> For visuals: emit inline `<svg>` for diagrams; for a photo/raster you would
> otherwise need to generate, emit `![placeholder: …](placeholder)` …

with a single placeholder instruction:
> For ANY visual (diagram OR photo), do NOT emit `<svg>` or image markup. Emit a
> described placeholder: `![visual: <diagram|photo> — <what to depict, with every
> label/value/axis> — image gen required](placeholder)`. Never fabricate an image,
> never invent an image URL, never output raw `<svg>`.

### 2. `prompts/_general/*.md` — standalone "## Visuals" sections
Audit each body for inline-`<svg>` instructions and flip them to the placeholder
rule. Known: `boss-arena.md` (## Visuals), `practice-rlc.md` (## Visuals),
`practice-error-detection.md` (## Visuals + "Labelled diagram" type),
`case-based-preview.md` (inline-svg mention in the blocks section). Re-grep
`svg` per file during execution to catch any others.

### 3. `app/services/prompts.py` — FAMILY_RULES (10 blocks)
Rewrite the **Visual policy** paragraph in each of: `_CBP_SCIENCES`, `_CBP_MATH`,
`_CBP_LANGUAGES`, `_CBP_HUMANITIES`, `_CBP_DEFAULT`, `_FC_SCIENCES`, `_FC_MATH`,
`_FC_LANGUAGES`, `_FC_HUMANITIES`, `_FC_DEFAULT`. Keep the family's *medium intent*
(which visuals matter for that subject) but state it as **what the placeholder
should describe**, not "emit `<svg>`". Drop every "Use inline `<svg>` for …" and
"defaults to SVG" phrasing; the medium tag (`diagram` vs `photo`) now lives inside
the placeholder. Leave **Case framing** and **Avoid** paragraphs intact except for
removing SVG-specific forbids ("decorative SVGs", "SVGs with tiny labels") which
fold into a generic "decorative visuals that don't carry the concept".

### 4. `app/services/agent.py` — runtime injection
- Delete `_SVG_RULES` (lines ~129–206) and the `_SVG_PHASES` gate that appends it
  (`_build_master_prompt`, line ~527).
- Replace with a small `_PLACEHOLDER_RULES` block (the format + "describe every
  label/value/axis; never emit `<svg>` or raster") injected for the **same content
  phases** (or unconditionally for all non-extract phases — simpler, decide in plan).
- `_SVG_PHASES` set: remove or repurpose. The stale comment referencing
  `LearningBlock.visual_svg` goes away.
- `_strip_svgs` (prior-output scrub): leave functionally (harmless once nothing
  emits `<svg>`), but the placeholder replacement text can stay `[diagram omitted]`.

### 5. `web/src/components/rich-text.tsx` — render the placeholder
Add an `img` component override to the `ReactMarkdown` `components` map that detects
`src === "placeholder"` (or alt starting `visual:`/`placeholder:`) and renders a
styled inline card — e.g. a bordered box with an image glyph and the alt text as
"Visual needed: <description>" — instead of a broken `<img>`. This also fixes the
*already-broken* raster placeholders rendered today. Single-file, no new dep.

## Non-goals / explicitly out of scope
- No actual image generation. Placeholders are inert; producing the image is a
  later/separate concern.
- No schema or DB changes (no `visual_svg` exists).
- Subject prompt dirs (`prompts/<subject>/`) are dead (`USE_SUBJECT_PROMPTS=False`)
  — not edited.
- `codex.py` / `kimi.py` `prompt_suffix` ("SVG inline") — update for consistency in
  the same pass (small), or note as a follow-up.

## Risks
- **Description quality.** A placeholder is only useful if richly described. The
  prompt must force medium + every label/value/axis. Acceptance smoke must inspect a
  real placeholder for completeness, not just absence of `<svg>`.
- **Judge interaction.** The LLM phase judge (`phase_judge.py`) grades against the
  prompt contract; once the contract says "placeholder, not SVG", a stray `<svg>`
  becomes a judge-flaggable miss — desirable, but watch for false majors during the
  first runs.
- **Geometry loss.** Geometry/figures that genuinely teach via a diagram now ship as
  a described placeholder until image-gen exists. Accepted per user decision.

## Acceptance (CLAUDE.md gate: real CLI smoke)
1. Real generation of a math/geometry lesson (highest SVG demand) — assert the
   output contains **zero** `<svg>` and at least one well-formed
   `![visual: … — image gen required](placeholder)` whose description names the
   medium and the labels/values.
2. A sciences flashcards run — same assertions.
3. Frontend: a job page renders the placeholder as a card (manual check or a
   `rich-text` render test), no broken image icon.
4. Full suite green (`uv run python -m pytest tests/ -q`).
