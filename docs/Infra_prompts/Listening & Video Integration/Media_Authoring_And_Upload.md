# Media Authoring & Upload — Builder Requirement

**Type:** Builder capability requirement (authoring side).
**Audience:** Production team + builder developers.
**Origin:** Raised by the dev team — the old builder supported image / video / audio upload; the new builder does not. This spec answers *whether* it should be added, *where* it goes, and *what must not happen* to existing media.

---

## 1. The Question Being Answered

Two questions came from the dev team:

1. The old builder let authors **insert images, video, and audio**. The new builder has no such ability. Should it stay without media upload, or should it be added — and if so, **where**?
2. Media that authors uploaded in the old builder **gets deleted when content moves to the new builder.** Is that acceptable?

**Answers, short version:**
1. **Yes — add image, audio, and video upload back.** The listening and video work depends on it, and authors expect it. Specific homes below.
2. **No — that data loss is not acceptable.** Existing media must round-trip through the new builder, not be silently dropped. This is a correctness requirement, not a nice-to-have.

---

## 2. Why Media Must Come Back

The new builder was designed around text-based games, so media upload was never wired in. But three things now require it:

- **The Listening Comprehension game** needs an **audio** file per task.
- **Listening Cases** in Case-Based Preview need an **audio** file per case.
- **The Extra Materials surface** needs **video / audio / PDF** attachments.
- Authors also legitimately want **images** in stories, flashcards, and question prompts — as they had before.

So media upload is not an optional extra; it is the foundation the listening + video features stand on. Build the upload capability once, reuse it everywhere.

---

## 3. Where Media Upload Belongs in the New Builder

A single, reusable **media field** (the author picks a file, sees a preview, can replace/remove it) should appear in these authoring places:

| Where in the builder | Media type | Used by |
|---|---|---|
| **Listening game editor** | Audio | Listening Comprehension game (one clip per task) |
| **Case-Based Preview editor** — case setup / story | Audio, Image | Listening Cases; illustrated story context |
| **Flashcard editor** — card front/back | Image | Visual vocabulary, diagrams |
| **Question / quiz prompt editors** | Image | Diagrams or pictures a question refers to |
| **Extra Materials editor** | Video, Audio, PDF, Link | The supplementary surface |

The author experience should match the old builder's: choose a file → it uploads → a preview appears → the item is attached, with a clear way to swap or delete it. Images, audio, and video each get the appropriate picker (e.g. images accept image files, audio accepts mp3/wav/etc., video accepts mp4 or a YouTube link).

---

## 4. Where Uploaded Media Goes

Reuse the **existing media pipeline** — the platform already uploads files and serves them from the shared media host (`edu.jakhongir.dev/media/...`) with a tracked **media id**. The new builder should upload through that same pipeline rather than inventing a new one.

Every uploaded item is stored as **both**:

- a **URL** (so the runtime can play/show it), and
- a **media id** (so the asset is tracked, reusable, and not orphaned).

YouTube videos and external links store only a URL (nothing is uploaded).

---

## 5. The Migration Rule (no more disappearing media)

This is the part that must be treated as a bug fix, not a feature.

**Rule:** when a homework's content passes through the new builder, any media it already has must be **preserved end-to-end** — read in, shown in the editor, and saved back out — so that editing and re-saving never strips it.

Concretely, the production/dev team must ensure:

1. **Round-trip.** Existing image/video/audio references survive being opened and saved in the new builder. The author sees them; saving keeps them.
2. **No silent drop.** If for any reason a piece of media cannot be shown in the new builder yet, it must still be **carried through untouched** on save — never discarded.
3. **Audit before migrating.** Before any batch migration of old homeworks into the new builder, **count how many homeworks carry media** and verify they come through intact on a sample. Do not run a mass migration that has not been checked for media survival.
4. **Recoverable, not destroyed.** Original uploaded assets on the media host must not be deleted as part of migration, so anything mis-handled can be re-linked rather than lost forever.

Until the round-trip is in place, **do not migrate media-bearing homeworks into the new builder**, because the current behaviour orphans their media.

---

## 6. What NOT to Do

- **Do not build a second upload pipeline.** Reuse the existing one (`/upload` → media host + media id).
- **Do not store media as a raw blob with no media id.** Without the id, the asset can't be tracked and is at risk on the next migration.
- **Do not let a save operation drop fields the builder doesn't render yet.** Carry unknown/extra media through untouched.
- **Do not mass-migrate before the audit + round-trip fix.** That is exactly what causes the reported data loss.
- **Do not confuse graded audio with extra audio.** Audio in the Listening game/case is graded content; audio in Extra Materials is supplementary. Same upload widget, different home.

---

## 7. Success Criteria

Media authoring is working correctly when:

- An author can upload image, audio, and video in the new builder, in the homes listed in §3, with preview + replace + remove.
- Every uploaded item carries a URL **and** a media id.
- Opening and re-saving an old, media-bearing homework in the new builder **keeps all its media**.
- A migration audit confirms media survival on a representative sample before any batch run.
- The Listening game, Listening Cases, and Extra Materials surface all draw their media from this one upload capability.

---

## Annex — Javob (dev jamoasiga, qisqacha)

> **1-savol — rasm/audio/video yuklash qo'shilsinmi va qayerda?**
> Ha, qo'shilishi kerak. Listening (tinglash) o'yini va qo'shimcha materiallar bularsiz ishlamaydi. Joylari: Listening o'yini muharririda — **audio**; Case-Based Preview (story/case) — **audio va rasm**; Flashcard va savol matnlarida — **rasm**; Qo'shimcha materiallar bo'limida — **video, audio, PDF, link**. Yuklash uchun **mavjud media tizimidan** foydalaning (`edu.jakhongir.dev/media/...`), yangi tizim yaratmang. Har bir fayl **URL + media id** bilan saqlanadi.
>
> **2-savol — eski builderdagi rasm/video yangi builderga o'tganda o'chib ketishi muammomi?**
> Ha, bu muammo — bunday bo'lmasligi kerak. Bu xatolik (data loss), uni tuzatish shart. Eski media yangi builderda **ochilganda ko'rinishi va saqlanganda saqlanib qolishi** kerak (round-trip). Yangi builder hali ko'rsata olmaydigan media bo'lsa ham, saqlashda **o'chirilmasdan saqlanib qolishi** kerak. **Ommaviy migratsiyadan oldin** nechta uy vazifasida media borligini sanab, namunada media saqlanishini tekshiring. Round-trip tuzatilmaguncha, media bor uy vazifalarini yangi builderga **ko'chirmang**.
