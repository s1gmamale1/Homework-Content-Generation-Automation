# English Target-Language Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `english` (L2) lessons generate English target-language content with an Uzbek "Siz" scaffolding bridge (CEFR-leveled), while every other subject stays formal-Uzbek — via a loader-side `{{LANGUAGE_RULES}}` substitution.

**Architecture:** Two language-policy blocks (`_LANG_UZBEK` default, `_LANG_ENGLISH`) live in `app/services/prompts.py`; `get_prompt` substitutes a `{{LANGUAGE_RULES}}` token by subject (mirrors the existing `{{SUBJECT}}` substitution). The 7 `prompts/_general/*.md` swap their hardcoded language directive for the token. No schema, no flow change.

**Tech Stack:** Python 3.13, Pydantic, pytest, `uv`. Spec: `docs/superpowers/specs/2026-06-01-english-target-language-design.md`.

---

## Conventions
- Tests: `"C:/Users/Recruiter/AppData/Roaming/Python/Python314/Scripts/uv.exe" run python -m pytest <args>`.
- Branch: `Nggaev-v2` directly (no worktree; another session shares the repo — touch only the files listed).
- Baseline: `uv run python -m pytest tests/ -q` is green (222) before starting.
- **Never edit `prompts/<subject>/*`** (read-only). Only `prompts/_general/*` + `app/services/prompts.py` + tests + the one doc.

## File Structure
- Modify: `app/services/prompts.py` (language blocks + `{{LANGUAGE_RULES}}` substitution)
- Modify: the 7 `prompts/_general/*.md` (swap directive → token; flashcards gets a per-phase hint)
- Modify: `tests/services/test_prompts_resolver.py` (extend), `tests/services/test_prompt_coverage.py` (add token-presence guard)
- Modify (doc): `docs/nets_general_prompts_mvp_design.md` §3.3

---

## Task 1: Language blocks + `{{LANGUAGE_RULES}}` substitution

**Files:**
- Modify: `app/services/prompts.py`
- Test: `tests/services/test_prompts_resolver.py`

- [ ] **Step 1: Write the failing test** (append to `tests/services/test_prompts_resolver.py`)

```python
@pytest.fixture
def tmp_lang(tmp_path, monkeypatch):
    (tmp_path / "_general").mkdir()
    (tmp_path / "_general" / "demo.md").write_text(
        "Title for {{SUBJECT}}.\n\n{{LANGUAGE_RULES}}\n", encoding="utf-8")
    monkeypatch.setattr(P, "PROMPTS_DIR", tmp_path)
    P._cache.clear(); P._hash_cache.clear()
    return tmp_path


def test_english_subject_gets_english_language_block(tmp_lang):
    out = P.get_prompt("english", "demo")
    assert "{{LANGUAGE_RULES}}" not in out
    assert "English (L2)" in out          # English-target block present
    assert "Siz" in out                    # UZ bridge retained


def test_nonenglish_subject_gets_uzbek_block(tmp_lang):
    out = P.get_prompt("physics", "demo")
    assert "{{LANGUAGE_RULES}}" not in out
    assert "formal Uzbek" in out
    assert "English (L2)" not in out       # no English-target leak


def test_language_token_substituted_alongside_subject(tmp_lang):
    out = P.get_prompt("physics", "demo")
    assert "{{SUBJECT}}" not in out and "{{LANGUAGE_RULES}}" not in out
```

- [ ] **Step 2: Run, verify it FAILS**

Run: `uv run python -m pytest tests/services/test_prompts_resolver.py -k language -v`
Expected: FAIL (`{{LANGUAGE_RULES}}` not substituted; markers absent).

- [ ] **Step 3: Edit `app/services/prompts.py`** — add the two blocks + map after `SUBJECT_LABELS`, and one substitution line in `get_prompt`.

Add after the `SUBJECT_LABELS = {...}` block:

```python
_LANG_UZBEK = (
    "All student-facing text in natural, formal Uzbek (\"Siz\", never \"sen\"). "
    "Preserve every term, formula, number, unit, and symbol exactly as in the "
    "source. Modern professional (non-bazaar) contexts."
)

_LANG_ENGLISH = (
    "This is an English (L2) lesson for native-Uzbek learners.\n"
    "Governing principle: the thing being LEARNED is in English; everything that "
    "HELPS them learn it is in Uzbek (\"Siz\").\n"
    "- In English: the target vocabulary, example sentences, passages/texts, "
    "collocations, grammar items, and anything the learner must read or produce.\n"
    "- In formal Uzbek (\"Siz\"): all scaffolding — task instructions, framing, "
    "hints, explanations, feedback, and the DPE/reasoning prompts (the UZ bridge).\n"
    "- CEFR (A1–B2): if the source shows a grade, level the English via "
    "G5→A1, G6→A1+, G7→A2, G8→A2+, G9→B1, G10→B1+, G11→B2; otherwise infer from the "
    "source. CEFR controls sentence length, tenses, and vocabulary range — never "
    "exceed the level (no B2 vocabulary in an A1/G5 lesson)."
)

LANGUAGE_RULES = {"english": _LANG_ENGLISH, "_default": _LANG_UZBEK}
```

In `get_prompt`, immediately after the existing `{{SUBJECT}}` replace line
(`body = body.replace("{{SUBJECT}}", SUBJECT_LABELS.get(subject, subject))`), add:

```python
    body = body.replace(
        "{{LANGUAGE_RULES}}",
        LANGUAGE_RULES.get(subject, LANGUAGE_RULES["_default"]),
    )
```

- [ ] **Step 4: Run, verify PASS**

Run: `uv run python -m pytest tests/services/test_prompts_resolver.py -v`
Expected: all pass (the 3 Path-A resolver tests + the 3 new language tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/prompts.py tests/services/test_prompts_resolver.py
git commit -m "feat(flow-v2): {{LANGUAGE_RULES}} resolver block (English-target vs Uzbek)"
```

---

## Task 2: Swap the token into the 7 prompts + flashcards hint + presence guard

**Files:**
- Modify: `prompts/_general/{case-based-preview,flashcards,memory-check,practice-rlc,practice-error-detection,boss-arena,reflection}.md`
- Test: `tests/services/test_prompt_coverage.py`

- [ ] **Step 1: Add the failing presence test** — append to `tests/services/test_prompt_coverage.py`

```python
import pathlib

def test_every_general_prompt_has_language_token():
    gdir = pathlib.Path(__file__).resolve().parents[2] / "prompts" / "_general"
    missing = [p.name for p in gdir.glob("*.md")
               if "{{LANGUAGE_RULES}}" not in p.read_text(encoding="utf-8")]
    assert not missing, f"prompts missing {{{{LANGUAGE_RULES}}}}: {missing}"
```

- [ ] **Step 2: Run, verify it FAILS**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py::test_every_general_prompt_has_language_token -v`
Expected: FAIL listing all 7 prompts (none has the token yet).

- [ ] **Step 3: Swap the directive → token in each of the 7 prompts.**

In EACH file, find the existing hardcoded language directive and **replace its text** with the literal token `{{LANGUAGE_RULES}}` (keep any surrounding `## Language` heading). The directive takes two forms — handle per file:
- `## Language` block form (`case-based-preview.md`, `boss-arena.md`, `practice-error-detection.md`, `practice-rlc.md`): under the `## Language` heading, replace the Uzbek directive sentence(s) with the single line `{{LANGUAGE_RULES}}`.
- one-line bullet form (`flashcards.md`, `memory-check.md`, `reflection.md`): replace the `- Language: Uzbek, "Siz" formal` bullet with a two-line block:
  ```
  ## Language

  {{LANGUAGE_RULES}}
  ```

**CRITICAL — remove the hardcoded Uzbek directive, do not leave it alongside the token.** A leftover "all text in Uzbek" line would contradict the English block for `english` lessons. Grep each file after editing to confirm no standalone "all student-facing text in ... Uzbek" / "Language: Uzbek" directive remains *outside* the token.

**LEAVE embedded Uzbek EXAMPLE strings as-is** — `practice-rlc.md` (`prediction_prompt`/role examples like "Hisoblashdan oldin…") and `reflection.md` (literal Uzbek output examples like "Bugun Siz…") are scaffolding examples and must stay Uzbek. Only the *policy directive* is being swapped.

- [ ] **Step 4: Add the flashcards per-phase hint.** In `prompts/_general/flashcards.md`, add this line in the card-format section (where front/back are described):

```
For an English (L2) lesson: the card front is the English target item (word / phrase / grammar structure); the back, hint, and explanation are the Uzbek bridge (gloss / meaning / usage note). For every other subject, both sides follow the Language rules above (Uzbek).
```

- [ ] **Step 5: Run the presence guard + full suite**

Run: `uv run python -m pytest tests/services/test_prompt_coverage.py -v`
Expected: PASS, including `test_every_general_prompt_has_language_token` and the existing per-phase coverage (resolved bodies still have no `{{SUBJECT}}`/`{{LANGUAGE_RULES}}` leftovers — `get_prompt` substitutes both).
Run: `uv run python -m pytest tests/ -q` → green.

- [ ] **Step 6: Commit**

```bash
git add prompts/_general/ tests/services/test_prompt_coverage.py
git commit -m "feat(flow-v2): general prompts use {{LANGUAGE_RULES}} (English-target for english)"
```

---

## Task 3: Full verify + 2-phase English smoke + doc fix + worklog

**Files:** (verification); `docs/nets_general_prompts_mvp_design.md`; `docs/memory/*`

- [ ] **Step 1: Full suite green** — `uv run python -m pytest tests/ -q 2>&1 | tail -5` (0 failures/errors; record count).

- [ ] **Step 2: Real `claude` smoke — TWO structurally-different english phases.**
Write a throwaway `smoke_english.py` at repo root that, for subject `english`, builds the prompt via `get_prompt("english", phase)` for **`flashcards`** AND **`memory-check`**, feeds a small Uzbek-textbook English `lesson_context` that names a grade (e.g. "Grade 7 English, Unit 4 — Daily routines", a few A2-level sentences), runs each through the real `claude` CLI via the structured runner (read `app/services/pipeline.py::_execute_phase` for the call shape; pass `homework_job_id=None`/`phase_output_id=None` — DB write is best-effort), and `model_validate`s each result. Then PRINT, for human check:
  - flashcards: a few cards — confirm **front = English target item, back/explanation = Uzbek**.
  - memory-check: an item — confirm **English options/items with Uzbek prompt + reason**.
  - both: confirm the English reads **CEFR-appropriate for Grade 7 (~A2)** — not B2 vocabulary.
Run `uv run python smoke_english.py`. Both phases must `model_validate` clean AND show the correct per-phase split + level. **Delete `smoke_english.py` afterward (do not commit it).** If the claude CLI is unreachable in this env, mark the smoke "NOT run — <reason>; command: uv run python smoke_english.py" in the worklog (honesty convention) — do not fake it.

- [ ] **Step 3: Doc fix.** In `docs/nets_general_prompts_mvp_design.md` §3.3, replace the bullet that says the `english` subject surfacing Uzbek is "acceptable as-is for the MVP" with a line noting English-target handling now exists via `{{LANGUAGE_RULES}}` (see `docs/superpowers/specs/2026-06-01-english-target-language-design.md`). Keep the rest of §3.3.

- [ ] **Step 4: Worklog.** Append the next-ID entry to `docs/memory/MASTER_MEMORY.md` + the matching `INDEX.md` row (follow existing style). Record: the `{{LANGUAGE_RULES}}` mechanism, English-target/UZ-bridge model (grounded in `english/*`), the NEW grade→CEFR ladder (assumption, smoke-validated), 7 prompts updated + flashcards hint, commits, suite count + smoke result, and the note that inert `prompts/english/*` still reference the deleted `classify.md` (fix when `USE_SUBJECT_PROMPTS` revives).

- [ ] **Step 5: Commit**

```bash
git add docs/nets_general_prompts_mvp_design.md docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs: English target-language handling shipped + worklog"
```

---

## Self-Review (done while writing)
- **Spec coverage:** mechanism §3 → Task 1; blocks §4 (incl. NEW grade→CEFR ladder) → Task 1 constants; prompt swap §5 + flashcards hint + embedded-Uzbek-stays → Task 2; tests §6 (resolver + presence + 2-phase smoke w/ split & CEFR checks) → Tasks 1–3; doc fix §7 → Task 3 Step 3; reading-out-of-scope §8 → not built (correct); worklog classify.md note §8 → Task 3 Step 4. All covered.
- **Placeholders:** none — all code/edits shown; per-file directive forms enumerated.
- **Consistency:** `{{LANGUAGE_RULES}}`, `LANGUAGE_RULES`, `_LANG_ENGLISH`/`_LANG_UZBEK`, `get_prompt` used identically across tasks; substitution placed right after the verified `{{SUBJECT}}` line.
- **No subject-prompt edits:** Task 2 touches only `prompts/_general/`; embedded Uzbek examples preserved.
