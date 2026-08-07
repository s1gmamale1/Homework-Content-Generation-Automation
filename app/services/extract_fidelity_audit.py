"""Extract-fidelity audit (offline): grades an already-generated `extract`
phase output against the textbook pages it was written from.

Loads exactly what the live guard's verify call sees for one job — the
extract markdown, the page-window source text, and (separately) the whole
book text the extract was actually generated from — plus a structured
per-claim verdict shape a later task's LLM call fills in.

Mirrors `app.services.teaching_audit`'s `AuditInputs` / `load_audit_inputs`
pattern, including its FAIL-LOUD posture: missing job/book/TOC row, NULL
page range, missing PDF, or empty page text all raise
`ExtractFidelityAuditError` — never silently degrade or return a partial
object.

Task 1 built the data-loading + result models (`ExtractAuditInputs`,
`load_extract_audit_inputs`, `ClaimVerdict`, `ExtractFidelityReport`,
`_normalize_span`). Task 2 adds the pieces needed to run and calibrate an
adjudicator LLM call, still with zero LLM calls of its own: `Adjudication`
(the structured-output wrapper passed as a response schema),
`build_adjudicator_prompt` (pure string assembly), `inject_mutation`
(deterministic, seeded fault injection for calibrating the adjudicator
against a known-wrong claim), and `reground_unsupported` (a free,
deterministic pass that re-checks `unsupported` claims against the whole
book text).

Task 3 (this addition) is the orchestrator: `audit_one` (the one billed
adjudicator call per lesson, FAIL-LOUD like `teaching_audit._call` — a dead
adjudicator must never read as "no drift"), `select_mutation` +
`audit_with_control` (the paired sensitivity-calibration leg — reuses an
already-computed pristine report, makes exactly ONE new call), and
`summarize_runs` (per-subject/overall aggregation, split by claim_type and
by cross_language). `LessonCandidate` + `select_sample` are the pure,
DB-free sampling algorithm `scripts/extract_fidelity_audit.py` calls after
its own DB query builds the candidate list — kept here so the stratified
sampling logic is unit-testable without a DB. No LLM calls, no network, no
DB, no PDF reads happen in this module except inside `audit_one` /
`audit_with_control`'s single `agent.run_phase` call each; `load_extract_
audit_inputs` and the CLI's DB query remain the only DB/PDF touchpoints.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, replace
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.services import agent

# Extra pages read on each side of the TOC's recorded range, matching
# `teaching_audit._PAGE_WINDOW_MARGIN` (measured -3..+2 TOC page-offset
# spread). A narrower window (e.g. margin=1, the `pipeline._verify_source_for_
# section` window) measures guard blindness, not a true drift base rate: a
# window that misses the lesson body makes every claim read `unsupported`,
# including a planted mutation, which would pass a calibration gate on a
# broken instrument.
_PAGE_WINDOW_MARGIN = 4

_WHITESPACE_RE = re.compile(r"\s+")


class ExtractFidelityAuditError(RuntimeError):
    """Any unrecoverable audit-input load failure (missing data)."""


def _normalize_span(s: str) -> str:
    """Prose normalization for claim-span substring matching: casefold,
    collapse whitespace runs to a single space, strip. Deliberately NOT
    `agent._normalize_expr`-style — that function is math-specific (maps
    `·*×`->`*`, `−–—`->`-`, `÷`->`/`, strips ALL spaces), which is wrong for
    prose. Match semantics (defined once, here, for Task 2's substring
    re-grounding and Task 3's planted-span detection): compare after this
    normalization, exact normalized substring only — no fuzzy matching, which
    would silently downgrade real drift."""
    return _WHITESPACE_RE.sub(" ", s.casefold()).strip()


ClaimStatus = Literal["contradicts", "unsupported", "ok"]
ClaimType = Literal["name", "date", "number", "definition", "quote", "term", "other"]


class ClaimVerdict(BaseModel):
    """One claim's grading against the source text.

    `claim_span` is the exact substring of the extract the verdict is
    about — carried verbatim so downstream consumers (Task 2's
    `reground_unsupported` substring match, Task 3's paired planted-span
    detection) don't each invent their own extraction. Match semantics: see
    `_normalize_span`.
    """

    claim_span: str
    claim_type: ClaimType
    status: ClaimStatus


class ExtractFidelityReport(BaseModel):
    """Per-lesson audit result: per-status claim counts (Task 1) plus,
    since Task 3, the lesson metadata + per-`claim_type` breakdown +
    regrounding downgrade count a later reporting step needs (decision #7):
    a JSON report line must be traceable to its job/subject/family/grade
    /languages without a second DB lookup, and `cross_language` (source
    language != output language) must be present so `summarize_runs` can
    split `unsupported` by it — a cross-language lesson's `unsupported` is
    inflated by design (`reground_unsupported`'s substring match rarely
    hits across a translation), so mixing the two populations makes the
    base rate meaningless.

    The metadata fields default to `None` (and `claim_type_counts`/`claims`
    default to empty) so `from_claims` — Task 1's pure counts-only
    constructor, still used by earlier tests — keeps working unchanged.
    """

    job_id: Optional[str] = None
    subject: Optional[str] = None
    family: Optional[str] = None
    grade: Optional[str] = None
    source_language: Optional[str] = None
    output_language: Optional[str] = None
    cross_language: Optional[bool] = None
    ok_count: int = 0
    contradicts_count: int = 0
    unsupported_count: int = 0
    # claim_type -> {"ok": n, "contradicts": n, "unsupported": n}
    claim_type_counts: dict[str, dict[str, int]] = Field(default_factory=dict)
    downgraded_count: int = 0
    claims: list[ClaimVerdict] = Field(default_factory=list)

    @property
    def total_count(self) -> int:
        return self.ok_count + self.contradicts_count + self.unsupported_count

    @classmethod
    def from_claims(cls, claims: list[ClaimVerdict]) -> "ExtractFidelityReport":
        """Aggregate a claim list into per-status counts (+ the
        per-`claim_type` breakdown). An empty list aggregates to zero
        drift, not an error. No lesson metadata — this is the pure,
        DB-free constructor; `audit_one` builds the full report."""
        counts = {"ok": 0, "contradicts": 0, "unsupported": 0}
        claim_type_counts: dict[str, dict[str, int]] = {}
        for c in claims:
            counts[c.status] += 1
            bucket = claim_type_counts.setdefault(
                c.claim_type, {"ok": 0, "contradicts": 0, "unsupported": 0}
            )
            bucket[c.status] += 1
        return cls(
            ok_count=counts["ok"],
            contradicts_count=counts["contradicts"],
            unsupported_count=counts["unsupported"],
            claim_type_counts=claim_type_counts,
            claims=list(claims),
        )


@dataclass(frozen=True)
class ExtractAuditInputs:
    job_id: str
    book_id: str
    subject: str
    family: str
    grade: Optional[str]
    source_language: str
    output_language: str
    lesson_title: str
    page_start: int
    page_end: int
    extract_md: str
    source_text: str
    whole_book_text: str


async def load_extract_audit_inputs(
    job_id: UUID | str, *, whole_book_text: Optional[str] = None
) -> ExtractAuditInputs:
    """Load everything the extract-fidelity audit needs for one job. FAILS
    LOUD on any gap — missing job/book/TOC row, NULL page range, missing
    PDF, empty page text, or no completed `extract` phase output.

    `whole_book_text`, when given, is used instead of re-reading the PDF —
    reading whole-book text costs seconds per call and a later task audits
    many lessons drawn from a handful of books, so the caller caches one
    read per `book_id`. When `None`, it's read via `agent.read_whole_book_text`.
    """
    from app.db import SessionLocal
    from app.models import Book, HomeworkJob, TOCEntry
    from app.repositories import phase_outputs as phase_repo
    from app.services import storage
    from app.services import subjects

    async with SessionLocal() as session:
        job = await session.get(HomeworkJob, job_id)
        if job is None:
            raise ExtractFidelityAuditError(f"homework_jobs row {job_id!r} not found")
        book = await session.get(Book, job.book_id)
        if book is None:
            raise ExtractFidelityAuditError(f"books row {job.book_id!r} not found")
        toc = await session.get(TOCEntry, job.toc_entry_id)
        if toc is None:
            raise ExtractFidelityAuditError(f"toc_entries row {job.toc_entry_id!r} not found")
        if toc.page_start is None or toc.page_end is None:
            raise ExtractFidelityAuditError(
                f"TOC entry {toc.id} has no page range (page_start={toc.page_start!r}, "
                f"page_end={toc.page_end!r}) — cannot derive a fidelity audit"
            )
        rows = await phase_repo.list_for_job(session, job.id)
        extract_row = next(
            (
                r
                for r in rows
                if r.phase_name == "extract" and r.status == "done" and (r.output_md or "").strip()
            ),
            None,
        )
        if extract_row is None:
            raise ExtractFidelityAuditError(
                f"job {job_id} has no completed 'extract' phase output to audit"
            )
        extract_md = extract_row.output_md
        subject, grade = job.subject, book.grade
        source_language, output_language = book.source_language, job.output_language
        lesson_title, book_id = toc.section_title, str(job.book_id)
        page_start, page_end = toc.page_start, toc.page_end

    subject_def = subjects.REGISTRY.get(subject)
    family = subject_def.family if subject_def is not None else "default"

    pdf_path = storage.book_pdf_path(book_id)
    if not pdf_path.exists():
        raise ExtractFidelityAuditError(f"source PDF missing: {pdf_path}")
    source_text = agent.read_page_range_text(
        pdf_path, page_start, page_end, margin=_PAGE_WINDOW_MARGIN
    )
    if not source_text:
        raise ExtractFidelityAuditError(
            f"pages {page_start}-{page_end} (±{_PAGE_WINDOW_MARGIN}) of {pdf_path.name} "
            f"yielded no text (image-only scan?) — cannot audit extract fidelity"
        )

    if whole_book_text is None:
        whole_book_text = agent.read_whole_book_text(pdf_path)
    if not whole_book_text:
        raise ExtractFidelityAuditError(
            f"{pdf_path.name} yielded no whole-book text — cannot audit extract fidelity"
        )

    return ExtractAuditInputs(
        job_id=str(job_id),
        book_id=book_id,
        subject=subject,
        family=family,
        grade=grade,
        source_language=source_language,
        output_language=output_language,
        lesson_title=lesson_title,
        page_start=page_start,
        page_end=page_end,
        extract_md=extract_md,
        source_text=source_text,
        whole_book_text=whole_book_text,
    )


# ============================================================================
# Task 2 — adjudicator prompt + mutation injection + free re-grounding pass.
# Everything below is pure (no LLM calls, no network, no DB, no PDF reads).
# ============================================================================


class Adjudication(BaseModel):
    """Structured-output wrapper for the adjudicator LLM call.

    `build_adjudicator_prompt` describes exactly this JSON shape (a single
    `"claims"` array of per-claim verdicts) in its prompt text. A later task
    passes `Adjudication` itself as the response schema for the structured
    LLM call, so the prompt's described shape and this model must stay in
    lockstep — if you change one, change the other.
    """

    claims: list[ClaimVerdict]


def build_adjudicator_prompt(inputs: ExtractAuditInputs) -> str:
    """Assemble the adjudicator prompt for one lesson's extract-fidelity
    grading. Pure string assembly — no LLM call happens here.

    Two hazards dominate real drift-detection noise for this pipeline and
    are called out explicitly, by name, using the lesson's ACTUAL languages
    (not a generic "the source language"):

    1. **Translation.** Extracts routinely render an `inputs.source_language`
       textbook into `inputs.output_language` prose (e.g. an English or
       Russian book into Uzbek). A naive checker flags every translated
       claim as drift; this prompt tells the model translation, paraphrase,
       transliteration, and rounding are all `ok`.
    2. **Legitimate compression.** An extract is a summary, not a
       transcription. Omitting a source detail is not drift — only claims
       the extract actually makes are graded.
    """
    return f"""You are grading an EXTRACT of a textbook lesson against the SOURCE PAGES it was written from, for factual fidelity only.

Lesson title: {inputs.lesson_title}
Source pages are written in: {inputs.source_language}
Extract is written in: {inputs.output_language}

For every discrete factual claim in the EXTRACT (a name, date, number, definition, quote, or term), decide one status:

- "contradicts" — the EXTRACT asserts something the SOURCE PAGES directly deny or state differently (e.g. a different year, a different person, a definition that means something else).
- "unsupported" — the EXTRACT asserts something you cannot locate anywhere in the SOURCE PAGES, and it is not merely a translation or paraphrase of something that IS there.
- "ok" — the claim is faithful to the SOURCE PAGES, including when it only reaches that faithfulness through translation, paraphrase, or rounding.

Translation-tolerance clause (read carefully — this is the single most common false alarm): the SOURCE PAGES are in {inputs.source_language} and the EXTRACT is in {inputs.output_language}. Paraphrase, translation from {inputs.source_language} to {inputs.output_language}, transliteration of names, and rounding of numbers are all "ok" — they are NOT drift. Never mark a claim "contradicts" or "unsupported" merely because its wording or language differs from the SOURCE PAGES; judge the MEANING, not the surface form.

Omission-is-not-drift clause: the EXTRACT is a summary of the SOURCE PAGES, not a full transcription. The ABSENCE of a source detail from the EXTRACT is not drift and is not a claim to grade — only grade claims the EXTRACT actually makes.

Return ONLY a JSON object with this exact shape:
{{
  "claims": [
    {{"claim_span": "<verbatim substring copied from the EXTRACT>", "claim_type": "name" | "date" | "number" | "definition" | "quote" | "term" | "other", "status": "contradicts" | "unsupported" | "ok"}}
  ]
}}

`claim_span` must be an exact verbatim substring of the EXTRACT text below — copy it, do not paraphrase it.

EXTRACT:
{inputs.extract_md}

SOURCE PAGES:
{inputs.source_text}
"""


@dataclass(frozen=True)
class Mutation:
    """Record of one planted, semantically WRONG claim, produced by
    `inject_mutation`'s swap-within-document technique: a span of `kind`
    already present in the document is replaced by a DIFFERENT span of the
    same `kind`, also already present in the document. That guarantees the
    planted text is plausible, in the right language, and genuinely
    contradicts the source — never a fabricated string that doesn't appear
    anywhere in the document.
    """

    kind: str
    original: str
    replacement: str
    offset: int


# Mirrors `app.services.phase_judge._YEAR_RE`. Deliberately mirrored rather
# than imported — this module's mutation logic shouldn't take a dependency
# on phase_judge's private internals for a two-line regex.
_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")

# Capitalized "word" candidates for kind="name": length >= 4, i.e. long
# enough to plausibly be a proper noun rather than an abbreviation.
_WORD_RE = re.compile(r"\b\w{4,}\b", re.UNICODE)

# Definitional connectors, in the shapes actually observed across a
# 3,427-extract corpus measurement (round-1 fix): em/en dash prose
# (" — "/" – "), an "is"/"are" copula (English-biased — only a 3-8% hit
# rate outside the english subject, kept because it's near-universal
# THERE), a markdown bullet with an inline colon ("- Term: predicate"),
# and a bold term followed by a colon ("**Term**:"). The colon-bullet form
# is nearly as common as the dash form across most subjects and was
# MISSED entirely before this fix — realistic bulleted extract markdown
# (the dominant real shape) produced zero definition candidates.
_DEF_CONNECTOR_RE = re.compile(
    r" — | – "
    r"|\bis\b|\bare\b"
    r"|^[ \t]*[-*][ \t]+[^\n:]+?:[ \t]+"
    r"|\*\*[^\n*]+?\*\*:[ \t]*",
    re.IGNORECASE | re.MULTILINE,
)

# Characters that, on their own, don't count as "real" content before a word
# on its line — markdown heading/list/number markers and plain whitespace.
_LINE_MARKER_CHARS = "#*->0123456789. \t"


def _date_candidates(md: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _YEAR_RE.finditer(md)]


def _is_word_initial_in_sentence_or_line(md: str, start: int) -> bool:
    """True if the word starting at `start` is the first word of its
    (markdown-marker-stripped) line, or the first word of its sentence
    (nothing but whitespace, or a `.`/`!`/`?`, precedes it on the line)."""
    line_start = md.rfind("\n", 0, start) + 1
    prefix = md[line_start:start]
    if prefix.strip().lstrip(_LINE_MARKER_CHARS) == "":
        return True
    j = len(prefix) - 1
    while j >= 0 and prefix[j] in " \t":
        j -= 1
    if j < 0:
        return True
    return prefix[j] in ".!?"


def _name_candidates(md: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for m in _WORD_RE.finditer(md):
        word = m.group(0)
        if not word[0].isupper():
            continue
        if _is_word_initial_in_sentence_or_line(md, m.start()):
            continue
        out.append((word, m.start(), m.end()))
    return out


def _definition_candidates(md: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for m in _DEF_CONNECTOR_RE.finditer(md):
        start = m.end()
        while start < len(md) and md[start] in " \t":
            start += 1
        end = start
        while end < len(md) and md[end] not in ".!?\n":
            end += 1
        text = md[start:end].rstrip()
        end = start + len(text)
        if text:
            out.append((text, start, end))
    return out


_CANDIDATE_FINDERS = {
    "date": _date_candidates,
    "name": _name_candidates,
    "definition": _definition_candidates,
}


def inject_mutation(
    md: str, kind: str, seed: int, *, forbidden_text: str
) -> Optional[tuple[str, Mutation]]:
    """Plant exactly one semantically WRONG claim in `md`, via
    swap-within-document: find two distinct candidate spans of `kind`
    already present in `md`, then replace one with the other's text. This
    guarantees the planted span is plausible, in the right language, and
    genuinely contradicts the source — never a fabricated string.

    `forbidden_text` (the whole book text the extract was generated from)
    screens candidate REPLACEMENT values: a replacement whose normalized
    form already occurs somewhere in `forbidden_text` is rejected, even if
    it's a valid distinct-text candidate. Reason: `reground_unsupported`
    downgrades any `unsupported` claim found anywhere in the whole book —
    if the planted replacement also happens to occur there, the planted
    error would get silently downgraded to `ok`, making the adjudicator
    look like it missed a mutation it never had a chance to catch. If every
    surviving candidate pair collides, returns `None` (skip the lesson —
    correct behavior, never fabricate a replacement absent from the
    document).

    Deterministic given `(md, kind, seed)`: candidate discovery is a pure
    scan of `md` (fixed order), and the only choice — enumerate every valid
    `(original, replacement)` pair up front, then pick one via a seeded
    `random.Random(seed).choice` — never the module-level `random` and
    never time-based entropy. Determinism holds across processes: pair
    enumeration is a plain list built by nested `enumerate` loops, with no
    `set`/`dict` iteration order in the path.

    Returns `(mutated_md, Mutation)`, or `None` when fewer than two
    distinct-text candidates exist for `kind` (including when every
    surviving candidate pair collides with `forbidden_text`) — this is the
    "skip this lesson" case and is safe to check with `is None`.

    Raises `ValueError` if `kind` is not one of the known kinds
    (`date`/`name`/`definition`). This is NOT part of the `None`-means-skip
    contract — an unrecognized `kind` is a caller bug (e.g. a typo), not a
    data gap, so it is never silently treated as "skip this lesson". A
    caller that only checks `if inject_mutation(...) is None: skip()` will
    get an uncaught `ValueError` for a bad `kind`; validate `kind` against
    `{"date", "name", "definition"}` first if that's undesired.
    """
    finder = _CANDIDATE_FINDERS.get(kind)
    if finder is None:
        raise ValueError(f"unknown mutation kind: {kind!r}")

    candidates = finder(md)
    if len(candidates) < 2:
        return None

    forbidden_norm = _normalize_span(forbidden_text)
    pairs: list[tuple[int, int]] = []
    for i, orig in enumerate(candidates):
        for j, repl in enumerate(candidates):
            if i == j or orig[0] == repl[0]:
                continue
            if forbidden_norm and _normalize_span(repl[0]) in forbidden_norm:
                continue
            pairs.append((i, j))
    if not pairs:
        return None

    rng = random.Random(seed)
    i, j = rng.choice(pairs)
    original_span = candidates[i]
    replacement_span = candidates[j]

    mutated = md[: original_span[1]] + replacement_span[0] + md[original_span[2] :]
    mutation = Mutation(
        kind=kind,
        original=original_span[0],
        replacement=replacement_span[0],
        offset=original_span[1],
    )
    return mutated, mutation


# A normalized substring match on a SHORT span is not grounding: a bare
# year (e.g. "1917") or a bare surname will match somewhere in a 200KB
# textbook by chance, silently downgrading a genuinely invented claim to
# "ok" — the exact opposite of this tool's purpose. Require the normalized
# span to carry real sentence-level content before a downgrade can fire.
_REGROUND_MIN_TOKENS = 2
_REGROUND_MIN_CHARS = 12


def reground_unsupported(
    claims: list[ClaimVerdict], whole_book_text: str
) -> tuple[list[ClaimVerdict], int]:
    """Free, deterministic re-check of every `unsupported` claim against the
    WHOLE book text (not just the page window the adjudicator saw).
    Rationale: the extract was generated from the whole book, so a claim
    grounded on a page outside the audit's page window is not drift — it's
    a window artifact. Downgrades a hit from `unsupported` to `ok`.

    `contradicts` claims are never downgraded — only `unsupported` is a
    window artifact; a contradiction is a real assertion the source denies,
    regardless of what else appears in the book. `ok` claims pass through
    unchanged.

    A downgrade only fires when the normalized claim span has at least
    `_REGROUND_MIN_TOKENS` whitespace-separated tokens AND at least
    `_REGROUND_MIN_CHARS` characters (see the constants' docstring) — below
    that threshold the claim keeps its `unsupported` status regardless of
    whether it matches.

    Matching uses Task 1's `_normalize_span` on both the claim span and the
    whole book text (exact normalized substring, no fuzzy matching) — the
    same match semantics Task 1 defined for this exact purpose, not a
    second normalizer.

    Returns `(new_claims, downgraded_count)`. `new_claims` is a NEW list of
    NEW `ClaimVerdict` objects — the input `claims` list and its objects
    are never mutated.
    """
    whole_norm = _normalize_span(whole_book_text)
    new_claims: list[ClaimVerdict] = []
    downgraded = 0
    for c in claims:
        new_status = c.status
        if c.status == "unsupported":
            span_norm = _normalize_span(c.claim_span)
            eligible = (
                len(span_norm) >= _REGROUND_MIN_CHARS
                and len(span_norm.split(" ")) >= _REGROUND_MIN_TOKENS
            )
            if eligible and span_norm in whole_norm:
                new_status = "ok"
                downgraded += 1
        new_claims.append(
            ClaimVerdict(claim_span=c.claim_span, claim_type=c.claim_type, status=new_status)
        )
    return new_claims, downgraded


# ============================================================================
# Task 3 — orchestrator (audit_one / select_mutation / audit_with_control /
# summarize_runs) + the pure sampling algorithm (LessonCandidate /
# select_sample). Every LLM call happens inside `audit_one` — exactly one
# `agent.run_phase` per call, FAIL-LOUD, mirroring `teaching_audit._call`.
# ============================================================================


def _claim_type_counts(claims: list[ClaimVerdict]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for c in claims:
        bucket = out.setdefault(c.claim_type, {"ok": 0, "contradicts": 0, "unsupported": 0})
        bucket[c.status] += 1
    return out


async def audit_one(
    inputs: ExtractAuditInputs,
    *,
    provider: str,
    model: Optional[str],
    transport: str,
    calls: list[dict],
) -> ExtractFidelityReport:
    """Run the ONE adjudicator call for one lesson's extract-fidelity audit,
    then the free `reground_unsupported` pass (decision #6: regrounding runs
    on every lesson's claims, unconditionally).

    Mirrors `teaching_audit._call`: FAILS LOUD on any call failure or an
    unparsed response — never silently returns a clean (zero-drift) report,
    because a dead adjudicator must not read as "no drift". On success,
    appends exactly one `{step, provider, model, usage}` entry to the
    caller-owned `calls` list (the audit's own usage rows are tagged
    `operation="xfid:audit"`, `homework_job_id=None`, `phase_output_id=None`
    — distinguishable from, and unattributed to, the production job whose
    extract is being graded).

    NOTE (carried from `teaching_audit._call`): `run_phase` may log more
    than one `agent_usages` row for this one logical call — a
    structured-output validation retry logs the failed attempt too. `usage`
    here is the successful final attempt only, so a summed $ total can
    undercount a retried call.
    """
    prompt = build_adjudicator_prompt(inputs)
    try:
        result = await agent.run_phase(
            provider=provider,
            model=model,
            phase_prompt=prompt,
            phase_name="__xfid__",
            homework_job_id=None,
            phase_output_id=None,
            schema=Adjudication,
            operation="xfid:audit",
            transport=transport,
        )
    except Exception as exc:
        raise ExtractFidelityAuditError(
            f"xfid:audit call failed for job {inputs.job_id}: {exc}"
        ) from exc
    if result.parsed is None:
        raise ExtractFidelityAuditError(
            f"xfid:audit returned no parsed Adjudication for job {inputs.job_id}"
        )
    calls.append(
        {"step": "audit", "provider": provider, "model": model, "usage": dict(result.usage or {})}
    )

    claims, downgraded = reground_unsupported(result.parsed.claims, inputs.whole_book_text)
    counts = {"ok": 0, "contradicts": 0, "unsupported": 0}
    for c in claims:
        counts[c.status] += 1

    return ExtractFidelityReport(
        job_id=inputs.job_id,
        subject=inputs.subject,
        family=inputs.family,
        grade=inputs.grade,
        source_language=inputs.source_language,
        output_language=inputs.output_language,
        cross_language=inputs.source_language != inputs.output_language,
        ok_count=counts["ok"],
        contradicts_count=counts["contradicts"],
        unsupported_count=counts["unsupported"],
        claim_type_counts=_claim_type_counts(claims),
        downgraded_count=downgraded,
        claims=claims,
    )


def lesson_seed(seed: int, job_id: str) -> int:
    """Stable per-lesson seed derived from a base `seed` + `job_id`.

    Deliberately NOT Python's built-in `hash()` — that's randomized
    per-process (`PYTHONHASHSEED`) and would break the cross-process
    determinism `inject_mutation`/`select_mutation`/`select_sample` all
    promise (a run must be reproducible from the same `--sample-seed`).
    `hashlib.sha256` is stable across processes and interpreters.
    """
    digest = hashlib.sha256(f"{seed}:{job_id}".encode("utf-8")).hexdigest()
    return seed ^ int(digest[:8], 16)


# Fallback order for kind selection is itself seed-derived (shuffled below),
# not fixed — this list is just the pre-shuffle starting point.
_MUTATION_KIND_ORDER = ("date", "name", "definition")


def select_mutation(
    md: str, seed: int, *, forbidden_text: str
) -> Optional[tuple[str, str, Mutation]]:
    """Try each mutation kind, in a deterministic seed-derived order, and
    return the first that successfully plants: `(kind, mutated_md,
    Mutation)`.

    Decision (measured against the real corpus): pinning one kind per
    lesson loses calibration samples — `date` plants on only ~5% of english
    lessons (English-language teaching text barely contains years) while
    `name` plants on 96-100% and `definition` on 54-57% of
    history/geografiya. So every kind is tried, in a seed-shuffled order,
    and the first success wins; only when EVERY kind fails to plant does
    this return `None` — the caller must skip that lesson, logged and
    counted, never silently.
    """
    order = list(_MUTATION_KIND_ORDER)
    random.Random(seed).shuffle(order)
    for kind in order:
        planted = inject_mutation(md, kind, seed, forbidden_text=forbidden_text)
        if planted is not None:
            mutated_md, mutation = planted
            return kind, mutated_md, mutation
    return None


def _claim_flags_span(claims: list[ClaimVerdict], span_text: str) -> bool:
    """True if some non-`ok` claim's (normalized) span contains the
    (normalized) `span_text` — used to detect whether an arm's claim list
    "flags" a planted mutation's replacement text. Matches on containment
    (not equality) because the adjudicator's `claim_span` is a verbatim
    EXTRACT substring that may wrap the planted text in a larger sentence,
    not necessarily the bare replacement token itself."""
    norm = _normalize_span(span_text)
    if not norm:
        return False
    return any(c.status != "ok" and norm in _normalize_span(c.claim_span) for c in claims)


@dataclass(frozen=True)
class PairedFidelityResult:
    """One lesson's paired sensitivity-calibration result: the pristine
    (unmutated) report, the mutated-arm report, the planted `Mutation`
    itself, and whether the instrument caught it."""

    kind: str
    mutation: Mutation
    pristine: ExtractFidelityReport
    mutated: ExtractFidelityReport
    detected_planted: bool


async def audit_with_control(
    inputs: ExtractAuditInputs,
    pristine: ExtractFidelityReport,
    *,
    seed: int,
    provider: str,
    model: Optional[str],
    transport: str,
    calls: list[dict],
) -> Optional[PairedFidelityResult]:
    """Paired sensitivity-calibration audit for one lesson.

    Reuses the already-computed `pristine` report for this lesson (decision
    #4: NEVER re-runs the pristine audit) and makes exactly ONE new call —
    the mutated arm, via `audit_one`. `forbidden_text` for the planted
    mutation is `inputs.whole_book_text` (decision #2 — that is what the
    collision screen in `inject_mutation` exists for).

    Returns `None` when `select_mutation` cannot plant any kind (all three
    exhausted) — the lesson must be SKIPPED, not fabricated; the caller is
    responsible for logging and counting the skip. No call is made in this
    case, so `calls` is left untouched.

    `detected_planted` is TRUE only when the mutated arm flags the planted
    replacement span AND the pristine arm does NOT flag that same span
    (`_claim_flags_span` on each arm's post-regrounding claims). Flagging
    the same span in BOTH arms is a FALSE POSITIVE — a pre-existing bias
    against that text unrelated to the mutation — not a detection.
    """
    planted = select_mutation(inputs.extract_md, seed, forbidden_text=inputs.whole_book_text)
    if planted is None:
        return None
    kind, mutated_md, mutation = planted

    mutated_inputs = replace(inputs, extract_md=mutated_md)
    mutated_report = await audit_one(
        mutated_inputs, provider=provider, model=model, transport=transport, calls=calls
    )

    mutated_flags = _claim_flags_span(mutated_report.claims, mutation.replacement)
    pristine_flags = _claim_flags_span(pristine.claims, mutation.replacement)
    detected = mutated_flags and not pristine_flags

    return PairedFidelityResult(
        kind=kind,
        mutation=mutation,
        pristine=pristine,
        mutated=mutated_report,
        detected_planted=detected,
    )


def _empty_bucket() -> dict:
    return {
        "lessons": 0,
        "ok_count": 0,
        "contradicts_count": 0,
        "unsupported_count": 0,
        "downgraded_count": 0,
        "claim_type_counts": {},
    }


def _add_report_to_bucket(bucket: dict, r: ExtractFidelityReport) -> None:
    bucket["lessons"] += 1
    bucket["ok_count"] += r.ok_count
    bucket["contradicts_count"] += r.contradicts_count
    bucket["unsupported_count"] += r.unsupported_count
    bucket["downgraded_count"] += r.downgraded_count
    for ctype, counts in r.claim_type_counts.items():
        dest = bucket["claim_type_counts"].setdefault(
            ctype, {"ok": 0, "contradicts": 0, "unsupported": 0}
        )
        for status, n in counts.items():
            dest[status] += n


def _new_group() -> dict:
    return {
        "combined": _empty_bucket(),
        "same_language": _empty_bucket(),
        "cross_language": _empty_bucket(),
    }


def summarize_runs(reports: list[ExtractFidelityReport]) -> dict:
    """Aggregate per-subject and overall counts of `contradicts` /
    `unsupported` (and `ok`) per lesson, split by `claim_type`, AND split
    by `cross_language` (decision #7).

    The `cross_language` split exists because `reground_unsupported` is
    language-blind: a claim in a cross-language lesson (source_language !=
    output_language, e.g. an English textbook summarized in Uzbek) almost
    never has its normalized span found verbatim in the whole book text —
    the words are literally different — so `unsupported` is inflated for
    that population BY DESIGN, not because more real drift occurred.
    Mixing cross-language and same-language lessons into one rate would
    make the base rate meaningless; `"combined"` is still reported for
    convenience, but `"same_language"` is the number that means "drift
    rate", and `"cross_language"` is a separate, inflated population.

    Returns `{"overall": <group>, "by_subject": {subject: <group>}}` where
    each `<group>` is `{"combined": <bucket>, "same_language": <bucket>,
    "cross_language": <bucket>}` and each `<bucket>` has `lessons`,
    `ok_count`, `contradicts_count`, `unsupported_count`,
    `downgraded_count`, `claim_type_counts`. A report with
    `cross_language=None` (unset) is treated as `same_language` (falsy).
    """
    overall = _new_group()
    by_subject: dict[str, dict] = {}
    for r in reports:
        lang_key = "cross_language" if r.cross_language else "same_language"
        subject_group = by_subject.setdefault(r.subject or "unknown", _new_group())
        for group in (overall, subject_group):
            _add_report_to_bucket(group["combined"], r)
            _add_report_to_bucket(group[lang_key], r)
    return {"overall": overall, "by_subject": by_subject}


# --------------------------------------------------------------------------
# Pure sampling algorithm. `scripts/extract_fidelity_audit.py` runs the DB
# query (candidate lessons per subject) and hands the results here — kept
# in this module, not the script, so the stratified-sampling logic itself
# is unit-testable without a DB (mirrors why `load_extract_audit_inputs`'s
# pure helpers live here instead of the CLI).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LessonCandidate:
    """One candidate lesson for sampling — enough metadata to stratify and
    to cache whole-book text by `book_id` — without yet loading the
    extract or the PDF (that happens lazily via `load_extract_audit_inputs`
    for only the lessons actually selected)."""

    job_id: str
    book_id: str
    subject: str
    grade: Optional[str]
    source_language: str


def select_sample(
    candidates: list[LessonCandidate],
    n: Optional[int],
    seed: int,
    *,
    stratify: bool = True,
) -> list[LessonCandidate]:
    """Deterministic sample of up to `n` candidates (`None` = take all)
    given a fixed `seed`. Never mutates `candidates`.

    `stratify=True` (default) spreads the sample as evenly as possible
    across distinct `(grade, source_language)` cells before letting any one
    cell contribute a second pick: cells (and their own members) are each
    seed-shuffled, then filled round-robin over a seed-shuffled cell order.
    A subject with only one cell (e.g. all-grade-8 english) degrades to a
    plain seeded shuffle. A subject with many more candidates than `n` and
    several cells (e.g. history across 7 grades x 2 languages) gets
    best-effort coverage of grade/language combinations instead of a plain
    random draw that could land entirely in one cell.

    Deterministic across processes: `candidates` is first sorted by
    `job_id` (never dict/set iteration order) before any `random.Random`
    shuffling, so the same `(candidates, n, seed)` always yields the same
    sample and the same order.
    """
    ordered = sorted(candidates, key=lambda c: c.job_id)
    if n is None or n >= len(ordered):
        rng = random.Random(seed)
        result = list(ordered)
        rng.shuffle(result)
        return result

    if not stratify:
        rng = random.Random(seed)
        rng.shuffle(ordered)
        return ordered[:n]

    cells: dict[tuple[Optional[str], str], list[LessonCandidate]] = {}
    for c in ordered:
        cells.setdefault((c.grade, c.source_language), []).append(c)
    cell_keys = sorted(cells.keys(), key=lambda k: (k[0] or "", k[1]))
    rng = random.Random(seed)
    for k in cell_keys:
        rng.shuffle(cells[k])
    cell_order = list(cell_keys)
    rng.shuffle(cell_order)

    selected: list[LessonCandidate] = []
    idx = {k: 0 for k in cell_order}
    while len(selected) < n:
        progressed = False
        for k in cell_order:
            if len(selected) >= n:
                break
            i = idx[k]
            if i < len(cells[k]):
                selected.append(cells[k][i])
                idx[k] = i + 1
                progressed = True
        if not progressed:
            break
    return selected
