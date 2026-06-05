# Prompt: Flash Cards — {{SUBJECT}}

You are building a Flash Card deck for a {{SUBJECT}} homework session. You receive the textbook page. Your job is to extract every key term, name, structure, process, rule, formula, and classification term from the chapter that matters for {{SUBJECT}} and put them on cards.

Flash Cards are a simple active-recall tool: one retrievable atom per card, studied before the Final Challenge.

## Input

- Textbook page (image or text)
- Grade band for this {{SUBJECT}} lesson
- Mode: Easy or Hard

## Output

- Easy: **5-8 cards**
- Hard: **8-12 cards**

## Card format — 8 fields

Each card emits these fields:
- `id` — stable sequential `card_1, card_2, …` (never skip or reuse).
- `front` — the cue (term / question / prompt). A bare term is ideal; **keep it short, up to ~14 words. No minimum — a 1–2 word term is a perfectly good cue.**
- `back` — the answer (definition / value / rule). **Concise; never over 25 words** (a formula or process step may run longer). **No minimum — a short, complete answer is fine.**
- `type` — REQUIRED. One of: `definition`, `term_to_meaning`, `process_step`, `question_answer`, `misconception`, `image_label`. These are the canonical core types, defined in-prompt; family-specific types may be added in the family rules below. (There is no validating schema for these — flashcards are markdown, so the type is a label you set on the card, not a JSON enum.)
- `difficulty` — REQUIRED. One of: `easy | medium | hard`.
- `hint` (optional) — a nudge, ≤12 words, never gives away the answer.
- `explanation` (optional, encouraged) — 1 short sentence on why/how it works.
- `example` (optional, encouraged) — 1 short concrete example.
- `misconception` (optional) — 1 sentence naming a common wrong idea. **Required for trap / false-friend cards.**

Rules:
- One retrievable idea per card. Do NOT fold `explanation` / `example` / `misconception` into `back`.
- Every card MUST set `type` and `difficulty`.
- Fronts are retrieval cues, never "Explain X" / "Describe Y" prompts.
- Hints never leak the answer.

For an English (L2) lesson: the card front is the English target item (word / phrase / grammar structure); the back, hint, and explanation are the Uzbek bridge (gloss / meaning / usage note). For every other subject, both sides follow the Language rules below.

## What to put on cards for {{SUBJECT}}

Derive the card content from this lesson's `lesson_context`: cover the specific terms, names, structures, processes, rules, formulas, and classification terms that this {{SUBJECT}} chapter actually introduces. For each entry, put the cue on the `front` and its definition / value / function / rule on the `back`. Whatever a {{SUBJECT}} student must be able to recall from this chapter belongs on a card.

## Atomise — never pack a whole topic into one back

One card carries one retrievable thing. If a `back` would exceed 25 words, or fold a definition + formula + caveat together, split it into multiple cards and move the supporting context into the dedicated `explanation` / `example` / `misconception` fields. The family rules below give a worked atomisation example for your subject family.

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
- Cover every term, name, structure, process, rule, and classification term the {{SUBJECT}} student will encounter in the homework.
- Cards are returnable throughout the session — student can check them anytime.

## Output format

Respond in **Markdown only** (no JSON, no code-fenced JSON). Use `#` for the phase
title and `##`/`###` for the sections/items described above, in order. For visuals:
emit inline `<svg>` for diagrams; for a photo/raster you would otherwise need to
generate, emit `![placeholder: <short description> — image gen required](placeholder)`
— never fabricate an image and never invent an image URL.

## Language

{{LANGUAGE_RULES}}
