# NETS Pure Content Automation — Flow v2 Roadmap

**Status:** Grounded in the real generator (`Homework-Content-Generation-Automation` @ `DaddysBranch`).
**Date:** 2026-05-29
**Companion:** `nets_pure_content_automation_flow_v2_plan.md`

---

## 0. Reality anchor

The generator already exists and is mature. Flow v2 is a **phase-set swap on existing infrastructure**, not a rebuild. Don't build a gateway, a parallel pipeline, structured-output handling, a queue, or a "registry" — they're done. Author the Flow v2 **prompts + schemas** and wire them into the flow tables.

---

## 1. Already built — reuse, don't rebuild

| Capability | Where | Status |
|---|---|---|
| Provider router (CLI, no SDKs) | `agent.py` + `providers/{claude,codex,gemini,kimi}.py` | done |
| Per-job model selection | `agent_models.MODEL_MANIFEST` | done |
| DAG-parallel generation | `pipeline.py` + `flows.PHASE_DEPS` | done |
| Structured output + retry-once | `agent.STRUCTURED_PHASE_SCHEMAS` | done |
| Extract + cross-job reuse | `toc_extractor.py` (pinned gemini) | done (flat summary — upgrade in PR-1) |
| Markdown packet + JSON columns | `pipeline.py` assembly | done (old sections — reshape in PR-5) |
| Queue / worker / usage / auth / Postgres | `worker.py`, `agent_usages`, `phase_outputs` | done |
| Prompt registry | `prompts/<subject>/*.md` + `flows.SUBJECT_FLOWS` | done — this *is* the registry |

Providers are `claude / codex / gemini / kimi` CLIs. No `Infra/` folder exists in the repo; the prompt registry is `prompts/<subject>/`.

---

## 2. The Flow v2 delta

| Legacy (today) | Flow v2 |
|---|---|
| `extract` (flat summary) | upgrade → structured source map (concept IDs) |
| `preview-easy` / `preview-hard` | Case-Based Preview (+ Decision Process Explanation) |
| `flashcards` | keep, stable IDs |
| `memory-sprint` | Memory Check (refs flashcard IDs, 60%) |
| `game-breaks` (generic) | 6 named games: RLC · Error Detection · Memory Matching · TicTacToe · Jigsaw · Sentence Filling |
| `real-life` | folds into Practice as RLC |
| `consolidation` | redistribute (optional) |
| `final-challenge` | Boss Arena (Why → How → What) |
| `reflection` | dropped |
| assembly | reshape to Flow v2 sections |

---

## 3. The mechanism — adding one Flow v2 phase

Proven by every legacy phase. Five local edits, no new infra:

```txt
1. prompts/<subject>/<phase>.md          # prompt, authored from spec
2. app/schemas/<phase>.py                # Pydantic output schema
3. agent.STRUCTURED_PHASE_SCHEMAS[...]   # register phase → schema (JSON mode)
4. flows.SUBJECT_FLOWS[...]              # insert into easy/hard sequence
5. flows.PHASE_DEPS[...]                 # declare prior-output deps
```

The scheduler, retry, usage tracking, and assembly pick it up automatically.

---

## 4. PRs (no gateway/registry PR — those exist already)

```txt
PR-1  Source map      upgrade extract → structured concepts + SourceMap schema
PR-2  Learning        Case-Based Preview (+DPE) · flashcards stable IDs · memory-check (replaces memory-sprint)
PR-3  Practice games  RLC · Error Detection · Memory Matching · TicTacToe · Jigsaw · Sentence Filling
PR-4  Boss Arena      replace final-challenge with Why→How→What
PR-5  Assembly        reshape markdown to Flow v2 sections; retire legacy section names
```

PR-2/3/4 generators run in parallel under the existing scheduler and can be developed against a mock source map. Legacy flow keeps working until new sequences replace it per subject.

---

## 5. Case-Based Preview content rule (the one that stays)

CBP v1.1 content standard. Required order:

```txt
1 case_setup
2 checkpoint_1        (recognition: identify / decide / justify_or_avoid_mistake)
3 learning_block_1
4 checkpoint_2
5 learning_block_2
6 checkpoint_3
7 decision_process_explanation   ← required, before the consequence, open-ended,
                                   concept · method · mistake, never an MCQ
8 final_simulation
9 feedback_summary
10 completion_rules
```

Method stays unnamed in the case body; the student commits first. Invalid CBP: DPE missing · DPE has options · DPE passable in one word · DPE after the consequence · source concept not preserved · student not the decision-maker.

---

## 6. The deliverable

The pipeline already persists per-phase structured JSON (`phase_outputs`) and assembles a markdown packet. Flow v2 reshapes assembly to:

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
## Practice Arc
### Real-Life Challenge · Error Detection · Memory Matching · TicTacToe ·
###   Jigsaw Matching · Sentence Filling
## Boss Arena
```

**Content convention:** answer keys, rubrics and teacher notes are labeled distinctly from student-visible text so a downstream consumer can split them. Content hygiene, not a security control.

---

## 7. Difficulty & dropped phases

- **Keep `classify` / Easy-Hard** in `SUBJECT_FLOWS` — it works (English/History already skip via `has_classify=False`). Flow v2 just changes which phases the sequences contain. `GenerationProfile` is optional cleanup.
- **Drop `reflection`** — the repo's reflection is a content closing-summary (not student marking, which was never here). Re-adds as one phase if wanted.

---

## 8. Content rules that stay (not infrastructure)

- DPE present (CBP v1.1).
- Source fidelity — every phase/game traces to source-map concept IDs; no invented facts.
- Spec conformance — each phase/game matches its authored prompt spec.
- Fail-fast — a phase in a sequence with no prompt file in `prompts/<subject>/` errors, never improvises.

(QA dropped earlier as technical; these are generation-time requirements in the prompts/schemas.)

---

## 9. The one rule

```txt
Book in. Flow v2 content out. Markdown handoff.
Swap the phase set on the generator that already exists — don't rebuild it.
No runtime. No platform. No SDKs.
```
