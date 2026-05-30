# Prompt: Flash Cards — Geometry

You are building a Flash Card deck for a Geometry homework session. You receive the textbook page. Your job is to extract every key term, theorem, and formula from the chapter and put them on cards.

Flash Cards are a simple reference tool. Nothing more.

## Input

- Textbook page (image or text)
- Grade: G7-9 (Geometriya)

## Output

- G7-9: **8-12 cards**

> **Diagram Rule:** Flash cards are a simple reference tool, so every diagram is written as a bracket `[Diagram: ...]` description using the Visual Layer notation (tick marks, arc marks, square corners, parallel arrows, color codes) — exactly like the examples below. Do NOT emit raw inline `<svg>` code on flash cards; the rich SVG visuals belong in the Case-Based Preview / learning panels, not crammed into a card. Mode B front cards show the diagram with an orange `?` on the unknown element.

## Card format — 8 fields

Each card emits these fields:
- `id` — stable sequential `card_1, card_2, …` (never skip or reuse).
- `front` — the cue (term / question / prompt). **3–14 words.**
- `back` — the answer (definition / value / rule). **5–22 words, never over 25** (a formula or process step may run longer).
- `type` — REQUIRED. One of: `definition`, `term_to_meaning`, `formula`, `process_step`, `question_answer`, `misconception`, `image_label`, `example`.
- `difficulty` — REQUIRED. One of: `easy | medium | hard`.
- `hint` (optional) — a nudge, ≤12 words, never gives away the answer.
- `explanation` (optional, encouraged) — 1 short sentence on why/how it works.
- `example` (optional, encouraged) — 1 short concrete example.
- `misconception` (optional) — 1 sentence naming a common wrong idea. **Required for trap / false-friend cards.**

Rules:
- One retrievable idea per card. Do NOT fold `explanation` / `example` / `misconception` into `back`.
- Every card MUST set `type` and `difficulty`.
- Diagrams: describe with a bracket `[Diagram: ...]` note — do NOT emit raw inline `<svg>`.

## Two card modes

Geometry flash cards come in two modes. Both modes are used in every deck — mix them.

---

**id:** Stable sequential ID — `"card_1"`, `"card_2"`, ... starting from 1. Never skip or reuse.

### Mode A — Name → Diagram (standard)

**Front:** Term name or theorem name. Short. Max 10 words.

**Back:** Definition or theorem statement. One line. Then the diagram in brackets using the Visual Layer notation standard.

> **id:** card_1  
> **front:** To'g'ri burchak (∠ = 90°)  
> **back:** 90° ga teng burchak.  
> **type:** definition  
> **difficulty:** easy  
> **example:** [Diagram: rays BA and BC, square corner symbol at vertex B]

> **id:** card_2  
> **front:** SAS tenglik belgisi  
> **back:** Agar ikki tomon va ular orasidagi burchak teng bo'lsa — uchburchaklar teng.  
> **type:** formula  
> **difficulty:** medium  
> **example:** [Diagram: triangles ABC and DEF, one tick on AB and DE (blue), arc at ∠B and ∠E (blue), one tick on BC and EF (blue)]

> **Front:** Parallel to'g'ri chiziqlar (∥)
> **Back:** Bir tekislikda kesishmaydigan ikki to'g'ri chiziq. [Diagram: two horizontal lines with single arrows, notation AB ∥ CD]

> **Front:** Uchburchak ichki burchaklari yig'indisi
> **Back:** ∠A + ∠B + ∠C = 180°. Misol: 50° + 70° + ∠C = 180° → ∠C = 60°. [Diagram: triangle ABC with all three angles marked and labeled]

---

### Mode B — Diagram → Name (visual recognition)

**Front:** A diagram description only — no label, no theorem name. Key elements marked, one element shown with a question mark.

**Back:** Theorem name or term name + the one-line definition.

> **Front:** [Diagram: two triangles, one tick on two sides, arc on included angles, all other elements grey, question mark on the relationship between triangles]
> **Back:** SAS tenglik belgisi — agar ikki tomon va ular orasidagi burchak teng bo'lsa, uchburchaklar teng.

> **Front:** [Diagram: straight line crossing two parallel lines, eight angles formed, one angle highlighted orange with a question mark, adjacent angle highlighted blue]
> **Back:** Tashqi bir tomonli burchaklar — parallel to'g'ri chiziqlar va kesuvchi hosil qilgan burchak juftlari.

> **Front:** [Diagram: triangle with two sides marked with one tick each, base angles marked with single arcs, question mark on triangle type]
> **Back:** Teng yonli uchburchak — ikki tomoni teng, asosi burchaklari ham teng.

**Mode B ratio:** at least 30% of the deck must be Mode B cards (minimum 3 of 8-12 cards). Mode B trains diagram reading — the skill tested in Panel 5 and Real-Life.

---

## Rules

- One concept per card
- Mode A: Front = name. Back = definition + diagram (Visual Layer notation). Extra detail goes in the explanation/example/misconception fields.
- Mode B: Front = diagram with question mark on the unknown. Back = theorem/term name + definition. Extra detail goes in the explanation/example/misconception fields.
- Every card MUST include a diagram description using the Visual Layer notation standard — no text-only geometry cards
- Diagram marks: tick marks for equal sides, arc marks for equal angles, square corners for right angles, arrows for parallel lines, color codes (blue=given, orange=to prove)
- NO practice problems, NO questions, NO hooks, NO stories
- Language: Uzbek, "Siz" formal
- Cover every theorem, definition, and term the student will encounter in the homework
- Cards are returnable throughout the session — student can check them anytime
