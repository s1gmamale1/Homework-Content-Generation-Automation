# Dashboard viewer port — read-only dashboard on its own port + token

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. TDD per task, commit per task, stage only listed files.

**Goal:** `uv run uvicorn viewer_main:app --port 8001` serves ONLY the coverage dashboard — its own SPA build with no operator nav, its own `DASHBOARD_TOKEN`, the same live data — so a non-technical viewer can be handed a URL+token that is useless against the operator app.

## Approach & key decisions

- **User-locked (2026-07-18):** (a) dedicated viewer process (Option A — not a reverse proxy, not a second full instance); (b) **separate `DASHBOARD_TOKEN`** — the operator `AUTH_TOKEN` is REJECTED on the viewer port and vice versa (strict separation = revocable + non-escalating); (c) **dashboard-only SPA build** — no Fleet/Monitor/Settings routes in the viewer bundle's router.
- **Read-only by construction:** `viewer_main.py` includes ONLY the health router + the dashboard router. No worker, **no lifespan sweeps** (main.py's lifespan mutates — sweeps stuck jobs; the viewer app gets a minimal lifespan: engine init/dispose only). The only DB access is the three SELECT queries in `app/repositories/subject_coverage.py`.
- **Fail-loud on missing token:** empty/unset `DASHBOARD_TOKEN` → the viewer app **refuses to start** (RuntimeError in lifespan). Rationale: on the operator app an empty token means "auth disabled" for a trusted dev setup; a *viewer-facing* port must never silently go open — the whole point is handing the URL out.
- **FE = same codebase, build flag, second outDir.** `npm run build:viewer` runs vite with `--mode viewer` (`vite.viewer.config.ts`: `outDir: "dist-viewer"`, defines `import.meta.env.VITE_VIEWER = "1"`). `App.tsx` renders a reduced router when the flag is set: `/login` (reused unchanged — it stores the token client-side; validation is the first API call 401ing, verified in `web/src/routes/login.tsx`) + `/dashboard` + `/` redirect → `/dashboard`; `layout.tsx` renders no nav items in viewer mode. One codebase — the dashboard page, api client, and pure modules are shared, so viewer and operator can never drift.
- **Same API path on both ports** (`/api/v1/dashboard/coverage`) so `lib/api.ts` works unchanged; the viewer app mounts `web/dist-viewer` with the same assets-mount + SPA-fallback pattern as `main.py:167-196`.
- **Load-bearing facts verified:** `app/auth.py` validates against `config.valid_auth_tokens()` (comma-separated; add a parallel `valid_dashboard_tokens()`); dashboard router (`app/api/v1/dashboard.py`) has no auth of its own — auth attaches at include time (`app/api/v1/__init__.py` pattern), so the viewer app can include the SAME router object under a different dependency; SPA login stores the token without server validation; vite config is single-mode today (`web/vite.config.ts`, `outDir: "dist"`).
- **Rejected:** reverse proxy (still exposes the operator token, adds infra per host); serving the full dist with dead routes (confusing for exactly the audience this is for); sharing `AUTH_TOKEN` (non-revocable escalation).

## Global constraints

- Zero changes to `main.py`, the operator SPA behavior, or `web/dist`. The operator build must remain byte-identical in behavior.
- Viewer app: GET-only surface (health + coverage). A test proves operator-only routes 404 on it.
- No migration. Stage only listed files. Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Gates: `uv run python -m pytest tests/ -q`; `cd web && npm run test && npx tsc -p tsconfig.app.json --noEmit && npm run build && npm run build:viewer`.

### Task 1: viewer token config + auth dependency (TDD)
Files: `app/config.py` (+`dashboard_token: str = ""` env `DASHBOARD_TOKEN` + `valid_dashboard_tokens()` mirroring `valid_auth_tokens()`), `app/auth.py` (+`get_viewer_user` — Bearer or `?token=`, validates ONLY against dashboard tokens, 401 otherwise), `tests/api/test_viewer_auth.py`.
RED: viewer token accepted; operator token rejected (401); missing → 401; multiple comma-separated viewer tokens work. GREEN, commit `feat(viewer): DASHBOARD_TOKEN config + get_viewer_user dependency`.

### Task 2: `viewer_main.py` (TDD)
Files: `viewer_main.py` (repo root, mirrors main.py's SPA-mount idiom, `WEB_DIST = web/dist-viewer`; minimal lifespan: refuse-start on empty `DASHBOARD_TOKEN`, engine dispose on shutdown; includes `health.router` + `dashboard.router` with `Depends(get_viewer_user)`), `tests/api/test_viewer_app.py`.
RED (httpx ASGITransport over `viewer_main.app`, monkeypatched `dashboard_token`): coverage 200 with viewer token / 401 with operator token; `/api/v1/books` and `/api/v1/jobs/batches` → 404; startup refuses when token empty. GREEN, commit `feat(viewer): read-only viewer app — health + coverage only`.

### Task 3: viewer FE build
Files: `web/vite.viewer.config.ts`, `web/package.json` (+`build:viewer`), `web/src/App.tsx` (viewer-flag router: login + dashboard + `/`→`/dashboard`), `web/src/components/layout.tsx` (no nav in viewer mode).
Gates: both builds + tsc + `npm run test`; `dist/` output of `npm run build` unchanged in behavior (operator routes still present there). Commit `feat(viewer): dashboard-only SPA build (dist-viewer)`.

### Task 4: acceptance (controller, $0 — no model calls)
Start `uvicorn viewer_main:app --port 8001` against the prod DB. Prove: curl coverage → 401 with operator token, 200 with `DASHBOARD_TOKEN`; `/api/v1/books` 404; browser `/dashboard` on :8001 renders live numbers matching :8000's; empty-token start refuses loudly; `:8000` untouched throughout.

### Task 5: finish
Docs (`README.md` run command, `docs/DEPLOY.md`, `docs/CODE_MAP.md`, `docs/HOW_IT_WORKS.md` viewer subsection), `.env` gains `DASHBOARD_TOKEN` (operator sets value), worklog **0153** (re-check INDEX tail), plan → `shipped/`, rebase-check, push, PR for GK2.
