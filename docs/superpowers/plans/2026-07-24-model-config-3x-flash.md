# Model config → 3.x flash family on the plain Gemini API key

## Approach & key decisions

**Goal:** move the generation pipeline off the 2.5 family to the 3.x flash models the
restored-$100k plain API key serves — content=`gemini-3.6-flash`, extract=`gemini-3.5-flash-lite`,
judge=`gemini-3.5-flash`, solver=`gemini-3.1-pro-preview` — without breaking cost attribution,
the self-grade guard, or the manifest-enforced invariants.

**Facts verified against the live key + code (not assumed):**
- The plain `GEMINI_API_KEY` (already in this machine's `.env`) serves all target IDs as real
  models: `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-pro-preview`
  (list_models probe, 2026-07-24).
- **2.5 is NOT deprecated on this key** — it still serves `gemini-2.5-flash/-pro/-lite`. So the
  premise "2.5 is unsupported" is false here; we KEEP 2.5 priced + in the manifest (past-row
  attribution + selectable) and only change the DEFAULTS. (User decision: "keep priced, drop as default".)
- **Auth needs no code change.** `agent._auth_env` (gemini/api branch) prefers `GEMINI_API_KEY`
  and only falls back to Vertex SA when the key is absent; `api_transport._client_for` builds a
  Developer-API client (`genai.Client(api_key=…)`, no Vertex location) when the key is present, so
  `_location_for`/Vertex routing is bypassed automatically. The "switch" is purely which key string
  each worker's `.env` holds — an operator/fleet task the user owns.
- **3.5-flash passes the real judge path.** A live run through production `phase_judge.judge`
  (`schema=Verdict`, transport=api) returned a valid parsed Verdict **5/5**, zero degraded-to-
  unavailable — the existing `_strip_code_fences` + one-retry absorbs the JSON-fencing the raw
  probe saw. Acceptance scales this to ~20 to confirm the ~0% parse-fail rate holds.
- **Live per-role config = the `launch_defaults` singleton (id=1)**, stamped onto each job; CLAUDE.md's
  `settings.extract_*` is stale (no such settings exist). `/settings` PUT validates picks against
  `agent_models.is_valid`, so new models MUST be in the manifest before the row can point at them.
  Current live row (queried 2026-07-24): content=`gemini-3-flash-preview`, extract=`gemini-2.5-flash`,
  judge=`gemini-2.5-flash`, **solver=`gemini-3.1-pro-preview` (already the target — no change)**.
- Pricing (Google's public list, https://ai.google.dev/gemini-api/docs/pricing, fetched 2026-07-24;
  re-verified in Task 1): 3.6-flash `1.50/7.50/0.15`, 3.5-flash `1.50/9.00/0.15`, 3.5-flash-lite
  `0.30/2.50/0.03` (input/output/cache_read per 1M). 3.1-pro-preview is tiered (≤200k `2/12/0.20`,
  >200k `4/18/0.40`); our prompts are well under 200k and the flat ≤200k entry already in `PRICE_MAP`
  is correct — no change.
- Self-grade guard is safe: content 3.6 ≠ judge 3.5 ≠ solver 3.1, all distinct — `judge_model_for`'s
  self-match fallback never triggers.

**Chosen approach:** a small code PR (manifest + pricing + tiers + one config default) plus a
**guarded data-migration** that flips the `launch_defaults` singleton — guarded on the current
values so a manual /settings override is never clobbered. Rejected: (a) flipping the row only via
the /settings UI — not version-controlled, easy to forget across the fleet's shared DB; (b) ripping
2.5 out of manifest/pricing — needless, 2.5 still works and past `agent_usages` rows need the price
rows for attribution; (c) tiered-pricing support for 3.1-pro — YAGNI, solver prompts are <200k.

**Acceptance = real model calls over the plain key** (the transport production uses): scaled judge
validation + one full end-to-end homework on the new config. Ops the user owns: fleet `.env` key
rollout + worker restart (prompts/config are read fresh, but workers must have the key).

Branch: `feat/model-config-3x-flash` off `origin/Nggaev-v2` (collision gate 2026-07-24: no
overlapping branches/PRs; `feat/gemini-global-default` is merged/stale, touches none of these files).

---

## Task 1 — Register the 3 new models (manifest + pricing + tiers)

**Files:** `app/services/agent_models.py`, `app/services/pricing.py`, `app/services/model_tiers.py`,
`tests/services/test_agent_models.py`, `tests/services/test_pricing.py`, `tests/services/test_model_tiers.py`
(+ `tests/api/test_agent_models_tiers.py` is the manifest-completeness test — it must stay green).

1. **RED** — add assertions:
   - `test_agent_models.py`: `is_valid("gemini", "gemini-3.6-flash")`, `…"gemini-3.5-flash"`,
     `…"gemini-3.5-flash-lite")` all True; 2.5 entries still valid.
   - `test_pricing.py`: `cost_usd("gemini","gemini-3.6-flash",{"prompt_tokens":1_000_000,"output_tokens":0,"cached_tokens":0})`
     == 1.50; output-only == 7.50; and the same shape for 3.5-flash (1.50/9.00) and 3.5-flash-lite (0.30/2.50).
   - `test_model_tiers.py`: `tier_of("gemini-3.6-flash")`==2, `…("gemini-3.5-flash")`==2, `…("gemini-3.5-flash-lite")`==4.
   Run the three files → new assertions fail.
2. **GREEN**:
   - `MODEL_MANIFEST["gemini"]`: insert `"gemini-3.6-flash"`, `"gemini-3.5-flash"`, `"gemini-3.5-flash-lite"`
     ahead of the 2.5 block (recommended-first ordering); keep every existing entry.
   - `PRICE_MAP`: add the three rows with a source+date comment
     (`# Google list price, ai.google.dev/gemini-api/docs/pricing, 2026-07-24`):
     `("gemini","gemini-3.6-flash"): {"input":1.50,"output":7.50,"cache_read":0.15}`,
     `("gemini","gemini-3.5-flash"): {"input":1.50,"output":9.00,"cache_read":0.15}`,
     `("gemini","gemini-3.5-flash-lite"): {"input":0.30,"output":2.50,"cache_read":0.03}`.
   - `_MODEL_TIER`: `"gemini-3.6-flash":2`, `"gemini-3.5-flash":2`, `"gemini-3.5-flash-lite":4`.
   - **Before committing, re-verify the three rates against the live pricing page** (WebFetch the URL,
     confirm the exact numbers still match; if Google changed them, use the live values and note it).
3. Run `uv run python -m pytest tests/services/test_agent_models.py tests/services/test_pricing.py tests/services/test_model_tiers.py tests/api/test_agent_models_tiers.py -q` — all green (the completeness test proves manifest⊆tiers⊆pricing).
4. **Commit:** `feat(models): register gemini-3.6-flash / 3.5-flash / 3.5-flash-lite (manifest+pricing+tiers)` (stage only the 6 files).

## Task 2 — Move the TOC-validator default off 2.5

**Files:** `app/config.py`, `tests/` (the config/toc-validator test home — implementer locates it).

1. **RED** — assert `settings.toc_validation_model == "gemini-3.5-flash-lite"` (a cheap vision check;
   lite matches its low-value/high-input shape).
2. **GREEN** — `config.py:224` `toc_validation_model: str = "gemini-2.5-flash"` → `"gemini-3.5-flash-lite"`.
   Leave `gemini_model = "gemini-2.0-flash-exp"` (line 28) UNTOUCHED unless a grep proves it's read at
   runtime (earlier sweep found no `settings.gemini_model` use outside tests — note it in the report, don't chase).
3. `uv run python -m pytest tests/ -k "toc or config or validation" -q`.
4. **Commit:** `chore(config): toc validator default gemini-2.5-flash → gemini-3.5-flash-lite`.

## Task 3 — Flip the launch_defaults singleton (guarded migration)

**Files:** new `alembic/versions/00XX_launch_defaults_3x_flash.py` (next revision id ≤32 chars),
`tests/` real-DB migration/roundtrip test if one exists for launch_defaults (else a repo-level assertion).

1. **Approach:** a data-only migration updating id=1, **guarded on current values** so a manual
   override is preserved:
   ```sql
   UPDATE launch_defaults SET content_model='gemini-3.6-flash'
     WHERE id=1 AND content_provider='gemini' AND content_model='gemini-3-flash-preview';
   UPDATE launch_defaults SET extract_model='gemini-3.5-flash-lite'
     WHERE id=1 AND extract_provider='gemini' AND extract_model='gemini-2.5-flash';
   UPDATE launch_defaults SET judge_model='gemini-3.5-flash'
     WHERE id=1 AND judge_provider='gemini' AND judge_model='gemini-2.5-flash';
   -- solver already gemini-3.1-pro-preview; no update.
   ```
   `downgrade()` reverses each with the symmetric guard. NO schema change.
2. **RED/verify** (real DB, `RUN_DB_INTEGRATION=1` on a scratch DB seeded to the current values):
   after `upgrade`, the row reads the three new models; a row pre-mutated to a manual override is
   left untouched (proves the guard).
3. `uv run alembic upgrade head` on the scratch DB green; `uv run python -m pytest tests/ -q` (offline bar) green.
4. **Commit:** `feat(launch-defaults): default gemini roles to 3.x flash family (guarded)`.

## Task 4 — Acceptance A: scaled judge validation (real 3.5-flash, plain key)

Scratchpad script (not committed): pull ~20 stored phase outputs + their extracts spanning all
phase types + several subjects; run each through `phase_judge.judge(judge_model="gemini-3.5-flash",
transport="api")`. Report: count returning a valid parsed Verdict (`available=True`), the parse-fail/
degrade rate, and how many needed the one retry. **Bar:** ≥95% valid-Verdict (the 5/5 pre-check
suggests ~100%); if it falls below, harden the parse path (balanced-brace JSON extractor before
`model_validate_json`) as an added task before shipping. Bounded ~20 calls; report cost.

## Task 5 — Acceptance B: one full end-to-end homework on the new config

Scratchpad script (not committed): generate ONE real homework end-to-end over `transport=api`
on the plain key with the new roles — content=`gemini-3.6-flash`, extract=`gemini-3.5-flash-lite`,
judge=`gemini-3.5-flash`, solver=`gemini-3.1-pro-preview` — for one real lesson. Assert: all 11
content phases produced + judged, solver ran on boss-arena, every `agent_usages` row priced > $0
(no $0/unpriced rows → proves pricing wired), and report the total `cost_usd` (sanity vs the
~$1.55/hw estimate). Run on a scratch DB, fleet untouched. No mass generation — one homework.

## Task 6 — Finish

1. Full suite: `uv run python -m pytest tests/ -q` (offline bar).
2. Rebase-check: `git fetch origin && git log HEAD..origin/Nggaev-v2` — rebase + re-run if base moved.
3. Push `feat/model-config-3x-flash`, open PR (base `Nggaev-v2`) — user/GK gates the merge; never self-merge.
4. Same finish: worklog in `docs/memory/MASTER_MEMORY.md` + `INDEX.md` row; de-stale
   **CLAUDE.md** (the stale `settings.extract_provider/extract_model` line — extract config is the
   `launch_defaults` row, and the default extract model is now 3.5-flash-lite), `docs/HOW_IT_WORKS.md`
   + `docs/CODE_MAP.md` (model/pricing/extract-pin references), `docs/DATABASE.md` (launch_defaults
   default values); `git mv` this plan → `docs/superpowers/plans/shipped/`.
5. **Ops (post-merge, operator — user owns):** apply the migration on the head DB
   (`alembic upgrade head`); roll the new `GEMINI_API_KEY` to every worker's `.env` + restart. Until a
   worker has the key it can't serve api gemini jobs (claim gate skips it) — not a failure, just capacity.

## Explicitly out of scope

- Removing 2.5 from manifest/pricing (kept — still works, needed for attribution).
- Tiered-pricing support for 3.1-pro (prompts <200k; flat ≤200k entry is correct).
- Fleet `.env` key rollout + worker restart (operator, user-owned).
- Any change to `_auth_env` / `api_transport` (already key-preferring and Vertex-bypassing).
- Editing the legacy `settings.gemini_model` unless proven live.
