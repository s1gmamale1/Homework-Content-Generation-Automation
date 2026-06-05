# Extract Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the per-lesson `extract` phase fail loudly instead of silently producing garbage — read the PDF **text locally** (no CLI file-read → dodges the gitignore block), have the model **locate the lesson by title** in that text (R2-immune), **validate** with two deterministic gates, and **fail over** across providers.

**Architecture:** Mirror the content-phase resilience. `agent.py` gains pure helpers (`read_whole_book_text`, `validate_extract_text` = Gate A, `validate_extract_summary` = Gate B) and a slimmed single-provider `summarize_lesson` (injects text, NO PDF attach, NO failover). The **failover loop lives in `_execute_phase`'s extract branch** in `pipeline.py` (where `_run_with_failover` lives — `agent.py` can't import it without a cycle): read text once → Gate A (terminal) → `_run_with_failover(run_fn = summarize + Gate B → raise ExtractRefusal)`. `failure_classifier` gets `ExtractRefusal` → immediate failover.

**Tech Stack:** FastAPI, pypdf, asyncio, pytest (DB-free: pure-function + signature/`inspect` tests, per `tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-06-05-extract-robustness-design.md`

**Commands:** Windows. Tests: `& ".\.venv\Scripts\python.exe" -m pytest <args>` via the **PowerShell tool** (bash chokes on `&`). Stage ONLY the files each task lists; never `git add -A` (parallel sessions share the branch).

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `app/config.py` | extract size budget + gate thresholds | T1 |
| `app/services/failure_classifier.py` | `ExtractRefusal` + classify → immediate failover | T2 |
| `app/services/agent.py` | Gate A `validate_extract_text` + Gate B `validate_extract_summary` (pure) | T3 |
| `app/services/agent.py` | `read_whole_book_text` (local pypdf) | T4 |
| `app/services/agent.py` | `summarize_lesson` (single-provider, text-injected, no attach) | T5 |
| `app/services/pipeline.py` | `_execute_phase` extract branch: local-text → Gate A → failover-summarize; cache `v1→v2` | T6 |
| acceptance + worklog | real CLI smoke + worklog | T7 |

---

## Task 1: Config — extract size budget + gate thresholds

**Files:**
- Modify: `app/config.py` (after the resilience settings block added in worklog 0031, i.e. after `failover_provider_order`)
- Test: `tests/services/test_config_extract_robustness.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_config_extract_robustness.py
from app.config import Settings


def test_extract_robustness_defaults():
    s = Settings(database_url="postgresql+asyncpg://x/y", _env_file=None)
    assert s.extract_max_text_chars > 100_000          # fits a normal textbook's text
    assert s.extract_min_text_chars > 0                 # Gate A floor
    assert 0.0 < s.extract_min_printable_ratio <= 1.0   # Gate A printable-letter ratio
    assert s.extract_min_summary_chars > 0              # Gate B floor
    # Gate B floor must be ABOVE the observed 275-char refusal that motivated this work
    assert s.extract_min_summary_chars >= 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_config_extract_robustness.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'extract_max_text_chars'`.

- [ ] **Step 3: Add the settings** (insert after the `failover_provider_order = Field(...)` block)

```python

    # ─── Extract robustness (local-text + gates) ──────────────────────────
    # Whole-book local text is injected into the extract prompt; if the book's
    # text exceeds this it terminal-fails here by design (large-book generation
    # is the separate subset-TOC/shrink effort). ~600K chars ≈ ~150K tokens —
    # fits a normal <20MB textbook comfortably inside gemini-flash's context.
    extract_max_text_chars: int = 600_000
    # Gate A (raw local text): below this many chars, or below this printable-
    # letter ratio, the PDF is treated as unreadable (scanned / broken font).
    extract_min_text_chars: int = 500
    extract_min_printable_ratio: float = 0.55
    # Gate B (summary): a real lesson summary is thousands of chars; the silent
    # refusal that motivated this was 275. Below this → reject → fail over.
    extract_min_summary_chars: int = 400
```

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_config_extract_robustness.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/services/test_config_extract_robustness.py
git commit -m "feat(config): extract-robustness settings (text budget + gate thresholds)"
```

---

## Task 2: `ExtractRefusal` + immediate-failover classification

**Files:**
- Modify: `app/services/failure_classifier.py`
- Test: `tests/services/test_failure_classifier.py` (extend)

> `failure_classifier` already exists with `classify(error) -> "transient"|"wall"|"hard"`. `_run_with_failover`'s same-provider retry budget is `{"transient":2,"hard":1,"wall":0}`, so classifying a refusal as `"wall"` gives **0 same-provider retries = immediate failover** — exactly what we want for a refusal/junk summary.

- [ ] **Step 1: Write the failing test** (append to `tests/services/test_failure_classifier.py`)

```python
def test_extract_refusal_is_immediate_failover():
    from app.services.failure_classifier import ExtractRefusal, classify
    # ExtractRefusal must classify as "wall" → budget 0 → no same-provider retry.
    assert classify(ExtractRefusal("Gate B: summary too short")) == "wall"
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_failure_classifier.py::test_extract_refusal_is_immediate_failover -q`
Expected: FAIL — `ImportError: cannot import name 'ExtractRefusal'`.

- [ ] **Step 3: Implement** — at the top of `app/services/failure_classifier.py`, after the module docstring/imports add the type; and guard `classify`:

```python
class ExtractRefusal(Exception):
    """A produced extract summary failed deterministic Gate B (refusal / too
    short / junk). Classified as a wall → immediate provider failover (0
    same-provider retries), never a same-provider retry."""
```

Then make `classify` short-circuit on it — insert as the FIRST lines of `classify`, before the `msg = str(error).lower()` line:

```python
    if isinstance(error, ExtractRefusal):
        return "wall"
```

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_failure_classifier.py -q`
Expected: all pass (the existing classifier tests + the new one).

- [ ] **Step 5: Commit**

```bash
git add app/services/failure_classifier.py tests/services/test_failure_classifier.py
git commit -m "feat(resilience): ExtractRefusal -> immediate failover (wall class)"
```

---

## Task 3: Deterministic gates (Gate A raw text, Gate B summary)

**Files:**
- Modify: `app/services/agent.py` (add two pure functions near `_decode_glyph_text`, ~line 862)
- Test: `tests/services/test_extract_gates.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_extract_gates.py
from app.config import settings
from app.services.agent import validate_extract_text, validate_extract_summary


def test_gate_a_accepts_real_text():
    text = "Franklar davlati. " * 200  # long, mostly letters
    assert validate_extract_text(text) is None


def test_gate_a_rejects_empty_and_short():
    assert validate_extract_text("") is not None
    assert validate_extract_text("   \n  ") is not None
    assert validate_extract_text("tiny") is not None


def test_gate_a_rejects_glyph_garbage():
    garbage = "/G55/G6D/G75 " * 400   # /Gxx glyph soup, no real letters (R10 case)
    assert validate_extract_text(garbage) is not None


def test_gate_b_accepts_real_summary():
    summary = "# Franklar davlati\n\n" + ("Bu darsda muhim tarixiy voqealar bor. " * 50)
    assert validate_extract_summary(summary) is None


def test_gate_b_rejects_short_refusal():
    # the actual 275-char refusal shape that motivated this work
    refusal = "Dars konteksti mavjud emas — PDF manba fayli ignore sozlamalari tufayli o'qib bo'lmadi."
    assert validate_extract_summary(refusal) is not None


def test_gate_b_does_not_false_trigger_on_legit_uzbek():
    # "mavjud emas" appears INSIDE a long, real summary → must PASS (not a refusal)
    summary = ("# Dars\n\n" + "Tarixiy manbalarga ko'ra ba'zi ma'lumotlar mavjud emas, "
               "ammo asosiy voqealar quyidagicha. " * 40)
    assert validate_extract_summary(summary) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_extract_gates.py -q`
Expected: FAIL — `ImportError: cannot import name 'validate_extract_text'`.

- [ ] **Step 3: Implement** (add to `app/services/agent.py` after `_decode_glyph_text`; `settings`, `re` already imported)

```python
# Refusal phrases (lowercased) that, when they appear NEAR THE START of a short
# output, mark a non-summary. Anchored to the first chars + short length so a
# legitimate "...ma'lumot mavjud emas" inside a real summary never false-fires.
_EXTRACT_REFUSAL_MARKERS = (
    "ignore pattern", "ignore sozlama", "couldn't read", "could not read",
    "o'qib bo'lmadi", "o`qib bo'lmadi", "konteksti mavjud emas",
    "konteksti bo'sh", "konteksti bo`sh", "manba fayli", "no text layer",
    "no lesson content",
)
_REFUSAL_HEAD_CHARS = 240


def validate_extract_text(text: str) -> Optional[str]:
    """Gate A — deterministic check on the RAW local PDF text. Returns a failure
    reason string, or None if the text looks like real, readable content.
    Terminal: a failure here means the input is unreadable (scanned / broken
    font), which no provider can fix."""
    stripped = (text or "").strip()
    if len(stripped) < settings.extract_min_text_chars:
        return f"unreadable PDF (no text layer): only {len(stripped)} chars extracted"
    letters = sum(c.isalpha() for c in stripped)
    ratio = letters / len(stripped)
    if ratio < settings.extract_min_printable_ratio:
        return f"unreadable PDF (no text layer): printable-letter ratio {ratio:.2f}"
    return None


def validate_extract_summary(summary: str) -> Optional[str]:
    """Gate B — deterministic check on a produced summary. Returns a failure
    reason, or None if it looks like a real summary. A failure triggers
    failover (the run_fn raises ExtractRefusal)."""
    stripped = (summary or "").strip()
    if len(stripped) < settings.extract_min_summary_chars:
        return f"summary too short ({len(stripped)} chars) — likely a refusal"
    head = stripped[:_REFUSAL_HEAD_CHARS].lower()
    for marker in _EXTRACT_REFUSAL_MARKERS:
        if marker in head:
            return f"refusal marker in summary head: {marker!r}"
    return None
```

> Note: `_REFUSAL_HEAD_CHARS` (240) is just above the 275-char refusal; combined with `extract_min_summary_chars=400`, the real refusal already fails on length — the marker check is the belt-and-suspenders for a refusal that happens to be longer. The legit-Uzbek test passes because the summary is long AND "mavjud emas" sits past the 240-char head.

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_extract_gates.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_extract_gates.py
git commit -m "feat(extract): deterministic Gate A (raw text) + Gate B (summary)"
```

---

## Task 4: `read_whole_book_text` — local pypdf whole-book read

**Files:**
- Modify: `app/services/agent.py` (add near `_read_pdf_pages`, ~line 896)
- Test: `tests/services/test_read_whole_book.py` (new)

> Reuses the existing `_read_pdf_pages(reader, indices, *, budget, already, pdf_name)` (which already applies `_decode_glyph_text` per page and respects a char budget). `read_whole_book_text` opens a `pypdf.PdfReader`, reads ALL pages up to the budget, and joins the chunks. No `pdfplumber` (the TOC path uses pypdf).

- [ ] **Step 1: Write the failing test** (build a tiny real PDF with reportlab if present, else a pypdf-writer fallback; keep it dependency-light by writing pages via pypdf if reportlab is absent)

```python
# tests/services/test_read_whole_book.py
import inspect

from app.services.agent import read_whole_book_text


def test_read_whole_book_signature_and_budget_param():
    sig = inspect.signature(read_whole_book_text)
    assert "pdf_path" in sig.parameters
    # honors the configured char budget (caps runaway-size books)
    src = inspect.getsource(read_whole_book_text)
    assert "extract_max_text_chars" in src
    assert "_read_pdf_pages" in src        # reuses the proven page reader
    assert "PdfReader" in src              # pypdf, not pdfplumber
```

> Scene-setting: the suite is DB-free and avoids heavy fixtures; a real multi-page PDF fixture is awkward cross-platform. Assert structurally (signature + source reuse of the proven helpers) like the repo's other `inspect`-based tests (`test_phase_validation_warnings.py`, `test_worker_heartbeat.py`). The real end-to-end read is proven by the Task 7 CLI smoke on an actual textbook.

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_read_whole_book.py -q`
Expected: FAIL — `ImportError: cannot import name 'read_whole_book_text'`.

- [ ] **Step 3: Implement** (add after `_read_pdf_pages`; confirm `from pypdf import PdfReader` is imported at the top of `agent.py` — the TOC path already uses pypdf, so it is; if the import is local to a function, add a module-level one)

```python
def read_whole_book_text(pdf_path: Path) -> str:
    """Read the WHOLE book's text locally via pypdf (no CLI, no file-read by any
    model → dodges the gitignore block and the >20MB CLI ceiling), capped at
    settings.extract_max_text_chars. Glyph-decoded per page. Returns the joined
    text (page-labeled chunks); '' if the PDF yields no text (scanned/broken)."""
    reader = PdfReader(str(pdf_path))
    n = len(reader.pages)
    chunks, _pages = _read_pdf_pages(
        reader,
        range(1, n + 1),
        budget=settings.extract_max_text_chars,
        already=set(),
        pdf_name=pdf_path.name,
    )
    return "".join(chunks).strip()
```

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_read_whole_book.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_read_whole_book.py
git commit -m "feat(extract): read_whole_book_text — local pypdf whole-book read (budgeted)"
```

---

## Task 5: `summarize_lesson` — single-provider, text-injected (no attach, no failover)

**Files:**
- Modify: `app/services/agent.py` (new function alongside `extract_lesson_context`, ~line 1242; keep `extract_lesson_context` for now — removed in T6)
- Test: `tests/services/test_summarize_lesson.py` (new — signature/source level; real call is the T7 smoke)

> This is the slim per-provider summarizer the failover loop calls. It builds the extract prompt with the lesson **title** + printed pages **as a hint** + the injected `book_text`, calls one CLI provider via `_spawn` with **NO attachments**, records usage, returns `(text, prompt_tokens, output_tokens)`. NO Gate B and NO failover here — those live in the `_execute_phase` extract branch (T6).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_summarize_lesson.py
import inspect

from app.services.agent import summarize_lesson


def test_summarize_lesson_shape():
    sig = inspect.signature(summarize_lesson)
    for p in ("provider", "model", "book_text", "section_title", "section_number",
              "page_start", "page_end", "homework_job_id", "phase_output_id"):
        assert p in sig.parameters, p
    src = inspect.getsource(summarize_lesson)
    assert "attachments=[]" in src            # NO PDF attached — text is injected
    assert "book_text" in src                 # injects the local text
    assert "locate" in src.lower() or "find" in src.lower()  # locate-by-title prompt
```

- [ ] **Step 2: Run to verify it fails**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_summarize_lesson.py -q`
Expected: FAIL — `ImportError: cannot import name 'summarize_lesson'`.

- [ ] **Step 3: Implement** — add a new locate-by-title prompt constant and the function. Add near `_EXTRACT_PHASE_PROMPT`:

```python
_SUMMARIZE_LESSON_PROMPT = """You are given the full text of a textbook below. \
Locate the lesson titled "{title}" (section {number}; it is printed around pages \
{ps}-{pe} — treat the page numbers only as a hint, find it by its TITLE) and write \
a concise, factual summary of THAT lesson's content for downstream homework \
generation. Summarize only that lesson. {rules}

===== FULL TEXTBOOK TEXT =====
{book_text}
===== END TEXTBOOK TEXT ====="""
```

```python
async def summarize_lesson(
    *,
    provider: str,
    model: Optional[str],
    book_text: str,
    section_title: str,
    section_number: str,
    page_start: int,
    page_end: int,
    homework_job_id: UUID,
    phase_output_id: UUID,
) -> tuple[str, int, int]:
    """Single-provider extract: inject the whole-book TEXT (no PDF attached),
    model locates the lesson by title and summarizes. Returns (text, prompt_tokens,
    output_tokens). Raises on CLI failure. NO Gate B / NO failover here (the
    _execute_phase extract branch wraps this in _run_with_failover + Gate B)."""
    prov = get_provider(provider)
    resolved_model = _resolve_model(provider, model)
    instruction = _SUMMARIZE_LESSON_PROMPT.format(
        title=section_title,
        number=section_number,
        ps=page_start if page_start is not None else "?",
        pe=page_end if page_end is not None else "?",
        rules=_NO_PREAMBLE,
        book_text=book_text,
    )
    prompt = _build_master_prompt(
        phase_prompt=instruction,
        phase_name="lesson.extract",
        lesson_context=None,
        prior_outputs=None,
        difficulty=None,
        schema=None,
        provider_suffix=prov.prompt_suffix(None),
        attachment_preamble="",   # no attachment preamble — text is inline
    )
    started_at = datetime.now(timezone.utc)
    t0 = perf_counter()
    rc, text, usage, stderr = await _spawn(
        provider=prov, model=resolved_model, prompt=prompt, attachments=[],
    )
    duration_s = perf_counter() - t0
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    ok = rc == 0
    await _record_usage(
        operation="lesson.extract", provider=provider, model_name=resolved_model,
        usage=usage, duration_s=duration_s, started_at=started_at, success=ok,
        homework_job_id=homework_job_id, phase_output_id=phase_output_id,
        error_message=None if ok else f"{provider} CLI exited rc={rc}",
        extra_envelope={"section_number": section_number, "section_title": section_title},
    )
    if not ok:
        raise RuntimeError(f"lesson.extract: {provider} CLI exited rc={rc} :: {_failure_preview(stderr, text)}")
    logger.success(
        f"agent.lesson.extract done | provider={provider} section={section_number} "
        f"chars={len(text)} input={prompt_tokens:,} output={output_tokens:,} duration_ms={duration_s * 1000:.0f}"
    )
    return text, prompt_tokens, output_tokens
```

(Confirm `_NO_PREAMBLE`, `_build_master_prompt`, `_spawn`, `_record_usage`, `_failure_preview`, `get_provider`, `_resolve_model` are all in scope — they are, used by the existing `extract_lesson_context`.)

- [ ] **Step 4: Run to verify it passes**

Run: `& ".\.venv\Scripts\python.exe" -m pytest tests/services/test_summarize_lesson.py -q`
Expected: PASS. Also `& ".\.venv\Scripts\python.exe" -c "import app.services.agent"` imports clean.

- [ ] **Step 5: Commit**

```bash
git add app/services/agent.py tests/services/test_summarize_lesson.py
git commit -m "feat(extract): summarize_lesson — single-provider text-injected summarizer"
```

---

## Task 6: Wire into the `_execute_phase` extract branch + cache bump

**Files:**
- Modify: `app/services/pipeline.py` (extract branch of `_execute_phase`; cache key `:532`)

> The extract branch currently: cached-extract early-return, then `agent.extract_lesson_context(provider=settings.extract_provider, …)` (attaches the PDF). Replace the non-cached extract with: read local text → Gate A (terminal) → `_run_with_failover` over `summarize_lesson` with Gate B inside the run_fn. `_run_with_failover`, `failure_classifier`, `agent`, `settings` are already imported in `pipeline.py`.

- [ ] **Step 1: Bump the cache key** at `pipeline.py:532`:

```python
        prompt_hash = "builtin:extract:v2"
```

- [ ] **Step 2: Replace the non-cached extract call.** Find the extract branch's real-extract call (currently `output_md, tin, tout = await agent.extract_lesson_context(provider=settings.extract_provider, model=settings.extract_model, pdf_path=pdf_path, section_title=section["title"], section_number=section["number"], page_start=section["page_start"], page_end=section["page_end"], homework_job_id=job_id, phase_output_id=po_id)` followed by `produced_by = settings.extract_provider` and `parsed_struct = None`). Replace that block with:

```python
            # Local whole-book text — no CLI file-read (dodges the gitignore block
            # + the >20MB ceiling). The model locates the lesson by title (R2-immune).
            book_text = await asyncio.to_thread(agent.read_whole_book_text, pdf_path)
            if len(book_text) >= settings.extract_max_text_chars:
                raise RuntimeError(
                    "lesson.extract: book too large for whole-text extract — "
                    "needs subset-TOC/shrink"
                )
            gate_a = agent.validate_extract_text(book_text)
            if gate_a is not None:
                raise RuntimeError(f"lesson.extract: {gate_a}")

            async def _extract_run(prov: str, mdl: Optional[str]):
                out, tin_, tout_ = await agent.summarize_lesson(
                    provider=prov, model=mdl, book_text=book_text,
                    section_title=section["title"], section_number=section["number"],
                    page_start=section["page_start"], page_end=section["page_end"],
                    homework_job_id=job_id, phase_output_id=po_id,
                )
                reason = agent.validate_extract_summary(out)
                if reason is not None:
                    raise failure_classifier.ExtractRefusal(f"lesson.extract Gate B: {reason}")
                return out, tin_, tout_

            output_md, tin, tout, produced_by = await _run_with_failover(
                requested_provider=settings.extract_provider,
                model=settings.extract_model,
                run_fn=_extract_run,
            )
            parsed_struct = None
```

> The Gate-A / size-gate `raise RuntimeError(...)` propagates out of `_execute_phase` → `_execute_one_phase` marks the job `failed` with that exact message + raises → the head loop unwinds cleanly (no retry, no silent proceed — terminal-by-message, per the spec). Gate-B `ExtractRefusal` is raised INSIDE `_extract_run` → `_run_with_failover` catches it → `failure_classifier.classify` returns `"wall"` → immediate failover to the next provider; when all are exhausted it raises → same loud-fail path.

- [ ] **Step 3: Verify import + full suite**

Run: `& ".\.venv\Scripts\python.exe" -c "import app.services.pipeline"` then `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q`
Expected: import clean; full suite green except the 1 known pre-existing red (`test_notion_defaults_disabled`).

- [ ] **Step 4: Commit**

```bash
git add app/services/pipeline.py
git commit -m "feat(pipeline): extract via local-text + Gate A + failover-summarize; cache v2"
```

---

## Task 7: Acceptance smoke + worklog

**No code.** Generation-affecting → proven by a real run (CLAUDE.md gate).

- [ ] **Step 1: Suites green** — `& ".\.venv\Scripts\python.exe" -m pytest tests/ -q` (all green but the known red).

- [ ] **Step 2: Real extract smoke** — restart the server, generate a section on the live history book (`41aec815`, the one that produced the 275-char garbage). Confirm:
  - the `extract` phase now produces a **real ~5K-char summary** (not a 275-char refusal) — check `phase_outputs.output_md` for `extract`;
  - downstream phases get real content (the LLM judge stops emitting "Dars konteksti mavjud emas" rejections);
  - `agent_usages` shows `lesson.extract` succeeding on gemini with **no PDF attachment**.

- [ ] **Step 3: Failure smokes** — (a) point at a scanned/no-text PDF (or temporarily lower `extract_min_text_chars`) → job `failed` with reason `"unreadable PDF (no text layer)"`, **not** a silent proceed. (b) Force a Gate-B refusal (e.g. a provider that returns junk) → confirm immediate failover to the next provider in `agent_usages`.

- [ ] **Step 4: Worklog** — add a worklog entry to `docs/memory/MASTER_MEMORY.md` + an `INDEX.md` row; close/mark **R10** (subsumed by Gate A) and note **R12(c)** resolved (local-text bypasses the gitignore block). Flag the TOC-extraction follow-up (same gitignore risk, separate path) in WISHLIST.

---

## Self-review

**Spec coverage:** local whole-book text (T4) ✓ · model-locates-by-title prompt (T5) ✓ · Gate A terminal (T3 + T6 raise) ✓ · Gate B failover (T3 + T6 run_fn) ✓ · `_run_with_failover` reuse, claude-excluded, gemini-first (T6) ✓ · `ExtractRefusal` → immediate failover (T2) ✓ · failure = message-only, no `max_attempts` mechanism (T6 raises propagate through the existing head-loop unwind) ✓ · size gate → loud-fail oversize, defer to subset (T6) ✓ · cache `v1→v2` at `:532` (T6) ✓ · pypdf-pinned (T4) ✓ · R10 subsumed / R12c resolved / TOC flagged (T7) ✓ · per-attempt visibility inherits R11 (no new work) ✓.

**Placeholder scan:** none — every code step has real code; the `read_whole_book_text` and `summarize_lesson` tests are `inspect`-level by necessity (DB-free harness + awkward real-PDF fixtures), with the real end-to-end behaviour proven by the T7 CLI smoke (consistent with CLAUDE.md + the repo's existing `inspect`-based tests).

**Type consistency:** `validate_extract_text`/`validate_extract_summary` return `Optional[str]` (reason|None) — used identically in T3 tests and T6. `summarize_lesson(...) -> (str,int,int)` matches the `_extract_run` unpack and the `_run_with_failover` `run_fn` contract `(out, tin, tout)`. `read_whole_book_text(pdf_path) -> str`. `ExtractRefusal` defined in T2, imported as `failure_classifier.ExtractRefusal` in T6. Settings names (`extract_max_text_chars`, `extract_min_text_chars`, `extract_min_printable_ratio`, `extract_min_summary_chars`) identical across T1/T3/T6.
