# Change Details

Full record of every change. Newest at the top. Each entry mirrors an entry in
`SUMMARY.json` but with the complete detail needed to retrace the work.

Format per entry:

```
## YYYY-MM-DD HH:MM — <branch> — <title>

**Why:** reason / request behind the change
**What changed:**
- file/path — what was done and why
- file/path — ...
**Notes:** caveats, follow-ups, anything non-obvious
```

---

## 2026-05-29 — alphaq — SourceMap foundation + Practice Arc game schemas (deliverable re-scoped)

**Why:** Deliverable #1 was re-scoped mid-flight. The original "skills mapping"
foundation was KEPT (it IS the SourceMap), and the new first deliverable is the
six Practice Arc games — RLC, Error Detection, Memory Matching, TicTacToe,
Jigsaw, Sentence Filling — each conforming to its Infra spec, tracing to
concepts, no disconnected drills. DEP: SourceMap only, fully parallel. Scope this
pass: schemas + conformance validators + concept-trace + tests (generation /
prompts / persistence deferred to the next step).

**SourceMap (kept from the aborted skills deliverable):**
- app/schemas/skills.py — SourceConcept, TargetSkill, SkillRegistry, SkillMapped
  (permissive; enforcement in skill_map).
- app/services/skill_map.py — validate_registry, validate_mission_mapping,
  resolve_skills, and (new) validate_concept_trace (empty = disconnected drill;
  dangling = unknown concept).
- app/services/gemini.py — extract_skill_registry (structured, builtin prompt).
- app/models/homework_job.py — skills_json column; migration 0011
  (down_revision b7c91d4e2a55); app/repositories/jobs.py set_skills_json.
- app/services/pipeline.py — best-effort skills extraction in the head after
  extract; persists skills_json; non-fatal for foundation.

**Practice Arc game schemas (new):**
- app/schemas/practice_games/ — NEW PACKAGE (named practice_games, NOT games, to
  avoid shadowing the legacy app/schemas/games.py GamesPack module — that
  collision was caught and fixed during verification).
  - common.py — shared CBP skeleton: MCQOption/MCQCheckpoint(identify|decide|
    justify), DecisionProcessExplanation, ConsequencePath/FinalSimulation,
    FeedbackSummary, CommonMistake(provenance), CaseMetadata, CaseBasedInteraction.
  - memory_matching.py, sentence_filling.py, tictactoe.py, jigsaw_matching.py —
    the 4 CBP-mode games (extend CaseBasedInteraction + game-specific payload).
  - error_detection.py — distinct spot+correct shape (one error block, accepted
    variants, why_prompt, patterns math/grammar/diagram).
  - RLC reused from app/schemas/real_life.py.
- app/services/game_conformance.py — per-game Infra-conformance validators +
  validate_game dispatch. CBP: exactly 3 ordered MCQ checkpoints, 1 correct each,
  DPE has 3 components, both consequence paths; per-game counts. Error Detection:
  exactly 1 error block, >=3 blocks, why mandatory for math/science. RLC: reuses
  beta_export.to_platform_case (must still satisfy the platform 5-step). All
  failures surface as GameConformanceError (concept-trace wrapped via _trace).
- tests/unit/test_game_conformance.py — valid instance per game + key violations.
- tests/unit/test_skill_map.py — registry + mapping + concept-trace.

**Verification:** py_compile clean. New tests 34/34 pass. Full suite in venv:
232 passed, 2 failed, 31 errors — the 2 failures are the SAME pre-existing ones
(test_flows SVG, test_pipeline_synth reading), the 31 errors are the same
integration-harness issue (pytest9/asyncio + needs Postgres). No new failures
from this work.

**Notes:** Package-name collision (games/ vs legacy games.py) was the one real
snag — caught at first full-suite run, fixed by renaming to practice_games.
Deferred to next step (not this deliverable): per-game generation prompts,
structured Gemini wiring, DB persistence columns, and pipeline fan-out of the
games. The conformance validators are ready to gate that generation when it lands.

<!-- Example:
## 2026-05-29 14:30 — alphaq — Added tracking files

**Why:** User wants every change logged for later review.
**What changed:**
- changelog/SUMMARY.md — created short-form log
- changelog/DETAILS.md — created detailed log
- WISHLIST.md — created bug tracker for testing phase
**Notes:** Update both SUMMARY and DETAILS on every change going forward.
-->

## 2026-05-29 — alphaq — Phase 4 Real-Life Challenge vertical slice (tasks 1–4)

**Why:** Phase 4 transition from prose to canonical structured JSON for the
Real-Life Challenge. Authoritative spec:
docs/nets_generator_flow_v2_transformation_plan_FINAL_PATCHED.md. The platform
runtime validates RLC against a strict 5-step contract
(decision → info_request → final_decision → concept_select → reasoning) — NOT
the old Bloom structure that was in the existing english prompt.

**What changed:**
- app/schemas/platform/__init__.py — new package exporting the platform mirror.
- app/schemas/platform/real_life_challenge.py — PINNED MIRROR of the platform
  RealLifeChallengeCase. Enforces: 5 steps in fixed order; decision/info_request/
  final_decision have >=2 options + exactly 1 correct; concept_select has >=3
  chips + exactly 1 correct; reasoning min_chars in [20,1000]. Marks server-only
  fields (is_correct, consequence, acceptable_keywords). Reconstructed from
  §6.2/§6.3 since the real Homeworks schema isn't vendored — swap is one file.
- app/schemas/real_life.py — canonical RealLifeChallenge: richer (Infra pedagogy:
  role/task/context/prediction/expert_feedback) with 5 step objects that map 1:1
  onto the platform contract for deterministic down-mapping.
- app/schemas/__init__.py — export RealLifeChallenge + RLC* models.
- app/services/gemini.py — import RealLifeChallenge, add "real-life" to
  STRUCTURED_PHASE_SCHEMAS. (real-life already in _SVG_PHASES, line 296.)
- app/models/homework_job.py — added real_life_json JSONB column.
- alembic/versions/0010_homework_real_life_json.py — migration, down_revision
  a3f5e2d18c44 (head = 0009 queue columns).

**Notes:** Schema source was hand-mirrored (user had no preference). Risk: the
hand-mirror may diverge from the real platform schema — the doc warns against
relying on a hand-mirror only. Flagged for sync when Homeworks schema is
available. Migration revision IDs are hashes, not filename numbers; verified
0009's id is a3f5e2d18c44 before chaining.

## 2026-05-29 — alphaq — Phase 4 RLC slice: pipeline, beta export, download, prompt, tests (tasks 5–10)

**Why:** Complete the RLC vertical slice — persist canonical output, export a
platform-validated student-safe payload, and prove the contract + no-leak rules
with tests.

**What changed:**
- app/repositories/jobs.py — added set_real_life_json (mirrors set_reading_json).
- app/services/pipeline.py — registered "real-life" in _JSON_COLUMN_SETTERS so
  structured output persists to real_life_json; added a "real-life" branch to
  _synth_md_for_structured (role/task/context/prediction summary, getattr-based
  so it tolerates partial objects like the other branches).
- app/services/beta_export.py — NEW. to_platform_case() down-maps canonical →
  platform RealLifeChallengeCase and validates (raises on contract violation =
  stop-the-line). adapt_to_beta() returns (student_safe_payload, bridge_warning):
  recursively strips is_correct/consequence/acceptable_keywords; warns when the
  reverse-test variant is bridged into 5-step (§17, not faithful runtime).
- app/api/v1/jobs.py — import beta_export; added _student_safe_real_life() helper
  and "real-life.json" to the download ZIP. On absent/malformed canonical it
  emits {"steps": []} rather than 500-ing the download.
- prompts/english/real-life.md — rewritten from the old Bloom prose to the
  platform 5-step JSON contract, sourced from the Infra RLC spec. Keeps
  first-person expert POV, grade-anchored roles, scope-lock, Strip Test, and
  inline-SVG-only guidance; documents server-only evaluation fields.
- tests/unit/test_beta_platform_contract.py — NEW. Valid canonical → platform
  validator passes; dict input accepted; reverse-test warns; missing-correct,
  <3 chips, and min_chars-out-of-range all rejected.
- tests/unit/test_no_answer_leak_beta_export.py — NEW. Asserts no server-only
  field reaches the student payload (top-level + nested), options keep label
  only, reasoning keeps prompt/min_chars, concept chips lose is_correct.

**Verification:** All 12 changed/new Python files compile (py_compile). The two
new tests pass (11/11) under system Python + pydantic 2.13. The FULL suite could
NOT be run here — no .venv exists in the worktree and app deps (loguru,
google-genai, sqlalchemy, fastapi) are not installed; pytest is present but the
service/integration layers fail to import. Build/full-test verification must run
in the proper uv environment before commit. Checked existing tests: none assert
the ZIP file set or the real-life prompt format, and test_real_life_has_cap
(2200) is untouched — no regressions expected.
