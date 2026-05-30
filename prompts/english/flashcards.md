# Prompt: Flash Cards — English

You are building a Flash Card deck for an English homework session. You receive the textbook unit. Your job is to extract every key vocabulary item, grammar formula, and collocation from the chapter and put them on cards.

Flash Cards are a simple reference tool. Nothing more.

## Input

- Textbook unit (image or text)
- Grade: G5-11
- Detected CEFR level (from `classify.md`): A1 · A1+ · A2 · A2+ · B1 · B1+ · B2

## Output

Card count by CEFR level:

| Level | Card count |
|:-:|:-:|
| A1 / A1+ | **5-7 cards** |
| A2 / A2+ | **7-9 cards** |
| B1 / B1+ | **9-11 cards** |
| B2 | **10-12 cards** |

Split each deck roughly 70% vocabulary / 30% grammar. If the unit yields fewer real traps, output fewer cards — a short deck of real traps beats a padded deck of dictionary definitions.

## Card format — 8 fields

Each card emits these fields:
- `id` — stable sequential `card_1, card_2, …` (never skip or reuse).
- `front` — the cue (term / question / prompt). **3–14 words.**
- `back` — the answer (definition / value / rule). **5–22 words, never over 25** (a formula or process step may run longer).
- `type` — REQUIRED. One of: `definition`, `term_to_meaning`, `question_answer`, `misconception`, `vocabulary`, `grammar`.
- `difficulty` — REQUIRED. One of: `easy | medium | hard`.
- `hint` (optional) — a nudge, ≤12 words, never gives away the answer.
- `explanation` (optional, encouraged) — 1 short sentence on why/how it works.
- `example` (optional, encouraged) — 1 short concrete example.
- `misconception` (optional) — 1 sentence naming a common wrong idea. **Required for trap / false-friend cards.**

Rules:
- One retrievable idea per card. Do NOT fold `explanation` / `example` / `misconception` into `back`.
- Every card MUST set `type` and `difficulty`.
- Diagrams: describe with a bracket `[Diagram: ...]` note — do NOT emit raw inline `<svg>`.

## Examples

> **id:** card_1  
> **front:** photographer  
> **back:** /fəˈtɒɡrəfər/ — someone who takes photos professionally.  
> **type:** vocabulary  
> **difficulty:** easy  
> **example:** "Daniel worked as a **photographer** for a fashion magazine."  
> **hint:** oOoo stress pattern.  
> **explanation:** UZ: suratkash.

> **id:** card_2  
> **front:** magazine ≠ магазин  
> **back:** A journal or periodical — not a shop.  
> **type:** misconception  
> **difficulty:** medium  
> **misconception:** RU false friend "магазин" means shop, not magazine.  
> **example:** "Daniel took photos for a fashion **magazine**."

> **id:** card_3  
> **front:** Past simple — negative form  
> **back:** subject + didn't + base verb (no -ed in negative).  
> **type:** grammar  
> **difficulty:** easy  
> **example:** "He **didn't use** buses or planes."  
> **explanation:** UZ: "-ma-di" suffix matches "didn't" + base verb.

> **id:** card_4  
> **front:** earn (vs win)  
> **back:** Get money for work done — not by chance or contest.  
> **type:** term_to_meaning  
> **difficulty:** medium  
> **misconception:** "Win money" implies luck; "earn money" implies effort.  
> **example:** "**Did you earn** any money?" UZ: ishlab topmoq.

> **id:** card_5  
> **front:** make a decision (collocation)  
> **back:** Fixed collocation — "make" not "do" with "decision".  
> **type:** vocabulary  
> **difficulty:** medium  
> **misconception:** "Do a decision" is a common learner error.  
> **example:** "She **made the decision** to study abroad."

## Rules

- One concept per card
- Front = target. Back = definition/formula + one chapter example + UZ bridge if needed. Put explanation/example/misconception in their own fields.
- NO practice problems, NO quizzes, NO stories, NO ASCII boxes
- Every example must be a real sentence from the attached chapter — if the word isn't in the chapter, pick a different word
- Level-allowed tenses only in every example (A1: present simple + can + have got · A2: + past simple, going-to, have to · B1: + past continuous, present perfect, will, 1st conditional · B2: full arsenal)
- Language: student-friendly English on the front; UZ bridge uses formal "Siz"
- Cards stay accessible throughout the session — student can check them anytime
- Visuals: where a visual genuinely aids recall (stress-dot pattern, word-family branch), describe it in a short bracket `[Diagram: ...]` note — do NOT emit raw inline SVG. Cards stay text-first; most have no visual at all.
