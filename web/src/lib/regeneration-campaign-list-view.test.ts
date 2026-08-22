import assert from "node:assert";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { CampaignList } from "../components/regeneration/campaign-list";
import type { RegenerationCampaignSummary } from "./types";

const campaign = {
  id: "11111111-1111-4111-8111-111111111111",
  status: "completed",
  is_terminal: true,
  attention_required: false,
  target_count: 1,
  status_counts: { published: 1 },
  bucket_counts: { published: 1 },
  canary_size: 1,
  refresh_extraction: false,
  exclusion_acknowledged: false,
  requested_phases: ["flashcards", "memory-check"],
  excluded_phases: [],
  subjects: ["biology"],
  grades: ["9"],
  lesson_count: 1,
  lesson_title: "Photosynthesis",
  publication_version: 3,
  publication_version_label: "Homework V3",
  app_git_revision: "abc123",
  estimated_cost_low_usd: 1,
  estimated_cost_high_usd: 2,
  canary_launched_at: null,
  approved_at: null,
  rejected_at: null,
  cancel_requested_at: null,
  completed_at: "2026-08-22T12:00:00Z",
  rejected_reason: null,
  cancel_requested_reason: null,
  created_at: "2026-08-22T10:00:00Z",
  updated_at: "2026-08-22T12:00:00Z",
} as RegenerationCampaignSummary;

const html = renderToStaticMarkup(
  React.createElement(CampaignList, {
    campaigns: [campaign],
    count: 1,
    limit: 50,
    offset: 0,
    selectedId: campaign.id,
    onSelect: () => undefined,
  }),
);

assert.match(html, /Biology · Grade 9/);
assert.match(html, /Photosynthesis/);
assert.doesNotMatch(html, /flashcards/);
