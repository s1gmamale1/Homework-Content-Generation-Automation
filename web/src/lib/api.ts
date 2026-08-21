import { clearToken, getToken } from "./auth";
import { cascadeDisclosure, judgeSignal, lessonCountLabel } from "./regeneration-state";
import { subjectLabel } from "./subjects";
import type {
  ApprovalGate,
  CascadeSummary,
  JudgeSignal,
  JudgeStatus,
  PhaseSelection,
} from "./regeneration-state";
import type {
  AgentStats,
  AvailableLanguages,
  BatchCancelResponse,
  BatchLaunchResponse,
  BatchLessonRow,
  BatchPauseResponse,
  BatchPreviewResponse,
  BatchRearchiveResponse,
  BatchResumeResponse,
  BatchSummary,
  Book,
  CoverageResponse,
  DeckResponse,
  Job,
  JobKind,
  LaunchDefaults,
  NotionGrade,
  NotionSubject,
  OutputLanguage,
  ProviderModelManifest,
  RegenerationActorRequest,
  RegenerationBucket,
  RegenerationCampaignDetail,
  RegenerationCampaignDraft,
  RegenerationCampaignList,
  RegenerationCampaignStatus,
  RegenerationCampaignSummary,
  RegenerationEligibleSource,
  RegenerationEligibleSources,
  RegenerationEstimateRequest,
  RegenerationEstimateResponse,
  RegenerationOutputLanguage,
  RegenerationPhasePlan,
  RegenerationPhasePlanRequest,
  RegenerationPublicationState,
  RegenerationReasonRequest,
  RegenerationTargetActionResult,
  RegenerationTargetReport,
  RegenerationTargetStatus,
  RegenerationWaveFailure,
  RoleTransport,
  SaKey,
  SaKeyAssignment,
  SessionLimitStrategy,
  Subject,
  TeacherDeck,
  TOCEntry,
  Transport,
  Worker,
  WorkerStatusResponse,
} from "./types";

class ApiError extends Error {
  status: number;
  /** The raw `detail` value from the response body when it parsed as JSON —
   *  either a plain string (stale-selector / no-textbook / language-mismatch)
   *  or a structured object (e.g. `ambiguous_textbook`'s
   *  `{error, message, candidates}`). Undefined when the body wasn't JSON. */
  detail?: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Authenticated fetch. Attaches the bearer token from sessionStorage to
 * every request, and on 401 clears the stored token (so the route guard
 * redirects to /login on next render).
 */
async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    // Token rejected by the server. Drop our local copy so the auth guard
    // bounces to /login. We don't navigate from here — that's the router's
    // job — but onAuthChange listeners (the guard) will pick this up.
    clearToken();
  }
  return res;
}

/**
 * FastAPI 422/4xx bodies are `{"detail": ...}`, but `detail` is MIXED-SHAPE
 * on some endpoints (BE-19): a plain string for stale-selector / no-textbook
 * / language-mismatch, but a DICT for `ambiguous_textbook`
 * (`{error, message, candidates}`). Extract a human-readable message either
 * way, while keeping the full parsed `detail` on the error for callers that
 * need the structured payload (e.g. the candidate list).
 */
function extractErrorMessage(text: string, fallback: string): { message: string; detail?: unknown } {
  if (!text) return { message: fallback };
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = parsed.detail;
      if (typeof detail === "string") return { message: detail, detail };
      if (detail && typeof detail === "object" && "message" in detail) {
        const msg = (detail as { message?: unknown }).message;
        return { message: typeof msg === "string" ? msg : text, detail };
      }
      return { message: text, detail };
    }
  } catch {
    // Not JSON — fall through to the raw text.
  }
  return { message: text };
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    const { message, detail } = extractErrorMessage(text, res.statusText);
    throw new ApiError(res.status, message, detail);
  }
  return (await res.json()) as T;
}

/**
 * Append the auth token as `?token=...` to a URL. Used for SSE streams and
 * downloads — places where we can't set a header (EventSource doesn't
 * support custom headers; <a download> doesn't either). Server-side, the
 * auth dep accepts header OR query param.
 */
function withTokenParam(url: string): string {
  const token = getToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

/**
 * POST to a regeneration route.
 *
 * `body === undefined` sends NO body and no `Content-Type` at all: `canary`,
 * `retry-generation` and `retry-publication` declare no request model, and an
 * unexpected payload against an `extra="forbid"` router is noise at best.
 */
async function regenerationPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await authFetch(`/api/v1/regeneration${path}`, {
    method: "POST",
    ...(body === undefined
      ? {}
      : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  return unwrap<T>(res);
}

export const api = {
  async listBooks(): Promise<Book[]> {
    const res = await authFetch("/api/v1/books");
    return unwrap<Book[]>(res);
  },

  async getCoverage(outputLanguage: OutputLanguage): Promise<CoverageResponse> {
    const res = await authFetch(
      `/api/v1/dashboard/coverage?output_language=${encodeURIComponent(outputLanguage)}`,
    );
    return unwrap<CoverageResponse>(res);
  },

  async uploadBook(file: File, subject: Subject, grade?: string, sourceLanguage?: OutputLanguage): Promise<Book> {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject);
    if (grade) fd.append("grade", grade);
    if (sourceLanguage) fd.append("source_language", sourceLanguage);
    const res = await authFetch("/api/v1/books", { method: "POST", body: fd });
    return unwrap<Book>(res);
  },

  async getBook(bookId: string, outputLanguage?: string | null, kind?: JobKind): Promise<Book> {
    // When a language is given, per-lesson status is scoped to it so the
    // launcher's "complete"/launch gate reflects the selected language.
    // `kind` scopes it the same way for job kind — omitted means the backend
    // default "homework" (byte-identical for every non-launcher caller).
    const params = new URLSearchParams();
    if (outputLanguage) params.set("output_language", outputLanguage);
    if (kind) params.set("kind", kind);
    const qs = params.toString();
    const res = await authFetch(
      `/api/v1/books/${encodeURIComponent(bookId)}${qs ? `?${qs}` : ""}`,
    );
    return unwrap<Book>(res);
  },

  async updateBook(
    bookId: string,
    patch: { original_filename?: string; subject?: Subject },
  ): Promise<Book> {
    const res = await authFetch(`/api/v1/books/${encodeURIComponent(bookId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    return unwrap<Book>(res);
  },

  async deleteBook(bookId: string): Promise<void> {
    const res = await authFetch(`/api/v1/books/${encodeURIComponent(bookId)}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new ApiError(res.status, text || res.statusText);
    }
  },

  async updateTocEntry(
    bookId: string,
    entryId: string,
    patch: Partial<
      Pick<
        TOCEntry,
        | "chapter_number"
        | "chapter_title"
        | "section_number"
        | "section_title"
        | "page_start"
        | "page_end"
      >
    >,
  ): Promise<TOCEntry> {
    const res = await authFetch(
      `/api/v1/books/${encodeURIComponent(bookId)}/toc/${encodeURIComponent(entryId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      },
    );
    return unwrap<TOCEntry>(res);
  },

  async deleteTocEntry(bookId: string, entryId: string): Promise<void> {
    const res = await authFetch(
      `/api/v1/books/${encodeURIComponent(bookId)}/toc/${encodeURIComponent(entryId)}`,
      { method: "DELETE" },
    );
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new ApiError(res.status, text || res.statusText);
    }
  },

  async generate(
    bookId: string,
    sectionId: string,
    opts: {
      force?: boolean;
      idempotencyKey?: string;
      provider?: string;
      model?: string | null;
      transport?: Transport;
      extract_transport?: RoleTransport;
      judge_transport?: RoleTransport;
      custom_prompts?: Record<string, string> | null;
      selected_phases?: string[] | null;
      extract_provider?: string | null;
      extract_model?: string | null;
      judge_provider?: string | null;
      judge_model?: string | null;
      output_language?: OutputLanguage | null;
    } = {},
  ): Promise<Job & { added_phases?: string[] }> {
    const {
      force = false,
      idempotencyKey,
      provider = "claude",
      model = null,
      transport = "cli",
      extract_transport = "inherit",
      judge_transport = "inherit",
      custom_prompts = null,
      selected_phases = null,
      extract_provider = null,
      extract_model = null,
      judge_provider = null,
      judge_model = null,
      output_language = null,
    } = opts;
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const res = await authFetch(
      `/api/v1/books/${encodeURIComponent(bookId)}/sections/${encodeURIComponent(sectionId)}/generate`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({
          force,
          provider,
          model,
          transport,
          extract_transport,
          judge_transport,
          custom_prompts,
          selected_phases,
          extract_provider,
          extract_model,
          judge_provider,
          judge_model,
          ...(output_language != null ? { output_language } : {}),
        }),
      },
    );
    return unwrap<Job>(res);
  },

  async getAgentModels(): Promise<ProviderModelManifest> {
    const res = await authFetch("/api/v1/agent/models");
    return unwrap<ProviderModelManifest>(res);
  },

  async getLaunchDefaults(): Promise<LaunchDefaults> {
    return unwrap<LaunchDefaults>(await authFetch("/api/v1/settings/launch-defaults"));
  },

  async updateLaunchDefaults(patch: Partial<LaunchDefaults>): Promise<LaunchDefaults> {
    return unwrap<LaunchDefaults>(await authFetch("/api/v1/settings/launch-defaults", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }));
  },

  async getAgentStats(): Promise<AgentStats> {
    const res = await authFetch("/api/v1/agent/stats");
    return unwrap<AgentStats>(res);
  },

  async getJob(jobId: string): Promise<Job> {
    const res = await authFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    return unwrap<Job>(res);
  },

  /**
   * Fetch the teacher-deck structured content for a `kind="teacher_material"`
   * job — see `GET /api/v1/jobs/<id>/deck`. 404s (via `ApiError`) when the
   * job doesn't exist, isn't `teacher_material`, or the `teacher-deck` phase
   * hasn't produced `content_json` yet.
   */
  async getDeck(jobId: string): Promise<TeacherDeck> {
    const res = await authFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/deck`);
    return (await unwrap<DeckResponse>(res)).content_json;
  },

  /**
   * Retry a failed job in place — reuses the same job row (keeping the
   * pinned provider/model) instead of creating a new one. Server returns
   * 409 if the job is not in `failed` status. The "regenerate from scratch"
   * path is `generate(..., { force: true })` from the section page.
   */
  async retryJob(jobId: string): Promise<Job> {
    const res = await authFetch(
      `/api/v1/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: "POST" },
    );
    return unwrap<Job>(res);
  },

  /** Re-attempt the best-effort Notion archive for a `done` job whose push
   *  previously failed — see `POST /api/v1/jobs/<id>/retry-archive`. */
  async retryArchiveJob(jobId: string): Promise<Job> {
    const res = await authFetch(
      `/api/v1/jobs/${encodeURIComponent(jobId)}/retry-archive`,
      { method: "POST" },
    );
    return unwrap<Job>(res);
  },

  async retryArchiveBatch(batchId: string, opts?: { stale?: boolean }): Promise<BatchRearchiveResponse> {
    const qs = opts?.stale ? "?stale=true" : "";
    const res = await authFetch(
      `/api/v1/jobs/batch/${encodeURIComponent(batchId)}/retry-archive${qs}`,
      { method: "POST" },
    );
    return unwrap<BatchRearchiveResponse>(res);
  },

  /**
   * Re-run book preparation (TOC extraction) on a `failed` or stuck
   * `toc_extracting` book — see `POST /api/v1/books/<id>/toc/retry`. Mirrors
   * `retryJob`: reuses the same book row and returns the updated `Book`.
   */
  async retryBookToc(bookId: string): Promise<Book> {
    const res = await authFetch(
      `/api/v1/books/${encodeURIComponent(bookId)}/toc/retry`,
      { method: "POST" },
    );
    return unwrap<Book>(res);
  },

  /**
   * Accept the TOC for a book in `toc_review` status — transitions the book
   * to `toc_ready` without re-running extraction. Mirrors `retryBookToc`.
   * See `POST /api/v1/books/<id>/toc/accept`.
   */
  async acceptToc(bookId: string): Promise<Book> {
    const res = await authFetch(
      `/api/v1/books/${encodeURIComponent(bookId)}/toc/accept`,
      { method: "POST" },
    );
    return unwrap<Book>(res);
  },

  /**
   * Request cancellation of a pending or running job — see
   * `POST /api/v1/jobs/<id>/cancel`. A queued job moves straight to
   * `cancelled`; a running one transitions to `cancelling` while the worker
   * tears the task down, then settles to `cancelled`.
   */
  async cancelJob(jobId: string): Promise<Job> {
    const res = await authFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    });
    return unwrap<Job>(res);
  },

  async listNotionGrades(): Promise<NotionGrade[]> {
    const res = await authFetch("/api/v1/notion/grades");
    return unwrap<NotionGrade[]>(res);
  },

  async listNotionSubjects(gradePageId: string): Promise<NotionSubject[]> {
    const res = await authFetch(
      `/api/v1/notion/grades/${encodeURIComponent(gradePageId)}/subjects`,
    );
    return unwrap<NotionSubject[]>(res);
  },

  /** `blockId` is sent whenever the caller has one — even when the part's
   *  candidate resolution was unambiguous — so a Notion-side reorder between
   *  crawl and prepare can't silently swap the fetched file (BE-19 task 6). */
  async fetchBookFromNotion(
    subjectPageId: string,
    grade: string,
    language?: OutputLanguage,
    blockId?: string,
  ): Promise<Book> {
    const res = await authFetch("/api/v1/books/from-notion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject_page_id: subjectPageId,
        grade,
        ...(language ? { language } : {}),
        ...(blockId ? { block_id: blockId } : {}),
      }),
    });
    return unwrap<Book>(res);
  },

  /** Fetch available UZ/RU/EN language containers for each subject in a grade.
   *  Returns { [app_subject]: { [lang]: { page_id, has_textbook, parts? } } }. */
  async fetchAvailableLanguages(gradePageId: string): Promise<AvailableLanguages> {
    const res = await authFetch(
      `/api/v1/notion/grades/${encodeURIComponent(gradePageId)}/available-languages`,
    );
    return unwrap<AvailableLanguages>(res);
  },

  async listBatches(): Promise<BatchSummary[]> {
    const res = await authFetch("/api/v1/jobs/batches");
    return (await unwrap<{ batches: BatchSummary[] }>(res)).batches;
  },
  async getBatch(batchId: string): Promise<BatchSummary> {
    const res = await authFetch(`/api/v1/jobs/batches/${encodeURIComponent(batchId)}`);
    return unwrap<BatchSummary>(res);
  },
  async batchJobs(batchId: string): Promise<BatchLessonRow[]> {
    const res = await authFetch(`/api/v1/jobs/batches/${encodeURIComponent(batchId)}/jobs`);
    return (await unwrap<{ jobs: BatchLessonRow[] }>(res)).jobs;
  },
  async launchBatch(body: {
    book_id: string; toc_entry_ids?: string[]; provider?: string; model?: string | null; transport?: Transport;
    extract_transport?: RoleTransport; judge_transport?: RoleTransport; force?: boolean;
    extract_provider?: string | null; extract_model?: string | null;
    judge_provider?: string | null; judge_model?: string | null;
    relaunch_mode?: "resume" | "discard";
    session_limit_strategy?: SessionLimitStrategy;
    output_language?: OutputLanguage | null;
    include_classes?: string[];
    /** "homework" (default, server-side) | "teacher_material" — forks its own
     *  batch and never resumes/adopts the other kind's job for a section. */
    kind?: JobKind;
  }): Promise<BatchLaunchResponse> {
    const res = await authFetch("/api/v1/jobs/batch", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    return unwrap(res);
  },

  /**
   * Preview a batch launch without mutating state. Returns counts of new,
   * resumable (failed/cancelled with saved phases), and empty sections.
   * Equivalent to `launchBatch({ ...body, preview: true })` but narrowly typed.
   */
  async previewBatch(body: {
    book_id: string; toc_entry_ids?: string[]; provider?: string; model?: string | null; transport?: Transport;
    extract_transport?: RoleTransport; judge_transport?: RoleTransport;
    extract_provider?: string | null; extract_model?: string | null;
    judge_provider?: string | null; judge_model?: string | null;
    session_limit_strategy?: SessionLimitStrategy;
    output_language?: OutputLanguage | null;
    include_classes?: string[];
    kind?: JobKind;
  }): Promise<BatchPreviewResponse> {
    const res = await authFetch("/api/v1/jobs/batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, preview: true }),
    });
    return unwrap<BatchPreviewResponse>(res);
  },

  /**
   * Cancel all pending/running jobs in a batch. Pending jobs move immediately
   * to `cancelled`; running ones move to `cancelling` (the worker tears them
   * down within heartbeat_seconds, then settles to `cancelled`).
   */
  async cancelBatch(batchId: string): Promise<BatchCancelResponse> {
    const res = await authFetch(
      `/api/v1/jobs/batch/${encodeURIComponent(batchId)}/cancel`,
      { method: "POST" },
    );
    return unwrap<BatchCancelResponse>(res);
  },

  /**
   * Resume all failed/cancelled jobs in a batch — reuses saved `done` phase
   * outputs so only unfinished phases re-run. Mirrors `retryJob` at batch scope.
   */
  async resumeBatch(batchId: string): Promise<BatchResumeResponse> {
    const res = await authFetch(
      `/api/v1/jobs/batch/${encodeURIComponent(batchId)}/resume`,
      { method: "POST" },
    );
    return unwrap<BatchResumeResponse>(res);
  },

  /** Pause a batch — sets paused_at/paused_reason="manual"; claim gate skips it. */
  async pauseBatch(batchId: string): Promise<BatchPauseResponse> {
    const res = await authFetch(
      `/api/v1/jobs/batch/${encodeURIComponent(batchId)}/pause`,
      { method: "POST" },
    );
    return unwrap<BatchPauseResponse>(res);
  },

  /** Unpause a batch — clears paused_at/paused_reason; claim gate re-includes it. */
  async unpauseBatch(batchId: string): Promise<BatchPauseResponse> {
    const res = await authFetch(
      `/api/v1/jobs/batch/${encodeURIComponent(batchId)}/unpause`,
      { method: "POST" },
    );
    return unwrap<BatchPauseResponse>(res);
  },

  async listWorkers(): Promise<{
    workers: Worker[];
    total: number;
    online: number;
    stale_after_seconds: number;
    version_floor: number | null;
  }> {
    const res = await authFetch("/api/v1/workers");
    return unwrap(res);
  },

  /** Drain a worker — sets status "draining"; worker finishes in-flight jobs then stops claiming. */
  async drainWorker(pcId: string): Promise<WorkerStatusResponse> {
    const res = await authFetch(
      `/api/v1/workers/${encodeURIComponent(pcId)}/drain`,
      { method: "POST" },
    );
    return unwrap<WorkerStatusResponse>(res);
  },

  /** Undrain a worker — reverts status to "online"; claim gate re-includes it. */
  async undrainWorker(pcId: string): Promise<WorkerStatusResponse> {
    const res = await authFetch(
      `/api/v1/workers/${encodeURIComponent(pcId)}/undrain`,
      { method: "POST" },
    );
    return unwrap<WorkerStatusResponse>(res);
  },

  /** Unconditional set (may LOWER) or clear of the fleet version floor — the operator escape hatch; the lifespan auto-stamp is the raise-only path. */
  async setVersionFloor(value: number | null): Promise<{ version_floor: number | null }> {
    const res = await authFetch("/api/v1/workers/version-floor", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    return unwrap<{ version_floor: number | null }>(res);
  },

  jobDownloadUrl(jobId: string): string {
    return withTokenParam(`/api/v1/jobs/${encodeURIComponent(jobId)}/download`);
  },

  bookTocStreamUrl(bookId: string): string {
    return withTokenParam(`/api/v1/books/${encodeURIComponent(bookId)}/toc/stream`);
  },

  jobStreamUrl(jobId: string): string {
    return withTokenParam(`/api/v1/jobs/${encodeURIComponent(jobId)}/stream`);
  },

  /* ── Versioned homework regeneration (app/api/v1/regeneration.py) ──────
   *
   * Thirteen routes under `/api/v1/regeneration`, mounted with the same
   * router-level `get_current_user` dependency as books/batch/jobs — so
   * `authFetch` is all the auth they need, exactly like every method above.
   *
   * With `REGENERATION_ENABLED=false` every route answers 404 by design: a
   * stale bundle must not be able to learn that the feature exists, let alone
   * mutate through it. `regenerationErrorView` turns that into operator prose.
   */

  /** Regenerable lessons with their current and next version, plus every
   *  selected lineage that was left out and why. Filters are REPEATED query
   *  params (`Query(default=[])`); omitting one means "do not filter on this
   *  axis", which is not the same as selecting nothing. */
  async listRegenerationEligible(
    filters: {
      bookIds?: string[];
      tocEntryIds?: string[];
      outputLanguages?: RegenerationOutputLanguage[];
    } = {},
  ): Promise<RegenerationEligibleSources> {
    const q = new URLSearchParams();
    for (const id of filters.bookIds ?? []) q.append("book_id", id);
    for (const id of filters.tocEntryIds ?? []) q.append("toc_entry_id", id);
    for (const language of filters.outputLanguages ?? []) q.append("output_language", language);
    const suffix = q.toString();
    const res = await authFetch(`/api/v1/regeneration/eligible${suffix ? `?${suffix}` : ""}`);
    return unwrap<RegenerationEligibleSources>(res);
  },

  /** The real dependency closure for one subject, with the edges an exclusion
   *  would break. Pure server-side: no database write, no model call. */
  async previewRegenerationPhasePlan(
    body: RegenerationPhasePlanRequest,
  ): Promise<RegenerationPhasePlan> {
    return regenerationPost<RegenerationPhasePlan>("/phase-plan", body);
  },

  /** Price a draft and preflight its Notion destinations. Creates nothing.
   *
   *  The create-only fields are STRIPPED here: `EstimateRequest` is
   *  `extra="forbid"`, so posting a create-shaped draft is a 422, not a
   *  tolerated superset. */
  async estimateRegeneration(
    draft: RegenerationCampaignDraft,
  ): Promise<RegenerationEstimateResponse> {
    return regenerationPost<RegenerationEstimateResponse>(
      "/estimate",
      regenerationEstimateBody(draft),
    );
  },

  /** Freeze an immutable campaign and its targets. Still no job, no model
   *  call and no Notion page — the canary launch is the first spend. */
  async createRegenerationCampaign(
    draft: RegenerationCampaignDraft,
  ): Promise<RegenerationCampaignDetail> {
    return regenerationPost<RegenerationCampaignDetail>(
      "/campaigns",
      regenerationCampaignBody(draft),
    );
  },

  async listRegenerationCampaigns(
    query: {
      statuses?: RegenerationCampaignStatus[];
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<RegenerationCampaignList> {
    const q = new URLSearchParams();
    // `status`, not `status_filter`: the route declares `alias="status"`.
    for (const status of query.statuses ?? []) q.append("status", status);
    if (query.limit != null) q.set("limit", String(query.limit));
    if (query.offset != null) q.set("offset", String(query.offset));
    const suffix = q.toString();
    const res = await authFetch(`/api/v1/regeneration/campaigns${suffix ? `?${suffix}` : ""}`);
    return unwrap<RegenerationCampaignList>(res);
  },

  /** The campaign report: every bucket, every reason, every dollar. */
  async getRegenerationCampaign(campaignId: string): Promise<RegenerationCampaignDetail> {
    const res = await authFetch(`/api/v1/regeneration/campaigns/${encodeURIComponent(campaignId)}`);
    return unwrap<RegenerationCampaignDetail>(res);
  },

  /** Preflight every destination, then create ONLY the canary revision jobs.
   *  Idempotent server-side: a target that already has a job is left alone. */
  async launchRegenerationCanary(campaignId: string): Promise<RegenerationCampaignDetail> {
    return regenerationPost<RegenerationCampaignDetail>(
      `/campaigns/${encodeURIComponent(campaignId)}/canary`,
    );
  },

  /** The one human gate. Releases the canaries for publication and creates
   *  every remaining revision exactly once. Requires the publisher flag. */
  async approveRegenerationCampaign(
    campaignId: string,
    body: RegenerationActorRequest,
  ): Promise<RegenerationCampaignDetail> {
    return regenerationPost<RegenerationCampaignDetail>(
      `/campaigns/${encodeURIComponent(campaignId)}/approve`,
      body,
    );
  },

  /** Decline the canary: nothing publishes and no version is consumed. */
  async rejectRegenerationCampaign(
    campaignId: string,
    body: RegenerationReasonRequest,
  ): Promise<RegenerationCampaignDetail> {
    return regenerationPost<RegenerationCampaignDetail>(
      `/campaigns/${encodeURIComponent(campaignId)}/reject`,
      body,
    );
  },

  /** Stop a campaign. Published pages and reserved versions stand. */
  async cancelRegenerationCampaign(
    campaignId: string,
    body: RegenerationReasonRequest,
  ): Promise<RegenerationCampaignDetail> {
    return regenerationPost<RegenerationCampaignDetail>(
      `/campaigns/${encodeURIComponent(campaignId)}/cancel`,
      body,
    );
  },

  /** Re-run a failed revision on its EXISTING snapshot and frozen phase plan.
   *  No new campaign, no new version. */
  async retryRegenerationGeneration(targetId: string): Promise<RegenerationTargetActionResult> {
    return regenerationPost<RegenerationTargetActionResult>(
      `/targets/${encodeURIComponent(targetId)}/retry-generation`,
    );
  },

  /** Re-queue the Notion write only: no model call, no new revision job, and
   *  the reserved version stays the same. Requires the publisher flag. */
  async retryRegenerationPublication(targetId: string): Promise<RegenerationTargetActionResult> {
    return regenerationPost<RegenerationTargetActionResult>(
      `/targets/${encodeURIComponent(targetId)}/retry-publication`,
    );
  },

  /** Give up on one target. Audited, never deletes a Notion page, and never
   *  reuses a version that was already reserved. */
  async abandonRegenerationTarget(
    targetId: string,
    body: RegenerationReasonRequest,
  ): Promise<RegenerationTargetActionResult> {
    return regenerationPost<RegenerationTargetActionResult>(
      `/targets/${encodeURIComponent(targetId)}/abandon`,
      body,
    );
  },

  // --- SA key management ---

  async listSaKeys(): Promise<{ keys: SaKey[] }> {
    const res = await authFetch("/api/v1/sa-keys");
    return unwrap<{ keys: SaKey[] }>(res);
  },

  /** Upload a JSON service-account key file. Uses FormData so the browser sets
   *  the multipart boundary — matches the uploadBook pattern (no forced Content-Type). */
  async uploadSaKey(file: File): Promise<SaKey> {
    const form = new FormData();
    form.append("file", file);
    const res = await authFetch("/api/v1/sa-keys", { method: "POST", body: form });
    if (!res.ok) throw new Error((await res.json()).detail ?? "upload failed");
    return res.json() as Promise<SaKey>;
  },

  async deleteSaKey(id: string): Promise<void> {
    const res = await authFetch(`/api/v1/sa-keys/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) throw new Error((await res.json()).detail ?? "delete failed");
  },

  /** Set (or clear, with `null`) the per-key concurrency override. Project-
   *  wide: the backend updates every sa_keys row sharing this key's
   *  project_id in one atomic statement. */
  async setSaKeyMaxConcurrentCalls(id: string, maxConcurrentCalls: number | null): Promise<SaKey> {
    const res = await authFetch(`/api/v1/sa-keys/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_concurrent_calls: maxConcurrentCalls }),
    });
    if (!res.ok) throw new Error((await res.json()).detail ?? "update failed");
    return res.json() as Promise<SaKey>;
  },

  async listSaKeyAssignments(): Promise<{ assignments: SaKeyAssignment[] }> {
    const res = await authFetch("/api/v1/sa-keys/assignments");
    return unwrap<{ assignments: SaKeyAssignment[] }>(res);
  },

  async assignSaKey(hostname: string, keyId: string): Promise<void> {
    const res = await authFetch(
      `/api/v1/sa-keys/assignments/${encodeURIComponent(hostname)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key_id: keyId }),
      },
    );
    if (!res.ok) throw new Error((await res.json()).detail ?? "assign failed");
  },

  async unassignSaKey(hostname: string): Promise<void> {
    const res = await authFetch(
      `/api/v1/sa-keys/assignments/${encodeURIComponent(hostname)}`,
      { method: "DELETE" },
    );
    // authFetch does NOT throw on 4xx/5xx — check res.ok so the caller's
    // success toast can't fire on a failed unassign (matches assignSaKey).
    if (!res.ok) throw new Error((await res.json()).detail ?? "unassign failed");
  },

  async scrubSaKey(hostname: string): Promise<void> {
    const res = await authFetch(
      `/api/v1/sa-keys/assignments/${encodeURIComponent(hostname)}/scrub`,
      { method: "POST" },
    );
    if (!res.ok) throw new Error((await res.json()).detail ?? "scrub failed");
  },
};

export { ApiError };

/* ══════════════════════════════════════════════════════════════════════
 * Regeneration: pure readers over the Task 9 response shapes
 *
 * Pure functions, no HTTP. They live in this module rather than in
 * `regeneration-state.ts` because Task 10 does not own that file: the Task 4
 * decision layer ships unchanged. Where a rule already exists there — the
 * cascade headline, the exclusion warning, the judge vocabulary, the money
 * format — this section REUSES it. Where the Task 4 type is narrower than the
 * API (`PlanTarget.language` has no `"en"`, and forcing one in would mislabel
 * an English lesson), the rule is restated here and `regeneration-state.test.ts`
 * asserts the two agree, so they cannot drift apart silently.
 *
 * Three rules run through everything below.
 *
 * **The server is authoritative.** Nothing here recomputes a phase closure, a
 * bucket membership or a cost; it renders what the API said. The one
 * re-derivation — the cascade headline — is cross-checked against the server's
 * own counts by a test.
 *
 * **A status code is never shown to an operator.** Every failure, park and
 * abandonment renders as a sentence, including the ones this build has never
 * seen.
 *
 * **Polling follows work, not screens.** A campaign parked on a human decision
 * is not refreshed: it cannot change until that human acts.
 * ══════════════════════════════════════════════════════════════════════ */

/** How often an ACTIVE campaign report is refreshed. */
export const REGENERATION_POLL_MS = 4_000;
/** The campaign list is a cheap rollup and never takes a write lock, but it
 *  still only ticks while at least one campaign is doing something. */
export const REGENERATION_LIST_POLL_MS = 10_000;

export const REGENERATION_CREATE_LABEL = "Create campaign";
/** Deliberately "Generate canary", never Fleet's first-run vocabulary. */
export const REGENERATION_LAUNCH_LABEL = "Generate canary";

export const REGENERATION_NO_SPEND_NOTE =
  "Previewing the phase plan, pricing the estimate and creating the campaign make no model " +
  "calls and create no Notion page. Nothing is spent and nothing is published until you " +
  "generate the canary and approve it.";

export const REGENERATION_LAUNCH_SPEND_NOTE =
  "Generating the canary is the first step that costs money. It still publishes nothing: the " +
  "canary waits here for your review, and no Notion page is created until you approve.";

export const REGENERATION_APPROVE_NOTE =
  "Approving is the only gate in this campaign. The remaining lessons regenerate automatically, " +
  "and every version that generates successfully publishes to Notion automatically — there is " +
  "no per-lesson publication approval.";

export const REGENERATION_REJECT_CONFIRMATION =
  "Reject this canary? No existing Notion version is deleted, no new page is created and no " +
  "publication version is consumed. The canary revisions stay readable here for audit, and " +
  "regenerating these lessons later needs a new campaign.";

export const REGENERATION_CANCEL_CONFIRMATION =
  "Cancel this campaign? Unfinished work stops. No existing Notion version is deleted: pages " +
  "that already published stay published, and any version that was already reserved stays " +
  "consumed. There is no automatic resume.";

/** Turn an internal snake/kebab token into operator prose. Used only for codes
 *  this build has no mapping for — an unknown code is spelled out, never
 *  echoed raw. */
function humanise(code: string): string {
  const words = code.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "";
}

function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many;
}

/* ── request bodies ───────────────────────────────────────────────────── */

/**
 * The `/estimate` body: the draft MINUS every create-only field.
 *
 * `EstimateRequest` inherits `extra="forbid"`, so `actor`, `notes`,
 * `estimated_cost_low_usd`, `estimated_cost_high_usd` and `app_git_revision`
 * are each a 422 rather than an ignored extra. One draft object drives both
 * calls; only this function decides what each route is allowed to see.
 */
export function regenerationEstimateBody(
  draft: RegenerationCampaignDraft,
): RegenerationEstimateRequest {
  return {
    selection: draft.selection,
    contract: draft.contract,
    selected_phases: draft.selected_phases,
    excluded_affected_phases: draft.excluded_affected_phases,
    refresh_extraction: draft.refresh_extraction,
    exclusion_acknowledged: draft.exclusion_acknowledged,
    canary_size: draft.canary_size,
  };
}

/** The `/campaigns` body: the estimate body plus the figures the operator was
 *  SHOWN, echoed back so the frozen campaign records what was approved. */
export function regenerationCampaignBody(
  draft: RegenerationCampaignDraft,
): RegenerationCampaignDraft {
  return {
    ...regenerationEstimateBody(draft),
    estimated_cost_low_usd: draft.estimated_cost_low_usd,
    estimated_cost_high_usd: draft.estimated_cost_high_usd,
    app_git_revision: draft.app_git_revision,
    actor: draft.actor,
    notes: draft.notes,
  };
}

/* ── vocabulary ───────────────────────────────────────────────────────── */

const REGENERATION_CAMPAIGN_STATUS_LABELS: Record<RegenerationCampaignStatus, string> = {
  draft: "Draft",
  canary_running: "Canary regenerating",
  awaiting_canary_approval: "Waiting for your review",
  approved: "Approved",
  bulk_running: "Regenerating the rest",
  attention_required: "Needs your attention",
  completed: "Completed",
  completed_with_abandonments: "Completed, some lessons abandoned",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

/** `"unknown"` is accepted because `TargetActionOut.campaign_status` really can
 *  be it: the router answers "unknown" when the campaign row cannot be read
 *  back. It gets its own words rather than the humanised token. */
export function regenerationCampaignStatusLabel(
  status: RegenerationCampaignStatus | "unknown",
): string {
  if (status === "unknown") return "Status unavailable";
  return REGENERATION_CAMPAIGN_STATUS_LABELS[status] ?? humanise(status);
}

const REGENERATION_TARGET_STATUS_LABELS: Record<RegenerationTargetStatus, string> = {
  planned: "Planned",
  generating: "Regenerating",
  awaiting_canary_approval: "Canary ready for review",
  publication_pending: "Queued to publish",
  publishing: "Publishing",
  published: "Published",
  generation_failed: "Regeneration failed",
  publication_failed: "Publishing failed",
  abandoned: "Abandoned",
};

export function regenerationTargetStatusLabel(status: RegenerationTargetStatus): string {
  return REGENERATION_TARGET_STATUS_LABELS[status] ?? humanise(status);
}

/**
 * The delivery situation, in words.
 *
 * `backing_off`, `retry_due` and `action_required` are the three shapes of
 * `publication_failed` and they are three DIFFERENT situations: the publisher
 * owns the first two, and only the third needs a human. Rendering them
 * identically buries every row that is actually stuck.
 */
const REGENERATION_PUBLICATION_STATE_LABELS: Record<RegenerationPublicationState, string> = {
  published: "Published to Notion",
  abandoned: "Abandoned before it published",
  publishing: "Writing the page now",
  queued: "Queued to publish",
  backing_off: "Waiting for an automatic retry",
  retry_due: "Automatic retry is due now",
  action_required: "Parked: no automatic retry left, so you have to decide",
  not_started: "Not published yet",
};

export function regenerationPublicationStateLabel(state: RegenerationPublicationState): string {
  return REGENERATION_PUBLICATION_STATE_LABELS[state] ?? humanise(state);
}

/* ── report buckets ───────────────────────────────────────────────────── */

export const REGENERATION_BUCKET_ORDER: RegenerationBucket[] = [
  "published",
  "publication_pending",
  "publication_failed",
  "generation_failed",
  "abandoned",
  "in_flight",
];

const REGENERATION_BUCKET_COPY: Record<RegenerationBucket, { label: string; description: string }> =
  {
    published: {
      label: "Regenerated and published",
      description:
        "A new versioned Homework page exists in Notion beside the original. Nothing that was " +
        "already published was cleared, renamed or overwritten.",
    },
    publication_pending: {
      label: "Regenerated, waiting to publish",
      description:
        "The revision is complete and queued. The publisher writes these pages by itself — " +
        "there is nothing for you to do.",
    },
    publication_failed: {
      label: "Regenerated, publishing failed",
      description:
        "The homework itself is fine; only the Notion write failed. Retrying publication never " +
        "re-runs the model and never allocates a new version.",
    },
    generation_failed: {
      label: "Regeneration failed",
      description:
        "No complete snapshot was produced, so there is nothing to publish. Each of these holds " +
        "its lesson's active lineage until you retry it or abandon it.",
    },
    abandoned: {
      label: "Abandoned",
      description:
        "You gave up on these. No Notion page was deleted, and a version that had already been " +
        "reserved stays consumed — the next successful regeneration publishes at the following " +
        "version.",
    },
    in_flight: {
      label: "Still working",
      description:
        "Planned, being rebuilt, or being written to Notion right now. Nothing here needs a " +
        "decision yet.",
    },
  };

export interface RegenerationBucketView {
  bucket: RegenerationBucket;
  label: string;
  description: string;
  count: number;
  targets: RegenerationTargetReport[];
}

/**
 * All six buckets, always, in the documented order.
 *
 * An empty bucket is still rendered: a report that silently drops a bucket
 * tells an operator the campaign is smaller than it is. Membership comes from
 * the server's own `bucket` field — this never re-derives it from `status`.
 */
export function regenerationBucketViews(
  detail: RegenerationCampaignDetail | null | undefined,
): RegenerationBucketView[] {
  const targets = detail?.targets ?? [];
  return REGENERATION_BUCKET_ORDER.map((bucket) => {
    const rows = targets.filter((t) => t.bucket === bucket);
    return { bucket, ...REGENERATION_BUCKET_COPY[bucket], count: rows.length, targets: rows };
  });
}

/* ── stranded release: approved, but the wave never landed ───────────── */

/** The recovery action's label. Deliberately NOT an approval word: the
 *  approval already happened and there is nothing new to review. */
export const REGENERATION_RELEASE_RETRY_LABEL = "Retry the release";

export interface RegenerationStrandedRelease {
  count: number;
  targetIds: string[];
  /** One line per stranded lesson, named — never a bare UUID when the report
   *  carries the lesson. */
  lines: string[];
  /** The same lines carrying their target id. Two lessons in one campaign can
   *  legitimately share a title — different books, or a repeated "Kirish" —
   *  so the TEXT is not a usable render key. */
  rows: RegenerationReleasedFailureLine[];
  headline: string;
  /** The full promise the recovery action makes. */
  detail: string;
  actionLabel: string;
  pendingLabel: string;
  /** What `regenerationPollDecision` says when it stops for this. */
  pollReason: string;
}

/**
 * A campaign that was approved but still has lessons with no revision job.
 *
 * `approve_canary` stamps `approved_at` in one transaction and creates the
 * wave in another, and `_prepare_wave` moves a target OUT of `planned` before
 * its job exists. So `planned` + no `revision_job_id` on an approved campaign
 * means the release transaction never committed for that target. Nothing on
 * the server repairs it: the reconciler walks revision JOBS, and this target
 * has none. Re-running approve is the documented, idempotent repair.
 *
 * `null` — i.e. no recovery offered — when the campaign is finished, was never
 * approved, or is being rejected/cancelled (re-releasing would fight that),
 * and for a target the operator has already asked to abandon, because the wave
 * skips those and re-running would promise a fix that cannot happen.
 */
export function regenerationStrandedRelease(
  detail: RegenerationCampaignDetail | null | undefined,
): RegenerationStrandedRelease | null {
  if (!detail || detail.is_terminal) return null;
  if (detail.approved_at === null) return null;
  if (detail.rejected_at !== null || detail.cancel_requested_at !== null) return null;

  const stranded = detail.targets.filter(
    (t) =>
      t.status === "planned" &&
      t.revision_job_id === null &&
      !t.is_terminal &&
      t.abandon_requested_at === null,
  );
  if (stranded.length === 0) return null;

  const count = stranded.length;
  const were = plural(count, "was", "were");
  return {
    count,
    targetIds: stranded.map((t) => t.id),
    lines: stranded.map((t) => regenerationTargetLabel(detail, t.id)),
    rows: stranded.map((t) => ({
      targetId: t.id,
      text: regenerationTargetLabel(detail, t.id),
    })),
    headline: `${lessonCountLabel(count)} ${were} approved but never started`,
    detail:
      "Approval and the release are two separate steps on the server, so an approval can be " +
      "recorded with nothing released. Retrying the release is idempotent: it re-runs the same " +
      "approve call, creates nothing twice, gives no lesson a second revision job and consumes " +
      "no extra version. There is nothing new to review, and every version that generates " +
      "successfully still publishes to Notion automatically.",
    actionLabel: REGENERATION_RELEASE_RETRY_LABEL,
    pendingLabel: "Retrying the release…",
    pollReason: [
      `${lessonCountLabel(count)} ${were} approved but never got a revision job.`,
      `Nothing starts ${plural(count, "it", "them")} on its own and refreshing cannot fix it,`,
      `so this report has stopped ticking: use "${REGENERATION_RELEASE_RETRY_LABEL}" on this`,
      "campaign — it re-runs the same idempotent approve call and creates nothing twice.",
      "Any lesson that did start keeps running in the background.",
    ].join(" "),
  };
}

/** One lesson, named. The target id is the fallback, not the default. */
export function regenerationTargetLabel(
  detail: RegenerationCampaignDetail | null | undefined,
  targetId: string,
): string {
  const target = detail?.targets.find((t) => t.id === targetId);
  if (!target) return `lesson ${targetId}`;
  const number = target.lesson.section_number ? `${target.lesson.section_number}. ` : "";
  const title = target.lesson.section_title ?? target.toc_entry_id;
  return `${number}${title} (${regenerationLanguageLabel(target.output_language)})`;
}

export interface RegenerationReleasedFailureLine {
  targetId: string;
  text: string;
}

/**
 * The wave failures, as lines an operator can act on.
 *
 * `WaveFailureOut` carries only ids; the lesson titles live on the report's
 * targets, so the two are joined here rather than printing a UUID at somebody.
 */
export function regenerationReleasedFailureLines(
  detail: RegenerationCampaignDetail | null | undefined,
  failures: RegenerationWaveFailure[] | undefined,
): RegenerationReleasedFailureLine[] {
  return (failures ?? []).map((failure) => {
    const status = failure.current_status
      ? ` (now ${regenerationTargetStatusLabel(failure.current_status).toLowerCase()})`
      : "";
    return {
      targetId: failure.target_id,
      text: `${regenerationTargetLabel(detail, failure.target_id)} — ${failure.reason}${status}`,
    };
  });
}

/**
 * Keep a mutation-only payload alive across the refetch that would erase it.
 *
 * `released_failures` exists ONLY on the mutation response — `GET
 * /campaigns/{id}` never carries it — so writing the fresh report into the
 * cache and then invalidating destroys the only record of which lessons the
 * release could not start. The server's copy wins per target; anything it no
 * longer mentions is kept from the transient copy.
 */
export function mergeReleasedFailures(
  server: RegenerationWaveFailure[] | undefined,
  transient: RegenerationWaveFailure[] | undefined,
): RegenerationWaveFailure[] {
  const merged = [...(server ?? [])];
  const seen = new Set(merged.map((f) => f.target_id));
  for (const failure of transient ?? []) {
    if (seen.has(failure.target_id)) continue;
    seen.add(failure.target_id);
    merged.push(failure);
  }
  return merged;
}

/** "2026-08-20 11:00 UTC" — one timestamp format, timezone stated. */
function formatWhen(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return `${new Date(ms).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/**
 * What a retry cleared, in words.
 *
 * `retry_publication` CLEARS `publication_last_error`, the attempt count and
 * the backoff stamp; the API captures them beforehand into `previous_*`
 * precisely so the UI can still show them. Dropping that on the floor throws
 * away the only surviving record of why the retry was needed.
 *
 * `null` when there is nothing preserved — a first attempt, or a generation
 * retry, which has no publication history to clear.
 */
export function regenerationRetryAudit(
  result: RegenerationTargetActionResult | null | undefined,
): string | null {
  if (!result) return null;
  const error = result.previous_publication_error;
  const attempts = result.previous_publication_attempts ?? 0;
  const due = result.previous_publication_next_attempt_at;
  if (!error && attempts <= 0 && !due) return null;

  const parts: string[] = [];
  if (attempts > 0) {
    parts.push(`${attempts} delivery ${plural(attempts, "attempt", "attempts")} had failed`);
  }
  if (due) parts.push(`the next automatic attempt was due ${formatWhen(due)}`);
  if (error) parts.push(`the last error was: ${error}`);
  return `Before this retry: ${parts.join("; ")}.`;
}

/* ── the draft's bounded scope: book → lessons ────────────────────────── */

/** Clamp the canary to something the campaign can actually honour.
 *
 *  Applied to the STORED value, not only to the rendered one: `canary_size` is
 *  posted from state, and the server's `ge=1` refusal arrives as a raw
 *  validation payload rather than as anything an operator can act on. */
export function clampCanarySize(value: number, targetCount: number): number {
  const ceiling = Math.max(1, Math.floor(Number.isFinite(targetCount) ? targetCount : 0));
  const wanted = Number.isFinite(value) ? Math.floor(value) : 1;
  return Math.min(Math.max(1, wanted), ceiling);
}

export const REGENERATION_PICK_BOOK_HINT =
  "Pick a textbook first. Regenerable lessons are listed one book at a time — asking the " +
  "server for every lesson lineage at once is neither bounded nor readable, and two lessons " +
  "called the same thing in different books are impossible to tell apart in one flat list.";

export interface RegenerationEligibleQuery {
  enabled: boolean;
  /** Straight into `api.listRegenerationEligible`. */
  filters: { bookIds: string[] };
  blockedReason: string | null;
}

/**
 * `/eligible`, bounded to ONE book.
 *
 * With no filter the route walks every completed homework lineage in the
 * database, which is thousands of rows for a list nobody can read. The books
 * list (~246 rows) is the bounded first step, and this gate keeps the lesson
 * query switched OFF until one is chosen.
 */
export function regenerationEligibleQuery(
  bookId: string | null | undefined,
): RegenerationEligibleQuery {
  const id = (bookId ?? "").trim();
  if (!id) {
    return { enabled: false, filters: { bookIds: [] }, blockedReason: REGENERATION_PICK_BOOK_HINT };
  }
  return { enabled: true, filters: { bookIds: [id] }, blockedReason: null };
}

export interface RegenerationBookOption {
  id: string;
  title: string;
  subject: string;
  subjectLabel: string;
  grade: string | null;
  gradeLabel: string;
  /** One line that identifies the book on its own. */
  label: string;
}

function bookOption(book: Book): RegenerationBookOption {
  // Real rows are missing a grade, and a filename can be blank — neither may
  // render as "null" or as an empty chip.
  const title = (book.original_filename ?? "").trim() || `Untitled book ${book.id.slice(0, 8)}`;
  const grade = (book.grade ?? "").trim() || null;
  const gradeLabel = grade ? `Grade ${grade}` : "Grade not recorded";
  const label = `${subjectLabel(book.subject)} · ${gradeLabel} · ${title}`;
  return {
    id: book.id,
    title,
    subject: book.subject,
    subjectLabel: subjectLabel(book.subject),
    grade,
    gradeLabel,
    label,
  };
}

/** Grades sort numerically where they are numbers, and a book with no grade
 *  sorts last rather than disappearing. */
function gradeRank(grade: string | null): number {
  const n = Number.parseInt(grade ?? "", 10);
  return Number.isNaN(n) ? Number.POSITIVE_INFINITY : n;
}

export function regenerationBookOptions(
  books: Book[] | undefined,
  filters: { subject?: string | null; grade?: string | null } = {},
): RegenerationBookOption[] {
  const subject = filters.subject ?? null;
  const grade = filters.grade ?? null;
  return (books ?? [])
    .filter((b) => subject === null || b.subject === subject)
    .filter((b) => grade === null || ((b.grade ?? "").trim() || "") === grade)
    .map(bookOption)
    .sort(
      (a, b) =>
        a.subjectLabel.localeCompare(b.subjectLabel) ||
        gradeRank(a.grade) - gradeRank(b.grade) ||
        a.title.localeCompare(b.title),
    );
}

export interface RegenerationFacet {
  value: string;
  label: string;
  count: number;
}

export interface RegenerationBookFacets {
  subjects: RegenerationFacet[];
  /** Scoped to the chosen subject; `""` is the real "no grade recorded"
   *  bucket, offered rather than silently dropping those books. */
  grades: RegenerationFacet[];
}

export function regenerationBookFacets(
  books: Book[] | undefined,
  filters: { subject?: string | null } = {},
): RegenerationBookFacets {
  const all = books ?? [];
  const subjects = new Map<string, number>();
  for (const book of all) subjects.set(book.subject, (subjects.get(book.subject) ?? 0) + 1);

  const subject = filters.subject ?? null;
  const grades = new Map<string, number>();
  for (const book of all) {
    if (subject !== null && book.subject !== subject) continue;
    const key = (book.grade ?? "").trim();
    grades.set(key, (grades.get(key) ?? 0) + 1);
  }

  return {
    subjects: [...subjects.entries()]
      .map(([value, count]) => ({ value, label: subjectLabel(value), count }))
      .sort((a, b) => a.label.localeCompare(b.label)),
    grades: [...grades.entries()]
      .map(([value, count]) => ({
        value,
        label: value ? `Grade ${value}` : "No grade recorded",
        count,
      }))
      .sort((a, b) => gradeRank(a.value || null) - gradeRank(b.value || null)),
  };
}

/** The draft fields narrowing touches. The wizard's draft extends this. */
export interface RegenerationScopeState {
  subjectFilter: string | null;
  gradeFilter: string | null;
  bookId: string | null;
  language: RegenerationOutputLanguage;
  selectedTocEntryIds: string[];
  selectedPhases: string[];
  excludedPhases: string[];
  acknowledged: boolean;
  canarySize: number;
}

export interface RegenerationScopeChange {
  subjectFilter?: string | null;
  gradeFilter?: string | null;
  bookId?: string | null;
  language?: RegenerationOutputLanguage;
}

/**
 * Narrow the draft, clearing exactly what the change invalidated.
 *
 * A selected lesson belongs to one book in one language; a phase list belongs
 * to one subject's flow. Leaving either behind posts a campaign the operator
 * never composed. Re-picking the same value changes nothing at all — a no-op
 * click must not wipe somebody's work.
 */
export function regenerationNarrowScope<T extends RegenerationScopeState>(
  state: T,
  change: RegenerationScopeChange,
): T {
  // Selection always dies with the scope; the canary can never outlive it.
  const dropSelection = { selectedTocEntryIds: [], acknowledged: false, canarySize: 1 };
  const dropPhases = { selectedPhases: [], excludedPhases: [] };
  // The cast is the documented TS limitation on spreading a generic: every
  // patch below only ever narrows fields declared on RegenerationScopeState.
  const patched = (over: Partial<RegenerationScopeState>): T => ({ ...state, ...over }) as T;

  if ("subjectFilter" in change && change.subjectFilter !== state.subjectFilter) {
    return patched({
      subjectFilter: change.subjectFilter ?? null,
      // A grade and a book from the previous subject mean nothing here.
      gradeFilter: null,
      bookId: null,
      ...dropPhases,
      ...dropSelection,
    });
  }
  if ("gradeFilter" in change && change.gradeFilter !== state.gradeFilter) {
    // Same subject, same flow: the phase ticks survive.
    return patched({ gradeFilter: change.gradeFilter ?? null, bookId: null, ...dropSelection });
  }
  if ("bookId" in change && change.bookId !== state.bookId) {
    // Another book may be another subject, so the phase list goes too.
    return patched({ bookId: change.bookId ?? null, ...dropPhases, ...dropSelection });
  }
  if (change.language !== undefined && change.language !== state.language) {
    // One book carries every language, so only the lessons are cleared.
    return patched({ language: change.language, ...dropSelection });
  }
  return state;
}

export const REGENERATION_LANGUAGE_LABELS: Record<RegenerationOutputLanguage, string> = {
  uz: "Uzbek",
  ru: "Russian",
  en: "English",
};

export function regenerationLanguageLabel(language: string): string {
  return REGENERATION_LANGUAGE_LABELS[language as RegenerationOutputLanguage] ?? language;
}

export interface RegenerationSourceRow {
  key: string;
  tocEntryId: string;
  /** "1. Kirish" — the section number is what tells two "Kirish" apart. */
  headline: string;
  bookLine: string;
  contextLine: string;
  versionText: string;
  languageLabel: string;
  noPageWarning: string | null;
  /** Everything the row renders, joined — the thing a filter box matches and
   *  the thing the test asserts nothing was dropped from. */
  searchText: string;
}

/**
 * One selectable lesson, fully identified.
 *
 * `EligibleSourceOut` has no book title, so the book is joined in from the
 * books list. Every axis that can distinguish two identically-titled lessons
 * is rendered: book, subject, grade, chapter, section number, language and
 * the version this regeneration would move it from and to.
 */
export function regenerationSourceRow(
  source: RegenerationEligibleSource,
  book: RegenerationBookOption | undefined,
): RegenerationSourceRow {
  const number = source.section_number ? `${source.section_number}. ` : "";
  const headline = `${number}${source.section_title}`.trim() || source.toc_entry_id;
  const grade = (source.grade ?? "").trim();
  const bookLine = book
    ? book.label
    : `${subjectLabel(source.subject)} · ${grade ? `Grade ${grade}` : "Grade not recorded"} · ` +
      `Book ${source.book_id.slice(0, 8)}`;
  const languageLabel = regenerationLanguageLabel(source.output_language);
  const versionText = `V${source.source_publication_version} → V${source.next_expected_version}`;
  const chapter = source.chapter_title.trim() || "no chapter recorded";
  const revisionNote = source.source_is_revision
    ? " · the source is itself a regenerated version, not the original"
    : "";
  const contextLine = `Chapter: ${chapter} · ${languageLabel} · ${versionText}${revisionNote}`;
  return {
    key: `${source.toc_entry_id}:${source.output_language}`,
    tocEntryId: source.toc_entry_id,
    headline,
    bookLine,
    contextLine,
    versionText,
    languageLabel,
    noPageWarning: source.has_notion_lesson_page
      ? null
      : "No Lesson Topic page is known for this lesson yet; the canary preflight will try to resolve one.",
    searchText: [headline, bookLine, contextLine].join(" · "),
  };
}

/* ── polling ──────────────────────────────────────────────────────────── */

export interface RegenerationPollDecision {
  shouldPoll: boolean;
  /** Straight into TanStack Query's `refetchInterval`. */
  intervalMs: number | false;
  /** What is moving right now, in operator words. Empty when nothing is. */
  activity: string[];
  /** Why we are, or are not, refreshing. */
  reason: string;
}

/** Campaign states in which the backend itself still has work to hand out, even
 *  if no individual target looks busy this instant. */
const REGENERATION_RELEASING_STATUSES = new Set<RegenerationCampaignStatus>([
  "canary_running",
  "bulk_running",
]);

/**
 * Poll only while something can actually change without the operator.
 *
 * Generation, publication and the publisher's own bounded retries all move on
 * their own, so a frozen report would read as stuck. A terminal campaign, a
 * canary waiting at the human gate, and a target parked on `action_required`
 * cannot change until somebody acts — refreshing those is pure request burn,
 * and on `awaiting_canary_approval` it would poll for as long as the tab is
 * open.
 *
 * A STRANDED RELEASE is checked before any of that, including before the
 * campaign-status line. `bulk_running` is derived from target statuses and
 * `planned` counts as in flight, so a campaign whose release never landed
 * looks busy at the status level while being permanently stuck at the target
 * level: this used to poll forever behind "the campaign is still releasing
 * revision jobs", which is a claim the data contradicts. It wins over
 * observed target work too — a partial release is the realistic shape of this
 * failure, and a lesson that can never start must not stay hidden behind the
 * lessons that did.
 */
export function regenerationPollDecision(
  detail: RegenerationCampaignDetail | null | undefined,
): RegenerationPollDecision {
  if (!detail) {
    return { shouldPoll: false, intervalMs: false, activity: [], reason: "No campaign is open." };
  }
  const stopped = (reason: string): RegenerationPollDecision => ({
    shouldPoll: false,
    intervalMs: false,
    activity: [],
    reason,
  });

  if (detail.is_terminal) {
    return stopped(
      `This campaign is ${regenerationCampaignStatusLabel(detail.status).toLowerCase()}; the report does not change on its own any more.`,
    );
  }

  // Approval and the bulk release are two transactions, so an approval can be
  // recorded with nothing released. Polling that forever would never fix it;
  // re-running the release does, and it creates nothing twice.
  const stranded = regenerationStrandedRelease(detail);
  if (stranded !== null) return stopped(stranded.pollReason);

  const targets = detail.targets;
  const tally = (predicate: (t: RegenerationTargetReport) => boolean): number =>
    targets.filter(predicate).length;

  const activity: string[] = [];
  const generating = tally((t) => t.status === "generating");
  if (generating > 0) activity.push(`${lessonCountLabel(generating)} regenerating`);
  const queued = tally((t) => t.status === "publication_pending");
  if (queued > 0) activity.push(`${lessonCountLabel(queued)} queued to publish`);
  const publishing = tally((t) => t.status === "publishing");
  if (publishing > 0) activity.push(`${lessonCountLabel(publishing)} publishing now`);
  const autoRetry = tally(
    (t) => t.publication_state === "backing_off" || t.publication_state === "retry_due",
  );
  if (autoRetry > 0) {
    activity.push(`${lessonCountLabel(autoRetry)} waiting on an automatic publish retry`);
  }
  if (REGENERATION_RELEASING_STATUSES.has(detail.status)) {
    activity.push("the campaign is still releasing revision jobs");
  }

  if (activity.length > 0) {
    return {
      shouldPoll: true,
      intervalMs: REGENERATION_POLL_MS,
      activity,
      reason: `Refreshing while ${activity.join(", ")}.`,
    };
  }

  const needsYou = tally((t) => t.action_required);
  if (needsYou > 0) {
    return stopped(
      `${lessonCountLabel(needsYou)} ${plural(needsYou, "needs", "need")} a decision from you. Nothing moves until you retry or abandon, so this report is not refreshing.`,
    );
  }

  if (detail.status === "awaiting_canary_approval") {
    return stopped(
      "The canary is ready and waiting for your review. Nothing changes until you approve or " +
        "reject it.",
    );
  }

  if (detail.status === "draft") {
    return stopped(
      "Nothing has been regenerated yet. This campaign is frozen and free until you generate " +
        "the canary.",
    );
  }

  return stopped("Nothing is in flight for this campaign.");
}

/** The list ticks only while a campaign is actually working. */
export function regenerationListPollMs(
  campaigns: RegenerationCampaignSummary[] | undefined,
): number | false {
  const busy = (campaigns ?? []).some(
    (c) => !c.is_terminal && REGENERATION_RELEASING_STATUSES.has(c.status),
  );
  return busy ? REGENERATION_LIST_POLL_MS : false;
}

/* ── the single campaign-level approval gate ──────────────────────────── */

const REGENERATION_NO_BULK_STEP_DETAIL =
  "The canary already covers every lesson in this campaign, so there is " +
  "no separate bulk step — approving publishes the packet you just reviewed.";

/** The version a target will publish as: the one already reserved, or the one
 *  after its source. Never hardcoded — a V2 source publishes V3. */
export function regenerationNextVersion(target: RegenerationTargetReport): number | null {
  if (target.publication_version != null) return target.publication_version;
  if (target.source_publication_version != null) return target.source_publication_version + 1;
  return null;
}

/**
 * Exactly one gate per campaign, over real API rows.
 *
 * A ONE-LESSON campaign is its own canary: no bulk step and no remainder,
 * whatever `canary_size` says. A multi-lesson campaign whose canary covers
 * every target takes the same no-bulk path — that second branch is why the
 * predicate is not simply `singleTarget`, and why an EMPTY bulk gate can never
 * render.
 *
 * This restates `regeneration-state.approvalGate` rather than calling it: that
 * function takes `PlanTarget`, whose `language` cannot express `"en"`, and
 * building one would mislabel an English lesson as Uzbek. The state test
 * asserts the two produce identical gates for a campaign both shapes can carry.
 */
export function regenerationApprovalGate(detail: RegenerationCampaignDetail): ApprovalGate {
  const targetCount = detail.targets.length;
  const singleTarget = targetCount === 1;
  const remainingCount = singleTarget ? 0 : Math.max(0, targetCount - detail.canary_size);
  const showsBulkGenerationGate = !singleTarget && remainingCount > 0;

  let approveLabel: string;
  let approveDetail: string;
  if (singleTarget) {
    const version = regenerationNextVersion(detail.targets[0]);
    approveLabel = version
      ? `Approve canary and publish V${version}`
      : "Approve canary and publish the next version";
    approveDetail = REGENERATION_NO_BULK_STEP_DETAIL;
  } else if (targetCount === 0) {
    approveLabel = "Nothing to approve";
    approveDetail =
      "This campaign has no lessons, so there is nothing to publish and " +
      "no separate bulk step.";
  } else if (!showsBulkGenerationGate) {
    approveLabel = `Approve canary and publish ${lessonCountLabel(targetCount)}`;
    approveDetail = REGENERATION_NO_BULK_STEP_DETAIL;
  } else {
    approveLabel = `Approve canary and regenerate ${remainingCount} remaining ${plural(
      remainingCount,
      "lesson",
      "lessons",
    )}`;
    approveDetail = `Approving publishes the reviewed canary and releases the remaining ${remainingCount} ${plural(remainingCount, "lesson", "lessons")}. This is the only approval gate in the campaign; there is no per-lesson publication approval.`;
  }

  return {
    singleTarget,
    remainingCount,
    showsBulkGenerationGate,
    canApprove: targetCount > 0,
    approveLabel,
    approveDetail,
    rejectLabel: "Reject campaign",
    rejectDetail:
      "Rejecting discards the canary: no Notion page is created and " +
      "no publication version is consumed.",
  };
}

/* ── per-target actions ───────────────────────────────────────────────── */

export type RegenerationActionKind = "retry-generation" | "retry-publication" | "abandon";

export interface RegenerationTargetAction {
  kind: RegenerationActionKind;
  label: string;
  /** The promise the button makes, in full. */
  detail: string;
  enabled: boolean;
  disabledReason: string | null;
  /** Abandon is audited: the API refuses a blank reason. */
  requiresReason: boolean;
}

const REGENERATION_ACTION_COPY: Record<
  RegenerationActionKind,
  { label: string; detail: string; requiresReason: boolean }
> = {
  "retry-generation": {
    label: "Retry regeneration",
    detail:
      "Re-runs the revision on the snapshot and phase plan this campaign froze. It does not " +
      "start a new campaign, re-plan the phases, or change which version this lesson will " +
      "publish as.",
    requiresReason: false,
  },
  "retry-publication": {
    label: "Retry publication",
    detail:
      "Re-queues the Notion write only. No Gemini call, no new revision job and no new version " +
      "number: the same reserved version and the same page identity are used again.",
    requiresReason: false,
  },
  abandon: {
    label: "Abandon lesson",
    detail:
      "Gives up on this lesson and records your reason. No Notion page is deleted. If a version " +
      "was already reserved it stays consumed and may remain unused, so the next successful " +
      "regeneration of this lesson publishes at the following version.",
    requiresReason: true,
  },
};

/**
 * What an operator may do to one target right now.
 *
 * Generation and publication failures are separate, retryable states with
 * separate paths — a generated revision is never regenerated because Notion
 * failed. Terminal targets offer nothing. While one of a target's mutations is
 * in flight every button on that row is disabled: the API is idempotent, so
 * this is about not lying to the operator rather than about safety.
 */
export function regenerationTargetActions(
  target: RegenerationTargetReport,
  opts: { pendingKind?: RegenerationActionKind | null; campaignTerminal?: boolean } = {},
): RegenerationTargetAction[] {
  if (target.is_terminal) return [];

  const kinds: RegenerationActionKind[] = [];
  if (target.status === "generation_failed") kinds.push("retry-generation");
  if (target.status === "publication_failed") kinds.push("retry-publication");
  kinds.push("abandon");

  const pending = opts.pendingKind ?? null;
  const disabledReason = pending
    ? "This lesson already has an action in flight — waiting for the server to answer."
    : opts.campaignTerminal
      ? "This campaign is finished, so its lessons can no longer be changed."
      : null;

  return kinds.map((kind) => ({
    kind,
    ...REGENERATION_ACTION_COPY[kind],
    enabled: disabledReason === null,
    disabledReason,
  }));
}

/** Reject / cancel / abandon store their reason as the audit record, so the
 *  API refuses a blank one. Catch it before the round trip. */
export function regenerationReasonError(reason: string): string | null {
  return reason.trim()
    ? null
    : "Type a reason — it is stored as the audit record for this decision.";
}

/* ── phase plan ───────────────────────────────────────────────────────── */

/**
 * The server's plan, in the shape the Task 4 cascade rules read.
 *
 * `canonical_phases` starts with `extract` and is partitioned exactly by
 * `regenerated_phases` + `copied_phases`, so the counts this produces are the
 * server's counts — asserted against them in `regeneration-state.test.ts`.
 */
export function phaseSelectionFromPlan(plan: RegenerationPhasePlan): PhaseSelection {
  return {
    allPhases: plan.canonical_phases,
    selected: plan.selected_phases,
    autoIncluded: plan.auto_included_phases,
    excluded: plan.excluded_affected_phases,
    extractionEnabled: plan.refresh_extraction,
  };
}

/** "Regenerates X of Y phases" and the honesty note under it, from the real
 *  dependency closure the planner returned. */
export function cascadeFromPlan(plan: RegenerationPhasePlan): CascadeSummary {
  return cascadeDisclosure(phaseSelectionFromPlan(plan));
}

/* ── judge / solver signals ───────────────────────────────────────────── */

export interface RegenerationJudgeCount {
  status: string;
  count: number;
  signal: JudgeSignal;
}

/** Every judge status this build knows, as a value list — so an API string can
 *  be matched against it without an unchecked cast. */
const REGENERATION_KNOWN_JUDGE_STATUSES: readonly JudgeStatus[] = [
  "pass",
  "unavailable",
  "refused",
  "major_shipped",
  "major_regen_failed",
];

/**
 * `judge_status_counts` rendered as prominent, non-blocking warnings.
 *
 * Soft verdicts do not block publication and never have — they are surfaced
 * loudly at the canary gate precisely because the machine will not stop for
 * them. A verdict this build does not recognise is spelled out rather than
 * dropped.
 */
/** A verdict this build has no mapping for is spelled out and treated as a
 *  warning — never dropped, and never echoed as a raw token. */
function unknownJudgeSignal(status: string): JudgeSignal {
  return {
    status: "unavailable",
    severity: "warning",
    blocksPublication: false,
    label: humanise(status),
    explanation:
      "The judge reported a verdict this build does not recognise. Read this phase by hand " +
      "before approving.",
  };
}

export function regenerationJudgeCounts(
  counts: Record<string, number> | undefined,
): RegenerationJudgeCount[] {
  return Object.entries(counts ?? {})
    .filter(([, count]) => count > 0)
    .map(([status, count]) => {
      const known = REGENERATION_KNOWN_JUDGE_STATUSES.find((s) => s === status);
      return {
        status,
        count,
        signal: known ? judgeSignal(known) : unknownJudgeSignal(status),
      };
    })
    .sort((a, b) => a.status.localeCompare(b.status));
}

/* ── errors ───────────────────────────────────────────────────────────── */

export interface RegenerationErrorView {
  title: string;
  message: string;
  /** One line per affected lesson / role / field, so an operator fixes the
   *  whole batch once instead of discovering them one round trip at a time. */
  details: string[];
  hint: string | null;
  code: string | null;
  status: number | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  return typeof value === "string" ? value : "";
}

function rowsOf(record: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const value = record[key];
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

/** One `{loc, msg}` row of a FastAPI validation error, as a sentence.
 *
 *  `loc` is a tuple like `["body", "contract", "model"]`; the transport prefix
 *  (`body`/`query`/`path`) means nothing to an operator, and an array index is
 *  spelled out rather than shown as a bare number. */
function validationLine(row: Record<string, unknown>): string {
  const loc = Array.isArray(row.loc) ? row.loc : [];
  const parts = loc
    .filter((p): p is string | number => typeof p === "string" || typeof p === "number")
    .map(String)
    .filter((p) => p !== "body" && p !== "query" && p !== "path" && p !== "header")
    .map((p) => (/^\d+$/.test(p) ? `item ${Number(p) + 1}` : humanise(p)));
  const field = parts.length > 0 ? parts.join(" → ") : "This request";
  return `${field} — ${text(row, "msg") || "is not valid"}`;
}

const REGENERATION_STALE_HINT =
  "The campaign moved on while this screen was open — refresh to see where it is now.";

/**
 * Any thrown value, rendered for an operator.
 *
 * The router raises `HTTPException(status, {"error", "message", ...})`, so the
 * structured payload — every blocked lesson, every retired role, every
 * offending transport field — arrives on `ApiError.detail`. Showing only
 * `message` throws away the list somebody has to act on. Two codes get their
 * server text REPLACED rather than shown: the flag-off 404, whose real message
 * is the word "Not Found", and the acknowledgement 422, whose message names a
 * request field instead of the tick box on screen.
 */
export function regenerationErrorView(err: unknown): RegenerationErrorView {
  const status = err instanceof ApiError ? err.status : null;
  const fallback = err instanceof Error ? err.message : String(err);
  const detail = err instanceof ApiError && isRecord(err.detail) ? err.detail : null;
  // A schema-level 422 carries a LIST, not `{error, message}` — so `unwrap`
  // could find no message and fell back to the raw response text. Rendering
  // that puts a JSON payload on screen.
  const invalidFields =
    err instanceof ApiError && Array.isArray(err.detail) ? err.detail.filter(isRecord) : null;
  const code = detail ? text(detail, "error") || null : null;
  const message = detail ? text(detail, "message") || fallback : fallback;

  if (status === 404 && !code) {
    return {
      title: "Regeneration is switched off on the server",
      message:
        "This build shows the regeneration screens, but the server is running with " +
        "REGENERATION_ENABLED=false, so every regeneration route answers as if it does not " +
        "exist. The backend flag is the real gate; turning it on is a deployment decision, not " +
        "a UI one.",
      details: [],
      hint: null,
      code: null,
      status,
    };
  }

  if (invalidFields !== null) {
    return {
      title: "That request was rejected before it ran",
      message:
        "The server refused this request because some of its fields are not valid. Nothing was " +
        "created, nothing was spent and nothing was published.",
      details: invalidFields.map(validationLine),
      hint:
        invalidFields.length === 0
          ? "The server did not say which field it objected to; re-check the draft and try again."
          : null,
      code: null,
      status,
    };
  }

  const base = { message, details: [] as string[], hint: null as string | null, code, status };

  switch (code) {
    case "publisher_disabled":
      return {
        ...base,
        title: "Automatic publishing is switched off",
        hint: "Nothing was changed by this request.",
      };
    case "preflight_blocked":
      return {
        ...base,
        title: "These lessons have nowhere to publish",
        details: rowsOf(detail ?? {}, "failures").map(
          (row) =>
            `${text(row, "lesson_title") || text(row, "toc_entry_id")} (${text(row, "output_language")}) — ` +
            `${humanise(text(row, "reason"))}: ${text(row, "detail")}`,
        ),
        hint: "Fix the Notion mapping, then generate the canary again. No money was spent.",
      };
    case "retired_model":
      return {
        ...base,
        title: "This campaign is pinned to a retired model",
        details: rowsOf(detail ?? {}, "retired").map(
          (row) =>
            `${text(row, "role")} is pinned to ${text(row, "provider")}/${text(row, "model")}, which no longer answers`,
        ),
        hint: "A frozen campaign cannot be re-pointed; create a new one on a live model.",
      };
    case "active_lineage_conflict":
      return {
        ...base,
        title: "Another campaign still holds these lessons",
        details: rowsOf(detail ?? {}, "lineages").map(
          (row) =>
            `lesson ${text(row, "toc_entry_id")}, output language ${text(row, "output_language")}`,
        ),
        hint:
          "A lesson can only be in one live regeneration at a time. Finish, retry or abandon " +
          "the other campaign's target first.",
      };
    case "no_eligible_targets":
      return {
        ...base,
        title: "None of these lessons can be regenerated",
        details: rowsOf(detail ?? {}, "candidates").map((row) => {
          const reasons = Array.isArray(row.reasons)
            ? row.reasons
                .filter((r): r is string => typeof r === "string")
                .map(humanise)
                .join("; ")
            : "";
          const why = [reasons, text(row, "detail")].filter(Boolean).join(" — ");
          return `lesson ${text(row, "toc_entry_id")} (${text(row, "output_language")}): ${why}`;
        }),
        hint: "A source must be a complete, finished homework job in that language.",
      };
    case "non_api_transport":
      return {
        ...base,
        title: "Regeneration runs over the api transport only",
        details: rowsOf(detail ?? {}, "offenders").map(
          (row) => `${text(row, "field")} resolves to ${text(row, "transport")}`,
        ),
        hint: "Pick api for the content transport and for every role that inherits it.",
      };
    case "exclusion_acknowledgement_required":
      return {
        ...base,
        title: "Acknowledge the consistency warning first",
        message:
          "Some phases you dropped sit downstream of phases you are regenerating, so their " +
          "current text would ship beside newly rebuilt upstream phases and the homework may " +
          "read inconsistently. Tick the acknowledgement box and try again.",
        hint: null,
      };
    case "illegal_campaign_state":
    case "illegal_target_state":
      return { ...base, title: "That is not possible from here", hint: REGENERATION_STALE_HINT };
    case "unknown_phase":
    case "unknown_subject":
    case "invalid_phase_selection":
    case "unknown_output_language":
    case "unknown_campaign_status":
      return { ...base, title: "That selection is not valid", hint: null };
    case "unresolvable_contract":
    case "invalid_campaign_draft":
      return {
        ...base,
        title: "This campaign cannot be frozen as configured",
        hint:
          "Every role needs a concrete provider and model, and the content model is never " +
          "guessed for you.",
      };
    default:
      return {
        ...base,
        title: status === null ? "The request did not reach the server" : "The request failed",
        hint: status === 409 ? REGENERATION_STALE_HINT : null,
      };
  }
}
