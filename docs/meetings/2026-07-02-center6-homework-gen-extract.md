# Center-6 meeting (2026-07-02) — homework CONTENT-GENERATION extract

> Narrowed extract from the recorded transcript ("Korzinka — Center-6.m4a"): only what
> touches the homework **generation pipeline**. App/platform topics (anti-cheat,
> compliance filters, per-student "Jarvis" AI, progress reports, video-lane mechanics)
> are deliberately out of scope here. Goal: a shortlist the team can act on.

## Task shortlist (act on these)

| # | Task | Owner | Where it lands |
|---|------|-------|----------------|
| 1 | **Narrow the October generation scope: ALL subjects EXCEPT the 5 language subjects** (O'zbek tili, Ona tili grammar, Rus tili + its grammar, English — "beshta fandan tashqari hammasi"). Feeds directly into the grade×subject matrix (Trello stage 1 SCOPE, due Jul 11). | operator (matrix) | Trello SCOPE |
| 2 | **Russian output is a first-class batch deliverable** ("Vazifa: rus tiliga ham yozing") — plan RU batches alongside UZ (per-language batches are separate rows; affects batch counts + cost math). Already supported: `output_language=ru`. | operator (matrix) + launch config | Trello SCOPE/BULK |
| 3 | **Content contract: every homework opens with a real-life case** — any subject, two approved shapes: storytelling or question-first (answer at the end), fun-fact hooks encouraged. Case-based-preview already leads with a real case — run a one-time **prompt-conformance check** against the meeting's contract rather than assuming. | gatekeeper/implementer (small) | WISHLIST `cbp-real-life-contract-1` |
| 4 | **Decide: images in generated homework?** Today's architecture deliberately emits described placeholders (no visuals). Boss opened the question: add image generation ("Gemini — the fancy one; we don't need best quality — you decide"). Needs a worked-up decision: model, cost/image, where in the pipeline, Notion rendering. | team decision → plan | WISHLIST `homework-images-1` |
| 5 | **Decide the batch's cost model: Vertex-api vs Claude subscriptions.** Meeting framing was subscription-first ("Claude bilan amallab ishlab bo'ladi", 4×Claude + 1×Codex = $1065, GLM incoming, don't depend on Google). Repo's locked strategy is api-only over the $100k Vertex credits (cli retired 2026-07-01). Recommendation: Vertex-api for the batch (subs hit session limits — killed the 2026-06-23 mass run; Vertex ceiling ≈16–21 concurrent homeworks), subscriptions as fallback pool. | user/boss decision | blocks BULK GEN |
| 6 | **Throughput readiness** — "productionni kuchaytiringlar": subs assigned per-task, expect fast burn, more subs if needed. For homework gen: be ready to run at volume the moment SCOPE lands (fleet is ready; concurrency ceiling is Vertex quota). | standing | Trello BULK |

## Cost cheat-sheet (the meeting's "$1/book" was a from-memory ballpark — these are measured)

| Unit | flash judge | pro judge |
|---|---|---|
| 1 homework (lesson) | **$0.45** | $1.04 |
| 1 chapter (~30–35 homeworks) | **~$15** | ~$34 |
| 1 book (~50–75 lessons) | **~$23–34** | ~$52–78 |
| Oct+Mar batch (~3,500 packets) | **~$1.6k** | ~$3.7k |

flash-lite content ≈ $0.33/hw ≈ $11/chapter. On CLI subscriptions the *marginal* cost is
≈$0 (flat $1065) — that's where the "$1/book" intuition came from; operationally retired.

## Context kept for the record (not generation-lane, tracked elsewhere)

- Platform/app: anti-cheat (single-session, screenshot block, integrity disclaimer),
  anti-agent/scraping defenses (bots doing/scraping homework), compliance filters
  (profanity, extremism), upload validation, Cloudflare startup credits (~$250k),
  automated compliance review.
- AI features to architect (4): school-data synthesizer, "Boss AI", chatbot, "Jarvis"
  per-student memory agent (learns from every homework attempt — this is the future
  per-student runtime AI cost line on the campaign card).
- Reporting: per-student progress report (e.g. last-10-homeworks progress, single
  PDF/Excel); internal content-efficiency reports.
- Video lane: cut intros/outros/questions, credits at end, ≤1h per video, 7–8/day,
  60–70 exercises in ~4 days, RU versions, ElevenLabs as backup.
