# Persist launcher selections across navigation (launcher-persist-selections-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Fleet launcher remember each book's provider/model/transport/extract/judge/on-limit picks across navigation and hard-refresh, instead of resetting to defaults every remount.

**Architecture:** FE-only. A small pure `localStorage` module (`web/src/lib/launcher-config.ts`) load/saves a per-`book_id` config blob. `ReadyCard` seeds its selection `useState`s from the saved blob via lazy initializers (no flash) and persists on change via a `useEffect`. The existing validation effects (apiSupported reset, model-seeding) are guarded to wait for the models query so a restored value isn't clobbered by the undefined-default during the loading window — restore THEN validate against real data.

**Tech Stack:** React 18, TypeScript, `@tanstack/react-query`, `localStorage`. No backend, no migration. No FE test runner exists → `tsc --noEmit` + `vite build` is the structural gate; behavioral proof is a live demonstration.

---

## Approach & key decisions

- **Chosen approach:** per-`book_id` `localStorage` blob (`launcher-config:<book_id>`), seeded into `ReadyCard`'s 10 selection `useState`s via lazy initializers, persisted by a `useEffect`. Defaults stay in ONE place (the component, via `saved.X ?? default`); the helper is dumb — returns `Partial<LauncherConfig>` (or `{}` on any error). This is forward-compatible: a saved blob missing a field added later just falls back to that field's default.
- **The 10 persisted selection fields** (`launcher.tsx:577-586`): `provider, transport, extractTransport, judgeTransport, sessionLimitStrategy, extractProvider, extractModel, judgeProvider, judgeModel, model`. **NOT persisted** (ephemeral UI): `expanded, choosing, selected, launching` (there is no `retrying` state in this component — the spec's mention was speculative; confirmed absent).
- **Load-bearing interaction (the spec flagged this — verified the exact mechanism):** two existing effects clobber a restored value during the cold-load window because they treat "models not loaded yet" as "unsupported":
  - `launcher.tsx:639-647` — `apiSupported = modelsQ.data?.api_supported?.[provider] ?? false`. On a hard-refresh first render `modelsQ.data` is `undefined` → `apiSupported=false` → `if (!apiSupported && transport==="api") setTransport("cli")` fires, **demoting a restored `api` pick to `cli`** before the manifest loads (and `cli→api` is never auto-restored). The second branch is already safe (serveability is fail-open when `fleet` is offline/undefined).
  - `launcher.tsx:663-675` — the model-seeding effect: on cold load `modelOptions=[]` → for a restored `api` config `!modelOptions.includes(model)` is true → `setModel(modelOptions[0] ?? null)` **nulls the restored model**, then re-seeds the *first* model (not the restored one) once data lands.
  - **Fix:** add `if (!modelsQ.data) return;` at the top of BOTH effects. This makes validation act on *loaded* data, not the undefined-default. It does NOT bypass the gate: once data loads, a genuinely unservable restored pick is still reset/greyed exactly as today. The provider-reset effect (`:651`) is already guarded by `if (!fleet?.online) return;` — no change needed there.
- **Why this is correct, not a gate-bypass:** restore = initial state → effects run on mount → with the guard, they sanitize once the manifest/fleet is known. A restored `claude·api` on a fleet with no Anthropic worker still greys/falls back to `cli` (capability gate intact) — it just doesn't get spuriously demoted during the half-second before the manifest arrives. It also fixes a latent default-collapse (today the default `transport="api"` can transiently collapse to `cli` on cold load by the same mechanism).
- **Rejected — persist to the batch/backend:** out of scope (spec says FE-only, no migration); `localStorage` is the right tool for ephemeral per-browser UI preference.
- **Rejected — a "skip first write" guard on the persist effect:** unnecessary. Re-writing the just-restored values is harmless; persisting a post-load sanitized value (e.g. unservable `api→cli`) is *desired*.
- **Verified facts (tip `068b153`):** the 10 `useState` defaults at `:577-586`; the three reset effects at `:639` / `:651` (fleet-guarded) / `:663`; `serveability` is fail-open offline (`src/lib/serveability.ts:20`); `Transport`/`RoleTransport`/`SessionLimitStrategy` exported from `src/lib/types.ts:67/71/76`; no FE test runner (`package.json` scripts: dev/build/preview/lint/format only); `launchBody` reads exactly these 10 fields (`:703-722`).

---

### Task 1: pure `localStorage` config module

**Files:**
- Create: `web/src/lib/launcher-config.ts`

- [ ] **Step 1: Write the module**

Create `web/src/lib/launcher-config.ts`:

```typescript
/**
 * Per-book launcher selection persistence (launcher-persist-selections-1).
 * Pure localStorage helpers — no React. Keyed launcher-config:<book_id> so two
 * books keep independent configs. All access is try/catch-wrapped: private mode,
 * quota, or a corrupt blob degrade to a no-op / empty object, never throw.
 */
import type { RoleTransport, SessionLimitStrategy, Transport } from "./types";

export interface LauncherConfig {
  provider: string;
  transport: Transport;
  extractTransport: RoleTransport;
  judgeTransport: RoleTransport;
  sessionLimitStrategy: SessionLimitStrategy;
  extractProvider: string | null;
  extractModel: string | null;
  judgeProvider: string | null;
  judgeModel: string | null;
  model: string | null;
}

const keyFor = (bookId: string) => `launcher-config:${bookId}`;

/**
 * Returns the saved selections for a book as a Partial (so the caller merges
 * onto its own current defaults — forward-compatible when fields are added).
 * Returns {} when storage is unavailable, empty, or the blob is unparseable.
 */
export function loadLauncherConfig(bookId: string): Partial<LauncherConfig> {
  try {
    const raw = localStorage.getItem(keyFor(bookId));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") return parsed as Partial<LauncherConfig>;
    return {};
  } catch {
    return {};
  }
}

/** Persists the selection fields for a book. No-op on any storage error. */
export function saveLauncherConfig(bookId: string, cfg: LauncherConfig): void {
  try {
    localStorage.setItem(keyFor(bookId), JSON.stringify(cfg));
  } catch {
    /* unavailable / quota — ignore, persistence is best-effort */
  }
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit`
Expected: no errors (the module is self-contained; types resolve from `./types`).

- [ ] **Step 3: Commit**

```bash
cd /Users/macmini5/Documents/HCGA-launcher-persist
git add web/src/lib/launcher-config.ts
git commit -m "launcher-persist: pure localStorage config module (per book_id)"
```

---

### Task 2: wire restore + persist into `ReadyCard` (+ guard the cold-load clobbers)

**Files:**
- Modify: `web/src/components/fleet/launcher.tsx` (imports; `:577-586` lazy init; new persist effect; guards at `:639` and `:663`)

- [ ] **Step 1: Add the import**

At the top of `launcher.tsx`, with the other `src/lib` imports, add:

```typescript
import { type LauncherConfig, loadLauncherConfig, saveLauncherConfig } from "@/lib/launcher-config";
```
(Match the existing import alias style in the file — if the file uses relative paths like `../../lib/...` rather than `@/lib/...`, use that instead. Check a neighboring import.)

- [ ] **Step 2: Seed the 10 useStates from saved config (lazy initializers)**

Replace the block at `launcher.tsx:577-586`:

```typescript
  const [provider, setProvider] = useState("claude");
  const [transport, setTransport] = useState<Transport>("api");
  const [extractTransport, setExtractTransport] = useState<RoleTransport>("cli");
  const [judgeTransport, setJudgeTransport] = useState<RoleTransport>("cli");
  const [sessionLimitStrategy, setSessionLimitStrategy] = useState<SessionLimitStrategy>("inherit");
  const [extractProvider, setExtractProvider] = useState<string | null>(null);
  const [extractModel, setExtractModel] = useState<string | null>(null);
  const [judgeProvider, setJudgeProvider] = useState<string | null>(null);
  const [judgeModel, setJudgeModel] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
```

with (seed once from localStorage; defaults are the fallback so a missing field is forward-compatible):

```typescript
  const saved = useState(() => loadLauncherConfig(book.id))[0];
  const [provider, setProvider] = useState(() => saved.provider ?? "claude");
  const [transport, setTransport] = useState<Transport>(() => saved.transport ?? "api");
  const [extractTransport, setExtractTransport] = useState<RoleTransport>(() => saved.extractTransport ?? "cli");
  const [judgeTransport, setJudgeTransport] = useState<RoleTransport>(() => saved.judgeTransport ?? "cli");
  const [sessionLimitStrategy, setSessionLimitStrategy] = useState<SessionLimitStrategy>(() => saved.sessionLimitStrategy ?? "inherit");
  const [extractProvider, setExtractProvider] = useState<string | null>(() => saved.extractProvider ?? null);
  const [extractModel, setExtractModel] = useState<string | null>(() => saved.extractModel ?? null);
  const [judgeProvider, setJudgeProvider] = useState<string | null>(() => saved.judgeProvider ?? null);
  const [judgeModel, setJudgeModel] = useState<string | null>(() => saved.judgeModel ?? null);
  const [model, setModel] = useState<string | null>(() => saved.model ?? null);
```

(`useState(() => loadLauncherConfig(book.id))[0]` reads storage exactly once at mount and keeps the snapshot stable for the lazy seeds below it.)

- [ ] **Step 3: Persist on change**

Add this effect immediately AFTER the three existing validation effects (i.e. after the model-seeding effect that ends at `:675`). It writes only the 10 selection fields:

```typescript
  // Persist selections per book so navigating away + back (and hard-refresh)
  // restores them. Only the launch-selection fields — never ephemeral UI state
  // (expanded / choosing / selected / launching).
  useEffect(() => {
    const cfg: LauncherConfig = {
      provider,
      transport,
      extractTransport,
      judgeTransport,
      sessionLimitStrategy,
      extractProvider,
      extractModel,
      judgeProvider,
      judgeModel,
      model,
    };
    saveLauncherConfig(book.id, cfg);
  }, [
    book.id,
    provider,
    transport,
    extractTransport,
    judgeTransport,
    sessionLimitStrategy,
    extractProvider,
    extractModel,
    judgeProvider,
    judgeModel,
    model,
  ]);
```

- [ ] **Step 4: Guard the two cold-load clobbers**

(a) At the apiSupported reset effect (`:639`), add a load-guard as the FIRST line inside the effect body, before the `if (!apiSupported ...)`:

```typescript
  useEffect(() => {
    if (!modelsQ.data) return; // don't sanitize against an unloaded manifest — would demote a restored api pick
    if (!apiSupported && transport === "api") {
      setTransport("cli");
      return;
    }
    if (fleet?.online && transport === "api" && !serveability(fleet, provider, "api").ok) {
      setTransport("cli");
    }
  }, [apiSupported, transport, fleet, provider, modelsQ.data]);
```
(Add `modelsQ.data` to the dep array as shown so the effect re-runs once the manifest lands.)

(b) At the model-seeding effect (`:663`), add the same guard as the FIRST line inside the effect body, before `if (transport === "api")`:

```typescript
    if (!modelsQ.data) return; // wait for modelOptions — else a restored api model gets nulled + re-seeded to the wrong one
```
(The effect already depends on `modelsQ.data`, so no dep-array change is needed there. Leave the existing `eslint-disable-next-line react-hooks/exhaustive-deps` as-is.)

- [ ] **Step 5: Typecheck + build (the structural gate)**

Run: `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build`
Expected: typecheck clean; build writes `web/dist/` with no errors.

- [ ] **Step 6: Commit**

```bash
cd /Users/macmini5/Documents/HCGA-launcher-persist
git add web/src/components/fleet/launcher.tsx
git commit -m "launcher-persist: restore+persist ReadyCard selections; guard cold-load clobbers"
```

---

### Task 3: behavioral acceptance + Finish

**Files:**
- Modify: `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`
- Move: this plan → `docs/superpowers/plans/shipped/`

- [ ] **Step 1: Behavioral acceptance (controller demonstrates against the live server)**

The controller runs the dev/built UI and verifies all four binding behaviors (FE-only, no money spent — no homework launched; we only set pickers and navigate):
1. **Navigate-away → return:** on a book, pick a non-default config (e.g. `gemini` / `gemini-2.5-pro` / `api`), go to another route, return → all pickers intact.
2. **Hard-refresh:** reload the page → still intact (this is the case the cold-load guards protect).
3. **Capability gate not defeated:** restore/pick a combo the fleet can't serve (e.g. `claude·api` with no Anthropic worker online) → it still greys / falls back to `cli` once the manifest+fleet load, does NOT launch a pending-forever combo.
4. **Two books independent:** set different configs on two books → each restores its own (per-`book_id` key, no cross-contamination); confirm two `launcher-config:<id>` keys in `localStorage`.

Record the result. If any fail, fix before finishing.

- [ ] **Step 2: Rebase-check before finishing**

```bash
cd /Users/macmini5/Documents/HCGA-launcher-persist
git fetch origin
git log HEAD..origin/Nggaev-v2 --oneline   # if non-empty: rebase onto origin/Nggaev-v2, re-run tsc+build, then continue
```

- [ ] **Step 3: Finish — worklog + INDEX + plan move**

- Worklog entry in `docs/memory/MASTER_MEMORY.md` (verify next-free — `0091` is current highest → likely `0092`) + a row in `docs/memory/INDEX.md`.
- De-stale reference docs ONLY if they describe launcher selection behavior (grep `README.md` / `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` for "launcher"; likely no change needed — note that in the worklog).
- `git mv docs/superpowers/plans/2026-06-26-launcher-persist-selections.md docs/superpowers/plans/shipped/`
- Commit with staged files only (never `git add -A`).

- [ ] **Step 4: PR to the gatekeeper (no self-merge)**

Push `launcher-persist-selections`, open a PR targeting `Nggaev-v2`, route it back to the gate for review + merge.
