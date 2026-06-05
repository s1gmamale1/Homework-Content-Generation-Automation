# Extract Robustness — Local-Text Injection + Deterministic Gates + Failover — Design Spec

**Date:** 2026-06-05
**Branch:** Nggaev-v2
**Status:** Draft for user review

## Goal

Make the per-lesson `extract` phase **fail loudly instead of silently producing garbage.** Today a failed extract returns a refusal that *looks* like success and silently poisons every downstream phase. This reworks extract to: read the PDF text **locally** (bypassing the gemini CLI file-read that gitignore now blocks), have the model **locate the lesson by title** within that text (R2-immune), **validate** the result with cheap deterministic gates, and **fail over** across providers — so a bad extract is always caught and never cascades.

## Why (the incident)

Live (job `8d7c31f9`, history): the gemini CLI could no longer read `var/books/…/source.pdf` (gitignore block — `var/` is in `.gitignore:22`, and gemini-cli's `read_file` now honours gitignore). `gemini-2.5-flash` returned `rc=0` with a **275-char "couldn't read the PDF" refusal** (a real extract is ~5K chars). That empty `lesson_context` was threaded into every phase → they refused (*"Dars konteksti mavjud emas"*) or hallucinated (boss-arena invented dates). The job "completed" 9/9 with **garbage content**; only the LLM judge flagged it. Three structural facts make this worse: the block hits **all** gemini models; **R2** (printed≠physical pages) means we can't trust page numbers; and **~43% of textbooks are >20MB** (worklog 0033), which the gemini CLI rejects.

## Current state (verified against code)

- **`extract_lesson_context` (def `agent.py:1242`)** attaches the **full PDF** (or a page-subset only for >20MB via `_should_subset_for_extract`/`_subset_pdf`) and lets the model locate the lesson by title + printed range. The comment at `1271-1277` does this *deliberately* to dodge R2: *"otherwise attach the full book and let the prompt name the printed page range (no physical-page-offset risk)."* The model reads the file via the gemini CLI's `read_file` tool — **this is the read that gitignore now blocks.**
- **R2 (open in ROADMAP):** `_subset_pdf` slices `page_start-1..page_end` (printed numbers) as physical indices, with no front-matter offset correction → wrong pages for any offset book. **We cannot trust `page_start`/`page_end` as physical.**
- **>20MB / 43% (worklog 0033):** gemini rejects >20MB; ~20 of 47 books exceed it (one 497MB scanned, one 93.6MB). Large-book generation is the planned **subset-TOC/auto-shrink** effort.
- **Helpers exist:** `_read_pdf_pages` (`agent.py:863`), `_decode_glyph_text` (`839`), `_run_with_failover` (`pipeline.py`), `_subset_pdf`/`_should_subset_for_extract`. Cross-job extract cache key `prompt_hash = "builtin:extract:v1"` (`pipeline.py:532`, single occurrence inside `_execute_phase`).
- **0033 did NOT touch extract/TOC/generate** — only added a PDF source + an `ingest_pdf` refactor. PDF path unchanged: `var/books/<id>/source.pdf`.

## Design

### New extract flow

```
extract phase (extract_lesson_context reworked):
  1. pypdf reads the WHOLE book text locally (reuse _read_pdf_pages over all pages +
     _decode_glyph_text for broken fonts). The model NEVER touches the file.
  2. SIZE GATE: if text > settings.extract_max_text_tokens →
     LOUD-FAIL "book too large for whole-text extract — needs subset-TOC/shrink"
     (TERMINAL; large-book generation is the deferred subset effort).
  3. GATE A (deterministic, raw text, ONCE): real content? min length + printable-letter
     ratio + /Gxx glyph pattern. Empty/glyph-garbage (scanned/broken-font) →
     LOUD-FAIL "unreadable PDF (no text layer)" (TERMINAL — unfixable input).
  4. SUMMARIZE via failover (_run_with_failover, requested = settings.extract_provider):
       run_fn(provider, model): inject whole-book text + prompt "locate the lesson titled
       '{title}' (printed pages {ps}-{pe} as a hint) and produce a factual summary"
       → GATE B validates the summary → refusal/too-short → RAISE (→ fail over to next provider)
  5. first provider whose summary passes Gate B → lesson_context.
  6. all providers exhausted → job FAILED (loud, /retry-eligible — may be transient).
```

### Read path — gitignore-immune AND R2-immune

- **`pypdf`** (NOT pdfplumber — may be absent; CLAUDE.md treats it as "if present"; the TOC path uses pypdf). Reuse `_read_pdf_pages` (extended to read the whole document) + `_decode_glyph_text`.
- The model locates the lesson by **title** within the whole-book text → **no page-number math** → R2 cannot bite. Printed `page_start`/`page_end` are passed only as a *hint*.
- No PDF attached to the CLI → dodges the gitignore block (and the gemini >20MB ceiling, for the text we inject). This restores the *proven* pre-break behaviour (full book → model self-locates), just via injected text instead of a blocked file.

### Deterministic gates (cheap, no LLM)

Extract failure modes are simple and detectable without a model, so a deterministic check is the right tool (the content phases keep the LLM judge — different concern: *input* quality, not *content* quality).

- **Gate A — raw text, pre-summary, once → TERMINAL.** Catches scanned/broken-font/empty PDFs (the real "can't read this" case). Signals: length below threshold, printable-letter ratio below threshold, `/Gxx` glyph pattern present. **Subsumes R10** (broken-font glyph garbage).
- **Gate B — summary, per attempt, inside `run_fn` → FAILOVER.** Catches the 275-char refusal. Signals: length below threshold **and/or** a refusal-context match — a refusal phrase **anchored** to context (near the start, combined with short length), NOT a bare substring, so a legitimate Uzbek `"…ma'lumot mavjud emas"` inside real content does not false-trigger failover. Start the blocklist tight; expand only on real misses.

### Failover

- Reuse `_run_with_failover(requested_provider=settings.extract_provider, model=settings.extract_model, run_fn=…)`. The actual chain is `gemini (requested → deduped to front) → codex → kimi → opencode` (NOT the raw `failover_provider_order` order `[codex, gemini, kimi, opencode]`; `_failover_chain` puts the requested provider first). **claude stays excluded** (already absent from `failover_provider_order`) — correct hygiene; note this is *not* fixing a live budget leak (extract already never touches claude).
- Gate B lives **inside** `run_fn`, so a junk summary RAISES and the driver fails over. To make a refusal fail over **immediately** (skip the generic `"hard" → 1 same-provider retry`), `run_fn` raises a **typed `ExtractRefusal`** error and the driver/classifier treats it as immediate-failover (0 same-provider retries) — the plan picks the classifier class (a dedicated budget-0 class, or reuse the existing `"wall"`).
- **Per-attempt visibility:** each failover attempt should write an `agent_usages` row so the switch is auditable — this inherits **R11**'s gap (a timed-out/refused attempt may not log a row). Cross-reference R11; don't re-solve it here.

### Failure handling (the core fix — never silent garbage)

**Verified flow (no new retry mechanism):** extract is a head phase. On any failure, `_execute_one_phase` marks the job `failed` + raises → `pipeline.run`'s head loop catches it and `return`s cleanly (`pipeline.py:161-164`) → no exception reaches the worker → `mark_failed_with_retry` is **never** called. So extract failures **already neither consume `max_attempts` nor auto-retry** — the job sits `failed` until a manual `/retry` (which works on any `failed` job). There is no existing mechanism separating "terminal" from "recoverable," and this spec does **not** invent one.

The distinction is therefore **message-only** (the real value, zero new code) — set a clear, distinct failure REASON:
- Size gate → `"book too large for whole-text extract — needs subset-TOC/shrink"`.
- Gate A → `"unreadable PDF (no text layer)"`.
- Gate B exhaustion → `"all extract providers refused/failed"`.

Either path, the extract **never proceeds downstream with bad output** — that's the whole point. (Making Gate-A/size jobs genuinely *un-retryable* — e.g. `/retry` rejecting jobs whose failure reason is terminal — would be a *new* mechanism and is **out of scope** here.)

### Cache

- Bump the extract cache key `prompt_hash` from `"builtin:extract:v1"` → `"builtin:extract:v2"` (**`pipeline.py:532`**, inside `_execute_phase`) so pre-rework cached extracts are not silently reused for new jobs.

## Components touched

- `app/services/agent.py` — rework `extract_lesson_context` (local whole-book text → size gate → Gate A → summarize-via-failover); add deterministic `_validate_extract_text` (Gate A) and `_validate_extract_summary` (Gate B); add `ExtractRefusal`; extend `_read_pdf_pages` to whole-document if needed.
- `app/services/pipeline.py` — extract branch of `_execute_phase`: drive extract through `_run_with_failover`; cache-key v1→v2; terminal-vs-recoverable failure handling.
- `app/services/failure_classifier.py` — recognise `ExtractRefusal` / refusal signal for immediate failover.
- `app/config.py` — `extract_max_text_tokens` (size gate) + Gate A/B thresholds.

## Scope

- **In:** the per-lesson `extract` phase, for books whose extracted text fits the size gate.
- **Out (flagged):**
  - **TOC extraction** — a separate gemini file-read path with the *same* gitignore risk. Log as a follow-up; do not fold in.
  - **Large-book GENERATION** (>20MB / scanned, ~43% of corpus) — the planned **subset-TOC/auto-shrink** effort. This spec **loud-fails** oversize/scanned input and defers; that effort can reuse this spec's local-text + title-locate building blocks.
- **Subsumes:** R10 (broken-font glyph garbage → Gate A).
- **Obsoletes:** the `.gemini/settings.json respectGitIgnore:false` workaround — local text bypasses the gitignore block entirely, so it's no longer needed.

## Testing

- **Gate A (unit):** good text passes; empty / `/Gxx` glyph-garbage / over-budget fail (terminal).
- **Gate B (unit):** real summary passes; a 275-char refusal and a refusal-phrase-at-start fail (→ failover); a legitimate Uzbek `"…mavjud emas"` inside real content **passes** (no false failover).
- **Failover (unit, stub `run_fn`):** refusal → next provider; all fail → raises; `ExtractRefusal` → immediate failover (0 same-provider retries); claude never appears.
- **Cache:** `prompt_hash` resolves to `"builtin:extract:v2"`.
- **Acceptance (real CLI smoke):** the live history book (`41aec815`) — confirm a **real ~5K summary** (not a 275-char refusal), downstream phases get real content, and the judge stops flagging "empty lesson context." Plus: a deliberately unreadable PDF → **terminal loud fail** (job `failed`, not a silent proceed); a forced provider refusal → failover to the next provider.

## Risks / open items

- **Title-locate** relies on the model finding the lesson title within the whole-book text — robust for normal books, and the size gate bounds the input. A title that recurs many times could in theory confuse the model; the printed-page **hint** mitigates this. Low risk.
- **Scanned PDFs** (no text layer) are **terminal** here by design — they need native image read / OCR, which belongs to the subset-TOC/shrink effort, not this one.
- **Refusal heuristic (Gate B)** is a tuned blocklist — start anchored and tight; treat false-failovers/misses as tuning, not redesign.
- **Whole-book cost & coverage (state plainly):** we inject the whole book's text **per lesson** (×N lessons) — bounded by the size gate and amortised by the cross-job extract cache, tolerable on cheap gemini-flash but a real cost. And by choosing whole-book over a page-band (for full R2-immunity), **any book whose text exceeds `extract_max_text_tokens` terminal-fails at extract by design.** Since ~43% of the corpus is already >20MB, a real slice becomes non-extractable here until the subset-TOC/shrink effort lands. This is an accepted, explicit trade-off (correctness over coverage), not an oversight.
- **Token budget** (`extract_max_text_tokens`) value to be set against gemini-flash's context and real book sizes during the plan — a conservative default that comfortably fits a normal <20MB textbook's text, biased so small books always pass and only genuinely-huge ones fail.
