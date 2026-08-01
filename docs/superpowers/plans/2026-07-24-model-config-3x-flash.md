# Model config → 3.x flash family on the plain Gemini API key (rev 2)

## Approach & key decisions

**Goal:** move the generation pipeline off the now-dead 2.5 family to the 3.x flash models the
restored-$100k plain Developer-API key serves — content=`gemini-3.6-flash`,
extract=`gemini-3.5-flash-lite`, judge=`gemini-3.5-flash`, solver=`gemini-3.1-pro-preview` — without
breaking cost attribution, the CLI/API transport invariant, the self-grade guard, or the fleet auth.

**Facts verified against the live key + code (rev 2 corrects rev-1 errors the gate caught):**
- All four target IDs are **callable** on the plain key (real `generate_content`, 2026-07-24):
  `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.1-pro-preview` all returned text; `gemini-3.5-flash-lite`
  listed + priced (validated in Task 5's real TOC call).
- **2.5 is DEAD on this key — corrected.** A real request to `gemini-2.5-flash` returns
  `404 "no longer available to new users"` (same for 2.5-pro / 2.5-lite). `list_models` still lists them
  but they are NOT callable — rev 1 wrongly concluded "2.5 works" from the listing. **Decision:** REMOVE
  the 2.5 IDs from `MODEL_MANIFEST` (not offerable — they 404 on the target key, and cli is retired),
  but KEEP their `PRICE_MAP` + `_MODEL_TIER` rows (historical `agent_usages` attribution; tier/price
  tables may be supersets of the manifest). This is the deliberate legacy policy the gate asked for.
- **The three new flash models fail on the gemini CLI** (historical prod: 16/16 3.5-flash CLI failures;
  `tests/services/test_agent_models.py::test_phantom_gemini_3_5_flash_removed` encodes it). The manifest
  is shared CLI+API and there is **no model-level api-only mechanism today** (`API_ONLY_PROVIDERS` is
  provider-level, only `clodex`). **Decision:** add a model-level api-only set and reject these three for
  `transport=cli` — consistent with the retired-cli standing decision.
- **Fleet auth is NOT "just put the key in .env" — corrected.** An active SA-key assignment calls
  `sa_key_apply.set_credentials_env` which does `env.pop("GEMINI_API_KEY")` on the worker's live
  `os.environ` (SA wins; `worker.py` applies it on idle). So an SA-assigned worker ignores the plain key.
  The switch requires an **operational transition** (Task 6 §Ops), not a code change; changing credential
  precedence in code is rejected (it would fight the BE-16 per-project SA limiter design). Consolidating
  the fleet onto ONE plain key also collapses N per-project concurrency lanes into one credential — a
  capacity implication the operator must weigh (flagged, user-owned).
- Live per-role config = the `launch_defaults` singleton (id=1); `/settings` validates picks via
  `agent_models.is_valid`. Current prod row (2026-07-24): content=`gemini-3-flash-preview`/api,
  extract=`gemini-2.5-flash`/api, judge=`gemini-2.5-flash`/api, **solver=`gemini-3.1-pro-preview`/api
  (already target)**, toc_transport=`api`. A **fresh 0048 DB differs**: content=`gemini-2.5-pro`,
  toc_transport=`cli`, extract/judge_transport=`inherit` — so a value-guarded UPDATE can't produce a
  consistent result from both states, and "never clobbers a manual override" is unachievable by value
  match (a manual pick equal to a legacy value is indistinguishable). **Decision:** migration 0049 sets
  id=1 to the full target tuple **unconditionally** (honest, once-only — alembic never re-runs it), fixing
  both the fresh-DB (2.5-pro, toc=cli) and prod cases; downgrade restores the current prod tuple.
- **3.5-flash passes the real judge path** — production `phase_judge.judge` (`schema=Verdict`,
  transport=api) returned a valid parsed Verdict 5/5; the existing `_strip_code_fences` + one-retry
  absorbs the fencing. Task 4 scales to a **20/20** bar on a scratch DB.
- **0 nonterminal jobs are stamped with 2.5** right now (queried) — the drain/restamp precondition is
  currently satisfied; Task 6 §Ops re-asserts it at switch time.
- Pricing (Google list, ai.google.dev/gemini-api/docs/pricing, 2026-07-24; re-verified in Task 1):
  3.6-flash `1.50/7.50/0.15`, 3.5-flash `1.50/9.00/0.15`, 3.5-flash-lite `0.30/2.50/0.03`. 3.1-pro
  tiered (≤200k `2/12/0.20`) — flat entry already correct, no change.
- **The completeness test covers tiers only, not pricing** (`test_tier_of_covers_every_manifest_pair`);
  Task 1 adds a `PRICE_MAP`-coverage test so a future unpriced manifest model can't bill $0.
- Self-grade guard safe: content 3.6 ≠ judge 3.5 ≠ solver 3.1.
- **Projected cost ≈ $1.43/hw** with flash-lite extract (content $0.427 + extract $0.034 + judge $0.847
  + solver $0.121), not $1.55.

**Collision / integration order:** `git fetch` 2026-07-24 — `feat/gemini-global-default` is merged/stale
(no target-file overlap). **Open PR #108 touches `docs/memory/INDEX.md` + `MASTER_MEMORY.md`** — the same
files Task 6 edits. **Integration order:** this branch's *code* is independent of #108; only the Task-6
worklog edit collides. Resolution: land Task 6's memory edits LAST, and immediately before pushing,
`git fetch origin` — if #108 merged first, rebase onto it and re-apply the worklog append (append-only,
trivial merge); if this merges first, #108 rebases onto us. Worklog id is **0161** (0160 shipped this
session). Next alembic revision is **0049**.

Branch: `feat/model-config-3x-flash` off `origin/Nggaev-v2`.

---

## Task 1 — Register 3 new API-only models; retire 2.5 from the manifest

**Files:** `app/services/agent_models.py`, `app/services/pricing.py`, `app/services/model_tiers.py`,
`tests/services/test_agent_models.py`, `tests/services/test_pricing.py`,
`tests/api/test_agent_models_tiers.py`.

1. **RED**:
   - `test_agent_models.py`: replace `test_phantom_gemini_3_5_flash_removed` with
     `test_gemini_3_5_flash_api_only`: `is_valid("gemini","gemini-3.5-flash")` True;
     `validate_transport("gemini","gemini-3.5-flash","cli")` returns a rejection reason (non-None);
     `validate_transport("gemini","gemini-3.5-flash","api")` returns None (allowed). Same for
     `gemini-3.6-flash`, `gemini-3.5-flash-lite`. Update `test_real_gemini_models_still_valid` to drop
     the 2.5 IDs and assert `is_valid("gemini","gemini-2.5-flash") is False` (retired from manifest).
   - `test_pricing.py`: exact-rate tests for the 3 new models AND a new
     `test_every_manifest_model_is_priced` iterating `MODEL_MANIFEST` asserting a `PRICE_MAP` entry
     (or a non-zero `cost_usd`) for each pair — closes the pricing-coverage gap.
   - `test_model_tiers.py`: `tier_of("gemini","gemini-3.6-flash")==2`, `("gemini","gemini-3.5-flash")==2`,
     `("gemini","gemini-3.5-flash-lite")==4` (note the **two-arg** `tier_of(provider, model)` signature).
2. **GREEN**:
   - `agent_models.py`: in `MODEL_MANIFEST["gemini"]` add the 3 new IDs, **remove** `gemini-2.5-pro`,
     `gemini-2.5-flash`, `gemini-2.5-flash-lite`. Add `GEMINI_API_ONLY_MODELS = frozenset({
     "gemini-3.6-flash","gemini-3.5-flash","gemini-3.5-flash-lite"})` and, in `validate_transport`,
     reject `transport=="cli"` when `model in GEMINI_API_ONLY_MODELS` (mirroring the existing
     `API_ONLY_PROVIDERS` cli rejection, with a clear reason string).
   - `pricing.PRICE_MAP`: add the 3 rows (source+date comment); KEEP the 2.5 rows (historical).
   - `_MODEL_TIER`: add the 3 (`3.6-flash`:2, `3.5-flash`:2, `3.5-flash-lite`:4); KEEP 2.5 rows.
3. `uv run python -m pytest tests/services/test_agent_models.py tests/services/test_pricing.py tests/services/test_model_tiers.py tests/api/test_agent_models_tiers.py tests/services/test_judge_resolution.py tests/integration/test_claim_gate_self_grade.py -q` — all green (verify no other test asserts 2.5 selectability; if one does, it encoded the now-false premise — update it in this commit with a comment).
4. **Commit:** `feat(models): 3.x flash family api-only, retire 2.5 from manifest (keep priced)`.

## Task 2 — TOC-validator default off 2.5

**Files:** `app/config.py:224`, `tests/services/test_agent.py` (the TOC-validator config test home —
`grep -rl toc_validation_model tests/`; implementer confirms exact file before adding).

1. **RED** — assert `settings.toc_validation_model == "gemini-3.5-flash-lite"`.
2. **GREEN** — `toc_validation_model: str = "gemini-2.5-flash"` → `"gemini-3.5-flash-lite"`. Leave
   `gemini_model = "gemini-2.0-flash-exp"` (line 28) untouched (report confirms no runtime read).
3. `uv run python -m pytest tests/ -k "toc_valid or config" -q`.
4. **Commit:** `chore(config): toc validator default 2.5-flash → 3.5-flash-lite`.

## Task 3 — Migration 0049: flip launch_defaults to the target tuple (unconditional)

**Files:** `alembic/versions/0049_launch_defaults_3x_flash.py` (revision id `0049_launch_defaults_3x`,
≤32 chars), `tests/repositories/test_launch_defaults_migration.py` (new real-DB test).

1. **`upgrade()`** — single UPDATE of id=1 to the target: content=`gemini-3.6-flash`,
   extract=`gemini-3.5-flash-lite`, judge=`gemini-3.5-flash`, solver=`gemini-3.1-pro-preview`, all
   providers `gemini`, and set `content_transport=extract_transport=judge_transport=toc_transport=
   solver_transport='api'` (fixes the fresh-DB `toc_transport='cli'` + `inherit` cases). **Unconditional**
   (once-only; drop the false "never clobbers override" claim — documented in the migration docstring).
   **`downgrade()`** — restore the current prod tuple (content=`gemini-3-flash-preview`,
   extract/judge=`gemini-2.5-flash`, solver=`gemini-3.1-pro-preview`, toc/others=`api`).
2. **RED/verify** (real DB, `RUN_DB_INTEGRATION=1`, scratch DB): seed the row to (a) the **fresh-0048
   tuple** (content=2.5-pro, toc_transport=cli, extract/judge_transport=inherit) and (b) the **current
   prod tuple**; after `upgrade`, BOTH yield the identical target tuple; after `downgrade`, both yield the
   prod tuple. Assert the exact 5 model + 5 transport columns.
3. `uv run alembic upgrade head` + `alembic downgrade -1` clean on the scratch DB; offline suite green.
4. **Commit:** `feat(launch-defaults): migration 0049 → 3.x flash target tuple (api transports)`.

## Task 4 — Acceptance A: scaled judge validation (scratch DB, 20/20)

Scratchpad script (not committed) against a **scratch DB** (`phase_judge.judge` writes `agent_usages`
via the global session — never run against edu_copy): copy ~20 stored phase outputs + extracts
(spanning every phase type + ≥4 subjects) into the scratch DB; judge each with
`judge_model="gemini-3.5-flash"`, transport=api. **Bar: 20/20 return a valid parsed Verdict**
(`available=True`), and record how many needed the retry. Report cost. If any call degrades to
unavailable → do **Task 4b** before shipping.

## Task 4b — (CONDITIONAL, only if Task 4 < 20/20) harden the Verdict parser

**Files:** `app/services/agent.py` (the `schema.model_validate_json` path ~line 1127),
`tests/services/test_agent_schema_parse.py` (new).
1. **RED** — unit tests feeding the parser prose-wrapped JSON (`"Here is the verdict:\n{...}"`),
   fenced JSON, and JSON with trailing prose; assert a balanced-brace extractor recovers the object.
2. **GREEN** — before `model_validate_json`, extract the first balanced `{...}` span from the
   fence-stripped candidate; fall back to the raw candidate. Keep the one-retry.
3. Re-run Task 4 → 20/20. **Commit:** `fix(agent): balanced-JSON extraction for structured verdicts`.

## Task 5 — Acceptance B: full pipeline incl. TOC over the plain key (scratch DB)

Scratchpad script (not committed) on a **scratch DB**, fleet untouched:
1. **TOC path (was missing in rev 1):** ingest a REAL PDF (copy one book's `source.pdf` + row into the
   scratch DB, clear its toc rows) and run `toc_extractor` with extract=`gemini-3.5-flash-lite`,
   toc_transport=api — assert a structured `ExtractedTOC` is produced AND the vision `validate_toc`
   (`toc_validation_model=gemini-3.5-flash-lite`) returns a status without raising.
2. **Content path:** generate ONE homework end-to-end (`pipeline.run` in-process) on the target roles —
   content=3.6-flash, extract=3.5-flash-lite, judge=3.5-flash, solver=3.1-pro. Assert all 11 phases
   produced + judged, solver ran on boss-arena, **every `agent_usages` row prices > $0** (proves pricing
   wired for all four models), report total `cost_usd` (sanity vs ~$1.43).
No mass generation — one TOC + one homework. Report cost.

## Task 6 — Finish

1. Full offline suite green.
2. **Rebase gate (#108 overlap):** `git fetch origin` — if `origin/Nggaev-v2` moved (esp. #108 merged),
   rebase; re-apply the worklog/INDEX appends on top (append-only). Re-run suite.
3. Push, open PR (base `Nggaev-v2`) — user/GK gates the merge; never self-merge.
4. Same finish: worklog **0161** in `MASTER_MEMORY.md` + `INDEX.md` row; de-stale **CLAUDE.md** (the
   stale `settings.extract_provider/extract_model` line — per-role config is the `launch_defaults` row;
   note 2.5 retired, defaults now 3.x flash), `docs/HOW_IT_WORKS.md` + `docs/CODE_MAP.md` (model/pricing/
   extract references, the new model-level api-only rule), `docs/DATABASE.md` (launch_defaults defaults);
   `git mv` this plan → `docs/superpowers/plans/shipped/`.
5. **Ops (operator, user-owned) — the required fleet transition (NOT "just edit .env"):**
   a. Pre-flight: assert **0 nonterminal jobs stamped with a 2.5 model** (drain/cancel any first).
   b. **Scrub every host's SA-key assignment** (the SA-keys panel scrub) so `set_credentials_env` stops
      popping `GEMINI_API_KEY`; verify the assignment is cleared and no `active.json` SA residue remains.
   c. Install the plain `GEMINI_API_KEY` in every worker's `.env`; **restart** each worker.
   d. Apply migration on the head DB (`alembic upgrade head`).
   e. Note: the fleet now shares ONE credential → one concurrency/rate lane (was N SA lanes); watch the
      credential limiter / 429s after cutover.

## Explicitly out of scope

- Changing `_auth_env`/`_gemini_client` credential precedence (would fight the SA limiter design).
- Tiered-pricing support for 3.1-pro (<200k prompts; flat entry correct).
- Re-enabling 2.5 anywhere (dead on the key; cli retired).
- The fleet SA-scrub + key rollout + restart itself (operator; the plan documents the sequence).
