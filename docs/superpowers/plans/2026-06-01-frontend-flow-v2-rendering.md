# Frontend Flow v2 Rendering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `web/`'s preview page render the Flow v2 content (CBP, Memory Check, Practice Arc, Boss Arena, Reflection, Source Map) as read-only review cards, instead of the current raw-markdown fallback.

**Architecture:** Add Flow v2 TS interfaces mirroring the live Pydantic schemas, build one read-only renderer per phase under `web/src/components/flow-v2/`, register phase→{column,component,title,division} in a single ordered table, and rewire `routes/preview.tsx` to drive off that registry when a job has Flow v2 columns (else fall back to the existing legacy render unchanged).

**Tech Stack:** React + TypeScript, TanStack Query, Tailwind v4 (CSS-var classes like `text-(--color-ink)`), Vite, Biome lint. **No frontend unit-test framework exists** — the per-task gate is `npx tsc -p tsconfig.app.json --noEmit` + `npm run lint`, and the final gate is a manual check against a live job. (This replaces TDD's red/green; there is nothing to unit-test for pure display components.)

**Source of truth:** the corrected spec `docs/nets_pure_content_automation_flow_v2_frontend_plan.md` and the live schemas in `app/schemas/`.

**Scope discipline (project owner's steer):** read-only **content review** — render full content INCLUDING answer keys, rubrics, feedback. NO student interactivity (no HP, no scoring, no answer-hiding, no game mechanics). This repo is the content factory, not the student app.

**Conventions every component follows (from `boss-fight.tsx`, `flashcard-deck.tsx`):**
- Tailwind v4 CSS-var classes: `text-(--color-ink)`, `text-(--color-ink-soft)`, `text-(--color-ink-muted)`, `bg-(--color-elevated)`, `bg-(--color-canvas)`, `border-(--color-border)`, `rounded-(--radius-md)`, accents `--color-accent`/`--color-accent-soft`/`--color-accent-border`, status `--color-success`/`--color-error`.
- `import { cn } from "@/lib/utils"`, `import { RichText } from "@/components/rich-text"` (renders markdown-ish strings; `<RichText inline>` for inline), `import { Badge } from "@/components/ui/badge"` (variants `accent|neutral|success|error`, sizes `default|sm|lg`).
- Components are **pure functions, no `useState`** (read-only). Each takes its typed data and returns JSX. Empty-data guard returns a muted "No …" line.

Run all commands from `web/`: `cd web` first (the build dir).

---

## Task 1: Flow v2 data contracts in `types.ts`

**Files:**
- Modify: `web/src/lib/types.ts` (extend `Flashcard` ~line 154; extend `Job` ~line 64; append new interfaces at end before the SSE block)

- [ ] **Step 1: Extend the `Flashcard` interface to the full 10-field shape**

Replace the current `Flashcard` (lines 154-159) with:

```ts
export type FlashcardType =
  | "definition" | "term_to_meaning" | "formula" | "process_step"
  | "question_answer" | "misconception" | "image_label" | "vocabulary"
  | "grammar" | "example";
export type FlashcardDifficulty = "easy" | "medium" | "hard";

export interface Flashcard {
  id: string;
  front: string;
  back: string;
  type: FlashcardType;
  difficulty: FlashcardDifficulty;
  hint?: string | null;
  explanation?: string | null;
  example?: string | null;
  misconception?: string | null;
  cluster?: string | null;
}
```

- [ ] **Step 2: Append the Flow v2 interfaces** (after `FlashcardsPack`, before the `/* SSE event payloads */` block)

```ts
/* ───────── Flow v2 (app/schemas/) ───────── */

/* Source Map (flow_v2.py :: SourceMap / SourceConcept) */
export type SourceConceptKind = "concept" | "term" | "formula" | "process" | "skill" | "fact";
export interface SourceConcept {
  id: string; label: string; statement: string;
  kind?: SourceConceptKind; source_ref?: string | null;
}
export interface SourceMap {
  subject_family: string; chapter: string; section: string; concepts: SourceConcept[];
}

/* Case-Based Preview (flow_v2.py :: CaseBasedPreview) */
export type CheckpointIntent = "identify" | "decide" | "justify_or_avoid_mistake";
export type CheckpointForm = "mcq" | "choice" | "short_select" | "true_false";
export interface CaseSetup { narrative: string; student_role: string; task: string; }
export interface CaseCheckpoint {
  intent: CheckpointIntent; form: CheckpointForm; question: string;
  options?: string[]; correct_index?: number | null; feedback: string;
}
export interface DecisionProcessExplanation {
  prompt: string; expected_components: ("concept" | "method" | "mistake")[];
  rubric: Record<string, unknown>; sample_acceptable_answer: string;
  eval_mode?: "ai" | "rubric_ai"; min_chars?: number; options?: null;
}
export interface CaseSimulation { correct_path: string; wrong_path: string; why_wrong_fails: string; }
export interface FeedbackSummary { understood: string; mistake: string; review: string; }
export interface CompletionRules { pass_condition: string; retry_condition: string; }
export interface LearningBlock {
  explanation: string; title?: string | null; visual_svg?: string | null; source_concept_id?: string | null;
}
export interface CaseBasedPreview {
  title: string; student_role: string; case_type: string; source_concept_ids: string[];
  case_setup: CaseSetup; checkpoints: CaseCheckpoint[];
  learning_block_1: LearningBlock; learning_block_2: LearningBlock;
  decision_process_explanation: DecisionProcessExplanation;
  final_simulation: CaseSimulation; feedback_summary: FeedbackSummary; completion_rules: CompletionRules;
}

/* CBP-mode game — COMPACT standalone (practice_games.py :: CbpModeGame, Path B). NOT a CBP. */
export type PracticeInteractionMode = "memory_match" | "jigsaw" | "sentence_fill" | "tictactoe";
export interface GameChoice { label: string; is_correct?: boolean; reason?: string | null; }
export interface MemoryMatchPair { left: string; right: string; }
export interface MemoryMatchPayload { pairs: MemoryMatchPair[]; }
export interface JigsawPiece { id: string; content: string; }
export interface JigsawPayload { pieces: JigsawPiece[]; allowed_assembly_types: string[]; }
export interface SentenceFillPayload { sentence: string; chips: GameChoice[]; }
export interface TicTacToePayload { cells: GameChoice[]; }
export type InteractionPayload =
  | MemoryMatchPayload | JigsawPayload | SentenceFillPayload | TicTacToePayload;
export interface CbpModeGame {
  title: string;
  source_concept_ids: string[];
  interaction_mode: PracticeInteractionMode;
  instruction: string;
  interaction_payload: InteractionPayload;
  why_prompt: string;
  expected_reasoning_keywords?: string[];
}

/* Memory Check (memory_check.py :: MemoryCheckPack) */
export type MemoryCheckKind = "multiple_choice" | "fill_blank" | "choose_correct_explanation";
export interface MemoryCheckOption { text: string; is_correct?: boolean; reason?: string | null; }
export interface MemoryCheckBlank { answer: string; accepted_variations?: string[]; }
export interface MemoryCheckItem {
  flashcard_id: string; kind: MemoryCheckKind; prompt: string;
  options?: MemoryCheckOption[]; blanks?: MemoryCheckBlank[];
  why_prompt?: string | null; expected_reasoning_keywords?: string[];
  correct_feedback?: string | null; wrong_feedback?: string | null; explanation?: string | null;
}
export interface MemoryCheckPack { items: MemoryCheckItem[]; pass_threshold?: number; }

/* Boss Arena (boss_arena.py :: BossArena / BossQuestion) — distinct from legacy FinalChallenge */
export interface BossArenaQuestion {
  concept_ids: string[]; difficulty: "easy" | "medium" | "hard";
  scenario: string; why: string; how: string; what: string;
  bloom_level?: string | null; pisa_level?: string | null;
  base_damage?: number; hints?: string[];
  correct_feedback: string; partial_feedback: string; wrong_feedback: string;
}
export interface BossArena { title?: string; starting_hp?: number; questions: BossArenaQuestion[]; }

/* Real-Life Challenge (practice_games.py :: RealLifeChallenge) */
export interface RlcDecision {
  question: string; options: string[]; correct_option: number;
  why_required?: boolean; confidence_required?: boolean; expected_reasoning?: string[];
  correct_feedback: string; partial_feedback: string; wrong_feedback: string;
}
export interface RealLifeChallenge {
  scenario_id?: string | null; concept_ids: string[]; role: string; task: string;
  grade_band?: string | null; pisa?: string | null; context: string; prediction_prompt: string;
  decisions: RlcDecision[]; red_herring?: string | null; final_summary: string;
}

/* Error Detection (practice_games.py :: ErrorDetection) */
export type ErrorPattern = "math_equation" | "grammar_sentence" | "science_diagram";
export interface ErrorBlock { id: string; content: string; is_error?: boolean; }
export interface ErrorDetection {
  task_id?: string | null; pattern: ErrorPattern; concept_ids: string[];
  grade_band?: string | null; difficulty?: string | null; blocks: ErrorBlock[];
  correct_answer_for_error_block: string; accepted_variants?: string[];
  common_mistake_source?: string; hint: string; why_prompt?: string;
  expected_reasoning_keywords?: string[];
  correct_feedback: string; wrong_correction_feedback: string; reveal_feedback: string;
}
```

- [ ] **Step 3: Add the Flow v2 columns to the `Job` interface**

Inside `interface Job` (after `reading_json` ~line 78), add:

```ts
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
```

- [ ] **Step 4: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: PASS (no errors). The new interfaces are unused so far — that's fine; they compile.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/types.ts
git commit -m "feat(web): Flow v2 data contracts in types.ts"
```

---

## Task 2: Shared review parts (`flow-v2/parts.tsx`)

DRY: three primitives every renderer reuses — a titled section card, an answer-key callout (this is a review tool, answer keys are shown but visually flagged), and a small labeled row.

**Files:**
- Create: `web/src/components/flow-v2/parts.tsx`

- [ ] **Step 1: Write the file**

```tsx
import type { ReactNode } from "react";
import { KeyRound } from "lucide-react";
import { RichText } from "@/components/rich-text";
import { cn } from "@/lib/utils";

/** A titled review card — the standard wrapper for one piece of phase content. */
export function ReviewCard({ title, children, className }: {
  title?: string; children: ReactNode; className?: string;
}) {
  return (
    <div className={cn("rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-4", className)}>
      {title && (
        <h4 className="mb-2 font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          {title}
        </h4>
      )}
      {children}
    </div>
  );
}

/** Answer key / teacher-only content — always visible (review tool) but flagged. */
export function AnswerKey({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2 flex gap-2 rounded-(--radius-sm) border border-(--color-accent-border) bg-(--color-accent-soft)/40 px-3 py-2 text-sm text-(--color-ink-soft)">
      <KeyRound className="mt-0.5 size-3.5 shrink-0 text-(--color-accent)" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** A "Label: value" row where the value may be markdown-ish. */
export function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <p className="my-1 text-sm leading-relaxed text-(--color-ink-soft)">
      <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
        {label}:{" "}
      </span>
      {typeof children === "string" ? <RichText inline>{children}</RichText> : children}
    </p>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/flow-v2/parts.tsx
git commit -m "feat(web): shared flow-v2 review parts"
```

---

## Task 3: Source Map renderer (`flow-v2/source-map.tsx`)

The answer-key index every other phase references: a list of concepts with id badge, label, statement, kind chip, source_ref.

**Files:**
- Create: `web/src/components/flow-v2/source-map.tsx`

- [ ] **Step 1: Write the file**

```tsx
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import type { SourceMap } from "@/lib/types";

export function SourceMapView({ map }: { map: SourceMap }) {
  if (!map.concepts?.length) {
    return <p className="text-sm text-(--color-ink-muted)">No source map.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
        {map.subject_family} · {map.chapter} · {map.section}
      </p>
      <div className="flex flex-col gap-2">
        {map.concepts.map((c) => (
          <div key={c.id} className="rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-3">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <Badge variant="accent" size="sm">{c.id}</Badge>
              <span className="text-sm font-medium text-(--color-ink)">{c.label}</span>
              {c.kind && <Badge variant="neutral" size="sm">{c.kind}</Badge>}
            </div>
            <RichText className="text-sm leading-relaxed text-(--color-ink-soft)">{c.statement}</RichText>
            {c.source_ref && (
              <p className="mt-1 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                {c.source_ref}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → PASS
```bash
git add web/src/components/flow-v2/source-map.tsx
git commit -m "feat(web): source-map review renderer"
```

---

## Task 4: Case-Based Preview renderer (`flow-v2/case-based-preview.tsx`)

Renders in CBP slot order: case setup → checkpoint 1 → learning block 1 → checkpoint 2 → learning block 2 → checkpoint 3 → DPE → final simulation → feedback summary → completion rules. Answer keys (correct option, simulation paths) shown via `AnswerKey`. **This renders `cbp_json` only — it is NOT reused by the games.**

**Files:**
- Create: `web/src/components/flow-v2/case-based-preview.tsx`

- [ ] **Step 1: Write the file**

```tsx
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import type { CaseCheckpoint, CaseBasedPreview, LearningBlock } from "@/lib/types";

function Checkpoint({ cp, n }: { cp: CaseCheckpoint; n: number }) {
  return (
    <ReviewCard title={`Checkpoint ${n}`}>
      <div className="mb-2 flex flex-wrap gap-2">
        <Badge variant="neutral" size="sm">{cp.intent}</Badge>
        <Badge variant="neutral" size="sm">{cp.form}</Badge>
      </div>
      <RichText className="text-sm font-medium text-(--color-ink)">{cp.question}</RichText>
      {cp.options && cp.options.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {cp.options.map((o, i) => {
            const correct = cp.correct_index === i;
            return (
              <li key={i} className="flex items-start gap-2 text-sm text-(--color-ink-soft)">
                <Badge variant={correct ? "success" : "neutral"} size="sm">
                  {String.fromCharCode(65 + i)}
                </Badge>
                <RichText inline>{o}</RichText>
              </li>
            );
          })}
        </ul>
      )}
      <AnswerKey><Labeled label="Feedback">{cp.feedback}</Labeled></AnswerKey>
    </ReviewCard>
  );
}

function Block({ lb, n }: { lb: LearningBlock; n: number }) {
  return (
    <ReviewCard title={`Learning Block ${n}`} className="border-(--color-accent-border) bg-(--color-accent-soft)/30">
      {lb.title && <p className="mb-1 text-sm font-semibold text-(--color-ink)">{lb.title}</p>}
      <RichText className="text-sm leading-relaxed text-(--color-ink-soft)">{lb.explanation}</RichText>
      {lb.source_concept_id && (
        <p className="mt-1 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          ↳ {lb.source_concept_id}
        </p>
      )}
    </ReviewCard>
  );
}

export function CaseBasedPreviewView({ cbp }: { cbp: CaseBasedPreview }) {
  const cps = cbp.checkpoints ?? [];
  const dpe = cbp.decision_process_explanation;
  const sim = cbp.final_simulation;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-(--color-ink)">{cbp.title}</span>
        <Badge variant="neutral" size="sm">{cbp.case_type}</Badge>
        {cbp.source_concept_ids?.map((id) => <Badge key={id} variant="accent" size="sm">{id}</Badge>)}
      </div>

      <ReviewCard title="Case setup">
        <Labeled label="Role">{cbp.case_setup.student_role}</Labeled>
        <RichText className="my-1 text-sm leading-relaxed text-(--color-ink-soft)">{cbp.case_setup.narrative}</RichText>
        <Labeled label="Task">{cbp.case_setup.task}</Labeled>
      </ReviewCard>

      {cps[0] && <Checkpoint cp={cps[0]} n={1} />}
      <Block lb={cbp.learning_block_1} n={1} />
      {cps[1] && <Checkpoint cp={cps[1]} n={2} />}
      <Block lb={cbp.learning_block_2} n={2} />
      {cps.slice(2).map((cp, i) => <Checkpoint key={i + 2} cp={cp} n={i + 3} />)}

      <ReviewCard title="Decision process (open-ended)">
        <RichText className="text-sm font-medium text-(--color-ink)">{dpe.prompt}</RichText>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {dpe.expected_components.map((c) => <Badge key={c} variant="neutral" size="sm">{c}</Badge>)}
        </div>
        <AnswerKey><Labeled label="Sample answer">{dpe.sample_acceptable_answer}</Labeled></AnswerKey>
      </ReviewCard>

      <ReviewCard title="Final simulation">
        <Labeled label="Correct path">{sim.correct_path}</Labeled>
        <Labeled label="Wrong path">{sim.wrong_path}</Labeled>
        <AnswerKey><Labeled label="Why wrong fails">{sim.why_wrong_fails}</Labeled></AnswerKey>
      </ReviewCard>

      <ReviewCard title="Feedback summary">
        <Labeled label="Understood">{cbp.feedback_summary.understood}</Labeled>
        <Labeled label="Mistake">{cbp.feedback_summary.mistake}</Labeled>
        <Labeled label="Review">{cbp.feedback_summary.review}</Labeled>
      </ReviewCard>

      <ReviewCard title="Completion">
        <Labeled label="Pass">{cbp.completion_rules.pass_condition}</Labeled>
        <Labeled label="Retry">{cbp.completion_rules.retry_condition}</Labeled>
      </ReviewCard>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → PASS
```bash
git add web/src/components/flow-v2/case-based-preview.tsx
git commit -m "feat(web): case-based-preview review renderer"
```

---

## Task 5: Memory Check renderer (`flow-v2/memory-check.tsx`)

Per-kind: MCQ/CCE → option objects with the correct one flagged + each `reason`; fill_blank → blanks + accepted variations. Always show `flashcard_id`, `why_prompt`, feedback.

**Files:**
- Create: `web/src/components/flow-v2/memory-check.tsx`

- [ ] **Step 1: Write the file**

```tsx
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import type { MemoryCheckItem, MemoryCheckPack } from "@/lib/types";

function Item({ item, n }: { item: MemoryCheckItem; n: number }) {
  return (
    <ReviewCard>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-mono text-[0.66rem] text-(--color-ink-muted)">#{n}</span>
        <Badge variant="neutral" size="sm">{item.kind}</Badge>
        <Badge variant="accent" size="sm">{item.flashcard_id}</Badge>
      </div>
      <RichText className="text-sm font-medium text-(--color-ink)">{item.prompt}</RichText>

      {item.options && item.options.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {item.options.map((o, i) => (
            <li key={i} className="flex items-start gap-2 text-sm text-(--color-ink-soft)">
              <Badge variant={o.is_correct ? "success" : "neutral"} size="sm">
                {String.fromCharCode(65 + i)}
              </Badge>
              <span className="min-w-0">
                <RichText inline>{o.text}</RichText>
                {o.reason && <span className="text-(--color-ink-muted)"> — <RichText inline>{o.reason}</RichText></span>}
              </span>
            </li>
          ))}
        </ul>
      )}

      {item.blanks && item.blanks.length > 0 && (
        <AnswerKey>
          {item.blanks.map((b, i) => (
            <Labeled key={i} label={`Blank ${i + 1}`}>
              {[b.answer, ...(b.accepted_variations ?? [])].join(" · ")}
            </Labeled>
          ))}
        </AnswerKey>
      )}

      {item.why_prompt && <Labeled label="Why">{item.why_prompt}</Labeled>}
      {(item.correct_feedback || item.wrong_feedback || item.explanation) && (
        <AnswerKey>
          {item.correct_feedback && <Labeled label="Correct">{item.correct_feedback}</Labeled>}
          {item.wrong_feedback && <Labeled label="Wrong">{item.wrong_feedback}</Labeled>}
          {item.explanation && <Labeled label="Explanation">{item.explanation}</Labeled>}
        </AnswerKey>
      )}
    </ReviewCard>
  );
}

export function MemoryCheckView({ pack }: { pack: MemoryCheckPack }) {
  if (!pack.items?.length) return <p className="text-sm text-(--color-ink-muted)">No memory check.</p>;
  return (
    <div className="flex flex-col gap-3">
      {pack.pass_threshold != null && (
        <p className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          Pass ≥ {Math.round(pack.pass_threshold * 100)}%
        </p>
      )}
      {pack.items.map((it, i) => <Item key={i} item={it} n={i + 1} />)}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → PASS
```bash
git add web/src/components/flow-v2/memory-check.tsx
git commit -m "feat(web): memory-check review renderer"
```

---

## Task 6: Boss Arena renderer (`flow-v2/boss-arena.tsx`)

Per question: difficulty + base_damage badges, scenario, the **Why / How / What** trio, concept_ids, hints, and correct/partial/wrong feedback. No HP game.

**Files:**
- Create: `web/src/components/flow-v2/boss-arena.tsx`

- [ ] **Step 1: Write the file**

```tsx
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import type { BossArena, BossArenaQuestion } from "@/lib/types";

function Question({ q, n }: { q: BossArenaQuestion; n: number }) {
  return (
    <ReviewCard title={`Question ${n}`}>
      <div className="mb-2 flex flex-wrap gap-2">
        <Badge variant="neutral" size="sm">{q.difficulty}</Badge>
        {q.base_damage != null && <Badge variant="error" size="sm">−{q.base_damage} HP</Badge>}
        {q.bloom_level && <Badge variant="neutral" size="sm">Bloom {q.bloom_level}</Badge>}
        {q.pisa_level && <Badge variant="neutral" size="sm">PISA {q.pisa_level}</Badge>}
        {q.concept_ids?.map((id) => <Badge key={id} variant="accent" size="sm">{id}</Badge>)}
      </div>
      <RichText className="text-sm font-medium text-(--color-ink)">{q.scenario}</RichText>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <ReviewCard title="Why" className="bg-(--color-canvas)"><RichText className="text-sm text-(--color-ink-soft)">{q.why}</RichText></ReviewCard>
        <ReviewCard title="How" className="bg-(--color-canvas)"><RichText className="text-sm text-(--color-ink-soft)">{q.how}</RichText></ReviewCard>
        <ReviewCard title="What" className="bg-(--color-canvas)"><RichText className="text-sm text-(--color-ink-soft)">{q.what}</RichText></ReviewCard>
      </div>
      {q.hints && q.hints.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-sm text-(--color-ink-muted)">
          {q.hints.map((h, i) => <li key={i}><RichText inline>{h}</RichText></li>)}
        </ul>
      )}
      <AnswerKey>
        <Labeled label="Correct">{q.correct_feedback}</Labeled>
        <Labeled label="Partial">{q.partial_feedback}</Labeled>
        <Labeled label="Wrong">{q.wrong_feedback}</Labeled>
      </AnswerKey>
    </ReviewCard>
  );
}

export function BossArenaView({ boss }: { boss: BossArena }) {
  if (!boss.questions?.length) return <p className="text-sm text-(--color-ink-muted)">No boss arena.</p>;
  return (
    <div className="flex flex-col gap-3">
      {boss.title && <p className="text-sm font-semibold text-(--color-ink)">{boss.title}</p>}
      {boss.questions.map((q, i) => <Question key={i} q={q} n={i + 1} />)}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → PASS
```bash
git add web/src/components/flow-v2/boss-arena.tsx
git commit -m "feat(web): boss-arena review renderer"
```

---

## Task 7: RLC + Error Detection renderers

Two standalone practice games.

**Files:**
- Create: `web/src/components/flow-v2/practice-rlc.tsx`
- Create: `web/src/components/flow-v2/practice-error-detection.tsx`

- [ ] **Step 1: Write `practice-rlc.tsx`**

```tsx
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import type { RealLifeChallenge, RlcDecision } from "@/lib/types";

function Decision({ d, n }: { d: RlcDecision; n: number }) {
  return (
    <ReviewCard title={`Decision ${n}`}>
      <RichText className="text-sm font-medium text-(--color-ink)">{d.question}</RichText>
      <ul className="mt-2 flex flex-col gap-1">
        {d.options.map((o, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-(--color-ink-soft)">
            <Badge variant={d.correct_option === i ? "success" : "neutral"} size="sm">{String.fromCharCode(65 + i)}</Badge>
            <RichText inline>{o}</RichText>
          </li>
        ))}
      </ul>
      {d.expected_reasoning && d.expected_reasoning.length > 0 && (
        <Labeled label="Expected reasoning">{d.expected_reasoning.join(" · ")}</Labeled>
      )}
      <AnswerKey>
        <Labeled label="Correct">{d.correct_feedback}</Labeled>
        <Labeled label="Partial">{d.partial_feedback}</Labeled>
        <Labeled label="Wrong">{d.wrong_feedback}</Labeled>
      </AnswerKey>
    </ReviewCard>
  );
}

export function RealLifeChallengeView({ rlc }: { rlc: RealLifeChallenge }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {rlc.concept_ids?.map((id) => <Badge key={id} variant="accent" size="sm">{id}</Badge>)}
      </div>
      <Labeled label="Role">{rlc.role}</Labeled>
      <Labeled label="Task">{rlc.task}</Labeled>
      <RichText className="text-sm leading-relaxed text-(--color-ink-soft)">{rlc.context}</RichText>
      <Labeled label="Predict">{rlc.prediction_prompt}</Labeled>
      {rlc.decisions.map((d, i) => <Decision key={i} d={d} n={i + 1} />)}
      {rlc.red_herring && <Labeled label="Red herring">{rlc.red_herring}</Labeled>}
      <ReviewCard title="Final summary"><RichText className="text-sm text-(--color-ink-soft)">{rlc.final_summary}</RichText></ReviewCard>
    </div>
  );
}
```

- [ ] **Step 2: Write `practice-error-detection.tsx`**

```tsx
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import type { ErrorDetection } from "@/lib/types";

export function ErrorDetectionView({ ed }: { ed: ErrorDetection }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Badge variant="neutral" size="sm">{ed.pattern}</Badge>
        {ed.concept_ids?.map((id) => <Badge key={id} variant="accent" size="sm">{id}</Badge>)}
      </div>
      <div className="flex flex-col gap-1">
        {ed.blocks.map((b) => (
          <div key={b.id} className={`flex items-start gap-2 rounded-(--radius-sm) border px-3 py-2 text-sm ${b.is_error ? "border-(--color-error) text-(--color-error)" : "border-(--color-border) text-(--color-ink-soft)"}`}>
            <Badge variant={b.is_error ? "error" : "neutral"} size="sm">{b.id}</Badge>
            <RichText inline>{b.content}</RichText>
          </div>
        ))}
      </div>
      <Labeled label="Hint">{ed.hint}</Labeled>
      {ed.why_prompt && <Labeled label="Why">{ed.why_prompt}</Labeled>}
      <AnswerKey>
        <Labeled label="Correct fix">{[ed.correct_answer_for_error_block, ...(ed.accepted_variants ?? [])].join(" · ")}</Labeled>
        <Labeled label="Correct fb">{ed.correct_feedback}</Labeled>
        <Labeled label="Wrong fb">{ed.wrong_correction_feedback}</Labeled>
        <Labeled label="Reveal fb">{ed.reveal_feedback}</Labeled>
      </AnswerKey>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck + commit**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → PASS
```bash
git add web/src/components/flow-v2/practice-rlc.tsx web/src/components/flow-v2/practice-error-detection.tsx
git commit -m "feat(web): RLC + error-detection review renderers"
```

---

## Task 8: CBP-mode game renderer (`flow-v2/cbp-mode-game.tsx`)

**Compact (Path B):** title + instruction + the typed `interaction_payload` (switch on `interaction_mode`) + the `why_prompt`. **No CBP shell.** Exhaustive switch so a new mode is a compile error.

**Files:**
- Create: `web/src/components/flow-v2/cbp-mode-game.tsx`

- [ ] **Step 1: Write the file**

```tsx
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import type {
  CbpModeGame, InteractionPayload, JigsawPayload, MemoryMatchPayload,
  SentenceFillPayload, TicTacToePayload,
} from "@/lib/types";

function Payload({ mode, payload }: { mode: CbpModeGame["interaction_mode"]; payload: InteractionPayload }) {
  switch (mode) {
    case "memory_match": {
      const p = payload as MemoryMatchPayload;
      return (
        <ReviewCard title="Pairs">
          <ul className="flex flex-col gap-1 text-sm text-(--color-ink-soft)">
            {p.pairs.map((pr, i) => <li key={i}><RichText inline>{pr.left}</RichText> ↔ <RichText inline>{pr.right}</RichText></li>)}
          </ul>
        </ReviewCard>
      );
    }
    case "jigsaw": {
      const p = payload as JigsawPayload;
      return (
        <ReviewCard title="Pieces">
          <ul className="flex flex-col gap-1 text-sm text-(--color-ink-soft)">
            {p.pieces.map((pc) => <li key={pc.id}><Badge variant="neutral" size="sm">{pc.id}</Badge> <RichText inline>{pc.content}</RichText></li>)}
          </ul>
          <Labeled label="Assembly types">{p.allowed_assembly_types.join(" · ")}</Labeled>
        </ReviewCard>
      );
    }
    case "sentence_fill": {
      const p = payload as SentenceFillPayload;
      return (
        <ReviewCard title="Sentence">
          <RichText className="text-sm text-(--color-ink)">{p.sentence}</RichText>
          <ul className="mt-2 flex flex-col gap-1">
            {p.chips.map((c, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-(--color-ink-soft)">
                <Badge variant={c.is_correct ? "success" : "neutral"} size="sm">{String.fromCharCode(65 + i)}</Badge>
                <span><RichText inline>{c.label}</RichText>{c.reason && <span className="text-(--color-ink-muted)"> — <RichText inline>{c.reason}</RichText></span>}</span>
              </li>
            ))}
          </ul>
        </ReviewCard>
      );
    }
    case "tictactoe": {
      const p = payload as TicTacToePayload;
      return (
        <ReviewCard title="Grid (3×3)">
          <div className="grid grid-cols-3 gap-1">
            {p.cells.map((c, i) => (
              <div key={i} className={`rounded-(--radius-sm) border p-2 text-center text-sm ${c.is_correct ? "border-(--color-success) text-(--color-success)" : "border-(--color-border) text-(--color-ink-soft)"}`}>
                <RichText inline>{c.label}</RichText>
              </div>
            ))}
          </div>
          {p.cells.some((c) => c.reason) && (
            <AnswerKey>
              {p.cells.filter((c) => c.reason).map((c, i) => <Labeled key={i} label={c.label}>{c.reason ?? ""}</Labeled>)}
            </AnswerKey>
          )}
        </ReviewCard>
      );
    }
    default: {
      const _exhaustive: never = mode;
      return _exhaustive;
    }
  }
}

export function CbpModeGameView({ game }: { game: CbpModeGame }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-(--color-ink)">{game.title}</span>
        <Badge variant="neutral" size="sm">{game.interaction_mode}</Badge>
        {game.source_concept_ids?.map((id) => <Badge key={id} variant="accent" size="sm">{id}</Badge>)}
      </div>
      <RichText className="text-sm leading-relaxed text-(--color-ink-soft)">{game.instruction}</RichText>
      <Payload mode={game.interaction_mode} payload={game.interaction_payload} />
      <ReviewCard title="Explain your reasoning">
        <RichText className="text-sm text-(--color-ink)">{game.why_prompt}</RichText>
        {game.expected_reasoning_keywords && game.expected_reasoning_keywords.length > 0 && (
          <AnswerKey><Labeled label="Keywords">{game.expected_reasoning_keywords.join(" · ")}</Labeled></AnswerKey>
        )}
      </ReviewCard>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → PASS
```bash
git add web/src/components/flow-v2/cbp-mode-game.tsx
git commit -m "feat(web): compact CBP-mode game review renderer"
```

---

## Task 9: Phase registry (`lib/flow-v2-phases.tsx`)

One ordered table the preview iterates. Each entry knows its `Job` column, title, division, an `isEmpty` guard, and a `render` thunk. Mirrors `flow_for()` order (no `reading`; one game per subject — list all four, `isEmpty` hides the null ones).

**Note the `.tsx` extension** — the render thunks use JSX, so it cannot be `.ts`. Imports elsewhere still write `@/lib/flow-v2-phases` (no extension).

**Files:**
- Create: `web/src/lib/flow-v2-phases.tsx`

- [ ] **Step 1: Write the file**

```tsx
import type { ReactNode } from "react";
import { SourceMapView } from "@/components/flow-v2/source-map";
import { CaseBasedPreviewView } from "@/components/flow-v2/case-based-preview";
import { MemoryCheckView } from "@/components/flow-v2/memory-check";
import { BossArenaView } from "@/components/flow-v2/boss-arena";
import { RealLifeChallengeView } from "@/components/flow-v2/practice-rlc";
import { ErrorDetectionView } from "@/components/flow-v2/practice-error-detection";
import { CbpModeGameView } from "@/components/flow-v2/cbp-mode-game";
import { FlashcardDeck } from "@/components/flashcards/flashcard-deck";
import type {
  BossArena, CaseBasedPreview, CbpModeGame, ErrorDetection, FlashcardsPack,
  Job, MemoryCheckPack, RealLifeChallenge,
} from "@/lib/types";

export type Division = "Learning Sections" | "Practice Arc" | "Boss Arena";

export interface FlowV2PhaseDef {
  key: string;
  column: keyof Job;
  title: string;
  division: Division;
  isEmpty: (data: unknown) => boolean;
  render: (data: any) => ReactNode;
}

const emptyArr = (k: string) => (d: unknown) => !d || !(d as Record<string, unknown[]>)[k]?.length;
const gameDef = (title: string, column: keyof Job): FlowV2PhaseDef => ({
  key: column as string, column, title, division: "Practice Arc",
  isEmpty: (d) => !d,
  render: (d: CbpModeGame) => <CbpModeGameView game={d} />,
});

export const FLOW_V2_PHASES: FlowV2PhaseDef[] = [
  { key: "cbp", column: "cbp_json", title: "Case-Based Preview", division: "Learning Sections",
    isEmpty: (d) => !d, render: (d: CaseBasedPreview) => <CaseBasedPreviewView cbp={d} /> },
  { key: "flashcards", column: "flashcards_json", title: "Flashcards", division: "Learning Sections",
    isEmpty: emptyArr("cards"), render: (d: FlashcardsPack) => <FlashcardDeck cards={d.cards ?? []} /> },
  { key: "memory-check", column: "memory_check_json", title: "Memory Check", division: "Learning Sections",
    isEmpty: emptyArr("items"), render: (d: MemoryCheckPack) => <MemoryCheckView pack={d} /> },
  { key: "practice-rlc", column: "practice_rlc_json", title: "Real-Life Challenge", division: "Practice Arc",
    isEmpty: (d) => !d, render: (d: RealLifeChallenge) => <RealLifeChallengeView rlc={d} /> },
  { key: "practice-error-detection", column: "practice_error_detection_json", title: "Error Detection", division: "Practice Arc",
    isEmpty: (d) => !d, render: (d: ErrorDetection) => <ErrorDetectionView ed={d} /> },
  gameDef("Memory Match", "practice_memory_match_json"),
  gameDef("TicTacToe", "practice_tictactoe_json"),
  gameDef("Jigsaw Matching", "practice_jigsaw_json"),
  gameDef("Sentence Filling", "practice_sentence_json"),
  { key: "boss-arena", column: "boss_arena_json", title: "Boss Arena", division: "Boss Arena",
    isEmpty: emptyArr("questions"), render: (d: BossArena) => <BossArenaView boss={d} /> },
];

export const DIVISION_ORDER: Division[] = ["Learning Sections", "Practice Arc", "Boss Arena"];

/** A job is Flow v2 if any Flow v2 column is populated. */
export function isFlowV2(job: Job): boolean {
  return Boolean(
    job.cbp_json || job.boss_arena_json || job.source_map_json ||
    job.memory_check_json || job.practice_rlc_json || job.practice_error_detection_json ||
    job.practice_memory_match_json || job.practice_tictactoe_json ||
    job.practice_jigsaw_json || job.practice_sentence_json,
  );
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → PASS
```bash
git add web/src/lib/flow-v2-phases.tsx
git commit -m "feat(web): flow-v2 phase registry"
```

---

## Task 10: Rewire `routes/preview.tsx`

Detect Flow v2; if so render Source Map + the registry phases grouped by division, plus the assembled-md Reflection tail; else fall back to the EXISTING legacy render unchanged.

**Files:**
- Modify: `web/src/routes/preview.tsx`

- [ ] **Step 1: Add imports** (top of file, with the other component imports)

```tsx
import { SourceMapView } from "@/components/flow-v2/source-map";
import { DIVISION_ORDER, FLOW_V2_PHASES, isFlowV2 } from "@/lib/flow-v2-phases";
```

- [ ] **Step 2: Extract the existing legacy body into a helper.** Rename the current `return (…)` JSX block (the `<>…</>` starting at the existing line ~192) into a function `LegacyPreview({ job, id }: { job: Job; id: string })` that returns that same JSX. Import `Job`: `import type { Job } from "@/lib/types"`. Keep `segments`/`MD_COMPONENTS` with it (move `segments` useMemo into `LegacyPreview`, since only the legacy path uses heading-splicing).

- [ ] **Step 3: Add the Flow v2 view function**

```tsx
function FlowV2Preview({ job }: { job: Job }) {
  const byDivision = DIVISION_ORDER.map((div) => ({
    div,
    phases: FLOW_V2_PHASES.filter((p) => p.division === div).filter((p) => {
      const data = job[p.column];
      return data != null && !p.isEmpty(data);
    }),
  })).filter((g) => g.phases.length > 0);

  return (
    <article className="mt-8 flex flex-col gap-10">
      {job.source_map_json && (
        <section>
          <h2 className="mb-4 text-xl font-semibold tracking-tight text-(--color-ink)">Source Map</h2>
          <SourceMapView map={job.source_map_json} />
        </section>
      )}
      {byDivision.map(({ div, phases }) => (
        <section key={div}>
          <h2 className="mb-4 text-xl font-semibold tracking-tight text-(--color-ink)">{div}</h2>
          <div className="flex flex-col gap-6">
            {phases.map((p) => (
              <div key={p.key}>
                <h3 className="mb-2 text-base font-semibold text-(--color-ink)">{p.title}</h3>
                <div className="rounded-(--radius-lg) border border-(--color-border) bg-(--color-elevated) p-5">
                  {p.render(job[p.column])}
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </article>
  );
}
```

- [ ] **Step 4: Branch in `PreviewPage`'s return.** Keep the existing header (back link + download + provider line). After it, swap the article block for:

```tsx
      {isFlowV2(job) ? <FlowV2Preview job={job} /> : <LegacyPreview job={job} id={id ?? ""} />}
```

(The `isLoading` / `!job.assembled_md` guards stay as-is. Note: a Flow v2 job always has `assembled_md`, so the existing guard is fine.)

- [ ] **Step 5: Typecheck + lint**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → PASS
Run: `cd web && npm run lint` → PASS (fix any Biome complaints — e.g. array index keys already use the `biome-ignore` pattern seen in the file)

- [ ] **Step 6: Commit**

```bash
git add web/src/routes/preview.tsx
git commit -m "feat(web): registry-driven Flow v2 preview, legacy fallback retained"
```

---

## Task 11: Build + live acceptance

**Files:** none (verification only)

- [ ] **Step 1: Production build**

Run: `cd web && npm run build`
Expected: build succeeds, writes `web/dist/`.

- [ ] **Step 2: Live check.** Start Postgres + API (`.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000`) and `cd web && npm run dev`. Generate one job (geometry book `a4fa550f…`, provider `claude`) and open `/preview/:id`. Confirm: Source Map, Case-Based Preview (3 checkpoints + 2 learning blocks + DPE + simulation), Memory Check (with pass threshold), the Practice Arc (RLC + Error Detection + the one subject-matched game — board + why_prompt), Boss Arena (Why/How/What), each as a titled review card with answer keys visible, in flow order. Then open an OLD legacy job and confirm it still renders via `LegacyPreview` unchanged.

- [ ] **Step 3: Final commit** (if any lint/polish fixes were needed)

```bash
git add -A web/
git commit -m "chore(web): Flow v2 preview polish after live check"
```

---

## Notes for the executor
- **Re-confirm the schemas before Task 1** — introspect `app/schemas/` (`CbpModeGame`, `CaseBasedPreview`, `MemoryCheckPack`, `BossArena`, `Flashcard`) to be sure nothing drifted since 2026-06-01. We've been burned by staleness twice.
- **`api.getJob` already returns `Job`** (used in `preview.tsx`) — adding columns to the `Job` interface flows through with no API-client change. Verify the backend `JobOut` serializes the columns (it does as of tip `136d63a`).
- **Branch:** work on `Nggaev-v2` (or a feature branch off it). `web/` is this work's ownership; avoid backend files.
- **No new routes, no data-fetching changes, no student interactivity.** If a task tempts you toward state/scoring, stop — it's out of scope.
