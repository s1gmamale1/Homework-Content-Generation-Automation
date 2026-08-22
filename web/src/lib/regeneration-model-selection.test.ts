import assert from "node:assert";
import { test } from "node:test";
import { defaultGuidedRegenerationDraft } from "./regeneration-draft";
import {
  effectiveRegenerationModels,
  regenerationLaunchContract,
  regenerationModelSelectionIssue,
} from "./regeneration-model-selection";
import type { LaunchDefaults, ProviderModelManifest } from "./types";

const DEFAULTS: LaunchDefaults = {
  content_provider: "gemini",
  content_model: "gemini-3.6-flash",
  content_transport: "cli",
  judge_provider: "gemini",
  judge_model: "gemini-3.6-flash",
  judge_transport: "inherit",
  solver_provider: "gemini",
  solver_model: "gemini-3.5-flash-lite",
  solver_transport: "cli",
  solver_boss_arena_enabled: true,
  extract_provider: "gemini",
  extract_model: "gemini-3.5-flash-lite",
  extract_transport: "inherit",
  toc_transport: "cli",
  output_language: "uz",
};

const MANIFEST: ProviderModelManifest = {
  providers: {
    gemini: ["gemini-3.6-flash", "gemini-3.5-flash-lite"],
    claude: ["claude-sonnet-4-6"],
    kimi: ["k2"],
    clodex: ["gpt-5.5"],
  },
  api_supported: { gemini: true, claude: true, kimi: false, clodex: true },
  api_only: { gemini: false, claude: false, kimi: false, clodex: true },
};

test("Settings mode freezes all four Settings pairs and never inherits their transports", () => {
  const draft = defaultGuidedRegenerationDraft();

  assert.deepStrictEqual(regenerationLaunchContract(draft, DEFAULTS), {
    provider: "gemini",
    model: "gemini-3.6-flash",
    transport: "api",
    judge_provider: "gemini",
    judge_model: "gemini-3.6-flash",
    judge_transport: "api",
    solver_provider: "gemini",
    solver_model: "gemini-3.5-flash-lite",
    solver_transport: "api",
    extract_provider: "gemini",
    extract_model: "gemini-3.5-flash-lite",
    extract_transport: "api",
    session_limit_strategy: "inherit",
  });
  assert.strictEqual(regenerationModelSelectionIssue(draft, DEFAULTS, MANIFEST), null);
});

test("Override mode ignores every Settings model pair", () => {
  const draft = {
    ...defaultGuidedRegenerationDraft(),
    modelSelectionMode: "override" as const,
    provider: "claude",
    model: "claude-sonnet-4-6",
    judgeProvider: "gemini",
    judgeModel: "gemini-3.5-flash-lite",
    solverProvider: "claude",
    solverModel: "claude-sonnet-4-6",
    extractProvider: "gemini",
    extractModel: "gemini-3.6-flash",
  };

  assert.deepStrictEqual(effectiveRegenerationModels(draft, DEFAULTS), {
    content: { provider: "claude", model: "claude-sonnet-4-6" },
    judge: { provider: "gemini", model: "gemini-3.5-flash-lite" },
    solver: { provider: "claude", model: "claude-sonnet-4-6" },
    extract: { provider: "gemini", model: "gemini-3.6-flash" },
  });
  assert.strictEqual(regenerationModelSelectionIssue(draft, DEFAULTS, MANIFEST), null);
});

test("a missing Settings role blocks spending with an actionable role name", () => {
  const defaults = { ...DEFAULTS, judge_model: null };

  assert.strictEqual(
    regenerationModelSelectionIssue(defaultGuidedRegenerationDraft(), defaults, MANIFEST),
    "Judge model is not configured in Settings.",
  );
  assert.strictEqual(regenerationLaunchContract(defaultGuidedRegenerationDraft(), defaults), null);
});

test("a retired override model is refused before campaign creation", () => {
  const draft = {
    ...defaultGuidedRegenerationDraft(),
    modelSelectionMode: "override" as const,
    provider: "gemini",
    model: "retired-flash",
    judgeProvider: "gemini",
    judgeModel: "gemini-3.6-flash",
    solverProvider: "gemini",
    solverModel: "gemini-3.5-flash-lite",
    extractProvider: "gemini",
    extractModel: "gemini-3.5-flash-lite",
  };

  assert.strictEqual(
    regenerationModelSelectionIssue(draft, DEFAULTS, MANIFEST),
    "Content model retired-flash is no longer available for gemini.",
  );
});

test("a provider without API support cannot be selected", () => {
  const draft = {
    ...defaultGuidedRegenerationDraft(),
    modelSelectionMode: "override" as const,
    provider: "kimi",
    model: "k2",
  };

  assert.strictEqual(
    regenerationModelSelectionIssue(draft, DEFAULTS, MANIFEST),
    "Content provider kimi cannot run regeneration through the API.",
  );
});

test("Extract rejects an API-only provider that lacks extraction fallbacks", () => {
  const draft = {
    ...defaultGuidedRegenerationDraft(),
    modelSelectionMode: "override" as const,
    provider: "gemini",
    model: "gemini-3.6-flash",
    judgeProvider: "gemini",
    judgeModel: "gemini-3.6-flash",
    solverProvider: "gemini",
    solverModel: "gemini-3.5-flash-lite",
    extractProvider: "clodex",
    extractModel: "gpt-5.5",
  };

  assert.strictEqual(
    regenerationModelSelectionIssue(draft, DEFAULTS, MANIFEST),
    "Extract provider clodex cannot run extraction safely.",
  );
});
