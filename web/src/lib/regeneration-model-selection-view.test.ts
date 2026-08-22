import assert from "node:assert";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { RegenerationModelSelection } from "../components/regeneration/model-selection";
import { ContentStep } from "../components/regeneration/content-step";
import { RegenerationWizard } from "../components/regeneration/regeneration-wizard";
import { defaultGuidedRegenerationDraft } from "./regeneration-draft";
import type { LaunchDefaults, ProviderModelManifest } from "./types";

const defaults: LaunchDefaults = {
  content_provider: "gemini",
  content_model: "gemini-3.6-flash",
  content_transport: "api",
  judge_provider: "gemini",
  judge_model: "gemini-3.6-flash",
  judge_transport: "api",
  solver_provider: "gemini",
  solver_model: "gemini-3.5-flash-lite",
  solver_transport: "api",
  solver_boss_arena_enabled: true,
  extract_provider: "gemini",
  extract_model: "gemini-3.5-flash-lite",
  extract_transport: "api",
  toc_transport: "api",
  output_language: "uz",
};

const manifest: ProviderModelManifest = {
  providers: {
    gemini: ["gemini-3.6-flash", "gemini-3.5-flash-lite"],
    claude: ["claude-sonnet-4-6"],
  },
  api_supported: { gemini: true, claude: true },
  api_only: { gemini: false, claude: false },
};

function render(draft = defaultGuidedRegenerationDraft()) {
  return renderToStaticMarkup(
    createElement(RegenerationModelSelection, {
      draft,
      defaults,
      manifest,
      onChange: () => undefined,
    }),
  );
}

test("Settings mode presents the four live defaults as read-only choices", () => {
  const html = render();

  assert.match(html, /Use Settings defaults/);
  assert.match(html, /Override models/);
  assert.match(html, /href="\/settings"/);
  for (const role of ["Content", "Judge", "Solver", "Extract"]) {
    assert.match(html, new RegExp(`>${role}<`), `${role} summary is missing`);
    assert.doesNotMatch(html, new RegExp(`aria-label="${role} provider"`));
  }
  assert.match(html, /gemini-3\.6-flash/);
  assert.match(html, /gemini-3\.5-flash-lite/);
});

test("Override mode renders an editable provider and model control for every role", () => {
  const draft = {
    ...defaultGuidedRegenerationDraft(),
    modelSelectionMode: "override" as const,
    provider: "gemini",
    model: "gemini-3.6-flash",
    judgeProvider: "gemini",
    judgeModel: "gemini-3.6-flash",
    solverProvider: "gemini",
    solverModel: "gemini-3.5-flash-lite",
    extractProvider: null,
    extractModel: null,
  };
  const html = render(draft);

  for (const role of ["Content", "Judge", "Solver", "Extract"]) {
    assert.match(html, new RegExp(`aria-label="${role} provider"`));
    assert.match(html, new RegExp(`aria-label="${role} model"`));
  }
  assert.match(html, /Extract provider is not configured/);
  assert.doesNotMatch(html, /href="\/settings"/);
});

test("the regeneration Content step owns the two-mode model selector", () => {
  const html = renderToStaticMarkup(
    createElement(ContentStep, {
      draft: defaultGuidedRegenerationDraft(),
      canonicalPhases: ["extract", "reflection"],
      plan: null,
      manifest,
      launchDefaults: defaults,
      planLoading: false,
      planErrorView: null,
      error: null,
      onChange: () => undefined,
      onBack: () => undefined,
      onContinue: () => undefined,
    }),
  );

  assert.match(html, /Use Settings defaults/);
  assert.match(html, /Override models/);
  assert.doesNotMatch(html, />Provider</);
});

test("the wizard passes the live Settings defaults into its Content step", () => {
  const html = renderToStaticMarkup(
    createElement(RegenerationWizard, {
      books: [],
      booksLoading: false,
      booksError: null,
      sources: [],
      ineligible: [],
      sourcesLoading: false,
      sourcesError: null,
      pickBookReason: null,
      phaseCatalog: ["extract", "reflection"],
      plan: null,
      planLoading: false,
      planError: null,
      estimate: null,
      estimateError: null,
      destinations: null,
      destinationsChecking: false,
      destinationError: null,
      onCheckDestinations: () => undefined,
      onChooseDestination: () => undefined,
      manifest,
      launchDefaults: defaults,
      manifestError: null,
      state: { ...defaultGuidedRegenerationDraft(), step: "content" as const },
      draftWarning: null,
      onChange: () => undefined,
      onDiscard: () => undefined,
      onCreateAndStart: () => undefined,
      starting: false,
      createError: null,
      onOpenCampaign: () => undefined,
    }),
  );

  assert.match(html, /gemini\/gemini-3\.6-flash/);
  assert.match(html, /gemini\/gemini-3\.5-flash-lite/);
  assert.doesNotMatch(html, /Not configured/);
});
