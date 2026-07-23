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
  generation smoke validated through `content_lint.lint_phase` (~$0.10) + a **three-arm**
  re-judge experiment over 40 stored phases with behavioral safety probes (~$4, judge-only,
  no content spend, committed script + sanitized result artifact). Ops note: workers cache
  prompts — fleet restart required after merge for any of this to take effect.
- **Lint semantics invert deliberately**: today `_lint_error_detection` *enforces* the old
  contract (warns `errdet_no_broken_marker` when no inline marker exists, and the
  `errdet-clean-*.md` fixtures pass *because* they carry the marker). Task 2 re-keys the
  whole family on a student-region/answer-key-region split with localized boundary headings;
  fixtures are re-authored to the new contract shape.

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

## Task 2 — Error-detection spoiler: contract edit + lint-family inversion

**Files:** `prompts/_general/practice-error-detection.md`, `app/services/content_lint.py`,
`tests/services/test_content_lint.py` (+ its errdet fixtures dir — locate via `FIX` in that
file), `tests/services/test_prompt_coverage.py`.

**Existing machinery (verified — build on it, do not duplicate):** entrypoint is
`content_lint.lint_phase(phase_name, output_md, *, subject, output_language)`;
`_lint_error_detection` (content_lint.py:215) already owns `_MARKER` (catches
"4-blok noto'g'ri", "BU BLOK XATO", "xato 4-blok", "(Broken)" — Uzbek apostrophe class,
`yorliq` noun, both orders) and `_REVEAL_HDR` (`reveal|ochish|oshkor`). Today it enforces
the OLD contract: `errdet_no_broken_marker` fires when NO marker exists anywhere. This task
**deliberately inverts** that family.

1. **RED** — prompt-text assertions in `tests/services/test_prompt_coverage.py`:

```python
def test_error_detection_contract_forbids_inline_marker():
    body = get_prompt("geografiya", "practice-error-detection")
    assert "(to the reader of this output, not to the student)" not in body
    assert "ONLY in" in body and "The correct version" in body
```

   Lint RED tests in `tests/services/test_content_lint.py` (new semantics):

```python
def test_errdet_marker_in_student_region_is_spoiler():
    # real production shape (G8 electroenergetika): marker inline in the blocks list
    md = ("# The blocks\n1. ok\n2. broken **(XATO BLOK)**\n"
          "# The correct version\n2-blok: to'g'ri matn\n# Reveal\n2-blok")
    assert "errdet_inline_spoiler" in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))

def test_errdet_clean_body_key_names_block_no_findings():
    md = ("# The blocks\n1. ok\n2. subtle slip\n"
          "## To'g'ri versiya\n2-blok noto'g'ri edi: ...\n## Reveal\n2-blok")
    assert not [c for c in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))
                if c.startswith("errdet_")]

def test_errdet_no_boundary_heading_never_spoilers():
    # conservative: no recognized answer-key boundary -> no spoiler finding
    md = "# The blocks\n1. ok\n2. broken (XATO)\nprose with no key heading"
    assert "errdet_inline_spoiler" not in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))

def test_errdet_key_region_names_no_block_warns():
    md = "# The blocks\n1. ok\n2. slip\n# The correct version\nto'g'ri matn, raqamsiz"
    assert "errdet_no_broken_marker" in _codes(cl.lint_phase(ED, md, subject="geografiya", output_language="uz"))
```

2. **GREEN** — contract (`practice-error-detection.md`):
   - Replace the tail of the **The blocks** bullet (lines 28-32) — delete
     "Make clear (to the reader of this output, not to the student) which block is the
     broken one." and append instead:
     > Do NOT mark the broken block inside the blocks list — no "(XATO)"-style labels, no
     > bold/emphasis tells, no wording hints. The list must read clean, exactly as the
     > student will see it. Identify the broken block ONLY in **The correct version** and
     > **Reveal** sections below, naming it by its number there.
   - In **The correct version** bullet: prepend "Open by naming the broken block's number,
     then give …".
   - Add a **Non-negotiables** bullet:
     > **No inline answer marker.** The blocks list is student-visible; any marker,
     > label, or typographic tell identifying the broken block inside it defeats the
     > entire exercise.

   **GREEN** — lint (`content_lint.py`), re-keyed on a region split:
   - `_ANSWER_KEY_HDR`: extend the boundary beyond `_REVEAL_HDR` to the correct-version
     headings, localized in ALL THREE output languages — English `the correct version`,
     Uzbek `to{_APOS}?g{_APOS}?ri versiya` (reuse `_APOS`), **Russian `правильная версия`**
     — plus the reveal set `reveal|ochish|oshkor|раскрытие` (and extend `_REVEAL_HDR` with
     `раскрытие` for the mismatch check). Live RU production emits `# Правильная версия` /
     `# Раскрытие`; without these the conservative no-boundary fallback would suppress the
     spoiler finding for every RU packet. Answer-key region starts at the FIRST such
     heading; student region is everything before it.
   - Extend `_MARKER` with:
     - the bare parenthesised Uzbek forms `\(\s*xato(\s+{_BLK})?\s*\)` ("(XATO)",
       "(XATO BLOK)") analogous to the existing `(Broken)` group ("XATO BLOK" without
       parens already matches the `xato\s+{_BLK}` alternative);
     - **Russian marker vocabulary**: extend `_BLK` with `блок`, add the wrongness
       adjectives `неправильн\w*|ошибочн\w*|неверн\w*|брокованн\w*` in the same
       pre/post/ordinal orders as the Uzbek forms, and the parenthesised form
       `\(\s*брокованн\w*\s+блок\s*\)` — live production contains inline
       `(БРОКОВАННЫЙ БЛОК)`.
   - New semantics of `_lint_error_detection`:
     - any `_MARKER` hit in the **student region** → `errdet_inline_spoiler` (NEW code),
       message citing the offending line. **Only when a boundary heading was found** — no
       recognized boundary ⇒ conservatively NO spoiler finding.
     - `errdet_no_broken_marker` inverts: fires when the answer-key region exists but names
       NO block id (`_BLOCK_ID` scan after the boundary), or when no boundary heading exists
       at all (the contract requires the section).
     - `errdet_multiple_broken`: ≥2 distinct block ids named as broken in the answer-key
       region (marker-based ids, per the existing groupdict recovery).
     - `errdet_reveal_mismatch`: first id after the correct-version boundary vs first id
       after `_REVEAL_HDR` differ, when both regions exist.
   - **Fixtures re-authored to the new contract shape** (they currently encode the old one):
     `errdet-clean-*.md` → clean student region + answer key naming the block;
     `errdet-zero-markers.md` → still warns (`errdet_no_broken_marker`, no key id);
     `errdet-two-markers.md` → ids in the key region for `errdet_multiple_broken`;
     add one REAL sampled spoiler output (G8 electroenergetika `**(XATO BLOK)**` job
     `83852be1-c31b-43cb-9b69-790be8fc57f6`, sanitized excerpt) as the inline-spoiler
     fixture, **and one REAL Russian regression fixture** (implementer samples a live RU
     errdet output with `# Правильная версия` boundary + a Russian inline marker from the
     DB) proving the RU path produces `errdet_inline_spoiler`, not the no-boundary
     fallback. Every existing `test_errdet_*` updated to the inverted semantics —
     deliberately, in the same commit, with a comment naming the inversion.
3. `uv run python -m pytest tests/services/test_content_lint.py tests/services/test_prompt_coverage.py -q`
4. **Commit:** `fix(prompts+lint): error-detection — spoiler-free student region, inverted errdet lint family`

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

def test_l2_subject_uz_medium_also_gets_uz_clause():
    """INTENTIONAL side effect: an English/Russian class packet rendered with the uz
    medium localizes its student-read labels into Uzbek too — labels are scaffolding,
    and the L2 scaffolding bridge is Uzbek."""
    body = prompts.get_prompt("english", "flashcards", output_language="uz")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE_UZ in body
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

   Also update the now-stale "uz is untouched / byte-identical" comments in `prompts.py`
   (verified at lines 83, 131, 141, 159): the FROZEN-BASE claim stays true (the base blocks
   are still byte-identical); what changed is that uz now gets an APPENDED label clause —
   say exactly that, dated 2026-07-23.

   **Plus a deterministic detector** (the existing `english_template` check does NOT
   recognize `Scenario`, `How to play`, or `Case-Based` headings — verified): add an
   `english_heading_leak` rule to `content_lint.py`, warn-only, firing when
   `output_language != "en"` and a HEADING line matches an explicit English structural-label
   list: `Scenario|How to play|Case-Based Preview|Relationship types|Role|Task|Checkpoint|
   Learning Block|Feedback summary|Memory Check|Reflection|Decision Process`. Explicitly
   EXCLUDE `Boss Arena` (intentional game name). Tests in
   `tests/services/test_content_lint.py`: fires on `## How to play` in uz output; silent on
   the same heading in en output; silent on `# Boss Arena` in uz output.
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
3. `uv run python -m pytest tests/services/test_prompt_coverage.py -q`
4. **Commit:** `fix(prompts): flashcards — deck-size budget wins over exhaustive coverage`

## Task 5 — Acceptance A: targeted generation smoke (real api calls, lint-validated)

Scratchpad script (NOT committed): pick one real geography lesson with a stored `extract`
output; call `agent.run_phase_prompt` over `transport=api` (gemini, explicit model) for the
3 affected phases — `practice-error-detection`, `flashcards`, `case-based-preview` — with the
stored extract as `lesson_context`. Validate the real outputs **through the shipped
machinery, not ad-hoc regexes**:
- run `content_lint.lint_phase` on each output: error-detection must produce NO
  `errdet_inline_spoiler` and NO `errdet_no_broken_marker` (i.e. clean student region AND
  the answer key names the block); NO `english_heading_leak` findings (the Task 3 rule —
  there is no `lint:language` code; the pre-existing codes are `mixed_script` /
  `english_template` / `calque` / `ru_uzbek_leak`).
- flashcards: deck within grade band (count `**id:** card_` occurrences); field keys/enums
  still canonical English.
- eyeball all 3 for Uzbek student-read headings.

Bounded: 3 calls, ≈$0.05–0.10 — report actual cost. If a check fails, iterate the contract
wording (back to the relevant task) before proceeding. No mass generation.

## Task 6 — Acceptance B: three-arm re-judge experiment + behavioral safety probes

**Committed, reproducible** (correction of the earlier scratchpad-only design):
- script: `scripts/experiments/rejudge_ab.py` (sanitized — no tokens/keys, DB URL from env);
- result artifact: `scripts/experiments/2026-07-23-rejudge-ab-results.json` containing the
  sampled job/phase IDs, the sampling seed, sha256 of each arm's fidelity rule AND contract
  text, judge provider/model, every raw verdict (incl. repeats), the transition tables,
  token usage, and actual cost.

**Cohort (pinned).** Mathematics = the 101-job campaign: batches `4a380da8` + `bd51015b`
(subject `matematika`, G5, 65 jobs) and `95f49c30` + `0fb09b6c` (subject **`math-algebra`**,
G11, 36 jobs) — both codes, the earlier "matematika" wording missed all 36 G11 jobs.
Geography = the 306 `done` jobs across the six `geografiya` books. The artifact lists the
exact job IDs drawn.

**Sample.** 40 stored phases — **exactly 5 items in each of 8 cells:
cohort {math, geografiya} × phase {case-based-preview, flashcards} × prior status
{major_shipped, clean}** (all eight cells verified to hold sufficient live rows).
Deterministic seeded draw per cell (`ORDER BY md5(:seed || phase_output_id)`, seed recorded).

**Arms (correction: a genuine OLD arm — Task 4 changes the flashcards contract, so
rule-swap alone measures neither old production nor Task 4):**
- **A** — old `_FIDELITY_RULE` + old contracts, both pinned to the **immutable branch-point
  SHA `57b81aa`** (`git show 57b81aa:<path>`), never the moving `origin/Nggaev-v2` ref;
  contracts passed via `judge(contract_override=…)` → the production baseline.
- **B** — new rule + old contracts → isolates Task 1.
- **C** — new rule + new contracts (no override) → the shipped state.

Identical inputs per item across arms (stored `output_md`, the job's stored extract as
`lesson_context`, `prior_outputs={}` in ALL arms — controlled). The old-rule arm
monkeypatches `phase_judge._FIDELITY_RULE` inside `try/finally` restoring the module global.
**Arm order counterbalanced per item** (rotate A/B/C execution order by item index — the
gemini judge calls are unseeded). **Discordant pivotal cases** (items whose A and C
verdicts disagree on `has_major` — the intended fix will likely produce MANY) are re-run
3× in the two deciding arms (6 calls each), **deterministically capped at the first 8
discordant items by item index** — capped-out items are listed in the artifact as
`discordant_not_replayed` (no silent truncation), single-run verdicts stand for them.

**Statistics (correction: the 50/50 draw is case-control — raw arm percentages must NOT be
compared to the population rates 17.5% math / 4.6% physics / 8.9% history).** Report:
1. **Paired transition tables** per phase (A→B, A→C): stayed-major / demoted / promoted.
2. **Reweighted population major-rates** per phase × cohort:
   `P(major_new) = prev × P(major_new | old-flagged) + (1−prev) × P(major_new | old-clean)`
   with `prev` = the real per-phase×cohort `major_shipped` prevalence queried from the DB
   (e.g. CBP-geo 59.2%, flashcards-geo 57.2%) and recorded in the artifact.
3. Residual-major breakdown for arm C: concealment-rule vs deck-size vs source-fidelity vs
   other — the data for the deferred CBP decision and the R25 update.

**Behavioral safety probes (must all pass before Task 7):** four constructed cases run
through the NEW rule (each 3×, unseeded model):
- a direct contradiction of the extract (planted wrong date) → **stays `major`** every run;
- an absent-but-uncontested true fact → **never `major`**;
- generated exercise values (invented practice numbers) → **no fidelity flag**;
- one manually-confirmed GENUINE defect from the audits (e.g. a verified wrong-fact flag
  chosen during implementation from `validation_warnings`) → **stays `major`**.

**Budget — hard-bounded, worst case.** 120 A/B/C calls + 12 safety-probe calls + at most
8 × 6 = 48 discordant-replay calls = **180 calls worst case ≈ $5.4**. The script takes
`--max-calls` (default 200) and `--max-cost-usd` (default 6.0) and **hard-stops cleanly**
when either is reached — partial results are written to the artifact with a `budget_hit`
marker and the skipped work enumerated; it never silently exceeds the approved amount.
Report actual calls + cost. Judge provider/model per `model_tiers` defaults,
`transport=api`. These calls write `agent_usages` rows (normal billing attribution) but
**never touch `phase_outputs` / `judge_status`** — no persisted operational re-judge.

**Commit:** `test(experiments): three-arm re-judge A/B + safety probes (script + artifact)`

## Task 7 — Finish

1. Full suite: `uv run python -m pytest tests/ -q` (canonical bar: WITHOUT `RUN_DB_INTEGRATION`).
2. Rebase-check: `git fetch origin && git log HEAD..origin/Nggaev-v2` — rebase + re-run suite
   if the base moved. **If the incoming commits touch judge or prompt inputs**
   (`app/services/phase_judge.py`, `app/services/prompts.py`, `prompts/**`,
   `app/services/content_lint.py`), **re-run Tasks 5–6**, not merely the unit suite — the
   acceptance evidence is against those inputs.
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
- **Persisted operational** re-judging (no `judge_status`/`validation_warnings` rewrites) or
  regenerating the existing 101 math / 306 geography / 122 clodex packets. Task 6's
  experimental judge calls are read-only with respect to packet state.
- G8-geometry Notion title collisions, tic-tac-toe verdict-in-board slips (judge already
  catches those; no contract defect).
