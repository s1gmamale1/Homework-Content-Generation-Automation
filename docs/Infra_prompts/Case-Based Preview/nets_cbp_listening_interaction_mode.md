# Case-Based Preview Interaction Mode: English Listening Case — v1

> **Aligned version:** This mode follows the NETS Case-Based Preview standard (`nets_case_based_preview_generation_standard_v1.md`) and the languages prompt (`nets_cbp_prompt_languages.md`).
> **Core shape (unchanged from the standard):** exactly **three MCQ checkpoints**, then a separate open-ended **Decision Process Explanation (DPE)** before the final consequence.
> **What is new:** the case material the student reasons from is an **audio clip**, not a text passage. The student must listen before they can act.

---

## 1. Purpose

The Listening Case turns a real-life spoken situation — a phone call, an announcement, a conversation, a voicemail — into a Case-Based Preview. The student plays the audio, takes on a role, and must **identify** what was said, **decide** the right response, and **justify** it, then explain the decision before seeing the consequence.

This brings the listening skill into the *preview* moment (first contact with the lesson), not only into the Practice Arc. It is the right mode when the lesson concept naturally lives in **spoken interaction** — understanding instructions, handling a request, catching key details in speech.

This is not a reading case with audio attached. **The case cannot be solved without listening** (see Forbids).

---

## 2. Inputs

```json
{
  "subject": "language (English)",
  "grade": "<grade number>",
  "language": "uz | ru | en  (interface language; audio is English)",
  "textbook": "<filename>",
  "chapter": "<chapter name>",
  "section": "<section name>",
  "page_range": "<start-end>",
  "source_text": "<optional extracted text the audio is built from>",
  "flashcards_terms": ["<optional canonical lesson terms>"],
  "audio_situation": "phone call | announcement | conversation | voicemail | instructions"
}
```

---

## 3. Source Extraction

Extract only source-supported listening logic:

- Topic and core concept (the lesson point the spoken situation exercises)
- The real-life situation that makes listening necessary
- Key spoken details the student must catch (a number, a name, a time, a request, an intent)
- Key terms, matching Flashcards when provided
- The correct interpretation of what was said (the meaning that must be preserved)
- A tempting **mishearing / misunderstanding** — what a student plausibly gets wrong (caught a wrong detail, missed the intent, reversed who-wants-what)
  - provenance: `source` if stated, `inferred` if generated from likely listener confusion
- Required skill: listen once or twice, hold the meaning, and act on it correctly

The case must fail because of a **listening** misunderstanding tied to the lesson, not because of an obscure word the student could never know.

---

## 4. Usable Skill

The student should be able to say:

> I can listen to a real spoken situation, catch the details that matter, and decide the right response.

---

## 5. Case Rules

### Student role

The student takes a role that has to **act on what they hear**: a receptionist taking a message, a traveller following an announcement, a friend helping with a plan, a shop assistant handling a phone order, a student following a teacher's instructions.

### Audio rules

- The clip is **English**, grade-appropriate in speed and vocabulary (see the Listening Comprehension spec §6 for bands).
- 15–60 seconds for a preview case (shorter than a full Practice-Arc listening task — the preview is one focused situation).
- Natural intonation and pacing. No robotic reading.
- **Replays:** allowed during the case (the preview is about learning, not testing) — but the **transcript stays hidden until the final consequence**.

### Checkpoint rules (the three MCQs)

1. **Identify** — "What did you hear?" Tests whether the student caught the key detail / intent.
2. **Decide** — "What should you do / say next?" Tests the right response given what was said.
3. **Justify / Avoid mistake** — "Which explanation best shows why that response is right?"

Each checkpoint's distractors must be **real mishearings**, not nonsense — the wrong detail, the reversed intent, the right words but wrong action.

---

## 6. Visual Rules

The Listening Case is **audio-first and low-asset**.

| Visual | Type | Used in | Purpose |
|---|---|---|---|
| Audio player | Player | Case setup + Checkpoint 1 | Play / pause / replay the clip; show replay count |
| Situation card | Text / CSS | Case setup | The role and the spoken situation, no spoilers |
| Response chips | CSS / text | Checkpoint 2 | The candidate responses |
| Transcript panel | Text | Final simulation only | Revealed after the case, with key lines highlighted |

No transcript before the final consequence.

---

## 7. Required Output Format

```markdown
# Case-Based Preview: [Title]

## Metadata
- Subject: English (Listening)
- Grade:
- Topic:
- Textbook address:
- Source concept:
- Required skill: Listen to a real spoken situation, catch what matters, and respond correctly
- Case type: Listening case
- Student role:
- audio_situation: phone call | announcement | conversation | voicemail | instructions

## Source Extraction
- Core concept:
- Key spoken details to catch:
- Key terms (must match Flashcards):
- Correct interpretation of the audio:
- Tempting mishearing / misunderstanding:
  - text:
  - provenance: source | inferred
- Textbook basis used:

## Audio
- audio_url: https://edu.jakhongir.dev/media/audio/[id].mp3
- duration_sec:
- accent: neutral
- speed: slow-clear | natural-clear
- voice_source: tts | human
- transcript: "[full English transcript — hidden from student until final consequence]"

## Visual Plan
| Visual | Type | Used in | Purpose |
|---|---|---|---|
| Audio player | player | Case setup + Checkpoint 1 | Play / replay clip |
| Situation card | text/css | Case setup | Role + situation, no spoilers |
| Response chips | css/text | Checkpoint 2 | Candidate responses |
| Transcript panel | text | Final simulation | Revealed after the case |

## Student View

### Case Setup
[2–4 sentence role-based situation. The student must need to LISTEN to proceed.]

▶ [Audio plays here — student listens before answering]

### Checkpoint 1: Identify (what did you hear?)
- Question:
- Options (MCQ):
  - A. [Correct detail / intent]
  - B. [Plausible mishearing — wrong number/name/time]
  - C. [Reversed intent — who wants what]
  - D. [Irrelevant detail]
- Correct answer:
- Feedback:

### Learning Block 1
[Explain that the student first had to catch the spoken meaning. Replays are fine; guessing is not.]

### Checkpoint 2: Decide (what should you do / say?)
- Question:
- Options (MCQ):
  - A. [Correct response to what was actually said]
  - B. [Response to the mishearing]
  - C. [Right words, wrong action]
  - D. [Irrelevant or rude/wrong-register response]
- Correct answer:
- Feedback:

### Learning Block 2
[Explain why the correct response follows from what was heard.]

### Checkpoint 3: Justify / Avoid Mistake
- Question: Which explanation best shows why that response is right?
- Options (MCQ):
  - A. [Correct explanation tying the heard detail to the response]
  - B. [Fluent but based on a mishearing]
  - C. [Right action, wrong reason]
  - D. [Unsupported explanation]
- Correct answer:
- Feedback:

### Decision Process Explanation
- Prompt: "Walk through your reasoning — (1) what was the key thing you heard, (2) why is your response the right one, (3) what mistake happens if someone mishears it?"
- Expected components: heard detail · correct response · the mishearing risk
- Pass condition: Student references all three in 2–4 sentences
- Sample acceptable answer:
- AI evaluation rubric:
  - Full: states the heard detail, the correct response, and the mishearing risk
  - Partial: correct response but explanation misses the heard detail or the risk
  - Retry: bases the answer on a mishearing, or skips the explanation

### Final Simulation / Consequence
- Correct path: The student responded to what was actually said — situation resolves well.
- Wrong path: The student acted on a mishearing — show the believable bad outcome.

Transcript panel (revealed now):

```txt
[Full English transcript, with the answer-bearing lines highlighted]
```

### AI Feedback Summary
- What student understood:
- What mistake appeared:
- What to review:
- Completion status: passed | Needs Retry
```

---

## 8. Completion Rules

- **Pass:** Student catches the key spoken detail, chooses the response that fits what was said, and explains the decision before the consequence.
- **Needs Retry:** Student acts on a mishearing, guesses without listening, picks right-words/wrong-action, or skips the explanation.

---

## 9. Forbids

1. Do not show the transcript before the final consequence. **This is the cardinal rule** — it turns the case into reading.
2. Do not build a case that can be solved from the on-screen text alone. Remove the audio and the case must collapse (Strip Test).
3. Do not test obscure words a student could never know. The misunderstanding must be a real, lesson-relevant mishearing.
4. Do not use robotic, word-by-word audio. Natural pacing is part of the skill.
5. Do not turn Checkpoint 3 into the open-ended explanation. Checkpoint 3 stays MCQ.
6. Do not place the Decision Process Explanation after the consequence.
7. Do not auto-pass the explanation.
8. Do not use distractors that are all defensible. Mishearings must be genuinely wrong given the audio.
9. Do not use terminology that conflicts with Flashcards.
10. Do not force a local reference that fails the swap test.

---

## 10. Uzbek Language Guardrails

When `language = uz` (interface):

- The **audio is English**; all instructions, the situation card, prompts, and feedback are in formal Uzbek (**Siz**). Never `sen` / `san`.
- Do not translate the audio content into the question stems in a way that hands over the answer.
- Keep instructions short and logical; explain any difficult instruction term inline.
- Preserve names, numbers, and times exactly as spoken.
- Slow-clear audio for lower grades; natural-clear for upper grades.

---

## 11. Self-Check

```txt
[ ] Source concept tied to a real spoken situation
[ ] Audio is English, grade-appropriate speed, natural intonation
[ ] Case CANNOT be solved without listening (Strip Test passes)
[ ] Transcript hidden until final consequence
[ ] Tempting mishearing is a real, lesson-relevant misunderstanding
[ ] Exactly 3 MCQ checkpoints (Identify · Decide · Justify)
[ ] Checkpoint 3 is MCQ, not written reasoning
[ ] Decision Process Explanation included after C3, before consequence
[ ] DPE asks for heard detail · correct response · mishearing risk
[ ] Final consequence shows correct path and wrong (mishearing) path
[ ] Transcript revealed with answer lines highlighted at the end
[ ] Key terms align with Flashcards
[ ] Mishearing provenance marked source | inferred
[ ] Uzbek guardrails followed when interface language = uz
```

If any line fails, regenerate.
