# Extract Coverage-Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the extract from free-form prose into a structured, enumerated **coverage contract** so (a) the extract stops silently dropping the lesson's worked-example/problem types, (b) Gate B validates *structure* not char-count (closing the compact-lesson false-positive), and (c) *lesson-core ⊆ packet* becomes a checkable, warn-only signal.

**Architecture:** The built-in extract prompts (text + vision) emit a fixed-header enumerated inventory (Concepts, Rules/Theorems, Formulas, Worked-example types, Key facts) with items in the lesson's language. A pure-Python parser reads those headers; Gate B uses it for structural validity; a deterministic post-job coverage check compares contract items against the assembled packet and rides `phase_outputs.validation_warnings` (the CQ-B channel, warn-only, never gates). Composes with CQ-D's fidelity guard unchanged.

**Tech Stack:** Python 3 / FastAPI, `app/services/{agent,pipeline,content_lint}.py`, `app/config.py`, pytest. No migration. No new SDK. No frontend.

---

## Approach & key decisions

**Locked with the user 2026-07-06** (4 decisions, recommendations accepted) and grounded in the Phase-0 audit (`docs/research/2026-07-06-coverage-audit.md`, 9 real `edu_copy` packets, one bounded gemini-3.1-pro judge each, ~$0.72 logged):

- **The gap is real and its shape decides the design.** Overall packet coverage 91% (81/89 core items), central-item ~97%. Of the 8 misses, **5 are EXTRACT-loss** (the extract under-summarized → invisible to every downstream check, incl. the judge which grades against the extract) and **3 are PHASE-loss**. The dominant, hand-verified pattern: **the extract systematically drops the lesson's worked-example/problem types** — kimyo §13 lost *both* its numerical example types (isotope→average-atomic-mass, composition+valence→element), dropping to 71% while central concepts held at 100%. Source `3-misol`/`4-misol` confirmed present by direct PDF inspection; the stored extract contained none of them.
- **Chosen: enumerated-markdown contract at the extract** (rejected fenced-JSON — it rewrites what every prose consumer reads; rejected hybrid — double surface). Fixed **English** section headers (stable/parseable regardless of content language) with items in the lesson language. A required `## Worked-example types` section is the direct fix for the dominant loss. A short lesson-gist line preserves the narrative context phases relied on.
- **Gate B becomes structure-aware, not structure-requiring** (safety-first). Fail iff a refusal marker is present OR (no parseable contract AND stripped length < a *low* fallback floor). A compact contract passes regardless of length → closes `extract-gateb-short-lesson-fp-1`. Rejected "require contract structure": that introduces a *new* false-fail mode when the model formats imperfectly, violating never-gate-on-v1. Evidence length is a bad proxy: FP Algebra §5 = 440c/100% covered; kimyo §13 = 1452c but lost examples. `extract_min_summary_chars` drops 400→120 (fallback only).
- **Coverage enforced warn-only, deterministic** (user pick). A conservative per-item token-presence check (an item is "thin" only when its salient tokens are wholly absent from the packet — low false-alarm, under-reports by design) aggregates to one `lint:coverage_thin` finding on the extract row. Never fails a job (validate_toc/solver lesson). Formulas excluded from matching v1 (symbol-heavy). A semantic/LLM pass is the documented follow-up.
- **Scope discipline (deferred, on record):** phase-side prompt nudges (the 3 PHASE-losses) = follow-up, measured first by this check; CQ-E `coverage` dimension = follow-up (contract is parseable so CQ-E can add a deterministic offline dimension). This lane's surface stays `agent.py` / `pipeline.py` / `config.py` / `content_lint.py` / tests / docs. **No migration.**
- **Load-bearing facts verified against code (2026-07-06):** Gate B = `agent.validate_extract_summary` (`agent.py:1266`), floor `extract_min_summary_chars=400` (`config.py:188`); extract prompts `_SUMMARIZE_LESSON_PROMPT` (`agent.py:2227`) + `_SUMMARIZE_VISION_PROMPT` (`:2238`), both consumed by `summarize_lesson`/`summarize_lesson_vision`; cache version `prompt_hash="builtin:extract:v2"` (`pipeline.py:924`) → must bump `:v3` (invalidates cross-job extract cache: one re-extract per active book, incurred organically per-job, extract pinned to cheap flash — bounded). CQ-D fidelity guard `_verify_and_maybe_regen_extract` (`pipeline.py:861`) wraps the text path and stays unmodified. Job finalize `set_status(...,"done")` at `pipeline.py:390` — coverage check inserts just before it. `content_lint.py` is pure-`re` (leaf); `agent.py` importing it is cycle-free.

**Cost consequence stated:** bumping to `:v3` re-extracts each active book once on its next job (flash-pinned, bounded); the coverage check is $0 (deterministic). No bulk backfill.

## File Structure

- `app/services/content_lint.py` — MODIFY: add `parse_extract_contract`, `contract_has_items` (pure parser, shared) and `lint_coverage` (warn-only check). Home matches CQ-B's channel + purity.
- `app/services/agent.py` — MODIFY: `validate_extract_summary` (structural Gate B, imports the parser); `_SUMMARIZE_LESSON_PROMPT` + `_SUMMARIZE_VISION_PROMPT` (contract format).
- `app/config.py` — MODIFY: `extract_min_summary_chars` 400→120 (fallback floor only).
- `app/services/pipeline.py` — MODIFY: bump `builtin:extract:v2`→`:v3`; wire the post-job coverage check before finalize.
- Tests: `tests/services/test_content_lint.py` (extend), `tests/services/test_agent.py` (extend), `tests/services/test_pipeline_coverage.py` (new).
- Docs (finish): `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, `docs/memory/*`.

---

### Task 1: Contract parser + structural helper (pure, in content_lint.py)

**Files:**
- Modify: `app/services/content_lint.py`
- Test: `tests/services/test_content_lint.py`

The parser recognizes the fixed English section headers (lenient: `##`/`###`, case-insensitive, tolerant of `&`/`-`/space variants) and collects bullet lines (`-`/`*`) under each until the next header. `contract_has_items` = at least one recognized section with ≥1 non-empty bullet.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/services/test_content_lint.py
from app.services.content_lint import parse_extract_contract, contract_has_items

_CONTRACT = """Algebraik kasrlarni ko'paytirish va bo'lish haqidagi dars.

## Concepts & terms
- Algebraik kasr
- Teskari kasr
## Formulas
- a/b · c/d = ac/bd
## Worked-example types
- Ikki algebraik kasrni ko'paytirib qisqartirish
- Bo'lishni teskarisiga ko'paytirishga keltirish
## Key facts
- Maxraj noldan farqli bo'lishi shart
"""

def test_parse_extract_contract_sections_and_items():
    c = parse_extract_contract(_CONTRACT)
    assert set(c) >= {"concepts", "formulas", "worked_example_types", "key_facts"}
    assert c["worked_example_types"] == [
        "Ikki algebraik kasrni ko'paytirib qisqartirish",
        "Bo'lishni teskarisiga ko'paytirishga keltirish",
    ]
    assert c["key_facts"] == ["Maxraj noldan farqli bo'lishi shart"]

def test_parse_lenient_on_header_level_and_case():
    md = "### concepts\n- x\n### WORKED EXAMPLE TYPES\n- y\n"
    c = parse_extract_contract(md)
    assert c["concepts"] == ["x"]
    assert c["worked_example_types"] == ["y"]

def test_contract_has_items_true_for_compact_contract():
    # compact §5-style: short, but enumerated -> valid
    assert contract_has_items(_CONTRACT) is True

def test_contract_has_items_false_for_prose_or_refusal():
    assert contract_has_items("Manba fayli o'qib bo'lmadi.") is False
    assert contract_has_items("") is False
    assert contract_has_items("## Concepts & terms\n\n## Formulas\n") is False  # headers, no items
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_content_lint.py -k "contract" -q`
Expected: FAIL (ImportError: cannot import name `parse_extract_contract`).

- [ ] **Step 3: Implement the parser**

```python
# add to app/services/content_lint.py (after the imports / near the top-level regexes)

# Fixed English contract headers the extract prompt emits (stable across content
# languages). Map header-variant -> canonical key. Lenient: ##/### any level,
# case-insensitive, '&'/'-'/whitespace tolerated.
_CONTRACT_SECTIONS = {
    "concepts": ("concept", "term"),          # "Concepts & terms"
    "rules_theorems": ("rule", "theorem"),    # "Rules & theorems"
    "formulas": ("formula",),
    "worked_example_types": ("worked", "example"),  # "Worked-example types"
    "key_facts": ("key fact", "facts"),
}
_HEADER_RE = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]*(?P<h>[^\n#].*?)[ \t]*$")
_BULLET_RE = re.compile(r"(?m)^[ \t]*[-*][ \t]+(?P<item>\S.*?)[ \t]*$")


def _canonical_section(header: str) -> "str | None":
    h = header.lower()
    for key, needles in _CONTRACT_SECTIONS.items():
        if all(n in h for n in needles) or any(n == h.strip(" :") for n in needles):
            return key
    return None


def parse_extract_contract(md: str) -> "dict[str, list[str]]":
    """Parse the enumerated extract contract into {canonical_section: [items]}.
    Only recognized sections with >=1 bullet appear. Lenient on header level/case."""
    text = md or ""
    out: "dict[str, list[str]]" = {}
    headers = list(_HEADER_RE.finditer(text))
    for i, m in enumerate(headers):
        key = _canonical_section(m.group("h"))
        if not key:
            continue
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        items = [b.group("item").strip() for b in _BULLET_RE.finditer(text[m.end():end])]
        items = [it for it in items if it]
        if items:
            out.setdefault(key, []).extend(items)
    return out


def contract_has_items(md: str) -> bool:
    """True iff the text parses to >=1 recognized contract section with an item."""
    return bool(parse_extract_contract(md))
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/services/test_content_lint.py -k "contract or parse" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/content_lint.py tests/services/test_content_lint.py
git commit -m "cov: enumerated extract-contract parser + structural helper (pure)"
```

---

### Task 2: Structural Gate B + lower fallback floor

**Files:**
- Modify: `app/services/agent.py` (`validate_extract_summary`, ~1266)
- Modify: `app/config.py:188`
- Test: `tests/services/test_agent.py`

Gate B: refusal marker → fail (unchanged); else contract items present → pass; else length-fallback against the (lowered) floor. Closes the compact-lesson FP.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/services/test_agent.py
from app.services.agent import validate_extract_summary

_COMPACT_CONTRACT = (
    "Dars: algebraik kasrlar.\n\n"
    "## Concepts & terms\n- Algebraik kasr\n"
    "## Worked-example types\n- Ikki kasrni ko'paytirib qisqartirish\n"
)  # ~110 chars — below the OLD 400 floor, structurally valid

def test_gate_b_passes_compact_contract_below_old_floor():
    # regression: this is the §5 false-positive class — must PASS now
    assert validate_extract_summary(_COMPACT_CONTRACT) is None

def test_gate_b_fails_refusal_marker_regardless_of_contract():
    bad = "Manba fayli o'qib bo'lmadi.\n## Concepts & terms\n- x\n"
    assert validate_extract_summary(bad) is not None

def test_gate_b_fails_near_empty_no_contract():
    assert validate_extract_summary("ok") is not None

def test_gate_b_passes_unformatted_but_substantial_prose():
    prose = "Bu dars algebraik kasrlar haqida. " * 6  # >120c, no contract headers
    assert validate_extract_summary(prose) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_agent.py -k "gate_b" -q`
Expected: FAIL — `test_gate_b_passes_compact_contract_below_old_floor` fails on the current 400 floor.

- [ ] **Step 3: Implement**

```python
# app/services/agent.py — replace the body of validate_extract_summary
from app.services import content_lint  # add to imports at top of file

def validate_extract_summary(summary: str) -> Optional[str]:
    """Gate B — structural validity of a produced extract contract. Returns a
    failure reason, or None if it looks like a real extract. A failure triggers
    failover (the run_fn raises ExtractRefusal).

    Refusal markers always fail. Otherwise a parseable enumerated contract is
    valid regardless of length (a compact lesson is legitimately short — the old
    char-floor false-failed it). Only when NO contract parses do we fall back to
    a low length floor to reject near-empty / unformatted-refusal output."""
    stripped = (summary or "").strip()
    head = stripped[:_REFUSAL_HEAD_CHARS].lower()
    for marker in _EXTRACT_REFUSAL_MARKERS:
        if marker in head:
            return f"refusal marker in summary head: {marker!r}"
    if content_lint.contract_has_items(stripped):
        return None
    if len(stripped) < settings.extract_min_summary_chars:
        return f"summary too short ({len(stripped)} chars) and no contract sections — likely a refusal"
    return None
```

```python
# app/config.py:188 — lower the floor; it is now a FALLBACK only
    extract_min_summary_chars: int = 120  # fallback floor when NO contract parses; structural parse is primary
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/services/test_agent.py -k "gate_b" -q && uv run python -m pytest tests/services/test_agent.py -q`
Expected: PASS (new + existing agent tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py app/config.py tests/services/test_agent.py
git commit -m "cov: structural Gate B (contract-aware) + lower fallback floor — closes extract-gateb-short-lesson-fp-1"
```

---

### Task 3: Contract-emitting extract prompts + cache bump to v3

**Files:**
- Modify: `app/services/agent.py` (`_SUMMARIZE_LESSON_PROMPT` ~2227, `_SUMMARIZE_VISION_PROMPT` ~2238)
- Modify: `app/services/pipeline.py:924` (`builtin:extract:v2` → `:v3`)
- Test: `tests/services/test_agent.py`, `tests/services/test_pipeline_coverage.py` (hash assertion)

Both prompts instruct the fixed English headers, items in the lesson language, a required Worked-example-types section, omit-empty-sections, and a one-line gist. Unit tests assert the *shape contract* (headers + worked-example instruction + hash); prompt *behavior* is proven by the acceptance smoke.

> **Note (acceptance):** `{rules}` (=`_NO_PREAMBLE`) forbids a "header sentence" — mild tension with the gist line. `_NO_PREAMBLE` targets filler openers ("Mana,", "Quyida,"), so the specific contract instruction should win, but the acceptance smoke MUST confirm the gist + all headers actually render (if the model drops the gist under `_NO_PREAMBLE`, the sections alone still satisfy the contract — no failure, just note it).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/services/test_agent.py
from app.services.agent import _SUMMARIZE_LESSON_PROMPT, _SUMMARIZE_VISION_PROMPT

_REQUIRED_HEADERS = ["## Concepts", "## Rules", "## Formulas", "## Worked-example types", "## Key facts"]

def test_extract_prompts_specify_the_contract_headers():
    for p in (_SUMMARIZE_LESSON_PROMPT, _SUMMARIZE_VISION_PROMPT):
        for h in _REQUIRED_HEADERS:
            assert h in p, f"{h!r} missing from extract prompt"
        assert "worked-example" in p.lower()
        # headers stay English; items in the lesson language
        assert "lesson's language" in p.lower() or "same language" in p.lower()
```

```python
# tests/services/test_pipeline_coverage.py (new file)
def test_extract_prompt_hash_is_v3():
    import inspect
    from app.services import pipeline
    src = inspect.getsource(pipeline)
    assert '"builtin:extract:v3"' in src
    assert '"builtin:extract:v2"' not in src
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_agent.py -k "contract_headers" tests/services/test_pipeline_coverage.py -q`
Expected: FAIL (headers absent; hash still v2).

- [ ] **Step 3: Implement the prompts + bump**

```python
# app/services/agent.py — replace _SUMMARIZE_LESSON_PROMPT and _SUMMARIZE_VISION_PROMPT

_CONTRACT_INSTRUCTIONS = """Write the summary as an ENUMERATED COVERAGE CONTRACT so that \
every downstream generator can see the full inventory of what this lesson teaches. \
Begin with ONE short sentence naming the lesson (the gist). Then emit ONLY these \
section headings, using the EXACT English words below (do NOT translate the headings), \
with the ITEMS written in the lesson's language:

## Concepts & terms
## Rules & theorems
## Formulas
## Worked-example types
## Key facts

Under each heading list one bullet ("- ") per item. OMIT a heading entirely if the \
lesson has no such items (e.g. a history lesson usually has no Formulas). \
"## Worked-example types" is REQUIRED whenever the lesson contains any worked example, \
sample problem, or solved exercise — list the TYPE of each (what the student must be able \
to solve), not the full worked solution. Be complete but concise: capture every distinct \
teachable item, especially the problem/exercise types, and do not invent items absent \
from the source."""

_SUMMARIZE_LESSON_PROMPT = """You are given the full text of a textbook below. \
Locate the lesson titled "{title}" (section {number}; it is printed around pages \
{ps}-{pe} — treat the page numbers only as a hint, find it by its TITLE) and write \
a factual coverage contract of THAT lesson's content for downstream homework \
generation. Summarize only that lesson. """ + _CONTRACT_INSTRUCTIONS + """ {rules}

===== FULL TEXTBOOK TEXT =====
{book_text}
===== END TEXTBOOK TEXT ====="""

_SUMMARIZE_VISION_PROMPT = """The attached PDF pages contain a textbook lesson. \
Locate the lesson titled "{title}" (section {number}; it is printed around pages \
{ps}-{pe} — treat the page numbers only as a hint, find it by its TITLE) and write \
a factual coverage contract of THAT lesson's content for downstream homework \
generation. Summarize only that lesson. """ + _CONTRACT_INSTRUCTIONS + """ {rules}"""
```

```python
# app/services/pipeline.py:924
        prompt_hash = "builtin:extract:v3"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/services/test_agent.py -k "contract_headers" tests/services/test_pipeline_coverage.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py app/services/pipeline.py tests/services/test_agent.py tests/services/test_pipeline_coverage.py
git commit -m "cov: extract prompts emit enumerated coverage contract; bump extract cache v2->v3"
```

---

### Task 4: Deterministic warn-only coverage check (content_lint.py)

**Files:**
- Modify: `app/services/content_lint.py`
- Test: `tests/services/test_content_lint.py`

`lint_coverage(contract_md, packet_md)` parses the contract, and for each item in the checked sections (Concepts, Rules/Theorems, Worked-example types, Key facts — **formulas excluded**, symbol-heavy) marks it "thin" only when NONE of its salient tokens (len≥4, apostrophe-normalized, lowercased) appears in the normalized packet. Aggregates to one `lint:coverage_thin` finding. Conservative by design (under-reports; warn-only).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/services/test_content_lint.py
from app.services.content_lint import lint_coverage, findings_to_warnings

_COV_CONTRACT = """## Worked-example types
- Izotoplar massa ulushi orqali o'rtacha atom massasini hisoblash
- Element tarkibi va valentlik orqali noma'lum elementni aniqlash
## Key facts
- Davriy qonun elementlarni tartiblaydi
"""

def test_coverage_flags_uncovered_worked_example_type():
    packet = "Davriy qonun haqida savollar. Elementlarni tartiblang."  # no isotope/valence vocab
    findings = lint_coverage(_COV_CONTRACT, packet)
    assert len(findings) == 1
    w = findings_to_warnings(findings)[0]
    assert w.startswith("lint:coverage_thin")
    assert "izotop" in w.lower() or "massa" in w.lower()

def test_coverage_clean_when_items_present():
    packet = ("Izotoplar massa ulushi masalasi: o'rtacha atom massasini hisoblang. "
              "Noma'lum elementni tarkibi va valentlik orqali aniqlang. Davriy qonun.")
    assert lint_coverage(_COV_CONTRACT, packet) == []

def test_coverage_formulas_excluded_and_empty_contract_noop():
    assert lint_coverage("## Formulas\n- a/b · c/d = ac/bd\n", "hech narsa") == []
    assert lint_coverage("", "anything") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_content_lint.py -k coverage -q`
Expected: FAIL (ImportError: `lint_coverage`).

- [ ] **Step 3: Implement**

```python
# add to app/services/content_lint.py
_COVERAGE_SECTIONS = ("concepts", "rules_theorems", "worked_example_types", "key_facts")
_APOS_CLASS = "['ʻʼ‘’`]"
_TOKEN_RE = re.compile(rf"[0-9A-Za-zЀ-ӿ{_APOS_CLASS}]+")


def _norm(s: str) -> str:
    return re.sub(_APOS_CLASS, "'", s.lower())


def _salient_tokens(label: str) -> "list[str]":
    return [t for t in _TOKEN_RE.findall(_norm(label)) if len(t) >= 4]


def lint_coverage(contract_md: str, packet_md: str) -> "list[LintFinding]":
    """Warn-only: contract items whose salient vocabulary is WHOLLY absent from the
    packet. Conservative (under-reports) — a nudge, never a gate. Formulas excluded
    (symbol-heavy). One aggregated finding."""
    contract = parse_extract_contract(contract_md)
    if not contract:
        return []
    packet = _norm(packet_md or "")
    thin: "list[str]" = []
    for section in _COVERAGE_SECTIONS:
        for item in contract.get(section, []):
            toks = _salient_tokens(item)
            if toks and not any(t in packet for t in toks):
                thin.append(item)
    if not thin:
        return []
    shown = "; ".join(t[:60] for t in thin[:6])
    more = f" (+{len(thin) - 6} more)" if len(thin) > 6 else ""
    return [LintFinding(
        code="coverage_thin",
        message=f"{len(thin)} contract item(s) appear uncovered by the packet: {shown}{more}",
    )]
```

*(If `LintFinding` fields differ, match the existing dataclass — read it first.)*

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/services/test_content_lint.py -k coverage -q && uv run python -m pytest tests/services/test_content_lint.py -q`
Expected: PASS (new + all existing content_lint tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/content_lint.py tests/services/test_content_lint.py
git commit -m "cov: deterministic warn-only coverage check (contract items vs packet)"
```

---

### Task 5: Wire the post-job coverage check into pipeline finalize

**Files:**
- Modify: `app/services/pipeline.py` (before `set_status(...,"done")` at :390)
- Test: `tests/services/test_pipeline_coverage.py`

A helper computes coverage warnings from the extract row + packet rows; the finalize block calls it, appends to the extract row's `validation_warnings`, try/except-wrapped (never fails a job).

- [ ] **Step 1: Write the failing test (pure helper, no DB)**

```python
# append to tests/services/test_pipeline_coverage.py
from app.services.pipeline import _coverage_warnings_for_job

def test_coverage_warnings_helper_flags_and_ignores_extract():
    rows = [
        {"phase_name": "extract", "output_md": "## Worked-example types\n- izotop massa hisoblash\n"},
        {"phase_name": "flashcards", "output_md": "Davriy qonun. Elementlar."},
        {"phase_name": "boss-arena", "output_md": "Savollar."},
    ]
    warns = _coverage_warnings_for_job(rows)
    assert warns and warns[0].startswith("lint:coverage_thin")

def test_coverage_warnings_empty_when_covered_or_no_extract():
    assert _coverage_warnings_for_job([{"phase_name": "flashcards", "output_md": "x"}]) == []
    rows = [
        {"phase_name": "extract", "output_md": "## Key facts\n- davriy qonun\n"},
        {"phase_name": "flashcards", "output_md": "Davriy qonun elementlarni tartiblaydi."},
    ]
    assert _coverage_warnings_for_job(rows) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/services/test_pipeline_coverage.py -k warnings -q`
Expected: FAIL (`_coverage_warnings_for_job` undefined).

- [ ] **Step 3: Implement helper + wire it**

```python
# app/services/pipeline.py — module-level helper (near other pipeline helpers)
def _coverage_warnings_for_job(rows) -> "list[str]":
    """Given phase rows (dicts or ORM objs with phase_name/output_md), compare the
    extract contract against the assembled packet and return warn-only coverage
    findings (lint:coverage_thin, may be empty). Pure; safe to call anywhere."""
    def _f(r, k):
        return r.get(k) if isinstance(r, dict) else getattr(r, k, None)
    extract = next((_f(r, "output_md") for r in rows if _f(r, "phase_name") == "extract"), None)
    if not extract:
        return []
    packet = "\n\n".join(
        _f(r, "output_md") or "" for r in rows if _f(r, "phase_name") != "extract")
    return content_lint.findings_to_warnings(content_lint.lint_coverage(extract, packet))
```

```python
# app/services/pipeline.py — inside the finalize block, BEFORE set_status(...,"done") at :390
        # Post-job coverage check (warn-only): does the packet cover the extract
        # contract? Rides the extract row's validation_warnings. Never fails a job.
        try:
            async with SessionLocal() as session:
                _rows = await phase_repo.list_for_job(session, job_id)
                _cov = _coverage_warnings_for_job(
                    [{"phase_name": r.phase_name, "output_md": r.output_md} for r in _rows])
                if _cov:
                    _ex = next((r for r in _rows if r.phase_name == "extract"), None)
                    if _ex is not None:
                        _merged = list(_ex.validation_warnings or []) + _cov
                        # guard=False: the extract row is already 'done' and the
                        # default guard (WHERE status != 'done') would no-op it.
                        await phase_repo.set_status(
                            session, _ex.id, _ex.status, validation_warnings=_merged,
                            guard=False)
                        await session.commit()
        except Exception as exc:  # noqa: BLE001 — advisory only, must never fail the job
            logger.warning(f"coverage check skipped (fail-open): {exc!r}")

        async with SessionLocal() as session:
            await jobs_repo.set_status(session, job_id, "done", completed_at=_utcnow())
            await session.commit()
```

*(Verify `phase_repo.set_status` accepts `validation_warnings=` and that keeping the same `status` is valid — mirror the CQ-B call at ~`pipeline.py:1161`; adjust the signature to match. Read `phase_repo.set_status` before writing.)*

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/services/test_pipeline_coverage.py -q`
Expected: PASS. Then: `uv run python -m pytest tests/services/test_pipeline.py -q` (no regression).

- [ ] **Step 5: Commit**

```bash
git add app/services/pipeline.py tests/services/test_pipeline_coverage.py
git commit -m "cov: wire warn-only post-job coverage check into pipeline finalize"
```

---

### Task 6: De-stale live-system docs (extract section)

**Files:**
- Modify: `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`

The extract is now a coverage contract; Gate B is structural; a warn-only coverage check runs at finalize. Update the extract/pipeline sections to describe the new behavior (not the worklog). No test.

- [ ] **Step 1:** Update `docs/HOW_IT_WORKS.md` extract section — extract emits an enumerated contract (concepts/rules/formulas/worked-example-types/key-facts, English headers + lesson-language items); Gate B validates structure not length; post-job coverage check into `validation_warnings`.
- [ ] **Step 2:** Update `docs/CODE_MAP.md` — `content_lint.parse_extract_contract`/`contract_has_items`/`lint_coverage`; `agent.validate_extract_summary` structural; `pipeline._coverage_warnings_for_job`; cache `builtin:extract:v3`.
- [ ] **Step 3: Commit**

```bash
git add docs/HOW_IT_WORKS.md docs/CODE_MAP.md
git commit -m "cov: de-stale HOW_IT_WORKS + CODE_MAP for the extract coverage contract"
```

---

## Acceptance gate (before PR — fact over theory)

Run from the main checkout (has `var/books` + `.env`); log all api costs.

1. **Real api smoke — full single-lesson generation on the new extract, human-read.** Generate one lesson (the compact Algebra §5 FP case + one large lesson, e.g. kimyo §13) over `transport=api` gemini, in-process. Confirm: (a) the extract is a well-formed contract with a populated `## Worked-example types` section that now includes the isotope/atomic-mass problem types §13 previously dropped; (b) Gate B accepts the compact §5 contract (the old floor's false-positive is the RED case — must pass); (c) phase quality is not visibly regressed vs the old prose extract; (d) the coverage check produced sane (or empty) warnings.
2. **CQ-E golden harness free tier green** — `uv run python -m pytest tests/golden -q` (and/or `scripts/golden_eval.py --no-llm`) over the 5 golden packets. It reads phases, not extracts → proves downstream shape didn't regress.
3. **CQ-D fidelity-guard tests green UNMODIFIED** — `uv run python -m pytest tests/services/test_agent.py -k "fidelity" -q` (and any CQ-D pipeline tests). Zero edits to those tests.
4. **Full suite** — `uv run python -m pytest tests/ -q`.

## Finish (per CLAUDE.md)

- Rebase-check: `git fetch origin && git log HEAD..origin/Nggaev-v2`; rebase onto `origin/Nggaev-v2` if it moved; re-run suite.
- PR `[coverage] Extract coverage-contract (round-2)` — GK2 merges. **Cascade note in the PR body: this merge UNBLOCKS the CQ-E baseline-freeze** (freeze now captures post-contract behavior).
- Worklog **0115** (re-verify next-free) in `docs/memory/MASTER_MEMORY.md` + INDEX row; close `extract-gateb-short-lesson-fp-1` in `docs/memory/WISHLIST.md`; `git mv` this plan → `docs/superpowers/plans/shipped/`.
