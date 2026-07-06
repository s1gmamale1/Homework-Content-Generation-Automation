# Prompt: Reflection — {{SUBJECT}}

You are building the Reflection phase for a {{SUBJECT}} homework session. A quiet,
structured debrief — no live scoring here; you emit the *structure* the student app
fills in.

## Input

- All previous phase outputs (especially Case-Based Preview + Boss Arena)
- Grade band for this {{SUBJECT}} lesson

## Output

Respond in **Markdown only** — no JSON, no code fences around the whole output.

Begin your output with a single top-level `# ` reflection title, then the five `##`
sections below.

**Required: all five sections below in full, totaling 150–250 words.** Each section has
its own `##` heading and its own content. **Omitting any section is a failed output.**

## 1. Summary (3 sentences)

What the student learned. Include the {{SUBJECT}} concept/rule/law name, the key
formula or principle (if any), and one real-world connection.

Phrase it in the OUTPUT LANGUAGE, e.g. Uzbek "Bugun Siz [concept] ni
o'rgandingiz…", Russian «Сегодня Вы изучили [concept]…».

## 2. Strong & Weak Points

Structure the app fills in **after** the student's attempt — do NOT assert how the
student performed (there is no attempt yet when this is generated):
- **Strong-points heading** (Uzbek **"Kuchli tomonlar:"**, Russian **«Сильные
  стороны:»**): name 1–2 concepts from THIS lesson that the Case-Based Preview /
  Boss Arena treated as core — the ones a confident student should have handled.
- **Weak-points heading** (Uzbek **"Zaif tomonlar:"**, Russian **«Слабые
  стороны:»**): name 1–2 concepts from THIS lesson that are the most error-prone /
  worth re-checking (name them; do not invent a score or a result).

## 3. One Thinking Question

Pick ONE (rotate), phrased in the OUTPUT LANGUAGE. Prefer questions tied to THIS
session's performance over generic curiosity (examples shown in Uzbek):
- "Bu bobda eng qiyin tushuncha nima edi?"
- "Do'stingizga buni qanday tushuntirgan bo'lar edingiz?"
- "Kundalik hayotda buni qayerda ko'rish mumkin?"
- "Asosiy qaroringizni nega shunday qabul qildingiz?" (why did you make your main decision?)
- "Keyingi safar qaysi xatodan qochasiz?" (what mistake would you avoid next time?)

## 4. Redo Route

- Whether to **redo** the weak-point practice (the student app decides pass/redo — you
  only state the route).
- A concrete **next-step suggestion** — what to review or practice next (name the specific
  weak concept or phase to return to), not just "try again".
- Retake rule, in the output language: **"Xuddi shu tushunchalar, lekin xuddi shu
  savollar emas"** (Russian: «Те же понятия, но не те же вопросы») — same concepts,
  not the same questions.
- The student **app**, not this output, sets the score and decides pass/redo. Do NOT
  state or imply a pass/fail verdict or any retry-status label — describe the
  redo route conditionally ("if the app marks a redo, return to …") and stop there.

## 5. Closing Line

One encouraging line, unconditional (no score branch), in the OUTPUT LANGUAGE, e.g.
Uzbek "Har bir mashq miyangizni kuchaytiradi. Ertaga davom etamiz!"

## Rules

- ~2 minutes, 5 sections. Not a graded score — emit the debrief *structure* as content.
- Summary and strong/weak points must reference actual session content.

## Language

{{LANGUAGE_RULES}}
