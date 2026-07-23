# Prompt quality: judge re-anchor + spoiler fix + uz label localization + flashcard contract

## Approach & key decisions

**Goal:** packets fully teach the lesson and the judge's flag actually means "hurts learning."
Four surgical fixes, all prompt/contract-side — **no regeneration of existing packets**.

- **Judge re-anchor** (`phase_judge.py:77` `_FIDELITY_RULE`): today "contradicted by, **or
  absent from**, the LESSON CONTEXT" → `major`. This one sentence drives the 56–59%
  CBP/flashcard flag rates in math AND geography (verified false positive: judge flagged an
  Eratosthenes fact that IS in the extract). New rule: **contradiction → major; absent-but-
  uncontested → minor**. Rejected: enriching the extract instead (bigger, doesn't fix the
  judge's semantics) and per-subject judge rules (R25's framing — the geography data shows
  it's fact-density, not subject).
- **Error-detection spoiler**: the contract *instructs* the inline marker
  (`practice-error-detection.md:32` "Make clear (to the reader of this output, not to the
  student)…") — 685 packets fleet-wide ship `**(XATO BLOK)**` on the answer. Notion is
  storage; the student platform renders later — so fix = broken-block identity lives ONLY in
  the answer-key sections (`The correct version` / `Reveal`), blocks list stays clean. Plus a
  deterministic warn-only lint (existing `content_lint.py` conventions, CQ-B).
- **uz label localization**: `_LOCALIZE_HEADINGS_CLAUSE` is only appended for en/ru
  (`prompts.py:165`); uz was frozen byte-identical in #83 → English `### Scenario`,
  `## How to play` leak to Uzbek students. User approved un-freezing uz. New uz clause is
  generic ("every label the student READS") — but **machine-facing keys stay English**: card
  field keys (`id/front/back/type/difficulty/...`) and backtick enums (`easy|medium|hard`)
  are parsing anchors for the future platform ingestion. Append-only ⇒ #83's frozen-copy
  tests stay green by construction.
- **Flashcards contradiction**: `flashcards.md:117` "Cover every term…" is mathematically
  incompatible with the 6–8-card G5 band (line 15) — deck size wins per-phase; coverage is a
  packet-level property (memory-check + games carry the rest). User locked this.
- **CBP concealment rule untouched this round** (user: re-judge first, decide from data —
  T6 produces that data). Judge grades against the same `get_prompt` contract, so every
  contract edit auto-propagates to the judge.
- **Acceptance = real model calls over `transport=api`** (cli retired): a targeted 3-phase
  generation smoke (~$0.10) + a controlled A/B re-judge of 40 stored phases (~$2.5,
  judge-only, no content spend). Ops note: workers cache prompts — fleet restart required
  after merge for any of this to take effect.

Branch: `feat/prompt-quality-reanchor` off `origin/Nggaev-v2` (collision gate run 2026-07-23:
no overlapping branches/PRs; only open PR is unrelated `fix/dashboard-mobile-wrap`).

---

## Task 1 — Judge fidelity re-anchor

**Files:** `app/services/phase_judge.py`, `tests/services/test_phase_judge.py`

1. **RED** — add to `tests/services/test_phase_judge.py`:

```python
def test_fidelity_rule_downgrades_absence_to_minor():
    """Re-anchor (2026-07-23): only a CONTRADICTION of the lesson context is major;
    an absent-but-uncontested world claim is minor. Guards the 56-59% false-major
    rates measured on math/geography CBP+flashcards."""
    rule = phase_judge._FIDELITY_RULE
    assert "CONTRADICTS" in rule
    assert "`minor`" in rule and "ABSENT" in rule
    # the old anchor text must be gone
    assert "contradicted by, or absent from" not in rule

def test_judge_prompt_carries_reanchored_rule():
    p = phase_judge._build_judge_prompt(contract="C", output_md="O")
    assert "merely ABSENT" in p
```

Run: `uv run python -m pytest tests/services/test_phase_judge.py -q` → the two new tests fail.

2. **GREEN** — replace `_FIDELITY_RULE` (phase_judge.py:77-86) with:

```python
_FIDELITY_RULE = (
    "\n\nSource-fidelity (CRITICAL): a LESSON CONTEXT section is provided below — the lesson "
    "the output was authored from. Treat it as ground truth for contradictions: raise a "
    "`major` failure for any factual claim ABOUT THE WORLD in the OUTPUT that CONTRADICTS "
    "the LESSON CONTEXT (a changed date, number, name, definition, rule, or causal claim). "
    "A world claim that is merely ABSENT from the LESSON CONTEXT but not contradicted by it "
    "(supporting context, standard curriculum facts) is at most `minor` — never `major`, "
    "never a reason to regenerate. DO NOT flag numbers the OUTPUT generates for teaching — "
    "practice-problem values, worked-example arithmetic, invented student names, hypothetical "
    "scenarios — these are expected and are NOT fidelity violations. A hint list of candidate "
    "issues may appear below; verify each against the LESSON CONTEXT before trusting it, and "
    "drop any you cannot substantiate."
)
```

3. Full judge test files green:
   `uv run python -m pytest tests/services/test_phase_judge.py tests/services/test_execute_phase_judge.py tests/services/test_pipeline_judge_status.py -q`
4. **Commit:** `fix(judge): fidelity re-anchor — contradiction=major, absence=minor`
   (stage ONLY the two files above).

## Task 2 — Error-detection spoiler: contract + deterministic lint

**Files:** `prompts/_general/practice-error-detection.md`, `app/services/content_lint.py`,
`tests/services/test_content_lint.py`, `tests/test_prompt_coverage.py` (or the existing
prompt-text test home — implementer verifies the right file and follows its pattern).

1. **RED** — prompt-text assertions:

```python
def test_error_detection_contract_forbids_inline_marker():
    body = get_prompt("geografiya", "practice-error-detection")
    assert "(to the reader of this output, not to the student)" not in body
    assert "ONLY in" in body and "The correct version" in body
```

   And a lint RED test (follow `test_content_lint.py` conventions):

```python
def test_lint_flags_inline_error_marker():
    md = "# The blocks\n1. ok\n2. broken **(XATO BLOK)**\n# The correct version\n..."
    warns = content_lint.lint("practice-error-detection", md)
    assert any("error_detection_spoiler" in w for w in warns)

def test_lint_allows_marker_free_blocks():
    md = "# The blocks\n1. ok\n2. subtle slip\n# The correct version\n2-blok: ..."
    assert not [w for w in content_lint.lint("practice-error-detection", md)
                if "error_detection_spoiler" in w]
```

   (implementer adapts to `content_lint`'s real entrypoint signature — read it first).

2. **GREEN** — in `practice-error-detection.md`:
   - Replace the tail of the **The blocks** bullet (lines 28-32) — delete
     "Make clear (to the reader of this output, not to the student) which block is the
     broken one." and append instead:
     > Do NOT mark the broken block inside the blocks list — no "(XATO)"-style labels, no
     > bold/emphasis tells, no wording hints. The list must read clean, exactly as the
     > student will see it. Identify the broken block ONLY in **The correct version** and
     > **Reveal** sections below, naming it by its number there.
   - In **The correct version** bullet: prepend "Open by naming the broken block's number, then give …".
   - Add a **Non-negotiables** bullet:
     > **No inline answer marker.** The blocks list is student-visible; any marker,
     > label, or typographic tell identifying the broken block inside it defeats the
     > entire exercise.
   - Lint rule in `content_lint.py`: for `practice-error-detection`, warn
     `error_detection_spoiler` when the text BEFORE the `The correct version` heading
     matches `r"XATO\s*BLOK|\(XATO\)|WRONG\s+BLOCK|ERROR\s+BLOCK|ОШИБОЧН|\(ОШИБКА\)"`
     (case-insensitive). Warn-only, like every other lint rule.
3. `uv run python -m pytest tests/services/test_content_lint.py tests/test_prompt_coverage.py -q`
4. **Commit:** `fix(prompts): error-detection — no inline broken-block marker + spoiler lint`

## Task 3 — uz label localization (un-freeze)

**Files:** `app/services/prompts.py`, `tests/services/test_prompts_output_language.py`

1. **RED**:

```python
def test_uz_medium_gets_uz_localize_clause():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="uz")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE_UZ in body
    # frozen base still present (append-only un-freeze)
    assert prompts.LANGUAGE_RULES["_default"] in body

def test_en_ru_keep_their_own_clause_not_uz():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="ru")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE in body
    assert prompts._LOCALIZE_HEADINGS_CLAUSE_UZ not in body
```

2. **GREEN** — in `prompts.py`, next to `_LOCALIZE_HEADINGS_CLAUSE` add:

```python
_LOCALIZE_HEADINGS_CLAUSE_UZ = (
    "\nEVERY label the student READS is part of the output language: render section "
    "headings, the phase title, game labels (\"How to play\", \"Scenario\", \"Role\", "
    "\"Task\", \"Relationship types\", \"Why/How/What\", \"Checkpoint\", \"Learning "
    "Block\"), and the feedback labels (Correct/Partial/Wrong) in Uzbek — never leave "
    "them in English. EXCEPTION — machine-facing keys stay exactly as the format "
    "defines them, in English: card field keys (id, front, back, type, difficulty, "
    "hint, explanation, example, misconception) and enum values in backticks "
    "(`easy`, `medium`, `hard`, card/relationship type names)."
)
```

   and in `_resolve_language_rule` (line 165-167) extend the append:

```python
    lang = (output_language or "").lower()
    if lang in ("en", "ru"):
        rule = rule + _LOCALIZE_HEADINGS_CLAUSE
    else:                       # uz / default — un-freeze (2026-07-23, user-approved)
        rule = rule + _LOCALIZE_HEADINGS_CLAUSE_UZ
    return rule
```

3. Whole language-rule surface green:
   `uv run python -m pytest tests/services/test_prompts_output_language.py tests/services/test_prompts_resolver.py tests/services/test_prompt_coverage.py -q`
   (#83 frozen-copy tests must pass UNCHANGED — if any needs editing, stop: the change
   isn't append-only.)
4. **Commit:** `feat(prompts): localize student-read labels for uz output (un-freeze #83)`

## Task 4 — Flashcards: deck size wins over exhaustive coverage

**Files:** `prompts/_general/flashcards.md`, prompt-text test file from Task 2

1. **RED**:

```python
def test_flashcards_contract_scopes_coverage_to_deck_budget():
    body = get_prompt("geografiya", "flashcards")
    assert "Cover every term, name, structure, process, rule, and classification term" not in body
    assert "Deck size wins" in body
```

2. **GREEN** — in `flashcards.md`:
   - Replace line 117 ("- Cover every term, …") with:
     > - Within the deck-size budget, cover the most load-bearing terms, names, structures,
     >   processes, rules, and classification terms of THIS lesson — the ones the
     >   {{SUBJECT}} student must recall to work the rest of the homework. **Deck size wins
     >   over exhaustive coverage**: when the lesson holds more atoms than the grade band
     >   allows, choose by learning value; the remaining terms are carried by Memory Check
     >   and the games, never by inflating the deck past its band.
   - Line 51 tail: change "Whatever a {{SUBJECT}} student must be able to recall from this
     chapter belongs on a card." → "Whatever a {{SUBJECT}} student must be able to recall
     from this chapter belongs in the deck, subject to the deck-size budget below."
   - Line 3 (the intro's second absolutism, quoted verbatim by a G6 judge flag): "Your job
     is to extract every key term, name, structure, process, rule, formula, and
     classification term from the chapter that matters for {{SUBJECT}} and put them on
     cards." → "Your job is to distill the chapter's key terms, names, structures,
     processes, rules, formulas, and classification terms into a deck sized to the grade
     band (below) — the highest-value atoms first."
     RED addition: `assert "extract every key term" not in body`.
3. `uv run python -m pytest tests/test_prompt_coverage.py -q`
4. **Commit:** `fix(prompts): flashcards — deck-size budget wins over exhaustive coverage`

## Task 5 — Acceptance A: targeted generation smoke (real api calls)

Scratchpad script (NOT committed): pick one real geography lesson with a stored `extract`
output; call `agent.run_phase_prompt` over `transport=api` (gemini, explicit model) for the
3 affected phases — `practice-error-detection`, `flashcards`, `case-based-preview` — with the
stored extract as `lesson_context`. Assert on the real outputs:
- error-detection: NO spoiler-regex hit before `The correct version`; broken block named there.
- flashcards: deck within grade band; field keys/enums still English.
- all 3: no English student-read labels (`### Scenario`, `## How to play`, `# Case-Based`…);
  headings in Uzbek.

Bounded: 3 calls, ≈$0.05–0.10 — report actual cost. If a check fails, iterate the contract
wording (back to the relevant task) before proceeding. No mass generation.

## Task 6 — Acceptance B: controlled A/B re-judge (judge-only, no content spend)

Scratchpad script: sample 40 stored phases — 20 `case-based-preview` + 20 `flashcards`,
half previously `major_shipped` / half clean, drawn from matematika + geografiya `done` jobs.
For each, run `phase_judge.judge` twice with identical inputs (stored output_md, the job's
stored extract as lesson_context, `prior_outputs={}` in both arms — controlled): arm OLD =
module's `_FIDELITY_RULE` monkeypatched to the pre-Task-1 text; arm NEW = shipped rule.
Judge provider/model per `model_tiers` defaults, `transport=api`.

Deliverable: table `phase × arm → major-rate`, plus the residual-major breakdown for NEW
(what % is concealment-rule vs deck-size vs real defects). ≈80 judge calls ≈ $2.5 — report
actual. **This is the data for the deferred CBP-concealment decision and the R25 update.**

## Task 7 — Finish

1. Full suite: `uv run python -m pytest tests/ -q` (canonical bar: WITHOUT `RUN_DB_INTEGRATION`).
2. Rebase-check: `git fetch origin && git log HEAD..origin/Nggaev-v2` — rebase + re-run suite
   if the base moved.
3. Push `feat/prompt-quality-reanchor`, open PR (base `Nggaev-v2`) — user/GK2 gates the merge;
   never self-merge.
4. Same finish, not deferred: worklog entry in `docs/memory/MASTER_MEMORY.md` + `INDEX.md`
   row (check INDEX tail for the next free number — they go stale mid-lane); update
   `docs/memory/ROADMAP.md` R25 with the T6 numbers (widen math-only → fact-dense subjects;
   close if the re-anchor resolves it); de-stale `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md`
   judge + language-rule descriptions; `git mv` this plan to `docs/superpowers/plans/shipped/`.
5. **Ops (post-merge, operator):** fleet pull + worker restart — prompts are cached at
   worker startup; until restart, generation still uses the old contracts.

## Explicitly out of scope (this plan)

- CBP concealment-rule redesign — decided from T6 data, own plan if warranted.
- Packet-level coverage loop / extract enrichment — own plan (goal A, next slice).
- Re-judging or regenerating the existing 101 math / 306 geography / 122 clodex packets.
- G8-geometry Notion title collisions, tic-tac-toe verdict-in-board slips (judge already
  catches those; no contract defect).
