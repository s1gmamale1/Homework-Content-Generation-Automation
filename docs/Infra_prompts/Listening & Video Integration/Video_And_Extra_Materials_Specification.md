# Video & Extra Materials — Surface Specification

**New surface. No predecessor spec.**
**Type:** Supplementary content surface (NOT a graded game, NOT a flow phase).
**Audience:** Production team creating homework prompts and content.

---

## 1. What the Extra Materials Surface Is

The Extra Materials surface — student-facing label **"Qo'shimcha materiallar"** — is a single, clearly separate box attached to a homework where the student can **watch extra videos** and **open extra resources** (audio, a PDF, a link) that support the lesson but are not part of the graded flow.

It exists because teachers asked for a place to attach a video — a worked example, a documentary clip, a song, a pronunciation model — without forcing it into a checkpoint or a game. The old builder let them attach images, video, and audio; this surface is where that lives in the new system, **as supplementary material rather than graded content.**

The defining line: **everything here is optional and ungraded.** Anything that is graded belongs in the flow (Case-Based Preview, games, Boss), not here.

---

## 2. Where It Lives in the Flow

The Extra Materials box sits **outside the gated flow**, reachable from the homework's hub/overview, available **before, during, and after** the graded work. It never gates anything and is never gated.

```
Homework hub
 ├─ Case-Based Preview      (graded)
 ├─ Flashcards + Memory     (graded)
 ├─ Practice Arc (games)    (graded)
 ├─ Reflection              (graded)
 └─ 📺 Qo'shimcha materiallar  ← THIS surface (optional, ungraded)
```

It is a drawer/box the student can open at will. Opening it, or watching a video, may be **tracked** (for the teacher's view and for engagement telemetry) but **does not affect the score or the pass gate.**

---

## 3. What It Can Hold

| Item type | Source | Student action | Notes |
|---|---|---|---|
| **Video** | YouTube link **or** uploaded file | Watch inline | Most common. YouTube embeds; uploaded video plays inline. |
| **Audio** | Uploaded file | Listen | A song, a pronunciation model, an extended clip. Ungraded — graded audio is the Listening game. |
| **Document** | Uploaded PDF | Open / read | Worksheet, extra reading, reference sheet. |
| **Link** | URL | Open externally | A safe, vetted external resource. |

Each item carries a short **title** and an optional one-line **why** ("Watch this for extra pronunciation practice"). Items can be marked **grade-visible** so the same homework can show different extras to different grade bands.

---

## 4. How It Should Behave

- **Optional and non-blocking.** The student can finish the homework without opening it. It never blocks the gate.
- **Trackable, not scored.** Record opens/watches for the teacher dashboard and engagement telemetry; never feed it into the 100-point game scores or the 60% pass gate.
- **Inline playback.** Video and audio play **inside** the surface (the mobile app already plays YouTube + uploaded video + audio; the web runtime plays them through the shared media player). The student does not get bounced to another app for video.
- **Clear separation.** Visually and structurally distinct from the graded flow, so neither students nor teachers confuse "extra" with "required."
- **Safe links only.** External links must be vetted by the production team — no open web embedding of unreviewed content.

---

## 5. Difficulty / Relevance by Grade Band

The surface is the same at every grade; the **content** is what changes.

| Grade Band | Typical extras |
|---|---|
| **G1–4** | A short song, a cartoon clip, a read-aloud video. Fun, light, reinforcing. |
| **G5–8** | A worked example video, a documentary segment, a pronunciation model. |
| **G9–11** | Exam-strategy videos, authentic English media (news clip, talk), a reference PDF — including IELTS listening/speaking models for the cert strand. |

Use `grade_visible` to show the right extras per band when a homework spans grades.

---

## 6. What the Production Team Outputs

Each homework's extra materials are a simple list:

```
"extra_materials": [
  {
    "id": "xm_001",
    "type": "video",
    "source": "youtube",
    "url": "https://www.youtube.com/watch?v=XXXXXXXX",
    "title": "Aeroport e'lonlarini tinglash — qo'shimcha mashq",
    "why": "Reys e'lonlarini tushunishni mustahkamlaydi.",
    "grade_visible": ["G5-8", "G9-11"]
  },
  {
    "id": "xm_002",
    "type": "video",
    "source": "upload",
    "url": "https://edu.jakhongir.dev/media/video/xm_002.mp4",
    "media_id": 4821,
    "title": "Worked example: past simple",
    "why": null,
    "grade_visible": ["G5-8"]
  },
  {
    "id": "xm_003",
    "type": "pdf",
    "source": "upload",
    "url": "https://edu.jakhongir.dev/media/docs/xm_003.pdf",
    "media_id": 4822,
    "title": "Unit 1 vocabulary reference",
    "grade_visible": ["all"]
  }
]
```

Uploaded items always carry both a `url` and a `media_id` so the asset is tracked and survives migration (see the Media Authoring spec). YouTube/link items carry only a `url`.

---

## 7. What NOT to Do

- **Do not put graded content here.** If a student is scored on it, it belongs in a game or checkpoint, not in Extra Materials.
- **Do not make it mandatory or gating.** The moment opening a video is required to progress, it stops being "extra" and the separation breaks.
- **Do not duplicate the Listening game.** Ungraded audio for enrichment is fine; an audio-with-questions assessment is the Listening Comprehension game, not this surface.
- **Do not embed unvetted external content.** Links and embeds must be reviewed by the production team for safety and relevance.
- **Do not bury required instructions in a video here.** Anything the student must know to complete the homework belongs in the flow, not in an optional box.
- **Do not count watches as mastery.** Watching is engagement, not evidence of learning.

---

## 8. Success Criteria

The Extra Materials surface is working correctly when:

- It is clearly separate from the graded flow and never gates progress.
- Video (YouTube + uploaded) and audio play inline on both mobile and web.
- Opens/watches are tracked for the teacher view but never affect score or pass.
- Uploaded items carry a `media_id` and survive content migration.
- `grade_visible` correctly filters extras per grade band.
- Nothing required to complete the homework lives only inside this box.

If any of these fail, the surface or its content needs review before that homework ships.
