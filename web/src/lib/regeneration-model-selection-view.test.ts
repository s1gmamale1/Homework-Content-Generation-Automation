import assert from "node:assert";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ContentStep } from "../components/regeneration/content-step";
import { RegenerationModelSelection } from "../components/regeneration/model-selection";
import { RegenerationWizard } from "../components/regeneration/regeneration-wizard";
import { ReviewStep } from "../components/regeneration/review-step";
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

test("valid Settings defaults make Review reachable without a draft content override", () => {
  const plan = {
    subject: "math",
    canonical_phases: ["extract", "reflection"],
    selected_phases: ["reflection"],
    auto_included_phases: [],
    regenerated_phases: ["reflection"],
    copied_phases: [],
    excluded_affected_phases: [],
    broken_dependency_edges: [],
    refresh_extraction: false,
    regenerated_phase_count: 1,
    copied_phase_count: 0,
    acknowledgement_required: false,
    acknowledgement_message: null,
  };
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
      phaseCatalog: plan.canonical_phases,
      plan,
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
      state: {
        ...defaultGuidedRegenerationDraft(),
        step: "content" as const,
        selectedTocEntryIds: ["lesson-1"],
      },
      draftWarning: null,
      onChange: () => undefined,
      onDiscard: () => undefined,
      onCreateAndStart: () => undefined,
      starting: false,
      createError: null,
      onOpenCampaign: () => undefined,
    }),
  );
  const reviewLabel = html.indexOf(">Review</span>");
  const reviewButton = html.slice(html.lastIndexOf("<button", reviewLabel), reviewLabel);

  assert.ok(reviewLabel > 0, "Review step label is missing");
  assert.doesNotMatch(reviewButton, /disabled/);
});

test("the review step names the source and all four frozen model choices", () => {
  const html = renderToStaticMarkup(
    createElement(ReviewStep, {
      draft: defaultGuidedRegenerationDraft(),
      launchDefaults: defaults,
      estimate: null,
      destinations: null,
      checking: false,
      starting: false,
      error: null,
      onBack: () => undefined,
      onCheckDestinations: () => undefined,
      onChooseDestination: () => undefined,
      onChange: () => undefined,
      onStart: () => undefined,
    }),
  );

  assert.match(html, /Model source/);
  assert.match(html, /Settings defaults/);
  for (const role of ["Content model", "Judge model", "Solver model", "Extract model"]) {
    assert.match(html, new RegExp(role));
  }
  assert.match(html, /gemini\/gemini-3\.6-flash/);
  assert.match(html, /gemini\/gemini-3\.5-flash-lite/);
  assert.doesNotMatch(html, /not selected/);
});
