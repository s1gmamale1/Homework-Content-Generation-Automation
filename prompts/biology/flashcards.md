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

## Card format

**Front:** Term name, structure name, organism name, or process name. Short. Max 10 words.

**Back:** Definition or function. One sentence. Where a diagram or structure helps understanding, add a short visual description in brackets OR include an SVG.

That's it.

## Biology-specific content types

- **Organism names:** scientific name + common name + kingdom/phylum if relevant
- **Structure names:** organ, organelle, tissue — always include its function on the back
- **Process names:** photosynthesis, mitosis, digestion — describe what happens and what it produces
- **Classification terms:** kingdom, phylum, class, order, family, genus, species

## SVG guidance

Include a simple inline SVG on the back ONLY when a diagram genuinely aids recognition — for example, a cell organelle, a leaf cross-section, or a food chain arrow. Keep SVGs small and clean. Skip SVG when a text description is sufficient.

SVG size limit: 200×150px.

## Examples

> **Front:** Fotosintez
> **Back:** O'simliklar quyosh energiyasi yordamida CO₂ va suvdan shakar va kislorod hosil qiladi. [Jarayon xloroplastning tilakoidlarida boradi]

> **Front:** Mitoxondriya
> **Back:** Hujayra organoidasi — ATP ko'rinishida energiya ishlab chiqaradi. "Hujayraning elektr stansiyasi" deb ataladi.

> **Front:** Fotosintez
> **Back:** Quyosh nuri → Xlorofill → CO₂ + H₂O → C₆H₁₂O₆ + O₂

> **Front:** Amyoba
> **Back:** Bir hujayrali protist; psevdopodiyalar yordamida harakat qiladi va oziq yutadi. Tip: Sarcodina.

> **Front:** Mitoz
> **Back:** Somatik hujayralar bo'linishi: 1 ona hujayra → 2 bir xil qiz hujayra. Bosqichlari: profaza → metafaza → anafaza → telofaza.

> **Front:** Xloroplast
> **Back:** O'simlik hujayrasi organoidasi — fotosintez bu yerda amalga oshadi. Ichida tilakoid membranalar va stroma mavjud.

## Rules

- One concept per card
- Front = name. Back = definition/function + optional visual description or SVG. Nothing else.
- NO practice problems, NO questions, NO explanations, NO hooks, NO stories
- NO calculations, NO formulas — this is Biology
- Language: Uzbek, "Siz" formal
- Cover every organism, structure, process, and classification term the student will encounter in the homework
- Cards are returnable throughout the session — student can check them anytime
