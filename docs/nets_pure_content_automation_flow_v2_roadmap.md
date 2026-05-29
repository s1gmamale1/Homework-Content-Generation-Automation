# NETS Pure Content Automation — Flow v2 Roadmap

**Status:** Pure content automation. No runtime, no platform.
**Date:** 2026-05-29
**Repo in scope:** the content generator only.
**Companion:** `nets_pure_content_automation_flow_v2_plan.md`

---

## 0. Scope correction

This is a **content factory**: book in, Flow v2 content out, Markdown handoff, content QA. Nothing downstream blocks this team.

**In scope (content only):**
- Book / textbook input
- Chapter + section extraction
- Source map (factual anchor)
- Flow v2 phase/game prompt execution from the Infra registry
- Generated content
- Final `homework-content.md`
- Content QA against Flow v2 specs

**Out of scope (deleted):**
- Homeworks runtime / student screen
- Platform PR-0 prerequisite
- CBP runtime flag · Unlock Gate enforcement
- Beta-platform-safe export
- Browser answer stripping
- Platform `ContentJSON` conformance
- Legacy runtime compatibility

**Kept on purpose** (content rules in platform clothing, reframed to content-spec): Decision Process Explanation (CBP v1.1), source fidelity, and "each game conforms to its Infra spec."

---

## 1. The content chain

```txt
ingest          book → locate chapter + section
   ↓
extract         section content → normalized text
   ↓
source-map      concepts · formulas · terms pinned (IDs) · no-invention check
   ↓
parallel ∥      CBP · flashcards · RLC · error-detection · memory-matching
                tictactoe · jigsaw · sentence-filling · boss draft
   ↓
memory-check    items reference flashcard IDs · 60%
   ↓
reflection      pass / needs-retry · weak_point_signals → teacher · parent · admin
   ↓
assemble        homework-content.md + content_package + flow_manifest
   ↓
qa              vs Flow v2 specs
```

The generator emits content artifacts only. It never produces frontend or runtime code as part of a homework job.

---

## 2. Phases (no Phase 0)

| Phase | Name | Depends on |
|---|---|---|
| 1 | AI Gateway + Flow v2 schemas | none |
| 2 | Ingestion + SourceMap + Infra registry | Phase 1 |
| 3 | Learning Sections (CBP · Flashcards · Memory Check) | SourceMap; Memory Check ← Flashcards |
| 4 | Practice games (6 types) | SourceMap only — fully parallel |
| 5 | Boss Arena | SourceMap (skill map ideal) |
| 6 | Reflection + Markdown assembly | all generated sections |
| 7 | Content QA | final Markdown + source map |

### Phase 1 — AI Gateway + Flow v2 schemas
- Gateway · orchestrator · providers (task→model policy, repair retry).
- Canonical Flow v2 content schemas including required `decision_process_explanation`.
- `GenerationProfile` — difficulty as metadata, not branch-skipping.
- **Acceptance:** structured call validates; repair retry works; provider fallback unit-tested.

### Phase 2 — Ingestion + SourceMap + Infra registry
- Book ingestion + chapter/section locator + extraction + normalize.
- `source_map` + no-invention check; concepts get IDs.
- Infra prompt registry — load by subject family + phase/game; never invent.
- Record prompt path/version/hash in `flow_manifest`.
- **Acceptance:** source_map per job; every enabled phase/game resolves a registry prompt; prompts traceable.

### Phase 3 — Learning Sections
- Case-Based Preview: 3 recognition checkpoints + DPE (slot 7, before consequence).
- Flashcards with stable IDs.
- Memory Check referencing flashcard IDs; item types per the content spec.
- **Acceptance:** exactly 3 checkpoints + DPE; MC refs IDs at 60%.

### Phase 4 — Practice games
- RLC · Error Detection · Memory Matching · TicTacToe · Jigsaw · Sentence Filling.
- Each mission maps to a target skill from SourceMap.
- RLC reverse-test (infer the unnamed formula) authored per the RLC spec.
- **Acceptance:** every game conforms to its Infra spec; no disconnected drills; traces to concepts.

### Phase 5 — Boss Arena
- Why → How → What encounters from the Boss Arena spec.
- Not flashcard recall, not a quiz-plus-HP wrapper.
- Can draft in parallel; ideally uses the practice skill map.
- **Acceptance:** every encounter has Why→How→What; reasoning preserved.

### Phase 6 — Reflection + Markdown assembly
- Reflection / Marking (Passed · Needs Retry).
- `weak_point_signals` → teacher / parent / admin.
- Assemble `homework-content.md` + `flow_manifest`.
- **Acceptance:** Markdown mirrors the package, includes every enabled game, no implementation code.

### Phase 7 — Content QA
- Registry coverage · source fidelity · DPE present.
- Each phase/game conforms to its Infra spec.
- Markdown complete · no invented content · no implementation code.
- **Acceptance:** `qa_report.json` green; QA Notes written into the Markdown.

A lightweight content preview / QA console (the existing generator frontend, de-platformed) is optional supporting tooling, not a blocking phase.

---

## 3. Dependency & parallelization

After the source map, almost everything runs in parallel. The only shared contract a teammate needs to begin is the **mock SourceMap shape** plus the expected Markdown section format.

| Part | Depends on | Start now? |
|---|---|---|
| Case-Based Preview | SourceMap only | yes (mock) |
| Flashcards | SourceMap only | yes (mock) |
| Real-Life Challenge | SourceMap only | yes (mock) |
| Error Detection | SourceMap only | yes (mock) |
| Memory Matching | SourceMap only | yes (mock) |
| TicTacToe | SourceMap only | yes (mock) |
| Jigsaw Matching | SourceMap only | yes (mock) |
| Sentence Filling | SourceMap only | yes (mock) |
| Boss Arena | SourceMap; skill map ideal | draft (mock) |
| Memory Check | Flashcards | waits |
| Reflection / Marking | all generated sections | waits |
| Markdown assembly | all generated sections | waits |
| Content QA | final Markdown + source map | waits |

**Start in parallel now:** Phase 1 and Phase 2 for the spine; the eight isolated generators (CBP, Flashcards, six games, Boss draft) on a mock SourceMap. Only Memory Check, Reflection, assembly, and QA wait.

---

## 4. Infra prompt registry

The pinned `Infra.zip` is the phase/game prompt/spec registry. Load by subject family + phase/game type.

```txt
Infra/Flow/                          New_Flow, nets_homework_flow_v2
Infra/Case-Based Preview/            math_family, sciences, languages, CBP standard
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

**Rules:**
- Don't invent a phase/game prompt when a registry file exists.
- Select prompt by subject family + game type.
- Record prompt path + version/source hash in `flow_manifest.json`.
- Fail content QA if any enabled phase/game has no prompt/spec mapping.

Implementation: pinned copied prompt pack + sync script (simplest for agents). The CBP v1.1 content standard is pinned alongside as the CBP source of truth.

---

## 5. Case-Based Preview content rule (the one that survived)

A content standard (CBP v1.1), not a runtime feature.

Required order:

```txt
1 case_setup
2 checkpoint_1
3 learning_block_1
4 checkpoint_2
5 learning_block_2
6 checkpoint_3
7 decision_process_explanation   ← required, before consequence
8 final_simulation
9 feedback_summary
10 completion_rules
```

- Checkpoints 1–3 are low-friction recognition/decision checks (`identify · decide · justify_or_avoid_mistake`) as mcq/choice/short_select.
- Production reasoning lives in the DPE — open-ended, evaluating concept · method · mistake, never an MCQ.
- The method/formula stays unnamed in the case body; the student commits first.

**QA hard-fails:** DPE missing · DPE has options · DPE passable in one word · DPE after the consequence · source concept not preserved · student not the decision-maker.

---

## 6. The deliverable — outputs per job

```txt
content_package.json     canonical Flow v2 content
homework-content.md      human handoff (mirrors the package, content only)
flow_manifest.json       prompt paths/versions/hashes, model usage, generation order
qa_report.json           content QA results
```

`homework-content.md` structure:

```md
# Homework Content
## Source Book / Chapter / Section
## Extracted Section Summary
## Source Map
## Learning Sections
### Case-Based Preview (Case Setup · Checkpoint 1 · Learning Block 1 · Checkpoint 2 ·
###   Learning Block 2 · Checkpoint 3 · Decision Process Explanation ·
###   Final Consequence / Simulation · Feedback Summary)
### Flashcard Learning
### Memory Check
## Progression Criteria          # descriptive pass thresholds, not enforced
## Practice Arc
### Real-Life Challenge · Error Detection · Memory Matching · TicTacToe ·
###   Jigsaw Matching · Sentence Filling · Other Enabled Practice Games
## Boss Arena
## Reflection / Marking
## Weak Point Signals
## QA Notes
```

**Content convention:** answer keys, rubrics and teacher notes are labeled distinctly from student-visible text so a downstream consumer can split them. Content hygiene, not a security control.

---

## 7. Content QA fence

QA validates content against Flow v2 specs — not platform compatibility. Any of these stops the job:

| Reject / flag when | Why |
|---|---|
| DPE missing, or modeled as a 4th MCQ | CBP v1.1 violation |
| CBP order wrong, or DPE after the consequence | CBP v1.1 violation |
| A generated phase/game has no Infra prompt/spec mapping | would mean invented content |
| Book/chapter/section extraction returned unrelated content | grounded in wrong source |
| `source_map` missing for any generated phase | fidelity risk |
| A generated phase/game doesn't match its Infra spec | spec drift |
| Boss Arena lacks Why → How → What | quiz skin |
| Markdown export missing, incomplete, or contains code | handoff unusable |
| Generated content not traceable to source concept IDs | fidelity risk |

---

## 8. The one rule

```txt
Book in. Flow v2 content out. Markdown handoff. Content QA.
No runtime. No platform. No export gymnastics.
```
