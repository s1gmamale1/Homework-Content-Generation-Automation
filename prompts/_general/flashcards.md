# Prompt: Flash Cards — {{SUBJECT}}

You are building a Flash Card deck for a {{SUBJECT}} homework session. You receive the textbook page. Your job is to extract every key term, name, structure, process, rule, formula, and classification term from the chapter that matters for {{SUBJECT}} and put them on cards.

Flash Cards are a simple reference tool. Nothing more.

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
- `front` — the cue (term / question / prompt). **3–14 words.**
- `back` — the answer (definition / value / rule). **5–22 words, never over 25** (a formula or process step may run longer).
- `type` — REQUIRED. One of: `definition`, `term_to_meaning`, `process_step`, `question_answer`, `misconception`, `image_label`.
- `difficulty` — REQUIRED. One of: `easy | medium | hard`.
- `hint` (optional) — a nudge, ≤12 words, never gives away the answer.
- `explanation` (optional, encouraged) — 1 short sentence on why/how it works.
- `example` (optional, encouraged) — 1 short concrete example.
- `misconception` (optional) — 1 sentence naming a common wrong idea. **Required for trap / false-friend cards.**

Rules:
- One retrievable idea per card. Do NOT fold `explanation` / `example` / `misconception` into `back`.
- Every card MUST set `type` and `difficulty`.
- Diagrams: describe with a bracket `[Diagram: ...]` note — do NOT emit raw inline `<svg>`.

For an English (L2) lesson: the card front is the English target item (word / phrase / grammar structure); the back, hint, and explanation are the Uzbek bridge (gloss / meaning / usage note). For every other subject, both sides follow the Language rules below.

## What to put on cards for {{SUBJECT}}

Derive the card content types from this lesson's `lesson_context` and source map: cover the specific terms, names, structures, processes, rules, formulas, and classification terms that this {{SUBJECT}} chapter actually introduces. For each entry, put the cue on the `front` and its definition / value / function / rule on the `back`. Whatever a {{SUBJECT}} student must be able to recall from this chapter belongs on a card.

## Diagram descriptions

Flash cards are a simple reference tool, so describe any helpful diagram as a short bracket `[Diagram: ...]` note. Do NOT emit raw inline `<svg>` code on flash cards; the rich SVG visuals belong in the Case-Based Preview / learning panels, not crammed into a card. Skip the bracket note when a plain text description is enough.

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

- One concept per card
- Front = name. Back = definition/function + optional bracket `[Diagram: ...]` description. Put any explanation/example/misconception in their own fields, not crammed into `back`.
- NO practice problems, NO questions, NO hooks, NO stories
- Include formulas only when the {{SUBJECT}} chapter itself treats them as key facts to recall; otherwise keep cards to terms and definitions
- Cover every term, name, structure, process, rule, and classification term the {{SUBJECT}} student will encounter in the homework
- Cards are returnable throughout the session — student can check them anytime

## Language

{{LANGUAGE_RULES}}
