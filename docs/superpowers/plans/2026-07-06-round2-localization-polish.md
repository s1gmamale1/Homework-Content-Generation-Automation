# Round-2 Prompt/Lint Localization Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the non-blocking localization defects from the 2026-07-03 CQ re-audit (`docs/research/2026-07-03-cq-reaudit-g6-10.md` §"Round-2 polish list") so RU-medium output is RU-only student-visible text, and extend `content_lint` to stop the two live false-positives + guard the RU leak.

**Architecture:** All student-facing phase text is emitted by the shared prompts in `prompts/_general/*.md` (one `.md` per phase, `{{LANGUAGE_RULES}}` substituted per medium by `prompts.get_prompt`). The FE renders `phase_outputs.output_md` **verbatim** through ReactMarkdown (`web/src/routes/preview.tsx:236-242`); the structured game components (`BossFight`, etc.) are unmounted legacy code — so English scaffold headers are student-visible, not structural. Fixes are therefore prompt-level (reframe hardcoded Uzbek literals as language-relative + a medium-rule heading-localization directive) plus deterministic `content_lint` vocab extensions.

**Tech Stack:** Python (prompts.py, content_lint.py), Markdown prompts, pytest. No DB, no migration, no FE change.

---

## Approach & key decisions

- **Root cause:** the shared prompt `.md` bodies hardcode Uzbek literals the model is told to emit verbatim (the «Hali emas» wrong-feedback opener in rlc + boss; reflection's `Kuchli/Zaif tomonlar` headings and Uzbek example strings; CBP's pre-asserted `Completion status: passed`; the `red herring` English token). These **override** the medium `{{LANGUAGE_RULES}}` and leak into RU output. The subject label injects `"Mathematics (Matematika)"` so the model echoes "Matematika" as the RU title.
- **Decision 1 — scope (user-locked): "Systemic + flagged".** Add ONE governing directive to the **en/ru** medium rules ("render every section heading, phase title, and subject name in the output language; do not copy the parenthetical source-language subject name or leave English structural labels"), localizing ALL scaffold headers + the title at once, PLUS reframe the ~5 prescribed Uzbek literals. **uz rule is left byte-identical** (directive appended only for en/ru), so `test_prompts_output_language.py`'s uz byte-identity holds and there is no uz-medium behavior change from the directive.
- **Decision 2 — mechanism (user-locked): "inline language-relative".** Reframe prescribed literals in the shared `.md` as *intent + per-language examples* (e.g. `open with a gentle "not yet" opener in the output language — Uzbek «Hali emas», Russian «Пока нет», English «Not yet»`). No new template machinery; the model already localizes reliably everywhere except these hardcoded spots. uz keeps «Hali emas» via the example.
- **CBP completion fix** mirrors the CQ-A reflection fix: the prompt emits the *structure* the app fills after the attempt, never a pre-decided `passed`/`Needs Retry` (which is also an `english_template` lint token).
- **content_lint vocab round** (both audited `errdet_no_broken_marker` flags were vocabulary misses, not content violations): teach `_MARKER` the `yorliq` block-noun synonym, the `blok … xato` postfix / parenthesised `(Broken)` forms; teach `_REVEAL_HDR` the `oshkor` header; add a narrow **RU-leak** check (flag `Hali emas`/`Kuchli tomonlar`/`Zaif tomonlar` when `output_language=="ru"`) as a regression guard for this fix.
- **Fold-in `cbp-real-life-contract-1`** (WISHLIST / meeting 2026-07-02 #3): add the two approved opening shapes (storytelling OR question-first, fun-fact hook encouraged) to CBP's Case setup; verify against real outputs at acceptance.
- **Typos out of scope:** Szenariy/keys-stadi/Shubham/davmidagi/chiqanda are **model-generated, not prompt-sourced** (verified absent from `prompts/`), so the brief's "fix only prompt-sourced typos" makes them a no-op. Only `red herring` is prompt-sourced.
- **Verified facts:** FE renders md verbatim (no header parsing); subject labels are bilingual `"X (Uzbek)"`; existing `test_reflection_prompt.py` asserts `kuchli`/`zaif` present → the Uzbek examples must stay in the prompt; `test_prompts_output_language.py` freezes uz byte-identity + the L2 `_l2_rule` base.
- **Lane isolation:** touches only `prompts/_general/*.md` + `app/services/prompts.py` + `app/services/content_lint.py` + tests. Does **NOT** touch `agent.py`/`pipeline.py` (the `feat/extract-coverage-contract` worktree owns the extract path). No file overlap.
- **Restart note:** workers cache prompts at startup — the PR must state a worker/head restart is required for these prompt changes to take effect live.

---

### Task 1: Medium-rule heading-localization directive (en/ru only)

**Files:**
- Modify: `app/services/prompts.py` (add `_LOCALIZE_HEADINGS_CLAUSE`; append it in `_resolve_language_rule` for en/ru)
- Test: `tests/services/test_prompts_output_language.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_prompts_output_language.py`:

```python
def test_ru_medium_appends_heading_localization_directive():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "case-based-preview", output_language="ru")
    assert "in the output language" in body.lower()
    # the directive names headings, the phase title, and the subject name
    low = body.lower()
    assert "heading" in low and "title" in low and "subject name" in low


def test_en_medium_appends_heading_localization_directive():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "boss-arena", output_language="en")
    assert "in the output language" in body.lower()


def test_uz_medium_has_no_heading_localization_directive():
    # uz must stay byte-identical to legacy — no appended directive.
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "reflection", output_language="uz")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE not in body
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_prompts_output_language.py -q -k "heading_localization"`
Expected: FAIL (`AttributeError: _LOCALIZE_HEADINGS_CLAUSE` / directive absent).

- [ ] **Step 3: Implement**

In `app/services/prompts.py`, add the constant just above `_resolve_language_rule` (after the `MEDIUM_RULES` dict, ~line 135):

```python
# Appended to the language rule for non-uz media ONLY. The shared prompt bodies
# name their sections/roles with English structural labels ("Scenario", "Role",
# "Why/How/What", "Checkpoint", "Learning Block", "Completion status") and the
# subject label is injected bilingually ("Mathematics (Matematika)"). Left alone,
# the model echoes those English/Uzbek strings into ru/en output. This directive
# tells it to localize them. uz is untouched (byte-identical to legacy).
_LOCALIZE_HEADINGS_CLAUSE = (
    "\nEVERY student-visible label is part of the output language: render each "
    "section heading, the phase title, and the subject name in the output "
    "language. Do NOT copy the parenthetical source-language subject name (e.g. "
    "write the localized subject name, not \"Matematika\") and do NOT leave "
    "English structural labels (Scenario, Role, Task, Why/How/What, Checkpoint, "
    "Learning Block, Completion status) untranslated."
)
```

Then in `_resolve_language_rule`, append the clause for en/ru media (the uz path
returns before the append). Rewrite the function body:

```python
def _resolve_language_rule(subject: str, output_language: str) -> str:
    """L2 language-class subjects (English/Russian) keep their L2 TARGET regardless
    of medium, but their scaffolding BRIDGE follows the chosen medium
    (l2-bridge-follows-medium). Every other subject renders in the chosen medium
    (uz/en/ru), defaulting uz. For non-uz media a heading-localization directive is
    appended so English structural labels + the source-language subject name are
    localized too (uz stays byte-identical)."""
    sd = subjects.REGISTRY.get(subject)
    if sd and sd.language in ("english", "russian"):
        rule = _l2_rule(sd.language, output_language)
    else:
        rule = MEDIUM_RULES.get(output_language, MEDIUM_RULES["uz"])
    if (output_language or "").lower() in ("en", "ru"):
        rule = rule + _LOCALIZE_HEADINGS_CLAUSE
    return rule
```

- [ ] **Step 4: Run to verify pass (incl. the frozen byte-identity tests)**

Run: `uv run python -m pytest tests/services/test_prompts_output_language.py -q`
Expected: PASS (new tests + the existing uz byte-identity + L2 frozen-base tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/prompts.py tests/services/test_prompts_output_language.py
git commit -m "polish: localize headings/title/subject-name for en/ru media"
```

---

### Task 2: practice-rlc.md — «Hali emas» opener + `red herring` language-relative

**Files:**
- Modify: `prompts/_general/practice-rlc.md` (lines ~34, 50, 61)
- Test: `tests/services/test_round2_localization.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_round2_localization.py`:

```python
import pathlib

_P = pathlib.Path(__file__).resolve().parents[2] / "prompts" / "_general"


def _read(name: str) -> str:
    return (_P / name).read_text(encoding="utf-8")


def test_rlc_not_yet_opener_is_language_relative():
    text = _read("practice-rlc.md")
    low = text.lower()
    # intent kept, framed for the output language, with the uz example still present
    assert "output language" in low
    assert "Hali emas" in text            # uz example retained
    # not prescribed as THE literal string to emit verbatim
    assert 'open with **"Hali emas"**' not in text


def test_rlc_red_herring_is_language_relative():
    text = _read("practice-rlc.md")
    # the English term may appear as a gloss, but the model is told to name it in
    # the output language (so it stops emitting the bare English token verbatim)
    low = text.lower()
    assert "output language" in low
    # a localized name is offered alongside the English gloss
    assert "chalg" in low or "отвлека" in low  # uz "chalg'ituvchi" / ru "отвлекающий"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_round2_localization.py -q -k rlc`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `prompts/_general/practice-rlc.md`:

Replace the Context red-herring line (`~line 33-34`):
```
  For G7+ include ONE irrelevant datum the student must dismiss, and note (to
  yourself, in the final summary) that it is the distracting datum (the "red
  herring") — when you name it in the summary, name it in the OUTPUT LANGUAGE
  (Uzbek "chalg'ituvchi ma'lumot", Russian «отвлекающий факт»), never the bare
  English "red herring".
```

Replace the Wrong-feedback line (`~line 50`):
```
  - **Wrong feedback** — MUST open with a gentle "not yet" opener in the OUTPUT
    LANGUAGE (Uzbek «Hali emas», Russian «Пока нет», English «Not yet») — never a
    flat "wrong" (Uzbek "Noto'g'ri", Russian «Неправильно»); re-aim with a
    guiding question, not the answer.
```

Replace the Final-summary red-herring clause (`~line 61`):
```
  misses, and (G7+) which datum was the distracting one (the "red herring", named
  in the output language) and why it didn't matter.
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/services/test_round2_localization.py -q -k rlc`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/practice-rlc.md tests/services/test_round2_localization.py
git commit -m "polish: rlc — language-relative not-yet opener + red-herring naming"
```

---

### Task 3: boss-arena.md — «Hali emas» opener language-relative

**Files:**
- Modify: `prompts/_general/boss-arena.md` (line ~47)
- Test: `tests/services/test_round2_localization.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/services/test_round2_localization.py`:

```python
def test_boss_not_yet_opener_is_language_relative():
    text = _read("boss-arena.md")
    low = text.lower()
    assert "output language" in low
    assert "Hali emas" in text  # uz example retained
    assert 'opens with **"Hali emas"**' not in text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_round2_localization.py -q -k boss`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `prompts/_general/boss-arena.md`, replace the Wrong feedback line (`~line 47-48`):
```
  - **Wrong** — opens with a gentle "not yet" opener in the OUTPUT LANGUAGE
    (Uzbek «Hali emas», Russian «Пока нет», English «Not yet»); never a flat
    "wrong" (Uzbek "Noto'g'ri", Russian «Неправильно»). Re-point the student with
    a guiding question, not the answer.
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/services/test_round2_localization.py -q -k boss`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/boss-arena.md tests/services/test_round2_localization.py
git commit -m "polish: boss-arena — language-relative not-yet opener"
```

---

### Task 4: case-based-preview.md — de-assert completion + opening-shape contract

**Files:**
- Modify: `prompts/_general/case-based-preview.md` (Case setup ~line 37-41; Feedback summary part 4 ~line 123; Completion rules; self-check #12 ~line 170)
- Test: `tests/services/test_round2_localization.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_round2_localization.py`:

```python
def test_cbp_does_not_pre_assert_completion():
    text = _read("case-based-preview.md")
    low = text.lower()
    # The app owns pass/redo AFTER the attempt — the prompt must not tell the model
    # to emit a decided "passed"/"Needs Retry" status (same defect class CQ-A fixed
    # in reflection). "Needs Retry" is also an english_template lint token.
    assert "needs retry" not in low, "CBP still prescribes a decided completion label"
    assert "`passed`" not in text, "CBP still prescribes a decided 'passed' status"
    assert "app" in low, "CBP must state the app owns pass/redo"


def test_cbp_names_two_approved_opening_shapes():
    # meeting 2026-07-02 #3 / cbp-real-life-contract-1
    text = _read("case-based-preview.md")
    low = text.lower()
    assert "storytelling" in low or "story" in low
    assert "question-first" in low or "question first" in low
    assert "fun-fact" in low or "fun fact" in low
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_round2_localization.py -q -k cbp`
Expected: FAIL.

- [ ] **Step 3: Implement**

(a) In `prompts/_general/case-based-preview.md`, extend the Case-setup line in the
canonical structure block (`~line 37-41`) to name the two approved opening shapes:
```
1. Case setup          — student role, narrative, clear task. Open with a real-life
                         case in ONE of the two approved shapes: **storytelling**
                         (a short concrete situation) OR **question-first** (pose
                         the hook question up front, resolve it at the end); a
                         fun-fact hook is encouraged. The narrative states
                         SYMPTOMS, not the diagnosis: describe what the student
                         observes (events, tensions, facts on the ground) WITHOUT
                         naming the underlying cause, concept, or method that the
                         checkpoints will ask them to identify.
```

(b) Rewrite Feedback-summary part 4 (`~line 123`) to emit structure, not a verdict:
```
4. **Completion status** — describe the redo route the student **app** applies
   AFTER the attempt (the app owns pass/redo; there is no attempt yet at
   generation). State it conditionally ("if the app marks a redo, return to …")
   — never emit a decided status such as a bare pass label or "Not Completed".
```

(c) In the "CBP canonical structure" list, change section 10 from `Completion rules`
to reflect the app-owned framing:
```
10. Redo route         — the conditional next-step the app applies after the attempt
```
(update the fenced structure block at `~line 51` accordingly).

(d) Rewrite self-check #12 (`~line 170`):
```
12. ✓ Feedback summary has all four parts, and the completion part describes a
    CONDITIONAL app-owned redo route (no decided pass/fail label)?
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/services/test_round2_localization.py -q -k cbp`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/case-based-preview.md tests/services/test_round2_localization.py
git commit -m "polish: CBP — de-assert completion (app-owned) + two opening shapes"
```

---

### Task 5: reflection.md — headings + example strings language-relative

**Files:**
- Modify: `prompts/_general/reflection.md` (Summary example ~line 27; Strong/Weak headings ~line 33,35; thinking questions ~line 42-44; retake rule ~line 53; closing line ~line 62)
- Test: `tests/services/test_round2_localization.py` + keep `tests/services/test_reflection_prompt.py` green

- [ ] **Step 1: Write the failing test**

Append to `tests/services/test_round2_localization.py`:

```python
def test_reflection_examples_are_language_relative():
    text = _read("reflection.md")
    low = text.lower()
    # headings + example strings framed as output-language examples, not literals
    assert "output language" in low
    # uz examples retained (test_reflection_prompt.py depends on kuchli/zaif)
    assert "Kuchli tomonlar" in text and "Zaif tomonlar" in text
    # a Russian example is offered for the strong/weak headings
    assert "Сильные" in text or "Слабые" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_round2_localization.py -q -k reflection`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `prompts/_general/reflection.md`:

Summary example (`~line 27`) — frame as output-language:
```
Phrase it in the OUTPUT LANGUAGE, e.g. Uzbek "Bugun Siz [concept] ni
o'rgandingiz…", Russian «Сегодня Вы изучили [concept]…».
```

Strong/Weak headings (`~line 33` and `~line 35`) — keep uz literal as the example,
name the heading generically + give the ru form:
```
- **Strong-points heading** (Uzbek **"Kuchli tomonlar:"**, Russian **«Сильные
  стороны:»**): name 1–2 concepts from THIS lesson that the Case-Based Preview /
  Boss Arena treated as core — the ones a confident student should have handled.
- **Weak-points heading** (Uzbek **"Zaif tomonlar:"**, Russian **«Слабые
  стороны:»**): name 1–2 concepts from THIS lesson that are the most error-prone /
  worth re-checking (name them; do not invent a score or a result).
```

Thinking-questions list (`~line 42-44`) — prefix the list with an output-language
note (leave the uz examples as examples):
```
Pick ONE (rotate), phrased in the OUTPUT LANGUAGE. Prefer questions tied to THIS
session's performance over generic curiosity (examples shown in Uzbek):
```

Retake rule (`~line 53`) — frame as output-language:
```
- Retake rule, in the output language: **"Xuddi shu tushunchalar, lekin xuddi shu
  savollar emas"** (Russian: «Те же понятия, но не те же вопросы») — same concepts,
  not the same questions.
```

Closing line (`~line 62`) — frame as output-language:
```
One encouraging line, unconditional (no score branch), in the OUTPUT LANGUAGE, e.g.
Uzbek "Har bir mashq miyangizni kuchaytiradi. Ertaga davom etamiz!"
```

- [ ] **Step 4: Run to verify pass (both files)**

Run: `uv run python -m pytest tests/services/test_round2_localization.py tests/services/test_reflection_prompt.py -q`
Expected: PASS (reflection existing tests still see `kuchli`/`zaif`/`redo`/`app`; no `needs retry`).

- [ ] **Step 5: Commit**

```bash
git add prompts/_general/reflection.md tests/services/test_round2_localization.py
git commit -m "polish: reflection — language-relative headings + example strings"
```

---

### Task 6: content_lint.py — marker vocab round + RU-leak guard

**Files:**
- Modify: `app/services/content_lint.py` (`_MARKER`, `_REVEAL_HDR`, new RU-leak check + dispatcher wiring)
- Test: `tests/services/test_content_lint.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_content_lint.py` (mirror the audit's literal strings):

```python
from app.services.content_lint import lint_phase

def _codes(findings):
    return {f.code for f in findings}

# --- marker vocab: these are NOT "no broken marker" (were live false-positives) --

def test_errdet_recognizes_yorliq_block_noun():
    md = (
        "# Xatoni top\n\n## Bloklar\n"
        "1-yorliq: a+b\n2-yorliq: a-b (Broken)\n3-yorliq: a*b\n\n"
        "## Oshkor qilish\n2-yorliq to'g'risi: ...\n"
    )
    codes = _codes(lint_phase("practice-error-detection", md, subject="matematika", output_language="uz"))
    assert "errdet_no_broken_marker" not in codes

def test_errdet_recognizes_xato_postfix_and_paren_broken():
    md = "# t\n\n2-blok (BU BLOK XATO)\n\n## Reveal\n2-blok\n"
    codes = _codes(lint_phase("practice-error-detection", md, subject="matematika", output_language="uz"))
    assert "errdet_no_broken_marker" not in codes

def test_errdet_oshkor_reveal_header_recognized_for_mismatch():
    # body marks block 2, reveal (Oshkor) names block 3 -> genuine mismatch fires
    md = "# t\n\nBlok 2 noto'g'ri\n\n## Oshkor qilish\nBlok 3 ...\n"
    codes = _codes(lint_phase("practice-error-detection", md, subject="matematika", output_language="uz"))
    assert "errdet_reveal_mismatch" in codes

# --- RU-leak guard (regression guard for the localization fix) -----------------

def test_ru_leak_flags_uzbek_template_tokens():
    md = "## Kuchli tomonlar\n...\n**Hali emas** — ...\n"
    codes = _codes(lint_phase("practice-rlc", md, subject="matematika", output_language="ru"))
    assert "ru_uzbek_leak" in codes

def test_ru_leak_silent_on_uz_output():
    md = "## Kuchli tomonlar\n**Hali emas** — ...\n"
    codes = _codes(lint_phase("practice-rlc", md, subject="matematika", output_language="uz"))
    assert "ru_uzbek_leak" not in codes
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_content_lint.py -q -k "yorliq or xato_postfix or oshkor or ru_leak"`
Expected: FAIL (yorliq/postfix/paren markers unrecognized → false `errdet_no_broken_marker`; `oshkor` header not seen → no mismatch; `ru_uzbek_leak` unknown code).

- [ ] **Step 3: Implement**

In `app/services/content_lint.py`:

(a) Add a block-noun fragment and rebuild `_BID`/`_MARKER`/`_REVEAL_HDR` to accept
`yorliq` and the new marker forms. Replace lines ~110-127:

```python
_APOS = r"['ʻʼ‘’]"
_NOT = rf"noto{_APOS}?g{_APOS}?ri"
# Block noun: Uzbek "blok"/"block" OR "yorliq" (label) — the audit found blocks
# named "N-yorliq" instead of "N-blok".
_BLK = r"(?:blo(?:k|ck)|yorli(?:q|g'|gʻ))"
# A block id in EITHER Uzbek order: cardinal "Blok 4" or ordinal "4-blok".
_BID = rf"(?:{_BLK}\s*(\d+)|(\d+)\s*-\s*{_BLK})"
# Every broken-block marker. The noun/ordinal-with-NOT forms REQUIRE a digit (so
# digitless prose never matches); each captures the id.
_MARKER = re.compile(
    rf"(?i)"
    rf"{_BLK}\s*(?P<id_pre>\d+)\s+{_NOT}"                    # "Blok 4 noto'g'ri"
    rf"|(?P<id_ord>\d+)\s*-\s*{_BLK}\s+{_NOT}"               # "4-blok noto'g'ri"
    rf"|{_NOT}\s+{_BLK}\s*(?P<id_post>\d+)?"                 # "noto'g'ri blok[ 4]"
    rf"|{_NOT}\s+(?P<id_post_ord>\d+)\s*-\s*{_BLK}"          # "noto'g'ri 4-blok"
    rf"|{_BLK}\s*(?P<id_xato>\d+)?\s+xato"                   # "blok[ 4] xato" / "BU BLOK XATO"
    rf"|(?P<id_xato_ord>\d+)\s*-\s*{_BLK}\s+xato"            # "4-blok xato"
    rf"|xato\s+{_BLK}\s*(?P<id_xatop>\d+)?"                  # "xato blok[ 4]"
    rf"|xato\s+(?P<id_xatop_ord>\d+)\s*-\s*{_BLK}"           # "xato 4-blok"
    rf"|(?P<eng>this is the broken block|broken block)"      # English markers, no id
    rf"|(?P<eng2>\(\s*broken\s*\))"                          # parenthesised "(Broken)"
)
_REVEAL_HDR = re.compile(r"(?im)^[ \t]*#{1,6}[ \t]*(reveal|ochish|oshkor)\b")
_BLOCK_ID = re.compile(rf"(?i)\b{_BID}")
```

(b) In `_lint_error_detection`, extend the `mid` extraction to the new groups and
the line-scan recovery to the new id-less English marker. Replace the `mid = …`
line (~146) and the recovery condition (~151):

```python
        mid = (gd.get("id_pre") or gd.get("id_ord") or gd.get("id_post")
               or gd.get("id_post_ord") or gd.get("id_xato") or gd.get("id_xato_ord")
               or gd.get("id_xatop") or gd.get("id_xatop_ord"))
        # Line-scan recovery is ONLY for the id-less English markers (whose id may
        # sit on the same line). The noto'g'ri/xato prose forms must NOT borrow a
        # nearby id — that produced spurious multiple/mismatch findings.
        if mid is None and (gd.get("eng") or gd.get("eng2")):
            bm = _BLOCK_ID.search(_line_around(output_md, m.start()))
            mid = (bm.group(1) or bm.group(2)) if bm else None
```

(c) Add the RU-leak check + wire it into the dispatcher. After `_CALQUES` (~line 61):

```python
# Uzbek template artifacts that must never survive into RU-medium student text
# (regression guard for the round-2 localization fix; narrow to avoid FPs).
_RU_UZBEK_LEAK = [
    re.compile(r"\bHali emas\b", re.IGNORECASE),
    re.compile(r"\bKuchli tomonlar\b", re.IGNORECASE),
    re.compile(r"\bZaif tomonlar\b", re.IGNORECASE),
]


def _lint_ru_leak(output_md: str, output_language: str) -> list[LintFinding]:
    if (output_language or "").lower() != "ru":
        return []
    out: list[LintFinding] = []
    seen: set[str] = set()
    for rx in _RU_UZBEK_LEAK:
        m = rx.search(output_md)
        if m and m.group(0).lower() not in seen:
            seen.add(m.group(0).lower())
            out.append(LintFinding("ru_uzbek_leak", f"Uzbek template token in RU output: {m.group(0)!r}"))
    return out
```

In `lint_phase`, add after the language findings (~line 185):
```python
    findings = _lint_language(output_md, output_language)
    findings += _lint_ru_leak(output_md, output_language)
```

(d) Update the module docstring "Known limitations" to note the `yorliq`/`xato`/
`(Broken)`/`oshkor` vocab is now recognized, and fold in the **CQ-B en-gate
docstring one-liner still owed** (from `cq-cluster-r21-progress`): split-intent note
that the `output_language=="en"` skip also drops universal artifacts (meta-preamble/
`Needs Retry`/`Mode:`) — acceptable while warn-only + en-medium unused.

- [ ] **Step 4: Run to verify pass (full lint suite — no regressions on real fixtures)**

Run: `uv run python -m pytest tests/services/test_content_lint.py -q`
Expected: PASS (new vocab + RU-leak tests AND the 31 existing real-fixture tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/content_lint.py tests/services/test_content_lint.py
git commit -m "polish: content_lint — yorliq/xato/(Broken)/oshkor vocab + RU-leak guard"
```

---

### Task 7: check_prompt_render regression sweep

**Files:**
- Verify only (no code): run `scripts/check_prompt_render.py` over uz/en/ru combos.

- [ ] **Step 1: Run the render harness for all three media**

Run: `uv run python -m scripts.check_prompt_render` (inspect its args; render every
phase × {uz,en,ru} × a couple of subject families). Confirm: no template variable
left unsubstituted (`{{…}}`), the en/ru renders carry `_LOCALIZE_HEADINGS_CLAUSE`,
uz renders do not, and no exception.
Expected: clean render for every combo.

- [ ] **Step 2: Full suite green**

Run: `uv run python -m pytest tests/ -q`
Expected: PASS (no `RUN_DB_INTEGRATION`).

- [ ] **Step 3: Commit (only if the harness needed a combo added)**

```bash
# only if scripts/check_prompt_render.py was extended
git add scripts/check_prompt_render.py
git commit -m "polish: check_prompt_render covers uz/en/ru heading localization"
```

---

### Task 8: Acceptance smoke — real api UZ + RU lessons (finish gate)

**This is the acceptance gate for a generation-affecting change — run over `transport=api` (Vertex/Anthropic), per CLAUDE.md.** Bounded: ONE UZ lesson + ONE RU lesson (single-lesson smokes are within the no-mass-gen money rule; report cost). Credentials note: the CLI shell `.env` historically had a stale Windows Vertex SA-key path — if the api smoke cannot authenticate here, this gate is handed to the gatekeeper (who has run the paid smokes for CQ-C/CQ-D), and the PR states so explicitly.

- [ ] **Step 1: Generate ONE UZ + ONE RU lesson end-to-end** (a math lesson, full packet), `transport=api`, on the round-2 prompts.

- [ ] **Step 2: Human-read the RU output** — assert zero «Hali emas», zero `Kuchli/Zaif tomonlar` (RU: «Сильные/Слабые стороны»), no bare `red herring`, no "Matematika" title (RU: «Математика»), no English scaffold headers (Scenario/Role/Why/How/What/Checkpoint). CBP emits no pre-decided completion status.

- [ ] **Step 3: Human-read the UZ output** — assert unregressed: «Hali emas» opener still present, `Kuchli/Zaif tomonlar` headings present, error-detection marker + reveal present, CBP opens with a real-life case.

- [ ] **Step 4: Run `content_lint` over both packets' phases** — assert no `ru_uzbek_leak` on the RU packet, no false `errdet_no_broken_marker` on either.

- [ ] **Step 5: Verify `cbp-real-life-contract-1`** — the two generated CBPs (+ any golden CBP output available) open in one of the two approved shapes with a fun-fact hook.

- [ ] **Step 6: Record cost** in the PR body.

---

### Task 9: Finish (worklog 0117 + closeout)

- [ ] Worklog entry `0117` in `docs/memory/MASTER_MEMORY.md` + row in `docs/memory/INDEX.md`.
- [ ] Close shipped WISHLIST lines: `cbp-real-life-contract-1` (and note the CQ-B en-gate docstring one-liner was folded here).
- [ ] De-stale `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` if the localization directive / lint vocab changes their described behavior (likely a one-line note in the content_lint + language-rules sections).
- [ ] `git mv` this plan into `docs/superpowers/plans/shipped/`.
- [ ] **Rebase-check before PR:** `git fetch origin` then `git log HEAD..origin/Nggaev-v2` — if base moved, rebase onto `origin/Nggaev-v2`, re-run the suite.
- [ ] PR titled `[round-2] prompt/lint localization polish` — body notes: **worker/head restart required** (prompts cached at startup); acceptance smoke results + cost; lane isolation vs the extract-coverage-contract worktree.
- [ ] Route to Gatekeeper-2 (no self-merge).
```
