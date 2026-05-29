# Prompt: Practice Arc — Tic-Tac-Toe Decision Grid (Case-Based Preview interaction mode: Tic-Tac-Toe Decision Grid)

You are generating one **Case-Based Preview** practice game for this geometry
(geometriya) lesson, skinned as a **Tic-Tac-Toe Decision Grid**: a 3×3 board of
action cells where the student must pick the cell that correctly applies the
lesson's theorem, construction, or solving strategy.

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
- `student_role` — solver / checker / analyst / planner.
- `case_type` — e.g. `practical_decision_case` (choose the correct theorem /
  construction / solving strategy).
- `source_concept_ids` — list (>=1) of source-map concept IDs from THIS lesson.
  Do not invent; trace every grid cell back to one of these.
- `case_setup` — `{narrative, student_role, task}`. A 2–4 sentence role-based
  scenario presenting a figure/problem where the student must choose the **correct
  theorem, construction step, or solving strategy**. The decision must NOT be
  answerable by guessing — the lesson theorem/method is the only key. Describe the
  3×3 decision board here (low-asset CSS/SVG), each cell a candidate move; embed a
  source-carrying SVG figure (the geometric diagram) — it almost always is needed.
- `checkpoints` — EXACTLY 3, in this order:
  1. `intent: "identify"`, `form: "mcq"` — **which source concept (theorem /
     property / construction rule) controls this figure?** Options: correct
     concept · related-but-wrong theorem · surface clue only · irrelevant detail.
     Set `correct_index`, write `feedback`.
  2. `intent: "decide"`, `form: "mcq"` — **the decision board.** "Qaysi katak
     amali bu masalaga eng mos?" The 4 options ARE the board action cells:
     correct strategy (applies the theorem/construction) · plausible-but-incomplete
     strategy (right theorem, missed condition) · fast-but-unsafe strategy
     (shortcut that skips a needed condition) · surface/irrelevant strategy. Set
     `correct_index`. `feedback` may name state-meter shifts (e.g. Aniqlik +30,
     Xato xavfi −20). Distractors must be tempting to a real learner, not silly.
  3. `intent: "justify_or_avoid_mistake"`, `form: "mcq"` — **which explanation
     justifies the chosen strategy AND rejects the tempting weak one?** Options:
     correct explanation (theorem + why this step fits) · general intuition
     without the theorem · tempting-but-incomplete strategy (missing condition) ·
     wrong strategy. Set `correct_index`, write `feedback`.
- `decision_process_explanation` — open-ended, AFTER C3, BEFORE the simulation:
  - `prompt` — ask for all three: (1) qaysi teorema/qoidani aniqladingiz,
    (2) nega bu qadam alternativlardan mosroq, (3) zaif strategiyada qanday xato
    (masalan, shartni e'tiborsiz qoldirish) yuzaga keladi.
  - `expected_components: ["concept", "method", "mistake"]`
  - `rubric` — object, e.g. `{"concept": 1, "method": 1, "mistake": 1}`.
  - `sample_acceptable_answer` — a model 2–4 sentence answer citing all three.
  - `eval_mode: "ai"`, `min_chars: 60`, `options: null` (MUST be null — never
    add choices; never auto-pass the DPE).
- `final_simulation` — `{correct_path, wrong_path, why_wrong_fails}`. Correct path:
  the theorem-aligned strategy improves the visible state meters (Aniqlik, Xato
  xavfi, Samaradorlik) and reaches the proven/solved result. Wrong path: the
  tempting weak strategy worsens at least one meter (e.g. an unprovable claim).
  `why_wrong_fails`: why it violates the theorem or skips a required condition.
- `feedback_summary` — `{understood, mistake, review}`.
- `completion_rules` — `{pass_condition, retry_condition}`. Pass: identifies the
  theorem, picks the theorem-aligned cell, explains before the consequence. Retry:
  picks a cell answerable without the lesson theorem, skips the explanation, or
  can't say why the weak path fails.

## Non-negotiables (from the spec)

- Grid solvable ONLY through the lesson concept — no general-intuition cells.
- Distractors plausible, never silly.
- EXACTLY 3 MCQ checkpoints; checkpoint 3 is MCQ (NOT the open explanation).
- DPE comes after C3 and before the consequence; never auto-pass it.
- Final simulation shows the correct path improving state vs the weak path
  worsening it (describe state meters in text/SVG).
- Align key terms with the Flashcards phase; never change numbers, formulas, or units.

## Visuals

Low-asset CSS/SVG only. Embed the source-carrying geometric diagram as inline SVG
(figures, constructions, angle/length marks), following the universal SVG rules
injected by the runtime. Do NOT specify size or colours.

## Language

All student-facing text in natural, formal Uzbek ("Siz"). Preserve every formula,
number, symbol, and unit exactly. Use the lesson's canonical geometry terms.
