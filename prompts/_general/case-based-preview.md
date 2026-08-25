# Prompt: Case-Based Preview — {{SUBJECT}}

You are building a **Case-Based Preview** (CBP) for a {{SUBJECT}} homework session: a
short guided learning case that turns this textbook section into a student-facing
decision situation. The student is a decision-maker — solver, observer, advisor,
writer, or analyst appropriate to {{SUBJECT}} — never a passive reader. Derive the
specific role from this lesson's content.

**Stakes:** low-to-medium. This is meaning-building, not final mastery.

## Textbook authority (NON-NEGOTIABLE)

NETS does not replace the textbook. Do NOT invent textbook facts, formulas,
definitions, dates, or lesson claims. The case may be fictional, but the lesson
concept must stay source-aligned: never change a formula, rule, fact, process,
chronology, or answer logic. If a common mistake is not stated in the textbook,
present it as an inferred typical error, not as a textbook claim.

For math/geometry lessons, source alignment includes factual verification: check
algebraic identities by expansion or substitution; cancel only common
multiplicative factors, never terms; preserve original domain restrictions for
rational expressions; do not reverse theorem implications; remember that a
square inherits both rectangle and rhombus properties; and do not reject
`(n-2)*180°` for a simple concave polygon (only self-intersecting star figures
need separate treatment).

**Claim precision:** never call a valid method "impossible" ("imkonsiz") when it is
merely less convenient for the case at hand, and never attribute a {{SUBJECT}}
convention to a fake authority (e.g. "xalqaro standartlarga mos"). State reasons
truthfully: "more convenient here", "the textbook's chosen form".

A "dragon needs algebra to open a gate" case is forbidden — strip the fantasy out
and the {{SUBJECT}} concept must still be the load-bearing reason the decision
succeeds or fails. Prefer adapting a real-life example or diagram the textbook
already gives; create a plausible case only when none exists.

## CBP canonical structure (NON-NEGOTIABLE)

Emit these sections in exactly this order:

```
0. Header              — three lines ABOVE the `#` phase title, one per line and in
                         the output language: the case type (storytelling OR
                         question-first), the student role you derived, and the
                         textbook concept the case is built on. This is where the
                         `Source concept rule` below is satisfied. It sits outside
                         the numbered sections, so it is never folded into the
                         narrative and never dropped by the heading match.
1. Case setup          — student role, narrative, clear task. Open with a real-life
                         case in ONE of the two approved shapes: **storytelling**
                         (a short concrete situation) OR **question-first** (pose
                         the hook question up front, resolve it at the end); a
                         fun-fact hook is encouraged. The narrative states
                         SYMPTOMS, not the diagnosis: describe what the student
                         observes (events, tensions, facts on the ground) WITHOUT
                         naming the underlying cause, concept, or method that the
                         checkpoints will ask them to identify.
2. Checkpoint 1        — Identify: which concept/structure/rule is involved?
3. Learning Block 1    — short, textbook-grounded explanation of the concept just identified
4. Checkpoint 2        — Decide: which method/factor/action drives the outcome?
5. Learning Block 2    — short explanation showing the method/relationship to apply
6. Checkpoint 3        — Justify or Avoid Mistake: explain why correct works or why the common mistake fails
7. Decision Process Explanation (DPE) — after Checkpoint 3, BEFORE the final simulation
8. Final simulation    — correct path + wrong path + why wrong fails
9. Feedback summary
10. Redo route         — the conditional next-step the app applies after the attempt
```

## Checkpoint rules

- **Exactly 3** checkpoints, in order: Identify → Decide → Justify-or-avoid-mistake.
- Each is a multiple-choice question — a single click, not a written answer. Keep the
  FORMAT low-friction; do NOT hollow out the thinking. Picking the right option must
  require APPLYING the lesson's rule to this case's specifics, not recognising which
  remembered rule the stem is already describing. Extended *written* reasoning is what
  belongs in the DPE rather than here.
- **Never answer the stem inside the stem.** A question must not name the rule, the
  group/category, the direction of the trend, or any condition its own options exist
  to discriminate. "Which rule applies when the atomic number increases down group
  IA?" is a failed stem — it has already stated the answer. Ask what the student must
  DECIDE ("place both samples in the table and say which is the stronger metal"), and
  let the options differ on that decision.
- Each checkpoint states its question, its answer choices, the correct choice, and
  feedback that explains why that choice fits the case.
- Each checkpoint has **3–4 options, exactly one correct**; **at least one distractor
  must be the lesson's common mistake**. Anti-leak — all three are required:
  - **Shape:** keep every option within roughly the same length. The correct option
    must never be the longest, nor the only one that supplies a mechanism or a reason.
  - **No lone absolutes:** never confine `mutlaqo` / `faqat` / `har doim` /
    `hech qachon` ("always", "never", "only") to the distractors — test-wise students
    eliminate such options on sight. Use them in none of the options, or in several.
  - **Wrong in exactly one way:** prefer distractors that get everything right but one
    thing — the correct position with the wrong conclusion, the correct conclusion
    with the wrong reason, the two given numbers swapped — over options that are
    plainly unrelated to the stem and eliminable without knowing the lesson.
- **Complete-rule feedback:** checkpoint feedback must state the lesson's rule in
  full. Never declare a partial condition "sufficient" ("kifoya") when the rule has
  more cases — e.g. for systems of equations, `a₁/a₂ = b₁/b₂` alone decides nothing;
  `c₁/c₂` must also be checked. If the lesson's common mistake is "forgetting step X",
  no checkpoint feedback may itself skip step X.

## Grade-band reasoning load

Scale the case to the lesson's grade (the pipeline supplies it in context — there is
no grade template variable, so read it from the surrounding material):
- **Lower grades** — one concrete, familiar context; obvious distractors; a short,
  guided DPE.
- **Upper grades** — layered context; subtle distractors that require *applying* the
  rule, not keyword-spotting; a fuller DPE that explicitly weighs the rejected option.

Difficulty scales the **reasoning load only** — never the numbers, formulas, dates, or
source facts, which stay exactly as the textbook states them at every grade.

## Learning Blocks (sections 3 & 5)

Two short teaching moments — call them Learning Block 1 and Learning Block 2.
- **Learning Block 1** (after Checkpoint 1): a 1–3 sentence explanation of the
  concept the student just identified, grounded in the textbook.
- **Learning Block 2** (after Checkpoint 2): a 1–3 sentence explanation that shows
  the method or relationship to apply.
- Keep them text-first and short. Describe a visual as a placeholder (see Output
  format) only if a diagram is essential and not already shown in the case — never
  emit `<svg>`; a brief `[Diagram: …]` note is preferred. This protects the budget.
- Do NOT name the governing method/formula in Learning Block 1 if Checkpoint 2 still
  expects the student to commit to it first.

## Decision Process Explanation (section 7 — non-negotiable)

After Checkpoint 3 and **before** the final simulation, present ONE open-ended
reasoning prompt — never a fourth multiple-choice question, never any answer choices.
The student writes 2–4 sentences answering all three of:

1. Which {{SUBJECT}} concept/structure did you spot in the situation?
2. Why did you pick this method over the alternatives?
3. What wrong interpretation would the common mistake have caused?

**Expected components:** concept · method · mistake. Score the answer **Full** (all
three present), **Partial** (one or two present), or **Retry** (none present) — partial
credit is allowed.

Where the family rules give subject-specific phrasings of these three questions,
use them — they re-flavor, never replace, the three components, and the scoring
above is unchanged.

**Required closing line (non-negotiable):** the DPE section MUST end with one
explicit evaluation note, written in the output language, stating that this answer
is NOT auto-passed — it is evaluated by reading the student's reasoning for the
concept, the method, and the mistake. Omitting this note violates the contract.
The note must assert this evaluation — never defer it to a human, never deny it:
"o'qituvchi tomonidan baholanadi", "avtomatik tekshirilmaydi" and equivalents in
any output language are banned. Placing the DPE AFTER the consequence is
forbidden (the student would rationalise backwards).

## Final simulation rules

Show both paths so the consequence reveals the {{SUBJECT}} content, not just a verdict:
- **Correct path** — walk through the successful outcome when the student's decision is applied.
- **Wrong path** — show what the common wrong choice produces instead.
- **Why the wrong path fails** — one required sentence on why it cannot be correct.

## Feedback summary

Close with a feedback section in exactly four parts:
1. **What the student understood** — the concept(s) they handled correctly.
2. **What mistake appeared** — the error seen across checkpoints and the DPE (if any).
3. **What to review** — the specific textbook point to revisit.
4. **Completion status** — describe the redo route the student **app** applies
   AFTER the attempt (the app owns pass/redo; there is no attempt yet at
   generation). State it conditionally ("if the app marks a redo, return to …")
   — never emit a decided status such as a bare pass label or "Not Completed".

## Source concept rule

Every checkpoint, learning block, and the simulation must trace to a real concept in
THIS lesson. Do not invent concepts. Name the case type and student role you derived,
and note which textbook concept the case is built on — in the Header block (section 0
of the canonical structure), never folded into the case narrative.

## Visual & case framing (family-specific)

{{FAMILY_RULES}}

## Language

{{LANGUAGE_RULES}}

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##` — one level, never `###` — for the ten sections described above, in
order. Deeper levels are reserved for content *inside* a section, never for restating
that section's own title.

**Machine-readable structure (non-negotiable)** — the platform importer matches
section headings, so heading shape is a contract:

- **One heading per section. Level `##`. Numbered `1.`–`10.`. Output language only.**
  The leading number is the importer's primary anchor, so it is never optional. Never
  restate a heading immediately beneath itself (no `##` followed by a `###` saying the
  same thing, no `**Bold label:**` echoing the heading it sits under), and never
  append an English gloss to an output-language heading — `## 2. Nazorat nuqtasi 1 —
  Aniqlash (Checkpoint 1 — Identify)` is wrong twice over. English keywords are NOT
  required anywhere in a heading: the student reads these, so they follow the output
  language like every other student-facing string.
- **Heading keywords** — after the number, the heading must contain, for Uzbek output:

  | # | Section | Heading contains |
  |---|---|---|
  | 1 | Case setup | `Vaziyat` |
  | 2 | Checkpoint 1 | `Nazorat nuqtasi 1 (Checkpoint 1)` and `Aniqlash` |
  | 3 | Learning Block 1 | `O'quv bloki 1` |
  | 4 | Checkpoint 2 | `Nazorat nuqtasi 2 (Checkpoint 2)` and `Qaror` |
  | 5 | Learning Block 2 | `O'quv bloki 2` |
  | 6 | Checkpoint 3 | `Nazorat nuqtasi 3 (Checkpoint 3)` and `Asoslash` |
  | 7 | DPE | `Qaror qabul qilish jarayoni` |
  | 8 | Final simulation | `Yakuniy simulyatsiya` |
  | 9 | Feedback summary | `Fikr-mulohaza` |
  | 10 | Redo route | `Qayta urinish` |

  For `en`/`ru` output use that language's natural equivalent of the same label and
  keep the number; the parenthetical `(Checkpoint N)` in the checkpoint headings is a
  machine-parsed key — keep it verbatim in every non-English output (in English
  output the heading is simply `Checkpoint N — …`). All ten need their heading — a
  section whose heading is missing or unnumbered is dropped from the student-facing
  story.
- Checkpoint options are lettered lines `A) <option>` … (3–4 of them, one per line).
  After the options, name the correct one on its own line: `**To'g'ri javob:**
  <letter>` (stripped before the student sees the item). This label is a
  machine-parsed key: EXACTLY `**To'g'ri javob:**` at every level and in every
  output language — including B1+ all-English lessons — never "Correct answer" or
  any translation. The three checkpoints must not all share one correct letter —
  vary the position of the correct option across them. Checkpoint feedback goes
  AFTER the options, never between the question and the options.
- **Plain text — no math markup.** Never wrap anything in `$…$`, `\(…\)` or `\[…\]`:
  there is no math renderer downstream, so the student literally sees the dollar
  signs. Write `Z = 11`, `Ar = 23`, `a1/a2 = b1/b2` as ordinary text.

**Visuals.** Do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram
OR photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must be self-sufficient: name the medium and every label/value/axis
so the visual can be produced from the text alone. Never output raw `<svg>`, never
fabricate an image, never invent an image URL.

**Setup-visual no-spoiler rule (overrides label-completeness for the setup visual
only):** the case setup's visual placeholder is PART of the setup — self-check #6
applies to its caption. Never depict or name the cause→effect chain, the concept, the
method, or any answer a checkpoint tests. If describing the visual completely would
reveal a checkpoint answer, depict less instead.

**What the setup visual MAY show — do not over-apply the rule above.** It may depict
any datum the setup text already states in words: the measured values, the readings,
the objects on the bench, the symptoms observed. Restating GIVEN data in visual form
is not a spoiler; only the INFERENCE drawn from it is. So prefer a labelled diagram of
the given data over a decorative scene photo — a photo that merely sets a mood teaches
nothing and spends the image budget doing it. Choose a photo only when the case really
turns on a real-world appearance the student has to look at.

## Self-check

1. ✓ Exactly 3 checkpoints, in order Identify → Decide → Justify/Avoid-mistake?
2. ✓ Both Learning Block 1 and Learning Block 2 present and short?
3. ✓ DPE is open-ended with no answer choices?
4. ✓ DPE placed after Checkpoint 3 and before the final simulation?
5. ✓ Final simulation shows correct path, wrong path, and why wrong fails?
6. ✓ {{SUBJECT}} concept/method NOT named in the setup or checkpoints — including
   the setup visual's caption (it must not depict the tested cause→effect or concept)?
7. ✓ Every concept traces to this lesson; no invented textbook facts?
8. ✓ Visuals follow the family policy above (right medium, no fabricated URLs)?
9. ✓ DPE ends with the required not-auto-passed evaluation note?
10. ✓ Each checkpoint has 3–4 same-shape options, exactly one correct, with ≥1 distractor being the common mistake (anti-leak)?
11. ✓ DPE names its expected components (concept · method · mistake) and a Full/Partial/Retry score?
12. ✓ Feedback summary has all four parts, and the completion part describes a
    CONDITIONAL app-owned redo route (no decided pass/fail label)?
13. ✓ No checkpoint feedback declares a partial condition "sufficient" when the rule has more cases; no "impossible" claims for merely-inconvenient methods; no fake authorities?
14. ✓ Header block (case type · student role · textbook concept) present above the phase title?
15. ✓ Exactly ONE heading per section — `##`, numbered `1.`–`10.`, output language only, no English gloss, no `###` or bold restatement underneath?
16. ✓ All ten sections carry their heading keyword, so none is dropped on import?
17. ✓ No `$…$` or other math markup anywhere in the output?
18. ✓ No checkpoint stem names the rule, group, or trend direction its own options exist to discriminate?
19. ✓ Correct option is not the longest and not the only one giving a reason; no absolutes confined to distractors?
20. ✓ Setup visual depicts the GIVEN data (labelled diagram preferred over a mood photo) without depicting the inference?
21. ✓ Closing note asserts the evaluation — no deferral to a human, no denial?
22. ✓ Family case-shape and distractor-dimension requirements for this subject met?

{{NOTATION_RULES}}
