/**
 * Serialization tests for the versioned-regeneration API client (Task 10).
 *
 * `npm test` runs `node --import tsx --test src/lib/*.test.ts`: no DOM, no
 * React renderer, no TanStack Query. What IS testable here — and what this file
 * covers — is the wire contract: for every one of the 13 Task 9 endpoints, the
 * exact method, URL, query serialization and request body, plus the structured
 * error shapes the router emits.
 *
 * Two contract facts drive most of the assertions.
 *
 * **`/estimate` and `/campaigns` take DIFFERENT bodies.** Both request models
 * inherit `_Strict` (`extra="forbid"`), so posting a create-shaped draft to
 * `/estimate` is a 422, not a tolerated superset. The client therefore strips
 * the create-only keys (`actor`, `notes`, `estimated_cost_*`,
 * `app_git_revision`) rather than sending one body to both routes.
 *
 * **`detail` is the structured payload, not decoration.** The router raises
 * `HTTPException(409, {"error": ..., "message": ..., ...})`, so a preflight
 * failure carries every blocked lesson and a retired-model refusal carries
 * every offending role. `ApiError.detail` must preserve it intact — rendering
 * only `message` throws away the list an operator has to act on.
 */
import assert from "node:assert";
import {
  ApiError,
  api,
  regenerationCampaignBody,
  regenerationEligibleQuery,
  regenerationErrorView,
  regenerationEstimateBody,
} from "./api";
import type { RegenerationCampaignDraft, RegenerationLaunchContract } from "./types";

/* ────────────────────────────────────────────────────────────────────
 * fetch capture
 * ──────────────────────────────────────────────────────────────────── */

interface Recorded {
  url: string;
  method: string;
  headers: Headers;
  body: unknown;
}

const calls: Recorded[] = [];
let nextStatus = 200;
let nextBody: unknown = {};

globalThis.fetch = (async (input: unknown, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  calls.push({
    url: String(input),
    method: init.method ?? "GET",
    headers,
    body: typeof init.body === "string" ? JSON.parse(init.body) : init.body,
  });
  return new Response(JSON.stringify(nextBody), {
    status: nextStatus,
    headers: { "Content-Type": "application/json" },
  });
}) as unknown as typeof fetch;

/** Run one client call against a canned response and return what went out. */
async function sent(fn: () => Promise<unknown>, body: unknown = {}): Promise<Recorded> {
  calls.length = 0;
  nextStatus = 200;
  nextBody = body;
  await fn();
  assert.strictEqual(calls.length, 1, "expected exactly one HTTP call");
  return calls[0];
}

/** Run one client call that is expected to fail, and return the ApiError. */
async function failed(
  fn: () => Promise<unknown>,
  status: number,
  body: unknown,
): Promise<ApiError> {
  calls.length = 0;
  nextStatus = status;
  nextBody = body;
  try {
    await fn();
  } catch (err) {
    assert.ok(err instanceof ApiError, `expected ApiError, got ${String(err)}`);
    return err;
  }
  throw new Error("expected the call to reject");
}

function path(url: string): string {
  return url.split("?")[0];
}

function query(url: string): URLSearchParams {
  return new URLSearchParams(url.split("?")[1] ?? "");
}

/* ────────────────────────────────────────────────────────────────────
 * Fixtures — real UUID-shaped ids, because the routes take UUID params.
 * ──────────────────────────────────────────────────────────────────── */

const CAMPAIGN_ID = "6f1c1d4e-9a2b-4c3d-8e5f-0a1b2c3d4e5f";
const TARGET_ID = "11112222-3333-4444-5555-666677778888";
const BOOK_ID = "aaaabbbb-cccc-dddd-eeee-ffff00001111";
const TOC_ID = "99998888-7777-6666-5555-444433332222";

const CONTRACT: RegenerationLaunchContract = {
  provider: "gemini",
  model: "gemini-3.5-flash",
  transport: "api",
  extract_transport: "inherit",
  extract_provider: null,
  extract_model: null,
  judge_transport: "inherit",
  judge_provider: null,
  judge_model: null,
  solver_transport: "inherit",
  solver_provider: null,
  solver_model: null,
  session_limit_strategy: "inherit",
};

const DRAFT: RegenerationCampaignDraft = {
  publication_version: 3,
  selection: {
    book_ids: [BOOK_ID],
    toc_entry_ids: [TOC_ID],
    output_languages: ["uz"],
  },
  contract: CONTRACT,
  selected_phases: ["flashcards"],
  excluded_affected_phases: ["reflection"],
  refresh_extraction: false,
  exclusion_acknowledged: true,
  canary_size: 2,
  destination_overrides: [],
  approved_destination_digest: "a".repeat(64),
  estimated_cost_low_usd: 1.25,
  estimated_cost_high_usd: 3.5,
  app_git_revision: "7209a4e",
  actor: "operator@example.com",
  notes: { why: "flashcard prompt refresh" },
};

/** Keys `EstimateRequest` does not declare; `extra="forbid"` 422s on each. */
const CREATE_ONLY_KEYS = [
  "destination_overrides",
  "approved_destination_digest",
  "estimated_cost_low_usd",
  "estimated_cost_high_usd",
  "app_git_revision",
  "actor",
  "notes",
];

/* ════════════════════════════════════════════════════════════════════
 * 1. GET /regeneration/eligible
 * ════════════════════════════════════════════════════════════════════ */

{
  const call = await sent(
    () =>
      api.listRegenerationEligible({
        bookIds: [BOOK_ID],
        tocEntryIds: [TOC_ID, TOC_ID],
        outputLanguages: ["uz", "ru"],
      }),
    { sources: [], ineligible: [], eligible_count: 0, ineligible_count: 0 },
  );
  assert.strictEqual(call.method, "GET");
  assert.strictEqual(path(call.url), "/api/v1/regeneration/eligible");
  const q = query(call.url);
  // Repeated params, not comma-joined: the route reads `Query(default=[])`.
  assert.deepStrictEqual(q.getAll("book_id"), [BOOK_ID]);
  assert.deepStrictEqual(q.getAll("toc_entry_id"), [TOC_ID, TOC_ID]);
  assert.deepStrictEqual(q.getAll("output_language"), ["uz", "ru"]);
  assert.strictEqual(call.body, undefined, "a GET must not carry a body");
}

{
  // No filters at all — the route's own "do not filter on this axis" default.
  const call = await sent(() => api.listRegenerationEligible(), {
    sources: [],
    ineligible: [],
    eligible_count: 0,
    ineligible_count: 0,
  });
  assert.strictEqual(call.url, "/api/v1/regeneration/eligible");
}

/* ════════════════════════════════════════════════════════════════════
 * 2. POST /regeneration/phase-plan
 * ════════════════════════════════════════════════════════════════════ */

{
  const call = await sent(() =>
    api.previewRegenerationPhasePlan({
      subject: "biology",
      selected_phases: ["flashcards"],
      excluded_affected_phases: ["reflection"],
      refresh_extraction: false,
      exclusion_acknowledged: false,
    }),
  );
  assert.strictEqual(call.method, "POST");
  assert.strictEqual(call.url, "/api/v1/regeneration/phase-plan");
  assert.strictEqual(call.headers.get("Content-Type"), "application/json");
  assert.deepStrictEqual(call.body, {
    subject: "biology",
    selected_phases: ["flashcards"],
    excluded_affected_phases: ["reflection"],
    refresh_extraction: false,
    exclusion_acknowledged: false,
  });
}

/* ════════════════════════════════════════════════════════════════════
 * 3. POST /regeneration/estimate — create-only keys stripped
 * ════════════════════════════════════════════════════════════════════ */

{
  const call = await sent(() => api.estimateRegeneration(DRAFT), {
    target_count: 0,
    canary_size: 1,
    acknowledgement_required: false,
    sources: [],
    ineligible: [],
    phase_plans: [],
    estimate: null,
    preflight: { ok: true, failure_count: 0, failures: [] },
  });
  assert.strictEqual(call.method, "POST");
  assert.strictEqual(call.url, "/api/v1/regeneration/estimate");
  const body = call.body as Record<string, unknown>;
  for (const key of CREATE_ONLY_KEYS) {
    assert.ok(
      !(key in body),
      `/estimate must not send create-only key ${key}: EstimateRequest forbids extras`,
    );
  }
  assert.deepStrictEqual(body, {
    publication_version: 3,
    selection: DRAFT.selection,
    contract: CONTRACT,
    selected_phases: ["flashcards"],
    excluded_affected_phases: ["reflection"],
    refresh_extraction: false,
    exclusion_acknowledged: true,
    canary_size: 2,
  });
}

// The pure builder is the thing the wizard reuses; assert it directly too.
{
  const body = regenerationEstimateBody(DRAFT);
  for (const key of CREATE_ONLY_KEYS) {
    assert.ok(!(key in (body as Record<string, unknown>)), `estimate body leaked ${key}`);
  }
  assert.strictEqual(body.canary_size, 2);
  assert.strictEqual(body.contract.transport, "api");
}

/* ════════════════════════════════════════════════════════════════════
 * 4. POST /regeneration/destinations — explicit read-only Notion review
 * ════════════════════════════════════════════════════════════════════ */

{
  const request = {
    publication_version: 3,
    selection: DRAFT.selection,
    destination_overrides: [],
  };
  const call = await sent(
    () => api.checkRegenerationDestinations(request),
    {
      ok: true,
      target_count: 1,
      checked_target_count: 1,
      destination_digest: "a".repeat(64),
      destinations: [],
    },
  );
  assert.strictEqual(call.method, "POST");
  assert.strictEqual(call.url, "/api/v1/regeneration/destinations");
  assert.deepStrictEqual(call.body, request);
}

/* ════════════════════════════════════════════════════════════════════
 * 5. POST /regeneration/campaigns — full draft, including reviewed destination
 * ════════════════════════════════════════════════════════════════════ */

{
  const call = await sent(() => api.createRegenerationCampaign(DRAFT), { id: CAMPAIGN_ID });
  assert.strictEqual(call.method, "POST");
  assert.strictEqual(call.url, "/api/v1/regeneration/campaigns");
  const body = call.body as Record<string, unknown>;
  for (const key of CREATE_ONLY_KEYS) {
    assert.ok(key in body, `/campaigns must send ${key}`);
  }
  // The operator approves the number they were SHOWN — it is echoed back.
  assert.strictEqual(body.estimated_cost_low_usd, 1.25);
  assert.strictEqual(body.estimated_cost_high_usd, 3.5);
  assert.strictEqual(body.app_git_revision, "7209a4e");
  assert.deepStrictEqual(body.notes, { why: "flashcard prompt refresh" });
  assert.strictEqual(body.publication_version, 3);
  assert.strictEqual(body.approved_destination_digest, "a".repeat(64));
  assert.deepStrictEqual(body.destination_overrides, []);
  assert.deepStrictEqual(regenerationCampaignBody(DRAFT), body);
}

/* ════════════════════════════════════════════════════════════════════
 * 5. GET /regeneration/campaigns (list)
 * ════════════════════════════════════════════════════════════════════ */

{
  const call = await sent(
    () =>
      api.listRegenerationCampaigns({
        statuses: ["canary_running", "attention_required"],
        limit: 25,
        offset: 50,
      }),
    { campaigns: [], count: 0, limit: 25, offset: 50 },
  );
  assert.strictEqual(call.method, "GET");
  assert.strictEqual(path(call.url), "/api/v1/regeneration/campaigns");
  const q = query(call.url);
  // `status`, not `status_filter`: the route declares `alias="status"`.
  assert.deepStrictEqual(q.getAll("status"), ["canary_running", "attention_required"]);
  assert.strictEqual(q.get("limit"), "25");
  assert.strictEqual(q.get("offset"), "50");
}

{
  const call = await sent(() => api.listRegenerationCampaigns(), {
    campaigns: [],
    count: 0,
    limit: 50,
    offset: 0,
  });
  assert.strictEqual(call.url, "/api/v1/regeneration/campaigns");
}

/* ════════════════════════════════════════════════════════════════════
 * 6. GET /regeneration/campaigns/{id}
 * ════════════════════════════════════════════════════════════════════ */

{
  const call = await sent(() => api.getRegenerationCampaign(CAMPAIGN_ID), { id: CAMPAIGN_ID });
  assert.strictEqual(call.method, "GET");
  assert.strictEqual(call.url, `/api/v1/regeneration/campaigns/${CAMPAIGN_ID}`);
}

/* ════════════════════════════════════════════════════════════════════
 * 7-10. Campaign mutations
 * ════════════════════════════════════════════════════════════════════ */

{
  const call = await sent(() => api.launchRegenerationCanary(CAMPAIGN_ID), { id: CAMPAIGN_ID });
  assert.strictEqual(call.method, "POST");
  assert.strictEqual(call.url, `/api/v1/regeneration/campaigns/${CAMPAIGN_ID}/canary`);
  // The route declares no body parameter; sending one is noise on the wire.
  assert.strictEqual(call.body, undefined);
}

{
  const call = await sent(() => api.approveRegenerationCampaign(CAMPAIGN_ID, { actor: "ops" }), {
    id: CAMPAIGN_ID,
  });
  assert.strictEqual(call.method, "POST");
  assert.strictEqual(call.url, `/api/v1/regeneration/campaigns/${CAMPAIGN_ID}/approve`);
  assert.deepStrictEqual(call.body, { actor: "ops" });
}

{
  const call = await sent(
    () => api.rejectRegenerationCampaign(CAMPAIGN_ID, { actor: "ops", reason: "wrong lessons" }),
    { id: CAMPAIGN_ID },
  );
  assert.strictEqual(call.url, `/api/v1/regeneration/campaigns/${CAMPAIGN_ID}/reject`);
  assert.deepStrictEqual(call.body, { actor: "ops", reason: "wrong lessons" });
}

{
  const call = await sent(
    () => api.cancelRegenerationCampaign(CAMPAIGN_ID, { actor: "ops", reason: "budget" }),
    { id: CAMPAIGN_ID },
  );
  assert.strictEqual(call.url, `/api/v1/regeneration/campaigns/${CAMPAIGN_ID}/cancel`);
  assert.deepStrictEqual(call.body, { actor: "ops", reason: "budget" });
}

/* ════════════════════════════════════════════════════════════════════
 * 11-13. Target mutations
 * ════════════════════════════════════════════════════════════════════ */

{
  const call = await sent(() => api.retryRegenerationGeneration(TARGET_ID), {
    campaign_id: CAMPAIGN_ID,
  });
  assert.strictEqual(call.method, "POST");
  assert.strictEqual(call.url, `/api/v1/regeneration/targets/${TARGET_ID}/retry-generation`);
  assert.strictEqual(call.body, undefined);
}

{
  const call = await sent(() => api.retryRegenerationPublication(TARGET_ID), {
    campaign_id: CAMPAIGN_ID,
  });
  assert.strictEqual(call.url, `/api/v1/regeneration/targets/${TARGET_ID}/retry-publication`);
  assert.strictEqual(call.body, undefined);
}

{
  const call = await sent(
    () => api.abandonRegenerationTarget(TARGET_ID, { actor: "ops", reason: "page collision" }),
    { campaign_id: CAMPAIGN_ID },
  );
  assert.strictEqual(call.url, `/api/v1/regeneration/targets/${TARGET_ID}/abandon`);
  assert.deepStrictEqual(call.body, { actor: "ops", reason: "page collision" });
}

/* ════════════════════════════════════════════════════════════════════
 * 14. Every endpoint is reachable and namespaced under /regeneration
 * ════════════════════════════════════════════════════════════════════ */

{
  const ENDPOINTS: [string, () => Promise<unknown>][] = [
    ["GET /eligible", () => api.listRegenerationEligible()],
    [
      "POST /phase-plan",
      () =>
        api.previewRegenerationPhasePlan({
          subject: "biology",
          selected_phases: ["reflection"],
          excluded_affected_phases: [],
          refresh_extraction: false,
          exclusion_acknowledged: false,
        }),
    ],
    ["POST /estimate", () => api.estimateRegeneration(DRAFT)],
    [
      "POST /destinations",
      () => api.checkRegenerationDestinations({
        publication_version: 3,
        selection: DRAFT.selection,
        destination_overrides: [],
      }),
    ],
    ["POST /campaigns", () => api.createRegenerationCampaign(DRAFT)],
    ["GET /campaigns", () => api.listRegenerationCampaigns()],
    ["GET /campaigns/{id}", () => api.getRegenerationCampaign(CAMPAIGN_ID)],
    ["POST /campaigns/{id}/canary", () => api.launchRegenerationCanary(CAMPAIGN_ID)],
    [
      "POST /campaigns/{id}/approve",
      () => api.approveRegenerationCampaign(CAMPAIGN_ID, { actor: "" }),
    ],
    [
      "POST /campaigns/{id}/reject",
      () => api.rejectRegenerationCampaign(CAMPAIGN_ID, { actor: "", reason: "no" }),
    ],
    [
      "POST /campaigns/{id}/cancel",
      () => api.cancelRegenerationCampaign(CAMPAIGN_ID, { actor: "", reason: "no" }),
    ],
    ["POST /targets/{id}/retry-generation", () => api.retryRegenerationGeneration(TARGET_ID)],
    ["POST /targets/{id}/retry-publication", () => api.retryRegenerationPublication(TARGET_ID)],
    [
      "POST /targets/{id}/abandon",
      () => api.abandonRegenerationTarget(TARGET_ID, { actor: "", reason: "no" }),
    ],
  ];
  assert.strictEqual(ENDPOINTS.length, 14, "Task 6 exposes exactly 14 endpoints");
  const seen = new Set<string>();
  for (const [name, run] of ENDPOINTS) {
    const call = await sent(run);
    assert.ok(
      path(call.url).startsWith("/api/v1/regeneration/"),
      `${name} must live under the regeneration namespace, got ${call.url}`,
    );
    seen.add(`${call.method} ${path(call.url)}`);
  }
  assert.strictEqual(seen.size, 14, "each endpoint must hit a distinct method+path");
}

/* ════════════════════════════════════════════════════════════════════
 * 15. Structured errors survive as `detail` and render as prose
 * ════════════════════════════════════════════════════════════════════ */

// 404 — the feature flag is off server-side. `detail` is a bare string here,
// and it is the router's flag guard: `HTTPException(404, "Not Found")`.
{
  const err = await failed(() => api.listRegenerationEligible(), 404, { detail: "Not Found" });
  assert.strictEqual(err.status, 404);
  assert.strictEqual(err.message, "Not Found");
  const view = regenerationErrorView(err);
  assert.match(view.title, /switched off|not available/i);
  assert.ok(!/404/.test(view.message), "the operator must not be shown a bare status code");
}

// 404 — a campaign or target that genuinely no longer exists. The SAME status
// with a DIFFERENT string detail: `_translate_campaign_error` raises
// `HTTPException(404, str(exc))`, i.e. the service's own sentence. Reporting
// that as "regeneration is switched off" sends an operator to the deployment
// flag over a row that was simply deleted.
{
  const missing = `regeneration campaign ${CAMPAIGN_ID} not found`;
  const err = await failed(() => api.getRegenerationCampaign(CAMPAIGN_ID), 404, {
    detail: missing,
  });
  const view = regenerationErrorView(err);
  assert.ok(
    !/switched off/i.test(view.title),
    `a deleted campaign must not read as a disabled feature: ${view.title}`,
  );
  assert.strictEqual(view.message, missing, "the server's own sentence must survive");
  assert.strictEqual(view.status, 404);
}
{
  const missing = `regeneration target ${TARGET_ID} not found`;
  const err = await failed(() => api.retryRegenerationPublication(TARGET_ID), 404, {
    detail: missing,
  });
  const view = regenerationErrorView(err);
  assert.ok(!/switched off/i.test(view.title));
  assert.strictEqual(view.message, missing);
}

// 409 preflight_blocked — every blocked lesson, in one response.
{
  const err = await failed(() => api.launchRegenerationCanary(CAMPAIGN_ID), 409, {
    detail: {
      error: "preflight_blocked",
      message: "2 lesson(s) have no Notion destination",
      count: 2,
      failures: [
        {
          toc_entry_id: TOC_ID,
          source_job_id: null,
          output_language: "uz",
          subject: "biology",
          grade: "8",
          lesson_title: "1-mavzu. Hujayra tuzilishi",
          reason: "no_lesson_topic_page",
          detail: "grade 8 biology is not mapped in NOTION_SUBJECT_PAGES",
        },
        {
          toc_entry_id: TOC_ID,
          source_job_id: null,
          output_language: "ru",
          subject: "biology",
          grade: "8",
          lesson_title: "Тема 1. Строение клетки",
          reason: "no_lesson_topic_page",
          detail: "grade 8 biology is not mapped in NOTION_SUBJECT_PAGES",
        },
      ],
    },
  });
  assert.strictEqual(err.status, 409);
  assert.strictEqual(err.message, "2 lesson(s) have no Notion destination");
  const detail = err.detail as { failures: unknown[] };
  assert.strictEqual(detail.failures.length, 2, "the blocked-lesson list must survive");
  const view = regenerationErrorView(err);
  assert.strictEqual(view.details.length, 2);
  assert.ok(view.details.some((line) => line.includes("1-mavzu. Hujayra tuzilishi")));
  assert.ok(view.details.some((line) => line.includes("NOTION_SUBJECT_PAGES")));
  assert.match(view.message, /Notion destination/);
}

// 409 retired_model — every offending role.
{
  const err = await failed(() => api.retryRegenerationGeneration(TARGET_ID), 409, {
    detail: {
      error: "retired_model",
      message: "campaign is pinned to a retired model",
      retired: [
        { role: "content", provider: "gemini", model: "gemini-2.5-flash" },
        { role: "judge", provider: "gemini", model: "gemini-2.5-pro" },
      ],
    },
  });
  const view = regenerationErrorView(err);
  assert.strictEqual(view.details.length, 2);
  assert.ok(view.details.some((line) => line.includes("gemini-2.5-flash")));
  assert.ok(view.details.some((line) => line.includes("judge")));
}

// 409 publisher_disabled — approve/retry-publication only.
{
  const err = await failed(() => api.approveRegenerationCampaign(CAMPAIGN_ID, { actor: "" }), 409, {
    detail: {
      error: "publisher_disabled",
      message:
        "automatic publication is unavailable: REGENERATION_PUBLISHER_ENABLED is off, so no " +
        "publication loop is running and this action would queue delivery work nobody serves. " +
        "Enable the publisher first.",
    },
  });
  const view = regenerationErrorView(err);
  assert.match(view.title, /publish/i);
  assert.match(view.message, /REGENERATION_PUBLISHER_ENABLED/);
}

// 409 active_lineage_conflict — the lessons another campaign still holds.
{
  const err = await failed(() => api.createRegenerationCampaign(DRAFT), 409, {
    detail: {
      error: "active_lineage_conflict",
      message: "1 lesson already has a live regeneration target",
      count: 1,
      lineages: [{ toc_entry_id: TOC_ID, output_language: "uz" }],
    },
  });
  const view = regenerationErrorView(err);
  assert.strictEqual(view.details.length, 1);
  assert.ok(view.details[0].includes("uz"));
}

// 409 no_eligible_targets — with the per-lineage reasons.
{
  const err = await failed(() => api.createRegenerationCampaign(DRAFT), 409, {
    detail: {
      error: "no_eligible_targets",
      message: "no selected lesson can be regenerated",
      candidates: [
        {
          toc_entry_id: TOC_ID,
          output_language: "uz",
          reasons: ["source_job_incomplete"],
          detail: "the newest job has 9 of 12 phases",
        },
      ],
    },
  });
  const view = regenerationErrorView(err);
  assert.strictEqual(view.details.length, 1);
  assert.ok(view.details[0].includes("9 of 12 phases"));
}

// 422 non_api_transport — a draft the operator just submitted.
{
  const err = await failed(() => api.estimateRegeneration(DRAFT), 422, {
    detail: {
      error: "non_api_transport",
      message: "regeneration runs over transport=api only",
      offenders: [
        { field: "transport", transport: "cli" },
        { field: "judge_transport", transport: "cli" },
      ],
    },
  });
  assert.strictEqual(err.status, 422);
  const view = regenerationErrorView(err);
  assert.strictEqual(view.details.length, 2);
  assert.ok(view.details.some((line) => line.includes("judge_transport")));
}

// 422 exclusion_acknowledgement_required — the acknowledgement tick box.
{
  const err = await failed(() => api.createRegenerationCampaign(DRAFT), 422, {
    detail: {
      error: "exclusion_acknowledgement_required",
      message:
        "excluding these phases leaves them authored against an older upstream output — the " +
        "resulting homework may be internally inconsistent; re-submit with " +
        "exclusion_acknowledged=true to confirm",
    },
  });
  const view = regenerationErrorView(err);
  assert.match(view.title, /acknowledge/i);
  assert.ok(
    !/exclusion_acknowledged=true/.test(view.message),
    "the operator is shown the tick box, not a request field",
  );
}

// 409 illegal_campaign_state — the campaign moved under the open tab.
{
  const err = await failed(() => api.approveRegenerationCampaign(CAMPAIGN_ID, { actor: "" }), 409, {
    detail: {
      error: "illegal_campaign_state",
      message: "campaign is 'rejected'; approve requires 'awaiting_canary_approval'",
    },
  });
  const view = regenerationErrorView(err);
  assert.match(view.message, /rejected/);
  assert.match(view.hint ?? "", /refresh|reload|up to date|moved on/i);
}

// Scope and canary refusals carry operator-specific recovery, not a generic
// stale-screen hint.
for (const row of [
  {
    status: 422,
    detail: {
      error: "unbounded_selection",
      message: "selection must include a book_id or toc_entry_id",
    },
    title: /book|lesson/i,
    hint: /choose|select/i,
  },
  {
    status: 422,
    detail: {
      error: "selection_too_large",
      message: "selection contains 501 eligible targets",
      count: 501,
      maximum: 500,
    },
    title: /too many/i,
    hint: /split|narrow/i,
  },
  {
    status: 422,
    detail: {
      error: "selection_discovery_too_large",
      message: "selection resolves to at least 1001 candidates",
      count_at_least: 1001,
      maximum: 1000,
    },
    title: /too broad/i,
    hint: /book|lesson/i,
  },
  {
    status: 409,
    detail: {
      error: "canary_not_reviewable",
      message: "one canary failed",
      blockers: ["generation_failed"],
      canary_count: 2,
      reason_code: "blocked",
      remedy: "Retry or abandon the failed canary in the campaign report.",
    },
    title: /canary/i,
    hint: /retry or abandon/i,
  },
  {
    status: 409,
    detail: {
      error: "notion_unavailable",
      message: "Notion publication is unavailable: NOTION_ENABLED is off",
    },
    title: /notion/i,
    hint: /configure|credential|enable/i,
  },
] as const) {
  const view = regenerationErrorView(new ApiError(row.status, row.detail.message, row.detail));
  assert.match(view.title, row.title);
  assert.match(view.hint ?? "", row.hint);
}

// A non-JSON body must not crash the renderer.
{
  const err = new ApiError(500, "Internal Server Error");
  const view = regenerationErrorView(err);
  assert.strictEqual(view.details.length, 0);
  assert.ok(view.message.length > 0);
}

// A thrown non-ApiError (network drop) still renders.
{
  const view = regenerationErrorView(new TypeError("Failed to fetch"));
  assert.ok(view.message.includes("Failed to fetch"));
  assert.strictEqual(view.details.length, 0);
}

/* ════════════════════════════════════════════════════════════════════
 * 16. /eligible is BOUNDED: one selected book, never the whole database
 *
 * `GET /eligible` with no filters walks every completed homework lineage
 * there is. The page therefore picks a book first — off the ~246-row books
 * list — and only then asks for that book's lessons.
 * ════════════════════════════════════════════════════════════════════ */

{
  const gate = regenerationEligibleQuery(null);
  assert.strictEqual(gate.enabled, false, "no book picked ⇒ the query must not run");
  assert.deepStrictEqual(gate.filters.bookIds, []);
  assert.ok((gate.blockedReason ?? "").length > 0, "the operator is told what to do first");

  const ready = regenerationEligibleQuery(BOOK_ID);
  assert.strictEqual(ready.enabled, true);
  assert.deepStrictEqual(ready.filters.bookIds, [BOOK_ID]);

  // The gate's filters go straight to the client, and serialize as repeated
  // `book_id` params — the name the route's `Query(default=[])` declares.
  const call = await sent(() => api.listRegenerationEligible(ready.filters), {
    sources: [],
    ineligible: [],
    eligible_count: 0,
    ineligible_count: 0,
  });
  assert.strictEqual(path(call.url), "/api/v1/regeneration/eligible");
  const q = query(call.url);
  assert.deepStrictEqual(q.getAll("book_id"), [BOOK_ID]);
  assert.deepStrictEqual(q.getAll("toc_entry_id"), [], "the book is the only bound needed");
  assert.deepStrictEqual(q.getAll("output_language"), []);
}

/* ════════════════════════════════════════════════════════════════════
 * 17. A FastAPI validation error is a LIST, and must read as plain language
 *
 * A schema-level 422 does not carry `{error, message}` — `detail` is
 * `[{loc, msg, type}, ...]`. `extractErrorMessage` cannot find a `message`
 * there, so it falls back to the RAW BODY TEXT: without special handling the
 * operator is shown a JSON blob.
 * ════════════════════════════════════════════════════════════════════ */

{
  const err = await failed(() => api.createRegenerationCampaign(DRAFT), 422, {
    detail: [
      {
        type: "greater_than_equal",
        loc: ["body", "canary_size"],
        msg: "Input should be greater than or equal to 1",
        input: 0,
      },
      {
        type: "missing",
        loc: ["body", "contract", "model"],
        msg: "Field required",
      },
    ],
  });
  assert.strictEqual(err.status, 422);
  const view = regenerationErrorView(err);
  assert.ok(!/[{[]"?(detail|loc|msg)/.test(view.message), "raw JSON must never reach the screen");
  assert.ok(!view.message.includes('"loc"'), `raw payload leaked: ${view.message}`);
  assert.ok(!/^\s*[[{]/.test(view.message), "the message must be a sentence, not a payload");
  assert.strictEqual(view.details.length, 2, "one plain-language line per rejected field");
  assert.ok(
    view.details.some((line) => /canary size/i.test(line)),
    `the offending field must be named in words: ${view.details.join(" | ")}`,
  );
  assert.ok(view.details.some((line) => /model/i.test(line)));
  assert.ok(
    view.details.every((line) => !line.includes("[") && !line.includes("{")),
    "each line is prose, not a serialized loc tuple",
  );
  assert.ok(!/422/.test(view.message), "the operator must not be shown a bare status code");
}

{
  // A single-item list, and an empty one, must both survive.
  const err = await failed(() => api.estimateRegeneration(DRAFT), 422, {
    detail: [
      { type: "int_parsing", loc: ["query", "limit"], msg: "Input should be a valid integer" },
    ],
  });
  const view = regenerationErrorView(err);
  assert.strictEqual(view.details.length, 1);
  assert.ok(view.details[0].length > 0);

  const empty = await failed(() => api.estimateRegeneration(DRAFT), 422, { detail: [] });
  const emptyView = regenerationErrorView(empty);
  assert.strictEqual(emptyView.details.length, 0);
  assert.ok(emptyView.message.length > 0, "an empty list still needs something to read");
  assert.ok(!/^\s*\[/.test(emptyView.message));
}

/* ════════════════════════════════════════════════════════════════════
 * 18. GET /books is read COMPLETE for the guided picker, or refuses
 *
 * `list_books(limit: int = 100, offset: int = 0)` answers the first 100 rows
 * to a caller that names no limit, so `api.listBooks()` hides most of a
 * ~246-book library behind a page nobody asked for and the guided picker
 * silently cannot reach the missing books. Paging would walk an offset window
 * that moves under an upload landing mid-walk; ONE over-sized statement
 * cannot. The extra row is the tripwire: a library that outgrew the picker has
 * to say so rather than hand back a quietly truncated list.
 * ════════════════════════════════════════════════════════════════════ */

/** `Book`-shaped enough for the client, which does no shape validation. */
function books(count: number): unknown[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `book-${i}`,
    subject: "biology",
    grade: "6",
    original_filename: `book-${i}.pdf`,
    source_language: "uz",
    status: "toc_ready",
    error_message: null,
    gemini_file_expires_at: null,
    file_size_bytes: 1024,
    created_at: "2026-08-21T00:00:00Z",
    toc: null,
  }));
}

{
  const library = books(246);
  const call = await sent(() => api.listAllBooks(), library);
  assert.strictEqual(call.method, "GET");
  assert.strictEqual(path(call.url), "/api/v1/books");
  const q = query(call.url);
  // One statement, explicitly bounded — NOT the router's 100-row default.
  assert.strictEqual(q.get("limit"), "2001");
  assert.strictEqual(q.get("offset"), "0");
  assert.strictEqual(call.body, undefined, "a GET must not carry a body");
}

{
  // Every book reaches the caller: the whole point of the wider read.
  calls.length = 0;
  nextStatus = 200;
  nextBody = books(246);
  const rows = await api.listAllBooks();
  assert.strictEqual(rows.length, 246);
  assert.strictEqual(calls.length, 1, "the complete library is ONE request, never a page walk");
  assert.strictEqual(rows[245].id, "book-245", "the tail of the library must survive the read");
}

{
  // The boundary itself is fine — 2000 rows is a complete library, not an
  // overflowing one, so `> 2000` must not fire at the limit.
  calls.length = 0;
  nextStatus = 200;
  nextBody = books(2000);
  const rows = await api.listAllBooks();
  assert.strictEqual(rows.length, 2000);
}

{
  // 2001 rows means row 2001 exists and the answer may already be short of the
  // truth. Refusing loudly beats a picker that is missing books it never
  // mentions.
  calls.length = 0;
  nextStatus = 200;
  nextBody = books(2001);
  const overflowing = api.listAllBooks();
  await assert.rejects(overflowing, (err: unknown) => {
    assert.ok(err instanceof Error, `expected an Error, got ${String(err)}`);
    assert.ok(!(err instanceof ApiError), "a full library is not an HTTP failure");
    assert.match(err.message, /safety limit of 2000 rows/);
    return true;
  });
}

{
  // A refusal from the router is still a refusal: the guided picker must see
  // the structured error, not an empty library.
  const err = await failed(() => api.listAllBooks(), 503, { detail: "database unavailable" });
  assert.strictEqual(err.status, 503);
  assert.strictEqual(err.message, "database unavailable");
}

console.log("OK");
