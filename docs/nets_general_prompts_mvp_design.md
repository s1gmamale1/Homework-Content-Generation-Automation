# NETS Flow v2 — General Prompts MVP (Design Spec)

**Status:** Approved design (brainstormed 2026-06-01). Ready for an implementation plan.
**Author target:** a separate execution session.
**Source of truth for content:** `docs/Infra_prompts/` (research specs) + `docs/Infra_prompts/Flow/New_Flow.md` (flow).
**Live code anchors:** `app/services/prompts.py` (loader), `app/services/flows.py` (flow), `app/services/pipeline.py` (orchestration), `app/services/agent.py` (`STRUCTURED_PHASE_SCHEMAS`).

> **Two-path scope.** This spec is **Path A — the lean MVP** (ships now, no schema change). **Path B** — re-introducing the subject-matched CBP-mode games after lightening `CbpModeGame` — is a separate follow-on workstream, sketched in §10. We do Path A first, then Path B.

---

## 1. Goal & non-goals

**Goal (Path A).** Ship an MVP where homework generates from **one set of general, subject-parameterized prompts** that work for every subject, driven by a **single flow** (no `classify`, no easy/hard, no CBP-mode games). Subject-specific prompt files stay on disk, untouched and inert, ready to become per-subject overrides later.

**Why no CBP-mode games in the MVP.** The four CBP-mode games (memory-match/tictactoe/jigsaw/sentence) inherit the **entire** Case-Based Preview shell, which makes a single one take ~20+ min to generate (worklog 0018: a live `practice-jigsaw` run hit >21.5 min and was killed). The two standalone games (RLC, Error Detection) are light and stay. The CBP-mode games return in **Path B** once their schema is lightened.

**Non-goals.**
- No subject-specific prompt authoring or edits (existing `prompts/<subject>/*` left exactly as-is).
- No easy/hard mechanism. No `reading` phase. No `classify`.
- **No schema changes** (Path A uses the existing light `RealLifeChallenge` / `ErrorDetection` schemas as-is).
- No frontend, provider/router, queue, or extract/source-map changes.

---

## 2. Decisions (locked during brainstorming)

1. **Resolution:** general-only now via `prompts/_general/<phase>.md`; subject dirs inert; a documented switch enables subject-override later.
2. **Practice Arc (Path A):** `RLC + Error Detection` only — both work for any subject and are light to generate. **No third subject-matched game** in the MVP (deferred to Path B).
3. **Classify removed** from the active system (code/flow/schema). The dormant `classify.md` subject files are **left on disk untouched** — they become dead.
4. **Language:** formal Uzbek ("Siz") is the default for **every** subject; no language/CEFR special-casing (deferred). (See §3.3.)
5. **Single flow for all subjects** (the sequence is identical regardless of subject in Path A).

---

## 3. Architecture

### 3.1 Prompt resolution (`app/services/prompts.py`)

Today `get_prompt(subject, phase_name)` reads `prompts/<subject>/<phase>.md`. Change it to resolve **general** by default and inject the subject:

- Add `GENERAL_DIR = "_general"` and a feature switch `USE_SUBJECT_PROMPTS = False` (module constant; later a setting).
- New resolution:
  ```
  if USE_SUBJECT_PROMPTS and (PROMPTS_DIR/subject/f"{phase}.md").exists():
      dir = subject          # future per-subject override (Path B+)
  else:
      dir = GENERAL_DIR      # MVP path — always general
  ```
- **Subject parameterization:** general prompt bodies contain the literal token `{{SUBJECT}}`. After loading, `get_prompt` substitutes it with a human-readable label (`SUBJECT_LABELS`, e.g. `physics → "Physics (Fizika)"`; fall back to the raw key). One file adapts to the running subject.
- `_load_subject` is generalized to `_load_dir(dirname)`; the cache key becomes the resolved dirname. `load_all()` preloads `_general` once (plus subject dirs only if `USE_SUBJECT_PROMPTS`).
- `get_prompt_hash` just hashes the resolved general body. This hash is **provenance only** (recorded on `agent_usages` rows); it does **not** drive cross-job reuse — `extract`, the only reused phase, uses its own hardcoded `"builtin:extract:v1"` (`pipeline.py:915`), never `get_prompt_hash`. So all subjects sharing one general file → one shared per-phase hash is harmless. (This corrects an earlier draft that wrongly claimed the hash had to be subject-distinct "for extract reuse.")
- **Preserve `get_prompt`'s 3rd `provider_suffix` parameter** (`prompts.py:32`) — the resolver keeps the same signature.
- Existing call sites (`pipeline.get_prompt(subject, phase)`) stay unchanged.

### 3.2 The single flow (`app/services/flows.py`)

Replace `SUBJECT_FLOWS` (the easy/hard, per-subject dict) with one flat sequence used by every subject:

```python
SUBJECTS = ["biology", "english", "geometriya-g7-11", "history",
            "kimyo-g7-11", "math-algebra", "physics"]

GENERAL_FLOW = [
    "case-based-preview", "flashcards", "memory-check",
    "practice-rlc", "practice-error-detection",
    "boss-arena", "reflection",
]

def flow_for(subject: str) -> list[str]:
    return list(GENERAL_FLOW)   # identical for all subjects in the MVP
```

- Remove `has_classify`, `easy`, `hard`, and the per-subject dict. `SUPPORTED_SUBJECTS = sorted(SUBJECTS)`.
- Whatever consumes `SUBJECT_FLOWS["easy"/"hard"]` in `pipeline.py` now calls `flow_for(subject)` (one sequence, no branch).
- `PHASE_DEPS`: keep `memory-check ← flashcards`; `practice-rlc ← case-based-preview, flashcards`; `practice-error-detection ← case-based-preview, flashcards, memory-check`; `boss-arena ← case-based-preview, flashcards, memory-check`; `reflection ← case-based-preview, boss-arena`. **Remove the `reading` entry and all CBP-mode game entries.** Drop the stale `preview-*` alias comments.
- Clean dead `MAX_OUTPUT_TOKENS_BY_PHASE` legacy keys (`preview-*`, `real-life`, `consolidation`); fix the misleading module docstring (flow.md is not the source of truth — New_Flow.md + this spec are).

### 3.3 Language handling

There is **no language detection in the system** — language is fixed in prompt text (the Uzbek Language Foundation spec; `app/` has no language field, `GenerationProfile.language` is unused). For the MVP:

- **Default = formal Uzbek ("Siz", never "sen") for ALL subjects**, formulas/numbers/units preserved verbatim, modern professional (non-bazaar) contexts. A single shared language block, reused across every general prompt — no per-subject language branching.
- The model also mirrors the source `lesson_context`; the `english` subject (English source) will naturally surface English terms — acceptable as-is for the MVP.
- **Deferred (NOT MVP):** explicit language control, CEFR leveling, English-scaffolding branch.

---

## 4. General prompts to author (`prompts/_general/`, 7 files)

**Authoring method — generalize a verified subject prompt; don't re-derive cold from the spec.** The existing `prompts/<subject>/*.md` already encode the WS1–WS4 fixes (learning blocks, memory-check option-objects, `accepted_variations`, `misconception`, etc.). Seed each general prompt by **generalizing a clean subject prompt** for that phase — *read* it (never modify `prompts/<subject>/`), strip the subject specifics, insert `{{SUBJECT}}` — and use the Infra spec only as a **cross-check**. This inherits the existing verification instead of re-opening four settled workstreams. Pick a **clean** representative — avoid `physics/flashcards.md` (a mislabeled math clone) and the `biology/history/english` reflections (stale v1 phase refs). The "must enforce" column below is the cross-check checklist. **One fix is still missing from the subject prompts and must be applied:** the CBP **JSON example** omits `learning_block_1/2` (the prose has them) — add them.

| File | Source spec(s) | Must enforce (key v2 points) |
|---|---|---|
| `case-based-preview.md` | `Case-Based Preview/nets_case_based_preview_generation_standard_v1.md` (+ family files as reference) | 10-slot order; **`learning_block_1` & `learning_block_2` present in BOTH prose AND the JSON example** (the verified gap); exactly 3 low-friction checkpoints; DPE open-ended, `options:null`, concept/method/mistake, before the simulation; method unnamed in the case body; `source_concept_ids` from the source map. |
| `flashcards.md` | `Flashcards/Flashcard Prompts/*` + `flashcard_study_engine_documentation.md` | v2 card shape: stable unique `id`, required `type` + `difficulty`, optional `hint/explanation/example/misconception/cluster`. |
| `memory-check.md` | `Flashcards/Quzilet Learning/{Multiple Choice,Fill in the blank,Choose Correct Explanation}/*` | 3 kinds; **option objects** `{text,is_correct,reason}` (4 for option-kinds, one correct); `fill_blank` → `blanks` `{answer,accepted_variations}`, no options; `why_prompt` + `expected_reasoning_keywords`; per-item `flashcard_id`; `pass_threshold` = 0.60. |
| `practice-rlc.md` | `Gamified Practices/Real Life Challenge/Real_Life_Challenge_Specification.md` | First-person expert; predict→decide→justify+confidence; distractors = real misconceptions; Strip Test; `concept_ids`. **Do NOT invent a "reverse-test" variant** (the spec has none — the current subject prompts fabricate it). |
| `practice-error-detection.md` | `Gamified Practices/Error Detection/Error_Detection_Specification.md` | Exactly one `is_error` block; type-the-correction; `why_prompt` mandatory for math/science patterns; `concept_ids`. |
| `boss-arena.md` | `Gamified Practices/Boss Arena/Boss_Arena_Specification.md` | Reasoning content; **Why→How→What all non-empty**; no MCQ options; `concept_ids`; difficulty/base_damage/hints/feedback; ≥4 questions. |
| `reflection.md` | `Flow/New_Flow.md` (Reflection/Debrief/Marking) | Short debrief (hardest? main decision? mistake to avoid? what clicked?); no references to deleted phases; tied to actual performance. |

All 7 share the language block (§3.3) and the `{{SUBJECT}}` token. **`extract`/source-map stay pinned & unchanged** (not in `_general`). The 4 CBP-mode game prompts are **Path B**.

---

## 5. Code changes (summary)

1. `app/services/prompts.py` — `_general` resolver (keeping the `provider_suffix` param), `USE_SUBJECT_PROMPTS` switch, `{{SUBJECT}}` substitution, `SUBJECT_LABELS`. Hash = the resolved general body (provenance only). (§3.1)
2. `app/services/flows.py` — `GENERAL_FLOW` + `flow_for()`; remove easy/hard/has_classify and the per-subject dict; fix `PHASE_DEPS` (drop `reading` + CBP-mode games); prune dead token-cap keys; fix docstring. (§3.2)
3. **Classify removal (real surgery — size it accordingly):** drop `"classify"` from `STRUCTURED_PHASE_SCHEMAS` (`agent.py`) + remove the `ClassifyDecision` registration. In `pipeline.py` this is wider than the head step: `difficulty` is threaded through ~4 function signatures (`:688`, `:776`, `:911`) and ~8 call sites (`:512`, `:611`, `:721`, `:826`, `:1020`, `:1037`), plus `flow[difficulty]` (`:582`), `set_difficulty` (`:577`), and the classify-handling block (`:568–585`). Remove the classify head phase and unthread `difficulty` throughout (or pin it to a constant); nothing may call `flow["easy"/"hard"]`. Leave `app/schemas/classify.py` and the `classify.md` files on disk (dead, untouched). *(Easy/hard is intentionally gone — do not reintroduce a difficulty branch.)*
4. `prompts/_general/` — the 7 new files (§4).
5. Tests — §7.

---

## 6. Untouched / out of scope
`prompts/<subject>/*` (all), `app/schemas/*` (no schema edits in Path A), the provider router, the worker/queue, the frontend, the extract/source-map pin, and the CBP-mode game schemas/prompts (Path B).

---

## 7. Testing & acceptance

- **Prompt coverage:** rewrite `tests/services/test_prompt_coverage.py` to assert every phase in `GENERAL_FLOW` resolves to a file in `prompts/_general/` for all subjects (fail-fast invariant, now general).
- **Classify gone:** a test asserting `"classify" not in STRUCTURED_PHASE_SCHEMAS`, no `has_classify`/`easy`/`hard` keys remain, and `flow_for(subject)` returns the 7-phase `GENERAL_FLOW` for every subject.
- **Resolver:** unit test that `get_prompt(subject, phase)` reads `_general` and substitutes `{{SUBJECT}}`; and that `USE_SUBJECT_PROMPTS=True` + an existing subject file would override (guards the future switch).
- **Schema validity (real):** `claude`-CLI smoke on ≥2 subjects (e.g. physics + english) for `case-based-preview`, `memory-check`, `practice-rlc`, `practice-error-detection`, and `boss-arena` → outputs `model_validate_json` clean against the live schemas (esp. CBP with both learning blocks). Output is formal Uzbek for all subjects.
- **Suite green:** `uv run python -m pytest tests/ -q`.

**Acceptance:** a full job on any subject runs `CBP → flashcards → memory-check → RLC → Error Detection → Boss → Reflection` from `_general` prompts, no `classify` step, schema-valid throughout, in practical wall-clock (no 20-min phases); subject dirs and schemas unmodified.

---

## 8. Risks & open decisions
- **CBP-mode heaviness is sidestepped in Path A** (no CBP-mode game runs). It is the entire subject of Path B.
- **`{{SUBJECT}}` convention** must be applied consistently in all 7 files or substitution silently no-ops; the coverage test should also assert the token is present and gets replaced.
- **Dead docs widen:** with general prompts active, the per-subject `flow.md` + `instruction.md` (already stale v1) and the dormant `classify.md` all become fully dead — note them together in the worklog cleanup. Do **not** edit or delete the subject prompt files.
- **Confirm at review:** `classify.md` dormant (not deleted).

---

## 9. Execution notes
- Work on an **isolated branch/worktree off current HEAD (`Nggaev-v2`)**; another session is committing to this repo. Path A touches `prompts/_general/` (new), `flows.py`, `prompts.py`, `agent.py`, `pipeline.py`, tests — coordinate to avoid collisions.
- Build order: (1) `prompts.py` resolver + switch, (2) the 7 `_general` prompts, (3) `flows.py` single flow, (4) classify removal, (5) tests, (6) real smoke. Prompts can be authored in parallel; flow/classify/test changes come after.

---

## 10. Path B (next workstream — separate spec)

**Goal:** re-introduce the four CBP-mode games (memory-match/tictactoe/jigsaw/sentence) as one subject-matched 3rd practice game, made practical by **lightening `CbpModeGame`**.

- **Lighten the schema** (the filed worklog-0018 follow-up): `CbpModeGame` should NOT inherit the full `CaseBasedPreview`. Target a compact shape — a short scenario/framing + the typed `interaction_payload` + *optionally* one checkpoint + the DPE — dropping case_setup / 3 checkpoints / both learning blocks / final_simulation / feedback / completion. This is a real schema + synth-render + prompt + test change. Its own brainstorm → spec → plan.
- **Re-add the subject-matched game** to the flow via a `SUBJECT_GAME` map (approved earlier): biology/history→memory-match · physics/kimyo/math-algebra→tictactoe · geometriya→jigsaw · english→sentence. Flow becomes `… → RLC → Error Detection → {subject game} → Boss → Reflection`.
- **Author the 4 CBP-mode general prompts** in `prompts/_general/` against the lightened schema (using the `Gamified Practices/{MemoryMatching,TicTacToe,JigsawMatching,SentenceFilling}.md` specs, but emitting the compact shape).
- **Acceptance:** each CBP-mode game generates in practical wall-clock (target ≲ a few min), schema-valid, with its typed payload.
