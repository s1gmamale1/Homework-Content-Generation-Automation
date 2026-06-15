# Listening & Video Integration — Index

**What this folder is.** The complete spec bundle for bringing **listening practice** and **video / extra materials** into NETS homework — plus the **media-upload** capability the builder needs to support them. It gathers, in one place, every document that defines *how these features should work* for the production team and builder devs.

These are **product / pedagogy specs in the house style** of the other `Infra_prompts` game specs (Error Detection, Real-Life Challenge, the Case-Based Preview prompts) — descriptions of how each thing should behave, not code or architecture.

---

## Why this exists

English homework had **no listening practice** — a real gap for a language product, and a hard requirement for the certification (IELTS-style) track. At the same time the dev team flagged that the new builder lost the **image / audio / video upload** the old builder had, and that old media gets dropped on migration.

These are one initiative: listening and video need media; the builder needs media upload back; the migration data-loss must be fixed. This bundle specs all of it.

---

## Decisions baked into these specs

- **Scope:** build all three surfaces — a **Listening game**, **audio inside Case-Based Preview**, and a separate **Extra Materials / video box**.
- **Media upload:** add full **image + audio + video** upload to the new builder, and **fix the migration data-loss** (media must round-trip).
- **Depth:** **grade-banded** — short clips + simple Q/A for lower grades; up to full IELTS-style multi-section listening for the upper / certification grades.
- **Reuse, don't reinvent:** listening reuses the existing game + scoring machinery; media reuses the existing upload pipeline (`edu.jakhongir.dev/media/...`).

---

## The files

| File | What it specifies | Canonical home |
|---|---|---|
| **`Listening_Comprehension_Specification.md`** | The new **Listening game** for the Practice Arc: audio clip → IELTS-style questions (MCQ / gap-fill / matching / short answer), gated transcript, grade-banded play rules, 100-pt scoring, mistake-repair. | `Gamified Practices/Listening Comprehension/` *(copy here)* |
| **`nets_cbp_listening_interaction_mode.md`** | **Audio inside Case-Based Preview**: a listening case (call / announcement / conversation) driving the standard 3-checkpoint + Decision-Process-Explanation CBP shape. | `Case-Based Preview/` *(copy here)* |
| **`Video_And_Extra_Materials_Specification.md`** | The separate, **ungraded** "Qo'shimcha materiallar" surface — extra videos (YouTube + uploaded), audio, PDFs, links — optional and non-gating. | this folder |
| **`Media_Authoring_And_Upload.md`** | The dev team's request answered: **where** image/audio/video upload goes in the builder, reuse of the existing pipeline, and the **migration rule** so old media stops disappearing. Includes an Uzbek summary for the dev team. | this folder |

> The Listening game and CBP listening mode live canonically in their own family folders (so they sit next to their siblings); identical copies are kept here so this bundle is self-contained. If you edit one, update both.

---

## How the pieces map to a homework

```
Homework
 ├─ Case-Based Preview        → can be a LISTENING CASE   (nets_cbp_listening_interaction_mode)
 ├─ Flashcards + Memory       → (images via media upload)
 ├─ Practice Arc (games)      → + LISTENING COMPREHENSION  (Listening_Comprehension_Specification)
 ├─ Reflection
 └─ 📺 Qo'shimcha materiallar  → EXTRA MATERIALS / VIDEO    (Video_And_Extra_Materials_Specification)

 builder (authoring)          → MEDIA UPLOAD everywhere     (Media_Authoring_And_Upload)
```

---

## Reading order

1. **`Media_Authoring_And_Upload.md`** — the foundation; everything else needs media.
2. **`Listening_Comprehension_Specification.md`** — the headline feature (the listening game).
3. **`nets_cbp_listening_interaction_mode.md`** — listening in the preview moment.
4. **`Video_And_Extra_Materials_Specification.md`** — the supplementary video/materials box.

---

## The one rule that ties it together

For **graded** listening, the answer must be obtainable **only by listening** — the transcript is hidden until after the student answers. Audio that exists just to be played, with no questions, belongs in **Extra Materials**, not in a game. That line keeps "listening practice" honest and keeps "extra video" clearly optional.
