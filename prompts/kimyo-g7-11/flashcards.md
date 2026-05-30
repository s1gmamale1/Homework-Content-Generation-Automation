# Prompt: Flash Cards — Kimyo

You are building a Flash Card deck for a Kimyo homework session. You receive the textbook page. Your job is to extract every key substance, reaction type, formula, safety rule, and Periodic Table relationship from the chapter and put them on cards.

Flash Cards are a simple reference tool. Nothing more.

## Input

- Textbook page (image or text)
- Grade: G7-11 (Kimyo)

## Output

- G7-11: **8-12 cards**

## Card format — 8 fields

Each card emits these fields:
- `id` — stable sequential `card_1, card_2, …` (never skip or reuse).
- `front` — the cue (term / question / prompt). **3–14 words.**
- `back` — the answer (definition / value / rule). **5–22 words, never over 25** (a formula or process step may run longer).
- `type` — REQUIRED. One of: `definition`, `term_to_meaning`, `formula`, `process_step`, `question_answer`, `misconception`, `image_label`.
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

Kimyo flash cards come in two modes. Both modes are used in every deck — mix them.

---

**id:** Stable sequential ID — `"card_1"`, `"card_2"`, ... starting from 1. Never skip or reuse.

### Mode A — Name → Three-Scale Description (standard)

**Front:** Substance name, compound name, or reaction type name. Short. Max 10 words.

**Back:** Three-scale description — macroscopic properties + microscopic structure description + symbolic formula or balanced equation. Then the lab sample or apparatus diagram in brackets.

Every Mode A card follows the **Three-Scale Card Format:**

```
Macro: [observable — color, state, smell, reactivity]
Micro: [particle description — ion arrangement, molecular structure, bond type]
Symbolic: [formula with valence / balanced equation with coefficient verification]
Safety: [one-line hazard note if applicable]
[Diagram: lab sample appearance OR molecule/ion structure OR lab apparatus with PPE labeled]
```

> **id:** card_1  
> **front:** Natriy xlorid (NaCl)  
> **back:** Oq kristall osh tuzi; suvda eriydi; Na⁺ va Cl⁻ ionlaridan iborat.  
> **type:** definition  
> **difficulty:** easy  
> **explanation:** Na⁺ va Cl⁻ ionlari kub panjarasida joylashgan.  
> **example:** [Diagram: white cubic crystals (macro) | Na⁺ orange spheres alternating with Cl⁻ green spheres in lattice (micro) | formula NaCl labeled]  
> *(Full three-scale back for Mode A:)*  
> Macro: oq kristall kukun, hidsiz, suvda eriydi.  
> Micro: Na⁺ va Cl⁻ ionlari kub panjarasida joylashgan.  
> Symbolic: NaCl (Na: valentligi +1, Cl: valentligi -1).  
> Safety: ko'zga tegsa — suv bilan yuving.

> **Front:** Neytrallash reaksiyasi
> **Back:**
> Macro: kislota + asos aralashtirilsa — indikator rang o'zgartiradi, issiqlik chiqadi.
> Micro: H⁺ va OH⁻ ionlari birlashib H₂O hosil qiladi.
> Symbolic: HCl + NaOH → NaCl + H₂O (balanced: H×2=H×2, O×1=O×1, Na×1=Na×1, Cl×1=Cl×1 ✓).
> Safety: kislota va ishqor — goggles va gloves majburiy.
> [Diagram: two beakers merging — one labeled "HCl (kislota)" red, one "NaOH (asos)" blue, product "NaCl + H₂O" labeled green]

> **Front:** Kislorod (O₂)
> **Back:**
> Macro: rangsiz, hidsiz, mazasiz gaz; yonishni qo'llab-quvvatlaydi.
> Micro: ikki kislorod atomi qo'sh bog' bilan birlashgan (O=O).
> Symbolic: O₂ (molekulyar kislorod). Oksidlanish darajasi 0.
> Safety: sof kislorod — yonuvchan moddalar bilan saqlash mumkin emas.
> [Diagram: two orange spheres connected by double bond (micro) | gas sample in flask (macro)]

---

### Mode B — Observable / Diagram → Substance or Reaction (visual recognition)

**Front:** A macroscopic observation or lab diagram — no substance name, no formula. Observable properties or reaction signs shown, substance identity or reaction type shown with a question mark.

**Back:** Substance name + formula + microscopic description + safety note.

> **Front:** [Diagram: clear solution turns blue with starch indicator; gas bubbles form; the gas relights a glowing splint (orange question mark on substance identity)]
> **Back:** Kislorod (O₂) — rangsiz gaz, yonishni qo'llab-quvvatlaydi. Mikro: O=O (qo'sh bog'). Formula: O₂. Safety: yonuvchan moddalardan uzoqda saqlang.

> **Front:** [Diagram: white solid dissolves in water — heat released (thermometer rises); solution turns red litmus blue (orange question mark on substance class)]
> **Back:** Ishqor (asos) — NaOH misol sifatida. Macro: oq kristall, suvda eriydi, issiqlik chiqaradi. Micro: Na⁺ va OH⁻ ionlari. Formula: NaOH. Safety: kuydirgich — goggles + gloves.

> **Front:** [Diagram: Periodic Table — Period 2, Group 17 element highlighted orange with question mark]
> **Back:** Ftor (F) — galogen, eng faol ametall. Macro: sariq gaz, o'tkir hid, juda zaharli. Micro: F₂ — ikki ftor atomi. Valentligi: -1. Safety: juda zaharli — maxsus himoya kerak.

> **Front:** [Diagram: iron nail in blue copper sulfate solution — nail turns red-brown, solution fades (orange question mark on reaction type)]
> **Back:** Siljish (almashinish) reaksiyasi — faolroq metal kamroq faol metalning tuzidan uni siqib chiqaradi. Fe + CuSO₄ → FeSO₄ + Cu. Balance: Fe×1=Fe×1, Cu×1=Cu×1, S×1=S×1, O×4=O×4 ✓.

**Mode B ratio:** at least 30% of the deck must be Mode B cards (minimum 3 of 8-12 cards). Mode B trains observation-to-formula translation — the skill tested in Panel 5 and Real-Life.

---

## Rules

- One concept per card
- Mode A: Front = name. Back = three-scale description (Macro + Micro + Symbolic + Safety) + lab diagram. Extra detail goes in the explanation/example/misconception fields.
- Mode B: Front = macroscopic observation or diagram with question mark. Back = substance/reaction name + three-scale description. Extra detail goes in the explanation/example/misconception fields.
- Every card MUST include a diagram description — showing either lab sample appearance, molecular/ion structure, or lab apparatus with PPE labeled
- Every Mode A card must include the Safety note — even if it is "no hazard for typical school lab use"
- All balanced equations on cards must be verified — equal atom counts on both sides
- NO practice problems, NO questions, NO hooks, NO stories
- Language: Uzbek, "Siz" formal
- Cover every substance, reaction type, formula, safety rule, and Periodic Table relationship the student will encounter in the homework
- Cards are returnable throughout the session — student can check them anytime
