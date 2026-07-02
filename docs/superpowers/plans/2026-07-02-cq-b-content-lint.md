# CQ-B — Deterministic Content Lint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure-Python, no-LLM, no-cost post-phase content lint that surfaces the two mechanical defect classes the 5-packet audit found (ROADMAP R21.3 + R21.4) as advisory `validation_warnings` — warn-only, never gating a regen, never failing a job.

**Architecture:** One new stateless module `app/services/content_lint.py` exposing `lint_phase(...) -> list[LintFinding]` + `findings_to_warnings(...) -> list[str]`. Wired into `pipeline._run_phase` immediately before the phase-output save, appending `lint:`-prefixed strings to the existing `warnings` list that already flows into `phase_outputs.validation_warnings`. Mirrors the existing `phase_judge._fidelity_flags` advisory precedent exactly — no schema change, no migration, no new column.

**Tech Stack:** Python 3, `re`, pytest. Fixtures are the real audited phase outputs (jobs `8f734563`, `3ca0da6f` from the `edu_copy` audit), committed under `tests/fixtures/content_lint/`.

---

## Approach & key decisions

- **Policy = WARN-ONLY (locked-by-default; the ONE open decision).** On any lint finding, fold `lint:<code>: <msg>` into `validation_warnings` — identical to the `_fidelity_flags` precedent (`phase_judge.py:94`) and identical wiring to how judge warnings already reach the DB. Rejected **regen-once-on-fail**: it costs a paid model call per fire (contradicts CQ-B's explicit "no LLM, no cost" mandate), adds a `_run_with_failover` code path, and a regen is not guaranteed to remove a Cyrillic splice or English label. Findings feed R20/human-review + Monitor. *(Asked the user; away — proceeding on the recommendation. Overridable at this single approval gate.)*
- **R21.3 is a FORMAT validator, not an answer-key checker (90%-bar pushback on the brief).** Verified against real data: BOTH audited error-detection outputs are format-clean — exactly one marked broken block + one Reveal each. Their actual defects (key endorses a wrong `+1` sign in `8f734563`; wrong final result carried forward in `3ca0da6f`) are **semantic** — un-catchable without re-solving the algebra, which is a no-LLM impossibility and is **CQ-C's** (R21.2 answer-key solver) job. R21.3 therefore delivers the deterministic contract the prompt *itself* already promises but nothing enforces (`practice-error-detection.md:51` "Any other count is rejected by the validator"): exactly-one-broken-block-marker + Reveal consistency. The two real outputs are used as **must-PASS (no-false-positive)** fixtures; must-FLAG cases are minimal mutations of them.
- **Language lint (R21.4) hits real artifacts** verified present in the fixtures: `hisoblaniб` (Latin+Cyrillic splice, `practice-rlc` of 8f734563); `### Mode: Hard` (English template leak, flashcards of 3ca0da6f); untagged `**misconception:**` cards (both flashcards). Mixed-script is a single-token both-scripts test → medium-agnostic (a pure-Cyrillic Russian word or pure-Latin Uzbek word never trips it). English-template blacklist is restricted to **structural/meta tokens** (`Mode:` label, `Needs Retry`, `red herring`, the shipped meta-preamble phrases) that are not legitimate student content in any subject; ambiguous bare English words (`Scenario`, `Wrong`) are excluded to avoid false-positives on L2/English lessons.
- **Dispatch by phase:** language checks → all non-extract phases; misconception-tag check → `flashcards` only; error-detection format check → `practice-error-detection` only. Extract is skipped (not student-facing; same gate as the judge).
- **Load-bearing facts (verified against code):** `warnings` in `_run_phase` is (re)assigned at `pipeline.py:1151` then persisted via `phase_repo.set_status(..., validation_warnings=warnings or None)` at `pipeline.py:1161`. `subject` and `output_language` are already in scope (passed to the judge at `pipeline.py:1042-1049`). Lint is inserted after 1151, inside the `if phase_name != "extract":` block, defensively wrapped so it can never fail a job.

## File Structure

- **Create** `app/services/content_lint.py` — the whole lint surface (data model + 3 language checks + misconception-tag check + error-detection format check + `lint_phase` dispatcher + `findings_to_warnings`). One file, one responsibility.
- **Create** `tests/services/test_content_lint.py` — all unit tests.
- **Create** `tests/fixtures/content_lint/` — 5 real audited fixtures + 3 mutated error-detection fixtures.
- **Modify** `app/services/pipeline.py` — import + one wiring block (~6 lines) before the save.
- **Modify (Finish)** `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `docs/memory/ROADMAP.md`, `docs/CODE_MAP.md`, `docs/HOW_IT_WORKS.md`.

---

## Task 0: Land the fixtures (real audited outputs)

**Files:**
- Create: `tests/fixtures/content_lint/errdet-clean-8f734563.md`
- Create: `tests/fixtures/content_lint/errdet-clean-3ca0da6f.md`
- Create: `tests/fixtures/content_lint/flashcards-modeleak-3ca0da6f.md`
- Create: `tests/fixtures/content_lint/flashcards-untagged-8f734563.md`
- Create: `tests/fixtures/content_lint/rlc-mixedscript-8f734563.md`

- [ ] **Step 1: Copy the extracted real outputs into the repo.** They already sit in the session scratchpad (`.../scratchpad/cqb-fixtures/`). Copy verbatim — do NOT hand-edit content; these are the ground truth.

```bash
SRC="/private/tmp/claude-501/-Users-macmini5-Documents-Homework-Content-Generation-Automation/56c9f301-13c5-45ae-9c98-156380143b87/scratchpad/cqb-fixtures"
DST="tests/fixtures/content_lint"
mkdir -p "$DST"
cp "$SRC/errdet-8f734563.md"           "$DST/errdet-clean-8f734563.md"
cp "$SRC/errdet-3ca0da6f.md"           "$DST/errdet-clean-3ca0da6f.md"
cp "$SRC/flashcards-3ca0da6f.md"       "$DST/flashcards-modeleak-3ca0da6f.md"
cp "$SRC/flashcards-8f734563.md"       "$DST/flashcards-untagged-8f734563.md"
cp "$SRC/all-8f734563-practice-rlc.md" "$DST/rlc-mixedscript-8f734563.md"
```

> If the scratchpad is gone, re-extract from `edu_copy`:
> `PGPASSWORD=edu psql -h 127.0.0.1 -p 5432 -U edu -d edu_copy -tAc "select output_md from phase_outputs where job_id::text like '8f734563%' and phase_name='practice-error-detection'"` (repeat per (job-prefix, phase)).

- [ ] **Step 2: Sanity-check the artifacts are present** (the tests depend on these exact strings):

Run:
```bash
grep -c "hisoblaniб" tests/fixtures/content_lint/rlc-mixedscript-8f734563.md          # expect 1
grep -c "Mode: Hard" tests/fixtures/content_lint/flashcards-modeleak-3ca0da6f.md      # expect >=1
grep -c "This is the broken block" tests/fixtures/content_lint/errdet-clean-3ca0da6f.md  # expect 1
```
Expected: `1`, `1`, `1`.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/content_lint/
git commit -m "cqb: add real audited phase-output fixtures for content lint"
```

---

## Task 1: Module skeleton + language lint (mixed-script, English-template, calque)

**Files:**
- Create: `app/services/content_lint.py`
- Test: `tests/services/test_content_lint.py`

- [ ] **Step 1: Write the failing tests** (real fixtures + focused unit cases):

```python
from pathlib import Path
from app.services import content_lint as cl

FIX = Path(__file__).parent.parent / "fixtures" / "content_lint"

def _codes(findings):
    return {f.code for f in findings}

def test_mixed_script_flags_real_splice():
    md = (FIX / "rlc-mixedscript-8f734563.md").read_text(encoding="utf-8")
    findings = cl.lint_phase("practice-rlc", md, subject="matematika", output_language="uz")
    assert "mixed_script" in _codes(findings)
    assert any("hisoblaniб" in f.message for f in findings)

def test_english_template_flags_mode_label():
    md = (FIX / "flashcards-modeleak-3ca0da6f.md").read_text(encoding="utf-8")
    findings = cl.lint_phase("flashcards", md, subject="matematika", output_language="uz")
    assert "english_template" in _codes(findings)
    assert any("Mode:" in f.message for f in findings)

def test_pure_cyrillic_russian_word_is_not_mixed_script():
    findings = cl.lint_phase("boss-arena", "ПОВТОРЕНИЕ курса алгебры", subject="matematika", output_language="ru")
    assert "mixed_script" not in _codes(findings)

def test_pure_latin_uzbek_word_is_not_mixed_script():
    findings = cl.lint_phase("boss-arena", "Algebraik kasrlarni qisqartirish", subject="matematika", output_language="uz")
    assert "mixed_script" not in _codes(findings)

def test_calque_qizil_seld_flagged():
    findings = cl.lint_phase("boss-arena", "Bu yerda qizil seld bor.", subject="matematika", output_language="uz")
    assert "calque" in _codes(findings)

def test_english_word_scenario_not_flagged_for_english_lesson():
    # ambiguous bare English words must not false-positive on an L2 English lesson
    findings = cl.lint_phase("case-based-preview", "Scenario: a shop sells apples.", subject="ingliz-tili", output_language="en")
    assert "english_template" not in _codes(findings)

def test_extract_phase_is_skipped():
    findings = cl.lint_phase("extract", "Mode: Hard\nhisoblaniб", subject="matematika", output_language="uz")
    assert findings == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_content_lint.py -q`
Expected: FAIL (`ModuleNotFoundError` / `AttributeError: lint_phase`).

- [ ] **Step 3: Implement the module skeleton + language checks**

```python
"""Deterministic, no-LLM content lint (ROADMAP R21.3 + R21.4, cluster CQ-B).

WARN-ONLY: every finding is advisory. Callers fold `findings_to_warnings(...)`
into `phase_outputs.validation_warnings` (the same channel the LLM judge uses).
Never gates a regen, never fails a job. Pure functions — no I/O, no model calls.

Known limitations (warn-only v1 — deliberately conservative, prefers under- to
over-flagging so it never false-positives a good packet):
- `errdet_reveal_mismatch` only fires when BOTH the body marker and the Reveal
  carry a numeric block id (`Blok N`). Unnumbered-marker outputs (e.g. the
  `(This is the broken block)` + `**Xato blok:**` style) get no mismatch check —
  silence here is "not enough signal to prove a mismatch", NOT "verified consistent".
- The misconception provenance check matches `source`/`inferred` anywhere in the
  card body, so an incidental "source" can mask a genuinely missing tag (false
  negative). Acceptable for v1; tighten to a trailing/parenthesised tag form only
  once the emitted format is pinned.
- Semantic answer-key correctness is out of scope by design (no-LLM) — that is
  CQ-C (R21.2). This module only enforces mechanical/format contracts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["LintFinding", "lint_phase", "findings_to_warnings"]

_MAX_FINDINGS = 12  # cap per phase — a wall of warnings helps nobody


@dataclass(frozen=True)
class LintFinding:
    code: str        # stable machine tag, e.g. "mixed_script"
    message: str     # human-readable, includes the offending snippet


# --- language checks ---------------------------------------------------------

_LATIN = re.compile(r"[A-Za-z]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_WORD = re.compile(r"[^\s`*_(){}\[\]<>.,:;!?\"'|]+")

# Structural / meta template tokens that are never legitimate student content in
# ANY subject (kept deliberately narrow to avoid L2/English false-positives).
_ENGLISH_TEMPLATE = [
    re.compile(r"(?m)^\s*#{0,6}\s*\**Mode:\s*", re.IGNORECASE),          # "Mode: Hard" label/heading
    re.compile(r"\bNeeds Retry\b", re.IGNORECASE),
    re.compile(r"\bred herring\b", re.IGNORECASE),
    re.compile(r"this is a direct content generation task", re.IGNORECASE),
    re.compile(r"the brainstorming skill", re.IGNORECASE),
]
_CALQUES = [re.compile(r"\bqizil seld\b", re.IGNORECASE)]


def _lint_language(output_md: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    seen_mixed: set[str] = set()
    for w in _WORD.findall(output_md):
        if _LATIN.search(w) and _CYRILLIC.search(w) and w not in seen_mixed:
            seen_mixed.add(w)
            out.append(LintFinding("mixed_script", f"mixed Latin+Cyrillic in one word: {w!r}"))
    for rx in _ENGLISH_TEMPLATE:
        m = rx.search(output_md)
        if m:
            out.append(LintFinding("english_template", f"English template token: {m.group(0).strip()!r}"))
    for rx in _CALQUES:
        m = rx.search(output_md)
        if m:
            out.append(LintFinding("calque", f"calque phrase: {m.group(0)!r}"))
    return out


# --- dispatcher --------------------------------------------------------------

def lint_phase(phase_name: str, output_md: str, *, subject: str, output_language: str) -> list[LintFinding]:
    """Return advisory findings for one phase output. Never raises on bad input."""
    if phase_name == "extract" or not (output_md or "").strip():
        return []
    findings = _lint_language(output_md)
    return findings[:_MAX_FINDINGS]


def findings_to_warnings(findings: list[LintFinding]) -> list[str]:
    return [f"lint:{f.code}: {f.message}" for f in findings]
```

> Note: the `test_english_word_scenario_not_flagged_for_english_lesson` test passes because `Scenario`/`Wrong` are deliberately NOT in `_ENGLISH_TEMPLATE`. Keep it that way — do not add ambiguous bare words.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run python -m pytest tests/services/test_content_lint.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/content_lint.py tests/services/test_content_lint.py
git commit -m "cqb: content_lint module + R21.4 language lint (mixed-script, template, calque)"
```

---

## Task 2: Misconception-tag lint (flashcards only)

**Files:**
- Modify: `app/services/content_lint.py`
- Test: `tests/services/test_content_lint.py`

Rule (from `flashcards.md:32,95`): every `**misconception:** …` card line must be tagged with its provenance — the word `source` or `inferred` must appear on that card. An untagged misconception → finding. Applies to the `flashcards` phase only.

- [ ] **Step 1: Write the failing tests**

```python
def test_untagged_misconception_flagged_in_real_flashcards():
    md = (FIX / "flashcards-untagged-8f734563.md").read_text(encoding="utf-8")
    findings = cl.lint_phase("flashcards", md, subject="matematika", output_language="uz")
    assert "misconception_untagged" in _codes(findings)

def test_tagged_misconception_not_flagged():
    card = "**misconception:** a common slip (inferred)"
    findings = cl.lint_phase("flashcards", card, subject="matematika", output_language="uz")
    assert "misconception_untagged" not in _codes(findings)

def test_misconception_tag_check_only_runs_on_flashcards():
    md = "**misconception:** untagged mistake"
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert "misconception_untagged" not in _codes(findings)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_content_lint.py -k misconception -q`
Expected: FAIL (the real-fixture test fails — check not implemented yet).

- [ ] **Step 3: Implement**

Add to `content_lint.py`:

```python
_MISCONCEPTION_LINE = re.compile(r"(?im)^\s*\**\s*misconception\s*:\**\s*(?P<body>.+)$")
_PROVENANCE = re.compile(r"\b(source|inferred)\b", re.IGNORECASE)


def _lint_misconception_tags(output_md: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    for m in _MISCONCEPTION_LINE.finditer(output_md):
        body = m.group("body")
        if not _PROVENANCE.search(body):
            snippet = body.strip()[:60]
            out.append(LintFinding(
                "misconception_untagged",
                f"misconception card missing source/inferred tag: {snippet!r}",
            ))
    return out
```

Wire it into `lint_phase` (after the language findings, before the cap):

```python
    findings = _lint_language(output_md)
    if phase_name == "flashcards":
        findings += _lint_misconception_tags(output_md)
    return findings[:_MAX_FINDINGS]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run python -m pytest tests/services/test_content_lint.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/content_lint.py tests/services/test_content_lint.py
git commit -m "cqb: R21.4 misconception source/inferred tag lint (flashcards)"
```

---

## Task 3: Error-detection format lint (EXACTLY-ONE-broken-block)

**Files:**
- Modify: `app/services/content_lint.py`
- Create: `tests/fixtures/content_lint/errdet-zero-markers.md`
- Create: `tests/fixtures/content_lint/errdet-two-markers.md`
- Create: `tests/fixtures/content_lint/errdet-reveal-mismatch.md`
- Test: `tests/services/test_content_lint.py`

Deterministic contract (`practice-error-detection.md:28-31,50-54`): exactly one block is marked broken, and the Reveal names that same one block. Codes:
- `errdet_no_broken_marker` — zero broken-block markers found.
- `errdet_multiple_broken` — markers reference ≥2 distinct block ids (the EXACTLY-ONE violation).
- `errdet_reveal_mismatch` — the Reveal names a different block than the body marker.

Marker vocabulary (multilingual, case-insensitive) — **both word orders** the real outputs use: noun-first `Blok N noto'g'ri` (8f734563's actual body marker `**Blok 4 noto'g'ri.**`), verb-first `noto'g'ri blok N`, `xato blok N`, and the English `this is the broken block` / `broken block` (with apostrophe variants `' ʻ ʼ ' '`). The digit is **required** on the noun-first form so prose like "Bu blok nega noto'g'ri edi?" (present in the real output, digitless) never trips it. Block id is captured from the marker itself where present, else the nearest `blok N` on the match line. The two real `errdet-clean-*` fixtures MUST return no `errdet_*` codes (no false positives).

- [ ] **Step 1: Create the three mutated fixtures.** Derive each from `errdet-clean-8f734563.md` (it uses explicit `**Blok N.**` headings + `**Blok 4 noto'g'ri.**` marker + `**Noto'g'ri blok: Blok 4.**` reveal — the easiest to mutate deterministically).

`errdet-zero-markers.md` — copy of the clean file with the checker-note line `*Tekshiruvchi uchun ... **Blok 4 noto'g'ri.** ...*` AND the final `**Noto'g'ri blok: Blok 4.**` reveal line both changed so no broken-marker vocabulary remains (e.g. delete those two lines).

`errdet-two-markers.md` — copy of the clean file with a second marker added on its **own new line** right after the checker note: `**Blok 6 noto'g'ri.**` (now blocks 4 AND 6 are marked → 2 distinct ids). (The `finditer` extraction below also handles both markers on one line, but a separate line keeps the fixture obvious.)

`errdet-reveal-mismatch.md` — copy of the clean file with the Reveal changed to name a different block: `**Noto'g'ri blok: Blok 5.**` while the body still marks Blok 4.

- [ ] **Step 2: Write the failing tests**

```python
ED = "practice-error-detection"

def test_clean_real_errdet_outputs_have_no_format_findings():
    for name in ("errdet-clean-8f734563.md", "errdet-clean-3ca0da6f.md"):
        md = (FIX / name).read_text(encoding="utf-8")
        findings = cl.lint_phase(ED, md, subject="matematika", output_language="uz")
        assert not [f for f in findings if f.code.startswith("errdet_")], f"false positive on {name}: {findings}"

def test_zero_markers_flagged():
    md = (FIX / "errdet-zero-markers.md").read_text(encoding="utf-8")
    assert "errdet_no_broken_marker" in _codes(cl.lint_phase(ED, md, subject="matematika", output_language="uz"))

def test_two_markers_flagged():
    md = (FIX / "errdet-two-markers.md").read_text(encoding="utf-8")
    assert "errdet_multiple_broken" in _codes(cl.lint_phase(ED, md, subject="matematika", output_language="uz"))

def test_reveal_mismatch_flagged():
    md = (FIX / "errdet-reveal-mismatch.md").read_text(encoding="utf-8")
    assert "errdet_reveal_mismatch" in _codes(cl.lint_phase(ED, md, subject="matematika", output_language="uz"))

def test_errdet_check_only_runs_on_that_phase():
    md = (FIX / "errdet-zero-markers.md").read_text(encoding="utf-8")
    findings = cl.lint_phase("boss-arena", md, subject="matematika", output_language="uz")
    assert not [f for f in findings if f.code.startswith("errdet_")]
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run python -m pytest tests/services/test_content_lint.py -k errdet -q`
Expected: FAIL.

- [ ] **Step 4: Implement**

Add to `content_lint.py`. The apostrophe class covers ASCII `'`, Uzbek `ʻ ʼ`, and curly `' '`:

```python
_APOS = r"['ʻʼ‘’]"
_NOT = rf"noto{_APOS}?g{_APOS}?ri"
# Every broken-block marker. Noun-first REQUIRES a digit (so digitless prose
# "blok nega noto'g'ri" never matches); each form captures the id when present.
_MARKER = re.compile(
    rf"(?i)"
    rf"blo(?:k|ck)\s*(?P<id_pre>\d+)\s+{_NOT}"        # "Blok 4 noto'g'ri"  (R1: noun-first)
    rf"|{_NOT}\s+blo(?:k|ck)\s*(?P<id_post>\d+)?"      # "noto'g'ri blok[ 4]"
    rf"|xato\s+blo(?:k|ck)\s*(?P<id_xato>\d+)?"        # "xato blok[ 4]"
    rf"|this is the broken block"                      # English markers, no id
    rf"|broken block"
)
_REVEAL_HDR = re.compile(r"(?im)^[ \t]*#{1,6}[ \t]*(reveal|ochish)\b")
_BLOCK_ID = re.compile(r"(?i)\bblo(?:k|ck)\s*(\d+)")


def _line_around(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start: end if end != -1 else len(text)]


def _lint_error_detection(output_md: str) -> list[LintFinding]:
    rev = _REVEAL_HDR.search(output_md)
    reveal_off = rev.start() if rev else len(output_md)

    body_ids: set[str] = set()
    body_marker_count = 0
    reveal_id: str | None = None

    for m in _MARKER.finditer(output_md):
        gd = m.groupdict()
        mid = gd.get("id_pre") or gd.get("id_post") or gd.get("id_xato")
        if mid is None:  # English/markerless form — recover an id from the same line if any
            bm = _BLOCK_ID.search(_line_around(output_md, m.start()))
            mid = bm.group(1) if bm else None
        if m.start() >= reveal_off:
            if reveal_id is None and mid is not None:
                reveal_id = mid
            continue
        body_marker_count += 1
        if mid is not None:
            body_ids.add(mid)

    if reveal_id is None and rev is not None:  # first block id after the reveal header
        bm = _BLOCK_ID.search(output_md, reveal_off)
        reveal_id = bm.group(1) if bm else None

    out: list[LintFinding] = []
    if body_marker_count == 0 and reveal_id is None:
        out.append(LintFinding("errdet_no_broken_marker",
                               "no broken-block marker found (prompt requires exactly one)"))
    elif len(body_ids) >= 2:
        out.append(LintFinding("errdet_multiple_broken",
                               f"multiple broken blocks marked: blocks {sorted(body_ids)}"))
    elif body_ids and reveal_id and reveal_id not in body_ids:
        out.append(LintFinding("errdet_reveal_mismatch",
                               f"reveal names block {reveal_id} but body marks {sorted(body_ids)}"))
    return out
```

Wire into `lint_phase`:

```python
    if phase_name == "practice-error-detection":
        findings += _lint_error_detection(output_md)
```

> **Traced against the real fixtures:** `errdet-clean-8f734563.md` — body `**Blok 4 noto'g'ri.**` → `id_pre=4`, `body_ids={4}`; reveal `**Noto'g'ri blok: Blok 4.**` → line-recovered `reveal_id=4` → 4∈{4}, no finding. `errdet-clean-3ca0da6f.md` — body `(This is the broken block)` (no id on its line) → `body_marker_count=1`, `body_ids={}`; reveal `**Xato blok:**` (digitless) → `reveal_id=None` → no finding (correctly conservative — can't prove a mismatch). Two-markers mutation adds `**Blok 6 noto'g'ri.**` → `body_ids={4,6}` → `errdet_multiple_broken`. Reveal-mismatch mutation → `body_ids={4}`, `reveal_id=5` → `errdet_reveal_mismatch`. Zero-markers mutation removes both marker lines → `body_marker_count=0 and reveal_id=None` → `errdet_no_broken_marker`. If TDD surprises you on a real fixture, loosen the check — never edit the real output.

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run python -m pytest tests/services/test_content_lint.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/services/content_lint.py tests/services/test_content_lint.py tests/fixtures/content_lint/errdet-*.md
git commit -m "cqb: R21.3 error-detection EXACTLY-ONE-broken-block format lint"
```

---

## Task 4: Wire lint into the pipeline (warn-only)

**Files:**
- Modify: `app/services/pipeline.py` (import line ~19; wiring at ~1151)
- Test: `tests/services/test_content_lint.py`

- [ ] **Step 1: Write the failing test** (unit-level, no DB/model — proves findings become `lint:`-prefixed warning strings):

```python
def test_findings_to_warnings_prefixes_lint():
    findings = cl.lint_phase("flashcards", "### Mode: Hard\n**misconception:** x", subject="matematika", output_language="uz")
    warnings = cl.findings_to_warnings(findings)
    assert warnings, "expected at least one warning string"
    assert all(w.startswith("lint:") for w in warnings)
    assert any(w.startswith("lint:english_template") for w in warnings)
```

- [ ] **Step 2: Run to verify it fails or passes**

Run: `uv run python -m pytest tests/services/test_content_lint.py -k findings_to_warnings -q`
Expected: PASS (the helper already exists from Task 1 — this test just pins the contract the wiring relies on). If it fails, fix `findings_to_warnings`.

- [ ] **Step 3: Add the import.** In `app/services/pipeline.py` line ~19, add `content_lint` to the existing `from app.services import ...` list (keep alphabetical-ish with the neighbors):

```python
from app.services import agent, book_fetch, content_lint, events_bus, failure_classifier, model_tiers, notion_archive, phase_judge, storage
```

- [ ] **Step 4: Add the wiring block.** In `_run_phase`, immediately after the line `warnings = outcome.warnings if outcome.available else []` (`pipeline.py:1151`) and before the `if warnings:` logger line, insert:

```python
        # CQ-B (R21.3/R21.4): deterministic content lint. WARN-ONLY — findings
        # join validation_warnings under a `lint:` prefix, never gate a regen,
        # never fail a job. Pure function; defensively wrapped regardless.
        try:
            _lint = content_lint.lint_phase(
                phase_name, output_md, subject=subject, output_language=output_language,
            )
            warnings = warnings + content_lint.findings_to_warnings(_lint)
        except Exception as exc:  # noqa: BLE001 — lint must NEVER fail a job
            logger.warning(f"[job {job_id}] {phase_name} content_lint error ({exc!r}); skipping")
```

> This sits inside the `if phase_name != "extract":` block, so extract is already excluded (belt-and-suspenders: `lint_phase` also early-returns on `extract`).

- [ ] **Step 5: Run the lint tests + a pipeline import smoke**

Run:
```bash
uv run python -m pytest tests/services/test_content_lint.py -q
uv run python -c "import app.services.pipeline"   # import must not break
```
Expected: PASS; import clean.

- [ ] **Step 6: Commit**

```bash
git add app/services/pipeline.py tests/services/test_content_lint.py
git commit -m "cqb: wire content_lint into pipeline as warn-only validation_warnings"
```

---

## Task 5: Finish (docs de-stale + backlog + full suite)

**Files:**
- Modify: `docs/CODE_MAP.md`, `docs/HOW_IT_WORKS.md`, `docs/memory/ROADMAP.md`, `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`
- Move: this plan → `docs/superpowers/plans/shipped/`

- [ ] **Step 1: Full suite green**

Run: `uv run python -m pytest tests/ -q`
Expected: all pass (no new failures vs the baseline; real-DB tests still skipped without `RUN_DB_INTEGRATION=1`).

- [ ] **Step 2: Rebase-check before finishing** (mandatory per CLAUDE.md):

```bash
git fetch origin
git log HEAD..origin/Nggaev-v2 --oneline   # if non-empty, rebase onto origin/Nggaev-v2 and re-run the suite
```

- [ ] **Step 3: De-stale reference docs.** In `docs/CODE_MAP.md` add a one-line entry for `app/services/content_lint.py` (deterministic warn-only post-phase lint). In `docs/HOW_IT_WORKS.md`, in the phase/judge section, note that after the judge each content phase runs a no-LLM content lint whose `lint:`-prefixed findings join `validation_warnings`.

- [ ] **Step 4: Backlog + worklog.** Close R21.3 + R21.4 in `docs/memory/ROADMAP.md` (mark shipped, cite worklog). Add a worklog entry to `docs/memory/MASTER_MEMORY.md` + an `INDEX.md` row. **Verify the next-free worklog id first** (`grep 010 docs/memory/INDEX.md | tail`) — the brief says **0110** but a parallel CQ session may hold 0109; use the actual next-free. Note in the worklog: R21.3 ships the deterministic FORMAT contract only; the two audited *semantic* error-detection defects remain CQ-C's scope.

- [ ] **Step 5: Move this plan to shipped** (history-preserving):

```bash
git mv docs/superpowers/plans/2026-07-02-cq-b-content-lint.md docs/superpowers/plans/shipped/
```

- [ ] **Step 6: Commit the finish bookkeeping**

```bash
git add docs/CODE_MAP.md docs/HOW_IT_WORKS.md docs/memory/ROADMAP.md docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md docs/superpowers/plans/shipped/2026-07-02-cq-b-content-lint.md
git commit -m "cqb: docs de-stale + close R21.3/R21.4 + worklog 01XX"
```

---

## Acceptance

- Pure-Python unit tests only — **no generation smoke** (deterministic, no model-behavior claim; CQ-B is no-LLM by mandate). Every must-flag and must-pass case is anchored to a real audited fixture or a minimal mutation of one.
- `validation_warnings` on affected phases now carry `lint:*` strings; nothing gates a regen; no job can fail from lint (defensive wrap + pure functions).
- PR title: `[CQ-B] Deterministic content lint (R21.3 error-detection format + R21.4 language) — warn-only`.
