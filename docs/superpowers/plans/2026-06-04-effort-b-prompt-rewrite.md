# Effort B — Prompt Rewrite to Infra Specs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `prompts/_general/*.md` to be faithful to `docs/Infra_prompts/` specs — markdown-native, family-aware for CBP + Flashcards — and purge the dead JSON-field vocabulary Effort A left behind.

**Architecture:** One new injection token, `{{FAMILY_RULES}}`, resolved in `app/services/prompts.py` exactly like the existing `{{LANGUAGE_RULES}}` (subject → family → named block constant, with a phase-level `_default` and a no-cross-family-leak guard). Only `case-based-preview` and `flashcards` carry family blocks; the other nine phases get content-only edits (purge + add omitted spec rules). Prompts are pure content: changes take effect on server restart (startup cache), zero migration.

**Tech Stack:** Python 3.13, FastAPI, pytest (DB-free unit tests — `tests/conftest.py` wires no real database), the four LLM CLIs for the acceptance smoke.

---

## Conventions for this plan (read first)

**Two kinds of task in this plan:**

1. **Mechanism tasks** (Task 1) — ordinary TDD with complete, verbatim Python and tests.
2. **Prompt-content tasks** (Tasks 2–8) — the deliverable is *authored markdown prose ported from a cited Infra spec*, not algorithmic code. These cannot be specified as one verbatim string (the prose is the creative port). They are instead bounded by:
   - **the cited source spec file** to port from (exact path given per task),
   - **verbatim fixed strings** that MUST appear (output-format block, the placeholder sentinel, the canonical enum, per-family visual defaults, the family-block skeleton — all given below),
   - **automated anchor tests** (dead-vocab purge, token resolution, family markers, placeholder form) that fail before and pass after,
   - the **acceptance smoke** (Task 10).
   Authoring rule: **port the cited spec; do not invent structure beyond it.** Keep each family block injection-sized (~12–25 lines), not a full-spec copy.

**Shared fixed strings** (use verbatim where a task references them):

- **OUTPUT_FORMAT_BLOCK** (already the footer of the current CBP/flashcards prompts — keep it):
  ```
  ## Output format

  Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
  title and `##`/`###` for the sections/items described above, in order. For visuals:
  emit inline `<svg>` for diagrams; for a photo/raster you would otherwise need to
  generate, emit `![placeholder: <short description> — image gen required](placeholder)`
  — never fabricate an image and never invent an image URL.
  ```
- **PLACEHOLDER_SENTINEL** (the only validator-safe raster form, per `_visuals_resolve`):
  ```
  ![placeholder: <short description> — image gen required](placeholder)
  ```
- **CANONICAL_FLASHCARD_TYPES** (in-prompt only — the `FlashcardType` schema was DELETED in Effort A; do NOT reference a schema):
  `definition`, `term_to_meaning`, `process_step`, `question_answer`, `misconception`, `image_label`.
  A family block MAY add family-specific types (e.g. languages: `vocabulary`, `grammar`; math/sciences: `formula`); the base prompt states the canonical core and that family blocks may extend it.
- **FAMILY_VISUAL_DEFAULT** (per family, ported from the Infra specs):
  - sciences → **IMAGE** default (real labs/organisms/phenomena); SVG for particle/molecular/process diagrams.
  - math → **SVG** default (figures, fraction bars, graphs, state diagrams); IMAGE only for real-world context.
  - languages → **IMAGE** default (communication settings/dialogue); SVG for tense timelines/conjugation tables.
  - humanities → **SVG** default (timelines, causal chains, labelled maps); IMAGE for portraits/monuments/artifacts.
  - Every IMAGE-default family block MUST state the **PLACEHOLDER_SENTINEL** rule (raster → placeholder, never a URL).
- **FAMILY_BLOCK_SKELETON** (each block follows this shape):
  - CBP: `**Visual policy:** …` · `**Case framing:** <2–3 family case patterns from spec>` · `**Avoid:** <family forbid list from spec>`
  - Flashcards: `**Card types:** …` · `**Atomisation:** <family example from spec>` · `**Visual policy:** …` · `**Avoid:** …`

**Subject → family map** (drives resolution; covers all 7 `flows.SUBJECTS`):
`sciences` = biology, kimyo-g7-11, physics · `math` = math-algebra, geometriya-g7-11 · `languages` = english · `humanities` = history.

**Dead-vocab blocklist** (the purge target — 67 occurrences across 10 files at plan time):
`options: null`, `eval_mode`, `min_chars`, `source_concept_ids`, `interaction_payload`, `interaction_mode`, `chips[]`, `expected_reasoning_keywords`, `base_damage`, `accepted_variants`, `source map`, `allowed_assembly_types`.

---

## Task 1: `{{FAMILY_RULES}}` resolution mechanism

**Files:**
- Modify: `app/services/prompts.py` (after the `LANGUAGE_RULES` block, ~`:42`; and inside `get_prompt`, ~`:88-98`)
- Test: `tests/services/test_prompts_resolver.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_prompts_resolver.py`:

```python
@pytest.fixture
def tmp_family(tmp_path, monkeypatch):
    (tmp_path / "_general").mkdir()
    (tmp_path / "_general" / "demo.md").write_text(
        "Title for {{SUBJECT}}.\n\n{{FAMILY_RULES}}\n", encoding="utf-8")
    (tmp_path / "_general" / "noblocks.md").write_text(
        "No family here for {{SUBJECT}}.\n\n{{FAMILY_RULES}}\n", encoding="utf-8")
    monkeypatch.setattr(P, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(P, "_SUBJECT_FAMILY", {
        "biology": "sciences", "math-algebra": "math",
        "english": "languages", "history": "humanities",
    })
    monkeypatch.setattr(P, "FAMILY_RULES", {"demo": {
        "sciences": "SCI-BLOCK", "math": "MATH-BLOCK",
        "languages": "LANG-BLOCK", "humanities": "HUM-BLOCK",
        "_default": "DEFAULT-BLOCK",
    }})
    P._cache.clear(); P._hash_cache.clear()
    return tmp_path


def test_family_token_resolves_per_subject(tmp_family):
    assert "SCI-BLOCK" in P.get_prompt("biology", "demo")
    assert "MATH-BLOCK" in P.get_prompt("math-algebra", "demo")
    assert "LANG-BLOCK" in P.get_prompt("english", "demo")
    assert "HUM-BLOCK" in P.get_prompt("history", "demo")
    assert "{{FAMILY_RULES}}" not in P.get_prompt("biology", "demo")


def test_family_unmapped_subject_falls_to_phase_default(tmp_family):
    # subject not in _SUBJECT_FAMILY → phase _default, never another family's block
    out = P.get_prompt("kimyo-g7-11", "demo")
    assert "DEFAULT-BLOCK" in out
    for leaked in ("SCI-BLOCK", "MATH-BLOCK", "LANG-BLOCK", "HUM-BLOCK"):
        assert leaked not in out


def test_family_no_entry_for_phase_collapses_to_empty(tmp_family):
    # 'noblocks' phase has no FAMILY_RULES entry → token replaced with "" (no leak, no leftover token)
    out = P.get_prompt("biology", "noblocks")
    assert "{{FAMILY_RULES}}" not in out
    for leaked in ("SCI-BLOCK", "DEFAULT-BLOCK"):
        assert leaked not in out


def test_family_missing_block_for_family_falls_to_default(tmp_family, monkeypatch):
    monkeypatch.setattr(P, "FAMILY_RULES", {"demo": {
        "sciences": "SCI-BLOCK", "_default": "DEFAULT-BLOCK"}})
    P._cache.clear(); P._hash_cache.clear()
    # math subject, no math block present → phase _default, NOT the sciences block
    out = P.get_prompt("math-algebra", "demo")
    assert "DEFAULT-BLOCK" in out and "SCI-BLOCK" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/services/test_prompts_resolver.py -k family -v`
Expected: FAIL — `AttributeError: module 'app.services.prompts' has no attribute '_SUBJECT_FAMILY'` (and `FAMILY_RULES`).

- [ ] **Step 3: Implement the mechanism**

In `app/services/prompts.py`, after the `LANGUAGE_RULES = {...}` line add:

```python
_SUBJECT_FAMILY = {
    "biology": "sciences",
    "kimyo-g7-11": "sciences",
    "physics": "sciences",
    "math-algebra": "math",
    "geometriya-g7-11": "math",
    "english": "languages",
    "history": "humanities",
}

# Family-varying prompt blocks, keyed [phase_name][family] with a phase-level
# "_default". Only CBP + flashcards vary by family; authored in Tasks 2-3.
# Resolution never leaks one family's block to another (see get_prompt).
FAMILY_RULES: dict[str, dict[str, str]] = {
    "case-based-preview": {},   # filled in Task 2
    "flashcards": {},           # filled in Task 3
}
```

In `get_prompt`, after the `{{LANGUAGE_RULES}}` replace and before the `provider_suffix` append, add:

```python
    phase_blocks = FAMILY_RULES.get(phase_name, {})
    family = _SUBJECT_FAMILY.get(subject)
    family_block = phase_blocks.get(family) or phase_blocks.get("_default", "")
    body = body.replace("{{FAMILY_RULES}}", family_block)
```

(Note: `phase_blocks.get(family)` with `family=None` returns `None`, so an unmapped
subject or a missing family block both fall through `or` to the phase `_default`; a
phase with no entry has `phase_blocks == {}` so the token collapses to `""`. No
branch can return a different family's block.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/services/test_prompts_resolver.py -k family -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run each file separately (no regressions)**

Run: `uv run python -m pytest tests/services/test_prompts_resolver.py -q` → PASS, and
`uv run python -m pytest tests/services/test_prompt_coverage.py -q` → PASS.
(Do NOT run the two files in one command: a pre-existing fixture bug — the `tmp_*`
fixtures monkeypatch `PROMPTS_DIR` and clear `_cache` with no teardown — leaves stale
temp-dir state cached when resolver runs before coverage in the same process, causing
false `KeyError` failures. The full suite is unaffected because pytest runs coverage
before resolver alphabetically. The fixture teardown is fixed in Task 9.)

- [ ] **Step 6: Commit**

```bash
git add app/services/prompts.py tests/services/test_prompts_resolver.py
git commit -m "feat(prompts): {{FAMILY_RULES}} resolution — subject→family, _default, no leak"
```

---

## Task 2: Rewrite Case-Based Preview (base + 4 family blocks)

**Files:**
- Modify: `prompts/_general/case-based-preview.md` (full rewrite)
- Modify: `app/services/prompts.py` (`FAMILY_RULES["case-based-preview"]`)
- Test: `tests/services/test_prompt_coverage.py`

**Source specs to port (read before authoring):**
- `docs/Infra_prompts/Case-Based Preview/nets_case_based_preview_generation_standard_v1.md` (master)
- `…/nets_cbp_prompt_sciences.md`, `…/nets_cbp_prompt_math_family.md`, `…/nets_cbp_prompt_languages.md`
- Humanities has **no** CBP spec — author it by extrapolating the 3 above + `docs/Infra_prompts/Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_humanities.md` (humanities visual policy: SVG timelines/causal-chains/maps, IMAGE for portraits/monuments; causal claims trace to textbook; no anachronistic state names).

- [ ] **Step 1: Write the failing anchor tests**

Add to `tests/services/test_prompt_coverage.py`:

```python
from app.services.prompts import get_prompt as _gp

_DEAD_VOCAB = [
    "options: null", "eval_mode", "min_chars", "source_concept_ids",
    "interaction_payload", "interaction_mode", "chips[]",
    "expected_reasoning_keywords", "base_damage", "accepted_variants",
    "source map", "allowed_assembly_types",
]


def _assert_clean(rendered: str):
    low = rendered.lower()
    hits = [tok for tok in _DEAD_VOCAB if tok.lower() in low]
    assert not hits, f"dead JSON vocab still present: {hits}"


def test_cbp_has_family_token():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "case-based-preview.md").read_text(encoding="utf-8")
    assert "{{FAMILY_RULES}}" in body


def test_cbp_family_visual_defaults_distinct_and_clean():
    sci = _gp("biology", "case-based-preview")
    mat = _gp("math-algebra", "case-based-preview")
    lan = _gp("english", "case-based-preview")
    hum = _gp("history", "case-based-preview")
    for r in (sci, mat, lan, hum):
        assert "{{FAMILY_RULES}}" not in r
        _assert_clean(r)
    # IMAGE-default families must carry the validator-safe placeholder rule
    assert "](placeholder)" in sci and "](placeholder)" in lan and "](placeholder)" in hum
    # families resolve to *different* blocks (no single shared block)
    assert sci != mat and lan != hum and sci != hum
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k cbp -v`
Expected: FAIL — token not yet in the file / blocks empty so all families render identically and dead vocab present.

- [ ] **Step 3: Rewrite `prompts/_general/case-based-preview.md`**

Author the base prompt by porting the master CBP spec. Keep the canonical pedagogy (exactly 3 checkpoints `identify→decide→justify_or_avoid_mistake`, learning_block_1/2, DPE after checkpoint 3, final simulation with `why_wrong_fails`). **Remove all dead vocab** (`source_concept_ids`, `options: null`, `eval_mode`, `min_chars` — describe these as markdown content rules in prose, not JSON fields). Add a family section before the Language section:

```
## Visual & case framing (family-specific)

{{FAMILY_RULES}}
```

End with **OUTPUT_FORMAT_BLOCK** and the existing `## Language\n\n{{LANGUAGE_RULES}}` and self-check. Verify the file still contains `{{SUBJECT}}` and `{{LANGUAGE_RULES}}`.

- [ ] **Step 4: Author the 4 CBP family blocks in `prompts.py`**

Set `FAMILY_RULES["case-based-preview"]` to a dict with keys `sciences`, `math`, `languages`, `humanities`, and `_default`. Each block follows the CBP **FAMILY_BLOCK_SKELETON** (`Visual policy` / `Case framing` / `Avoid`), porting the matching spec, using the **FAMILY_VISUAL_DEFAULT** for that family and stating the **PLACEHOLDER_SENTINEL** rule in every IMAGE-default block (sciences, languages, humanities). Keep each ~12–25 lines. Example shape (sciences — port real content from the sciences spec):

```python
_CBP_SCIENCES = (
    "**Visual policy:** Default to a real-world IMAGE (labs, organisms, phenomena); "
    "use inline `<svg>` only for particle/molecular/process diagrams that carry the "
    "decision. For any raster you would otherwise generate, emit "
    "`![placeholder: <short description> — image gen required](placeholder)` — never "
    "fabricate a URL.\n"
    "**Case framing:** <port 2-3 science case patterns from nets_cbp_prompt_sciences.md "
    "(e.g. phenomenon→prediction→formula→consequence; safety→observation→particle→result)>.\n"
    "**Avoid:** <port the science forbid list (no magic/unreality; do not narrow "
    "organism-wide concepts to human-only unless the topic is human biology)>."
)
```

Define `_CBP_MATH`, `_CBP_LANGUAGES`, `_CBP_HUMANITIES` the same way (math → SVG default; languages → IMAGE default + placeholder; humanities → SVG default + IMAGE-for-portraits + placeholder, causal claims trace to textbook, no anachronistic names). Set `_default = _CBP_SCIENCES`-equivalent generic (a neutral visual/case framing with the placeholder rule). Wire:

```python
FAMILY_RULES["case-based-preview"] = {
    "sciences": _CBP_SCIENCES, "math": _CBP_MATH,
    "languages": _CBP_LANGUAGES, "humanities": _CBP_HUMANITIES,
    "_default": _CBP_DEFAULT,
}
```

- [ ] **Step 5: Run the anchor tests + coverage**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k "cbp or every_flow" -v`
Expected: PASS (token present, families distinct, placeholder rule present, no dead vocab, no unreplaced `{{SUBJECT}}`).

- [ ] **Step 6: Commit**

```bash
git add prompts/_general/case-based-preview.md app/services/prompts.py tests/services/test_prompt_coverage.py
git commit -m "feat(prompts): rewrite CBP to spec — family-aware, markdown-native, authored humanities variant"
```

---

## Task 3: Rewrite Flashcards (base + 4 family blocks)

**Files:**
- Modify: `prompts/_general/flashcards.md` (full rewrite)
- Modify: `app/services/prompts.py` (`FAMILY_RULES["flashcards"]`)
- Test: `tests/services/test_prompt_coverage.py`

**Source specs:** `docs/Infra_prompts/Flashcards/Flashcard Prompts/flashcard_study_engine_documentation.md` (master) + the four `nets_flashcard_game_prompt_{sciences,math_family,languages,humanities}.md`.

- [ ] **Step 1: Write the failing anchor tests**

Add to `tests/services/test_prompt_coverage.py`:

```python
def test_flashcards_has_family_token_and_canonical_enum():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "flashcards.md").read_text(encoding="utf-8")
    assert "{{FAMILY_RULES}}" in body
    for t in ("definition", "term_to_meaning", "process_step",
              "question_answer", "misconception", "image_label"):
        assert t in body, f"canonical type {t} missing"
    # the deleted schema must NOT be referenced
    assert "FlashcardType" not in body


def test_flashcards_families_distinct_and_clean():
    sci = _gp("physics", "flashcards")
    lan = _gp("english", "flashcards")
    hum = _gp("history", "flashcards")
    mat = _gp("geometriya-g7-11", "flashcards")
    for r in (sci, lan, hum, mat):
        assert "{{FAMILY_RULES}}" not in r
        _assert_clean(r)
    assert sci != lan and lan != hum and sci != mat
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k flashcards -v`
Expected: FAIL — token absent / families identical.

- [ ] **Step 3: Rewrite `prompts/_general/flashcards.md`**

Port the master flashcard doc. Keep the 8-field card shape (id/front/back/type/difficulty/hint/explanation/example/misconception), the back-length rule, and the worked Uzbek examples. State the canonical type set (**CANONICAL_FLASHCARD_TYPES**) and add: *"These are the canonical core types, defined in-prompt; family-specific types may be added in the family rules below. (There is no `FlashcardType` schema — flashcards are markdown.)"* Insert before the Language section:

```
## Card types & visuals (family-specific)

{{FAMILY_RULES}}
```

End with **OUTPUT_FORMAT_BLOCK** + `## Language\n\n{{LANGUAGE_RULES}}`. Remove any dead vocab.

- [ ] **Step 4: Author the 4 flashcard family blocks in `prompts.py`**

Set `FAMILY_RULES["flashcards"]` with `sciences`/`math`/`languages`/`humanities`/`_default`, each following the flashcards **FAMILY_BLOCK_SKELETON** (`Card types` / `Atomisation` / `Visual policy` / `Avoid`), porting the matching family spec. Use **FAMILY_VISUAL_DEFAULT**; IMAGE-default families state the **PLACEHOLDER_SENTINEL** rule. Port each spec's atomisation example (sciences: photosynthesis → 6 cards; math: quadratic formula → 3 cards; languages: Present Perfect + irregulars → 4 cards; humanities: Amir Temur → 6 cards) and family type extensions (languages: `vocabulary`,`grammar`; math/sciences: `formula`). Keep ~12–25 lines each. Wire `FAMILY_RULES["flashcards"] = {...}`.

- [ ] **Step 5: Run anchor tests + coverage**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k "flashcards or every_flow" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add prompts/_general/flashcards.md app/services/prompts.py tests/services/test_prompt_coverage.py
git commit -m "feat(prompts): rewrite Flashcards to spec — family-aware cards, in-prompt enum (schema gone)"
```

---

## Task 4: Light pass — memory-check

**Files:**
- Modify: `prompts/_general/memory-check.md`
- Test: `tests/services/test_prompt_coverage.py`

**Source:** the three `docs/Infra_prompts/Flashcards/Quzilet Learning/*_Specification.md` (Multiple Choice, Fill in the blank, Choose Correct Explanation) — these are the memory-check mechanics.

- [ ] **Step 1: Write the failing test**

```python
def test_memory_check_clean_and_consistent():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "memory-check.md").read_text(encoding="utf-8")
    _assert_clean(_gp("biology", "memory-check"))
    # bug fix: the "how many kinds" rule must be stated once, as ">=2 of 3" — not "all 3"
    assert "all 3 kinds" not in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k memory_check -v`
Expected: FAIL — dead vocab present and/or "all 3 kinds" present.

- [ ] **Step 3: Edit `prompts/_general/memory-check.md`**

Remove dead JSON-field vocab (describe options/blanks/reasons as markdown content, not JSON fields). Fix the contradiction: the current prompt says "all 3 kinds" in the body (~L35) but "≥2 of 3" in the self-check (~L63) — **reconcile both to "≥2 of 3"**. Add the distractor-quality rule from the Choose-Correct-Explanation spec: *every wrong option must encode the flawed reasoning that makes it tempting to a half-learned student (no joke/nonsense distractors)*.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k memory_check -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/memory-check.md tests/services/test_prompt_coverage.py
git commit -m "fix(prompts): memory-check — purge JSON vocab, reconcile >=2-of-3, distractor rule"
```

---

## Task 5: Light pass — practice-rlc + practice-error-detection

**Files:**
- Modify: `prompts/_general/practice-rlc.md`, `prompts/_general/practice-error-detection.md`
- Test: `tests/services/test_prompt_coverage.py`

**Sources:** `docs/Infra_prompts/Gamified Practices/Real Life Challenge/Real_Life_Challenge_Specification.md`; `…/Error Detection/Error_Detection_Specification.md`.

- [ ] **Step 1: Write the failing test**

```python
def test_rlc_and_error_detection_clean_with_strip_test():
    for subj, phase, path in [
        ("biology", "practice-rlc", "practice-rlc.md"),
        ("biology", "practice-error-detection", "practice-error-detection.md"),
    ]:
        body = (pathlib.Path(__file__).resolve().parents[2]
                / "prompts" / "_general" / path).read_text(encoding="utf-8")
        _assert_clean(_gp(subj, phase))
        assert "strip" in body.lower(), f"{path} missing the Strip Test rule"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k strip_test -v`
Expected: FAIL — dead vocab present; "strip" absent.

- [ ] **Step 3: Edit both files**

Purge dead JSON-field vocab from each. Add the **Strip Test** rule each spec mandates: *remove the lesson concept and the scenario/task must stop working* (RLC §11–12; Error Detection §11). For error-detection, strengthen the "real, common mistake — not an absurd nonsense error" rule. For RLC, surface "draw concepts from the lesson when provided" as a primary rule (not buried).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k strip_test -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/practice-rlc.md prompts/_general/practice-error-detection.md tests/services/test_prompt_coverage.py
git commit -m "fix(prompts): rlc + error-detection — purge JSON vocab, add Strip Test rule"
```

---

## Task 6: Light pass — boss-arena

**Files:**
- Modify: `prompts/_general/boss-arena.md`
- Test: `tests/services/test_prompt_coverage.py`

**Source:** `docs/Infra_prompts/Gamified Practices/Boss Arena/Boss_Arena_Specification.md`.

- [ ] **Step 1: Write the failing test**

```python
def test_boss_arena_clean_with_adaptation():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "boss-arena.md").read_text(encoding="utf-8")
    _assert_clean(_gp("biology", "boss-arena"))
    low = body.lower()
    assert "weak" in low, "missing weak-skill adaptation rule"
    for part in ("why", "how", "what"):
        assert part in low
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k boss_arena -v`
Expected: FAIL — dead vocab and/or no weak-skill adaptation rule.

- [ ] **Step 3: Edit `prompts/_general/boss-arena.md`**

Purge dead vocab (describe `concept_ids`/`base_damage`/feedback as markdown sections, not JSON fields). Add the **weak-skill adaptation** rule (Boss targets the student's earlier weak spots — spec §5). Strengthen the **Why → How → What all three mandatory** rule (skipping any one = not a Boss question — spec §4/§34).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k boss_arena -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/boss-arena.md tests/services/test_prompt_coverage.py
git commit -m "fix(prompts): boss-arena — purge JSON vocab, add weak-skill adaptation + Why/How/What"
```

---

## Task 7: Compact + clean — the 4 CBP-mode games

**Files:**
- Modify: `prompts/_general/practice-memory-match.md`, `practice-tictactoe.md`, `practice-jigsaw.md`, `practice-sentence.md`
- Test: `tests/services/test_prompt_coverage.py`

**Sources:** the matching `docs/Infra_prompts/Gamified Practices/{Memory Matching, TicTacToe, Jigsaw Matching, Sentence Filling}` spec. **Deliberate divergence:** these specs are written as full Case-Based Preview cases (3 checkpoints + DPE). We have a dedicated CBP phase, so **keep these compact games** — port only the content rules (sourcing, anti-leak, the game interaction, WHY-when-math/science) and purge JSON vocab. Do NOT expand into CBP cases. Record this in the worklog.

- [ ] **Step 1: Write the failing test**

```python
_GAMES = [
    ("biology", "practice-memory-match"),
    ("physics", "practice-tictactoe"),
    ("geometriya-g7-11", "practice-jigsaw"),
    ("english", "practice-sentence"),
]

def test_games_clean_and_compact():
    for subj, phase in _GAMES:
        rendered = _gp(subj, phase)
        _assert_clean(rendered)
        # stayed compact: NOT rebuilt into a 3-checkpoint CBP case
        assert "checkpoint 3" not in rendered.lower(), f"{phase} ballooned into a CBP case"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k games_clean -v`
Expected: FAIL — dead vocab present in all 4.

- [ ] **Step 3: Edit each of the 4 files (one step each, same recipe)**

For each file: remove dead JSON-field vocab (`interaction_payload`, `interaction_mode`, `chips[]`, `allowed_assembly_types`, `expected_reasoning_keywords`, etc.); describe the game's interaction in markdown prose; keep the WHY prompt mandatory for math/science; keep it a single compact game (no 3-checkpoint/DPE expansion). Port the source rules (sourcing from lesson, anti-leak: the answer must not be obvious from formatting).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k games_clean -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/practice-memory-match.md prompts/_general/practice-tictactoe.md prompts/_general/practice-jigsaw.md prompts/_general/practice-sentence.md tests/services/test_prompt_coverage.py
git commit -m "fix(prompts): 4 CBP-mode games — purge JSON vocab, port rules, keep compact (deliberate divergence)"
```

---

## Task 8: Polish — reflection

**Files:**
- Modify: `prompts/_general/reflection.md`
- Test: `tests/services/test_prompt_coverage.py`

- [ ] **Step 1: Write the failing test**

```python
def test_reflection_instructs_top_heading_and_markdown_only():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "reflection.md").read_text(encoding="utf-8")
    low = body.lower()
    # must instruct a single top-level `# ` title in the OUTPUT (fixes validator _has_top_heading)
    assert "# " in body and "top-level" in low or "begin your output with a single `#" in low
    assert "markdown only" in low, "missing explicit markdown-only instruction"
    assert "omitting any section" in low, "missing all-sections gate"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k reflection -v`
Expected: FAIL — no markdown-only line / no explicit top-heading-in-output instruction.

- [ ] **Step 3: Edit `prompts/_general/reflection.md`**

Add an explicit output instruction so the model emits a single top-level `# ` title (e.g. add to the Output section: *"Begin your output with a single `# ` reflection title, then the five `##` sections below."*). This fixes the validator `_has_top_heading` flag the current `##`-only output triggers. Add the **OUTPUT_FORMAT_BLOCK** "Respond in Markdown only" line. Make the all-sections rule an explicit gate: *"Omitting any section is a failed output."*

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -k reflection -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/reflection.md tests/services/test_prompt_coverage.py
git commit -m "fix(prompts): reflection — emit top-level # title, markdown-only, all-sections gate"
```

---

## Task 9: Global purge guard + full suite

**Files:**
- Test: `tests/services/test_prompt_coverage.py`
- Modify: `tests/services/test_prompts_resolver.py` (fixture teardown fix)

- [ ] **Step 0: Fix the pre-existing fixture cache-pollution bug**

The `tmp_prompts`, `tmp_lang`, and `tmp_family` fixtures in `test_prompts_resolver.py`
monkeypatch `P.PROMPTS_DIR` and call `P._cache.clear()` at setup, but never restore the
cache on teardown — so when this file runs before `test_prompt_coverage.py` in the same
process, the real `_general` phases resolve against stale temp-dir cache and raise
`KeyError`. Convert each of the three fixtures from `return tmp_path` to a generator that
clears the caches on the way out:

```python
    P._cache.clear(); P._hash_cache.clear()
    yield tmp_path
    P._cache.clear(); P._hash_cache.clear()
```

Verify the previously-failing combined run is now green:
Run: `uv run python -m pytest tests/services/test_prompts_resolver.py tests/services/test_prompt_coverage.py -q`
Expected: PASS (no `KeyError`, order-independent).

- [ ] **Step 1: Write the failing global test**

```python
def test_no_dead_json_vocab_anywhere_in_general_prompts():
    gdir = pathlib.Path(__file__).resolve().parents[2] / "prompts" / "_general"
    offenders = {}
    for p in gdir.glob("*.md"):
        low = p.read_text(encoding="utf-8").lower()
        hits = [tok for tok in _DEAD_VOCAB if tok.lower() in low]
        if hits:
            offenders[p.name] = hits
    assert not offenders, f"dead JSON vocab remains: {offenders}"


def test_no_unreplaced_tokens_for_any_pair():
    for subj in flows.SUPPORTED_SUBJECTS:
        for phase in flows.flow_for(subj):
            out = _gp(subj, phase)
            assert "{{" not in out, f"unreplaced token in {subj}/{phase}"
```

- [ ] **Step 2: Run to verify it passes (all prior tasks already purged each file)**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -q`
Expected: PASS. If `test_no_dead_json_vocab_anywhere…` fails, a file was missed — fix that file, do not weaken the test.

- [ ] **Step 3: Run the entire backend suite**

Run: `uv run python -m pytest tests/ -q`
Expected: PASS except the single known pre-existing red `tests/test_config_notion.py::test_notion_defaults_disabled` (out of scope — local `.env NOTION_ENABLED=true` leak). No other failures.

- [ ] **Step 4: Commit**

```bash
git add tests/services/test_prompt_coverage.py
git commit -m "test(prompts): global guard — no dead JSON vocab, no unreplaced tokens"
```

---

## Task 10: Acceptance smoke (real CLI — sciences + humanities)

**Files:** none (verification only). Per CLAUDE.md, generation-affecting work is proven by a real CLI run, in-process, no server needed.

- [ ] **Step 1: Sciences family — Kimyo CBP + Flashcards**

Run a real in-process generation of `case-based-preview` and `flashcards` for a kimyo lesson (the kimyo book used in the Effort-A smoke). Confirm: markdown output (no JSON), the **sciences** visual policy (IMAGE default / particle-SVG), any raster uses `![placeholder: … — image gen required](placeholder)` (validator-clean), and no `{{...}}` token leakage.

- [ ] **Step 2: Humanities family — history CBP + Flashcards (exercises the authored variant)**

Run the same two phases for a history lesson. Confirm the **humanities** block is what rendered (SVG timeline/causal-chain framing, IMAGE-for-portraits with placeholder), causal claims grounded in the source, no anachronistic names. This is the review gate for the authored-not-ported humanities CBP.

- [ ] **Step 3: Validator check on the smoke outputs**

For each produced markdown, run `app.services.phase_validator.validate(phase, md, subject=…)` and confirm zero warnings (specifically: `_has_top_heading` passes, `_visuals_resolve` passes — no fabricated URLs).

- [ ] **Step 4: Record the smoke result**

Note the job/lesson ids and the per-phase verdict for the worklog (Task done by the controller, not committed here).

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- `{{FAMILY_RULES}}` mechanism + leak-guard + token-no-entry → Task 1.
- Tier-1 CBP (family-aware, authored humanities) → Task 2. Tier-1 Flashcards (enum in-prompt, schema-gone note) → Task 3.
- Tier-2a light (memory-check, rlc, error-detection, boss-arena) → Tasks 4–6.
- Tier-2b compact games (deliberate divergence) → Task 7.
- Tier-2c reflection polish (`#`, markdown-only, gate) → Task 8.
- Dead-vocab purge → enforced per-file in Tasks 2–8, globally in Task 9.
- Visual placeholder-form requirement (reviewer note 2) → fixed-string rule + Task 2/3 anchor tests.
- Flashcards enum-in-prompt / schema-gone (reviewer note 1) → Task 3 test asserts `FlashcardType` absent.
- Token-no-entry resolution (reviewer note 3) → Task 1 `test_family_no_entry_for_phase_collapses_to_empty`.
- Testing strategy (resolver, coverage, smoke) → Tasks 1, 9, 10.
- Out of scope (validator `RULES`, Uzbek WS5, the pre-existing red) → not touched; Task 9 explicitly tolerates the known red.

**2. Placeholder scan** — no "TBD/TODO" steps. Prompt-content tasks intentionally cite a source spec to port rather than inline 8 full prose blocks; this is declared in "Conventions" and bounded by fixed strings + anchor tests + smoke (not a hidden placeholder).

**3. Type consistency** — `FAMILY_RULES` / `_SUBJECT_FAMILY` names, the `phase_blocks.get(family) or phase_blocks.get("_default", "")` resolution, the `_DEAD_VOCAB` list, and `_gp`/`_assert_clean` helpers are defined once (Task 1 / Task 2) and reused consistently downstream.
