# Prompt: Flash Cards — Biology (Biologiya G5-11)

You are building a Flash Card deck for a Biology homework session. You receive the textbook page. Your job is to extract every key term, organism name, structure name, process name, and classification term from the chapter and put them on cards.

Flash Cards are a simple reference tool. Nothing more.

## Input

- Textbook page (image or text)
- Grade: G5-7 (Biologiya) or G8-11 (Biologiya)
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

## Biology-specific content types

- **Organism names:** scientific name + common name + kingdom/phylum if relevant
- **Structure names:** organ, organelle, tissue — always include its function on the back
- **Process names:** photosynthesis, mitosis, digestion — describe what happens and what it produces
- **Classification terms:** kingdom, phylum, class, order, family, genus, species

## Diagram descriptions

Flash cards are a simple reference tool, so describe any helpful diagram as a short bracket `[Diagram: ...]` note — for example, a cell organelle, a leaf cross-section, or a food-chain arrow. Do NOT emit raw inline `<svg>` code on flash cards; the rich SVG visuals belong in the Case-Based Preview / learning panels, not crammed into a card. Skip the bracket note when a plain text description is enough.

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

> **id:** card_4  
> **front:** Mitoz  
> **back:** Somatik hujayralar bo'linishi: 1 ona hujayra → 2 bir xil qiz hujayra.  
> **type:** process_step  
> **difficulty:** hard  
> **explanation:** Bosqichlari: profaza → metafaza → anafaza → telofaza.

> **id:** card_5  
> **front:** Xloroplast  
> **back:** O'simlik hujayrasi organoidasi — fotosintez bu yerda amalga oshadi.  
> **type:** image_label  
> **difficulty:** easy  
> **example:** [Diagram: chloroplast cross-section with thylakoid membranes and stroma labeled]

## Rules

- One concept per card
- Front = name. Back = definition/function + optional bracket `[Diagram: ...]` description. Put any explanation/example/misconception in their own fields, not crammed into `back`.
- NO practice problems, NO questions, NO hooks, NO stories
- NO calculations, NO formulas — this is Biology
- Language: Uzbek, "Siz" formal
- Cover every organism, structure, process, and classification term the student will encounter in the homework
- Cards are returnable throughout the session — student can check them anytime
