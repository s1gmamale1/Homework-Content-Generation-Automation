# Launch Config Centralization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Settings → Launch defaults the single place to configure **Content generation** (provider/model/transport) alongside the existing Judge/Extract/TOC roles, and remove the Judge/Extract selectables from the Fleet launcher so it only configures the content generator (seeded from the global default).

**Architecture:** Add `content_provider`/`content_model`/`content_transport` to the `launch_defaults` singleton (migration + model + repo + settings API), surface a Content row in `settings.tsx`, and rewire `launcher.tsx` to (a) seed its content provider/model/transport from the new default, (b) drop the Judge/Extract `RoleAgentControls`, and (c) send `judge_*`/`extract_*` as Auto/`inherit` so the backend resolves them from the global default. (c) closes the `launcher-role-transport-default-1` bug (Fleet launcher was hardcoding role transport to `"cli"`, overriding the Settings default).

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic; React + TypeScript + react-query; Radix Select. No FE test runner — FE acceptance is `tsc --noEmit` + `npm run build` + `npx tsx` for pure helpers + in-browser eyeball (the reviewer's).

---

## Approach & key decisions

- **Chosen approach:** content default is stored in the SAME `launch_defaults` singleton as judge/extract/toc (one row, partial PUT), and the **Fleet launcher seeds its content selection from it** (it does NOT change the launcher's contract — content provider/model/transport are still sent explicitly per launch; the default only changes the pre-filled value). Mirrors how judge/extract are already modeled.
- **Rejected — backend content-resolution from `ld`:** also resolving content provider/model/transport server-side when a launch omits them. Rejected for this plan: the UI always sends explicit content values, so it's unneeded surface; and the single-job role-default gap (`single-job-launch-defaults-1`) is a separate WISHLIST item. Out of scope here.
- **Rejected — also stripping Judge/Extract from the Section per-lesson launcher:** change #3 says "in fleet page." Section keeps its controls (per-lesson power-user surface) — gate-confirmed.
- **Content default ≠ judge default (hard rule):** the content seed (`content_model`) MUST NOT equal the live judge default. If content==judge, a fresh Fleet launch trips the self-grade guard → judge swaps to a Claude fallback → needs a Claude key → strands unclaimable on an all-gemini fleet. The live judge default is `gemini-2.5-flash`, so content seeds to **`gemini-2.5-pro`** (also the quality choice for student-facing content). See [[flow-games-roles-retry]] self-grade guard + the `judge-self-fallback-1` WISHLIST trap.
- **Migration number is decided AT EXECUTION, not hardcoded:** there is an in-flight `0038_output_language` migration that also chains off `0037_launch_defaults` → two heads if this one also hardcodes `0038`/`down_revision=0037`. The implementer MUST run `uv run alembic heads` first and chain off the *actual* current single head. Below assumes `0038_output_language` has landed → this becomes `0039_launch_defaults_content` with `down_revision="0038_output_language"`; if it hasn't, fall back to `0038_launch_defaults_content`/`down_revision="0037_launch_defaults"`. (The PR gate independently verifies single-head.)
- **Load-bearing facts (verified against code @ tip `dd9e1e7`):**
  - `launch_defaults` is a singleton (`id=1`, `CheckConstraint("id = 1")`), columns nullable, seeded by migration `0037_launch_defaults`.
  - `launch_defaults_repo.update` only writes keys in `_MUTABLE` (`app/repositories/launch_defaults.py:11`) — content keys MUST be added there or the PUT silently drops them.
  - Settings PUT validation (`app/api/v1/settings.py:47`) requires judge/extract provider+model concrete; we extend the same concreteness rule to content.
  - Fleet launcher state inits: `provider ?? "claude"` (`launcher.tsx:579`), `transport ?? "api"` (`:580`), `extractTransport ?? "cli"` / `judgeTransport ?? "cli"` (`:581-582`) — the `"cli"` hardcode is the bug; provider/model already default to `null`/Auto (`:584-587`). `launchBody` sends `extract_transport`/`judge_transport` at `:749-750`.
  - Backend resolves a role transport from the global default ONLY when the value is `"inherit"` (`resolve_role_transport_default`, `agent_models.py:116`; applied `jobs.py:246`). So the launcher must send `"inherit"` (not `"cli"`) for the default to win.
  - `RoleAgentControls` shows `Auto → <resolvedDefault>` for provider/model (`RoleAgentControls.tsx`); after removal the launcher relies entirely on the Settings default.

---

### Task 1: Migration — add content_* columns to launch_defaults

**Files:**
- Create: `alembic/versions/<REV>_launch_defaults_content.py` — `<REV>` decided at execution (see Step 0)
- Test: `tests/db/test_migration_content_columns.py`

> Throughout this task, `<REV>` = the revision id you assign in Step 0 (e.g. `0039_launch_defaults_content`). Substitute it consistently in the test, the run commands, and the migration file.

- [ ] **Step 0: Pin the revision chain (DO THIS FIRST — prevents the multi-head break)**

Run: `uv run alembic heads`
- If it prints a single head `0038_output_language` → set `<REV>="0039_launch_defaults_content"`, `down_revision="0038_output_language"`.
- If it prints a single head `0037_launch_defaults` (multi-language not yet merged) → set `<REV>="0038_launch_defaults_content"`, `down_revision="0037_launch_defaults"`.
- If it prints **two heads** → STOP and escalate; the base is already broken, don't add a third.

- [ ] **Step 1: Write the failing test** (real-DB, gated like the repo's other migration tests)

```python
import os
import pytest
from sqlalchemy import text
from alembic.config import Config
from alembic import command

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_DB_INTEGRATION"), reason="needs RUN_DB_INTEGRATION + DATABASE_URL"
)

def test_content_columns_added_and_seeded():
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "<REV>")  # the revision id assigned in Step 0
    from sqlalchemy import create_engine
    eng = create_engine(os.environ["DATABASE_URL"].replace("+asyncpg", "+psycopg2"))
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(text(
            "select column_name from information_schema.columns "
            "where table_name='launch_defaults'"))}
        assert {"content_provider", "content_model", "content_transport"} <= cols
        row = c.execute(text(
            "select content_provider, content_model, content_transport "
            "from launch_defaults where id=1")).one()
        assert row[0] and row[1] and row[2]  # seeded concrete
```

- [ ] **Step 2: Run it to verify it fails** (`createdb`-backed scratch DB per [[sdd-acceptance-gate-learnings]])

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://edu@localhost:5433/edu_scratch uv run python -m pytest tests/db/test_migration_content_columns.py -q`
Expected: FAIL (revision `<REV>` not found).

- [ ] **Step 3: Write the migration** (revision id ≤32 chars — see [[alembic-jsonb-upsert-gotchas]]; `revision`/`down_revision` from Step 0)

```python
"""launch_defaults: add content_provider/model/transport"""
from alembic import op
import sqlalchemy as sa

revision = "<REV>"            # Step 0: e.g. "0039_launch_defaults_content"
down_revision = "<PREV_HEAD>" # Step 0: e.g. "0038_output_language"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("launch_defaults", sa.Column("content_provider", sa.String(32), nullable=True))
    op.add_column("launch_defaults", sa.Column("content_model", sa.String(128), nullable=True))
    op.add_column("launch_defaults", sa.Column("content_transport", sa.String(16), nullable=True))
    # Seed concrete + manifest-valid. content_model MUST NOT equal the judge
    # default (gemini-2.5-flash) — else content==judge trips the self-grade
    # guard and strands an all-gemini fleet (see Approach). Use gemini-2.5-pro.
    op.execute(
        "UPDATE launch_defaults SET content_provider='gemini', "
        "content_model='gemini-2.5-pro', content_transport='api' WHERE id=1"
    )

def downgrade() -> None:
    op.drop_column("launch_defaults", "content_transport")
    op.drop_column("launch_defaults", "content_model")
    op.drop_column("launch_defaults", "content_provider")
```

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/<REV>_launch_defaults_content.py tests/db/test_migration_content_columns.py
git commit -m "feat(launch-defaults): migration <REV> — add content_provider/model/transport"
```

---

### Task 2: Model + repo — expose content_* columns

**Files:**
- Modify: `app/models/launch_defaults.py:28` (after `toc_transport`)
- Modify: `app/repositories/launch_defaults.py:11-15` (`_MUTABLE`)
- Test: `tests/services/test_launch_defaults_repo.py` (extend or create)

- [ ] **Step 1: Write the failing test**

```python
import os, pytest
pytestmark = pytest.mark.skipif(not os.getenv("RUN_DB_INTEGRATION"), reason="real DB")

@pytest.mark.asyncio
async def test_update_persists_content_fields(db_session):
    from app.repositories import launch_defaults as repo
    row = await repo.update(db_session, {
        "content_provider": "claude", "content_model": "claude-opus-4-8",
        "content_transport": "cli"})
    assert row.content_provider == "claude"
    assert row.content_model == "claude-opus-4-8"
    assert row.content_transport == "cli"
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=... uv run python -m pytest tests/services/test_launch_defaults_repo.py -q`
Expected: FAIL (`content_provider` not a column / not in `_MUTABLE`).

- [ ] **Step 3: Add the columns + _MUTABLE keys**

In `app/models/launch_defaults.py` after line 28:
```python
    content_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    content_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    content_transport: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
```

In `app/repositories/launch_defaults.py` `_MUTABLE`:
```python
_MUTABLE = (
    "judge_provider", "judge_model", "judge_transport",
    "extract_provider", "extract_model", "extract_transport",
    "toc_transport",
    "content_provider", "content_model", "content_transport",
)
```

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models/launch_defaults.py app/repositories/launch_defaults.py tests/services/test_launch_defaults_repo.py
git commit -m "feat(launch-defaults): model + repo expose content_* fields"
```

---

### Task 3: Settings API — serialize + validate content_*

**Files:**
- Modify: `app/api/v1/settings.py` (`LaunchDefaultsOut`, `LaunchDefaultsUpdate`, `_serialize`, PUT validation)
- Test: `tests/api/test_settings_launch_defaults.py` (extend or create)

- [ ] **Step 1: Write the failing tests**

```python
import os, pytest
pytestmark = pytest.mark.skipif(not os.getenv("RUN_DB_INTEGRATION"), reason="real DB")

@pytest.mark.asyncio
async def test_put_content_concrete_ok(client):
    r = await client.put("/api/v1/settings/launch-defaults", json={
        "content_provider": "gemini", "content_model": "gemini-2.5-pro",
        "content_transport": "api"})
    assert r.status_code == 200
    assert r.json()["content_provider"] == "gemini"

@pytest.mark.asyncio
async def test_put_content_null_provider_rejected(client):
    r = await client.put("/api/v1/settings/launch-defaults",
        json={"content_provider": None, "content_model": None})
    assert r.status_code == 422

@pytest.mark.asyncio
async def test_put_content_offmanifest_rejected(client):
    r = await client.put("/api/v1/settings/launch-defaults", json={
        "content_provider": "gemini", "content_model": "nope-not-real"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=... uv run python -m pytest tests/api/test_settings_launch_defaults.py -q`
Expected: FAIL (content fields absent from schema → not echoed; no validation).

- [ ] **Step 3: Implement**

Add `content_provider: str | None`, `content_model: str | None`, `content_transport: str | None` to BOTH `LaunchDefaultsOut` (with the others) and `LaunchDefaultsUpdate` (`= None`). Add them to `_serialize`. Extend validation in `put_launch_defaults` — add `"content"` to the concreteness + manifest loop, and validate its transport is `cli|api`:
```python
    for role in ("judge", "extract", "content"):
        prov = merged.get(f"{role}_provider")
        mdl = merged.get(f"{role}_model")
        if prov is None or mdl is None:
            raise HTTPException(422, f"{role} provider+model must be concrete")
        if not is_valid(prov, mdl):
            raise HTTPException(422, f"{role}: off-manifest (provider, model) ({prov!r}, {mdl!r})")
    ct = merged.get("content_transport")
    if ct is not None and ct not in ("cli", "api"):
        raise HTTPException(422, "content_transport must be 'cli' or 'api'")
    if ct == "api" and not api_supported(merged.get("content_provider") or ""):
        raise HTTPException(422, "content_transport=api requires an api-capable content_provider")
```
(Keep the existing judge/extract transport `validate_role_transport` loop and TOC checks unchanged.)

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: PASS. Then full backend suite: `uv run python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/settings.py tests/api/test_settings_launch_defaults.py
git commit -m "feat(launch-defaults): settings API serializes + validates content_*"
```

---

### Task 4: FE types + Settings Content row

**Files:**
- Modify: `web/src/lib/types.ts:437` (`LaunchDefaults` interface)
- Modify: `web/src/routes/settings.tsx` (state + form sync + Content `RoleRow` + handleSave + header label)

- [ ] **Step 1: Extend the type**

Add to `LaunchDefaults`:
```ts
  content_provider: string | null;
  content_model: string | null;
  content_transport: "cli" | "api" | null;
```

- [ ] **Step 2: Add Content state + sync (settings.tsx)**

Mirror the Judge block: add `contentProvider/contentModel/contentTransport` useState (transport default `"api"`); in the form-sync `useEffect` set them from `data.content_*`; compute `contentModelOptions` like `judgeModelOptions`.

- [ ] **Step 3: Render a Content row + include in save**

Add a `<RoleRow label="Content" .../>` above the Judge row (content is the primary role). NOTE: `RoleRow`'s transport select uses `ROLE_TRANSPORT_OPTIONS` (includes "Auto"). For content, pass a `cli|api`-only option set — add a `transportOptions?` prop to `RoleRow` defaulting to `ROLE_TRANSPORT_OPTIONS`, and pass `TOC_TRANSPORT_OPTIONS` for the Content row (content has no "inherit" — it IS the job transport). Extend `handleSave` to require `contentProvider && contentModel` and send `content_provider/content_model/content_transport`. Update the header chip text `judge · extract · toc` → `content · judge · extract · toc` and the header `<p>` copy to mention Content.

- [ ] **Step 4: Verify (FE acceptance — no test runner)**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: clean typecheck + build.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/types.ts web/src/routes/settings.tsx
git commit -m "feat(settings): add Content generation row to launch defaults"
```

---

### Task 5: Fleet launcher — seed content from default, remove Judge/Extract, send inherit/Auto

**Files:**
- Modify: `web/src/components/fleet/launcher.tsx` (state init `:579-589`, the expanded controls block `:894-955`, `launchBody` `:749-750`, persisted-config seeding)

- [ ] **Step 1: Seed content state from the global default**

The launcher already reads `defaultsQ` (`launcher.tsx:604`, `getLaunchDefaults`). Change the content state initializers so a fresh card (no saved per-book config) falls back to the global content default, not hardcoded `"claude"`/`"api"`:
- `provider`: `saved.provider ?? defaultsQ.data?.content_provider ?? "claude"`
- `model`: `saved.model ?? defaultsQ.data?.content_model ?? null`
- `transport`: `saved.transport ?? defaultsQ.data?.content_transport ?? "api"`

Because `defaultsQ.data` is undefined on first render, apply the seed in a guarded `useEffect` that runs once `defaultsQ.data` arrives AND there's no saved value AND the user hasn't edited (mirror the cold-load guard pattern from `launcher-persist`/[[fe-design-system-pointers]]). Keep `loadLauncherConfig` precedence (an explicit saved pick always wins).

- [ ] **Step 2: Remove the Judge + Extract RoleAgentControls**

Delete the two `<RoleAgentControls label="Extract"…/>` and `label="Judge"` blocks (`launcher.tsx:923-955`). Keep provider, CLI|API toggle, session-limit strategy, and the api model picker. Remove now-unused state (`extractProvider/extractModel/judgeProvider/judgeModel` and their setters) and the `judgeWarning` wiring if it becomes dead. Keep `extractTransport`/`judgeTransport` state ONLY if still referenced by `launchBody` (Step 3 changes them to constants — prefer removing the state and inlining).

- [ ] **Step 3: Send judge/extract as Auto/inherit (closes `launcher-role-transport-default-1`)**

In `launchBody` (`:749-750` and the persisted-config object `:695-710`), send:
```ts
    extract_provider: null, extract_model: null, extract_transport: "inherit",
    judge_provider: null,  judge_model: null,  judge_transport: "inherit",
```
so the backend resolves them from the global default (`resolve_role_transport_default` returns `ld.*` only on `"inherit"`). Drop `extractTransport`/`judgeTransport`/`extract*Provider`/`extract*Model` from the persisted `LauncherConfig` shape in `web/src/lib/launcher-config.ts` (and its load/save) since they're no longer user-set in Fleet.

- [ ] **Step 4: Verify (FE acceptance)**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: clean (no unused-var/type errors — remove all now-dead Judge/Extract references).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/fleet/launcher.tsx web/src/lib/launcher-config.ts
git commit -m "feat(fleet): launcher configures content only; judge/extract follow Settings defaults (closes launcher-role-transport-default-1)"
```

---

### Task 6: Acceptance — real launch resolves roles from the Settings default

**Files:** none (verification only)

- [ ] **Step 1: Backend proof the default wins**

With a scratch DB at head, PUT launch-defaults with `extract_transport="api"`, then POST `/api/v1/jobs/batch` with a body that sends `extract_transport="inherit"` (what the new launcher sends) and assert the created jobs persist `extract_transport="api"` (the global default), proving Step 3 of Task 5 closes the bug. Add as `tests/api/test_batch_inherit_resolves_default.py` (RUN_DB_INTEGRATION).

- [ ] **Step 2: Full backend suite green**

Run: `uv run python -m pytest tests/ -q` → all pass.

- [ ] **Step 3: FE eyeball handoff (reviewer)**

Note in the PR: reviewer to verify in-browser that (a) Settings shows a Content row that saves; (b) the Fleet launcher card shows only Content provider/model/transport + session-limit (no Judge/Extract); (c) a fresh card pre-selects the Settings content default.

- [ ] **Step 4: Commit (if Step 1 added a test)**

```bash
git add tests/api/test_batch_inherit_resolves_default.py
git commit -m "test(launch): inherit role transport resolves to the global default"
```

---

## Self-review notes
- **Coverage:** #2 = Tasks 1-4 (content in Settings, end to end). #3 = Task 5 (Fleet launcher). Task 6 binds the bug-fix.
- **Type consistency:** `content_transport` is `"cli" | "api" | null` everywhere (model String(16), schema `str|None`, TS union); content has NO `"inherit"` (it IS the job transport) — do not reuse `ROLE_TRANSPORT_OPTIONS` for the content row.
- **Out of scope (separate WISHLIST items):** backend resolution of omitted content (`content` is always explicit from the UI); `single-job-launch-defaults-1` (the /generate path ignoring `ld`); Section launcher keeps Judge/Extract.
