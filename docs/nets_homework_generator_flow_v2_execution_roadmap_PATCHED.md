# NETS Homework Generator — Flow v2 Execution Roadmap

**Status:** Execution-ready after Claude review patches. This document is the **operational playbook** for transforming `Homework-Content-Generation-Automation` into a Flow v2 generator while keeping `Homeworks` runtime compatibility safe.
**Date:** 2026-05-29
**Supersedes:** earlier Flow v2 generator transition notes and combined repo-state summaries
**Companion docs:**
- `nets_generator_flow_v2_transformation_plan_FINAL.md`
- `claude_review_verification_log_final.md`
- `homework_content_generation_automation_current_state.md`
- `homeworks_reference_platform_current_state.md`
- `Homeworks/docs/HOMEWORK_FLOW_V2_PLAN.md`

**Audience:** engineers executing the migration, PR reviewers, prompt/schema maintainers, runtime integration reviewers

---

## Revision locks

This roadmap includes the final corrections from the review loop:

- Use **book/textbook ingestion → chapter/section extraction → source map** as the required input chain.
- Use **three output layers**:
  - canonical v2 authoring package
  - beta-platform-safe export
  - future full Flow v2 runtime export
- Do **not** flip generator default to full runtime v2 until the platform can render Case-Based Preview and Unlock Gate.
- Treat `decision_process_explanation` as **required by `nets_case_based_preview_generation_standard_v1_1.md`**, not optional.
- Memory Check beta export must use only:
  - `mcq`
  - `fill_blank`
  - `choose_explanation`
- Real-Life Challenge beta export must validate against the platform’s exact 5-step `RealLifeChallengeCase` contract.
- Subject/phase prompts come from `Homeworks/server/prompts/<subject>/`.
- Project standards come from NETS standard files, especially the CBP v1.1 standard.
- Boss Arena remains the **mastery peak inside Practice Arc**.
- Old Easy/Hard phase skipping is replaced by `GenerationProfile`.
- Add explicit answer-leak QA fields.
- Add weak-point signal output for teacher/parent/admin routing.
- Reverse-test RLC can be adapted into current 5-step RLC only as a compatibility bridge, not a faithful runtime-native implementation.
- Every job must output content artifacts only: canonical JSON, beta-safe JSON, `homework-content.md`, flow manifest, and QA report.

---

## 0. TL;DR

The generator repo should be rebuilt as a **Flow v2 canonical content package producer**, while the `Homeworks` repo remains the runtime reference and compatibility target.

The execution path is:

```txt
Platform PR-0
  add CBP + Unlock Gate runtime support in Homeworks

Generator PR-1
  AI Gateway + Flow v2 schemas

Generator PR-2
  Book ingestion + chapter/section extraction + SourceMap + Infra prompt registry sync

Generator PR-3
  Learning Sections generation

Generator PR-4
  Practice Arc + RLC export

Generator PR-5
  Boss Arena export

Generator PR-6
  Reflection + weak-point signals + manifest

Generator PR-7
  QA validator + beta-platform conformance tests

Generator PR-8
  Generator UI preview update
```

The most important rule:

```txt
Build full canonical Flow v2 now.
Export only beta-platform-safe content until Homeworks runtime can render full Flow v2.
```

No ghost CBP. No invalid `content_json`. No “v2 ready” cosplay.

---

## 1. The map — phases at a glance

| Phase | Repo | Name | Primary output | Gate |
|---|---|---|---|---|
| **0** | `Homeworks` | Platform CBP + Unlock Gate prerequisite | Runtime can render CBP and gate Practice Arc | Required before full v2 default |
| **1** | Generator | AI Gateway + Flow v2 schemas | Provider-agnostic structured generation foundation | Phase 2 |
| **2** | Generator | Book ingestion + chapter/section extraction + SourceMap + Infra prompt registry sync | Textbook-grounded source map and pinned prompt/standard packs | Phase 3 |
| **3** | Generator | Learning Sections | CBP + flashcards + Memory Check canonical output | Phase 4 |
| **4** | Generator | Practice Arc + RLC | Conceptual Practice Arc and platform-valid RLC export | Phase 5 |
| **5** | Generator | Boss Arena | Why → How → What Boss Arena and beta boss adapter | Phase 6 |
| **6** | Generator | Reflection + manifest | Reflection, weak-point signals, flow manifest, package assembler | Phase 7 |
| **7** | Generator | QA + conformance fence | Platform schema validation, leak tests, banned old names | Phase 8 |
| **8** | Generator | UI preview | Authoring/QA console for canonical and beta exports | Final smoke |

**Parallel tracks possible:**
- Phase 0 can run in parallel with Generator Phase 1–2.
- Phase 3 can produce canonical CBP before platform PR-0 lands, but beta export must keep CBP runtime-disabled.
- Phase 4 and Phase 5 can share export-adapter work if the schema foundation is already stable.

---

## 2. Phase 0 — Platform runtime prerequisite

**Repo:** `s1gmamale1/Homeworks`

**Goal:** Make the runtime capable of consuming and rendering the new Flow v2 learning structure.

This phase is the lock on the castle gate. Without it, the generator can create CBP, but students will never see it.

### Deliverables

1. **Content schema**
   - Add `case_based_preview` to `ContentJSON`.
   - Keep additive migration: old homeworks without `flow_version` remain legacy.
   - Keep `extra="allow"` behavior.

2. **Injector**
   - Add `case_based_preview → CBP` runtime constant.
   - Ensure server-only fields are stripped before student-visible injection.
   - Do not expose answer/rubric fields to the browser.

3. **Runtime surface**
   - Add `screen-cbp` or equivalent Case-Based Preview runtime panel.
   - Add Learning Hub state.
   - Add Unlock Gate state.
   - Add Practice Arc locked/unlocked state.

4. **Runtime state machine**
   - Branch by `flow_version`.
   - Legacy rows use the old flow.
   - `flow_version: "v2"` enters the new dispatcher.

5. **Gate conditions**
   - CBP pass: checkpoint score + DPE requirement.
   - Flashcards + Memory Check pass: all required cards viewed + Memory Check ≥60%.
   - Practice Arc opens only when both learning branches pass.

6. **Regression tests**
   - Legacy homework still renders.
   - v2 homework renders Learning Hub.
   - CBP renders.
   - Unlock Gate blocks Practice Arc until conditions pass.
   - No answer keys leak to tutor/runtime prompt.

### Tests

- `test_flow_v2_dispatch.py`
- `test_case_based_preview_schema.py`
- `test_case_based_preview_runtime.py`
- `test_unlock_gate.py`
- `test_legacy_homework_still_renders.py`
- `test_tutor_no_leak_case_based.py`

### Acceptance criteria

- A `content_json` with `flow_version: "v2"` and `case_based_preview` renders CBP.
- Practice Arc remains locked until CBP + Memory Check pass.
- Old 9-phase homeworks still render unchanged.
- No CBP answer/rubric data leaks into browser-visible payload.

### Dependencies

None.

### Stop-the-line

- CBP field validates but does not render.
- Practice Arc can be opened without passing the gate.
- Any legacy homework fails to render.
- Any answer key or rubric leaks into browser payload.

---

## 3. Phase 1 — AI Gateway + Flow v2 schemas

**Repo:** `s1gmamale1/Homework-Content-Generation-Automation`

**Goal:** Replace Gemini-first direct calls with a provider-agnostic AI Gateway and add canonical Flow v2 schemas.

### Deliverables

1. **AI Gateway**
   - `app/services/ai/gateway.py`
   - `app/services/ai/orchestrator.py`
   - `app/services/ai/contracts.py`
   - `app/services/ai/providers/base.py`
   - `app/services/ai/providers/gemini_provider.py`
   - Future-ready stubs:
     - `kimi_provider.py`
     - `openai_provider.py`
     - `anthropic_provider.py`

2. **Task model policy**

```python
class AITask(str, Enum):
    TEXTBOOK_EXTRACT = "textbook_extract"
    SOURCE_MAP_BUILD = "source_map_build"
    CASE_PREVIEW_GENERATE = "case_preview_generate"
    FLASHCARDS_GENERATE = "flashcards_generate"
    MEMORY_CHECK_GENERATE = "memory_check_generate"
    PRACTICE_ARC_GENERATE = "practice_arc_generate"
    BOSS_ARENA_GENERATE = "boss_arena_generate"
    REFLECTION_GENERATE = "reflection_generate"
    CONTENT_QA_REVIEW = "content_qa_review"
    SCHEMA_REPAIR = "schema_repair"
```

3. **Structured generation behavior**
   - JSON-mode request where supported.
   - Pydantic schema validation.
   - One repair retry.
   - Provider/model/cost/log metadata.
   - Clean phase failure if still invalid.

4. **Canonical Flow v2 schemas**

```txt
app/schemas/flow_v2/
  __init__.py
  source_map.py
  case_based_preview.py
  flashcards.py
  memory_check.py
  practice_arc.py
  boss_arena.py
  reflection.py
  flow_manifest.py
  content_package.py
  generation_profile.py
  weak_point_signal.py
```

5. **Case-Based Preview DPE**
   - Required `decision_process_explanation`.
   - Slot 7.
   - Open-ended.
   - AI/rubric evaluated.
   - Checks concept + method + mistake.
   - Not a 4th MCQ.

6. **GenerationProfile**
   - Replaces old classify-based phase skipping.

```python
class GenerationProfile(BaseModel):
    grade_band: str
    difficulty_band: Literal["light", "standard", "advanced"]
    pisa_target: str
    item_count_profile: dict
    language_complexity: str
    hint_density: str
```

### Tests

- AI Gateway selects provider by task.
- Provider fallback works.
- Structured output validates.
- Repair retry works.
- Failure is clean after second invalid response.
- All Flow v2 schemas validate minimal and realistic fixtures.
- CBP rejects missing DPE.
- CBP rejects DPE with options.
- CBP rejects DPE after consequence.
- `GenerationProfile` does not remove required phases.

### Acceptance criteria

- Existing generator behavior still works in legacy mode.
- New Flow v2 schemas pass tests.
- Gateway can run at least Gemini provider.
- No phase generation code calls Gemini directly after migration path is introduced.

### Dependencies

None.

### Stop-the-line

- Structured generation can bypass validation.
- DPE is optional or weakly typed.
- Gateway failure causes silent partial output.
- Phase skipping survives in new Flow v2 path.

---

## 4. Phase 2 — Book ingestion + chapter/section extraction + SourceMap + Infra prompt registry sync

**Repo:** Generator

**Goal:** Make textbook fidelity and prompt-source consistency load-bearing.

### Deliverables

1. **Book ingestion and section extraction**

Input support:

```txt
PDF/textbook file
book metadata
chapter identifier
section identifier
subject
grade
language
```

Extraction chain:

```txt
book_source
→ chapter_locator
→ section_locator
→ extracted_section_content
→ source_map_json
```

Required extraction output:

```json
{
  "book_source": {},
  "chapter": {},
  "section": {},
  "extracted_section_content": {
    "raw_text": "...",
    "clean_text": "...",
    "figures_or_tables": [],
    "page_refs": []
  }
}
```

2. **Flow v2 division mapper**

Map extracted content into:

```txt
Learning Sections
  Case-Based Preview
  Flashcards
  Memory Check

Practice Arc
  Real-Life Challenge
  Error Detection
  Memory Matching
  TicTacToe
  Jigsaw Matching
  Sentence Filling
  other enabled games

Boss Arena
Reflection / Marking
```

3. **Infra prompt registry**

Load prompts/specs from uploaded `Infra.zip` structure:

```txt
Infra/Flow/New_Flow.md
Infra/Case-Based Preview/*
Infra/Flashcards/Flashcard Prompts/*
Infra/Flashcards/Quzilet Learning/*
Infra/Gamified Practices/Real Life Challenge/*
Infra/Gamified Practices/Error Detection/*
Infra/Gamified Practices/Memory Matching/*
Infra/Gamified Practices/TicTacToe/*
Infra/Gamified Practices/Jigsaw Matching/*
Infra/Gamified Practices/Sentence Filling/*
Infra/Gamified Practices/Boss Arena/*
Infra/Uzbek Specification/*
```

Rules:

```txt
Do not invent prompts when a registry file exists.
Select prompt by subject family + phase/game type.
Record prompt path/version/hash in flow_manifest.json.
Fail QA if an enabled phase/game has no prompt/spec mapping.
```


4. **SourceMap builder**

```txt
extract_textbook_section
  ↓
source_map_json
```

Source map includes:

```txt
textbook title
subject
grade
section number/title
page range
core concepts
formulas
terms
textbook examples
common mistakes
source alignment notes
forbidden invention checks
```

5. **Platform prompt sync**

Create:

```txt
scripts/sync_platform_prompts.py
prompts_platform_pinned/
  manifest.json
  server/prompts/<subject>/*.md
```

Manifest:

```json
{
  "source_repo": "s1gmamale1/Homeworks",
  "source_branch": "server",
  "source_commit": "...",
  "synced_at": "...",
  "files": []
}
```

6. **Project standards sync**

Create:

```txt
standards_pinned/
  manifest.json
  nets_case_based_preview_generation_standard_v1_1.md
  nets_homework_flow_v2.md
  interactivity_standard.md
  gamification_standard.md
```

7. **Prompt resolver**

```python
get_prompt_pack(subject, phase, flow_version="v2")
```

Returns:

```python
PromptPack(
    phase_prompt=...,
    project_standards=[...],
    schema=...,
    prompt_version=...,
    source_commit=...
)
```

8. **Input assembly**

Prompt input order:

```txt
source fidelity rules
project standard rules
subject phase prompt
schema hint
source_map
prior phase outputs
generation profile
```

### Tests

- Book/PDF input can locate a chapter + section.
- Extracted section content contains clean text and page refs.
- Flow v2 division mapper assigns source concepts to learning/practice/boss/reflection.
- Infra prompt registry resolves every enabled phase/game.
- Prompt sync writes manifest.
- Prompt pack resolves subject + phase.
- CBP prompt pack includes CBP v1.1 standard.
- Memory Check prompt uses platform-supported three modes.
- No generator-local divergent prompt is used when platform prompt exists.
- SourceMap is passed to every downstream phase.

### Acceptance criteria

- A book input produces `extracted_section_content`.
- Extracted section maps into Flow v2 divisions.
- All required game prompts/specs resolve from the Infra registry.
- Every v2 generation has a source map.
- Every prompt call records prompt version/source commit.
- CBP uses v1.1 standard.
- Memory Check prompt matches runtime-supported types.

### Dependencies

Phase 1.

### Stop-the-line

- Book/chapter/section extraction fails or returns unrelated content.
- Any enabled game lacks a prompt/spec mapping.
- SourceMap missing for any generated phase.
- Prompt source is ambiguous.
- Generator prompt fork appears without explicit exception.
- CBP prompt does not include v1.1 DPE requirement.

---

## 5. Phase 3 — Learning Sections generation

**Repo:** Generator

**Goal:** Generate the Flow v2 learning layer: Case-Based Preview, Flashcards, and Memory Check.

### Deliverables

1. **Case-Based Preview generator**
   - Produces canonical `case_based_preview`.
   - Includes exactly 3 checkpoints.
   - Includes required DPE slot 7.
   - Includes final consequence/simulation.
   - Includes feedback summary and completion rules.

2. **Flashcards generator**
   - Stable card IDs.
   - Source concept references.
   - Term/definition/formula/process support.
   - Beta adapter to platform `term`, `def`, `cluster`.

3. **Memory Check generator**
   - Near-term supports only:
     - `mcq`
     - `fill_blank`
     - `choose_explanation`
   - 5–8 items.
   - Every item references a flashcard.
   - Pass threshold default 60%.

4. **Beta export behavior**
   - Include `flashcards`.
   - Include `memory_check`.
   - Do not include runtime-bearing `case_based_preview` unless `PLATFORM_CBP_RUNTIME_READY=true`.
   - Always include CBP in canonical authoring package.

### Tests

- CBP has 3 checkpoints.
- CBP has DPE before consequence.
- DPE is open-ended.
- DPE checks concept/method/mistake.
- Flashcards all have stable IDs.
- Memory Check refs existing flashcard IDs.
- Memory Check beta export only uses allowed three types.
- Beta export omits CBP when readiness flag is false.

### Acceptance criteria

- One real textbook section produces valid canonical learning package.
- Beta export validates against current platform schema.
- No unsupported Memory Check type leaks into beta export.
- CBP is preserved in canonical package even if omitted from beta runtime export.

### Dependencies

Phases 1–2.

### Stop-the-line

- DPE is missing or optional.
- Memory Check emits `tile_match` or other unsupported type into beta export.
- CBP silently lands in a platform field that runtime does not read.
- Flashcard IDs do not match Memory Check references.

---

## 6. Phase 4 — Practice Arc + RLC

**Repo:** Generator

**Goal:** Replace old `game-breaks`, standalone `real-life`, and standalone `consolidation` with conceptual Practice Arc generation.

### Deliverables

1. **Canonical Practice Arc**

```json
{
  "title": "...",
  "target_skills": [],
  "missions": [],
  "simulations": [],
  "games": [],
  "real_life_challenge": {},
  "completion_rules": {}
}
```

2. **Practice task types**

```txt
conceptual_practice
debugging_task
error_detection
calculation_chain
formula_translation
simulation
real_life_challenge
tile_match
adaptive_quiz
sentence_fill
memory_palace
```

3. **RLC variants**

Canonical generator may support:

```txt
expert_case_5_step
reverse_test_same_story_new_numbers
```

4. **Beta RLC adapter**

Current beta platform supports only the 5-step expert-case contract:

```txt
decision
info_request
final_decision
concept_select
reasoning
```

Reverse-test must be adapted into this only as a compatibility bridge.

5. **Platform conformance validation**

Before beta export:

```python
ContentJSON.model_validate(beta_export)
```

or validate against a pinned/vendor copy of the platform schema.

### Tests

- Practice Arc has at least one conceptual mission.
- Every task maps to `target_skill`.
- No random disconnected games.
- RLC has exactly 5 steps in fixed order.
- Decision/info/final-decision steps have ≥2 options and exactly 1 correct.
- Concept-select has ≥3 chips and exactly 1 correct.
- Reasoning `min_chars` is between 20 and 1000.
- Reverse-test adapter is marked as compatibility bridge, not faithful runtime-native implementation.

### Acceptance criteria

- Practice Arc canonical package validates.
- Beta export RLC validates against platform contract.
- RLC answer keys are server-only.
- No old `game-breaks` phase naming remains in canonical Flow v2.

### Dependencies

Phases 1–3.

### Stop-the-line

- RLC fails platform schema.
- Reverse-test is advertised as faithful runtime-native while using concept-select.
- Practice Arc becomes entertainment-only.
- Old standalone `real-life` / `game-breaks` concepts return as Flow v2 source-of-truth.

---

## 7. Phase 5 — Boss Arena

**Repo:** Generator

**Goal:** Generate Boss Arena as the mastery peak inside Practice Arc.

### Deliverables

1. **Canonical Boss Arena**

```json
{
  "title": "...",
  "boss_type": "sub",
  "mastery_goal": "...",
  "encounters": [],
  "completion_rules": {}
}
```

2. **Boss encounter**

Each encounter includes:

```txt
scenario
source_concept_ids
weak_point_targets
why_prompt
how_prompt
what_prompt
expected_reasoning
rubric
hints
damage
```

3. **Beta boss adapter**

Exports:

```json
{
  "boss_questions": [],
  "boss_meta": {
    "use_dynamic_boss": true
  }
}
```

4. **Reasoning preservation**

The beta `prompt` must preserve the Why → How → What reasoning chain even if the runtime currently renders boss questions through older surfaces.

### Tests

- Every encounter has Why/How/What.
- Every encounter maps to source concept IDs.
- Boss does not test only recall.
- Beta export validates against platform `BossQuestion` / `BossMeta`.
- Boss answer keys are stripped from student-visible payload.

### Acceptance criteria

- Boss Arena canonical validates.
- Beta boss export validates.
- Dynamic boss flag behavior is explicit.
- No “quiz + HP skin” boss slips through.

### Dependencies

Phases 1–4.

### Stop-the-line

- Boss prompt lacks Why/How/What.
- Boss answer spec leaks into browser.
- Boss is disconnected from Practice Arc target skills.
- Boss becomes a static MCQ bank.

---

## 8. Phase 6 — Reflection, weak-point signals, manifest, package assembler

**Repo:** Generator

**Goal:** Assemble the full canonical package and produce actionable marking/feedback outputs.

### Deliverables

1. **Reflection / Marking**

```json
{
  "student_questions": [],
  "system_feedback_template": {},
  "pass_status_rules": {},
  "redo_route": {},
  "weak_point_signals": []
}
```

2. **WeakPointSignal**

```python
class WeakPointSignal(BaseModel):
    concept_id: str
    source_phase: str
    evidence: list[dict]
    severity: Literal["low", "medium", "high"]
    target_accounts: list[Literal["teacher", "parent", "admin"]]
    recommended_action: str
```

3. **Flow manifest**

```json
{
  "flow_version": "v2",
  "generation_version": "...",
  "subject": "...",
  "grade": 8,
  "section_title": "...",
  "phase_order": [],
  "unlock_rules": {},
  "pass_thresholds": {},
  "source_fidelity_checks": [],
  "prompt_versions": {},
  "model_usage": {}
}
```

4. **Package assembler**

Outputs:

```txt
canonical_v2_authoring_package.json
beta_platform_safe_export.json
flow_manifest.json
qa_report.json
```

5. **Human-readable Markdown export**

Generate:

```txt
homework-content.md
```

Required structure:

```md
# Homework Content

## Source Book / Chapter / Section
## Extracted Section Summary
## Source Map
## Learning Sections
### Case-Based Preview
### Flashcard Learning
### Memory Check
## Unlock Gate Requirements
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

Rules:

```txt
Content only — no frontend/runtime code.
Must mirror canonical package.
Must include all enabled games.
Must not claim unsupported runtime behavior.
```

6. **ZIP export update**

Include:

```txt
homework-v2-canonical.json
homeworks-beta-content-json.json
source-map.json
case-based-preview.json
flashcards.json
memory-check.json
practice-arc.json
boss-arena.json
reflection.json
flow-manifest.json
qa-report.json
homework-content.md
```

### Tests

- Reflection validates.
- Weak-point signals reference real concept IDs.
- Flow manifest includes every phase and prompt version.
- Package assembler includes canonical + beta outputs.
- `homework-content.md` mirrors canonical package content.
- ZIP contains all expected files.
- Authoring-only fields are labeled as authoring-only.

### Acceptance criteria

- One generated job creates complete package files.
- One generated job creates human-readable `homework-content.md`.
- Beta export is distinct from canonical package.
- Weak-point signals exist and are machine-readable.
- Old ZIP files remain available only in legacy/debug mode.

### Dependencies

Phases 1–5.

### Stop-the-line

- Weak-point signals are vague strings instead of structured payloads.
- Flow manifest missing prompt/model usage.
- Canonical and beta exports are mixed together.
- Runtime-unsupported fields are treated as active runtime behavior.

---

## 9. Phase 7 — QA validator + beta-platform conformance fence

**Repo:** Generator

**Goal:** Prevent Flow v2 drift and invalid platform exports before they reach the runtime.

### Deliverables

1. **Flow v2 validator**

```txt
app/services/qa/flow_v2_validator.py
```

2. **Beta platform contract test**

```txt
tests/test_beta_platform_contract.py
```

Must validate generator beta export against the platform’s real or pinned schema.

3. **Answer-leak validator**

Server-only fields must not appear in student-visible export.

Boss server-only:

```txt
accepted
acceptable
ans
accepted_answers
answer_spec
```

RLC server-only:

```txt
is_correct
consequence
acceptable_keywords
```

Sentence Fill server-only:

```txt
answers
explanations
```

Tile Match server-only:

```txt
explanation
```

4. **Banned legacy phase-name fence**

Hard fail in canonical v2 visible labels:

```txt
Memory Sprint
Game Breaks
Final Challenge
Consolidation phase
Start Homework
Boss quiz
HP quiz
```

5. **Markdown export validator**

Validate:

```txt
homework-content.md exists
homework-content.md mirrors canonical phases/games
homework-content.md contains content only
homework-content.md does not include runtime implementation instructions
```

6. **Readiness-flag tests**

- `PLATFORM_CBP_RUNTIME_READY=false` omits runtime CBP from beta export.
- `PLATFORM_CBP_RUNTIME_READY=true` includes it only if platform schema supports it.
- Full v2 default cannot be enabled unless hard-gate checklist passes.

### Tests

- `test_no_old_phase_names.py`
- `test_no_answer_leak_beta_export.py`
- `test_beta_platform_contract.py`
- `test_memory_check_export_contract.py`
- `test_rlc_export_contract.py`
- `test_cbp_runtime_flag.py`
- `test_flow_manifest_complete.py`
- `test_homework_content_markdown_export.py`
- `test_infra_prompt_registry_coverage.py`
- `test_weak_point_signals.py`

### Acceptance criteria

- Invalid beta export cannot pass CI.
- Unsupported Memory Check types cannot reach beta runtime.
- RLC schema drift is caught.
- Answer-leak fields are caught by name.
- Full v2 default is blocked until platform readiness gates are true.

### Dependencies

Phases 1–6.

### Stop-the-line

- Beta export validates only against a hand-written mirror and not platform schema.
- Any answer key leaks.
- Old phase names appear in new v2 visible output.
- Readiness flags can be bypassed.

---

## 10. Phase 8 — Generator UI preview update

**Repo:** Generator

**Goal:** Replace old-flow preview UI with a Flow v2 authoring and QA console.

### Deliverables

1. **Flow v2 preview page**

Sections:

```txt
Source Map
Learning Sections
  Case-Based Preview
  Flashcards
  Memory Check
Practice Arc
Boss Arena
Reflection / Marking
QA Report
Exports
```

2. **Compatibility status panel**

Show:

```txt
Canonical package: valid/invalid
Beta platform export: valid/invalid
CBP runtime status: ready/not ready
Unlock Gate status: ready/not ready
Full v2 default: blocked/unblocked
```

3. **Phase regeneration controls**

```txt
Regenerate phase
Repair schema only
Regenerate easier
Regenerate harder
Run QA only
Export beta-safe JSON
Export canonical package
```

4. **Warnings**

Examples:

```txt
CBP is canonical-only because platform runtime flag is false.
Reverse-test RLC is adapted into 5-step compatibility shape.
Memory Check restricted to 3 runtime-supported modes.
```

### Tests

- UI renders canonical package sections.
- UI shows authoring-only labels.
- UI shows beta export compatibility.
- Old Memory Sprint/Game Breaks/Final Challenge UI is not used for Flow v2 jobs.
- Regenerate action calls correct phase endpoint.
- Export buttons download correct JSON.

### Acceptance criteria

- Author can inspect every canonical phase.
- Author can see what will and will not appear in current runtime.
- QA failures are visible and actionable.
- Legacy job preview still works.

### Dependencies

Phases 1–7.

### Stop-the-line

- UI implies CBP is runtime-visible when it is not.
- Canonical and beta exports are visually conflated.
- Old phase preview parser drives v2 jobs.
- QA failures are hidden behind raw JSON only.

---

## 11. Target repo layout

```txt
Homework-Content-Generation-Automation/
├── app/
│   ├── services/
│   │   ├── ai/
│   │   │   ├── gateway.py
│   │   │   ├── orchestrator.py
│   │   │   ├── contracts.py
│   │   │   └── providers/
│   │   │       ├── base.py
│   │   │       ├── gemini_provider.py
│   │   │       ├── kimi_provider.py
│   │   │       ├── openai_provider.py
│   │   │       └── anthropic_provider.py
│   │   ├── book_ingestion.py
│   │   ├── chapter_extractor.py
│   │   ├── source_map.py
│   │   ├── flow_division_mapper.py
│   │   ├── prompt_registry.py
│   │   ├── markdown_exporter.py
│   │   ├── flows_v2.py
│   │   ├── assembler_v2.py
│   │   ├── export_adapters/
│   │   │   ├── canonical.py
│   │   │   └── beta_platform.py
│   │   └── qa/
│   │       └── flow_v2_validator.py
│   ├── schemas/
│   │   └── flow_v2/
│   │       ├── source_map.py
│   │       ├── case_based_preview.py
│   │       ├── flashcards.py
│   │       ├── memory_check.py
│   │       ├── practice_arc.py
│   │       ├── boss_arena.py
│   │       ├── reflection.py
│   │       ├── flow_manifest.py
│   │       ├── generation_profile.py
│   │       ├── weak_point_signal.py
│   │       └── content_package.py
├── infra_prompt_registry_pinned/
│   ├── manifest.json
│   └── Infra/
│       ├── Flow/
│       ├── Case-Based Preview/
│       ├── Flashcards/
│       ├── Gamified Practices/
│       └── Uzbek Specification/
├── prompts_platform_pinned/
│   ├── manifest.json
│   └── server/prompts/<subject>/*.md
├── standards_pinned/
│   ├── manifest.json
│   ├── nets_case_based_preview_generation_standard_v1_1.md
│   ├── nets_homework_flow_v2.md
│   ├── interactivity_standard.md
│   └── gamification_standard.md
├── scripts/
│   ├── sync_infra_prompt_registry.py
│   ├── sync_platform_prompts.py
│   ├── sync_project_standards.py
│   └── validate_beta_export.py
├── docs/
│   ├── nets_generator_flow_v2_transformation_plan_FINAL.md
│   ├── nets_homework_generator_flow_v2_execution_roadmap.md
│   ├── phase-1-ai-gateway-YYYY-MM-DD.md
│   ├── phase-2-source-map-prompts-YYYY-MM-DD.md
│   ├── phase-3-learning-sections-YYYY-MM-DD.md
│   ├── phase-4-practice-arc-YYYY-MM-DD.md
│   ├── phase-5-boss-arena-YYYY-MM-DD.md
│   ├── phase-6-package-assembler-YYYY-MM-DD.md
│   ├── phase-7-qa-contract-YYYY-MM-DD.md
│   └── phase-8-ui-preview-YYYY-MM-DD.md
└── tests/
    ├── test_ai_gateway.py
    ├── test_flow_v2_schemas.py
    ├── test_case_based_preview_schema.py
    ├── test_memory_check_export_contract.py
    ├── test_rlc_export_contract.py
    ├── test_beta_platform_contract.py
    ├── test_no_answer_leak_beta_export.py
    ├── test_no_old_phase_names.py
    └── test_flow_manifest_complete.py
```

---

## 12. Branch / PR strategy

### Rule

One PR per phase. Do not mix platform runtime work and generator schema work in the same PR.

### PR title format

```txt
[Flow v2 Phase N] <deliverable name>
```

Examples:

```txt
[Flow v2 Phase 0] Add CBP runtime and Unlock Gate
[Flow v2 Phase 1] Add AI Gateway and canonical schemas
[Flow v2 Phase 2] Add SourceMap and prompt sync
```

### Merge order

```txt
Platform Phase 0 can run parallel with Generator Phase 1–2.

Generator Phase 3+ should not claim runtime-v2 readiness until Platform Phase 0 lands.

Generator Phase 7 must land before any default flip.
```

### Required PR checklist

```txt
[ ] Tests added
[ ] Legacy mode preserved
[ ] Beta export contract checked where relevant
[ ] Answer-leak check passed where relevant
[ ] Prompt/standard source recorded
[ ] Phase result doc committed
```

---

## 13. Compatibility matrix

| Feature | Canonical package | Beta export now | Full runtime later |
|---|---|---|---|
| Source map | Yes | Authoring-only | Maybe |
| Flow manifest | Yes | Authoring-only | Maybe |
| Case-Based Preview | Yes | No, unless runtime flag true | Yes |
| DPE | Required | No, unless CBP runtime true | Yes |
| Flashcards | Yes | Yes | Yes |
| Memory Check | Yes | Yes, 3 types only | Richer later |
| Practice Arc wrapper | Yes | Adapted into supported keys | Yes |
| RLC expert case | Yes | Yes, strict 5-step | Yes |
| Reverse-test RLC | Yes | Compatibility bridge only | Native after infer-step support |
| Boss Arena | Yes | Adapted into `boss_questions` | Yes |
| Reflection | Yes | Yes | Yes |
| Weak-point signals | Yes | Authoring/backend only first | Yes |
| `homework-content.md` | Yes | Human review only | N/A |

---

## 14. Working-assumption update flow

After each phase lands:

1. Update phase result doc.
2. Update roadmap if a contract changes.
3. Update compatibility matrix.
4. Update prompt/standard manifest if prompt sources changed.
5. Re-run beta export conformance tests.

No silent assumptions. Every contract change gets a tiny paper trail.

---

## 15. Consolidated stop-the-line conditions

| Condition | Phase | Why |
|---|---|---|
| Platform cannot render CBP but generator flips full v2 default | 0 / 7 | Student sees incomplete homework |
| DPE missing from CBP | 1 / 3 | Violates CBP v1.1 |
| DPE represented as 4th MCQ | 1 / 3 | Violates CBP v1.1 |
| Memory Check beta export emits unsupported type | 3 / 7 | Platform validation failure |
| RLC export fails 5-step validator | 4 / 7 | Platform validation failure |
| Reverse-test called runtime-native while adapted via concept_select | 4 | Pedagogy mismatch |
| Boss lacks Why → How → What | 5 | Boss becomes quiz skin |
| Any answer key leaks into student-visible export | 4 / 5 / 7 | Security/pedagogy failure |
| Old phase names appear in v2 visible content | 7 | Flow drift |
| Prompt source cannot be traced to platform prompt or project standard | 2 | Drift risk |
| Book/chapter/section extraction returns unrelated content | 2 | Whole generated homework is grounded in wrong source |
| Source map missing for any generated phase | 2 | Textbook fidelity risk |
| Enabled game has no Infra prompt/spec mapping | 2 / 7 | Generator may invent game content |
| Markdown export missing or contains implementation code | 6 / 7 | Human handoff becomes unusable |
| Legacy generation breaks | Any | Migration is no longer additive |

---

## 16. Communication & handoff

- Phase completion = PR merged + tests green + result doc committed.
- Any stop-the-line event gets a dedicated issue/comment before continuing.
- Runtime claims require browser smoke, not only schema validation.
- Generator claims require canonical package + beta export artifacts.
- Reviewers should check:
  - source-of-truth compliance
  - runtime compatibility
  - answer-leak fields
  - old phase-name drift
  - legacy preservation

---

## 17. Execution start recommendation

Start with two parallel tracks:

### Track A — Platform prerequisite

```txt
[Flow v2 Phase 0] Add CBP runtime and Unlock Gate
```

Why first:

```txt
This unlocks true runtime Flow v2 and removes the ghost-content risk.
```

### Track B — Generator foundation

```txt
[Flow v2 Phase 1] Add AI Gateway and canonical schemas
[Flow v2 Phase 2] Add book ingestion, chapter extraction, SourceMap, and Infra prompt registry sync
```

Why parallel:

```txt
These do not require platform runtime to be finished.
They build the generator backbone while platform catches up.
```

Do not start serious beta export flipping until:

```txt
Platform Phase 0 + Generator Phase 7 are both green.
```

---

End of roadmap. Begin Phase 0 and Phase 1 in parallel.
