# Content-quality audit — 5 packets, Algebra + Geometry G8 (UZ) — 2026-07-01

First deep human-proxy audit of shipped packets (the R20 golden-eval precursor). Method:
per packet, one auditor read all 11 phase outputs **and the real textbook lesson pages**
(pdftotext on the source PDF), traced every question to where its concept was taught,
re-solved every math problem independently, and simulated a grade-8 student walking the
arc. Independently cross-checked by an external ChatGPT review of packet #1
(`~/Downloads/homework_review_report.txt`) — overlapping-but-different findings; both
retained below.

**Result: 5/5 FLAG.** Both providers (3 gemini, 2 claude) → the defects are
prompt/pipeline-systemic, not model-specific. The in-pipeline judge passed all five
(`judge_status=ok`) — it grades contract/fidelity, not answer-key correctness or
curriculum boundaries.

## Packets

| # | Job | Lesson (book pp.) | Gen | Flag driver |
|---|-----|-------------------|-----|-------------|
| 1 | `3ca0da6f` | Algebraik kasr. Kasrlarni qisqartirish (12–17) | gemini | Boss Q2 broken: `(y²+4y)/(y+4)` — the "wrong" cancellation coincidentally = right answer (`y`); asks for a difference at `y=1` that doesn't exist. Also (ChatGPT-caught, verified): error-detection block 6 repeats block 5's wrong result → violates the prompt's EXACTLY-ONE-broken-block rule (`practice-error-detection.md:50-54`). |
| 2 | `8f734563` | Algebraik kasrlarni qo'shish va ayirish (22–26) | claude | Wrong answer in practice-rlc (x=5 → packet says 21/100, truth 21/120=7/40); error-detection plant has TWO sign errors while the key insists on one and endorses the wrong `+1`; **English meta-preamble shipped in the deliverable** ("This is a direct content generation task… the brainstorming skill doesn't apply"); memory-check marks the textbook's own 4-step list (p.23) wrong in favor of the extract's 5-step synthesis. |
| 3 | `263d99c5` | y = k/x funksiya. Xossalari, grafigi (34–38) | claude | **Correct student graded WRONG**: packet declares Ox/Oy symmetry between `y=k/x` and `y=−k/x` mutually exclusive — false, both hold (origin symmetry composes). memory-check card 9 marks the true Oy option "xato"; boss Q3 declares one side of a two-sides-both-right debate the winner. Boss Q4 tests "asimptota" — defined nowhere in packet or lesson. |
| 4 | `9504ad94` | Parallelogramm va uning xossalari (8–10) | gemini | **Next-lesson leakage**: boss Q4 asks to justify sides-equal ⇒ parallelogram — that is the 2-alomat (p.11, NEXT lesson); this lesson teaches properties only. Hint misattributes it to "2-xossa". |
| 5 | `1122356a` | Pifagor teoremasi va uning turli isbotlari (41–43) | gemini | **Next-lesson leakage**: preview case + boss Q1 built on the CONVERSE (§18, next lesson: verify a right angle from side lengths). Boss Q1's "What" (110cm diagonal ⇒ obtuse?) requires side/angle monotonicity taught nowhere in packet OR book. |

## What held up (5/5)

- **Within-packet taught-before-asked:** memory-match pairs traced 1:1 to flashcards in
  every packet; games test taught material. The DAG's prior-phase injection works.
- **Textbook fidelity:** terminology matches the books' Uzbek terms near-verbatim;
  no invented core claims (drift was in examples/attribution, minor).
- **Arithmetic is mostly excellent:** packets #4/#5 had ~15 computations each verified
  100% correct; hints ladder properly (why → how → never the answer) almost everywhere.

## Defect taxonomy (systemic)

1. **Curriculum-boundary leakage (3/5, worst class):** the generator reaches for the
   concept's natural completion — converse theorem, recognition criteria, "asymptote" —
   which is precisely the NEXT lesson. It sees only this lesson's extract but knows the
   topic from pretraining; nothing tells it where the curriculum boundary is. Concentrates
   in case-based-preview and boss-arena hard questions.
2. **Answer-key errors (3/5, most damaging):** a correct student is graded wrong
   (#3 symmetry; #2 second sign error denied; #2 textbook's own 4-step list marked wrong).
   Teaches falsehood with authority. Invisible to the judge.
3. **Broken/unanswerable hard questions (2/5):** #1 boss Q2, #5 boss Q1-What.
4. **Language artifacts (5/5):** English template scaffolding student-visible
   (`Mode: Hard`, `Why/How/What`, `Needs Retry`, "red herring"/"qizil seld" calque);
   Cyrillic chars spliced into Latin words (3/5: `atamа`, `bajariши`, `hisoblaniб`);
   garbled *surat* variants; `Mode: Hard` violates `flashcards.md:14` ("grade, never a
   mode"); missing misconception `source`/`inferred` tags violates `flashcards.md:32,95`.
5. **Reflection fabricates outcomes (≥2/5, likely all):** pre-written "Needs Retry /
   ikkilanishlar kuzatildi" narratives before any student attempt.
6. **Extract drift (2/5):** extract mis-transcribed a textbook example (#1 `−3/(2a)` vs
   book's `−3/a`; #2 invented an Example-1 that isn't the book's) — propagated into
   flashcards/jigsaw in #2.

## Remediations → ROADMAP R21

See R21 in `docs/memory/ROADMAP.md`. Cross-check note: the external review's one real
miss was docking for a missing "Unlock Gate artifact" — that gate is app runtime logic,
never generated content. Its `74/100` flat-ranks severities; use the taxonomy above.
