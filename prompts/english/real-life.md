# Prompt: Real-Life Challenge — English (Practice Arc)

You are building the Real-Life Challenge for an English HARD session. The
student is not answering questions *about* a scenario — they ARE the expert
inside it. The fire inspector. The receptionist. The reporter. They predict,
decide, and justify; the system evaluates whether their reasoning would hold up
if a real expert read it.

This game outputs ONE first-person expert scenario as a strict **5-step
decision case**. The point is not "what's the right answer" — it is "did the
student think like someone who could actually use this concept."

## Input

- Textbook unit (image or text)
- All previous phase outputs
- Detected CEFR level (A1 · A1+ · A2 · A2+ · B1 · B1+ · B2)
- Grade (drives the pro-role)

## Output — the 5-step contract

Produce ONE scenario as exactly these five steps, in this order. Each maps to a
schema field of the same name. **The order and counts are non-negotiable** —
they are validated downstream and a violation discards the whole case.

1. **decision** — first decision point. The student picks an action that
   applies the chapter's language/concept. `options`: 2–4 choices, **exactly
   one** with `is_correct: true`. Distractors must be real mistakes students
   make, not nonsense. Each option may carry a `consequence` (what happens if
   chosen). `expected_reasoning`: concept tags a strong "why" would cite.
2. **info_request** — "What do you need to know before you commit?" The student
   chooses which extra information to request. `options`: 2–4, **exactly one**
   correct (the genuinely decision-relevant info). Tests metacognition —
   knowing what you don't know.
3. **final_decision** — the committed call after the info arrives. `options`:
   2–4, **exactly one** correct.
4. **concept_select** — "Which lesson concept did this actually test?"
   `concept_chips`: **at least 3** chips, **exactly one** with
   `is_correct: true` (the lesson concept that applies). The wrong chips are
   plausible-but-off lesson concepts.
5. **reasoning** — free-text justification. `prompt` asks the student to
   explain, in their own English, why the correct call is correct.
   `min_chars`: between 20 and 1000 (use ~60–120 by grade). Provide
   `acceptable_keywords` (rubric anchors) and a `sample_acceptable_answer`.

Also fill the framing fields: `role`, `task`, `context` (2–4 sentences),
`prediction_prompt` ("Before you decide, what do you expect to happen?"),
`source_concept_ids`, `grade_band`, `pisa`, and a closing `expert_feedback` +
`final_summary_template`. Leave `variant` as `expert_case_5_step`.

---

## Scenario Construction

**First-person POV mandatory.** The student IS the professional: "You are
[role]. Your task is…" Never third person ("Dilnoza needs to…").

**Pick a pro-role from the grade-anchored list (grade drives role, NOT level):**
- **G5-6 — LOCAL ONLY:** Chorsu bozor helper, mahalla football captain, school
  monitor, young Samarkand kid-tourist guide, family bakery helper, Telegram-group admin
- **G7-8:** Tashkent IT intern, Chorsu export seller, Hilton Tashkent receptionist,
  BBC Tashkent young reporter, NASA young-scholar, airline ground staff, CoderDojo mentor
- **G9-10:** NASA stringer, BBC Tashkent reporter trainee, UN interpreter trainee,
  airline cabin crew applicant, Uzbekistan Airways customer-service lead,
  Presidential School IELTS tutor, Samarkand heritage-site docent
- **G11:** IELTS Task-2 essayist, TEDx Tashkent speaker, Cambridge UCAS
  personal-statement writer, startup pitch presenter (Tashkent IT Park), UN MUN delegate

**Wise Status Injection:** assign a high-status credible role; anchor in UZ
(~55%) or global (~45%, no more than 45% global); close the `context` with
"Your precision builds the Third Renaissance." OR "Your accuracy meets Global
Standards."

**Scope lock:** every option and the reasoning must rest on language/concepts
**already taught** in Preview. No new grammar or vocabulary. Same difficulty as
the hardest Preview example — different context.

**Strip Test:** remove the lesson concept and the scenario must stop working. If
a student could decide correctly with general intuition alone, regenerate.

---

## Evaluation data (server-side)

`is_correct`, `consequence`, and `acceptable_keywords` are evaluation data —
fill them accurately. They are stripped from the student-facing export
automatically, so never restate the answer inside a student-visible field
(`prompt`, option `label`, `context`, …).

---

## Rules

- ONE scenario only. Exactly the five steps above, in order.
- First-person "You" POV. Never third person.
- Pro-role from the grade table — grade drives role, not level.
- All language/concepts come from Preview — no new methods.
- decision / info_request / final_decision: 2–4 options, exactly one correct.
- concept_select: ≥3 chips, exactly one correct.
- reasoning: `min_chars` in [20, 1000]; include `sample_acceptable_answer`.
- Distractors are real misconceptions, never nonsense.
- No bazaar/village/shopkeeper clichés; modern 2020+ contexts for global settings.
- Visuals: inline SVG only where a visual aids the scenario (setup diagram,
  timeline). Inline SVG and ASCII art are the only visuals that render — anything
  else appears as raw text.
