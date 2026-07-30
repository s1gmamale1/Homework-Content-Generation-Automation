# RU homework batch — content QA & learning review (2026-07-30)

12 packets: 3 per subject at each subject's **median grade** (history g8, biology g8,
math-algebra g9, geometriya g9), drawn latest-per-lesson and spread early/middle/late through
each textbook. Corpus: 857 done RU packets across 4 subjects.

Method: 4 independent subject reviewers (one per subject, same rubric, same model tier so the
subjects stay comparable) + a deterministic pass using the repo's own `content_lint`,
`toc_classifier` and structural checks. **Every reviewer finding carries a verbatim quote; all
70 quotes were machine-verified against the source packets — 70/70 EXACT, zero fabricated.**
The three Critical findings were then re-derived by hand.

## Verdicts

| | learning capability | content QA |
|---|---|---|
| STRONG | 5 | — |
| ADEQUATE | 7 | — |
| WEAK / FAILING | **0** | — |
| CLEAN | — | 0 |
| MINOR_ISSUES | — | 9 |
| SERIOUS_ISSUES | — | 3 |

No packet fails to teach its lesson. The headline risk is **3 Critical defects in 2 packets**,
all of which would be internalized as fact by a student.

## The 3 Criticals (each independently re-derived)

**1. algebra B `fff33735` — wrong answer key, quadrant signs.**
Checkpoint 2 asks the sign of x and y at **290°**. 290° is in quadrant IV → cos>0, sin<0 → option 4
(`x>0, y<0`). The packet marks **option 1 (`x>0 и y>0`)** correct — and its own feedback one line
below says *"положительную абсциссу (x>0) и отрицательную ординату (y<0)"*. **The key contradicts
its own explanation.** A student trusting the bolded key learns sine is positive in QIV — precisely
the misconception this lesson exists to prevent, delivered at the lesson's entry point.

**2. geometry B `5628b6f2` — median-length formula missing the factor ½.**
Packet drills `BD = √(2a² + 2c² − b²)`; the true formula is `m_b = ½√(2a² + 2c² − b²)`. On the
packet's own boss-arena scenario (sides 6, 10, median to side 8) it yields **14.42 instead of
7.21 — a median longer than the longest side, i.e. geometrically impossible.** The wrong literal
appears in **4 phases**: flashcards, memory-match, tictactoe, boss-arena hint.

**3. geometry B `5628b6f2` — Pythagoras/law-of-cosines relation reversed.**
Reflection states *"теорема Пифагора является обобщением теоремы косинусов"*. It is the special
case, not the generalisation. The same packet's **memory-check explicitly marks that exact
sentence as «Ошибочное суждение»** — so the closing summary teaches the misconception the quiz
just penalised.

### Root cause differs, and that decides the fix

- **Criticals 2 and 3 originate in the EXTRACT, not the generator.** The pipeline's own
  distillation contains, verbatim, `- BD = √2a² + 2c² – b² (длина медианы)` under *Formulas* and
  `- Теорема Пифагора является обобщением теоремы косинусов (в случае прямого угла)` under
  *Rules & theorems*. The generator faithfully propagated both.
  **This class is structurally invisible to the LLM judge**, which grades the packet *against the
  extract* — propagating an extract error scores as good fidelity. Same blind spot as ROADMAP R24.
- **Critical 1 is generator-origin and sits in a solver blind spot.**
  `pipeline.py:40 _SOLVER_PHASES = ("memory-check", "practice-error-detection", "practice-rlc",
  "boss-arena")` — **`case-based-preview` is not enrolled**, yet it emits keyed checkpoints.
  Measured across these 12 packets: **54 of 181 keyed answers (30%) sit outside solver coverage**,
  case-based-preview being the largest uncovered source (33 keys). That is where this Critical landed.

## Cross-cutting patterns (all 4 subjects)

1. **Graded items that test untaught facts** — every subject. bio A (Sechenov's authorship,
   Beruni's «Сайдана»), bio C (thorax shape — present only in an image caption), hist B (the 1400
   Syria campaign / sultan Faraj, in tictactoe only), hist C (building-type classifications the
   extract never supplies), geo C (`среднее пропорциональное` first appears inside the jigsaw).
   The exercise becomes a coin-flip instead of a discrimination task.
2. **The RLC distractor announces itself** — in *all three* history packets, *all three* biology
   packets, and geo C, the case text prints `(отвлекающий факт)` next to the red herring. The
   exercise exists to train filtering irrelevant data; labelling it removes the entire task.
3. **Localization is incomplete and uneven** — see below; present in 12/12.
4. **Reflection fabricates a performance verdict** before the student has answered anything, and
   repeatedly names as "weak" the material the packet drilled hardest (bio A, bio C, algebra ×3).

## Localization (deterministic, 12/12 packets)

| category | occurrences | examples |
|---|---|---|
| Directive **explicitly names** these — still leaked | **302** | `**Why**`/`**What**`/`**How**` (111), `**Correct:**`/`**Partial:**`/`**Wrong:**` (147), `## Checkpoint 1`, `## Learning Block 1` |
| Never enumerated — gap by construction | ~250 | `## Case setup`, `## Feedback summary`, `**Concepts tested:**`, `**Identify:**`, `**Decide:**`, `# Reveal` |
| **Uzbek** in Russian output | 7 | `chalg'ituvchi ma'lumot` (×6, in 6 different packets), `Tarix` |
| Machine-key carve-out — by design, but student-visible | 983 | `front`, `back`, `type`, `difficulty`, `(source)`, `(inferred)` |

**Timing rules out the easy explanation.** #114 (`f1bb195`, "RU/EN label localization → uz parity")
landed 2026-07-24 15:03 UTC; **all 12 specimens were generated after it** (earliest 20:14 UTC).
`Correct/Partial/Wrong`, `Checkpoint` and `Learning Block` were named in the directive *even before*
#114, so their presence is a failure under either prompt version — a conclusion independent of
whether workers were restarted. This is the enumerated-directive weakness: it only translates what
it names, and is not reliably obeyed even for that.

## What is healthy

- **Structure: 12/12** — all 11 phases present, zero raw `<svg>`, zero malformed visual
  placeholders, pass gate `0,60` present everywhere.
- **Launch filtering: 846/850** RU rows correctly classified as lessons.
- **Arithmetic is overwhelmingly sound** — reviewers hand-computed ~40 keyed results in algebra and
  every computation in geometry; exactly one wrong key and one wrong formula surfaced.
- **Causal teaching is real** in history (the classic fact-listing failure did *not* appear) and in
  the mechanism-heavy biology lesson (circulation), which teaches structure→function rather than labels.

## Recommendations, in order of value

1. **Enrol `case-based-preview` in `_SOLVER_PHASES`** (`pipeline.py:40`). Closes the 30% coverage
   gap where Critical 1 landed. One-line change; the solver already exists.
2. **Add a key-vs-feedback self-consistency gate**: reject when the marked-correct option
   contradicts the explanatory text beneath it. Catches Critical 1 mechanically without re-deriving
   any mathematics.
3. **Extract-fidelity guard for formulas and theorem direction.** Criticals 2 and 3 prove the extract
   can carry a false formula or a reversed implication into every downstream phase, invisibly to the
   judge. Cheapest effective form: require any formula the packet introduces to be **numerically
   verified on the packet's own example** before it may appear in a card or question — one
   substitution exposes the missing ½ instantly.
4. **Forbid grading on untaught facts**: no error-detection block, tictactoe cell or distractor may
   hinge on a name, work, date or property that no earlier phase states.
5. **Stop printing `(отвлекающий факт)` inside RLC case text.**
6. **Replace the enumerated localization directive with a generic rule** ("every student-read label
   renders in the output language; only backticked machine keys stay English"), since enumeration
   demonstrably under-covers.

## Provenance

Specimen list, per-packet reviews and the verification harness: session scratchpad
`ru_review/` (manifest.json + 12 specimen files + RUBRIC.md). Selection is reproducible from the
query in this document's method note; job IDs are recorded in `manifest.json`.
