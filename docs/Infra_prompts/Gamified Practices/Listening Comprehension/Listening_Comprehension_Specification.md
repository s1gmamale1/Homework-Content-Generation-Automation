# Listening Comprehension — Game Mechanic Specification

**New game. No predecessor spec.**
**Type:** Game (position in flow determined by homework design, not by this spec)
**Subject scope:** English first. The mechanic generalises to any listening-based language strand later, but v1 is authored for English.
**Audience:** Production team creating homework prompts and content.

---

## 1. What Listening Comprehension Is

Listening Comprehension is the first game in the catalogue whose load-bearing input is **sound, not text**. The student plays an audio clip — a conversation, an announcement, a voicemail, a short lecture — and then answers questions about what they heard.

The cognitive event is decoding **spoken** language in real time: holding meaning while the audio moves on, catching specific details, and inferring tone or intent. That is a different skill from reading, and the whole product has been missing it.

This is modelled on the **IELTS Listening** format because it is the clearest, most respected real-world standard for the skill, and because the upper grades are already on a certification track. Lower grades get a gentle, scaffolded version of the same shape; upper grades get something close to the real exam.

> **The defining rule:** the answer must be obtainable **only by listening**. If a student could answer correctly by reading a transcript on screen, this is not a listening game — it is a reading game wearing headphones. The transcript is never visible while questions are open. (See the Strip Test in §11.)

---

## 2. When Listening Comprehension Fires

Listening Comprehension is deployable wherever the homework flow places it — most naturally in the **Practice Arc** alongside the other games, but it can also seed a Case-Based Preview (see the companion spec `nets_cbp_listening_interaction_mode.md`).

What it requires to function:

- A hosted **audio asset** for the clip (uploaded or generated — see §10 and the Media Authoring spec).
- The vocabulary and structures in the audio must sit at or just below the student's current level. Listening tests comprehension, not first-exposure vocabulary. New words a student has never met should not be the thing being tested.
- For graded sessions, the audio language is **English**; the UI, instructions, and feedback are in the student's interface language (Uzbek by default — see §12).

It can run once or several times in a session. Each run is one clip with its question set.

---

## 3. The Student Experience (Flow)

Each Listening Comprehension task goes through this loop:

```
1. Pre-listening screen — context + the role/situation, and a "what to listen for" focus
   ("Siz aeroportdasiz. E'lonni tinglang va reys haqidagi ma'lumotni toping.")
2. Question preview — the student SEES the questions before audio plays (IELTS convention)
   so they know what to listen for. No answers can be entered yet.
3. Play — audio plays. A clear player: play/pause, scrub (grade-dependent), replay count shown.
4. Answer — student answers the question set: MCQ, gap-fill (type what they heard),
   matching, or short answer.
5. (Higher grades) Second play allowed or not, depending on grade band (§6).
6. Submit — answers are evaluated. Per-question feedback shown.
7. Transcript reveal — ONLY now, after submit, the transcript appears, with the
   answer-bearing lines highlighted. This is the teaching moment.
8. Optional WHY / inference prompt for higher grades on inference questions.
9. Task ends, telemetry recorded (score, replays used, time).
```

The transcript is gated behind submission at every grade. Before submit, the student has audio only.

---

## 4. Clip Types (the four IELTS-style sections)

v1 uses four clip types, mirroring the IELTS Listening sections in rising difficulty. A homework uses **a subset chosen by grade band** (§6) — not all four every time.

| # | Clip type | Register | Example |
|---|---|---|---|
| **S1** | Everyday conversation (2 speakers) | Social / transactional | Booking a class, asking directions, a phone call to a shop |
| **S2** | Everyday monologue (1 speaker) | Social / informational | A tour guide, a public announcement, a voicemail |
| **S3** | Discussion (2–4 speakers) | Educational / semi-academic | Students planning a project, a tutor and learner |
| **S4** | Lecture / talk (1 speaker) | Academic | A short explanatory talk on a topic from the lesson |

Each section has the same loop shape (§3) but rising speed, vocabulary load, and number of speakers.

### Question formats (all reuse existing answer-checking)

| Format | What the student does | Best for |
|---|---|---|
| **Multiple choice** | Pick the correct option | Gist, main idea, attitude |
| **Gap-fill / dictation** | Type the exact word(s) heard | Specific detail, spelling, numbers, names |
| **Matching** | Match speakers/items to statements | Who-said-what, categorising |
| **Short answer** | Type a 1–3 word answer | Specific factual detail |

Gap-fill and short answer accept minor spelling/format variants; the production team lists accepted variants (same discipline as Error Detection).

---

## 5. Adaptation Logic

Listening Comprehension adapts at the **task selection** level, not inside a clip.

Before launching, the system checks upstream session telemetry:

- Which listening sub-skill has the student been shaky on (gist vs detail vs inference)?
- Which clip types has the student already done recently (rotate for variety)?
- What grade band / level fits recent performance?

It then selects a task from the pool that targets a shaky sub-skill where possible, varies the clip type, and matches the grade band — or one tier up if the student has been succeeding. There is no within-clip branching; every student hears the same audio for the same task ID.

---

## 6. Difficulty by Grade Band

Same loop shape across all grades. Difficulty comes from the audio and the play rules, not from removing the listening requirement.

| Grade Band | Clip types used | Length | Speed / accent | Replays | Transcript | Question count | PISA target |
|---|---|---|---|---|---|---|---|
| **G1–4** | S1 only | 20–40 s | Slow, very clear, neutral | Unlimited | Available **after** submit, can re-listen freely | 2–3 | L1–L2 |
| **G5–8** | S1–S2 | 40–90 s | Natural-but-clear | 2 plays | After submit | 4–6 | L3–L4 |
| **G9–11** | S1–S3 (cert: all 4) | 90 s–3 min | Natural pace, light accent variety | **1 play** (IELTS-real); 2 for first-attempt only | After submit | 6–10 | L4–L6 |

**"Unit 1–2 or all 4 depending on grade"** maps directly: lower grades run one short section; middle grades run two; the **G10–11 certification strand** runs the full four-section set as a single mock-exam-style listening homework.

One audio per task, all grades. The audio must be authentic-sounding, not robotic word-by-word reading — natural intonation and pauses are part of what makes listening hard and real.

---

## 7. Scoring

Listening Comprehension uses a **100-point** model (same shape as Error Detection — the cognitive event is comprehension, scored per question).

| Criterion | Max Points | What It Measures |
|---|---|---|
| **Comprehension accuracy** | 80 | Across the question set: correct answers, weighted by question difficulty |
| **Inference / WHY (higher grades)** | 20 | On inference questions: does the explanation show the student reasoned from what was said, not guessed? |
| **Total** | **100** | |

For grades without an inference prompt, the 20 points fold into comprehension accuracy (100 max), keeping the 100 total.

### Penalties and modifiers

| Event | Effect |
|---|---|
| Extra replay used beyond the grade-band allowance (where allowance is finite) | −5 per extra replay |
| Gap-fill with a misspelling that still proves comprehension | Accept (it is a listening test, not a spelling test) unless the word IS a taught spelling target |
| No answer submitted for a question | 0 for that question, no extra penalty |

### Pass tiers

| Score | Rating | Feedback Framing (uz) |
|---|---|---|
| 85–100 | Sharp Ear | "Ajoyib! Audioni aniq tushundingiz." |
| 65–84 | Good Listener | "Yaxshi! Asosiy ma'lumotni tushundingiz, ba'zi tafsilotlarni o'tkazib yubordingiz." |
| 40–64 | Getting There | "Asosiy g'oyani tushundingiz, lekin tafsilotlarni tinglashda mashq kerak." |
| Below 40 | Hali emas | "Hali emas. Transkriptni o'qib, audioni qayta tinglaymiz." |

The 60% session pass threshold from the interactivity standard applies.

---

## 8. Replay & Transcript Behaviour (the load-bearing rules)

These two rules are what keep the game honest. They are not optional polish.

- **Replays are grade-banded and finite at the top.** Lower grades may replay freely (the goal is exposure and confidence). Upper grades get the IELTS-real single play, because handling transient audio once is the actual exam skill.
- **The transcript is never visible while questions are open.** It appears only after submit, with answer lines highlighted, as the teaching moment. Showing it earlier collapses the game into reading (fails the Strip Test).
- **A hint, where offered, never reveals the answer** — it re-points the student ("Listen again from 0:15 — what number did the speaker say?") and re-plays a segment, consistent with Boss / Error Detection hint discipline.

---

## 9. Mistake Repair Signal

If the student got a vocabulary item, fact, or concept wrong earlier in the session and then correctly answers a listening question that depends on it (without seeing the answer between attempts), that counts as a **mistake repair** event, fed to downstream session components for adaptation and the Reflection narrative — same contract as Boss, Error Detection, and Real-Life Challenge.

Listening is especially good at repair for **vocabulary and numbers/names**: hearing a word used in context after misreading it earlier is a strong reinforcement event. Production team should favour clips that re-use session vocabulary so repair opportunities exist.

---

## 10. What the Production Team Outputs Per Task

Each Listening Comprehension task in the homework content should include:

```
{
  "task_id": "listen_eng_g7_001",
  "clip_type": "S2",
  "concept_tags": ["airport_vocabulary", "future_simple"],
  "grade_band": "G5-8",
  "pisa": "L3",
  "audio_url": "https://edu.jakhongir.dev/media/audio/listen_eng_g7_001.mp3",
  "audio_meta": {
    "duration_sec": 62,
    "accent": "neutral",
    "speed": "natural-clear",
    "voice_source": "tts | human",
    "speakers": 1
  },
  "context": "Siz aeroportdasiz. E'lonni tinglang.",
  "listen_for": "Reys raqami, darvoza (gate), va kechikish vaqti.",
  "transcript": "Good afternoon passengers. Flight BA-204 to London ...",
  "questions": [
    {
      "q_id": "q1",
      "format": "multiple_choice",
      "question": "What is the flight number?",
      "options": ["BA-240", "BA-204", "BA-402", "BA-024"],
      "correct_option": 1,
      "transcript_evidence": "Flight BA-204 to London"
    },
    {
      "q_id": "q2",
      "format": "gap_fill",
      "question": "The flight is delayed by ____ minutes.",
      "answer": "40",
      "accepted_variants": ["forty", "40 minutes"],
      "transcript_evidence": "delayed by forty minutes"
    }
  ],
  "replays_allowed": 2,
  "why_prompt": null,
  "correct_feedback": "Aniq! E'londagi asosiy ma'lumotlarni to'g'ri tingladingiz.",
  "wrong_feedback": "Hali emas. Audioni qayta tinglang va raqamlarga e'tibor bering.",
  "transcript_reveal_note": "Highlight the lines that answer each question."
}
```

**Audio sourcing.** Each clip needs a real audio file. Acceptable sources, in order of preference for v1:
1. High-quality text-to-speech with natural intonation (fastest to author at scale).
2. Human-recorded audio (best for upper-grade authenticity / accent variety).
Robotic, word-by-word TTS is **not** acceptable — unnatural pacing makes the listening task artificial.

**Minimum pool size:** 8 tasks per concept tag per grade band, so adaptation (§5) has room to operate.

---

## 11. What NOT to Do

These are forbidden in Listening Comprehension:

- **Transcript visible while questions are open.** This is the cardinal sin. It turns listening into reading. Transcript appears only after submit.
- **Answers obtainable without the audio.** If general knowledge or the on-screen question text alone answers the question, it fails the **Strip Test** — remove the audio and the task must collapse to nothing. Regenerate.
- **Testing words the student has never met.** Listening tests comprehension of known language under real-time pressure, not vocabulary first-exposure. Unknown words must not be the answer key.
- **Robotic / word-by-word audio.** Natural intonation, pauses, and pace are part of the skill. Flat TTS makes the task fake.
- **Spelling failure where comprehension succeeded.** In gap-fill, a misspelling that still proves the student heard the word is accepted — unless the spelling itself is the taught target. List accepted variants.
- **Unlimited replays at the top grades.** G9–11 (especially the cert strand) must face the single-play reality; infinite replays remove the actual exam skill.
- **Forced Uzbek context that fails the swap test.** Local colour is welcome, but if swapping "Chorsu Bazaar" for "the market" doesn't change the listening task, drop the forced reference.
- **Audio with no purpose.** Background music, sound effects, or ambient audio are not Listening Comprehension. The audio must carry the answerable content.

---

## 12. Uzbek Language Guardrails

The **audio is in English** (this is an English listening game). Everything around it follows the standard guardrails:

- UI, instructions, context, `listen_for`, and feedback default to **Uzbek**, using formal **Siz**. Never `sen` / `san`.
- Keep instructions short and logical. Explain any difficult instruction term inline.
- Do not translate the audio content into the questions in a way that gives the answer away.
- For accent: lower grades use a single neutral accent; upper grades may introduce mild accent variety, but never to the point where the difficulty is the accent rather than the comprehension.
- Numbers, names, times, and spellings in the audio must be unambiguous when listened to at the grade-appropriate speed.

---

## 13. Success Criteria

Listening Comprehension is working correctly when:

- Every answer is obtainable **only** by listening — the Strip Test passes.
- The transcript is gated behind submit at every grade band.
- Replay allowances match the grade band (finite at the top).
- Audio sounds natural, at a grade-appropriate speed, and uses known language.
- Gap-fill / short-answer checking accepts comprehension-proving variants without rewarding random strings.
- Mistake repair (especially vocabulary/numbers) is being produced and is consumable downstream.
- The cert strand (G10–11) can assemble a full four-section set as a mock-exam-style listening homework.

If any of these fail, the task pool or audio needs review before that homework ships.
