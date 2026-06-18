# Per-phase custom prompts + phase subset selection

**Date:** 2026-06-18
**Status:** Approved (brainstorm) — pending plan
**Supersedes:** the earlier append-only single-prompt design (same filename, prior commit). That design is abandoned: it appended one prompt to every phase, ran all phases, excluded the judge, and only *documented* the dedup trap. The corrected requirements below replace it.

## Problem

On the section launch page, the user wants, per generation:
- **Per-phase custom prompts:** for each of the 11 phases, optionally upload a `.md` that **replaces** that phase's built-in prompt (not appends).
- **Phase subset:** optionally generate only a chosen subset of the 11 phases instead of all of them.

The system must load these for the run but never write them to `prompts/`. Mirror the same capability on the batch endpoint.

This touches generation, so acceptance is a **real CLI smoke run** (not a code-structure argument) proving (1) a custom phase prompt actually changes that phase's output, and (2) the judge grades against the custom prompt.

## The four gate conditions (hard requirements)

### Gate 1 — Section dedup must not silently reuse a plain job

`generate` (and the batch launcher) reuse an existing pending/running/done job for a `(book, section[, transport])` via `jobs_repo.find_active_for_section` (jobs.py:60). The lookup considers **only** book + section + transport — nothing about prompts or phase selection. So a custom/subset launch over a section that already ran would silently hand back the old built-in / all-11 output — wrong result, no error.

**Fix (chosen):** **treat any custom-or-subset launch as a forced fresh run.** When the request carries custom prompts OR an explicit phase subset, skip the natural-key reuse check entirely (behave as `force=True` for the reuse step), so it always creates a new job. The task allowed either a dedup-key marker or force-fresh; force-fresh is chosen for simplicity and zero false-reuse risk. The advisory lock (layer 3) and header-key idempotency (layer 1) still apply. Applies identically at the **batch** endpoint (custom/subset batch launches create fresh jobs; never adopt/skip plain jobs).

### Gate 2 — The judge needs the custom prompt too

`phase_judge.judge` re-loads the built-in contract at `phase_judge.py:148` (`get_prompt(subject, phase_name)`) to score the phase. If the generator used a custom prompt but the judge scores against the built-in rules, it will flag spurious "violations" and trigger a wasteful regeneration.

**Fix:** add `contract_override: Optional[str] = None` to `judge`; when set, use it instead of `get_prompt(...)`. The pipeline passes the **same per-phase prompt text the generator used** as the override. The post-regen judge call (pipeline.py:781) gets the same override. The regen prompt (pipeline.py:772) already builds on `base_phase_prompt`, which will be the custom prompt — so the regen path needs no extra change beyond using the custom base.

### Gate 3 — Phase subset must respect dependencies

Phases feed each other via `flows.PHASE_DEPS` (flows.py:52): e.g. `boss-arena` needs preview + flashcards + memory-check; `reflection` needs preview + boss-arena. If the user picks a phase but not its dependencies, it would run with missing inputs.

**Fix:** compute the **dependency closure** of the user's selection server-side at launch (a new pure helper `flows.expand_phase_selection(subject, selected) -> (ordered_phases, added_deps)`):
- Start from the user's selected phases; transitively add every `PHASE_DEPS` ancestor (alias-resolved against the subject's live flow), iterating to a fixpoint.
- Return the closure **ordered by the subject's canonical flow order**, plus the set of phases that were auto-added.
- The endpoint stores the closure as the job's `phases` list and **returns `added_phases`** so the UI can show "we also added X, Y because Z needs them." Don't silently run broken; don't hard-block.
- `extract` is always implicitly included (the head phase every content phase depends on); it is never part of the user-selectable 11 and never replaceable.

### Gate 4 — Provenance hash from the real text

Content-phase outputs are **not** reused across jobs (only `extract` is — pipeline.py:628), so there is no data-corruption risk. But unless usage/provenance records carry a hash of the **actual** prompt text, every custom run looks identical to a built-in run in the logs.

**Fix:** the effective `prompt_hash` for a phase becomes `sha256(custom_text)` when that phase used a custom prompt, else the existing `get_prompt_hash(subject, phase_name)` (built-in file hash). It is:
- written to `phase_outputs.prompt_hash` (already the provenance field, set via `phase_repo.create_or_reset` — pipeline.py:608/613), and
- tagged onto the `agent_usages` row for the generator call via the recorder's `extra_envelope` (`{"prompt_hash": <effective>}` into `raw_envelope`), so usage rows are distinguishable too.

No new prompt-hash column is added (reuses `phase_outputs.prompt_hash` + the existing `agent_usages.raw_envelope` JSON).

## Storage

**No 11 columns.** Two new nullable columns, mirrored on job and batch:

| Column | Type | Meaning |
|---|---|---|
| `custom_prompts` | JSONB (nullable) | `{ phase_name: custom_md }` — only the phases the user overrode. NULL/`{}` = all built-in. |
| `phases` | JSONB (nullable) | ordered list of content phases to run (the dependency closure). NULL = all 11 (default flow). |

- On `homework_jobs` and on `batches` (so a batch records what it launched and each job it creates inherits both).
- `extract` is never a key in `custom_prompts` and never listed in `phases` (it's the always-on head).

## Data flow

```
section.tsx
  per-phase: FileReader.readAsText → customPrompts[phase] = text
  phase pickers → selectedPhases[]   (UI also shows auto-added deps, see Gate 3)
  → api.generate({ ..., custom_prompts, phases })
  → POST .../generate
       validate: phase names ∈ flow; each custom_md ≤ 20 000 chars; total payload guard
       expand_phase_selection(subject, phases) → (closure, added_phases)
       custom OR subset present ⇒ force-fresh (skip find_active_for_section)   [Gate 1]
  → jobs_repo.create(custom_prompts=..., phases=closure)
  → homework_jobs.custom_prompts / .phases
  → pipeline.run() reads job.custom_prompts / job.phases
       content sequence = closure (flow-ordered) instead of full flow_for(subject)
       per phase: prompt = custom_prompts.get(phase) or get_prompt(subject, phase)   [replace]
       effective_hash = sha256(custom) if custom else get_prompt_hash(...)            [Gate 4]
       judge(..., contract_override = custom_prompts.get(phase))                      [Gate 2]
  → response includes added_phases (FE surfaces it)
```

Batch mirrors this: `BatchLaunchRequest` gains `custom_prompts` + `phases`; the closure + force-fresh logic run per target lesson; the batch row stores both.

## Components

### Backend

| File | Change |
|---|---|
| `alembic/versions/0027_*.py` | Add nullable `custom_prompts JSONB` + `phases JSONB` to `homework_jobs` **and** `batches`. |
| `app/models/homework_job.py`, `app/models/batch.py` | Add the two `Mapped[Optional[...]]` columns. |
| `app/services/flows.py` | New `expand_phase_selection(subject, selected) -> tuple[list[str], list[str]]` (closure + added), pure, fixpoint over `PHASE_DEPS` alias-resolved. |
| `app/schemas/job.py` | `GenerateRequest.custom_prompts: dict[str,str] \| None`, `phases: list[str] \| None`. |
| `app/api/v1/batch.py` | `BatchLaunchRequest` gains the same two fields. |
| `app/api/v1/jobs.py` + `app/api/v1/batch.py` | Validate (phase names, per-prompt length, closure); force-fresh when custom/subset; pass to `jobs_repo.create`; `/generate` returns `added_phases`. |
| `app/repositories/jobs.py` | `create(custom_prompts=None, phases=None)`. |
| `app/repositories/batches.py` | `get_or_create_for_book` stores the two fields on the batch. |
| `app/services/pipeline.py` | `run()` reads `job.custom_prompts`/`job.phases`; build content sequence from `phases` closure; thread `custom_prompts` to `_execute_phase`; per-phase prompt = custom-or-builtin (replace); effective hash; `contract_override` into both judge calls. |
| `app/services/phase_judge.py` | `judge(..., contract_override: Optional[str] = None)`; `contract = contract_override or get_prompt(...)`. |
| `app/services/agent.py` | thread an optional `prompt_hash`/`extra_envelope` so the generator's `agent_usages` row carries the effective hash (Gate 4). |

### Frontend

| File | Change |
|---|---|
| `web/src/lib/api.ts` | `generate` opts gain `custom_prompts?: Record<string,string> \| null`, `phases?: string[] \| null`; include in body; response type gains `added_phases?: string[]`. |
| `web/src/routes/section.tsx` | A phase list (the 11 phases) with: a checkbox per phase (subset selection) and a per-phase `.md` upload (FileReader → `customPrompts[phase]`). On generate, if the server reports `added_phases`, toast/show "also generating X, Y (dependencies)". |
| `web/src/lib/subjects.ts` / `types.ts` | A user-facing phase list/labels for the picker (the 11 content phases; `extract` excluded). |

## Verified, no change

- **Gate 4 / batch rollup:** `batches_repo.rollup_for_batch` (batches.py:56) tallies by **job status** (latest job per lesson, GROUP BY status) — it does not assume 11 phases. A subset job is still `pending/running/done`. The `complete` flag in `_rollup_payload` (batch.py:46) keys on status counts only. **Subset-safe as-is.**

## Error handling

- Unknown phase name (not in the subject's flow) → 400 listing valid phases.
- Per-prompt over 20 000 chars → 400 naming the phase.
- Empty `phases: []` → 400 ("select at least one phase") — distinct from `null` (= all).
- FE file read error / empty file → toast; that phase stays built-in.
- Judge unavailable / regen failure: unchanged (still soft-degrades for cli; api auth still raises).

## Known limitation (documented)

The batch row remains keyed `UNIQUE(book_id, transport)`. A custom/subset batch reuses the same batch row as a plain batch of the same book+transport (its jobs are force-fresh, but they live in that batch's rollup). Per-custom-variant batch separation is **out of scope**; benchmarking distinct custom batches over one book+transport isn't isolated. Flagged for the user; revisit only if needed.

## Testing

- **flows.expand_phase_selection:** picking `reflection` alone → closure includes preview + flashcards + memory-check + boss-arena + reflection; `added_phases` lists the four; order matches canonical flow. Picking all → no additions. Picking a phase with no deps → just itself.
- **Schema:** `GenerateRequest` / `BatchLaunchRequest` round-trip `custom_prompts` + `phases`; defaults None.
- **Endpoint (no-DB):** unknown phase → 400; oversize per-prompt → 400; empty `phases` → 400. Custom/subset present ⇒ reuse check skipped (assert `find_active_for_section` not consulted / fresh job created) — monkeypatch pattern from `test_transport_validation`.
- **Endpoint (DB):** custom_prompts + phases persist on the job; `added_phases` in response; a second custom launch over a done section creates a NEW job (Gate 1).
- **Repo:** `create(custom_prompts=, phases=)` persists; absent → NULL.
- **Pipeline (no-DB / source + unit):** content sequence equals the closure; per-phase prompt = custom when present else built-in (pure helper); effective hash = sha256(custom) vs builtin; `contract_override` passed to `judge` for custom phases (source-inspection like `test_execute_phase_judge`).
- **Judge unit:** `judge(contract_override="X")` builds the judge prompt from "X", not `get_prompt`.
- **FE:** `tsc --noEmit` + `npm run build` clean.
- **Acceptance (REQUIRED real CLI smoke):** one real `gemini`/`claude` run with (a) a custom prompt on one phase that demands a recognizable marker in the output, and (b) a 2-phase subset whose dependency closure auto-expands. Confirm: the custom phase output shows the marker; the judge does **not** spuriously regenerate it (judge saw the custom contract); the auto-added dependency phases ran; `extract` ran and is unaffected.

## Out of scope (YAGNI)

- Saving / reusing prompt presets or a prompt library; server-side file storage.
- Per-custom-variant batch separation (see limitation).
- Displaying the custom prompt back in the preview/job UI (beyond `added_phases`).
- Custom prompt for `extract` or the judge instructions themselves.
- Re-keying idempotency on a custom/subset signature (force-fresh is the chosen path).
