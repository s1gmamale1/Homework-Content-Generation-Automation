# R21.8 — Solver-config editing + observability

**Worklog:** 0118 · **Branch:** `feat/solver-settings-badge` (worktree `../HCGA-solver-fe`, off `origin/Nggaev-v2` @ `fe2ffa2`) · **Commit prefix:** `slvr:` · **No migration** (solver columns shipped in mig 0043).

## Approach & key decisions

CQ-C (0112) shipped the **solver** role with per-role columns (`solver_provider/model/transport` on launch_defaults/homework_jobs/batches, `phase_outputs.solver_status`) + resolution/claim-gate logic, but left it operator-*invisible* and *unadjustable*: the fleet default is only settable via the mig-0043 seed, and `solver_status` never reaches the FE. R21.8 closes that gap — **read-only to all shipped resolution logic** (no `pipeline.py`/`model_tiers.py`/claim-gate/migration changes).

- **Backend = judge-mirror, line-for-line.** The solver role is stamped/resolved exactly like judge (`resolve_solver` ≡ `resolve_judge`). So the settings surface clones judge: `_MUTABLE` += 3 fields; `LaunchDefaultsOut`/`Update` += 3 fields; `_serialize` += 3; and **solver joins the `judge/extract/content` required-concrete + manifest-validated loop** and the `judge/extract` transport-validation loop. Solver is **required-concrete (not nullable)** — this is *not* an open choice: the shipped claim gate strands any job stamped from a null solver default (the exact failure the mig-0043 backfill fixed). Seeded default is `gemini`/`gemini-3.1-pro-preview`/`inherit` (verified in 0043 + manifest).
- **The badge needs one new serialized field.** `PhaseOut` (`app/schemas/job.py:21`) serializes `judge_status` but **not** `solver_status`; add it (`from_attributes` reads the shipped column automatically). `JobOut` already carries solver_provider/model/transport from CQ-C — no change there.
- **FE clones judge, with one deliberate divergence: `mismatch_regen` renders GREEN (success).** The judge chip is label-only with a single hardcoded amber style; the solver chip needs a status→color map because "solver fixed a wrong key" (`mismatch_regen`) is a *win*, not a warning.
- **Guard-rail (locked with user): footgun warning near both content pickers + a settings-row hint.** The concrete hazard: content generator = `gemini-3.1-pro-preview` → self-grade guard swaps solver+judge to the claude peer → on this keyless-Anthropic fleet those jobs go **unclaimed forever**. A cheap amber-hint precedent exists (`section.tsx` `judgeWarning` → `RoleAgentControls`; `launcher.tsx` translate-hint). Warn when the selected content model is `gemini-3.1-pro-preview`.
- **Rejected: per-job solver override editors** (launcher/section RoleAgentControls). Out of scope — R21.8 is the *global default* editor + observability. The per-job body shapes already accept solver fields from CQ-C stamping; a UI to set them per-launch is a separate follow-up (note in ROADMAP, don't build).
- **Collision note:** no committed overlap with in-flight `feat/extract-coverage-contract` (docs/research only) or `fix/round2-localization-polish` (no committed diff). Both my web/src files (`settings.tsx`, `preview.tsx`, `types.ts`, `launcher.tsx`, `section.tsx`) must be rebase-checked at finish since polish *may* land uncommitted web/src edits.

**Global constraints (bind every task + reviewer):**
- Surface = `app/repositories/launch_defaults.py`, `app/api/v1/settings.py`, `app/schemas/job.py`, `web/src/**`, tests. **NO** changes to `pipeline.py`, `model_tiers.py`, `worker.py`, `jobs.py`/`batches.py` claim/stamp logic, or any migration. Resolution + claim-gate are shipped and gated — read-only.
- Solver global default is **required-concrete + manifest-valid** (mirror judge/content). Null provider or model → PUT 422.
- Stage ONLY each task's listed files. Never `git add -A`.
- `cd web && npx tsc -p tsconfig.app.json --noEmit` and `npm run build` must be clean after every FE task.
- Backend real-DB tests are gated on `RUN_DB_INTEGRATION=1` (scratch DB, pin `127.0.0.1`); the canonical green bar is the suite run WITHOUT the flag.
- Commit per task, prefix `slvr:`.

---

## Task 1 — Backend: settings API exposes the solver role (mutable + validated)

**Files:** `app/repositories/launch_defaults.py`, `app/api/v1/settings.py`, `tests/api/test_settings_launch_defaults.py`.

**TDD — write these tests first** (append to `tests/api/test_settings_launch_defaults.py`, mirroring the judge cases). They are real-DB (`RUN_DB_INTEGRATION=1`); each mutating test restores the singleton.

```python
# ── solver_* fields (R21.8) — mirror judge exactly ──────────────────────────

@pytest.mark.asyncio
async def test_get_returns_seeded_solver_defaults():
    """(solver-a) GET returns the mig-0043 solver seed."""
    async with _client() as c:
        r = await c.get("/api/v1/settings/launch-defaults", headers=_HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["solver_provider"] == "gemini"
    assert body["solver_model"] == "gemini-3.1-pro-preview"
    assert body["solver_transport"] == "inherit"


@pytest.mark.asyncio
async def test_put_solver_override_persists():
    """(solver-b) PUT a concrete solver override → 200, GET reflects it."""
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    async with _client() as c:
        r = await c.put(
            "/api/v1/settings/launch-defaults",
            headers=_HDR,
            json={"solver_provider": "claude", "solver_model": "claude-opus-4-7",
                  "solver_transport": "api"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["solver_provider"] == "claude"
    assert body["solver_model"] == "claude-opus-4-7"
    assert body["solver_transport"] == "api"

    async with SessionLocal() as s:
        await launch_defaults_repo.update(
            s, {"solver_provider": "gemini", "solver_model": "gemini-3.1-pro-preview",
                "solver_transport": "inherit"},
        )
        await s.commit()


@pytest.mark.asyncio
async def test_put_rejects_null_solver_provider():
    """(solver-c) PUT {"solver_provider": null} → 422 (required-concrete)."""
    async with _client() as c:
        r = await c.put("/api/v1/settings/launch-defaults", headers=_HDR,
                        json={"solver_provider": None})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_null_solver_model():
    """(solver-d) PUT {"solver_model": null} → 422."""
    async with _client() as c:
        r = await c.put("/api/v1/settings/launch-defaults", headers=_HDR,
                        json={"solver_model": None})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_solver_offmanifest():
    """(solver-e) PUT off-manifest solver model → 422."""
    async with _client() as c:
        r = await c.put("/api/v1/settings/launch-defaults", headers=_HDR,
                        json={"solver_provider": "gemini", "solver_model": "not-a-model"})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_rejects_bad_solver_transport():
    """(solver-f) PUT invalid solver_transport → 422."""
    async with _client() as c:
        r = await c.put("/api/v1/settings/launch-defaults", headers=_HDR,
                        json={"solver_transport": "bogus"})
    assert r.status_code == 422, r.text
```

**Implementation:**

1. `app/repositories/launch_defaults.py` — add solver to `_MUTABLE`:
```python
_MUTABLE = (
    "judge_provider", "judge_model", "judge_transport",
    "solver_provider", "solver_model", "solver_transport",
    "extract_provider", "extract_model", "extract_transport",
    "toc_transport",
    "output_language",
    "content_provider", "content_model", "content_transport",
)
```

2. `app/api/v1/settings.py`:
   - `LaunchDefaultsOut` — add after the judge fields:
     ```python
     solver_provider: str | None
     solver_model: str | None
     solver_transport: str | None
     ```
   - `LaunchDefaultsUpdate` — add the same three with `= None` defaults.
   - `_serialize` — add `solver_provider=row.solver_provider, solver_model=row.solver_model, solver_transport=row.solver_transport,`.
   - Required-concrete + manifest loop — add `"solver"`:
     ```python
     for role in ("judge", "solver", "extract", "content"):
     ```
   - Transport-validation loop — add `"solver"`:
     ```python
     for role in ("judge", "solver", "extract"):
     ```

**Commit:** `slvr: expose solver role in launch-defaults settings API (R21.8)`

**Verify:**
```
cd ../HCGA-solver-fe && uv run python -m pytest tests/api/test_settings_launch_defaults.py -q            # collects clean w/o flag
RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_cqc \
  uv run python -m pytest tests/api/test_settings_launch_defaults.py -q                                   # real-DB green
```
(Scratch DB must be at head 0043; if not, `uv run alembic upgrade head` against it first.)

---

## Task 2 — Backend: `PhaseOut` serializes `solver_status`

**Files:** `app/schemas/job.py`, `tests/api/test_job_serialization.py`.

**TDD — add to `tests/api/test_job_serialization.py`:**
```python
def test_phaseout_serializes_solver_status():
    class _Row:
        phase_name = "practice-error-detection"
        phase_order = 1
        status = "done"
        output_md = "x"
        tokens_input = 1
        tokens_output = 1
        started_at = None
        completed_at = None
        error_message = None
        validation_warnings = None
        judge_status = None
        solver_status = "mismatch_regen"

    out = PhaseOut.model_validate(_Row())
    assert out.solver_status == "mismatch_regen"
```
(Also extend the existing `_Row` in `test_phaseout_serializes_judge_status` with `solver_status = None` so the class stays a valid attribute source — `from_attributes` needs every declared field present on the row.)

**Implementation** — `app/schemas/job.py`, add after line 21:
```python
    solver_status: Optional[str] = None   # ok | mismatch_regen | mismatch_shipped | mismatch_regen_failed | unavailable | refused | None
```

**Commit:** `slvr: serialize phase_outputs.solver_status in PhaseOut (R21.8)`

**Verify:** `cd ../HCGA-solver-fe && uv run python -m pytest tests/api/test_job_serialization.py -q`

---

## Task 3 — FE: types + Solver settings row + self-grade hint

**Files:** `web/src/lib/types.ts`, `web/src/routes/settings.tsx`.

1. **`web/src/lib/types.ts`:**
   - `LaunchDefaults` interface — add after the judge fields:
     ```ts
     solver_provider: string | null;
     solver_model: string | null;
     solver_transport: RoleTransport | null;
     ```
   - `PhaseOut` interface — add after `judge_status`:
     ```ts
     solver_status: string | null;
     ```

2. **`web/src/routes/settings.tsx`** — clone judge:
   - State (after the judge trio, ~line 142):
     ```tsx
     const [solverProvider, setSolverProvider] = useState<string | null>(null);
     const [solverModel, setSolverModel] = useState<string | null>(null);
     const [solverTransport, setSolverTransport] = useState<RoleTransport>("inherit");
     ```
   - Sync effect (after the judge lines, ~line 161):
     ```tsx
     setSolverProvider(data.solver_provider ?? null);
     setSolverModel(data.solver_model ?? null);
     setSolverTransport((data.solver_transport as RoleTransport) ?? "inherit");
     ```
   - Model options (after `judgeModelOptions`, ~line 176):
     ```tsx
     const solverModelOptions = solverProvider
       ? (manifest?.providers?.[solverProvider] ?? [])
       : [];
     ```
   - Save-validation guard (line 212) — add solver to the concrete check + error copy:
     ```tsx
     if (!contentProvider || !contentModel || !judgeProvider || !judgeModel ||
         !solverProvider || !solverModel || !extractProvider || !extractModel) {
       setSaveError(
         "Content, Judge, Solver, and Extract provider+model must all be set — no Auto allowed for global defaults",
       );
       return;
     }
     ```
   - Save payload (after the judge fields, ~line 224):
     ```tsx
     solver_provider: solverProvider,
     solver_model: solverModel,
     solver_transport: solverTransport,
     ```
   - Header caption (line 272): `content · judge · solver · extract · toc · language`; intro copy (line 251): "…for the Content, Judge, Solver, Extract, and TOC roles."
   - The **Solver `<RoleRow>`** + self-grade hint — insert immediately after the Judge row (after line 327):
     ```tsx
     {/* Solver row — re-solves answer keys; resolves like Judge (self-grade guard). */}
     <RoleRow
       label="Solver"
       provider={solverProvider}
       model={solverModel}
       transport={solverTransport}
       onProvider={setSolverProvider}
       onModel={setSolverModel}
       onTransport={setSolverTransport}
       providerNames={providerNames}
       modelOptions={solverModelOptions}
     />
     <p className="ml-16 -mt-2 max-w-[46ch] text-[0.65rem] leading-snug text-white/40">
       Re-solves answer keys and regenerates on a high-confidence mismatch. If the
       solver model equals the content generator, the self-grade guard swaps it to a
       peer model automatically.
     </p>
     ```

**Commit:** `slvr: Solver role editor on /settings + self-grade hint (R21.8)`

**Verify:**
```
cd ../HCGA-solver-fe/web && npx tsc -p tsconfig.app.json --noEmit && npm run build
```

---

## Task 4 — FE: `solver_status` badge on the phase/preview console

**Files:** `web/src/routes/preview.tsx`.

Add a label map + a color map next to `JUDGE_STATUS_LABEL` (~line 117):
```tsx
// Solver states. mismatch_regen is a SUCCESS (the solver caught a wrong key and
// the phase was regenerated) — render it green, not amber.
const SOLVER_STATUS_LABEL: Record<string, string> = {
  mismatch_regen: "answer-key fixed",
  mismatch_shipped: "key mismatch shipped",
  mismatch_regen_failed: "key regen failed",
  unavailable: "solver unavailable",
  refused: "solver declined",
  // `ok` is the clean case — no chip (mirrors judge, which shows nothing on ok).
};

const SOLVER_STATUS_CLASS: Record<string, string> = {
  mismatch_regen: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300/90",
  mismatch_shipped: "border-rose-400/30 bg-rose-400/10 text-rose-300/90",
  mismatch_regen_failed: "border-rose-400/30 bg-rose-400/10 text-rose-300/90",
  unavailable: "border-amber-400/30 bg-amber-400/10 text-amber-300/90",
  refused: "border-amber-400/30 bg-amber-400/10 text-amber-300/90",
};
```

Render the chip in the phase header, immediately after the judge chip (after line 224):
```tsx
{p.solver_status && SOLVER_STATUS_LABEL[p.solver_status] && (
  <span className={cn(
    "rounded-full border px-2 py-0.5 font-mono text-[0.62rem] uppercase tracking-wider",
    SOLVER_STATUS_CLASS[p.solver_status] ?? "border-amber-400/30 bg-amber-400/10 text-amber-300/90",
  )}>
    {SOLVER_STATUS_LABEL[p.solver_status]}
  </span>
)}
```
(`cn` is already imported in preview.tsx.)

**Commit:** `slvr: solver_status badge on phase console (green on answer-key-fixed) (R21.8)`

**Verify:** `cd ../HCGA-solver-fe/web && npx tsc -p tsconfig.app.json --noEmit && npm run build`

---

## Task 5 — FE: content-picker self-grade footgun warning

**Files:** `web/src/components/fleet/launcher.tsx`, `web/src/routes/section.tsx`.

The hazard string (shared wording): **"content = gemini-3.1-pro-preview → the solver & judge swap to a Claude peer; on a Gemini-only (no Anthropic key) fleet those jobs stay unclaimed."** Warn when the selected content **model** is `gemini-3.1-pro-preview`.

1. **`launcher.tsx`** — the content model Select closes at line 1109 inside the `flex flex-wrap` row ending line 1111. Add an amber hint as a sibling right after the `)}` at 1110 (cloning the translate-hint at 1088–1094):
```tsx
{model === "gemini-3.1-pro-preview" && (
  <span className="max-w-[22rem] text-[0.62rem] leading-snug text-amber-300/85">
    ⚠ Solver & judge will swap to a Claude peer (self-grade guard). Gemini-only
    fleets can’t claim these jobs — pick a different content model.
  </span>
)}
```

2. **`section.tsx`** — add a `contentWarning` next to `judgeWarning` (~line 184):
```tsx
const contentWarning =
  model === "gemini-3.1-pro-preview"
    ? "Content = gemini-3.1-pro-preview swaps solver & judge to a Claude peer; Gemini-only fleets can’t claim these jobs."
    : null;
```
Render it beneath the content model Select (after the content-model `</Select>` at ~line 709), matching the existing amber advisory style:
```tsx
{contentWarning && (
  <p className="mt-1 text-[0.7rem] leading-snug text-amber-300/90">{contentWarning}</p>
)}
```
(Verify the exact `model`/`setModel` variable names and the content-model Select's closing tag against the live file before editing — the implementer reads `section.tsx:660-712` first.)

**Commit:** `slvr: warn near content picker when content=gemini-3.1-pro-preview (unclaimable footgun) (R21.8)`

**Verify:** `cd ../HCGA-solver-fe/web && npx tsc -p tsconfig.app.json --noEmit && npm run build`

---

## Task 6 — Finish: docs de-stale + worklog + ROADMAP close

**Files:** `docs/memory/MASTER_MEMORY.md` (worklog 0118), `docs/memory/INDEX.md` (row), `docs/memory/ROADMAP.md` (close R21.8), `docs/HOW_IT_WORKS.md` + `docs/CODE_MAP.md` (solver now operator-editable + observable), `docs/DATABASE.md` (only if a claim about columns changed — likely no-op, note it).

1. **Worklog 0118** in `MASTER_MEMORY.md` — what shipped (settings editor + badge + footgun warning), the required-concrete decision + why (claim-gate strand), the mismatch_regen=green choice, and the **deferred** per-job solver override editor.
   - **S1 docs-conflict caution (standing):** worklogs 0117/0118/0119 (polish, this, coverage) may land in any order → expect `MASTER_MEMORY.md` / `INDEX.md` append conflicts on rebase. Hand-merge keeping BOTH blocks, and after rebase **verify the `| 011x` INDEX row order is ascending** — it has come out wrong on three consecutive merges. Do not `git add -A` the resolution; stage only these doc files.
2. **ROADMAP.md** — close R21.8 (built); if the per-job override editor is worth tracking, add a one-line follow-up.
3. **INDEX.md** — one worklog-0118 row.
4. **HOW_IT_WORKS.md / CODE_MAP.md** — de-stale: solver default is now editable at `/settings`; `solver_status` renders on the phase console; note the content=gemini-3.1-pro-preview footgun warning.

**Commit:** `slvr: worklog 0118 + close R21.8 + de-stale reference docs`

**Then (finish sequence):**
```
cd ../HCGA-solver-fe && uv run python -m pytest tests/ -q                    # full suite green (no flag)
cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build            # FE green
cd ../HCGA-solver-fe && GIT_OPTIONAL_LOCKS=0 git fetch origin && GIT_OPTIONAL_LOCKS=0 git log HEAD..origin/Nggaev-v2 --oneline
# if origin/Nggaev-v2 moved: rebase onto it, resolve web/src conflicts (polish-branch risk), re-run suite + tsc + build.
```
Hand to GK2 for review/merge — **no self-merge**. Acceptance evidence for the gate: settings-API solver tests green (real-DB), `test_launch_stamps_defaults.py` already proves PUT-default→job-stamp path, tsc+build clean, FE in-browser behavioral check (Solver row saves; footgun hint appears for gemini-3.1-pro-preview).

**S2 — badge acceptance uses a REAL production catch (no synthetic row):** the solver's first live catch already exists in `edu_copy` — the 2026-07-03 re-audit's **G10 `memory-check` phase carries a genuine `solver_status='mismatch_regen'`**. Open that job in the preview console; the green "answer-key fixed" chip on a real catch is the feature demonstrating itself. Find the job id with:
```
GIT_OPTIONAL_LOCKS=0 psql "$EDU_COPY_URL" -c "SELECT job_id, phase_name, solver_status FROM phase_outputs WHERE solver_status='mismatch_regen' LIMIT 5;"
```
(read-only against edu_copy — do not write). GK2 will eyeball this at the gate.
