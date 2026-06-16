# SDK-based `transport=api` (Gemini + Claude) — design

- **Date:** 2026-06-16
- **Branch:** `feat/sdk-api-transport` (off `Nggaev-v2`)
- **Backlog:** follow-up to Phase 4 transport toggle (worklog 0053) — make `transport=api` an *efficient* direct API path, not a CLI-with-key wrapper.
- **Status:** design — pending written-spec review

## Problem

Today `transport=api` does **not** make a direct API call. It runs the **same provider CLI** as `transport=cli`, just with the API key injected into the child env (`agent._auth_env`). The CLIs are *agents*: they prepend a large system prompt and may run internal turns.

Measured (`scripts/api_vs_cli_compare.py`, gemini-3.1-pro-preview, identical prompt, CLI-with-key vs direct):

| | CLI (`transport=api`) | direct (SDK) |
|---|---|---|
| input tokens | 12,118 (incl. CLI's ~9,170-token system prompt) | 2,948 |
| total tokens (realistic CBP) | ~18k–67k | ~7k–9k |
| latency | 56–85s | 35–60s |

So the CLI path is **~2.6–10× the tokens and ~1.5–3× slower** for the same output. A spec-graded review of the generated case-based-preview content found **direct-API output is on par with (slightly better than) the CLI output** — no quality penalty. The official **`google-genai` SDK** was confirmed working against the Vertex service account (`scripts/api_vs_cli_compare.py --only sdk` produced a complete, graded-PASS CBP). Conclusion: for api jobs, call the provider directly.

## Goal

When `transport=api`, generate via the official provider **SDK** instead of spawning the CLI:
- **gemini** → `google-genai`
- **claude** → `anthropic`

Everything else — prompt assembly, usage recording, pricing, failover, the judge, the `(text, usage)` contract — stays identical. `transport=cli` is **completely unchanged** (still the CLIs). The other three CLIs (kimi/codex/opencode) are cli-only and untouched (`api_supported` is already `{claude, gemini}`).

## Locked decisions (from the investigation)

1. **SDKs, not hand-rolled httpx.** For gemini the SDK handles Vertex-SA OAuth mint/refresh internally (the painful part of raw httpx) and one client covers AI-Studio-key *and* Vertex modes; it also tracks Vertex endpoint/version churn. For claude the SDK adds typed usage + retries over a bare key call. Verified gemini end-to-end via the harness.
2. **Both api providers move to the SDK** (gemini + claude). Including claude matters because the **Opus judge runs on every api job** (`model_tiers.judge_model_for` → claude) and is the largest Claude cost line — so it captures the biggest saving.
3. **Dispatch at the `_spawn` chokepoint** (see verified facts) — one branch covers all api spawns of both providers (content, the gemini-pinned extract, the claude judge) with zero call-site edits.
4. **Gemini: no output cap** (parity with today's CLI — the gemini CLI is invoked with no token-limit flag). **Claude: an output cap is mandatory** (the Anthropic Messages API *requires* `max_tokens`) → new `settings.api_max_output_tokens` (default **8192**), applied to the claude path only. ⚠ **Open for your nod:** 8192 covers a CBP (~6k output) + the judge with headroom; if any phase truncates, bump the setting (model-cap permitting). No `thinking_config` override in v1 (defaults).
5. **Text-only in v1.** No content phase currently attaches a PDF (`PHASE_FILE_NEEDED` is empty for every subject; extract uses local whole-book text). The SDK path supports `attachments=[]` and **raises loudly** if a non-empty attachment list is ever passed under api transport. File-over-API is a documented out-of-scope follow-up.

## Verified facts the build relies on (don't re-derive)

- **`_spawn` (`app/services/agent.py:303`) is the single provider-call chokepoint.** Only place `asyncio.create_subprocess_exec` runs for these providers; already receives the `Provider` object + `transport`; returns `(returncode, result_text, usage, stderr)` with normalized usage keys `prompt_tokens / output_tokens / cached_tokens / total_tokens / raw`. Every caller (`run_phase` content `:649`, extract path, judge) goes through it. A dispatch at the top of `_spawn` covers every api spawn of both providers uniformly.
- **`_auth_env` api branches** encode the credential + loud-on-missing rules to mirror: gemini → `GEMINI_API_KEY` else Vertex SA (`GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT`, location default `global`) else **raise**; claude → `ANTHROPIC_API_KEY` else **raise**. Never inject `""` / never silent OAuth fallback.
- **Per-provider usage semantics** (must be preserved for correct pricing):
  - **gemini** (`providers/gemini.py:parse_envelope`): `output = candidates + thoughts` (thoughts billed as output); `prompt` INCLUDES cached. SDK `usage_metadata` → `prompt_token_count`, `candidates_token_count`, `thoughts_token_count`, `cached_content_token_count`, `total_token_count`.
  - **claude** (`providers/claude.py`): `prompt = input_tokens` (DISJOINT — excludes cache reads); `cached = cache_read_input_tokens`; `output = output_tokens`; `total = input + output`. SDK `message.usage` → `input_tokens`, `output_tokens`, `cache_read_input_tokens`.
- **Pricing is per-provider and transport-agnostic** (`pricing.cost_usd`); api rows record `auth_mode="api"` and price from the same usage dict — **no pricing change**. (The cached-token formula already differs per provider; don't collapse it.)
- **Neither SDK is currently a dependency** (CLI-only era, worklog 0003 — removed for multi-provider *uniformity*, not because SDKs were bad). Add both to `pyproject.toml`.

## Design

### 1. New module — `app/services/api_transport.py`

Single entry point; lazy-imports each SDK inside its branch so the cli path never loads them.

```python
async def generate(*, provider: str, model: str | None, prompt: str,
                    attachments: list[Path]) -> tuple[int, str, dict, str]:
    """Generate via the provider SDK. Returns the SAME 4-tuple as agent._spawn:
    (returncode, text, usage, stderr). rc=0 success; on error rc=1 + stderr carries
    the cause (so run_phase's existing failure/failover path is unchanged)."""
```

- **Attachments guard** (both providers): non-empty → raise `NotImplementedError("api transport is text-only in v1")`. (Unreachable today.)
- **gemini branch** (`from google import genai`): build client mirroring `_auth_env` (key → `genai.Client(api_key=…)`; else Vertex SA → `genai.Client(vertexai=True, project=…, location=…)`; else raise). `await client.aio.models.generate_content(model=resolved, contents=prompt)` (native async; no cap; no `to_thread`). Map `usage_metadata` (thoughts folded into output, prompt cached-inclusive); store it in `raw`.
- **claude branch** (`from anthropic import AsyncAnthropic`): `ANTHROPIC_API_KEY` or raise. `await client.messages.create(model=resolved, max_tokens=settings.api_max_output_tokens, messages=[{"role":"user","content":prompt}])`. Text = join of `block.text` for text blocks. Map `message.usage` (disjoint cached); store in `raw`.
- **Error mapping** (both): wrap the SDK call; on exception return `(1, "", empty_usage, str(exc))` so `run_phase`'s rc≠0 handling + same-provider failover apply. Auth/permission (401/403) messages propagate into `stderr` so the existing api auth-failure handling stays loud (job-level, not silent degrade).

### 2. Dispatch in `agent._spawn`

At the top of `_spawn` (after the docstring, before `binary = _resolve_binary(provider)`):

```python
if transport == "api" and provider.name in ("gemini", "claude"):
    from app.services import api_transport  # lazy — keeps SDK imports off the cli path
    return await api_transport.generate(
        provider=provider.name, model=model, prompt=prompt, attachments=attachments,
    )
```

Nothing else changes — `_spawn` CLI body, `run_phase`, `run_phase_prompt`, the extract path, the judge, the pipeline. Usage recording (`_record_usage(..., auth_mode=transport)`), the `(text, pt, ot)` return, schema-mode retries, and `_run_with_failover` all operate unchanged on the returned 4-tuple. (Schema/JSON mode used by the judge is handled at the `run_phase` layer — the SDK path just returns text — so no extra work.)

### 3. Config

Add `api_max_output_tokens: int = 8192` to `app/config.py` (env `API_MAX_OUTPUT_TOKENS`) — used by the **claude** SDK branch only (Anthropic requires it). Read via pydantic settings (this is a tuning knob, not an api credential — credentials stay `os.environ`).

### 4. Dependencies

Add `google-genai` and `anthropic` to `pyproject.toml`. The gemini/claude **CLIs stay required** for `transport=cli`; both coexist.

## Data flow

api spawn (content / gemini-pinned extract / claude judge) → `run_phase` builds the master prompt → `_spawn` sees `(api, gemini|claude)` → `api_transport.generate` builds the SDK client from the same credential sources → native-async SDK call → normalized `(rc, text, usage)` → `run_phase` records the usage row (`auth_mode=api`, priced via `pricing.cost_usd`) and returns `(text, pt, ot)` exactly as the CLI path did. cli jobs never enter this branch.

## Error handling

- **Missing credentials** → raise (loud) → failed spawn → job fails clearly. (The worker `has_api_keys` claim gate already prevents claiming api jobs without keys — defense-in-depth.)
- **Transient (429/5xx)** → rc=1; existing same-provider retry budget applies.
- **Auth/permission (401/403)** → message in `stderr`; existing api auth-failure path fails the job loudly (consistent with the judge rule).
- **Attachments passed** → raise (unreachable today).

## Testing / acceptance

Unit (`tests/services/test_api_transport.py`, SDK clients mocked — no network):
1. **gemini creds:** `GEMINI_API_KEY` → `api_key` client; Vertex SA (no key) → `vertexai=True` client w/ project+location; neither → raises.
2. **claude creds:** `ANTHROPIC_API_KEY` → client; missing → raises.
3. **usage mapping:** gemini (thoughts folded into output, prompt cached-inclusive); claude (disjoint cached, total = input+output); `raw` populated for both.
4. **claude cap:** `max_tokens` passed = `settings.api_max_output_tokens`.
5. non-empty `attachments` → `NotImplementedError`.
6. SDK exception → `(1, "", empty_usage, "<cause>")` (not a fake success).

Dispatch (`tests/services/test_agent.py`):
7. `_spawn(provider=gemini|claude, transport="api")` delegates to `api_transport.generate` (patched), no subprocess; `transport="cli"` still spawns the CLI; `transport="api"` with a cli-only provider (kimi/codex/opencode) still spawns the CLI.

Acceptance — **real SDK smoke** (affects generation → real call, not theory): real `api_transport.generate` calls producing complete, spec-valid phases — gemini (Vertex SA) already proven via `scripts/api_vs_cli_compare.py --only sdk`; add a claude (API key) smoke. Plus full suite green (`uv run python -m pytest tests/ -q`); FE untouched.

## Out of scope (follow-ups)

- **File/PDF attachments over the API** (Files API / inline) — text-only v1; raises if attempted.
- **Gemini output-cap / thinking-budget config** — gemini stays uncapped; config hooks deferred.
- **Removing the CLIs** — still required for `transport=cli`.
- Any pipeline / batch / frontend change — none needed (transport plumbing already exists; this swaps only the api spawn mechanism).
- **Claude cache-write pricing** (`pricing-1`) — unchanged; out of scope here.
