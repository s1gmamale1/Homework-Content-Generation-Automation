# NETS Homework Content Generator — Corrected Flow v2 Transformation Plan

**Status:** Corrected after Claude review  
**Date:** 2026-05-29  
**Target repo:** `s1gmamale1/Homework-Content-Generation-Automation`  
**Reference repo:** `s1gmamale1/Homeworks`  
**Purpose:** Patch the original Flow v2 generator transformation plan so it no longer silently emits runtime-invisible content or beta-platform-invalid JSON.

---

---

## Final patch — book ingestion, Infra prompt registry, and Markdown content export

This plan now explicitly requires the generator to run the full content-generation chain:

```txt
book/textbook file
→ chapter + section locator
→ extracted section content
→ source map
→ Flow v2 division mapping
→ dependency-aware phase/game generation
→ canonical content package
→ beta-platform-safe runtime export
→ human-readable homework-content.md
```

The generator must create **content artifacts only**. It must not generate frontend/runtime code as part of a homework job.

Every generated homework job must produce:

```txt
1. canonical_v2_authoring_package.json
2. beta_platform_safe_export.json
3. homework-content.md
4. flow_manifest.json
5. qa_report.json
```

The Markdown file is for human review and content handoff. Runtime still requires structured JSON.


# 0. Executive Decision

The original architecture remains mostly correct:

- Keep the two-package split:
  - canonical generator package
  - beta-platform `content_json` export
- Keep additive migration.
- Keep provider-gateway model routing.
- Keep phase-DAG scheduling.
- Keep structured Pydantic validation and one repair retry.
- Keep banned legacy phase-name QA checks.

But the plan must be corrected in four load-bearing ways:

1. **Do not flip generator default to runtime Flow v2 until the platform can render Case-Based Preview and Unlock Gate.**
2. **Canonical generator schemas may be richer, but beta export must obey the platform’s current constrained schemas exactly.**
3. **Case-Based Preview must include a separate reasoning/decision explanation slot in the canonical package.**
4. **The platform repo must become the prompt/source-contract authority, or prompt drift will become inevitable.**

---

# 1. Verification Summary

## 1.1 Verified as true

### Claim A — Platform Flow v2 is planned but not fully implemented

The reference `Homeworks` repo has a Flow v2 plan document that explicitly says the plan is **not yet implemented**. The same document describes the current runtime as a 9-phase linear flow with no score gates.

**Decision:** The generator cannot safely default new jobs to fully-runtime v2 yet.

---

### Claim B — Platform has no active `case_based_preview` runtime path

The platform schema currently contains `flow_version`, `flashcards`, and `memory_check`, but no explicit `case_based_preview` field in `ContentJSON`.

The injector maps known payload keys into runtime JS constants. It currently includes:

```txt
PANELS
QUOTES
FLASHCARDS
MS_QUESTIONS
GB_ADAPTIVE_QUIZ
GB_WHY_CHAIN
GB_MEMORY_MATCH
GB_PUZZLE_LOCK
GB_MYSTERY_BOX
GB_TTT
MC
READING
CONSOLIDATION
REFLECTION
```

There is no `case_based_preview → CBP` constant.

**Decision:** Case-Based Preview must be canonical-generator-only until the platform adds schema + injector + screen + state-machine support.

---

### Claim C — Platform Memory Check export accepts only 3 item types

The platform `MemoryCheckItem.type` is constrained to:

```txt
mcq
fill_blank
choose_explanation
```

Even with `extra="allow"`, a constrained `Literal[...]` field will reject unsupported values like `tile_match`.

**Decision:** Canonical memory check can support richer types, but beta export must down-map to the 3 supported types.

---

### Claim D — Platform Real-Life Challenge has a strict 5-step contract

The platform `RealLifeChallengeCase` requires exactly 5 steps in this order:

```txt
decision
info_request
final_decision
concept_select
reasoning
```

It also requires:

- decision/info/final-decision steps have at least 2 options
- each such step has exactly 1 correct option
- concept-select has at least 3 chips
- concept-select has exactly 1 correct chip
- reasoning has sane `min_chars`

**Decision:** Generator RLC export must validate against the platform’s exact `RealLifeChallengeCase` contract before writing beta `content_json`.

---

### Claim E — Platform already has a Memory Check prompt

At least `server/prompts/math-algebra/memory-check.md` exists in the reference platform and already restricts output modes to:

```txt
mcq
fill_blank
choose_explanation
```

**Decision:** Do not duplicate or rewrite Memory Check prompts inside the generator. Consume or sync the platform prompt pack.

---

## 1.2 Final correction after v1.1 standard verification

### Claim F — Case-Based Preview standard requires a separate Decision Process Explanation slot

This is now accepted as a **required standard rule**, not an optional generator-side improvement.

The authoritative project standard is:

```txt
nets_case_based_preview_generation_standard_v1_1.md
```

It explicitly requires **Decision Process Explanation** as **slot 7** of the Case-Based Preview structure:

```txt
after Checkpoint 3
before Final Consequence / Simulation
```

The placement is non-negotiable because it creates a **commit-before-consequence** moment:

```txt
student explains concept + method + mistake before seeing the consequence
```

If this is asked after the consequence, the student can rationalize backward. If it is asked before, the consequence confirms or contradicts the reasoning chain.

**Decision:** `decision_process_explanation` is required by CBP standard v1.1 and must not be cut during implementation.

Generator prompts and schema must point to the v1.1 standard as the CBP source-of-truth.

---

# 2. Corrected Architecture

## 2.1 Three output layers, not two

The plan originally had:

```txt
canonical_v2
beta_platform_v2
```

Corrected structure:

```txt
canonical_v2_authoring_package
beta_platform_safe_export
future_beta_platform_full_v2_export
```

### Layer 1 — Canonical v2 authoring package

This is the generator’s full source-of-truth package.

It may contain:

```json
{
  "flow_version": "v2",
  "book_source": {},
  "chapter": {},
  "section": {},
  "extracted_section_content": {},
  "source_map": {},
  "case_based_preview": {},
  "flashcards": [],
  "memory_check": {},
  "practice_arc": {},
  "boss_arena": {},
  "reflection": {},
  "flow_manifest": {},
  "qa_report": {},
  "homework_content_md": ""
}
```

This package is allowed to be richer than the platform runtime.

---

### Layer 2 — Beta platform safe export

This must only emit fields that current `Homeworks` runtime can safely consume.

Current safe fields include:

```json
{
  "flow_version": "v2",
  "meta": {},
  "flashcards": [],
  "memory_check": {},
  "real_life_challenge": {},
  "gb_adaptive_quiz": [],
  "gb_tile_match": [],
  "gb_sentence_fill": [],
  "gb_memory_palace": {},
  "boss_questions": [],
  "boss_meta": {},
  "reflection": {}
}
```

Important:

```txt
case_based_preview must NOT be emitted as runtime-bearing beta content until platform readiness is true.
```

---

### Layer 3 — Future beta platform full v2 export

This unlocks only after platform PRs land:

```json
{
  "flow_version": "v2",
  "case_based_preview": {},
  "flashcards": [],
  "memory_check": {},
  "practice_arc": {},
  "boss_arena": {},
  "reflection": {}
}
```

---

---

## 2.2 Book ingestion → chapter/section extraction → Flow v2 mapping

The generator must not assume pasted text as the only input path.

Required input chain:

```txt
Book/PDF/textbook source
→ locate chapter
→ locate section
→ extract section content
→ normalize extracted text
→ build source_map_json
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

The `source_map` becomes the factual anchor for every generated phase and game.

Downstream generation order:

```txt
extract_book_section
        ↓
build_source_map
        ↓
case_based_preview + flashcards    # parallel
        ↓
memory_check                       # after flashcards
        ↓
practice_arc_games                 # after learning sections
        ↓
boss_arena                         # after practice targets exist
        ↓
reflection_marking                 # last
        ↓
markdown + json exports
```

Every generated phase/game must point back to the relevant source concept IDs.


# 3. Corrected Platform Readiness Gate

## 3.1 New flag

Add generator config:

```env
PLATFORM_CBP_RUNTIME_READY=false
PLATFORM_UNLOCK_GATE_READY=false
PLATFORM_FULL_FLOW_V2_READY=false
```

## 3.2 Export behavior

```python
if export_target == "beta_platform":
    if PLATFORM_CBP_RUNTIME_READY:
        include case_based_preview
    else:
        omit case_based_preview from runtime content_json
        include it only in authoring package / ZIP
```

## 3.3 Default behavior

The generator must **not** flip new jobs to full runtime v2 until this checklist passes:

```txt
[ ] Homeworks ContentJSON has case_based_preview field.
[ ] Homeworks injector maps case_based_preview to a JS constant.
[ ] perfect_homework.html has screen-cbp or equivalent runtime surface.
[ ] Runtime has v2 dispatcher / state machine.
[ ] Runtime has Unlock Gate.
[ ] Practice Arc is gated by CBP + Flashcards + Memory Check.
[ ] Legacy homeworks still render.
```

---

# 4. Corrected Case-Based Preview Schema

## 4.1 Canonical schema

```python
class DecisionProcessExplanation(BaseModel):
    prompt: str
    expected_components: list[Literal["concept", "method", "mistake"]]
    rubric: dict
    sample_acceptable_answer: str
    eval_mode: Literal["ai", "rubric_ai"] = "ai"
    min_chars: int = 60
    options: None = None
```

```python
class CaseBasedPreview(BaseModel):
    title: str
    student_role: str
    case_type: str
    source_concept_ids: list[str]

    case_setup: CaseSetup
    checkpoints: list[CaseCheckpoint]  # exactly 3

    decision_process_explanation: DecisionProcessExplanation

    final_simulation: CaseSimulation
    feedback_summary: FeedbackSummary
    completion_rules: CompletionRules
```

## 4.2 Required order

```txt
1. case_setup
2. checkpoint_1
3. learning_block_1
4. checkpoint_2
5. learning_block_2
6. checkpoint_3
7. decision_process_explanation
8. final_simulation
9. feedback_summary
10. completion_rules
```

## 4.3 Important correction to checkpoint design

The original plan said every checkpoint must require “thinking.” That is too vague and may push the model toward open-ended checkpoint spam.

Corrected rule:

```txt
Checkpoints 1–3 are low-friction recognition / decision checks.
The production reasoning lives in decision_process_explanation.
```

Allowed checkpoint types:

```txt
identify
decide
justify_or_avoid_mistake
```

Allowed UI forms:

```txt
mcq
choice
short_select
true_false only if meaningful
```

Not allowed:

```txt
4th checkpoint
checkpoint-as-essay
checkpoint with no consequence
checkpoint that asks unrelated trivia
```

## 4.4 QA hard-fails

Reject CBP if:

```txt
decision_process_explanation missing
decision_process_explanation has options
decision_process_explanation can be passed with one word
decision_process_explanation appears after final_simulation
final_simulation missing correct/wrong paths
student is not decision-maker
textbook concept is not preserved
```

---

# 5. Corrected Memory Check Contract

## 5.1 Canonical memory check may be richer

Canonical generator can keep richer item types:

```txt
mcq
fill_blank
choose_explanation
true_false
tile_match
term_definition
formula_name
vocabulary_meaning
```

## 5.2 Beta export must down-map

Beta export must force:

```txt
type ∈ {"mcq", "fill_blank", "choose_explanation"}
```

Additional granularity goes into metadata:

```json
{
  "type": "mcq",
  "mc_subtype": "term_definition"
}
```

Example mapping:

| Canonical type | Beta type | Extra field |
|---|---|---|
| true_false | mcq | `mc_subtype: "true_false"` |
| tile_match | mcq or omit | `mc_subtype: "tile_match_source"` |
| term_definition | mcq | `mc_subtype: "term_definition"` |
| formula_name | mcq | `mc_subtype: "formula_name"` |
| vocabulary_meaning | mcq | `mc_subtype: "vocabulary_meaning"` |
| fill_blank | fill_blank | unchanged |
| choose_explanation | choose_explanation | unchanged |

## 5.3 Recommended near-term simplification

For PR-1 / PR-2, do not generate unsupported canonical Memory Check types at all.

Use only:

```txt
mcq
fill_blank
choose_explanation
```

This matches the platform prompt and avoids adapter magic until runtime is stronger.

---

# 6. Corrected Real-Life Challenge Export

## 6.1 Bind generator RLC to platform contract

Generator canonical `real_life_challenge` must either:

1. exactly implement platform `RealLifeChallengeCase`, or
2. adapt into platform `RealLifeChallengeCase` and validate before export.

## 6.2 Required platform shape

```txt
steps length == 5
step order:
  decision
  info_request
  final_decision
  concept_select
  reasoning
```

## 6.3 Required correctness invariants

```txt
decision/info/final_decision:
  options >= 2
  exactly 1 option has is_correct = true

concept_select:
  concept_chips >= 3
  exactly 1 chip has is_correct = true

reasoning:
  min_chars between 20 and 1000
```

## 6.4 Export conformance test

Add:

```txt
tests/test_beta_platform_contract.py
```

The test must import or vendor the platform schema and run:

```python
ContentJSON.model_validate(generator_beta_export)
```

Do not rely on a hand-written mirror only.

---

# 7. Corrected Prompt and Standard Source-of-Truth Plan

## 7.1 Do not create a divergent `prompts_v2/` island

The previous plan proposed a new generator-local `prompts_v2/`.

Corrected decision:

```txt
Subject/phase prompts come from the platform repo:
Homeworks/server/prompts/<subject>/

Project-level standards come from the NETS project standard files:
for CBP, use nets_case_based_preview_generation_standard_v1_1.md
```

So the generator consumes two pinned sources:

```txt
1. Platform prompt pack — runtime-aligned subject prompts
2. Project standards pack — flow/CBP/interactivity/gamification rules
```

## 7.2 Infra prompt registry

The uploaded `Infra.zip` is treated as the phase/game prompt registry for Flow v2 generation.

The generator must load the relevant prompts/specs by:

```txt
subject family
phase
game type
runtime export target
```

Current registry files detected:

- `Infra/Uzbek Specification/NETS_Uzbek_Language_Foundation_Review.md`
- `Infra/Case-Based Preview/nets_cbp_prompt_sciences.md`
- `Infra/Case-Based Preview/nets_cbp_prompt_languages.md`
- `Infra/Case-Based Preview/nets_cbp_prompt_math_family.md`
- `Infra/Case-Based Preview/nets_case_based_preview_generation_standard_v1.md`
- `Infra/Flow/New_Flow.md`
- `Infra/Flow/nets_homework_flow_v2.html`
- `Infra/Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_math_family.md`
- `Infra/Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_humanities.md`
- `Infra/Flashcards/Flashcard Prompts/flashcard_study_engine_documentation.md`
- `Infra/Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_languages.md`
- `Infra/Flashcards/Flashcard Prompts/nets_flashcard_game_prompt_sciences.md`
- `Infra/Gamified Practices/Real Life Challenge/Real_Life_Challenge_Specification.md`
- `Infra/Gamified Practices/Memory Matching/MemoryMatching.md`
- `Infra/Gamified Practices/Boss Arena/Boss_Arena_Specification.md`
- `Infra/Gamified Practices/TicTacToe/TicTacToe.md`
- `Infra/Gamified Practices/Jigsaw Matching/JigsawMatching.md`
- `Infra/Gamified Practices/Error Detection/Error_Detection_Specification.md`
- `Infra/Gamified Practices/Sentence Filling/SentenceFilling.md`
- `Infra/Flashcards/Quzilet Learning/Choose Correct Explanation/Choose_Correct_Explanation_Specification.md`
- `Infra/Flashcards/Quzilet Learning/Multiple Choice/Multiple_Choice_Specification.md`
- `Infra/Flashcards/Quzilet Learning/Fill in the blank/Fill_In_The_Blank_Specification.md`

Required registry behavior:

```txt
Do not invent phase/game prompts when a registry prompt exists.
Use the correct prompt by subject family and game type.
Record prompt file path + version/source hash in flow_manifest.json.
Fail QA if a required Flow v2 phase/game has no prompt/spec mapping.
```


## 7.3 Recommended implementation

Use one of:

```txt
Option A: git submodule
Option B: pinned copied prompt pack with sync script
Option C: package artifact published from platform repo
```

Recommended for now:

```txt
Option B — pinned copied prompt pack + sync script
```

Because it is simpler for Codex/Claude agents and avoids submodule weirdness.

## 7.4 Required files

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

## 7.5 Rule

Do not rewrite platform prompts in generator unless:

```txt
- platform prompt does not exist, or
- generator needs an authoring-only prompt not used by runtime
```

---

# 8. Corrected Flow Interpretation

## 8.1 Current locked flow

The user-provided Flow v2 source-of-truth still supports:

```txt
Learning Sections
→ Unlock Gate
→ Practice Arc
→ Boss Arena
→ Reflection
```

Boss Arena is not removed. It is the peak of the Practice Arc.

## 8.2 Corrected wording

Use this wording everywhere:

```txt
Practice Arc includes the Boss Arena as its mastery peak.
```

Avoid presenting Boss as a disconnected extra top-level island.

## 8.3 Real-Life Challenge position

RLC should be a Practice Arc mission.

Recommended model:

```txt
Case-Based Preview = guided low-stakes case that teaches.
RLC = higher-stakes reverse/applied test using same concept with changed numbers/context.
Boss Arena = integrated final reasoning proof.
```

## 8.4 RLC variants

Support both as variants:

```txt
expert_case_5_step
reverse_test_same_story_new_numbers
```

But beta export currently supports the 5-step expert case shape. Reverse-test variant must adapt into the 5-step structure or wait for platform support.

---

# 9. Corrected Difficulty / Classification Plan

## 9.1 Do not keep old Easy/Hard branch skipping

Old generator used `classify` to skip phases. That should die.

## 9.2 Keep difficulty as generation metadata

Add:

```python
class GenerationProfile(BaseModel):
    grade_band: str
    difficulty_band: Literal["light", "standard", "advanced"]
    pisa_target: str
    item_count_profile: dict
    language_complexity: str
```

## 9.3 Difficulty affects

```txt
number of Memory Check items
Practice Arc complexity
RLC PISA level
Boss encounter difficulty
hint density
rubric strictness
sentence complexity
```

Difficulty must **not** remove:

```txt
Case-Based Preview
Flashcards
Memory Check
Practice Arc
Boss/Mini-Boss
Reflection
```

---

# 10. Corrected Answer-Leak Rules

## 10.1 Exact server-only fields

The generator QA validator must assert these fields are not present in student-visible runtime export.

### Boss

Server-only / strip from client:

```txt
accepted
acceptable
ans
accepted_answers
answer_spec
```

### RLC

Server-only / strip from client:

```txt
is_correct
consequence
acceptable_keywords
```

### Sentence Fill

Server-only / strip from client:

```txt
answers
explanations
```

### Tile Match

Server-only / strip from client:

```txt
explanation
```

## 10.2 QA test

Add:

```txt
tests/test_no_answer_leak_beta_export.py
```

Assertions:

```python
assert "answer_spec" not in student_payload
assert "is_correct" not in student_payload
assert "acceptable_keywords" not in student_payload
assert "answers" not in student_payload
```

---

---

# 11. Human-readable Markdown content export

Every generation job must produce:

```txt
homework-content.md
```

This file is the human-readable content handoff. It must follow the Flow v2 divisions and include generated content only, not implementation instructions or frontend code.

Required Markdown structure:

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
The Markdown export mirrors the canonical package.
The Markdown export does not replace JSON runtime exports.
The Markdown export must include all generated games that are enabled for the homework.
The Markdown export must not include unsupported runtime claims.
```


# 12. Authoring-Only Fields

These fields are **not runtime-bearing** until platform support exists:

```txt
source_map
flow_manifest
qa_report
case_based_preview when PLATFORM_CBP_RUNTIME_READY=false
practice_arc canonical wrapper
boss_arena canonical wrapper
```

They are for:

```txt
authoring
QA
audit
traceability
future runtime
```

They must not be assumed to affect student runtime unless explicitly adapted into supported beta keys.

---

# 13. Weak Point Signal Output

Add:

```python
class WeakPointSignal(BaseModel):
    concept_id: str
    source_phase: str
    evidence: list[dict]
    severity: Literal["low", "medium", "high"]
    target_accounts: list[Literal["teacher", "parent", "admin"]]
    recommended_action: str
```

Reflection/Marking should output:

```json
{
  "weak_point_signals": []
}
```

Purpose:

```txt
Sub-threshold attempts become needs-attention signals, not just local feedback text.
```

---

# 14. Corrected Implementation Sequence

## Platform PR-0 — CBP and Unlock Gate runtime prerequisite

**Repo:** `s1gmamale1/Homeworks`

Must land before generator full-v2 default.

Tasks:

```txt
Add case_based_preview schema field.
Add injector constant CBP.
Add screen-cbp or equivalent runtime surface.
Add v2 dispatcher or guarded state machine.
Add Unlock Gate.
Add tests proving Practice Arc locked until CBP + Memory Check pass.
```

Acceptance:

```txt
A content_json with case_based_preview renders CBP.
Practice Arc remains locked until gate conditions pass.
Legacy rows still render.
```

---

## Generator PR-1 — AI Gateway + schema foundation

**Repo:** `s1gmamale1/Homework-Content-Generation-Automation`

Tasks:

```txt
Add provider-agnostic AI Gateway.
Keep Gemini provider.
Add provider interface for future Kimi/OpenAI/Anthropic.
Add Flow v2 canonical schemas.
Add DecisionProcessExplanation to CBP.
Add GenerationProfile instead of old branch-skipping classify.
```

---

## Generator PR-2 — Book ingestion + chapter/section extraction + SourceMap + prompt registry sync

Tasks:

```txt
Build source_map from extraction.
Add prompt sync from platform repo.
Pin source commit.
Use platform memory-check prompt instead of duplicating it.
```

---

Tasks:

```txt
Accept book/PDF/textbook input.
Locate chapter and section.
Extract section content.
Normalize extracted text into clean source material.
Build source_map_json from the extracted section.
Load Flow v2 prompts/specs from the Infra prompt registry.
Sync platform prompts and project standards.
Pin prompt/standard source versions in flow_manifest.json.
```


## Generator PR-3 — Learning Sections

Tasks:

```txt
Generate case_based_preview canonical package.
Generate flashcards.
Generate memory_check using only supported 3 modes initially.
Persist canonical JSON.
Export safe beta memory_check.
```

Important:

```txt
Do not emit case_based_preview into beta runtime content_json unless PLATFORM_CBP_RUNTIME_READY=true.
```

---

## Generator PR-4 — Practice Arc + RLC

Tasks:

```txt
Generate canonical practice_arc.
Generate RLC as a Practice Arc mission.
Adapt RLC to platform RealLifeChallengeCase.
Validate against platform schema before export.
```

---

## Generator PR-5 — Boss Arena

Tasks:

```txt
Generate canonical boss_arena.
Adapt to beta boss_questions + boss_meta.
Preserve Why → How → What in prompt/rubric.
Assert no boss answer keys leak.
```

---

## Generator PR-6 — Reflection / weak-point signals / manifest / Markdown export

Tasks:

```txt
Generate reflection.
Generate weak_point_signals.
Generate flow_manifest.
Generate homework-content.md.
Mark authoring-only fields clearly.
```

---

## Generator PR-7 — Contract conformance and QA fence

Tasks:

```txt
Test beta export against platform ContentJSON.
Test MemoryCheck export uses only supported types.
Test RLC exactly matches 5-step platform contract.
Test CBP does not silently go into unsupported runtime.
Test answer leak fields are absent from student payload.
Test banned old phase labels are absent from canonical v2.
```

---

## Generator PR-8 — UI preview update

Tasks:

```txt
Show canonical package sections.
Show which parts are runtime-supported vs authoring-only.
Show platform compatibility status.
Show QA report and export warnings.
```

---

# 15. Hard Gates Before “v2 Default”

Do not declare generator “Flow v2 default ready” until all are true:

```txt
[ ] Book/chapter/section extraction produces clean extracted_section_content.
[ ] Every phase/game has a prompt/spec mapping in the Infra prompt registry.
[ ] homework-content.md mirrors the canonical package and contains content only.
```

```txt
[ ] Platform renders CBP.
[ ] Platform gates Practice Arc.
[ ] Generator beta export validates against platform ContentJSON.
[ ] MemoryCheck export uses supported types.
[ ] RLC export passes exact platform validator.
[ ] Boss export strips answer keys.
[ ] source_map and flow_manifest are marked authoring-only.
[ ] weak_point_signals exist in reflection output.
[ ] No old phase names in canonical v2 package.
[ ] Legacy generator mode still available.
```

---



# 16. Final Corrected Recommendation

The corrected migration strategy is:

```txt
Build book-to-canonical Flow v2 generation now.
Treat CBP DPE as required by `nets_case_based_preview_generation_standard_v1_1.md`.
Export beta-platform-safe content now.
Block full runtime-v2 default until platform CBP + Unlock Gate exist.
Validate beta export against the platform’s real schema.
Use platform prompts plus project standards as source-of-truth.
```

This avoids the two biggest failure modes:

```txt
1. Silent content loss: generator emits CBP, runtime ignores it.
2. Hard validation failure: generator emits richer shapes, platform rejects them.
```

The plan is still strong. It just needed a seatbelt before the engine got strapped to a rocket.

---

# 17. Reverse-Test RLC Caveat

The generator may carry a canonical Practice Arc variant called:

```txt
reverse_test_same_story_new_numbers
```

This variant means:

```txt
same underlying concept
changed numbers/context
student infers the unnamed formula or method
```

However, the current beta platform RLC runtime contract is the 5-step expert-case shape:

```txt
decision → info_request → final_decision → concept_select → reasoning
```

The `concept_select` step gives students concept chips to recognize from. That is useful, but it does **not fully express** the reverse-test pedagogy where the student must infer the unnamed formula/method without being handed concept labels.

**Implementation rule:**

```txt
For now, adapt reverse-test RLC into the 5-step platform contract only as a compatibility bridge.
Do not claim it is a faithful runtime implementation of the reverse-test mechanic.
```

**Future platform requirement:**

Add an explicit RLC step kind such as:

```txt
infer_method
infer_formula
```

before reverse-test can be called fully runtime-native.
