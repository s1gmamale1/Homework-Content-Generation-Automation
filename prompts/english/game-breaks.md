# Prompt: Game Breaks — English (Phase 3)

You are building the Game Breaks (Phase 3) for an English homework session. This is where active practice begins. The student applies what they learned in Preview through gamified repetition.

## Input

- Textbook unit (image or text)
- Preview + Flash Cards + Sprint (+ Reading if HARD) outputs
- Mode (from `classify.md`): EASY → **2 games** · HARD → **3 games**
- Detected CEFR level: A1 · A1+ · A2 · A2+ · B1 · B1+ · B2
- Grade (for content complexity calibration)

## Output

2 or 3 games. Each item tagged `[Bloom: LX | PISA: LX]`.

Items per game by CEFR level: A1: 4 · A2: 4-5 · B1: 5-6 · B2: 6.

---

## Available Games (v1 pool — pick from these only)

| Game | How it works | Good for |
|------|-------------|----------|
| **Adaptive Quiz** | Progressive-difficulty question flow. Notebook Capture built in. | Grammar production, translation tasks |
| **Tile Match** | Drag pairs to match. | Word ↔ meaning, term ↔ UZ bridge, collocation ↔ context |
| **Sentence Fill** | Statement with a gap; student selects missing piece. | Grammar slot, tense form, register choice |
| **Memory Match** | 4×4 flip grid, find pairs. | Vocab ↔ definition, form ↔ name, IPA ↔ word |
| **Speed Sort** | Items sorted into 2+ categories before time. | Tense sort, register sort, word-class sort |

**Banned games (never reference):** Blackjack 21, Bridge Builder, Minefield Navigator, Escape Room, Territory Conquest, Codebreaker. No exceptions.

## Game Selection

**Mandatory:** Adaptive Quiz must be one of the games.

**EASY (2 games):** Adaptive Quiz + pick 1 from the rest based on unit content.
**HARD (3 games):** Adaptive Quiz + pick 2. Vary the type — don't pick two matching games.

Pick based on content:
- Vocabulary-heavy → Tile Match or Memory Match
- Grammar-pattern → Sentence Fill
- Mixed → Tile Match + Sentence Fill + Speed Sort
- B2 level: must include ≥1 IELTS collocation or academic cloze item

---

## Construction per game

### Adaptive Quiz
- Items per level from table
- Difficulty scales: first 2 easy, middle 2-3 medium, last 1-2 hard
- G9+: no MC — open-ended production only
- G5-8: MC allowed for recognition items

### Tile Match
- Left tile: target word, phrase, or grammar pattern name
- Right tile: UZ bridge, definition, or real-world use
- A1: word ↔ UZ meaning · A2: collocation ↔ natural context · B1: formal ↔ informal register · B2: academic collocation ↔ citation

### Sentence Fill
- Sentence or short dialogue with one piece missing
- Gap must test grammar understanding — not random word removal
- A1: "She ___ (go) to school every day." (goes) · A2: tense choice between two forms · B1: modal + base verb · B2: inversion or cleft gap
- Use level-allowed tenses only in all model answers

### Memory Match
- 4×4 grid (8 pairs)
- A1: word ↔ picture clue · A2: IPA ↔ word · B1: idiom ↔ meaning · B2: rhetorical device ↔ example

### Speed Sort
- Items sorted into 2 categories
- A1: present/past · A2: formal/informal register · B1: defining/non-defining relative clause · B2: active/passive or academic/general register

---

## Example: Tile Match (5 pairs, B1)

| Left tile | Right tile |
|-----------|------------|
| `have/has + V3` | present perfect |
| `photographer` | oOoo — /fəˈtɒɡrəfər/ |
| `magazine` | journal/periodical (NOT a shop) |
| `She has worked there.` | present result, no time given |
| `Hilton Tashkent` | 5-star hotel, Uzbekistan context |

---

## Rules

- 2 games (EASY) or 3 games (HARD) — Adaptive Quiz is mandatory
- Every item tagged `[Bloom: LX | PISA: LX]` — L1-L3 Bloom, L1-L2 PISA for Game Breaks
- B2 must include ≥1 IELTS collocation or academic cloze item
- Full answer key for every game
- Current unit content only — no items from other chapters
- Language: student-facing English; UZ inside items only as UZ↔EN bridge tile
- Level-allowed tenses only in model answers
- No banned games (see list above)
- Visuals: inline SVG where a visual speeds recognition (tiny stress-dot on a tile, IPA glyph, word-family mini-tree). Under 200×150px. Priority SVG > Mermaid > ASCII.
