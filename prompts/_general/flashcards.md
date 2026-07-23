# Prompt: Flash Cards — {{SUBJECT}}

You are building a Flash Card deck for a {{SUBJECT}} homework session. You receive the textbook page. Your job is to distill the chapter's key terms, names, structures, processes, rules, formulas, and classification terms into a deck sized to the grade band (below) — the highest-value atoms first.

Flash Cards are a simple active-recall tool: one retrievable atom per card, studied before the Final Challenge.

## Input

- Textbook page (image or text)
- Grade band for this {{SUBJECT}} lesson

## Output — deck size by grade band

The pipeline injects the grade, never a mode. Size the deck to the grade band:
- **G5-6 → 6-8 cards** — core atoms only, plainest wording.
- **G7-8 → 8-10 cards** — core atoms plus one `misconception` card.
- **G9-11 → 10-12 cards** — the full atom set, including subtler distinctions.

Grade scales the retrieval load, never source accuracy.

## Card format — 8 fields

Each card emits these fields:
- `id` — stable sequential `card_1, card_2, …` (never skip or reuse).
- `front` — the cue (term / question / prompt). A bare term is ideal; **keep it short, up to ~14 words. No minimum — a 1–2 word term is a perfectly good cue.**
- `back` — the answer (definition / value / rule). **Concise; never over 25 words** (a formula or process step may run longer). **No minimum — a short, complete answer is fine.**
- `type` — REQUIRED. Core types: `definition`, `term_to_meaning`, `process_step`, `question_answer`, `misconception`, `image_label`. Add `formula` when the lesson is mathematical or scientific (equations, laws); add `grammar` and `vocabulary` when the lesson is a language lesson (`grammar` = pattern → rule, `vocabulary` = L2 word → L1 meaning). These are the canonical types, defined in-prompt; the family rules below say which extensions apply to your subject. (There is no validating schema for these — flashcards are markdown, so the type is a label you set on the card, not a JSON enum.)
- `difficulty` — REQUIRED. One of: `easy | medium | hard`.
- `hint` (optional) — a nudge, ≤12 words, never gives away the answer.
- `explanation` (optional, encouraged) — 1 short sentence on why/how it works.
- `example` (optional, encouraged) — 1 short concrete example.
- `misconception` (optional) — 1 sentence naming a common wrong idea. **Required for trap / false-friend cards.** Mark its provenance: `source` when the textbook itself states the mistake, `inferred` when you derived it. NEVER present an `inferred` misconception as a textbook-stated fact.

Rules:
- One retrievable idea per card. Do NOT fold `explanation` / `example` / `misconception` into `back`.
- Every card MUST set `type` and `difficulty`.
- Fronts are retrieval cues, never "Explain X" / "Describe Y" prompts.
- Hints never leak the answer.
- For math/geometry lessons, run a factual sanity check before finishing:
  verify algebraic identities by expansion or substitution; cancel only common
  multiplicative factors, never terms; preserve original domain restrictions for
  rational expressions; do not reverse theorem implications; remember that a
  square inherits both rectangle and rhombus properties; and do not reject
  `(n-2)*180°` for a simple concave polygon (only self-intersecting star figures
  need separate treatment).

For an English (L2) lesson: the card front is the English target item (word / phrase / grammar structure); the back, hint, and explanation are the Uzbek bridge (gloss / meaning / usage note). For every other subject, both sides follow the Language rules below.

## What to put on cards for {{SUBJECT}}

Derive the card content from this lesson's `lesson_context`: cover the specific terms, names, structures, processes, rules, formulas, and classification terms that this {{SUBJECT}} chapter actually introduces. For each entry, put the cue on the `front` and its definition / value / function / rule on the `back`. Whatever a {{SUBJECT}} student must be able to recall from this chapter belongs in the deck, subject to the deck-size budget below.

## Atomise — never pack a whole topic into one back

One card carries one retrievable thing. If a `back` would exceed 25 words, or fold a definition + formula + caveat together, split it into multiple cards and move the supporting context into the dedicated `explanation` / `example` / `misconception` fields. The family rules below give a worked atomisation example for your subject family.

## Sub-skill spread & scope window

Before writing cards, enumerate the section's **sub-skills** (e.g. for standard form
of monomials: identify what is/isn't a monomial · reorder factors · combine
coefficients · combine like variables into powers · read off the coefficient ·
evaluate at given values). Then:

- **Spread:** the deck must cover **at least 3 distinct sub-skills** when the lesson
  teaches that many. No single micro-skill (e.g. "multiply coefficients, don't add")
  may drive more than about a third of the cards — repeating one drill teaches less
  than varied retrieval. **Escape hatch:** if the lesson genuinely teaches fewer than
  3 sub-skills, vary the cue formats and example/number types instead — NEVER import
  adjacent-section material to hit the count.
- **Variety:** for quantitative lessons, vary the number types across cards as the
  grade allows — integers, negatives, and (from grade 6–7 up) simple fractions and
  decimals. A deck where every value is a small positive integer under-prepares the
  student for the textbook's own exercises.
- **Scope window (anti-overload):** teach and test ONLY this section's core concept
  chain. Adjacent-section techniques (e.g. the discriminant next to Viet's theorem,
  rational-expression simplification next to factoring) may be *named* in an
  `explanation` field as contrast, but never taught-and-tested as their own cards.
  Test: if a concept would need its own learning block for the card to be fair, it
  is out of scope — cut it.

## Card types & visuals (family-specific)

{{FAMILY_RULES}}

## Examples

> **id:** card_1  
> **front:** Fotosintez  
> **back:** O'simliklar quyosh energiyasi yordamida CO₂ va suvdan shakar va kislorod hosil qiladi.  
> **type:** process_step  
> **difficulty:** medium  
> **explanation:** Jarayon xloroplastning tilakoidlarida boradi.  
> **example:** CO₂ + H₂O → C₆H₁₂O₆ + O₂

> **id:** card_2  
> **front:** Mitoxondriya  
> **back:** Hujayra organoidasi — ATP ko'rinishida energiya ishlab chiqaradi.  
> **type:** definition  
> **difficulty:** easy  
> **hint:** "Hujayraning elektr stansiyasi" deb ataladi.

> **id:** card_3  
> **front:** Amyoba  
> **back:** Bir hujayrali protist; psevdopodiyalar yordamida harakat qiladi va oziq yutadi.  
> **type:** term_to_meaning  
> **difficulty:** medium  
> **example:** Tip: Sarcodina.

(The examples above illustrate the card *shape* only — the actual cards must be drawn from this {{SUBJECT}} lesson's own content, not from these.)

## Rules

- One concept per card.
- Front = cue. Back = definition/function/value. Put any explanation/example/misconception in their own fields, not crammed into `back`.
- NO practice problems, NO multi-step scenarios, NO hooks, NO stories — scenarios belong in the Case-Based Preview, not on a flashcard.
- Include formulas only when the {{SUBJECT}} chapter itself treats them as key facts to recall; otherwise keep cards to terms and definitions.
- Within the deck-size budget, cover the most load-bearing terms, names, structures,
  processes, rules, and classification terms of THIS lesson — the ones the
  {{SUBJECT}} student must recall to work the rest of the homework. **Deck size wins
  over exhaustive coverage**: when the lesson holds more atoms than the grade band
  allows, choose by learning value; the remaining terms are carried by Memory Check
  and the games, never by inflating the deck past its band.
- Cards are returnable throughout the session — student can check them anytime.
- **Claim precision:** never call a valid method "impossible" when it is merely less
  convenient, and never attribute a {{SUBJECT}} convention to a fake authority
  (e.g. "xalqaro standartlarga mos" for standard form). State reasons truthfully:
  "more convenient here", "the textbook's chosen form".
- Self-check before finishing: every card sets `type` + `difficulty`, no `back` packs an un-atomised topic, every `misconception` card is tagged `source` or `inferred` (never an inferred mistake presented as a textbook fact), the deck covers ≥3 sub-skills (or uses the narrow-lesson escape hatch), no micro-skill exceeds ~1/3 of cards, and no adjacent-section technique is taught-and-tested.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals: do NOT emit `<svg>` or any image/HTML markup. For ANY visual (diagram
OR photo), emit a described placeholder instead — never the visual itself:
`![visual: <diagram|photo> — <what to depict, with every label, value, and axis> — image gen required](placeholder)`
The description must be self-sufficient: name the medium and every label/value/axis
so the visual can be produced from the text alone. Never output raw `<svg>`, never
fabricate an image, never invent an image URL.

## Language

{{LANGUAGE_RULES}}
