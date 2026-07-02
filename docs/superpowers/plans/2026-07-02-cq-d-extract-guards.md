# CQ-D — Extract-quality guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two independent extract-quality guards in one PR — (1) an extract-example *fidelity* check that catches worked-example drift the judge is blind to, and (2) a garbled-text *plausibility* detector that routes broken-font / mojibake PDFs to the vision path instead of poisoning the extract.

**Architecture:** Both guards live on the extract surface (`app/services/agent.py` + the extract branch of `app/services/pipeline.py::_execute_phase`). Item 2 is a cheap deterministic gate; item 1 is a hybrid: a free deterministic pre-check gates a paid gemini-flash verify, which gates a single extract regen.

**Tech Stack:** Python 3.14, pydantic, pypdf, pytest / pytest-asyncio; gemini over Vertex (`transport=api`) for the acceptance smokes.

---

## Approach & key decisions

- **Two guards, one PR** (CQ-D code pair). Independent surfaces; both are "does the extract faithfully reflect the source PDF."
- **Item 2 — glyph/garbage detection (locked: char-plausibility → route to vision).** Signal = an **expected-alphabet plausibility ratio**: `(alphabetic chars in Latin ∪ Cyrillic ∪ Uzbek-modifier) / (all alphabetic chars)`. *Measured on the real 26-book corpus:* every real book (Uzbek-Latin, Cyrillic, English, glyph-recovered, homoglyph) scores **0.999–1.000**; the one genuinely-garbled book `f20db30c` (RU cp1251 mojibake — `Ó÷åáíèê äëÿ` for "Учебник для") scores **0.073**. Floor **0.70** separates them with enormous margin. Added to Gate A (`validate_extract_text`) and the TOC `toc_text_usable` gate; a failure returns a reason string, which the pipeline **already routes to the vision path** (`pipeline.py:929`); loud-fail only if vision also fails (existing Gate B). No new dependency.
- **Load-bearing fact (verified, reshapes item 2):** `_decode_glyph_text` (shipped [0035]/[0036]) **already recovers all four local `/Gxx` books perfectly** (letterratio 0.77–0.94, correct Uzbek). So the pure-glyph case is *not* the live gap — the letter-density ratio in `validate_extract_text` passes it correctly. The live, currently-**unguarded** gap is text that is letter-dense but written in the *wrong alphabet* (mojibake, or a subset font whose glyph≠byte) — `f20db30c` passes today's Gate A at letterratio 0.88. The plausibility ratio is the signal density can't see.
- **Item 1 — extract-example fidelity (locked: hybrid free pre-check → LLM-on-hits → regen once).** Root cause verified in code: the drift is *in the extract* (`summarize_lesson`), and the judge cannot see it — `_FIDELITY_RULE` (`phase_judge.py:76`) **explicitly exempts** "worked-example arithmetic," and the judge grades downstream phases against the *extract* (LESSON CONTEXT = extract), never the book. Extract-time is the only place we hold the source (`book_text`). Mechanism: after Gate B, a **free** deterministic pass finds numeric/equation expressions in the summary that are absent from `book_text` (`extract_fidelity_candidates`); only if candidates exist do we spend **one** gemini-flash `verify_extract_fidelity` call; only on a **confirmed** mismatch do we re-run `summarize_lesson` **once** with a correction hint (Gate-B'd; keep original if the regen refuses). Extract is cached cross-job (`find_latest_extract`, `pipeline.py:861`), so the guard runs on first production only → cost amortizes. Pinned to the extract provider/model/transport (gemini-flash).
- **Rejected:** LLM-verify-always (unbounded per-lesson $, violates the money rule); deterministic-only fidelity (blind to "invented Example-1" structural drift); dictionary-hit-ratio detector (needs a wordlist asset for no accuracy gain over the alphabet ratio at these margins); glyph-decode-dominance signal (would false-flag all four recovered `/Gxx` books).

---

## File Structure

- `app/config.py` — new setting `extract_min_alpha_ratio` (item 2).
- `app/services/agent.py` — new pure helpers `_is_expected_alpha`, `_alpha_plausibility_ratio` (item 2); `_normalize_expr`, `extract_numeric_expressions`, `extract_fidelity_candidates`, `ExtractFidelityVerdict`, `verify_extract_fidelity` (item 1); edits to `validate_extract_text`, `extract_toc` (toc gate), `summarize_lesson` (item 1 `correction_hint`).
- `app/services/pipeline.py` — wire the plausibility gate is automatic (Gate A already routes); wire item 1 verify+regen into `_extract_run`.
- Tests: `tests/services/test_alpha_plausibility.py` (new), `tests/services/test_extract_gates.py` (extend), `tests/services/test_toc_source_text.py` (extend), `tests/services/test_extract_fidelity.py` (new), `tests/services/test_summarize_lesson.py` (extend), `tests/services/test_pipeline_extract_dispatch.py` (extend).
- Acceptance: `scripts/cqd_extract_guards_smoke.py` (new, real api calls).

---

## Task 1: Item 2 — alphabet-plausibility helper + setting

**Files:**
- Modify: `app/config.py` (extract settings block, ~line 176)
- Modify: `app/services/agent.py` (add helpers near `validate_extract_text`, ~line 1106)
- Test: `tests/services/test_alpha_plausibility.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_alpha_plausibility.py
from app.services.agent import _alpha_plausibility_ratio, _is_expected_alpha


def test_real_latin_uzbek_scores_high():
    txt = "Umumiy o‘rta ta’lim maktablarining 8-sinfi uchun darslik. Uchburchak perimetri."
    assert _alpha_plausibility_ratio(txt) >= 0.95


def test_real_cyrillic_scores_high():
    txt = "Ш. А. Алимов, О. Р. Холмухамедов. Алгебра. Учебник для 8 классов."
    assert _alpha_plausibility_ratio(txt) >= 0.95


def test_cp1251_mojibake_scores_low():
    # RU text mis-decoded cp1251-as-latin1: the real f20db30c failure shape.
    txt = "Ó÷åáíèê äëÿ 8 êëàññîâ øêîë îáùåãî ñðåäíåãî îáðàçîâàíèÿ"
    assert _alpha_plausibility_ratio(txt) < 0.30


def test_too_little_text_is_treated_plausible():
    # Below the sample floor we cannot judge — never false-fire.
    assert _alpha_plausibility_ratio("ab cd") == 1.0


def test_is_expected_alpha_blocks_latin1_accents():
    assert _is_expected_alpha("a") and _is_expected_alpha("Я") and _is_expected_alpha("ʻ")
    assert not _is_expected_alpha("÷") and not _is_expected_alpha("å")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_alpha_plausibility.py -q`
Expected: FAIL — `ImportError: cannot import name '_alpha_plausibility_ratio'`.

- [ ] **Step 3: Implement the helpers**

In `app/services/agent.py`, immediately above `def validate_extract_text` (~line 1106):

```python
# Alphabets a real curriculum textbook is written in: ASCII Latin, the Cyrillic
# block, and the Uzbek modifier letters (ʻ/ʼ and their curly-quote variants).
# Text extracted as the WRONG bytes — cp1251 Cyrillic mis-decoded as latin1
# (mojibake: "Ó÷åáíèê"), or a subset font whose glyph codes don't equal their
# byte values — stays `.isalpha()` but lands mostly OUTSIDE these blocks. Real
# books score ~1.00; the RU-mojibake book f20db30c scores 0.07 (measured). This
# is the signal validate_extract_text's letter-DENSITY ratio cannot see (garbage
# letters are still letters). Below _ALPHA_RATIO_MIN_SAMPLE alphabetic chars we
# cannot judge, so we return 1.0 (plausible) — never false-fire on a tiny slice.
_UZBEK_MODIFIER_LETTERS = frozenset("ʻʼ‘’")
_ALPHA_RATIO_MIN_SAMPLE = 200


def _is_expected_alpha(c: str) -> bool:
    if ("a" <= c <= "z") or ("A" <= c <= "Z"):
        return True
    if 0x0400 <= ord(c) <= 0x04FF:   # Cyrillic
        return True
    return c in _UZBEK_MODIFIER_LETTERS


def _alpha_plausibility_ratio(text: str) -> float:
    """Fraction of alphabetic chars that belong to an alphabet a real textbook is
    written in. 1.0 when there is too little text to judge. See the block comment
    above for why this catches garbage the letter-density ratio passes."""
    letters = [c for c in (text or "") if c.isalpha()]
    if len(letters) < _ALPHA_RATIO_MIN_SAMPLE:
        return 1.0
    good = sum(1 for c in letters if _is_expected_alpha(c))
    return good / len(letters)
```

In `app/config.py`, in the extract settings block (right after `extract_min_printable_ratio`, ~line 176):

```python
    # Below this fraction of alphabetic chars belonging to a real alphabet
    # (Latin/Cyrillic/Uzbek), the text layer is garbled (cp1251 mojibake or a
    # subset font whose glyph!=byte) — route to vision. Real books measure
    # >=0.999; the RU-mojibake book scores 0.07. 0.70 leaves a huge margin.
    extract_min_alpha_ratio: float = 0.70
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_alpha_plausibility.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/services/agent.py tests/services/test_alpha_plausibility.py
git commit -m "cqd: add expected-alphabet plausibility ratio helper + setting (item 2)"
```

---

## Task 2: Item 2 — wire plausibility into Gate A

**Files:**
- Modify: `app/services/agent.py::validate_extract_text` (~line 1106–1126)
- Test: `tests/services/test_extract_gates.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_extract_gates.py — append
from app.services.agent import validate_extract_text


def test_gate_a_flags_mojibake_that_passes_letter_density():
    # 600+ chars of cp1251-as-latin1 mojibake: letter-DENSITY is high (all
    # alphabetic) so the old gate passed it; the alphabet ratio must now reject.
    txt = ("Ó÷åáíèê äëÿ 8 êëàññîâ øêîë îáùåãî ñðåäíåãî îáðàçîâàíèÿ. " * 20)
    reason = validate_extract_text(txt)
    assert reason is not None and "plausib" in reason.lower()


def test_gate_a_passes_real_uzbek_book_text():
    txt = ("Uchburchakning perimetri, medianasi, balandligi va bissektrisasi. "
           "Parallelogrammning xossalari va alomatlari haqida. " * 20)
    assert validate_extract_text(txt) is None
```

- [ ] **Step 2: Run to verify the mojibake test fails**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_extract_gates.py -q -k "mojibake or real_uzbek"`
Expected: `test_gate_a_flags_mojibake_that_passes_letter_density` FAILS (returns None today); the real-uzbek one passes.

- [ ] **Step 3: Add the plausibility check to `validate_extract_text`**

After the `if ratio < settings.extract_min_printable_ratio:` block (before `return None`), add:

```python
    plaus = _alpha_plausibility_ratio(stripped)
    if plaus < settings.extract_min_alpha_ratio:
        return f"garbled PDF text layer: alphabet-plausibility {plaus:.2f}"
    return None
```

Also update the `validate_extract_text` docstring: it currently says a failure is "Terminal … which no provider can fix." That is stale — the pipeline routes Gate-A failures to the **vision** path (`pipeline.py:929`). Change the last sentence to:

```
    reason string, or None if the text looks like real, readable content. A
    failure marks the local text unusable (scanned / broken-font / garbled); the
    pipeline routes it to a vision extract, which fails loud only if it also
    cannot read the pages."""
```

- [ ] **Step 4: Run to verify all pass**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_extract_gates.py -q`
Expected: PASS (all, including the two new).

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_extract_gates.py
git commit -m "cqd: reject garbled text layer in Gate A (routes to vision) (item 2)"
```

---

## Task 3: Item 2 — wire plausibility into the TOC usable-gate

**Files:**
- Modify: `app/services/agent.py::extract_toc` (~line 1381, the `toc_text_usable` computation)
- Test: `tests/services/test_toc_source_text.py` (extend) — pure-gate assertion via a small helper

Because `toc_text_usable` is computed inline in `extract_toc`, extract the predicate into a tiny pure function so it is unit-testable without spawning.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_toc_source_text.py — append
from app.services.agent import _toc_text_is_usable


def test_toc_text_unusable_when_garbled():
    garbled = ("Ó÷åáíèê äëÿ 8 êëàññîâ " * 30)
    assert _toc_text_is_usable(garbled, pages_scanned=10) is False


def test_toc_text_usable_when_real_and_dense():
    real = ("§1. Uchburchaklar. Perimetri va yuzasi. Parallelogramm xossalari. " * 30)
    assert _toc_text_is_usable(real, pages_scanned=4) is True


def test_toc_text_unusable_when_empty():
    assert _toc_text_is_usable("", pages_scanned=10) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_toc_source_text.py -q -k usable`
Expected: FAIL — `ImportError: cannot import name '_toc_text_is_usable'`.

- [ ] **Step 3: Extract the predicate and use it**

In `app/services/agent.py`, add near `extract_text_is_too_sparse` (~line 1230):

```python
def _toc_text_is_usable(toc_source_text: str, pages_scanned: int) -> bool:
    """True when the locally-extracted TOC text is present, dense enough (not a
    watermark-only scan), AND written in a real alphabet (not mojibake/glyph
    garbage). Any failure → the caller vision-attaches the printed contents page."""
    if not toc_source_text:
        return False
    if extract_text_is_too_sparse(toc_source_text, pages_scanned):
        return False
    return _alpha_plausibility_ratio(toc_source_text) >= settings.extract_min_alpha_ratio
```

Then replace the inline computation in `extract_toc` (~line 1381):

```python
    toc_text_usable = _toc_text_is_usable(
        toc_source_text, toc_source_meta.get("pages_scanned", 0)
    )
```

(Delete the old `has_local_toc_text and not extract_text_is_too_sparse(...)` expression; `has_local_toc_text` is still computed/logged above and used later for the `source` label — leave those.)

- [ ] **Step 4: Run to verify it passes + no TOC regressions**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_toc_source_text.py tests/services/test_extract_toc_vision.py tests/services/test_extract_toc_degarble.py tests/services/test_toc_extractor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_toc_source_text.py
git commit -m "cqd: TOC usable-gate rejects garbled text → vision (item 2)"
```

---

## Task 4: Item 1 — deterministic fidelity candidates (free pre-check)

**Files:**
- Modify: `app/services/agent.py` (add near the extract helpers, ~line 1140)
- Test: `tests/services/test_extract_fidelity.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_extract_fidelity.py
from app.services.agent import (
    _normalize_expr,
    extract_numeric_expressions,
    extract_fidelity_candidates,
)


def test_normalize_unifies_minus_and_slash_and_spaces():
    assert _normalize_expr("−3 / (2a)") == _normalize_expr("-3/(2a)")


def test_extract_numeric_expressions_requires_operator():
    exprs = extract_numeric_expressions("Javob: −3/a va tekshiring x=5. Sahifa 12.")
    # fractions/equations captured; the bare page number is not.
    assert any("3/a" in e for e in exprs)
    assert any("x=5" in e.replace(" ", "") for e in exprs)
    assert all("12" != e for e in exprs)


def test_candidate_is_drifted_expression_absent_from_source():
    book = "Namuna: kasrni qisqartiramiz, natija −3/a. Boshqa misol 21/120."
    summary = "Ishlangan misolda natija −3/(2a) boʻladi; yana 21/100."
    cands = extract_fidelity_candidates(summary, book)
    # both drifted values are ungrounded in the source
    assert any("3/(2a)" in c for c in cands)
    assert any("21/100" in c for c in cands)


def test_no_candidates_when_grounded():
    book = "Natija −3/a. Ikkinchi misol 21/120 = 7/40."
    summary = "Misol: −3/a. Yana 21/120."
    assert extract_fidelity_candidates(summary, book) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_extract_fidelity.py -q`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implement the pre-check**

In `app/services/agent.py` (near the other extract helpers):

```python
# A worked-example value worth grounding: a run of digits/letters/parens that
# contains at least one digit AND a structural math operator ('/' or '='). This
# targets fractions and equation results (the drift class: -3/(2a) vs -3/a,
# 21/100 vs 21/120) and deliberately SKIPS bare numbers and page ranges (which
# legitimately reappear in prose and would only cost a wasted verify call).
_FIDELITY_EXPR_RE = re.compile(r"[0-9A-Za-z()][0-9A-Za-z()/=+\-−–.·*×÷]{1,38}")
_FIDELITY_MAX_CANDIDATES = 12


def _normalize_expr(s: str) -> str:
    out = s.lower().replace(" ", "")
    for ch in "−–—":          # minus variants → ascii hyphen
        out = out.replace(ch, "-")
    for ch in "·*×":          # multiplication variants
        out = out.replace(ch, "*")
    out = out.replace("÷", "/")
    return out


def extract_numeric_expressions(text: str) -> set[str]:
    """Normalized fraction/equation expressions in `text` (digit + '/' or '=')."""
    found: set[str] = set()
    for m in _FIDELITY_EXPR_RE.findall(text or ""):
        if any(c.isdigit() for c in m) and ("/" in m or "=" in m):
            found.add(_normalize_expr(m))
    return {e for e in found if len(e) >= 3}


def extract_fidelity_candidates(summary: str, book_text: str) -> list[str]:
    """Worked-example expressions in the extract SUMMARY that do not appear in the
    source BOOK_TEXT — candidate transcription drift. Free (no model call). An
    empty list means the deterministic pass found nothing to verify."""
    norm_book = _normalize_expr(book_text or "")
    cands = sorted(e for e in extract_numeric_expressions(summary) if e not in norm_book)
    return cands[:_FIDELITY_MAX_CANDIDATES]
```

Ensure `import re` exists at the top of `agent.py` (it does — `_GLYPH_NAME_RE` uses it).

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_extract_fidelity.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_extract_fidelity.py
git commit -m "cqd: deterministic extract-fidelity candidate finder (item 1 pre-check)"
```

---

## Task 5: Item 1 — LLM verify + summarize_lesson correction hint

**Files:**
- Modify: `app/services/agent.py` (add `ExtractFidelityVerdict` + `verify_extract_fidelity`; add `correction_hint` to `summarize_lesson`)
- Test: `tests/services/test_extract_fidelity.py` (extend), `tests/services/test_summarize_lesson.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_extract_fidelity.py — append
import pytest
from unittest.mock import AsyncMock, patch
from app.services import agent as agent_mod
from app.services.agent import ExtractFidelityVerdict, verify_extract_fidelity


@pytest.mark.asyncio
async def test_verify_returns_mismatches_from_model():
    fake = agent_mod.PhaseResult(
        text="{}",
        parsed=ExtractFidelityVerdict(mismatches=["extract says -3/(2a); source has -3/a"]),
    )
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)) as rp:
        out = await verify_extract_fidelity(
            summary="… -3/(2a) …", book_text="… -3/a …",
            candidates=["-3/(2a)"], provider="gemini", model="gemini-2.5-flash",
            transport="api", homework_job_id=None, phase_output_id=None,
        )
    assert out == ["extract says -3/(2a); source has -3/a"]
    assert rp.call_args.kwargs["schema"] is ExtractFidelityVerdict


@pytest.mark.asyncio
async def test_verify_clean_returns_empty():
    fake = agent_mod.PhaseResult(text="{}", parsed=ExtractFidelityVerdict(mismatches=[]))
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)):
        out = await verify_extract_fidelity(
            summary="x", book_text="x", candidates=["21/100"],
            provider="gemini", model="gemini-2.5-flash", transport="api",
            homework_job_id=None, phase_output_id=None,
        )
    assert out == []
```

```python
# tests/services/test_summarize_lesson.py — append (mirror the file's existing spawn-mock style)
@pytest.mark.asyncio
async def test_summarize_lesson_appends_correction_hint(monkeypatch):
    captured = {}

    async def fake_spawn(*, provider, model, prompt, attachments, transport):
        captured["prompt"] = prompt
        return 0, "OK summary text " * 40, {"prompt_tokens": 1, "output_tokens": 1, "cached_tokens": 0, "total_tokens": 2, "raw": {}}, ""

    monkeypatch.setattr(agent, "_spawn", fake_spawn)
    monkeypatch.setattr(agent, "_record_usage", AsyncMock())
    await agent.summarize_lesson(
        provider="gemini", model="gemini-2.5-flash", book_text="book",
        section_title="T", section_number="1", page_start=1, page_end=2,
        homework_job_id=uuid4(), phase_output_id=uuid4(), transport="api",
        correction_hint="extract says -3/(2a); source has -3/a",
    )
    assert "-3/a" in captured["prompt"] and "correct" in captured["prompt"].lower()
```

(Match the actual import names already used at the top of `test_summarize_lesson.py`: `agent`, `AsyncMock`, `uuid4`. Add any missing import.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_extract_fidelity.py tests/services/test_summarize_lesson.py -q`
Expected: FAIL — `ImportError`/`TypeError: unexpected keyword 'correction_hint'`.

- [ ] **Step 3: Implement**

In `app/services/agent.py`:

```python
class ExtractFidelityVerdict(BaseModel):
    """Model verdict for the extract-fidelity check. `mismatches` is empty when
    every suspect expression is faithfully grounded in the source."""
    mismatches: list[str] = Field(default_factory=list)


_VERIFY_FIDELITY_PROMPT = (
    "You are checking a LESSON SUMMARY against the SOURCE TEXTBOOK TEXT it was "
    "written from. Below are SUSPECT expressions found in the summary that a "
    "quick scan could not locate in the source. For each, decide whether the "
    "summary faithfully reflects a worked example / value from the source. "
    "Report ONLY genuine transcription errors — a value or worked example the "
    "summary states that CONTRADICTS the source (e.g. summary '-3/(2a)' vs "
    "source '-3/a', or an example the source does not contain). Do NOT report a "
    "value the source simply phrases differently, rounds, or that the summary "
    "legitimately derived. For each real error, add one line "
    "'summary says X; source has Y'. If all are fine, return an empty list.\n\n"
    "SUSPECT EXPRESSIONS:\n{suspects}\n\n"
    "===== LESSON SUMMARY =====\n{summary}\n===== END SUMMARY =====\n\n"
    "===== SOURCE TEXTBOOK TEXT =====\n{book_text}\n===== END SOURCE ====="
)


async def verify_extract_fidelity(
    *, summary: str, book_text: str, candidates: list[str],
    provider: str, model: Optional[str], transport: str,
    homework_job_id: Optional[UUID], phase_output_id: Optional[UUID],
) -> list[str]:
    """One structured gemini-flash call: which of `candidates` are real extract
    transcription errors vs the source. Returns confirmed mismatch descriptions
    (empty = clean). Never raises for a bad verdict — on any failure returns []
    (fail-open: the guard must never block or corrupt a good extract)."""
    if not candidates:
        return []
    prompt = _VERIFY_FIDELITY_PROMPT.format(
        suspects="\n".join(f"- {c}" for c in candidates),
        summary=summary, book_text=book_text,
    )
    try:
        result = await run_phase(
            provider=provider, model=model, phase_prompt=prompt,
            phase_name="lesson.extract.verify", schema=ExtractFidelityVerdict,
            homework_job_id=homework_job_id, phase_output_id=phase_output_id,
            operation="lesson.extract.verify", transport=transport,
        )
    except Exception as exc:
        logger.warning(f"agent.verify_extract_fidelity failed (fail-open): {exc!r}")
        return []
    parsed = result.parsed
    if isinstance(parsed, ExtractFidelityVerdict):
        return [m for m in parsed.mismatches if m.strip()]
    return []
```

Add `correction_hint` to `summarize_lesson` — extend the signature (`correction_hint: str = ""`) and, after building `instruction` from `_SUMMARIZE_LESSON_PROMPT`, append:

```python
    if correction_hint:
        instruction += (
            "\n\nIMPORTANT — your previous summary mis-transcribed the source. "
            "Correct these and re-summarize faithfully from the textbook text:\n"
            f"{correction_hint}"
        )
```

`agent.py` line 40 is `from pydantic import BaseModel, ValidationError` — change it to `from pydantic import BaseModel, Field, ValidationError` (Field is not yet imported).

- [ ] **Step 4: Run to verify they pass**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_extract_fidelity.py tests/services/test_summarize_lesson.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_extract_fidelity.py tests/services/test_summarize_lesson.py
git commit -m "cqd: gemini-flash extract-fidelity verify + summarize_lesson correction hint (item 1)"
```

---

## Task 6: Item 1 — wire verify + regen into the pipeline

**Files:**
- Modify: `app/services/pipeline.py::_execute_phase` (`_extract_run`, ~line 963–974)
- Test: `tests/services/test_pipeline_extract_dispatch.py` (extend)

- [ ] **Step 1: Write the failing tests** (target the extracted helper directly — no full-branch harness needed)

```python
# tests/services/test_pipeline_extract_dispatch.py — append
import pytest
from unittest.mock import AsyncMock
from app.services import pipeline
from app.services import agent as agent_mod

_SECTION = {"title": "T", "number": "1", "page_start": 1, "page_end": 2}
_GOOD = "Ishlangan misol natija −3/a bo‘ladi. " * 30
_DRIFT = "Ishlangan misol natija −3/(2a) bo‘ladi. " * 30
_BOOK = "Manba matni: qisqartirish natijasi −3/a. " * 40   # grounds -3/a, NOT -3/(2a)


@pytest.mark.asyncio
async def test_regens_once_on_confirmed_drift(monkeypatch):
    calls = {"n": 0}

    async def fake_summarize(*, correction_hint="", **kw):
        calls["n"] += 1
        return (_GOOD if correction_hint else _DRIFT), 2, 3

    monkeypatch.setattr(pipeline.agent, "summarize_lesson", fake_summarize)
    monkeypatch.setattr(pipeline.agent, "verify_extract_fidelity",
                        AsyncMock(return_value=["summary says -3/(2a); source has -3/a"]))
    out, xin, xout = await pipeline._verify_and_maybe_regen_extract(
        out=_DRIFT, book_text=_BOOK, prov="gemini", mdl="gemini-2.5-flash",
        transport="api", section=_SECTION, job_id=None, po_id=None,
    )
    assert calls["n"] == 1                     # exactly one regen
    assert "-3/(2a)" not in out and "-3/a" in out
    assert (xin, xout) == (2, 3)               # regen tokens billed


@pytest.mark.asyncio
async def test_no_verify_call_when_no_candidates(monkeypatch):
    spy = AsyncMock(return_value=[])
    monkeypatch.setattr(pipeline.agent, "verify_extract_fidelity", spy)
    out, xin, xout = await pipeline._verify_and_maybe_regen_extract(
        out=_GOOD, book_text=_BOOK, prov="gemini", mdl=None, transport="api",
        section=_SECTION, job_id=None, po_id=None,
    )
    assert out == _GOOD and (xin, xout) == (0, 0)
    spy.assert_not_called()                    # -3/a is grounded → no paid call


@pytest.mark.asyncio
async def test_no_regen_when_verify_clean(monkeypatch):
    monkeypatch.setattr(pipeline.agent, "verify_extract_fidelity", AsyncMock(return_value=[]))
    called = {"n": 0}

    async def fake_summarize(**kw):
        called["n"] += 1
        return _GOOD, 1, 1

    monkeypatch.setattr(pipeline.agent, "summarize_lesson", fake_summarize)
    out, xin, xout = await pipeline._verify_and_maybe_regen_extract(
        out=_DRIFT, book_text=_BOOK, prov="gemini", mdl=None, transport="api",
        section=_SECTION, job_id=None, po_id=None,
    )
    assert out == _DRIFT and called["n"] == 0 and (xin, xout) == (0, 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_pipeline_extract_dispatch.py -q -k drift`
Expected: FAIL (only one summarize call today; no regen).

- [ ] **Step 3: Implement the wiring**

To keep it testable, add a helper in `pipeline.py` and call it from `_extract_run` after Gate B:

```python
async def _verify_and_maybe_regen_extract(
    *, out: str, book_text: str, prov: str, mdl, transport: str,
    section: dict, job_id, po_id,
) -> tuple[str, int, int]:
    """Item 1 guard: free candidate scan → flash verify on hits → one regen on
    confirmed drift. Returns (text, extra_prompt_tokens, extra_output_tokens).
    Fail-open: any problem keeps the original extract."""
    candidates = agent.extract_fidelity_candidates(out, book_text)
    if not candidates:
        return out, 0, 0
    mismatches = await agent.verify_extract_fidelity(
        summary=out, book_text=book_text, candidates=candidates,
        provider=prov, model=mdl, transport=transport,
        homework_job_id=job_id, phase_output_id=po_id,
    )
    if not mismatches:
        return out, 0, 0
    logger.warning(f"[job {job_id}] extract fidelity: {len(mismatches)} drift(s) → regen: {mismatches}")
    corrected, tin2, tout2 = await agent.summarize_lesson(
        provider=prov, model=mdl, book_text=book_text,
        section_title=section["title"], section_number=section["number"],
        page_start=section["page_start"], page_end=section["page_end"],
        homework_job_id=job_id, phase_output_id=po_id, transport=transport,
        correction_hint="\n".join(f"- {m}" for m in mismatches),
    )
    if agent.validate_extract_summary(corrected) is None:
        return corrected, tin2, tout2      # accept corrected
    return out, tin2, tout2                # regen refused → keep original, but bill the call
```

Use the module-level `logger` (loguru) — **not** `log` (that name is the per-job `logger.bind(...)` local inside the job coroutine, not in scope here). Match the `logger.info(f"[job {job_id}] …")` style already used in this file.

Then inside `_extract_run`, after the Gate B block and before `return out, tin_, tout_`:

```python
                    out, xin, xout = await _verify_and_maybe_regen_extract(
                        out=out, book_text=book_text, prov=prov, mdl=mdl,
                        transport=extract_transport, section=section,
                        job_id=job_id, po_id=po_id,
                    )
                    return out, tin_ + xin, tout_ + xout
```

Rewrite the Step-1 test to target `_verify_and_maybe_regen_extract` directly (cleaner than driving the whole branch): assert 2 summarize calls when verify returns a mismatch, 0 regens when candidates empty, 0 regens when verify returns [].

- [ ] **Step 4: Run to verify it passes + no dispatch regressions**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/services/test_pipeline_extract_dispatch.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py tests/services/test_pipeline_extract_dispatch.py
git commit -m "cqd: verify+regen-once extract-fidelity guard in pipeline (item 1)"
```

---

## Task 7: Acceptance smoke — real api calls (both items)

**Files:**
- Create: `scripts/cqd_extract_guards_smoke.py`

This is the CLAUDE.md acceptance gate: real model calls over `transport=api` (Vertex). Requires the Vertex SA env (`GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT`).

- [ ] **Step 1: Write the smoke script**

It performs three real checks and prints PASS/FAIL:

1. **Item 2 detection (no model):** `read_whole_book_text` on `var/books/f20db30c-*/source.pdf` → assert `validate_extract_text(...)` returns a "plausibility" reason (garbage detected). Also assert a real Uzbek book (`var/books/5e295cbc-*`) returns `None`.
2. **Item 2 vision recovery (real gemini-api call):** `summarize_lesson_vision(provider="gemini", model="gemini-2.5-flash", pdf_path=f20db30c, page_start/-end from a mid-book lesson, transport="api")` → assert the returned summary is non-trivial AND `_alpha_plausibility_ratio(summary) >= 0.9` (Cyrillic recovered, not mojibake). Proves the "route to vision actually recovers" requirement R10 flagged as UNPROVEN.
3. **Item 1 verify (real gemini-api call):** take a real lesson's `book_text` (any Uzbek math book), hand-build a summary that drifts one value (e.g. replace a real `-3/a` with `-3/(2a)`), run `extract_fidelity_candidates` (assert the drift is a candidate) → `verify_extract_fidelity(...)` over api (assert it returns a non-empty mismatch). Then run verify on the FAITHFUL summary (assert empty). Proves the flash verify discriminates.

Print a one-line summary and `sys.exit(1)` on any failure.

- [ ] **Step 2: Run the smoke**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m scripts.cqd_extract_guards_smoke`
Expected: all three PASS. (Controller runs this personally — it is the acceptance proof.)

- [ ] **Step 3: Commit**

```bash
git add scripts/cqd_extract_guards_smoke.py
git commit -m "cqd: real-api acceptance smoke for both extract guards"
```

---

## Task 8: Full suite + finish

- [ ] **Step 1: Full suite green**

Run: `cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m pytest tests/ -q`
Expected: the canonical bar (no `RUN_DB_INTEGRATION`) — 0 failed. Fix any regression before proceeding.

- [ ] **Step 2: Rebase check** (per CLAUDE.md finish)

```bash
git fetch origin && git log HEAD..origin/Nggaev-v2 --oneline
```
If the base moved ahead, rebase onto `origin/Nggaev-v2`, resolve conflicts, re-run the suite.

- [ ] **Step 3: Finish artifacts (part of this task, not deferred)**
- Worklog entry in `docs/memory/MASTER_MEMORY.md` (verify the next-free ID — plan assumes **0111**, confirm against `INDEX.md`) + a row in `docs/memory/INDEX.md`.
- Close CQ-D items 1+2 in `docs/memory/REMEDIATION_CLUSTERS.md` (Cluster 10) and R21.6 / R10 in `docs/memory/ROADMAP.md`.
- `git mv docs/superpowers/plans/2026-07-02-cq-d-extract-guards.md docs/superpowers/plans/shipped/`.
- De-stale reference docs the change touched: `docs/HOW_IT_WORKS.md` (extract guards) + `docs/CODE_MAP.md` (`agent.py` extract helpers + `pipeline.py` extract branch).

- [ ] **Step 4: Open PR** titled `[CQ-D] Extract-quality guards: fidelity verify + garbled-text detection` to `Nggaev-v2` (route to the gate; no self-merge).
