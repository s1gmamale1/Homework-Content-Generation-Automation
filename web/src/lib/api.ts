import { clearToken, getToken } from "./auth";
import {
  cascadeDisclosure,
  costComparison,
  formatUsd,
  judgeSignal,
  lessonCountLabel,
} from "./regeneration-state";
import type {
  ApprovalGate,
  CascadeSummary,
  CostComparison,
  JudgeSignal,
  JudgeStatus,
  PhaseSelection,
} from "./regeneration-state";
import { subjectLabel } from "./subjects";
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
  JobStatus,
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
  RegenerationIneligibleLineage,
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

  /**
   * The COMPLETE library, in one bounded statement.
   *
   * `list_books(limit=100, offset=0)` answers the first 100 rows to a caller
   * that names no limit, so `listBooks` hides most of a ~246-book library —
   * fine for a paged Library screen, fatal for a picker whose whole job is
   * "choose any book". Paging would walk an offset window that moves under an
   * upload landing mid-walk; one over-sized read cannot. The extra row is the
   * tripwire: a library that outgrew the picker has to say so rather than hand
   * back a quietly truncated list.
   *
   * Cached under its own `["books", "all"]` key, so Fleet's and Library's
   * 100-row `["books"]` entry can never overwrite it.
   */
  async listAllBooks(): Promise<Book[]> {
    const rows = await unwrap<Book[]>(await authFetch("/api/v1/books?limit=2001&offset=0"));
    if (rows.length > 2000) {
      throw new Error("Book library exceeded the guided picker safety limit of 2000 rows");
    }
    return rows;
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
/** A stable fingerprint of everything a create refusal could be ABOUT.
 *
 *  `useMutation` keeps `error` until the next `mutate()`, so an
 *  `active_lineage_conflict` naming three lessons stays on screen after the
 *  operator has deselected them — describing a draft that no longer exists.
 *  Comparing this signature against the one captured when the request was sent
 *  is what scopes the error to its own draft.
 *
 *  Deliberately covers only the fields the server reads: a cosmetic state
 *  change must not silently discard a refusal the operator has not addressed.
 */
export function regenerationDraftSignature(draft: {
  bookId: string | null;
  language: string;
  selectedTocEntryIds: string[];
  selectedPhases: string[];
  excludedPhases: string[];
  refreshExtraction: boolean;
  acknowledged: boolean;
  canarySize: number;
  provider: string;
  model: string | null;
}): string {
  return JSON.stringify([
    draft.bookId,
    draft.language,
    [...draft.selectedTocEntryIds].sort(),
    [...draft.selectedPhases].sort(),
    [...draft.excludedPhases].sort(),
    draft.refreshExtraction,
    draft.acknowledged,
    draft.canarySize,
    draft.provider,
    draft.model,
  ]);
}

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
/** What the campaign list header may honestly claim.
 *
 *  `GET /campaigns` is paged (`limit` defaults to 50) and returns its own
 *  `count` — the total matching the filter. Labelling the rendered array
 *  "N total" turns a capped first page into a claim about the whole database,
 *  which is exactly the number an operator uses to decide nothing is missing.
 *
 *  `count: null` is a failed read: cached rows may still be on screen, but
 *  their number says nothing about the total.
 */
export function regenerationCampaignCountLabel(input: {
  shown: number;
  count: number | null;
  limit: number;
  offset: number;
}): string {
  if (input.count == null) return "unknown";
  if (input.shown >= input.count && input.offset === 0) return `${input.count} total`;
  if (input.offset > 0) {
    const first = input.offset + 1;
    const last = input.offset + input.shown;
    return `${first}\u2013${last} of ${input.count}`;
  }
  return `${input.shown} of ${input.count}`;
}

export const REGENERATION_RELEASE_RETRY_LABEL = "Retry the release";
export const REGENERATION_CANARY_RETRY_LABEL = "Retry canary generation";

/** WHICH release stalled. The two are recovered by different endpoints, and
 *  each refuses the other's phase, so this is not decoration. */
export type RegenerationStrandedPhase = "canary" | "bulk";

/** The mutation that repairs it: `POST /campaigns/{id}/canary` before
 *  approval, `POST /campaigns/{id}/approve` after. */
export type RegenerationStrandedAction = "launch-canary" | "approve";

export interface RegenerationStrandedRelease {
  phase: RegenerationStrandedPhase;
  action: RegenerationStrandedAction;
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
  /** One sentence naming the stranded lessons and the recovery, for a report
   *  that is STILL refreshing because other lessons are genuinely moving. */
  pollNote: string;
  /** What `regenerationPollDecision` says when it stops for this. */
  pollReason: string;
}

/** A target the server promised a revision job and never gave one.
 *
 *  BOTH statuses count. `_prepare_wave` moves a target `planned -> generating`
 *  and COMMITS, and `_create_wave` creates the job afterwards in a separate
 *  session per target — so the crash window spans both sides of that commit,
 *  and a `generating` row with no `revision_job_id` is just the same failure
 *  one transaction later. Reading only `planned` left that half invisible, and
 *  worse: it was counted as a lesson busy regenerating, so the report polled it
 *  forever and no control offered to start it.
 *
 *  `retry_generation` accepts exactly these two statuses
 *  (`_CREATABLE_TARGET_STATUSES`), which is what makes the row-level recovery
 *  in `regenerationTargetActions` real rather than hopeful. */
function isJoblessTarget(t: RegenerationTargetReport): boolean {
  return (
    (t.status === "planned" || t.status === "generating") &&
    t.revision_job_id === null &&
    !t.is_terminal &&
    t.abandon_requested_at === null
  );
}

/**
 * A campaign that was launched but still has lessons with no revision job.
 *
 * Neither release is one transaction. `approve_canary` stamps `approved_at`
 * and commits, moves the targets to `generating` and commits again, and only
 * then creates the revision jobs — one session each. `launch_canary` has the
 * same seam for the canary targets. Dying anywhere in that sequence leaves a
 * target at `planned` OR `generating` with no job, and nothing on the server
 * repairs it: the reconciler walks revision JOBS, and this target has none.
 *
 * WHICH repair depends on the phase, and they are not interchangeable:
 * `launch_canary` REFUSES an approved campaign ("the bulk wave is released by
 * approve_canary, not by a relaunch"), so offering the canary retry after
 * approval would be a guaranteed 409, and offering approve before the canary
 * has run would jump the one human gate. The phase is therefore read off the
 * launch stamps, not guessed from the status.
 *
 * Before approval only the CANARY targets were promised a job — the rest are
 * `planned` with no job BY DESIGN, waiting at the gate — so the pre-approval
 * scan is restricted to `is_canary`. Without that restriction this would fire
 * on every healthy multi-lesson campaign.
 *
 * `null` — i.e. no recovery offered — for an UNTOUCHED campaign (neither
 * stamp: nothing was ever promised), a finished one, one being
 * rejected/cancelled (re-releasing would fight that), and for a target the
 * operator has already asked to abandon, because the wave skips those and
 * re-running would promise a fix that cannot happen.
 *
 * ACCEPTED IMPRECISION: `_create_wave` creates the jobs one at a time, so for
 * the moments between the commit and the last job a healthy release genuinely
 * looks like this. The note is still literally true at that instant, the
 * recovery is idempotent, and any lesson that already has its job keeps the
 * report polling. Erring this way costs a transient warning; erring the other
 * way is the permanent freeze this predicate exists to end.
 */
export function regenerationStrandedRelease(
  detail: RegenerationCampaignDetail | null | undefined,
): RegenerationStrandedRelease | null {
  if (!detail || detail.is_terminal) return null;
  if (detail.rejected_at !== null || detail.cancel_requested_at !== null) return null;

  const approved = detail.approved_at !== null;
  // No launch has been attempted at all: an untouched draft owes no lesson a
  // job, and calling it stranded would warn on every campaign before its
  // first click.
  if (!approved && detail.canary_launched_at === null) return null;
  const phase: RegenerationStrandedPhase = approved ? "bulk" : "canary";

  const stranded = detail.targets.filter(
    (t) => isJoblessTarget(t) && (phase === "bulk" || t.is_canary),
  );
  if (stranded.length === 0) return null;

  const count = stranded.length;
  const were = plural(count, "was", "were");
  const it = plural(count, "it", "them");
  const common = {
    phase,
    count,
    targetIds: stranded.map((t) => t.id),
    lines: stranded.map((t) => regenerationTargetLabel(detail, t.id)),
    rows: stranded.map((t) => ({
      targetId: t.id,
      text: regenerationTargetLabel(detail, t.id),
    })),
  };

  if (phase === "canary") {
    return {
      ...common,
      action: "launch-canary",
      headline: `${lessonCountLabel(count)} ${were} launched but never started`,
      detail: [
        "Starting the canary and creating its revision job are two separate steps on the",
        "server, so a canary can be recorded as started with nothing running behind it.",
        `${REGENERATION_CANARY_RETRY_LABEL} is idempotent: it re-runs the same launch, gives no`,
        "lesson a second revision job and consumes no extra version. It is not a decision about",
        "the canary — nothing has been generated yet, so there is nothing to read and nothing",
        "to decline.",
      ].join(" "),
      actionLabel: REGENERATION_CANARY_RETRY_LABEL,
      pendingLabel: "Retrying canary generation…",
      pollNote: [
        `${lessonCountLabel(count)} ${were} launched but never got a revision job;`,
        `refreshing cannot start ${it} —`,
        `use "${REGENERATION_CANARY_RETRY_LABEL}" on this campaign.`,
      ].join(" "),
      pollReason: [
        `${lessonCountLabel(count)} ${were} launched but never got a revision job.`,
        `Nothing starts ${it} on its own and refreshing cannot fix it, so this report has`,
        `stopped ticking: use "${REGENERATION_CANARY_RETRY_LABEL}" on this campaign — it`,
        "re-runs the same idempotent launch and creates nothing twice.",
      ].join(" "),
    };
  }

  return {
    ...common,
    action: "approve",
    headline: `${lessonCountLabel(count)} ${were} approved but never started`,
    detail:
      "Approval and the release are two separate steps on the server, so an approval can be " +
      "recorded with nothing released. Retrying the release is idempotent: it re-runs the same " +
      "approve call, creates nothing twice, gives no lesson a second revision job and consumes " +
      "no extra version. There is nothing new to review, and every version that generates " +
      "successfully still publishes to Notion automatically.",
    actionLabel: REGENERATION_RELEASE_RETRY_LABEL,
    pendingLabel: "Retrying the release…",
    pollNote: [
      `${lessonCountLabel(count)} ${were} approved but never got a revision job;`,
      `refreshing cannot start ${it} —`,
      `use "${REGENERATION_RELEASE_RETRY_LABEL}" on this campaign.`,
    ].join(" "),
    pollReason: [
      `${lessonCountLabel(count)} ${were} approved but never got a revision job.`,
      `Nothing starts ${it} on its own and refreshing cannot fix it,`,
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
/**
 * Tick or untick one lesson, and keep the STORED canary size honest.
 *
 * `canary_size` is posted from state, and the server's refusal is a bare
 * `ge=1`/`le=target_count` validation payload rather than anything an operator
 * can act on — so a canary of 3 left behind by deselecting down to 2 lessons
 * has to be corrected here, at the event that shrank it. Doing it in an effect
 * instead would write state during render and risk a loop; doing it only at
 * the POST boundary leaves the number on screen disagreeing with the number
 * that gets sent.
 *
 * Only the lesson list and the canary move. A lesson selection says nothing
 * about the phase plan, the exclusion acknowledgement or the extract refresh,
 * so all three survive untouched — unlike `regenerationNarrowScope`, where a
 * new book really can mean a new subject's flow.
 */
export function regenerationToggleLesson<T extends RegenerationScopeState>(
  state: T,
  tocEntryId: string,
): T {
  const selectedTocEntryIds = state.selectedTocEntryIds.includes(tocEntryId)
    ? state.selectedTocEntryIds.filter((v) => v !== tocEntryId)
    : [...state.selectedTocEntryIds, tocEntryId];
  return {
    ...state,
    selectedTocEntryIds,
    canarySize: clampCanarySize(state.canarySize, selectedTocEntryIds.length),
  };
}

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
  /** Non-null whenever this campaign has lessons that were approved but never
   *  got a revision job — whether or not the report is still refreshing. */
  strandedNote: string | null;
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
    return {
      shouldPoll: false,
      intervalMs: false,
      activity: [],
      strandedNote: null,
      reason: "No campaign is open.",
    };
  }
  // Approval and the bulk release are two transactions, so an approval can be
  // recorded with nothing released. Nothing on the server repairs that, and
  // refreshing certainly cannot — but it is a property of SOME lessons, not of
  // the campaign, so it is carried alongside every decision below rather than
  // short-circuiting them.
  const stranded = regenerationStrandedRelease(detail);
  const strandedNote = stranded?.pollNote ?? null;
  const stopped = (reason: string): RegenerationPollDecision => ({
    shouldPoll: false,
    intervalMs: false,
    activity: [],
    strandedNote,
    reason,
  });

  if (detail.is_terminal) {
    return stopped(
      `This campaign is ${regenerationCampaignStatusLabel(detail.status).toLowerCase()}; the report does not change on its own any more.`,
    );
  }

  const targets = detail.targets;
  const tally = (predicate: (t: RegenerationTargetReport) => boolean): number =>
    targets.filter(predicate).length;

  const activity: string[] = [];
  // `generating` alone is NOT evidence of work: `_prepare_wave` writes that
  // status and COMMITS before `_create_wave` gives the target its job, so a
  // crashed release leaves rows that say "regenerating" with nothing behind
  // them. Counting those was what kept a permanently dead report ticking, and
  // what hid the recovery behind a lesson that was never going to move. The
  // job id is the only evidence the report carries that something real was
  // queued.
  const generating = tally((t) => t.status === "generating" && t.revision_job_id !== null);
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
  // Everything pushed above is SELF-MOVING: generation and publication proceed
  // without an operator, and every one of them is evidenced by a revision job.
  // A stranded lesson never will move, so it is deliberately not counted here —
  // it decides whether the poll stops, not whether it runs.
  if (stranded !== null && activity.length === 0) return stopped(stranded.pollReason);

  // Only trustworthy when no lesson is stranded: `bulk_running` is derived from
  // target statuses and `planned` counts as in flight, so a campaign whose
  // release never landed claims to be releasing forever.
  if (stranded === null && REGENERATION_RELEASING_STATUSES.has(detail.status)) {
    activity.push("the campaign is still releasing revision jobs");
  }

  if (activity.length > 0) {
    const moving = `Refreshing while ${activity.join(", ")}.`;
    return {
      shouldPoll: true,
      intervalMs: REGENERATION_POLL_MS,
      activity,
      strandedNote,
      // A PARTIAL release is the realistic shape of this failure: the lessons
      // that started still need refreshing, and the ones that never started
      // still need a human. Reporting only the first froze a live report;
      // reporting only the second hid the recovery.
      reason: strandedNote ? `${moving} ${strandedNote}` : moving,
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

/** Target statuses that move WITHOUT an operator. The list route deliberately
 *  does not roll up per campaign, so `status_counts` is all the evidence a
 *  summary carries — there is no `revision_job_id` at this level. */
const REGENERATION_SELF_MOVING_TARGET_STATUSES = [
  "generating",
  "publication_pending",
  "publishing",
];

/**
 * The summary-level shadow of `regenerationStrandedRelease`.
 *
 * An approved `bulk_running` campaign whose only non-terminal lessons are
 * `planned` is the shape of a release that never landed, and the detail screen
 * already refuses to poll it. Without this the LIST kept ticking every 10s
 * forever behind the same `bulk_running` status the detail screen had already
 * declared stuck.
 *
 * ACCEPTED IMPRECISION: a campaign approved seconds ago, whose revision jobs
 * exist but are all still waiting on the launch stagger, is `planned` too and
 * looks identical here. It stops the list tick early for that campaign; opening
 * it is what shows the truth, because the DETAIL poll can see `revision_job_id`
 * and keeps refreshing. Erring this way costs one manual refresh; erring the
 * other way polls a dead campaign for as long as the tab is open.
 */
function summaryStrandedRelease(c: RegenerationCampaignSummary): boolean {
  if (c.status !== "bulk_running") return false;
  if (c.approved_at === null) return false;
  if (c.rejected_at !== null || c.cancel_requested_at !== null) return false;
  if ((c.status_counts.planned ?? 0) <= 0) return false;
  return REGENERATION_SELF_MOVING_TARGET_STATUSES.every((s) => (c.status_counts[s] ?? 0) === 0);
}

/** The list ticks only while a campaign is actually working. */
export function regenerationListPollMs(
  campaigns: RegenerationCampaignSummary[] | undefined,
): number | false {
  const busy = (campaigns ?? []).some(
    (c) =>
      !c.is_terminal && REGENERATION_RELEASING_STATUSES.has(c.status) && !summaryStrandedRelease(c),
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
/** Which of the two pre-approval decisions this campaign can still take.
 *
 *  Approve and Reject are NOT the same gate. `reject_canary` is legal for any
 *  campaign that has launched a canary, has not been approved, and is not
 *  terminal — which includes `attention_required`, the state a campaign parks
 *  in when its canary FAILED to generate. Rendering both decisions only for
 *  `awaiting_canary_approval` left exactly that campaign with no way out but
 *  abandoning every target one at a time.
 *
 *  Approve stays narrow on purpose: `approve_canary` also accepts
 *  `attention_required`, but at least one canary still needs repair or an
 *  explicit abandon decision, so offering "approve and publish" there would
 *  invite a release before the gate is reviewable.
 */
export interface RegenerationDecisionGate {
  canApprove: boolean;
  canReject: boolean;
  /** Why approve is absent while reject is offered; null when both are. */
  rejectNote: string | null;
}

const REGENERATION_TERMINAL_CAMPAIGN_STATUSES: readonly RegenerationCampaignStatus[] = [
  "completed",
  "completed_with_abandonments",
  "rejected",
  "cancelled",
];

export function regenerationDecisionGate(
  detail: Pick<
    RegenerationCampaignDetail,
    "status" | "approved_at" | "canary_launched_at" | "rejected_at" | "cancel_requested_at"
  >,
): RegenerationDecisionGate {
  const terminal = REGENERATION_TERMINAL_CAMPAIGN_STATUSES.includes(detail.status);
  const preApproval =
    !terminal &&
    detail.approved_at == null &&
    detail.rejected_at == null &&
    detail.cancel_requested_at == null;
  // Narrower than "legal": `reject_canary` also accepts `canary_running`, but a
  // campaign whose canary is still generating is not stuck — "Cancel campaign"
  // is offered for every non-terminal campaign — and rejecting there would stop
  // work in flight under a button that reads like a review verdict. The two
  // states below are the ones where the operator has actually finished looking.
  const canReject =
    preApproval &&
    detail.canary_launched_at != null &&
    (detail.status === "awaiting_canary_approval" || detail.status === "attention_required");
  const canApprove = preApproval && detail.status === "awaiting_canary_approval";
  return {
    canApprove,
    canReject,
    rejectNote:
      canReject && !canApprove
        ? "One or more canary lessons need attention. Use their controls in the campaign " +
          "report to retry failed work or abandon an unrecoverable lesson. Approval returns " +
          "when a reviewable canary is ready; rejecting ends the whole campaign without " +
          "creating a Notion page or consuming a publication version."
        : null,
  };
}

/** A revision job has nothing to open or download until it is `done`.
 *
 *  The download endpoint serves the packet the job produced, so offering the
 *  link while the revision is pending, running or failed hands the operator a
 *  link that answers with a partial packet or an error — at the canary gate,
 *  where "I read it" is the whole decision.
 */
export function regenerationRevisionLinksReady(status: JobStatus | null): boolean {
  return status === "done";
}

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

/** Actual spend against the estimate, with the SCOPE stated.
 *
 *  At the canary gate only `canary_size` of `target_count` lessons have run, so
 *  scoring that spend against the whole-campaign high bound always reads
 *  "far below estimate" — the one moment an operator is deciding whether to
 *  release the rest. A percentage is only produced once every lesson has a
 *  revision job; before that the number is reported with what it covers and
 *  no verdict attached.
 */
export interface RegenerationCostView {
  text: string;
  direction: CostComparison["direction"];
  scope: "partial" | "complete";
}

export function regenerationCanaryCostView(
  detail: RegenerationCampaignDetail,
): RegenerationCostView {
  const actual = detail.actual_cost.usd;
  const ran = detail.actual_cost.revision_job_count;
  const total = detail.target_count;
  const estimated = detail.estimated_cost_high_usd ?? detail.estimated_cost_low_usd ?? 0;
  if (total > 0 && ran < total) {
    const covered = `${ran} of ${total} ${plural(total, "lesson", "lessons")}`;
    return {
      scope: "partial",
      direction: "on_target",
      text:
        estimated > 0
          ? `Actual ${formatUsd(actual)} so far, covering ${covered} — the ${formatUsd(
              estimated,
            )} estimate covers all ${total}.`
          : `Actual ${formatUsd(actual)} so far, covering ${covered} — no estimate was recorded for this campaign.`,
    };
  }
  const comparison = costComparison(estimated, actual);
  return { scope: "complete", direction: comparison.direction, text: comparison.text };
}

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

/** What a retry means for a lesson that never got a revision job at all. The
 *  standard copy describes re-running a revision; there is no revision here to
 *  re-run, and saying so is what tells the operator the campaign-level release
 *  is the thing that failed. */
const REGENERATION_JOBLESS_RETRY_DETAIL =
  "This lesson was moved to regenerating but never got a revision job, so nothing is running " +
  "for it. Retrying finishes the creation the release did not: it uses the snapshot and phase " +
  "plan this campaign froze, gives the lesson no second job, re-plans nothing and consumes no " +
  "extra version.";

/**
 * What an operator may do to one target right now.
 *
 * Generation and publication failures are separate, retryable states with
 * separate paths — a generated revision is never regenerated because Notion
 * failed. Terminal targets offer nothing. While one of a target's mutations is
 * in flight every button on that row is disabled: the API is idempotent, so
 * this is about not lying to the operator rather than about safety.
 *
 * A `generating` target with NO revision job is retryable too, and this is the
 * per-lesson half of the stranded-release recovery. `retry_generation` accepts
 * `("generation_failed", "generating")` and, finding no job, runs
 * `_create_wave([target_id], contract)` — i.e. it finishes the creation the
 * release never got to. A `generating` target that HAS its job still offers
 * nothing but abandon: retrying work already in flight is a no-op, and the
 * button would be a lie.
 */
export function regenerationTargetActions(
  target: RegenerationTargetReport,
  opts: { pendingKind?: RegenerationActionKind | null; campaignTerminal?: boolean } = {},
): RegenerationTargetAction[] {
  if (target.is_terminal) return [];

  const joblessGeneration = target.status === "generating" && target.revision_job_id === null;
  const kinds: RegenerationActionKind[] = [];
  if (target.status === "generation_failed" || joblessGeneration) kinds.push("retry-generation");
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
    ...(kind === "retry-generation" && joblessGeneration
      ? { detail: REGENERATION_JOBLESS_RETRY_DETAIL }
      : {}),
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
/** The extract is not a member of `selected_phases`; it has its own switch.
 *
 *  `canonical_phases` is `("extract", *flow_for(subject))`, so the catalog
 *  probe hands the wizard a phase the server refuses:
 *  `build_phase_plan(selected_phases=["extract"])` raises `UnknownPhaseError`
 *  ("pass refresh_extraction=True to re-run the extraction instead"), which
 *  reaches the operator as a 422 on a chip the screen invited them to click.
 */
export const REGENERATION_EXTRACT_PHASE = "extract";

export function regenerationSelectablePhases(canonicalPhases: string[]): string[] {
  return canonicalPhases.filter((phase) => phase !== REGENERATION_EXTRACT_PHASE);
}

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

/** The API's token for a clean verdict is `ok`; this build's vocabulary calls
 *  it `pass` (`regeneration-state.JudgeStatus`).
 *
 *  Without this every passing phase fell through to "a verdict this build does
 *  not recognise" — severity `warning` — so the canary gate counted the phases
 *  that PASSED as judge warnings and told the operator to hand-read them.
 */
const REGENERATION_JUDGE_STATUS_ALIASES: Record<string, JudgeStatus> = { ok: "pass" };

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
      const aliased = REGENERATION_JUDGE_STATUS_ALIASES[status];
      const known = aliased ?? REGENERATION_KNOWN_JUDGE_STATUSES.find((s) => s === status);
      return {
        status,
        count,
        signal: known ? judgeSignal(known) : unknownJudgeSignal(status),
      };
    })
    .sort((a, b) => a.status.localeCompare(b.status));
}

/* ── load states: error, loading and empty are three different things ──── */

export const REGENERATION_READ_RETRY_LABEL = "Try this read again";

export const REGENERATION_SOURCES_LOADING = "Loading eligible lessons…";
export const REGENERATION_SOURCES_EMPTY =
  "No lesson in this book has a complete published homework job in this language to " +
  "regenerate from.";
export const REGENERATION_PLAN_NONE =
  "Nothing selected yet — tick a phase above, or turn on the extract refresh further down.";
export const REGENERATION_PLAN_LOADING = "Working out what this selection pulls in…";

export interface RegenerationSourcesView {
  mode: "blocked" | "loading" | "error" | "empty" | "list";
  /** Non-null whenever the eligible read failed, even if stale rows render. */
  error: RegenerationErrorView | null;
  /** The single line that replaces the rows, or null when rows render. */
  message: string | null;
  /** Whatever the cache still holds — a failed refresh hides nothing. */
  sources: RegenerationEligibleSource[];
}

/**
 * A failed lesson read is NOT a book with no regenerable lessons.
 *
 * `/eligible` failing is a fact about the step where lessons are PICKED. It
 * used to be handed to the phase step as a plan failure, so a 500 on the
 * lesson read appeared under "Pick the phases to rebuild" while the lesson
 * step calmly stated that this book has nothing to regenerate — a claim about
 * data that never arrived, made in the one place an operator would believe it.
 *
 * `blocked` comes first because no request was made at all: until a book is
 * picked the query is deliberately switched off (`regenerationEligibleQuery`),
 * which is neither a failure nor an empty answer. `error` then wins over
 * loading and empty, and never suppresses rows the cache still holds.
 */
export function regenerationSourcesView(input: {
  sources: RegenerationEligibleSource[] | undefined;
  isLoading: boolean;
  error: RegenerationErrorView | null;
  blockedReason: string | null;
}): RegenerationSourcesView {
  const sources = input.sources ?? [];
  if (input.blockedReason) {
    return { mode: "blocked", error: null, message: input.blockedReason, sources };
  }
  if (input.error) {
    return { mode: "error", error: input.error, message: null, sources };
  }
  if (input.isLoading && sources.length === 0) {
    return { mode: "loading", error: null, message: REGENERATION_SOURCES_LOADING, sources };
  }
  if (sources.length === 0) {
    return { mode: "empty", error: null, message: REGENERATION_SOURCES_EMPTY, sources };
  }
  return { mode: "list", error: null, message: null, sources };
}

export interface RegenerationPlanStepView {
  mode: "none" | "loading" | "error" | "ready";
  message: string | null;
}

/**
 * "Nothing is selected" and "the planner has not answered yet" are different.
 *
 * The cascade step rendered "Nothing selected yet." whenever the plan was
 * absent — which is also what a plan in flight and a plan that just 500'd look
 * like. Only the first is a statement about the operator's selection; the
 * other two are statements about a request, and one of them has an error block
 * of its own directly above.
 */
export function regenerationPlanStepView(input: {
  plan: RegenerationPhasePlan | null;
  hasSelection: boolean;
  isLoading: boolean;
  error: RegenerationErrorView | null;
}): RegenerationPlanStepView {
  if (input.plan) return { mode: "ready", message: null };
  // The error itself renders through RegenerationProblem; repeating it as
  // prose here would say the same thing twice, and claiming an empty selection
  // would say something false.
  if (input.error) return { mode: "error", message: null };
  if (!input.hasSelection) return { mode: "none", message: REGENERATION_PLAN_NONE };
  return { mode: "loading", message: REGENERATION_PLAN_LOADING };
}

/** Why the create button is blocked at the dependency-plan step.
 *
 *  `plan` is null in three different situations and only one of them is about
 *  the operator's own selection. Reporting "Pick at least one phase" while a
 *  plan request is in flight — or while its error block sits directly above
 *  saying the read failed — sends the operator to change something that was
 *  never wrong.
 */
export function regenerationPlanBlockedReason(
  planStep: RegenerationPlanStepView,
): string | null {
  switch (planStep.mode) {
    case "ready":
      return null;
    case "loading":
      return "Waiting for the planner to answer.";
    case "error":
      return (
        "The dependency plan could not be read, so there is nothing to freeze " +
        "this campaign to."
      );
    default:
      return "Pick at least one phase, or turn on the extract refresh.";
  }
}

/** One lineage that cannot be regenerated.
 *
 *  A `toc_entry_id` is the only identity `/eligible` returns for an INELIGIBLE
 *  row — there is no title on it, because the lesson never had a finished job
 *  to read one from — so the UUID has to render. Saying what it IS is the
 *  difference between an identifier and a wall of hex, and the reasons are
 *  server codes, which are spelled out like every other status on screen. */
export function regenerationIneligibleLine(row: RegenerationIneligibleLineage): string {
  const head = `Lesson id ${row.toc_entry_id} (${regenerationLanguageLabel(row.output_language)})`;
  const why = [row.reasons.map(humanise).join("; "), row.detail].filter(Boolean).join(" — ");
  return why ? `${head} — ${why}` : `${head} — no reason was recorded for this lineage.`;
}

/** A solver verdict, in operator words. The counts arrive keyed by the raw
 *  `mismatch_regen`-style token, and every other status on these screens is
 *  rendered as prose; this one was being echoed verbatim. */
export function regenerationSolverStatusLabel(status: string): string {
  return humanise(status) || status;
}

export const REGENERATION_LIST_LOADING = "Loading campaigns…";
export const REGENERATION_LIST_EMPTY = "No regeneration campaigns yet.";
export const REGENERATION_DETAIL_LOADING = "Loading this campaign…";
export const REGENERATION_DETAIL_IDLE =
  "Pick a campaign to read its canary and its report, or freeze a new one on the left.";

export interface RegenerationCampaignListView {
  mode: "error" | "loading" | "empty" | "list";
  /** Non-null whenever the last read failed, even if stale rows still render. */
  error: RegenerationErrorView | null;
  /** The single line that replaces the rows, or null when rows render. */
  message: string | null;
  /** Whatever the cache still holds — a failed refresh hides nothing. */
  campaigns: RegenerationCampaignSummary[];
}

/**
 * A failed campaign list is NOT an empty one.
 *
 * `GET /campaigns` is the only regeneration query this page runs
 * unconditionally, which makes it the one place the server-side flag can be
 * observed without picking a book first: with `REGENERATION_ENABLED=false`
 * every route answers 404, and that 404 has to reach the screen as prose. It
 * used to arrive as `campaigns.data === undefined`, which the list rendered as
 * "No regeneration campaigns yet." — a claim about the data made from a
 * request that never returned any.
 *
 * `error` wins over both other states, and it never suppresses rows the cache
 * still holds: a failed refresh over a known list must not blank the list.
 */
export function regenerationCampaignListView(input: {
  campaigns: RegenerationCampaignSummary[] | undefined;
  isLoading: boolean;
  error: unknown;
}): RegenerationCampaignListView {
  const campaigns = input.campaigns ?? [];
  if (input.error) {
    return { mode: "error", error: regenerationErrorView(input.error), message: null, campaigns };
  }
  if (input.isLoading && campaigns.length === 0) {
    return { mode: "loading", error: null, message: REGENERATION_LIST_LOADING, campaigns };
  }
  if (campaigns.length === 0) {
    return { mode: "empty", error: null, message: REGENERATION_LIST_EMPTY, campaigns };
  }
  return { mode: "list", error: null, message: null, campaigns };
}

export interface RegenerationDetailView {
  mode: "idle" | "loading" | "error" | "ready";
  /** Only ever the report for the campaign that is actually selected. */
  detail: RegenerationCampaignDetail | null;
  /** The read that failed — including a refresh that failed over good data. */
  error: RegenerationErrorView | null;
  message: string | null;
}

/**
 * "Nothing is selected" and "the selection has not arrived yet" are different.
 *
 * The pane used to be driven by `detail.data ?? null`, so the first render
 * after clicking a campaign — and every failed read of one — told the operator
 * to pick a campaign they had just picked. `idle` is now a statement about the
 * SELECTION and nothing else; everything after it is a statement about the
 * request.
 *
 * TanStack keys this query per campaign, so `data` is a previous SUCCESS for
 * this id, not a leftover from another one — but the id is checked anyway,
 * because rendering one campaign's report under another campaign's heading is
 * the worst failure on this screen. Cached data plus a failed refresh stays
 * `ready` WITH the error: blanking a readable report to show a refresh failure
 * loses more than it explains.
 */
export function regenerationDetailView(input: {
  selectedId: string | null;
  data: RegenerationCampaignDetail | null | undefined;
  error: unknown;
}): RegenerationDetailView {
  if (input.selectedId === null) {
    return { mode: "idle", detail: null, error: null, message: REGENERATION_DETAIL_IDLE };
  }
  const detail = input.data ?? null;
  if (detail !== null && detail.id === input.selectedId) {
    return {
      mode: "ready",
      detail,
      error: input.error ? regenerationErrorView(input.error) : null,
      message: null,
    };
  }
  if (input.error) {
    return {
      mode: "error",
      detail: null,
      error: regenerationErrorView(input.error),
      message: null,
    };
  }
  return { mode: "loading", detail: null, error: null, message: REGENERATION_DETAIL_LOADING };
}

export interface RegenerationMutationView {
  pending: boolean;
  error: RegenerationErrorView | null;
}

export interface RegenerationTimedMutationError {
  submittedAt: number;
  error: RegenerationErrorView | null;
}

/** Error from the most recently submitted action, including a successful
 * newer action whose null error supersedes an older refusal. */
export function regenerationLatestMutationError(
  actions: readonly RegenerationTimedMutationError[],
): RegenerationErrorView | null {
  if (actions.length === 0) return null;
  return actions.reduce((latest, action) =>
    action.submittedAt >= latest.submittedAt ? action : latest,
  ).error;
}

/**
 * A mutation result, scoped to the thing that produced it.
 *
 * TanStack keeps `error`, `variables` and `isPending` on the mutation itself,
 * not on the campaign — so a refused approve on campaign A stayed on screen
 * when the operator moved to campaign B, and B rendered A's
 * `illegal_campaign_state` as though B were the stale one. `variables` is the
 * server-independent record of WHICH campaign or target the operator acted on,
 * so it is what decides ownership here.
 *
 * `reset()` on selection would fix the same bug by throwing the error away —
 * but it also throws away `variables`, which is the evidence of WHO the error
 * belonged to, and it would have to fire from the selection handler that also
 * owns the route's deliberately mutation-only audit state
 * (`released_failures`, `previous_publication_*`). Filtering keeps the record:
 * campaign A's refusal is still attributed to A on the way back, until a later
 * call on the same mutation legitimately replaces it.
 *
 * `pending` is filtered the same way, so campaign B never renders "Approving…"
 * because campaign A is mid-flight. A mutation that has never run carries
 * `variables === undefined` and therefore owns nothing.
 */
export function regenerationMutationView<V>(
  state: { error?: unknown; variables?: V; isPending?: boolean },
  owns: (vars: V) => boolean,
): RegenerationMutationView {
  const vars = state.variables;
  const owned = vars !== undefined && owns(vars);
  return {
    pending: owned && state.isPending === true,
    error: owned && state.error ? regenerationErrorView(state.error) : null,
  };
}

export interface RegenerationKeyedLine {
  key: string;
  text: string;
}

/**
 * Render keys for the lists that have no server id — validation details,
 * estimate notes, rollup warnings.
 *
 * Their text is the only thing they carry, and it is genuinely repeatable: two
 * lessons called "Kirish", the same zero-volume note for two phases, the same
 * validation message for two fields. Keying on the text collapses those into
 * one row, which silently under-reports the very problem being rendered.
 *
 * The position is folded into the key here rather than in JSX, so the callers
 * never touch a bare array index and the collision is provable without a DOM.
 */
export function regenerationKeyedLines(
  lines: readonly string[] | null | undefined,
): RegenerationKeyedLine[] {
  return (lines ?? []).map((text, index) => ({ key: `${index}:${text}`, text }));
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

function stringsOf(record: Record<string, unknown>, key: string): string[] {
  const value = record[key];
  return Array.isArray(value) ? value.filter((row): row is string => typeof row === "string") : [];
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
    // TWO different 404s share this status. The flag guard raises the literal
    // `HTTPException(404, "Not Found")`, so `detail` is exactly that string;
    // `_translate_campaign_error` raises `HTTPException(404, str(exc))`, whose
    // detail is the service's own sentence naming the row. Reporting the second
    // as the first sends an operator to a deployment flag over a deleted row,
    // so the match is on the exact string and nothing looser.
    const rawDetail = err instanceof ApiError ? err.detail : undefined;
    if (typeof rawDetail === "string" && rawDetail === "Not Found") {
      return {
        title: "Regeneration is switched off on the server",
        message:
          "This build shows the regeneration screens, but the server is running with " +
          "REGENERATION_ENABLED=false, so every regeneration route answers as if it does not " +
          "exist. The backend flag is the real gate; turning it on is a deployment decision, " +
          "not a UI one.",
        details: [],
        hint: null,
        code: null,
        status,
      };
    }
    return {
      title: "That campaign or lesson is not there any more",
      message: message || "The server could not find what this screen asked for.",
      details: [],
      hint:
        "It may have been deleted, or this screen may still be holding an id from an earlier " +
        "session. Nothing was changed by this request; go back to the campaign list and pick " +
        "one that is still there.",
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
    case "notion_unavailable":
      return {
        ...base,
        title: "Notion publication is not configured on this head",
        hint:
          "Enable Notion with a valid credential on the designated head, restart it, and " +
          "then approve again. No target was released and no version was reserved.",
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
    case "unbounded_selection":
      return {
        ...base,
        title: "Choose a book or lesson first",
        hint: "Select at least one book or individual lesson, then request the estimate again.",
      };
    case "selection_too_large":
      return {
        ...base,
        title: "Too many eligible lessons for one campaign",
        hint: "Narrow the selection or split it into separately reviewed campaigns.",
      };
    case "selection_discovery_too_large":
      return {
        ...base,
        title: "That selection is too broad to inspect safely",
        hint: "Choose a specific book or a smaller set of lessons, then try again.",
      };
    case "canary_not_reviewable": {
      const remedy = text(detail ?? {}, "remedy");
      return {
        ...base,
        title: "The canary is not ready for approval",
        details: stringsOf(detail ?? {}, "blockers").map(
          (blocker) => `Blocked by ${humanise(blocker)}`,
        ),
        hint: remedy || "Retry or abandon the affected canary lessons in the campaign report.",
      };
    }
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
