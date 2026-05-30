# Prompt: Practice Arc — Tic-Tac-Toe Decision Grid (Case-Based Preview interaction mode: Tic-Tac-Toe Decision Grid)

You are generating one **Case-Based Preview** practice game for this chemistry
(kimyo) lesson, skinned as a **Tic-Tac-Toe Decision Grid**: a 3×3 board of action
cells where the student must pick the cell that correctly applies the lesson's
classification, reaction check, or safe-handling rule.

**Emit `interaction_mode = "tictactoe"`** — the literal string, in every response.

The contract is the standard CBP contract: **EXACTLY 3 MCQ checkpoints**
(`identify` → `decide` → `justify_or_avoid_mistake`), then an open-ended
**Decision Process Explanation (DPE)** *after* checkpoint 3 and *before* the final
simulation, then a correct-vs-wrong simulation and a feedback summary. The grid
must be solvable **only through the lesson concept** — never general intuition,
common sense, or test-taking logic.

## What to produce

Emit the structured object the response schema requests (`CbpModeGame`,
extending `CaseBasedPreview`). Fill every field; invent no fields.

- `interaction_mode` — the literal `"tictactoe"`.
- `title` — names the lesson concept + decision grid framing.
- `student_role` — lab assistant / technician / checker / analyst / observer.
- `case_type` — e.g. `practical_decision_case` (safe classification / reaction
  check / handling step).
- `source_concept_ids` — list (>=1) of source-map concept IDs from THIS lesson.
  Do not invent; trace every grid cell back to one of these.
- `case_setup` — `{narrative, student_role, task}`. A 2–4 sentence role-based lab
  scenario where the student must choose a **safe classification, a reaction
  check, or a correct handling step** that follows the lesson rule. The decision
  must NOT be answerable by guessing — the chemistry rule is the only key.
  Describe the 3×3 decision board here (low-asset CSS/SVG), each cell an action.
- `checkpoints` — EXACTLY 3, in this order:
  1. `intent: "identify"`, `form: "mcq"` — **which source concept (rule /
     classification / reaction principle) controls this situation?** Options:
     correct concept · related-but-wrong concept · surface clue only · irrelevant
     detail. Set `correct_index`, write `feedback`.
  2. `intent: "decide"`, `form: "mcq"` — **the decision board.** "Qaysi katak
     amali bu vaziyatga eng mos?" The 4 options ARE the board action cells:
     correct action (applies the rule / safe step) · plausible-but-incomplete
     action · fast-but-unsafe action · surface/irrelevant action. Set
     `correct_index`. `feedback` may name state-meter shifts (e.g. Xavfsizlik +30,
     Xavf −20, Aniqlik +25). Distractors must be tempting to a real learner, not
     silly — and never an unsafe step dressed as obviously correct.
  3. `intent: "justify_or_avoid_mistake"`, `form: "mcq"` — **which explanation
     justifies the chosen action AND rejects the tempting weak strategy?**
     Options: correct explanation (rule + why the action is safe/right) · general
     intuition without the rule · tempting-but-incomplete step · wrong/unsafe step.
     Set `correct_index`, write `feedback`.

## Learning Blocks (required — slots 3 & 5)

Emit `learning_block_1` (after Checkpoint 1) and `learning_block_2` (after Checkpoint 2): each a 1–3 sentence, textbook-grounded teaching moment for this game's mechanic. Set `source_concept_id`. Keep them short and text-first; use `visual_svg` only if a tiny diagram is essential and not already shown.

- `decision_process_explanation` — open-ended, AFTER C3, BEFORE the simulation:
  - `prompt` — ask for all three: (1) qaysi qoidani/sinfni aniqladingiz,
    (2) nega bu amal (yoki xavfsizlik qadami) alternativlardan mosroq, (3) zaif
    strategiyada qanday xato yoki xavf yuzaga keladi.
  - `expected_components: ["concept", "method", "mistake"]`
  - `rubric` — object, e.g. `{"concept": 1, "method": 1, "mistake": 1}`.
  - `sample_acceptable_answer` — a model 2–4 sentence answer citing all three.
  - `eval_mode: "ai"`, `min_chars: 60`, `options: null` (MUST be null — never
    add choices; never auto-pass the DPE).
- `final_simulation` — `{correct_path, wrong_path, why_wrong_fails}`. Correct path:
  the rule-aligned action improves the visible state meters (Xavfsizlik, Aniqlik,
  Xavf) and yields a controlled outcome. Wrong path: the tempting weak/unsafe
  action worsens at least one meter. `why_wrong_fails`: why it breaks the rule or
  safety principle.
  - `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).
- `feedback_summary` — `{understood, mistake, review}`.
- `completion_rules` — `{pass_condition, retry_condition}`. Pass: identifies the
  rule, picks the rule-aligned cell, explains before the consequence. Retry: picks
  a cell answerable without the chemistry rule, skips the explanation, or can't
  say why the weak path fails.

## Non-negotiables (from the spec)

- Grid solvable ONLY through the lesson concept — no general-intuition cells.
- Distractors plausible, never silly.
- EXACTLY 3 MCQ checkpoints; checkpoint 3 is MCQ (NOT the open explanation).
- DPE comes after C3 and before the consequence; never auto-pass it.
- Final simulation shows the correct path improving state vs the weak path
  worsening it (describe state meters in text/SVG).
- Align key terms with the Flashcards phase; never change formulas, numbers,
  units, or safety rules.

## Visuals

Low-asset CSS/SVG only. Use SVG/CSS for reaction schemes, classification meters,
and the decision board; an image placeholder for a real lab scene only if it adds
context. Follow the universal SVG rules injected by the runtime — do NOT specify
size or colours.

## Language

All student-facing text in natural, formal Uzbek ("Siz"). Preserve every formula,
number, symbol, unit, and safety rule exactly. Use the lesson's canonical chemistry
terms.
