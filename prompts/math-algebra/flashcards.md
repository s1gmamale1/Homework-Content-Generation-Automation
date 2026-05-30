# Prompt: Flash Cards — Math + Algebra

You are building a Flash Card deck for a Math/Algebra homework session. You receive the textbook page. Your job is to extract every key term and formula from the chapter and put them on cards.

Flash Cards are a simple reference tool. Nothing more.

## Input

- Textbook page (image or text)
- Grade: G5-6 (Matematika) or G7-9 (Algebra)

## Output

- G5-6: **5-7 cards**
- G7-9: **8-12 cards**

## Card format — 8 fields

Each card emits these fields:
- `id` — stable sequential `card_1, card_2, …` (never skip or reuse).
- `front` — the cue (term / question / prompt). **3–14 words.**
- `back` — the answer (definition / value / rule). **5–22 words, never over 25** (a formula or process step may run longer).
- `type` — REQUIRED. One of: `definition`, `term_to_meaning`, `formula`, `process_step`, `question_answer`, `misconception`, `example`.
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
> **front:** Yuza (to'g'ri to'rtburchak)  
> **back:** S = a × b — ikki tomonning ko'paytmasi.  
> **type:** formula  
> **difficulty:** easy  
> **example:** a = 5, b = 3 → S = 15 m²

> **id:** card_2  
> **front:** Diskriminant  
> **back:** D = b² − 4ac — kvadrat tenglamaning yechimlari sonini aniqlaydi.  
> **type:** formula  
> **difficulty:** medium  
> **example:** a=1, b=−5, c=6 → D = 1  
> **hint:** D > 0 bo'lsa ikki yechim, D = 0 bo'lsa bir yechim.

> **id:** card_3  
> **front:** Natural son  
> **back:** 1, 2, 3, ... kabi sanash uchun ishlatiladigan sonlar; 0 kirmaydi.  
> **type:** definition  
> **difficulty:** easy  
> **misconception:** Ba'zilar 0 ni natural son deb o'ylaydi — bu noto'g'ri.

> **id:** card_4  
> **front:** O'nliklarga ko'paytirish usuli  
> **back:** a × 20 = a × 2 × 10 — avval 2 ga, keyin 10 ga ko'paytir.  
> **type:** process_step  
> **difficulty:** medium  
> **example:** 43 × 20 = 43 × 2 × 10 = 860

## Rules

- One concept per card
- Front = name. Back = definition or formula + one example. Put explanation/example/misconception in their own fields.
- NO practice problems, NO questions, NO hooks, NO stories
- Language: Uzbek, "Siz" formal
- Cover every formula and term the student will encounter in the homework
- Cards are returnable throughout the session — student can check them anytime
