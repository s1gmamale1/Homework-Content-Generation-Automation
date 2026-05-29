# Project Wishlist — deferred / future work

> Not scheduled. Items here are intentionally deferred. Local-only (this lives under the
> gitignored `docs/memory/`). Move an item into a PR/plan when it's picked up.

---

## W1 — Add `opencode` as a 5th CLI provider — ✅ DONE 2026-05-29 (commit 8a96435)

**Status:** IMPLEMENTED (user reinstated; harvested from PR #1 / molotov). See [[MASTER_MEMORY]] §0010.
`app/services/providers/opencode.py` + registered in PROVIDERS + MODEL_MANIFEST (zen models)
+ `_PROVIDER_DEFAULT_MODEL["opencode"]="opencode/deepseek-v4-flash-free"`. 10 unit tests green.

**⚠️ KEY UNVERIFIED RISK — stdin vs positional prompt.** My research below suspected the
prompt is a POSITIONAL arg and opencode might NOT read piped stdin. **molotov's implementation
uses stdin** (`run` with no positional message, prompt piped) and reportedly verified it against
real generation in PR #1. opencode is NOT installed in this env, so we could not independently
confirm. **First action when opencode is installed: run one real generation.** If it HANGS
(the anomalyco/opencode#11891 failure mode), the fix is the argv-positional prompt routing
described under "To implement" below (the driver always pipes stdin today). Token usage may
report zeros like kimi. Extract pin left on gemini; opencode-as-extractor via EXTRACT_PROVIDER/
EXTRACT_MODEL.

**Goal:** Add `opencode` to the CLI-subprocess router alongside `claude / codex / gemini / kimi`.

**Why deferred:** opencode is not installed on this machine, and its CLI contract is
version-specific — couldn't build/verify it accurately yet.

**Research already done (so we don't repeat it):**
- "opencode" is several projects with DIFFERENT flags:
  - `sst/opencode` (opencode.ai, current): `opencode run "<msg>" --format json`
  - older/forks (`opencode-ai/opencode`, `anomalyco/opencode`): `opencode -p "<msg>" -f json`
- Headless flag: `--dangerously-skip-permissions`; quiet: `-q/--quiet`. Model: `-m/--model provider/model` (e.g. `anthropic/claude-3-5-sonnet`). Attachments: `-f/--file`.
- **Prompt is a POSITIONAL arg, not stdin.** Our driver (`app/services/agent.py::_spawn`)
  always pipes the prompt to stdin and `Provider.build_argv()` never receives the prompt —
  so opencode needs EITHER confirmation it reads piped stdin OR a small additive router tweak
  to pass the prompt as an argv positional.
- **Token usage likely NOT reported** in headless mode (open FR sst/opencode#3307) → would
  report zeros like the `kimi` provider.
- Known subprocess HANG with JSON mode (anomalyco/opencode#11891) — exactly the failure our
  stdin/positional mismatch would cause if prompt routing is wrong.
- Sources: opencode.ai/docs/cli, github sst/opencode#3307, github anomalyco/opencode#11891.

**To implement (when resumed):**
1. Get from user: `opencode --version`; a RAW `--format json` sample of one headless run
   (paste full stdout — gives the exact event schema for `parse_envelope`); desired model
   identifier(s) for `MODEL_MANIFEST`; confirm opencode is installed on the runtime/deploy host.
2. Decide prompt routing: confirm stdin works, else make the minimal additive Provider/driver
   change to allow an argv-positional prompt (existing 4 stdin providers must stay unaffected).
3. Files: new `app/services/providers/opencode.py`; register in `providers/__init__.py`
   (`PROVIDERS`, `__all__`); add `MODEL_MANIFEST["opencode"]` + `_PROVIDER_DEFAULT_MODEL["opencode"]`
   in `app/services/agent.py`; tests in `tests/services/test_providers.py` (parse the real sample);
   optional `config.py` rate-limit settings + web model-picker (`web/src/lib/types.ts`).
4. Build TDD against the real sample (how the other 4 providers were derived).
