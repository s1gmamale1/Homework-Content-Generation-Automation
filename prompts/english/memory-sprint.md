# Prompt: Memory Sprint — English (Phase 1)

You are building the Memory Sprint (Phase 1) for an English homework session. Quick warm-up — tap-only, under 2 minutes. The student just finished Preview and Flash Cards. Now you activate prior-unit knowledge with fast recognition questions.

This is NOT new practice. No writing, no production, no multi-step analysis. Just: do you remember what you learned before this unit?

## Input

- Textbook unit (image or text)
- Detected CEFR level (from `classify.md`): A1 · A1+ · A2 · A2+ · B1 · B1+ · B2
- Prior unit reference (for item content)

## Output

Item count and format by CEFR level. Must use at least 2 different formats.

| Level | Item count | Format distribution |
|:-:|:-:|---|
| A1 / A1+ | 3 | 2 MC + 1 T/F |
| A2 / A2+ | 4-5 | 2 MC + 1 T/F + 1 YNNG |
| B1 / B1+ | 5-6 | 3 MC + 1 T/F + 1 YNNG |
| B2 | 7 | 3 MC + 2 T/F + 2 YNNG |

---

## 3 Formats (ONLY these — nothing else)

### Multiple Choice (MC4)
- Question + 4 options. 1 correct, 3 wrong.
- Each wrong option must reflect a real student mistake — not random:
  - Wrong tense (simple when perfect needed)
  - False friend confusion (magazine vs shop)
  - Mis-stress leading to mis-spelling
  - Partial answer (recognised form but wrong slot)

Example:
> Which sentence uses the past simple correctly?
> A) She has went to school.  B) **She went to school.** ✓  C) She go to school.  D) She goed to school.
> (A = present perfect mistake · C = no conjugation · D = regular-rule over-application)

### True / False (T/F)
- Statement about a grammar rule or vocabulary fact from the PRIOR unit.
- Must test a RULE or common MISCONCEPTION — not trivial recall.
- BAD: "'Went' is past tense of 'go' — True or False?" (trivial)
- GOOD: "In the past simple, we use 'did' for questions with all verbs including 'be' — True or False?"

### Yes / No / Not Given (YNNG)
- Statement about what the PRIOR unit states or implies.
- Student taps Yes / No / Not Given.
- "Not Given" = the chapter does not address this claim.
- Include at least 1 item with "Not Given" as the correct answer when YNNG is used.

Example:
> "According to the previous unit, a receptionist always answers calls in formal English — Yes / No / Not Given"
> Correct: Not Given. (The chapter described the role but never specified register rules.)

---

## Rules

- **Tap only.** No typing, no drag-and-drop, no fill-in-blank, no open-ended.
- **2 minutes max.** Every item answerable in ≤25 seconds.
- **Prior unit only.** No items from the current chapter.
- **No hints.** No help available in this phase.
- **No production.** If solving requires writing a sentence, it doesn't belong here.
- **Wrong answer feedback:** one line showing the gap + UZ bridge. Show WHY, not just what.
- Language: student-facing English. UZ bridge in feedback uses formal "Siz".
- Put the most important prior-unit concept as item 1 — primacy effect.
- Tags: each item `[Bloom: L1-L2 | PISA: L1]` — Sprint is recognition only, never L3+.
- Visuals: inline SVG only where it speeds recognition (stress-dot, tiny sentence diagram). Under 200×150px.
