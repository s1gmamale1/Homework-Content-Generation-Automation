# Prompt: Topic Vocabulary — {{SUBJECT}}

You are building the Topic Vocabulary phase for a {{SUBJECT}} homework session. It
is the student's FIRST content section, read BEFORE the Case-Based Preview: the
words they need to survive this lesson's theme, each explained at their exact
level. No games here, no questions, no answers to hide — a clean, warm glossary.

## Input

- The lesson extract (theme, passages, target vocabulary of THIS lesson)
- Grade band for this {{SUBJECT}} lesson

## Output

Respond in **Markdown only** — no JSON, no code fences around the whole output.

Begin with a single top-level `# ` title naming the lesson theme, then on its own
line the level badge exactly as `**CEFR:** <level>` (the level this homework is
leveled at, from the grade ladder in the Language section).

Then **8–12 entries**, numbered, each in EXACTLY this shape:

```
### 1. journey (noun)
**Definition:** a trip from one place to another, especially a long one.
**Tarjima:** sayohat — uzoq masofaga borish.
**Example:** The journey to Samarkand takes four hours.
```

- `### N. word (part of speech)` — the headword exactly as the lesson uses it
  (single word or short phrase; part of speech in English: noun, verb, adjective,
  adverb, phrase).
- `**Definition:**` — in ENGLISH, written using ONLY vocabulary at or below this
  homework's CEFR level (an A2 word gets an A2-worded definition; if a defining
  word would exceed the level, paraphrase simpler). One sentence, no synonyms-only
  definitions ("journey — a trip" alone is a failed entry; say what it IS).
- `**Tarjima:**` — the mother-tongue line: the translation, then an en-dash and a
  short native-language gloss. This line is ALWAYS present at EVERY level,
  including B1 and above — it is the one deliberate exception to any
  all-English rule. (Russian-medium sessions write this label as
  `**Перевод:**` with a Russian gloss instead.)
- `**Example:**` — one example sentence in English AT the homework's level, from
  this lesson's world (theme, characters, places), never a generic dictionary
  sentence.

## Selecting the words

- Every word comes from THIS lesson's theme: words the lesson's passages, tasks
  and games actually use or clearly need. Never import off-theme words to fill
  the count.
- Prefer: the lesson's explicit target vocabulary first, then theme-critical
  supporting words a student at this level likely does not know yet.
- No duplicates, no trivially-known words for the level (an A2+ lesson does not
  glossary "cat"), no proper names.

## Rules

- ~2 minutes of reading. 8–12 entries, nothing after the last entry.
- The `**Definition:**` / `**Tarjima:**` / `**Example:**` labels are fixed
  machine-facing keys — exactly these words, exactly this bold-colon form, one
  per line, in this order inside every entry.
- Definitions and examples obey the CEFR cap absolutely: sentence length, tense
  inventory, and vocabulary range all at or below the homework's level.
- Do not reveal or reference any other phase's answers or content structure.

## Self-check before you emit

1. ✓ Title + `**CEFR:** <level>` line present?
2. ✓ 8–12 entries, numbered, each with all three labeled lines in order?
3. ✓ Every definition uses only at-or-below-level vocabulary?
4. ✓ Tarjima line present in every entry (yes, even at B1+)?
5. ✓ Every example sentence is from this lesson's world and at level?

## Language

{{LANGUAGE_RULES}}

{{NOTATION_RULES}}
