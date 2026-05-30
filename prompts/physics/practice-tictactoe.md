# Prompt: Practice Arc — Tic-Tac-Toe Decision Grid (Case-Based Preview interaction mode: Tic-Tac-Toe Decision Grid)

You are generating one **Case-Based Preview** practice game for this physics lesson,
skinned as a **Tic-Tac-Toe Decision Grid**: a 3×3 board of action cells where the
student must pick the cell that correctly applies the lesson's law or mechanism.

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
- `student_role` — solver / technician / lab assistant / observer / analyst.
- `case_type` — e.g. `practical_decision_case` (prediction/action under a law).
- `source_concept_ids` — list (>=1) of source-map concept IDs from THIS lesson.
  Do not invent; trace every grid cell back to one of these.
- `case_setup` — `{narrative, student_role, task}`. A 2–4 sentence role-based
  scenario where the student must predict an outcome or choose an action that
  follows a physical **law or mechanism**. The decision must NOT be answerable
  by guessing — the law is the only key. Describe the 3×3 decision board here
  (low-asset CSS/SVG), each cell an action; embed a source-carrying SVG figure
  (force diagram, circuit, ray diagram, graph) when the setup needs a figure.
- `checkpoints` — EXACTLY 3, in this order:
  1. `intent: "identify"`, `form: "mcq"` — **which source concept (law/mechanism)
     controls this situation?** Options: correct law · related-but-wrong law ·
     surface clue only · irrelevant detail. Set `correct_index`, write `feedback`.
  2. `intent: "decide"`, `form: "mcq"` — **the decision board.** "Qaysi katak
     amali bu vaziyatga eng mos?" The 4 options ARE the board action cells:
     correct action (applies the law) · plausible-but-incomplete action ·
     fast-but-unsafe/unsupported action · surface/irrelevant action. Set
     `correct_index`. `feedback` may name state-meter shifts (e.g. Aniqlik +30,
     Xavf −20). Distractors must be tempting to a real learner, not silly.
  3. `intent: "justify_or_avoid_mistake"`, `form: "mcq"` — **which explanation
     justifies the chosen action AND rejects the tempting weak strategy?**
     Options: correct explanation (law + why the action fits) · general intuition
     without the law · tempting-but-incomplete strategy · wrong/unsafe strategy.
     Set `correct_index`, write `feedback`.

## Learning Blocks (required — slots 3 & 5)

Emit `learning_block_1` (after Checkpoint 1) and `learning_block_2` (after Checkpoint 2): each a 1–3 sentence, textbook-grounded teaching moment for this game's mechanic. Set `source_concept_id`. Keep them short and text-first; use `visual_svg` only if a tiny diagram is essential and not already shown.

- `decision_process_explanation` — open-ended, AFTER C3, BEFORE the simulation:
  - `prompt` — ask for all three: (1) qaysi qonun/mexanizmni aniqladingiz,
    (2) nega bu amal alternativlardan mosroq, (3) zaif strategiyada qanday xato
    yuzaga keladi.
  - `expected_components: ["concept", "method", "mistake"]`
  - `rubric` — object, e.g. `{"concept": 1, "method": 1, "mistake": 1}`.
  - `sample_acceptable_answer` — a model 2–4 sentence answer citing all three.
  - `eval_mode: "ai"`, `min_chars: 60`, `options: null` (MUST be null — never
    add choices; never auto-pass the DPE).
- `final_simulation` — `{correct_path, wrong_path, why_wrong_fails}`. Correct path:
  the law-aligned action improves the visible state meters (Aniqlik, Xavf,
  Samaradorlik) and yields a controlled/solved outcome. Wrong path: the tempting
  weak action worsens at least one meter. `why_wrong_fails`: why it violates the law.
  - `why_wrong_fails`: one sentence on why the wrong path cannot be correct (REQUIRED).
- `feedback_summary` — `{understood, mistake, review}`.
- `completion_rules` — `{pass_condition, retry_condition}`. Pass: identifies the
  law, picks the law-aligned cell, explains before the consequence. Retry: picks a
  cell answerable without the law, skips the explanation, or can't say why the
  weak path fails.

## Non-negotiables (from the spec)

- Grid solvable ONLY through the lesson concept — no general-intuition cells.
- Distractors plausible, never silly.
- EXACTLY 3 MCQ checkpoints; checkpoint 3 is MCQ (NOT the open explanation).
- DPE comes after C3 and before the consequence; never auto-pass it.
- Final simulation shows the correct path improving state vs the weak path
  worsening it (describe state meters in text/SVG).
- Align key terms with the Flashcards phase; never change numbers, formulas, units.

## Visuals

Low-asset CSS/SVG only. For physics, embed source-carrying inline SVG (force /
circuit / ray diagrams, graphs) following the universal SVG rules injected by the
runtime. Do NOT specify size or colours.

## Language

All student-facing text in natural, formal Uzbek ("Siz"). Preserve every formula,
number, symbol, and unit exactly. Use the lesson's canonical physics terms.
