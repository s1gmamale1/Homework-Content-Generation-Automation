# NETS Pure Content Automation — Flow v2 **Frontend** Transformation Plan

**Status:** Design plan for execution by a separate session. Grounded in the real
frontend (`web/`) and the Flow v2 backend on `Nggaev-v2`.
**Date:** 2026-05-30 · **Re-verified:** 2026-06-01 (against backend tip `136d63a`, post Path A + Path B).
**Companion:** `nets_pure_content_automation_flow_v2_plan.md` (the backend plan this mirrors).

> **Re-verification note (2026-06-01, post Path A + Path B).** Re-checked field-by-field
> against the live schemas/flow. The skeleton holds (`JobOut` serves all the Flow v2 columns;
> no backend change needed; registry/legacy-fallback approach intact). Contracts that changed
> and are now reflected below:
> - **CBP-mode games were lightened (Path B / worklog 0021).** `CbpModeGame` is no longer a
>   `CaseBasedPreview` — it is a **compact standalone** schema (`title`, `source_concept_ids`,
>   `interaction_mode`, `instruction`, `interaction_payload`, `why_prompt`,
>   `expected_reasoning_keywords`). It must NOT reuse the CBP view; render the board + the one
>   `why_prompt` reasoning step.
> - **`reading` was removed from the flow (Path A / worklog 0019).** No flow emits it; the
>   `reading_json` column persists but is never populated. `ReadingExperience` is kept dormant
>   for legacy jobs only — do not register `reading` as an active Flow v2 phase.
> - **Single flow, no easy/hard, no classify (Path A).** `SUBJECT_FLOWS` is gone → `flow_for()`
>   + a `SUBJECT_GAME` map; exactly **one** subject-matched CBP-mode game runs per job.
> - **Memory Check / Flashcard** moved to their enriched shapes (option-objects + blanks + WHY;
>   10-field card) — already reflected in §4.

---

## 0. The one rule (mirror of the backend plan)

```txt
The backend swapped its phase set to Flow v2. The frontend still renders the legacy flow.
Mirror the same swap onto web/: swap the phase components, reuse the React infrastructure.
Don't rebuild the SPA. Content-generation review only — no student interactivity.
```

The SPA's "infrastructure" — routes, the API client (`web/src/lib/api.ts`), auth, the
TanStack-Query data layer, the SSE hook, the layout, and the `ui/` kit — is **done and
correct** and must not be rebuilt. The only thing out of sync with the backend is the
**phase-rendering layer**. That is the whole job.

---

## 1. Reality anchor — what already works (reuse, do not rebuild)

Every action in the product is already wired in the UI; only the *output rendering* is legacy.

| Capability | Where it lives | Status |
|---|---|---|
| Auth + route guard | `lib/auth.ts`, `components/protected-route.tsx` | done |
| API client (all endpoints) | `lib/api.ts` | done |
| Upload / library / book / section / job / usage routes | `routes/*.tsx` | done |
| Live SSE (TOC + job progress) | `hooks/use-event-source.ts` | done |
| Generate w/ provider+model+force pickers | `routes/section.tsx`, `routes/job.tsx` | done |
| Retry / download | `routes/job.tsx`, `routes/preview.tsx` | done |
| UI kit (card, badge, button, select, …) | `components/ui/*` | done |
| Markdown renderer | `routes/preview.tsx` `MD_COMPONENTS` | done — keep, reuse |

**Backend is ready for this.** `JobOut` (`app/schemas/job.py`) **already serializes every
Flow v2 column**: `source_map_json`, `cbp_json`, `memory_check_json`, `boss_arena_json`,
`practice_rlc_json`, `practice_error_detection_json`, `practice_memory_match_json`,
`practice_tictactoe_json`, `practice_jigsaw_json`, `practice_sentence_json` (plus the
still-live `flashcards_json` / `reading_json`). **No backend change is required for this
plan.** `GET /api/v1/jobs/{id}` already returns all of it.

---

## 2. The Flow v2 delta (the frontend phase-set swap)

| Legacy frontend (today) | Flow v2 frontend |
|---|---|
| `components/flashcards/flashcard-deck.tsx` | **keep** — add stable `id` to the `Flashcard` type |
| `components/memory-sprint/memory-sprint.tsx` | replaced by **Memory Check** (`memory_check_json`) |
| `components/games/*` (`game-card`, `memory-match`, `tile-match`, `sentence-fill`, `adaptive-quiz`) | replaced by **Practice Arc** review renderers (`practice_*_json`) |
| `components/boss-fight/boss-fight.tsx` (HP quiz) | replaced by **Boss Arena** (`boss_arena_json`, Why→How→What) |
| `components/reading/reading-experience.tsx` | **dormant** — Flow v2 dropped `reading` (Path A); kept only for legacy jobs, not in the active render path |
| (none) | **Source Map** review (`source_map_json`) — new |
| (none) | **Case-Based Preview** review (`cbp_json`) — new |
| `preview.tsx` legacy heading/JSON splicing | rewired to drive off the Flow v2 columns |

Legacy components are **left dormant** (kept in the tree, removed from the active render
path) — the exact mirror of how the backend kept `final-challenge` dormant. This lets old
(pre-Flow-v2) jobs still render through the legacy path.

---

## 3. The mechanism — how one Flow v2 phase gets added to the frontend

The frontend mirror of the backend's five-edit recipe. Each phase = four small, local edits:

```txt
1. web/src/lib/types.ts                       # TS interface mirroring the Pydantic schema
2. web/src/components/flow-v2/<phase>.tsx      # read-only review renderer for that JSON
3. web/src/lib/flow-v2-phases.ts               # register phase → {column, component, title, division}
4. (automatic) routes/preview.tsx              # the registry-driven preview picks it up
```

No new routing, no new data fetching. `preview.tsx` iterates the registry, so once a phase
is registered it renders automatically — the mirror of "the scheduler/assembly pick it up
for free" on the backend.

---

## 4. Data contracts — TS interfaces to add (`web/src/lib/types.ts`)

Mirror the Pydantic schemas exactly. Source of truth in parentheses.

```ts
/* Source Map (app/schemas/flow_v2.py :: SourceMap / SourceConcept) */
export type SourceConceptKind = "concept" | "term" | "formula" | "process" | "skill" | "fact";
export interface SourceConcept { id: string; label: string; statement: string; kind: SourceConceptKind; source_ref?: string | null; }
export interface SourceMap { subject_family: string; chapter: string; section: string; concepts: SourceConcept[]; }

/* Case-Based Preview (app/schemas/flow_v2.py :: CaseBasedPreview) */
export type CheckpointIntent = "identify" | "decide" | "justify_or_avoid_mistake";
export type CheckpointForm = "mcq" | "choice" | "short_select" | "true_false";
export interface CaseSetup { narrative: string; student_role: string; task: string; }
export interface CaseCheckpoint { intent: CheckpointIntent; form: CheckpointForm; question: string; options: string[]; correct_index?: number | null; feedback: string; }
export interface DecisionProcessExplanation { prompt: string; expected_components: ("concept"|"method"|"mistake")[]; rubric: Record<string, unknown>; sample_acceptable_answer: string; eval_mode: "ai" | "rubric_ai"; min_chars: number; options: null; }
export interface CaseSimulation { correct_path: string; wrong_path: string; why_wrong_fails: string; }
export interface FeedbackSummary { understood: string; mistake: string; review: string; }
export interface CompletionRules { pass_condition: string; retry_condition: string; }
/* LearningBlock (CBP standard §5, slots 3 & 5) — short teaching step between checkpoints.
   LB1 explains the concept after Checkpoint 1; LB2 shows the method after Checkpoint 2.
   Added to the backend schema after this plan's first draft; both are REQUIRED. */
export interface LearningBlock { explanation: string; title?: string | null; visual_svg?: string | null; source_concept_id?: string | null; }
export interface CaseBasedPreview {
  title: string; student_role: string; case_type: string; source_concept_ids: string[];
  case_setup: CaseSetup; checkpoints: CaseCheckpoint[];
  learning_block_1: LearningBlock; learning_block_2: LearningBlock;   // REQUIRED
  decision_process_explanation: DecisionProcessExplanation;
  final_simulation: CaseSimulation; feedback_summary: FeedbackSummary; completion_rules: CompletionRules;
}

/* CBP-mode game (app/schemas/practice_games.py :: CbpModeGame). Path B (worklog 0021)
   made this a COMPACT STANDALONE schema — it NO LONGER extends CaseBasedPreview. It is a
   game board + one open reasoning step, not a full CBP. The payload (pairs / jigsaw pieces /
   sentence chips / 9-cell board) is discriminated by interaction_mode. Render the payload +
   the why_prompt; do NOT render a CBP shell (there isn't one). */
export type PracticeInteractionMode = "memory_match" | "jigsaw" | "sentence_fill" | "tictactoe";
export interface GameChoice { label: string; is_correct: boolean; reason?: string | null; }
export interface MemoryMatchPair { left: string; right: string; }
export interface MemoryMatchPayload { pairs: MemoryMatchPair[]; }                         // 4–8 pairs
export interface JigsawPiece { id: string; content: string; }
export interface JigsawPayload { pieces: JigsawPiece[]; allowed_assembly_types: string[]; } // 3–6 pieces
export interface SentenceFillPayload { sentence: string; chips: GameChoice[]; }            // ≥3 chips, exactly 1 correct
export interface TicTacToePayload { cells: GameChoice[]; }                                  // exactly 9 cells, ≥1 correct
export type InteractionPayload = MemoryMatchPayload | JigsawPayload | SentenceFillPayload | TicTacToePayload;
export interface CbpModeGame {
  title: string;
  source_concept_ids: string[];
  interaction_mode: PracticeInteractionMode;
  instruction: string;                       // 1–2 sentences: the task
  interaction_payload: InteractionPayload;   // narrow by interaction_mode at the render site
  why_prompt: string;                        // the one open "explain your reasoning" step
  expected_reasoning_keywords: string[];
}

/* Memory Check (app/schemas/memory_check.py :: MemoryCheckPack) — per-type item model.
   options are OBJECTS (not string[]); there is NO correct_index — correctness is per-option.
   option-kinds (multiple_choice / choose_correct_explanation) carry exactly 4 options, one
   is_correct, and no blanks. fill_blank carries blanks and no options. */
export type MemoryCheckKind = "multiple_choice" | "fill_blank" | "choose_correct_explanation";
export interface MemoryCheckOption { text: string; is_correct: boolean; reason?: string | null; }
export interface MemoryCheckBlank { answer: string; accepted_variations: string[]; }
export interface MemoryCheckItem {
  flashcard_id: string; kind: MemoryCheckKind; prompt: string;
  options: MemoryCheckOption[]; blanks: MemoryCheckBlank[];
  why_prompt?: string | null; expected_reasoning_keywords: string[];
  correct_feedback?: string | null; wrong_feedback?: string | null; explanation?: string | null;
}
export interface MemoryCheckPack { items: MemoryCheckItem[]; pass_threshold: number; }

/* Boss Arena (app/schemas/boss_arena.py :: BossArena / BossQuestion) */
export interface BossQuestion { concept_ids: string[]; difficulty: "easy" | "medium" | "hard"; scenario: string; why: string; how: string; what: string; bloom_level?: string | null; pisa_level?: string | null; base_damage: number; hints: string[]; correct_feedback: string; partial_feedback: string; wrong_feedback: string; }
export interface BossArena { title?: string; starting_hp?: number; questions: BossQuestion[]; }

/* Real-Life Challenge (app/schemas/practice_games.py :: RealLifeChallenge) */
export interface RlcDecision { question: string; options: string[]; correct_option: number; why_required: boolean; confidence_required: boolean; expected_reasoning: string[]; correct_feedback: string; partial_feedback: string; wrong_feedback: string; }
export interface RealLifeChallenge { scenario_id?: string | null; concept_ids: string[]; role: string; task: string; grade_band?: string | null; pisa?: string | null; context: string; prediction_prompt: string; decisions: RlcDecision[]; red_herring?: string | null; final_summary: string; }

/* Error Detection (app/schemas/practice_games.py :: ErrorDetection) */
export type ErrorPattern = "math_equation" | "grammar_sentence" | "science_diagram";
export interface ErrorBlock { id: string; content: string; is_error: boolean; }
export interface ErrorDetection { task_id?: string | null; pattern: ErrorPattern; concept_ids: string[]; grade_band?: string | null; difficulty?: string | null; blocks: ErrorBlock[]; correct_answer_for_error_block: string; accepted_variants: string[]; common_mistake_source: string; hint: string; why_prompt: string; expected_reasoning_keywords: string[]; correct_feedback: string; wrong_correction_feedback: string; reveal_feedback: string; }
```

Extend the `Flashcard` interface to its full Flow v2 shape — it gained more than just `id`
(`app/schemas/flashcards.py`): required `type` and `difficulty`, plus several optional fields.
`reading_json` (`ReadingPassage`) is already in `types.ts` and unchanged — but **dormant**:
no Flow v2 flow emits a `reading` phase anymore (Path A), so the column is always null on
new jobs. Keep the type for legacy jobs; do not register `reading` as an active phase (§6).

```ts
export type FlashcardType = "definition" | "term_to_meaning" | "formula" | "process_step"
  | "question_answer" | "misconception" | "image_label" | "vocabulary" | "grammar" | "example";
export type FlashcardDifficulty = "easy" | "medium" | "hard";
export interface Flashcard {
  id: string; front: string; back: string; type: FlashcardType; difficulty: FlashcardDifficulty;
  hint?: string | null; explanation?: string | null; example?: string | null;
  misconception?: string | null; cluster?: string | null;
}
```

Then add the new columns to `Job`:

```ts
export interface Job {
  /* …existing… */
  source_map_json: SourceMap | null;
  cbp_json: CaseBasedPreview | null;
  memory_check_json: MemoryCheckPack | null;
  boss_arena_json: BossArena | null;
  practice_rlc_json: RealLifeChallenge | null;
  practice_error_detection_json: ErrorDetection | null;
  practice_memory_match_json: CbpModeGame | null;
  practice_tictactoe_json: CbpModeGame | null;
  practice_jigsaw_json: CbpModeGame | null;
  practice_sentence_json: CbpModeGame | null;
}
```

---

## 5. Components to build (`web/src/components/flow-v2/`)

All are **read-only review renderers**: render the full content including answer keys,
rubrics, and feedback (this is a content tool, not a student app). Match the existing visual
language — use the `ui/card`, `ui/badge` primitives and the same Tailwind tokens
(`--color-ink`, `--color-elevated`, `--color-accent`, etc.) the legacy components use.

1. **`source-map.tsx`** — `<SourceMapView map={SourceMap} />`. A definition list of concepts:
   each row shows an `id` badge, `label`, `statement`, a `kind` chip, and `source_ref` if
   present. This is the answer key index every other phase references.

2. **`case-based-preview.tsx`** — `<CaseBasedPreviewView cbp={CaseBasedPreview} />`.
   Renders, in CBP slot order with the learning blocks interleaved between checkpoints:
   Case Setup (role + narrative + task) → Checkpoint 1 → **Learning Block 1** (concept) →
   Checkpoint 2 → **Learning Block 2** (method) → Checkpoint 3 (each checkpoint: question,
   options with the correct one marked, intent/form badges, feedback; each learning block:
   `title` + `explanation`, optional `visual_svg`, `source_concept_id`) → **DPE** (prompt,
   `expected_components` chips, sample acceptable answer, rubric) clearly flagged as the
   open-ended production step → Final Simulation (correct path / wrong path / why wrong
   fails) → Feedback Summary → Completion Rules. This renders the `cbp_json` phase **only** — it
   is NOT reused by the CBP-mode games anymore (Path B made those compact standalone; see item 7).

3. **`memory-check.tsx`** — `<MemoryCheckView pack={MemoryCheckPack} />`. Header shows the
   `pass_threshold` (e.g. "Pass ≥ 60%"). Render per `kind` (no `correct_index` — correctness is
   per-option):
   - `multiple_choice` / `choose_correct_explanation`: the 4 `options` objects, the `is_correct`
     one marked, each option's `reason` shown (why a distractor is wrong / flawed reasoning).
   - `fill_blank`: the `blanks` (`answer` + `accepted_variations`).
   - Always: `flashcard_id` reference badge, prompt, and — when present — `why_prompt`,
     `expected_reasoning_keywords`, `correct_feedback` / `wrong_feedback`, `explanation`.

4. **`boss-arena.tsx`** — `<BossArenaView boss={BossArena} />`. Per question: difficulty +
   `base_damage` badges, the scenario, then the **Why / How / What** trio laid out as three
   labeled blocks (the defining structure), `concept_ids` chips, hints, and the
   correct/partial/wrong feedback. No HP game — a review of the reasoning content.

5. **`practice-rlc.tsx`** — `<RealLifeChallengeView rlc={RealLifeChallenge} />`. Role, task,
   context, prediction prompt, then each decision (options with correct marked, expected
   reasoning keywords, the three feedback strings, why/confidence flags), the red herring if
   present, and the final summary.

6. **`practice-error-detection.tsx`** — `<ErrorDetectionView ed={ErrorDetection} />`. Pattern
   badge, the blocks list with the broken one flagged (`is_error`), the correct answer +
   accepted variants, hint, why-prompt, and feedback strings.

7. **`cbp-mode-game.tsx`** — `<CbpModeGameView game={CbpModeGame} />`. **Compact (Path B):** render
   the `title`, the `instruction`, the typed `interaction_payload` (switch on `interaction_mode`),
   then the `why_prompt` reasoning step (with `expected_reasoning_keywords` as teacher-side chips).
   There is **no CBP shell** — `CbpModeGame` no longer extends `CaseBasedPreview`, so do not call
   `CaseBasedPreviewView` here. Four small payload sub-renderers (co-located here or in `flow-v2/payloads/`):
   - `memory_match` → a two-column pairs table (`MemoryMatchPayload.pairs` → left ↔ right).
   - `jigsaw` → the `pieces` list + the `allowed_assembly_types`.
   - `sentence_fill` → the `sentence` with the `chips` listed, the `is_correct` chip marked,
     each chip's `reason`.
   - `tictactoe` → a 3×3 grid of the 9 `cells`, the `is_correct` cell(s) marked, `reason` shown.
   Exhaustive `switch` on `interaction_mode` so adding a mode is a compile error until handled.
   Registry points all four practice-* CBP modes at this one symbol — but only the subject's
   matched game (`SUBJECT_GAME`) is non-null per job; the other three columns are empty.

---

## 6. The registry (`web/src/lib/flow-v2-phases.ts`)

The frontend mirror of `STRUCTURED_PHASE_SCHEMAS` + `flows.flow_for()` ordering (the old
`SUBJECT_FLOWS` is gone — Path A). One flow for all subjects, with one subject-matched game
inserted from `flows.SUBJECT_GAME`. A single ordered table the preview iterates:

```ts
export type Division = "Learning Sections" | "Practice Arc" | "Boss Arena" | "Reflection";
export interface FlowV2PhaseDef {
  key: string;                      // phase name, e.g. "practice-rlc"
  column: keyof Job;                // e.g. "practice_rlc_json"
  title: string;                    // display, e.g. "Real-Life Challenge"
  division: Division;
  isEmpty: (data: unknown) => boolean;   // true ⇒ phase didn't run, skip
  render: (data: any) => React.ReactNode;
}

export const FLOW_V2_PHASES: FlowV2PhaseDef[] = [
  // Learning Sections
  { key: "case-based-preview", column: "cbp_json", title: "Case-Based Preview", division: "Learning Sections", … },
  { key: "flashcards",         column: "flashcards_json", title: "Flashcard Learning", division: "Learning Sections", … },  // reuse FlashcardDeck
  { key: "memory-check",       column: "memory_check_json", title: "Memory Check", division: "Learning Sections", … },
  // NOTE: no `reading` entry — Path A dropped the reading phase; `reading_json` is always null
  //       on Flow v2 jobs. (Legacy jobs render reading via the dormant legacy path, not here.)
  // Practice Arc
  { key: "practice-rlc",              column: "practice_rlc_json", title: "Real-Life Challenge", division: "Practice Arc", … },
  { key: "practice-error-detection",  column: "practice_error_detection_json", title: "Error Detection", division: "Practice Arc", … },
  { key: "practice-memory-match",     column: "practice_memory_match_json", title: "Memory Matching", division: "Practice Arc", … },
  { key: "practice-tictactoe",        column: "practice_tictactoe_json", title: "TicTacToe", division: "Practice Arc", … },
  { key: "practice-jigsaw",           column: "practice_jigsaw_json", title: "Jigsaw Matching", division: "Practice Arc", … },
  { key: "practice-sentence",         column: "practice_sentence_json", title: "Sentence Filling", division: "Practice Arc", … },
  // Boss
  { key: "boss-arena", column: "boss_arena_json", title: "Boss Arena", division: "Boss Arena", … },
];
```

Order and division mirror the backend `_LEARNING_PHASES` / `_PRACTICE_PHASES` / `_BOSS_PHASES`
groupings in `app/services/pipeline.py` so the on-screen order matches `homework.md`.

---

## 7. Rewiring `routes/preview.tsx`

Replace the fragile legacy "match a markdown heading, splice in a widget" logic with a
**registry-driven, column-driven** render. The current code keys off legacy headings
(`## Flashcards`, `## Memory Sprint`, `## Game Breaks`, `## Final Challenge`) and legacy
columns — none of which Flow v2 emits — which is exactly why a Flow v2 job currently shows as
plain markdown.

New behavior:

1. **Detect flow version.** If any Flow v2 column is non-empty (`cbp_json` || `boss_arena_json`
   || any `practice_*_json`), render the **Flow v2 view**; else fall back to the existing
   legacy view unchanged (old jobs keep working — dormant legacy path).
2. **Flow v2 view:** render top-to-bottom:
   - **Source Map** (`<SourceMapView>`), if present.
   - The **Extracted Section Summary** + **Source Book/Chapter/Section** as markdown (pull
     from `assembled_md`, or render a small header from the job/section data).
   - For each `Division`, a `## {division}` header, then each registered phase whose column
     is non-empty, rendered via its `render()` in registry order, inside a titled card.
   - **Reflection** as markdown (prose-only phase — read its `## Reflection` block from
     `assembled_md`, since it has no JSON column).
3. Keep the existing `Download .zip` / `.md` buttons and the `MD_COMPONENTS` markdown styling
   (reused for the prose phases).

Net effect: the preview becomes a clean, ordered, per-phase content **review** of exactly what
the Flow v2 backend produced, answer keys and all.

---

## 8. Out of scope for this plan (explicit)

- **No student interactivity** (no answer-hiding, no HP, no scoring, no game mechanics) — this
  is content-generation review. (Per the project owner's steer.)
- **No backend changes** — `JobOut` already serves everything. (If a later "student/teacher
  split export" is wanted, that's the separate Export sub-project, not this.)
- **No new routes** and **no dashboard/batch/QA-editing** — those are the other three platform
  sub-projects (generation dashboard + batch; controls & export; per-phase QA/editing), each
  its own later plan. This plan is *only* the phase-set mirror that makes `web/` Flow-v2-native.
- Legacy components are **not deleted**, only removed from the active render path.

---

## 9. Testing & acceptance

**Tooling that exists:** `cd web && npx tsc -p tsconfig.app.json --noEmit` (typecheck) and
`npm run lint` (biome). There is **no** frontend unit-test framework in the repo today — do
not invent one for this; rely on typecheck + lint + manual verification against a live job.

**Acceptance checklist:**
1. `npx tsc -p tsconfig.app.json --noEmit` clean; `npm run lint` clean.
2. New TS interfaces match the Pydantic schemas field-for-field (cross-check the files cited
   in §4).
3. With the stack running (Postgres + `uvicorn` + `npm run dev`), generate one job per a couple
   of subjects (e.g. `physics`, `history`) and confirm `/preview/:id` shows: Source Map,
   Case-Based Preview (3 checkpoints + DPE + simulation), Memory Check (with threshold), the
   Practice Arc (RLC + Error Detection + the one subject-matched CBP-mode game — compact:
   board + `why_prompt`), Boss Arena (Why/How/What), and Reflection — each as a titled review
   card, answer keys visible, in flow order. (No `reading`; no easy/hard — single flow.)
4. An **old/legacy** job (or one with only legacy columns) still renders via the legacy path
   unchanged.
5. Empty phases are skipped (no empty cards) via each def's `isEmpty`.

---

## 10. Execution notes for the running session

- **Isolation:** another session is committing to this repo. Do this work on an **isolated
  branch/worktree** cut from **`Nggaev-v2`** (current HEAD — the live line; `flow-v2-integration`
  is 40 commits behind it and stale), and treat `web/` as this project's
  ownership to avoid collisions; the backend is being touched by the other session.
- **Build order within this plan:** (1) `types.ts` interfaces → (2) the seven `flow-v2/`
  components → (3) `flow-v2-phases.ts` registry → (4) `preview.tsx` rewire → (5) typecheck +
  lint + manual. Components can be built in parallel; the registry and preview rewire come last.
- **Reuse, don't reinvent:** `FlashcardDeck` is kept and registered as-is (Flow v2 still runs
  `flashcards`); extend the `Flashcard` type to its full 10-field shape. `ReadingExperience` is
  **dormant** (reading dropped in Path A) — keep it for legacy jobs, do not register it.
- **Follow existing patterns:** copy the card/section/badge styling from
  `components/boss-fight/boss-fight.tsx` and `components/memory-sprint/memory-sprint.tsx` so the
  Flow v2 renderers visually match the rest of the app.
```
