# Prompt: Error Detection — spot the broken clause, type the correction

You are generating an **Error Detection** task for this lesson — one English
sentence broken into clauses/phrases, where **exactly one** block contains a
grammar error. The student finds the broken block, then **types the correction
themselves**. The system does NOT auto-reveal; producing the fix is the
load-bearing cognitive event.

Use `pattern: "grammar_sentence"`. The blocks are **clauses/phrases** of one
sentence. The sentence content itself is the English being tested and stays in
English; everything else (instructions, hint, feedback) is formal Uzbek.

## What to produce

One Error Detection task, emitted in the structured form the response schema
requests. Fill every field:

- `task_id` — short stable slug (e.g. `err_eng_g6_pastsimple_001`); optional.
- `pattern` — exactly `"grammar_sentence"`.
- `concept_ids` — the lesson grammar concept(s). Use the **source concept IDs
  from the lesson's source map when provided**; otherwise short kebab-case slugs.
  >=1.
- `grade_band` / `difficulty` — e.g. `"G5-8"`, `"medium"`; set them.
- `blocks` — **3 or more** clauses/phrases (3–4 for G1–4, 4–5 for G5–8, 5–6 for
  G9–11). Each block: `id`, `content` (an English clause/phrase), `is_error`.
  **EXACTLY ONE block has `is_error: true`** — the grammar error. The rest of the
  sentence must be correct so the slip is subtle.
- `correct_answer_for_error_block` — the corrected English clause/phrase.
- `accepted_variants` — accepted English forms (capitalisation, contraction like
  `did not` vs `didn't`) so a right answer is never rejected on form.
- `common_mistake_source` — the real, taught-pattern mistake (e.g. "present
  perfect used for a finished past time", "subject–verb agreement", "wrong past
  form of an irregular verb").
- `hint` — ONE probing hint about the rule, in Uzbek (e.g. "Harakat o'tgan yili
  tugagan — qaysi zamon mos keladi?"). It must NEVER reveal the corrected clause.
- `why_prompt` — **may be `""`** for `grammar_sentence` mechanical fixes (the
  schema allows empty here). Provide a short Uzbek WHY prompt only if the error
  genuinely needs reasoning; otherwise leave it `""`.
- `expected_reasoning_keywords` — if a WHY prompt is given, the rule terms it
  should reference (the tense/agreement rule); otherwise may be empty.
- `correct_feedback` — affirms they spotted and fixed it themselves (Uzbek).
- `wrong_correction_feedback` — **encouraging**, offers the hint. NOT "Noto'g'ri".
- `reveal_feedback` — shown only after the second wrong attempt: the corrected
  clause plus the one-line reason (Uzbek).

## Non-negotiables

- **Exactly one error.** Any other count is rejected by the validator.
- **A TAUGHT grammar pattern only.** The error must be a grammar rule the student
  has been taught this session — tense, agreement, article, preposition,
  word order. **Never a vocabulary trick, idiom, or word the student wouldn't
  know.**
- **Real mistake, not nonsense.** Use errors real learners make, not absurd
  scrambles every student spots instantly.
- **No auto-reveal.** No correct clause in the hint or any pre-reveal feedback.
- **Strip Test must pass:** without the sentence, the task is just "tap a block,
  type the fix" — no answer leakage.
- Test a pattern already shown in correct form earlier in the session.

## Language

The sentence and its corrections stay in **English** (that is the content being
tested). All surrounding student-facing text — `hint`, `why_prompt` (if any), and
all feedback — is in natural, formal Uzbek ("Siz").
