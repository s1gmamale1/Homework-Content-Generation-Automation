# NETS Pure Content Automation — Flow v2 Transformation Plan

**Status:** Rewritten to correct scope. This is a **pure content-automation** plan.
**Supersedes:** `nets_generator_flow_v2_transformation_plan_FINAL_PATCHED.md` and every earlier version that mixed in Homeworks runtime/platform concerns.
**Date:** 2026-05-29
**Repo in scope:** the content generator only (`Homework-Content-Generation-Automation`).

---

## 0. Scope correction (read this first)

This project is a **content factory**. It takes a textbook and produces Flow v2 homework **content**: structured JSON plus a human-readable Markdown handoff. It does **not** build, render, gate, or export to any runtime.

**This plan is NOT about, and contains nothing about:**

- Homeworks runtime / student screen
- server gates or Unlock Gate enforcement
- Practice Arc locking at runtime
- browser answer stripping / injector behavior
- beta-platform-safe export
- platform schema (Pydantic `ContentJSON`) conformance
- legacy runtime compatibility
- any "Platform PR-0" prerequisite or runtime readiness flag

All of those were the wrong scope and have been removed. Nothing in the runtime world blocks this team.

**This plan IS only:**

```txt
book / textbook
  → locate chapter + section
  → extract + normalize section content
  → build source map
  → map source concepts to Flow v2 divisions
  → run the correct prompt per phase/game (from the Infra registry)
  → generate content
  → assemble homework-content.md
  → content QA against Flow v2 specs
```

Three content rules survived the scope cut **because they are content standards, not platform rules** (flagged here so nobody deletes them by accident):

1. **Decision Process Explanation (DPE)** in Case-Based Preview — required by `nets_case_based_preview_generation_standard_v1_1.md`.
2. **Source fidelity** — every generated phase/game traces back to extracted source concepts; no invented facts.
3. **Spec conformance** — each phase/game matches its **Infra content spec** (not a platform schema).

---

## 1. What the generator does

A single generation job runs this chain and nothing more:

```txt
extract_book_section
        ↓
build_source_map
        ↓
parallel generation (all depend on SourceMap only):
  case_based_preview
  flashcards
  real_life_challenge
  error_detection
  memory_matching
  tictactoe
  jigsaw_matching
  sentence_filling
  boss_arena (draft)
        ↓
memory_check            # depends on flashcards
        ↓
reflection_marking      # depends on all generated sections
        ↓
assemble homework-content.md
        ↓
content_qa
```

The generator emits **content artifacts only**. It must never generate frontend or runtime code as part of a homework job.

---

## 2. Outputs per job

Every job produces exactly these:

```txt
1. content_package.json     # canonical Flow v2 content
2. homework-content.md       # human-readable handoff (mirrors the package)
3. flow_manifest.json        # prompt paths/versions/hashes, model usage, generation order
4. qa_report.json            # content QA results
```

There is **no** "beta-safe export" and **no** "future runtime export." Those layers are deleted. Runtime is somebody else's project; if a runtime ever wants this content, that is a separate downstream adapter, not part of this plan.

---

## 3. Book ingestion → extraction → SourceMap → Flow v2 mapping

The generator must not assume pasted text is the only input path.

Required input chain:

```txt
Book / PDF / textbook source
  → locate chapter
  → locate section
  → extract section content
  → normalize extracted text
  → build source_map
  → map source concepts into Flow v2 divisions/phases
```

Required extraction outputs:

```json
{
  "book_source": {},
  "chapter": {},
  "section": {},
  "extracted_section_content": {},
  "source_map": {}
}
```

The `source_map` is the factual anchor. Every generated phase/game must reference the relevant source concept IDs. If extraction returns content unrelated to the requested chapter/section, the whole job is grounded in the wrong source → **stop and re-extract**.

---

## 4. Dependency graph and parallelization

After SourceMap, almost everything runs in parallel. The team can start isolated generators immediately using a **mock SourceMap** — the only shared contract they need is the mock SourceMap shape plus the expected Markdown section format.

| Part | Depends on |
|---|---|
| Case-Based Preview | SourceMap only |
| Flashcards | SourceMap only |
| Real-Life Challenge | SourceMap only |
| Error Detection | SourceMap only |
| Memory Matching | SourceMap only |
| TicTacToe | SourceMap only |
| Jigsaw Matching | SourceMap only |
| Sentence Filling | SourceMap only |
| Boss Arena | SourceMap; ideally the practice skill map, but can draft in parallel |
| Memory Check | Flashcards |
| Reflection / Marking | all generated sections |
| Markdown assembly | all generated sections |
| Content QA | final Markdown + source map |

**Start-now tasks (mock SourceMap):** Case-Based Preview, Flashcards, every Practice game, Boss Arena draft. Only Memory Check waits for Flashcards; only Reflection and final assembly wait for everything.

---

## 5. Infra prompt registry

The uploaded `Infra.zip` is the phase/game prompt/spec registry. The generator loads the correct prompt by **subject family + phase/game type**.

Registry structure:

```txt
Infra/Flow/                          New_Flow.md, nets_homework_flow_v2
Infra/Case-Based Preview/            math_family, sciences, languages, CBP standard v1.x
Infra/Flashcards/Flashcard Prompts/  math_family, sciences, languages, humanities
Infra/Flashcards/Quzilet Learning/   Multiple Choice, Fill in the blank, Choose Correct Explanation
Infra/Gamified Practices/Real Life Challenge/
Infra/Gamified Practices/Error Detection/
Infra/Gamified Practices/Memory Matching/
Infra/Gamified Practices/TicTacToe/
Infra/Gamified Practices/Jigsaw Matching/
Infra/Gamified Practices/Sentence Filling/
Infra/Gamified Practices/Boss Arena/
Infra/Uzbek Specification/           NETS_Uzbek_Language_Foundation_Review
```

Registry rules:

```txt
Do not invent a phase/game prompt when a registry file exists.
Select prompt by subject family and game type.
Record prompt path + version/source hash in flow_manifest.json.
Fail content QA if any enabled phase/game has no prompt/spec mapping.
```

Implementation: pinned copied prompt pack + a sync script is simplest for agents (avoids submodule weirdness). The CBP **content standard** (`nets_case_based_preview_generation_standard_v1_1.md`) is a project standard and is pinned alongside the registry as the source of truth for CBP.

---

## 6. Case-Based Preview content spec

CBP is the load-bearing learning section. Its structure is a **content** requirement (CBP v1.1), independent of any runtime.

Canonical shape:

```python
class DecisionProcessExplanation(BaseModel):
    prompt: str
    expected_components: list[Literal["concept", "method", "mistake"]]
    rubric: dict
    sample_acceptable_answer: str
    eval_mode: Literal["ai", "rubric_ai"] = "ai"
    min_chars: int = 60
    options: None = None        # never an MCQ

class CaseBasedPreview(BaseModel):
    title: str
    student_role: str
    case_type: str
    source_concept_ids: list[str]
    case_setup: CaseSetup
    checkpoints: list[CaseCheckpoint]            # exactly 3
    decision_process_explanation: DecisionProcessExplanation
    final_simulation: CaseSimulation
    feedback_summary: FeedbackSummary
    completion_rules: CompletionRules
```

Required order:

```txt
1 case_setup
2 checkpoint_1
3 learning_block_1
4 checkpoint_2
5 learning_block_2
6 checkpoint_3
7 decision_process_explanation     ← required, before consequence
8 final_simulation
9 feedback_summary
10 completion_rules
```

Checkpoint design:

```txt
Checkpoints 1–3 are low-friction recognition / decision checks.
The production reasoning lives in decision_process_explanation.
Allowed checkpoint intents: identify · decide · justify_or_avoid_mistake.
Allowed forms: mcq · choice · short_select · true_false (only if meaningful).
Not allowed: a 4th checkpoint · checkpoint-as-essay · checkpoint with no consequence · unrelated trivia.
The method/formula is left unnamed in the case body; the student commits before the consequence.
```

QA hard-fails (reject the CBP):

```txt
decision_process_explanation missing
decision_process_explanation has options
decision_process_explanation passable in one word
decision_process_explanation appears after final_simulation
final_simulation missing correct/wrong paths
student is not the decision-maker
source/textbook concept not preserved
```

---

## 7. Flashcards + Memory Check

**Flashcards** — generated from SourceMap, carry **stable IDs** so Memory Check can reference them.

**Memory Check** — depends on Flashcards; items reference flashcard IDs; pass threshold 60% (content design). Item types follow the **Flow v2 content spec / Quizlet-Learning Infra specs** (Multiple Choice, Fill in the blank, Choose Correct Explanation, and any others the spec enables). There is no platform "3-types-only" restriction here — that was a runtime-export constraint and is gone. Use whatever item types the content spec defines.

---

## 8. Practice games content

Replace the old `game-breaks` / standalone `real-life` / standalone `consolidation` with conceptual practice tied to target skills. Each game is generated from its **Infra spec** and references SourceMap concept IDs.

| Game | Source of truth |
|---|---|
| Real-Life Challenge | `Infra/.../Real Life Challenge/` |
| Error Detection | `Infra/.../Error Detection/` |
| Memory Matching | `Infra/.../Memory Matching/` |
| TicTacToe | `Infra/.../TicTacToe/` |
| Jigsaw Matching | `Infra/.../Jigsaw Matching/` |
| Sentence Filling | `Infra/.../Sentence Filling/` |

Rules:

- Every mission maps to a target skill drawn from SourceMap; no disconnected drills.
- Each game conforms to its Infra spec — that is the content contract (no platform validator involved).
- **Real-Life Challenge** follows the RLC Infra specification. The **reverse-test** variant (same story, new numbers, student infers the unnamed formula) is authored directly per the content spec — there is no "5-step platform bridge" caveat anymore; that was platform clothing and is deleted.

---

## 9. Boss Arena content

Boss Arena is the mastery peak inside the Practice arc. It is **reasoning content**, not a quiz skin.

```txt
Every Boss encounter carries Why → How → What reasoning.
Not flashcard recall, not a quiz-plus-HP wrapper.
Can be drafted in parallel from SourceMap; ideally uses the practice skill map once available.
Generated from Infra/.../Boss Arena/ spec.
```

---

## 10. Reflection / Marking + Weak Point Signals

Reflection/Marking produces the outcome content (Passed · Needs Retry) and the weak-point data layer.

```python
class WeakPointSignal(BaseModel):
    concept_id: str
    source_phase: str
    evidence: list[dict]
    severity: Literal["low", "medium", "high"]
    target_accounts: list[Literal["teacher", "parent", "admin"]]
    recommended_action: str
```

Reflection output includes `weak_point_signals: []`. Purpose: sub-threshold attempts become **needs-attention signals**, not just local feedback text. This is content/data output, not a runtime feature.

---

## 11. Markdown content export

Every job produces `homework-content.md` — the human handoff. It mirrors the canonical package, contains **generated content only** (no implementation instructions, no frontend code), and includes every enabled game.

```md
# Homework Content

## Source Book / Chapter / Section
## Extracted Section Summary
## Source Map

## Learning Sections
### Case-Based Preview
#### Case Setup
#### Checkpoint 1
#### Learning Block 1
#### Checkpoint 2
#### Learning Block 2
#### Checkpoint 3
#### Decision Process Explanation
#### Final Consequence / Simulation
#### Feedback Summary
### Flashcard Learning
### Memory Check

## Progression Criteria          # content-level pass notes (CBP ≥2/3, Memory Check ≥60%) — descriptive, not enforced
## Practice Arc
### Real-Life Challenge
### Error Detection
### Memory Matching
### TicTacToe
### Jigsaw Matching
### Sentence Filling
### Other Enabled Practice Games

## Boss Arena
## Reflection / Marking
## Weak Point Signals
## QA Notes
```

Note: the old "Unlock Gate Requirements" heading is renamed **Progression Criteria** and is purely descriptive content (what the pass thresholds are), not server enforcement.

**Content-output convention (replaces the deleted answer-stripping rule):** in both the JSON package and the Markdown, clearly label answer keys, rubrics, and teacher-only notes as distinct from student-visible text, so a downstream consumer can separate them. This is content hygiene, not a security control.

---

## 12. GenerationProfile (difficulty)

Drop the old Easy/Hard phase-skipping. Keep difficulty as **generation metadata**:

```txt
GenerationProfile { difficulty, grade_band, target_skills, subject_family, ... }
```

Difficulty changes the depth/complexity of generated items; it does not skip proving learning and does not branch the phase list.

---

## 13. Content QA (the fence)

QA validates **content**, not platform compatibility. Reject/flag a job when:

```txt
DPE missing or modeled as a 4th MCQ                       (CBP v1.1 violation)
CBP order wrong or DPE after the consequence
a generated phase/game has no Infra prompt/spec mapping   (would mean invented content)
book/chapter/section extraction returned unrelated content
source_map missing for any generated phase
a generated phase/game does not match its Infra spec
Boss Arena lacks Why → How → What
Markdown export missing, incomplete, or contains implementation code
generated content not traceable to source concept IDs
```

QA writes results to `qa_report.json` and `## QA Notes` in the Markdown.

---

## 14. Implementation sequence (no Phase 0)

No platform prerequisite. Phases are content-generator work only; most can start in parallel against a mock SourceMap.

```txt
Phase 1  AI Gateway + Flow v2 content schemas (incl. DPE, GenerationProfile)
Phase 2  Book ingestion + chapter/section extraction + SourceMap + Infra prompt registry
Phase 3  Learning Sections — CBP (with DPE), Flashcards (stable IDs), Memory Check (after Flashcards)
Phase 4  Practice games — RLC, Error Detection, Memory Matching, TTT, Jigsaw, Sentence Filling
Phase 5  Boss Arena — Why → How → What
Phase 6  Reflection / Marking + weak-point signals + Markdown assembly
Phase 7  Content QA (registry coverage, source fidelity, DPE present, spec conformance, MD complete)
```

**Start in parallel now:** Phase 1 (schema/gateway) and Phase 2 (ingestion + registry). Isolated phase/game generators (Phase 3–5) can begin immediately on a mock SourceMap. Final assembly (Phase 6) and QA (Phase 7) close the loop once sections exist.

A lightweight content **preview / QA console** (the existing generator frontend, de-platformed) is optional supporting tooling, not a blocking phase.

---

## 15. What was removed and why

| Removed | Reason |
|---|---|
| Platform PR-0 / Homeworks runtime prerequisite | runtime, not content |
| CBP runtime readiness flag | runtime gate |
| Unlock Gate server enforcement | runtime gate (kept only as descriptive "Progression Criteria" content) |
| Beta-platform-safe export layer | platform export, not content |
| Future full-v2 runtime export layer | platform export |
| Platform `ContentJSON` conformance test | platform schema, replaced by Infra-spec conformance |
| Memory Check "3 types only" | platform-export constraint; item types now follow the content spec |
| RLC "strict 5-step platform contract" + reverse-test bridge caveat | platform validator; RLC now follows its Infra spec directly |
| Browser answer stripping / injector server-only | runtime security; replaced by content-level answer/teacher labeling |
| Legacy runtime compatibility | runtime concern |
| "Three output layers" | collapsed to canonical package + Markdown + manifest + QA |
| Claims A–F platform verification framing | platform reverse-engineering; irrelevant to content |

---

## 16. The one rule

```txt
Book in. Flow v2 content out. Markdown handoff. Content QA.
No runtime. No platform. No export gymnastics.
```
