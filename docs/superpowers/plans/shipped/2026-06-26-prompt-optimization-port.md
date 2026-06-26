# Prompt Optimization Port — Class-A Infra_prompts → `_general` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. One subagent per task; controller stress-tests every commit (read diff + render-check). Steps use `- [ ]`.

**Goal:** port the genuinely-portable optimizations from `s1gmamale1/Class-A-Education-Development` `docs/Infra_prompts` into our live runtime prompts, as surgical enrichments — NOT a wholesale replace.

**Architecture:** locked — keep our collapsed `{{SUBJECT}}` single-file-per-phase design. Per-family content lives in `prompts.py` `FAMILY_RULES` (`_CBP_*`/`_FC_*`, injected at `{{FAMILY_RULES}}`); language rules in `LANGUAGE_RULES` (`_LANG_UZBEK`/english/russian, injected at `{{LANGUAGE_RULES}}`). No new phases, no architecture change.

**Tech Stack:** `prompts/_general/*.md` + `app/services/prompts.py`; verified by a render-check (no LLM) + representative real CLI/api generation smokes.

---

## Approach & key decisions

- **This is a surgical port, not a replace** (verified via 5 parallel reviews of all source files mapped to our 11 phases). The source files are production specs for a *different* pipeline: JSON I/O, "retrieve textbook from address", scoring economies (HP/damage, 100/300-pt, XP), runtime adaptation engines, and — for the 4 "game" files — wholesale **CBP-case expansions** (MCQ checkpoints + learning blocks + consequence panels) that our prompts explicitly forbid. Our `_general` prompts + `prompts.py` injection are already a tighter distillation of the same lineage. A literal replace would regress us (break the markdown-only/judge contract, re-introduce SVG, bloat 3-4KB prompts). **DROP across all phases:** JSON schemas/`*_id`/Bloom-PISA tags, scoring economies, runtime/telemetry adaptation, pool-size minimums, "retrieve from address", per-family pinning enums, "position in flow" prose, and (critically) the `diagram_svg`/"SVG required" fields — our contract is described-placeholder-only.
- **Confirmed bug fix (high value):** `flashcards.md:11-16` sizes the deck off `Mode: Easy or Hard`, but `Mode` is never substituted (`get_prompt` only does `{{SUBJECT}}`/`{{LANGUAGE_RULES}}`/`{{FAMILY_RULES}}`) and the pipeline pins `difficulty=None` (classify removed, 0067). The source's grade-band deck-size table both fixes the dead variable and optimizes sizing.
- **Three edit surfaces:** (1) `prompts/_general/*.md` shared structural body; (2) `prompts.py` `_CBP_*`/`_FC_*` family blocks (per-family visual/fidelity for CBP + flashcards); (3) `prompts.py` `_LANG_UZBEK` (language register/simplify/gloss rules). The Uzbek source doc is marked *"boss-review, NOT ratified production"* → port the register/simplify/gloss INTENT; do NOT over-engineer unproven per-subject guardrails.
- **Sentence Filling = keep our cloze** (user-locked). The source "Sentence Filling" is a different game (error-detection/repair, English-scoped) that would duplicate our `practice-error-detection` phase. Apply only a minor distractor-flavor tweak.
- **Acceptance = render-check (free, all phases) + representative real generation smokes** (flashcards, case-based-preview, one practice game). Generation-affecting per CLAUDE.md, so at least some real CLI/api calls; kept minimal per the no-mass-gen money rule. Prompt edits change `get_prompt_hash` → prior cached phases won't be reused on next run (expected, no migration).
- **Verified facts (against tip `1437221`):** `get_prompt` substitutes the 3 tokens (`prompts.py:367,370,374`); `LANGUAGE_RULES` keys = english/russian/_default(=`_LANG_UZBEK`) (`:51`); `FAMILY_RULES` = {case-based-preview, flashcards} × {sciences,math,languages,humanities,_default} (`:303`); flashcards dead-Mode at `flashcards.md:11-16`.

### Conventions
- **Worktree/branch:** `feat/prompt-optimization-port` at `../HCGA-prompt-port` (cut off `origin/Nggaev-v2` tip `1437221`). All work here.
- **Commit prefix:** `prompts:`. **Stage only each task's files.**
- **Worklog ID:** next-free at finish (verify live tip; **0089 taken by the C5 resilience merge → likely 0090**, reconcile at merge).
- **No migration.**

---

## Shared render-check (used by every task's verification + the final gate)

Create once in Task 0, reused thereafter. `scripts/check_prompt_render.py`:
```python
"""No-LLM render gate: every (subject, phase) renders with no leftover {{...}} tokens.
Run: uv run python -m scripts.check_prompt_render"""
from app.services.flows import flow_for
from app.services.prompts import get_prompt
import re, sys

# representative subjects spanning all families
SUBJECTS = ["matematika", "biologiya", "tarix", "ingliz-tili", "ona-tili"]
# Derive phases from the live flow (robust to future phase changes); extract has
# no _general prompt file, so skip it.
PHASES = [p for p in flow_for(SUBJECTS[0]) if p != "extract"]
bad = []
for s in SUBJECTS:
    for ph in PHASES:
        body = get_prompt(s, ph)
        leftover = re.findall(r"\{\{[A-Z_]+\}\}", body)
        if leftover:
            bad.append(f"{s}/{ph}: leftover {leftover}")
if bad:
    print("FAIL:\n" + "\n".join(bad)); sys.exit(1)
print(f"RENDER OK: {len(SUBJECTS)*len(PHASES)} (subject,phase) combos, no leftover tokens")
```
**Commit (Task 0):** `prompts: add no-LLM render gate for {{...}} token resolution`. Run it now to capture the GREEN baseline before edits.

---

## Task 1 — Flashcards (bug fix + grade-band + provenance + union types) + `_FC_*` fidelity

**Files:** `prompts/_general/flashcards.md`, `app/services/prompts.py` (`_FC_SCIENCES/_MATH/_LANGUAGES/_HUMANITIES/_DEFAULT`)
**Source (re-fetch for exact wording):** `Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_{math_family,sciences,humanities,languages}.md` + `flashcard_study_engine_documentation.md`

- [ ] **Edit `flashcards.md`:**
  - REPLACE the `Mode: Easy or Hard` block + `Easy: 5-8 / Hard: 8-12` (lines ~11-16) with grade-band sizing: **G5-6 → 6-8 cards** (core atoms, plainest wording); **G7-8 → 8-10** (+1 misconception card); **G9-11 → 10-12** (full atom set, subtler distinctions). State "grade scales retrieval load, never source accuracy." (Fixes the dead-Mode bug.)
  - ADD a **provenance** rule: mark common-mistake/misconception cards as `source` (textbook states it) vs `inferred`; never present an inferred misconception as textbook-stated. Mirror in the self-check.
  - EXPAND the canonical `type` list to the union, subject-gated by prose: keep the six, add `formula` (math/science) and `grammar`/`vocabulary` (language lessons).
  - KEEP our explicit "**No minimum**" on front/back — do NOT import the source 3-14/5-22 minimums.
- [ ] **Edit `_FC_*` family blocks** (`prompts.py`): fold each family's subject-fidelity guardrails from the matching source family prompt — Math: don't change numbers/variables/formulas/units/calc-order, no word-problem cards. Sciences: physics answers carry units (`F=10 N` not `F=10`), chemistry equations balanced, biology no numeric-calc cards. Humanities: no invented causality (use textbook's causal language), quote primary sources exactly or mark paraphrase, geography stats carry a year, no anachronism. Languages: never exceed grade/CEFR even in examples, false-friend cards carry a `misconception`. Keep each block tight (it's injected verbatim). Do NOT import SVG-default wording — keep our placeholder-only visual line.
- [ ] **Verify:** `uv run python -m scripts.check_prompt_render` (GREEN); manually diff that no JSON/SVG/retrieve-from-address leaked in; confirm flashcards.md no longer mentions `Mode:`/`Easy:`/`Hard:` deck sizing.
- [ ] **Commit:** `prompts: flashcards grade-band sizing (fix dead Mode var) + provenance + _FC_ fidelity`

---

## Task 2 — Case-Based Preview (distractor + anti-leak + feedback + DPE rubric) + `_CBP_*` visual

**Files:** `prompts/_general/case-based-preview.md`, `app/services/prompts.py` (`_CBP_*`)
**Source:** `Case-Based Preview/nets_cbp_prompt_{math_family,sciences,humanities,languages}.md` (the EVOLVED truth; the `..._standard_v1.md` is stale on the DPE axis — ignore its no-DPE structure).

- [ ] **Edit `case-based-preview.md`:**
  - ADD common-mistake distractor rule: "each checkpoint has 3-4 options, exactly one correct; **at least one distractor must be the lesson's common mistake**; keep all options similar in length/format so the answer can't be guessed from shape."
  - ADD grade-band reasoning-load guidance (grade-agnostic prose, no grade template var): lower grades → one concrete familiar context, obvious distractors, short guided DPE; upper grades → layered context, subtle apply-the-rule distractors, fuller DPE weighing the rejected option. "Difficulty scales reasoning load only — never numbers/formulas/dates/source facts."
  - SPECIFY the feedback summary as 4 parts: understood / mistake appeared / what to review / status (`passed` | `Needs Retry`); Completion uses "Needs Retry", never bare "Not Completed".
  - ADD a DPE rubric scaffold: `Expected components: concept · method · mistake` + a Full/Partial/Retry line (keep the existing not-auto-passed note). Mirror new "must"s in the self-check so generator + judge read the same bar.
  - DROP the flashcards-term-matching hard rule (CBP is an early phase, usually no flashcards dep) → at most "use the lesson's canonical terms consistently."
- [ ] **Edit `_CBP_*` family blocks** (`prompts.py`): refine each from the matching source family prompt's visual/case-type intelligence — math → diagram placeholder for figures/fractions/graphs; sciences → photo for lab/organism + diagram for mechanism; humanities → photo scene + diagram for timeline/map/causal-chain; languages → photo scene + diagram for sentence/tense structure. Described placeholders only (never SVG).
- [ ] **Verify:** render-check GREEN; diff for no JSON/SVG/retrieve-from-address/listening-mode leakage.
- [ ] **Commit:** `prompts: CBP common-mistake distractor + 4-part feedback + DPE rubric + _CBP_ visual`

---

## Task 3 — Error Detection (per-pattern block counts + anti-patterns; drop SVG trap)

**Files:** `prompts/_general/practice-error-detection.md`
**Source:** `Gamified Practices/Error Detection/Error_Detection_Specification.md`

- [ ] **Edit:**
  - FIX block-count guidance to be per-pattern (ours conflates): equation steps G1-4=3 / G5-8=4-5 / G9-11=5-6; sentence blocks 3-4 / 4-5 / 5-6; diagram labels 4 / 5-6 / 6-8.
  - ADD "no time pressure / no speed scoring — this is recognition + construction."
  - ADD a one-line guard against over-strict exact-match rejection (pair with the existing accepted-variants note).
  - Optionally add one compact non-math example (e.g. "She have been → went").
  - **DROP / do NOT port the `diagram_svg` field** (source §10) — it violates our placeholder-only rule. Drop the 100-pt scoring, JSON schema, telemetry.
- [ ] **Verify:** render-check GREEN; confirm no `svg` token entered the file.
- [ ] **Commit:** `prompts: error-detection per-pattern block counts + anti-patterns (no SVG)`

---

## Task 4 — Real-Life Challenge (confidence-pattern feedback steer)

**Files:** `prompts/_general/practice-rlc.md`
**Source:** `Gamified Practices/Real Life Challenge/Real_Life_Challenge_Specification.md`

- [ ] **Edit:** ADD a short confidence-meaning steer (2-3 lines, framed as "how to color feedback", NOT points): Sure+wrong → flag the confident misconception in the Wrong feedback; Guess+correct → don't over-praise (lucky recall ≠ mastery). Optionally add one compact worked example (cyanosis → low oxygen → cellular respiration) + a one-line "decoration ≠ learning evidence". DROP the 300-pt scoring, creative/incomplete-info variants, JSON.
- [ ] **Verify:** render-check GREEN.
- [ ] **Commit:** `prompts: RLC confidence-pattern feedback steer + worked example`

---

## Task 5 — Small practice-game distractor enrichments (memory-match, jigsaw, tictactoe)

**Files:** `prompts/_general/practice-memory-match.md`, `practice-jigsaw.md`, `practice-tictactoe.md`, `practice-sentence.md`
**Source:** matching `Gamified Practices/{Memory Matching, Jigsaw Matching, TicTacToe, Sentence Filling}/*.md`

- [ ] **memory-match:** extend the pair-type list with the 4 extras — `symbol↔rule`, `word↔correct-usage`, `historical event↔consequence`, `formula-part↔quantity`. Do NOT import the hidden-card-reconstruction/recall-summary flow (UI runtime). *(Highest-value of this group.)*
- [ ] **jigsaw:** expand "tempting wrong pairings" to name the 3 flavors — surface-but-unsupported / one-right-one-wrong-node / reversed-pair.
- [ ] **tictactoe:** add "plausible-but-incomplete" and "surface-clue-only" to the wrong-cell flavor list. KEEP our multi-select model; reject the source's single-best-MCQ framing + state-meters.
- [ ] **sentence (cloze KEPT):** add "reversed/opposite cause-effect connector" + "opposite meaning" to the distractor reasons. No mechanic change.
- [ ] **Verify:** render-check GREEN.
- [ ] **Commit:** `prompts: practice-game distractor/pair-type enrichments (memory-match/jigsaw/tictactoe/sentence)`

---

## Task 6 — Low-touch (boss-arena, memory-check, reflection)

**Files:** `prompts/_general/boss-arena.md`, `memory-check.md`, `reflection.md`
**Source:** `Gamified Practices/Boss Arena/*`, flashcard Test-mode spec, `Flow/New_Flow.md` (reflection section).

- [ ] **boss-arena:** soft grade→question-count steer ("lean to 4 for early grades, 6 for senior"; keep the 4-6 range) + ONE compact worked Why→How→What example + name the counterfactual "What" ("what would change if…"). DROP HP/damage/combo scoring, the 15-pool minimum, JSON, MCQ-scaffolding exception (keep our stricter open-reasoning ban).
- [ ] **memory-check:** add grade-calibrated distractor subtlety (obvious → near-miss → rule-dependent) + "avoid two consecutive items of the same kind when possible". **KEEP our per-item feedback** — do NOT import the source "no feedback during the test" rule.
- [ ] **reflection:** add the 2 missing questions ("Why did you make your main decision?", "What mistake would you avoid next time?") to the rotation + an explicit next-step-suggestion beat + the "Needs Retry" / never-bare-"Not Completed" terminology. KEEP our no-invented-score stance.
- [ ] **Verify:** render-check GREEN.
- [ ] **Commit:** `prompts: boss worked-example + memory-check grade distractors + reflection questions/terminology`

---

## Task 7 — Cross-cutting Uzbek language rules (`_LANG_UZBEK`)

**Files:** `app/services/prompts.py` (`_LANG_UZBEK`; touch english/russian only if a clearly-shared rule)
**Source:** `Uzbek Specification/NETS_Uzbek_Language_Foundation_Review.md`

- [ ] **Edit `_LANG_UZBEK`** to add the language-quality INTENT (not the JSON/validator machinery): formal **Siz** register; forbid `sen/san`, mixed Siz+informal, casual `-yapti/-iyapti`; "simplify the wording around the subject, not the subject itself" — never change formulas/numbers/units/dates/answer-logic; avoid Russian/English calques; split long sentences logically (no robotic chopping, no childish/slang); preserve subject terms + add simple glosses for hard terms; one homework must not mix apostrophe styles. Keep it tight (injected into all 11 prompts). DROP the field/validator/JSON/QA-pipeline framing and unproven per-subject confidence levels.
- [ ] **Verify:** render-check GREEN (the block renders into all 11 prompts for `_default`/uz subjects).
- [ ] **Commit:** `prompts: port Uzbek language-foundation register/simplify/gloss rules into _LANG_UZBEK`

---

## Task 8 — Acceptance (render gate + representative real smokes + suite)

- [ ] **Render gate:** `uv run python -m scripts.check_prompt_render` → RENDER OK (all combos, no leftover tokens).
- [ ] **Existing tests:** `uv run python -m pytest tests/ -q` — green. If any test asserts specific prompt content (e.g. a get_prompt/render test), update it to match the ported text (note which).
- [ ] **Representative real generation smokes** (minimal, per no-mass-gen rule): copy `.env` into the worktree, then run an in-process single-phase generation via `agent.run_phase_prompt` (gemini api or claude cli) with a small real `lesson_context` for **3 representative phases — flashcards (bug fix), case-based-preview (biggest delta), one practice game** — and eyeball that output is well-formed markdown honoring the new rules (grade-sized deck, common-mistake distractor, no SVG, no JSON). Write `scripts/smoke_prompt_port.py`. Paste outputs.
- [ ] **Commit:** `prompts: prompt-port acceptance smoke (render gate + representative generation)`

---

## Finish (controller — after gate merges)
- Rebase-check on `origin/Nggaev-v2`; re-run render gate + suite.
- Worklog (next-free, verify live tip) + INDEX row; `git mv` plan → `plans/shipped/`.
- De-stale `docs/CODE_MAP.md` / `HOW_IT_WORKS.md` only if they describe prompt content/structure (likely a light note that `_general` + `prompts.py` FAMILY_RULES/LANGUAGE_RULES carry the ported optimizations). No DEPLOY/DATABASE change.
- WISHLIST: note the flashcards dead-Mode bug fix.

## Self-review
- **Coverage:** all 11 phases + the 2 `prompts.py` injection surfaces covered; each high/medium-value delta from the 5 reviews mapped to a task. Sentence kept as cloze per lock.
- **Type consistency:** edits are prose; the only code-shaped change is `_LANG_UZBEK`/`_FC_*`/`_CBP_*` string constants (already exist). No signature changes.
- **Placeholders:** none — each task lists concrete deltas + DROP list + exact files + verify command.
- **Risk control:** render gate guards token resolution on every task; DROP lists prevent SVG/JSON/scoring leakage; real smokes guard generation quality on the highest-delta phases.
