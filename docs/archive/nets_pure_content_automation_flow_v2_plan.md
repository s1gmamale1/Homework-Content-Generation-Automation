# NETS Pure Content Automation — Flow v2 Transformation Plan

**Status:** Grounded in the real generator (`Homework-Content-Generation-Automation`, branch `DaddysBranch`). This replaces the earlier abstract plan.
**Date:** 2026-05-29
**Scope:** pure content automation — book in, Flow v2 content out, Markdown handoff. No runtime, no platform.

---

## 0. Reality anchor

The branch is a **mature legacy-flow generator**, not a greenfield. A large part of what earlier plans called "build" already exists and works. So Flow v2 is a **phase-set swap on existing infrastructure**, not a rebuild.

The headline correction: stop treating infrastructure (gateway, parallel pipeline, structured outputs, assembly, queue) as work. It's done. The real work is authoring the Flow v2 **prompts + schemas** and wiring them into the existing flow tables.

---

## 1. What already exists — reuse, do not rebuild

| Capability | Where it lives | Status |
|---|---|---|
| Provider abstraction | `app/services/agent.py` + `providers/{claude,codex,gemini,kimi}.py` | **Done.** CLI-subprocess router. **No SDKs** (explicitly forbidden in `CLAUDE.md`). |
| Per-job model selection | `agent_models.MODEL_MANIFEST`, `_PROVIDER_DEFAULT_MODEL` | Done. Only `claude` has a default; `gemini/kimi/codex` resolve `None` (guarded by a test). |
| DAG-parallel generation | `app/services/pipeline.py` + `flows.PHASE_DEPS` | **Done.** Wave scheduler runs phases concurrently when deps are met. |
| Structured output + repair | `agent.STRUCTURED_PHASE_SCHEMAS` → `model_validate_json` → retry-once on `ValidationError` | Done. |
| Extract + cross-job reuse | `toc_extractor.py`, pinned to `settings.extract_provider/model` (`gemini` / `gemini-2.5-flash`) | Done (but output is a flat summary — see §4). |
| Markdown packet + JSON columns | `pipeline.py` assembly step | Done (old-flow shaped — needs reshaping, §8). |
| Queue / worker / usage / auth / Postgres | `worker.py`, `agent_usages`, `homework_jobs`, `phase_outputs` | Done. |
| Prompt registry | `prompts/<subject>/*.md` (+ `flow.md` per subject) mirrored by `flows.SUBJECT_FLOWS` | **This is the registry.** No separate "Infra loader" exists or is needed. |

**Provider note:** the providers are `claude`, `codex`, `gemini`, `kimi` CLIs. Earlier plans said "openai/anthropic SDK providers" and an "AI Gateway to build" — both wrong. Don't reintroduce SDKs.

**Infra.zip note:** there is **no `Infra/` folder in the repo.** If `Infra.zip` is a separate pack of authored Flow v2 prompt specs, the integration is "port its specs into `prompts/<subject>/*.md` and register in `flows.py`" — not "build a registry."

---

## 2. The Flow v2 delta (the real work)

Current phases are legacy. Flow v2 swaps them:

| Legacy phase (today) | Flow v2 |
|---|---|
| `extract` (flat summary) | upgrade → structured **source map** with concept IDs |
| `preview-easy` / `preview-hard` | **Case-Based Preview** (3 checkpoints + Decision Process Explanation) |
| `flashcards` | keep, add stable IDs |
| `memory-sprint` | **Memory Check** (references flashcard IDs, 60%) |
| `game-breaks` (one generic) | split into **6 named games**: RLC, Error Detection, Memory Matching, TicTacToe, Jigsaw, Sentence Filling |
| `real-life` (standalone) | folds into Practice as the RLC mission |
| `consolidation` | redistribute into learning/practice (optional) |
| `final-challenge` (HP quiz) | **Boss Arena** (Why → How → What) |
| `reflection` | **kept** — `New_Flow.md` (source-of-truth) keeps Reflection / Debrief / Marking; this overrides the earlier "dropped" note in §9 |
| assembly (old sections) | reshape to Flow v2 sections (§8) |

Flashcards survives; everything else is new prompts/schemas or a rename.

---

## 3. The mechanism — how one Flow v2 phase gets added

This is already proven by every legacy phase. Each new phase = five small, local edits:

```txt
1. prompts/<subject>/<phase>.md          # the prompt (authored from the spec)
2. app/schemas/<phase>.py                # Pydantic output schema
3. agent.STRUCTURED_PHASE_SCHEMAS[...]   # register phase → schema for JSON mode
4. flows.SUBJECT_FLOWS[...]              # insert into the subject's easy/hard sequence
5. flows.PHASE_DEPS[...]                 # declare which prior outputs it consumes
```

No new infrastructure. The parallel scheduler, retry, usage tracking, and assembly pick it up automatically.

---

## 4. Source map (upgrade `extract`)

Today `extract` produces a flat factual summary. Flow v2 needs a structured **source map** so every generated phase can cite concept IDs.

- Option A: change the `extract` prompt + add a `SourceMap` schema so `extract` emits structured concepts (cheapest; keeps the gemini pin and cross-job reuse).
- Option B: add a separate `source-map` phase depending on `extract`.

Recommended: **Option A** — keep one pinned extract, upgrade its output. The source map is the factual anchor; every phase references its concept IDs. Extraction returning content unrelated to the requested chapter/section means the whole job is grounded wrong → re-extract.

---

## 5. Learning Sections

**Case-Based Preview** (new prompt + schema). Content standard CBP v1.1. Required order:

```txt
1 case_setup
2 checkpoint_1        (recognition: identify / decide / justify_or_avoid_mistake)
3 learning_block_1
4 checkpoint_2
5 learning_block_2
6 checkpoint_3
7 decision_process_explanation   ← REQUIRED, before the consequence, open-ended,
                                   evaluates concept · method · mistake, never an MCQ
8 final_simulation
9 feedback_summary
10 completion_rules
```

- Checkpoints 1–3 are low-friction (mcq/choice/short_select). The production reasoning lives only in the DPE.
- The method/formula stays unnamed in the case body; the student commits first.
- Invalid CBP (reject as content): DPE missing · DPE has options · DPE passable in one word · DPE after the consequence · source concept not preserved · student not the decision-maker.

**Flashcards** — keep the existing phase; add stable IDs so Memory Check can reference them.

**Memory Check** — replaces `memory-sprint`. New prompt + schema; items reference flashcard IDs; 60% threshold; item types per the content spec. Depends on `flashcards` (already the pattern: legacy `memory-sprint` depends on `flashcards` in `PHASE_DEPS`).

---

## 6. Practice games

Replace the single generic `game-breaks` with six named games, each its own prompt, each mapping missions to target skills from the source map:

```txt
Real-Life Challenge · Error Detection · Memory Matching · TicTacToe · Jigsaw Matching · Sentence Filling
```

- **Schemas — three, not six.** *(Implemented 2026-05-29.)* The six games' content contracts collapse to three Pydantic schemas (`app/schemas/practice_games.py`): the four games whose own spec files title them *"Case-Based Preview Interaction Mode"* (Memory Matching, Jigsaw, Sentence Filling, TicTacToe) share **one** `CbpModeGame` schema (`CaseBasedPreview` + an `interaction_mode` discriminator); only `RealLifeChallenge` and `ErrorDetection` are genuinely distinct shapes. Each game is still its own pipeline phase (`practice-rlc`, `practice-error-detection`, `practice-memory-match`, `practice-tictactoe`, `practice-jigsaw`, `practice-sentence`) with its own per-subject prompt.
- **Curated per-subject arc, not all-six-everywhere.** Each subject runs a 2-3 game arc that fits its target skills (set in `flows.SUBJECT_FLOWS`), sitting between the learning sections and the Boss Arena. Running all six on every lesson would violate "no random disconnected games / tasks must match target skill."
- RLC absorbs the legacy standalone `real-life`. The reverse-test variant (same story, new numbers, infer the unnamed method) is authored per its spec.
- No disconnected drills — every game traces to source concept IDs.

---

## 7. Boss Arena

Replaces `final-challenge`. New prompt + schema. Reasoning content (Why → How → What), not a flashcard-recall HP quiz. Mastery peak of the practice arc. Can draft in parallel from the source map; ideally consumes the practice skill map.

---

## 8. Assembly reshape

The pipeline already assembles a markdown packet + structured JSON columns. Reshape the assembly so the markdown carries Flow v2 sections:

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

**Content convention:** answer keys, rubrics, teacher notes are labeled distinctly from student-visible text in both the JSON and the markdown so a downstream consumer can split them. Content hygiene, not a security control.

The structured per-phase JSON the pipeline already persists (`phase_outputs`) is the structured deliverable; the reshaped markdown is the human handoff.

---

## 9. Difficulty & dropped phases

**Difficulty — keep what works.** `classify` + Easy/Hard sequences in `SUBJECT_FLOWS` are real and load-bearing (English/History already skip via `has_classify=False`). Keep the mechanism; Flow v2 just changes which phases the easy/hard sequences contain. Migrating to a `GenerationProfile` is optional cleanup, not required.

**Reflection — kept.** *(Corrected 2026-05-29.)* This earlier said "dropped." The source-of-truth flow `docs/Infra_prompts/Flow/New_Flow.md` explicitly **keeps** "Reflection / Debrief / Marking" as the closing section of the cycle, so it stays in the Flow v2 sequence (it is the last phase in every subject sequence). The repo's `reflection` phase serves this role. What *is* dropped is the standalone **`consolidation`** phase — `New_Flow.md` marks it "superseded," its Anchor-Lock / Memory-Lock behaviour redistributed across Flashcards + Memory Check + Reflection.

---

## 10. Content rules that stay (not infrastructure)

- **DPE present** (CBP v1.1) — the only production-reasoning step in CBP.
- **Source fidelity** — every phase/game traces to source-map concept IDs; no invented facts.
- **Spec conformance** — each phase/game matches its authored prompt spec.
- **Fail-fast** — if an enabled phase has no prompt in `prompts/<subject>/`, generation fails rather than inventing content. (`flows.py` already mirrors `prompts/<subject>/`; a missing prompt should error, not improvise.)

These are generation-time requirements baked into the prompts/schemas, not a separate QA stage (QA dropped earlier as technical).

---

## 11. Prompts — source of truth

- Runtime prompts live in `prompts/<subject>/*.md`; `flow.md` per subject is the human doc; `flows.SUBJECT_FLOWS` mirrors it in code.
- Flow v2 prompts are authored here (one `.md` per new phase per subject family), porting from the CBP v1.1 standard and any `Infra.zip` spec pack.
- Keep `flows.py` in sync with the prompt files; a phase in a sequence with no prompt file is a bug.

---

## 12. What changed from the previous plan

| Previous plan said | Reality on `DaddysBranch` |
|---|---|
| Build an "AI Gateway" (Phase 1) | Exists — CLI router (`agent.py` + `providers/`). Deleted from the plan. |
| Providers: gemini/kimi/openai/anthropic SDKs | Actual: `claude/codex/gemini/kimi` CLIs, no SDKs. |
| Build parallel generation | Exists — `pipeline.py` + `flows.PHASE_DEPS` wave scheduler. |
| Build structured-output + repair | Exists — `STRUCTURED_PHASE_SCHEMAS` + retry-once. |
| Build an "Infra prompt registry" loader | No `Infra/` in repo; registry is `prompts/<subject>/` + `flows.py`. |
| 4 job artifacts incl. `qa_report.json` | Repo persists `phase_outputs` JSON + assembled markdown; QA dropped. |
| Replace `SUBJECT_FLOWS` / drop Easy-Hard | Keep it; it works. GenerationProfile optional. |
| Reflection/Marking + weak-point signals | Never in this repo; repo `reflection` is a content phase, now dropped. |

---

## 13. Implementation sequence (PRs)

No gateway PR, no registry PR — those don't exist as work.

```txt
PR-1  Source map — upgrade extract output to structured concepts + SourceMap schema
PR-2  Learning — Case-Based Preview (+DPE) prompt & schema; flashcards stable IDs;
                 memory-check replacing memory-sprint
PR-3  Practice games — RLC + Error Detection + Memory Matching + TTT + Jigsaw + Sentence Filling
PR-4  Boss Arena — replace final-challenge with Why→How→What
PR-5  Assembly reshape — Flow v2 markdown sections; retire legacy section names
```

Each PR is the §3 mechanism applied to its phases (prompt + schema + `STRUCTURED_PHASE_SCHEMAS` + `SUBJECT_FLOWS` + `PHASE_DEPS`). PR-2/3/4 generators run in parallel under the existing scheduler and can be developed against a mock source map. Legacy flow keeps working until the new sequences replace it per subject.

---

## 14. The one rule

```txt
Book in. Flow v2 content out. Markdown handoff.
Swap the phase set on the generator that already exists — don't rebuild it.
No runtime. No platform. No SDKs.
```
