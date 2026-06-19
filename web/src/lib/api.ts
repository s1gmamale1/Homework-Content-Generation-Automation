import { clearToken, getToken } from "./auth";
import type {
  AgentStats,
  BatchCancelResponse,
  BatchLaunchResponse,
  BatchLessonRow,
  BatchPreviewResponse,
  BatchResumeResponse,
  BatchSummary,
  Book,
  Job,
  NotionGrade,
  NotionSubject,
  ProviderModelManifest,
  RoleTransport,
  Subject,
  TOCEntry,
  Transport,
  Worker,
} from "./types";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
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

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || res.statusText);
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

export const api = {
  async listBooks(): Promise<Book[]> {
    const res = await authFetch("/api/v1/books");
    return unwrap<Book[]>(res);
  },

  async uploadBook(file: File, subject: Subject, grade?: string): Promise<Book> {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject);
    if (grade) fd.append("grade", grade);
    const res = await authFetch("/api/v1/books", { method: "POST", body: fd });
    return unwrap<Book>(res);
  },

  async getBook(bookId: string): Promise<Book> {
    const res = await authFetch(`/api/v1/books/${encodeURIComponent(bookId)}`);
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
        }),
      },
    );
    return unwrap<Job>(res);
  },

  async getAgentModels(): Promise<ProviderModelManifest> {
    const res = await authFetch("/api/v1/agent/models");
    return unwrap<ProviderModelManifest>(res);
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

  async fetchBookFromNotion(subjectPageId: string, grade: string): Promise<Book> {
    const res = await authFetch("/api/v1/books/from-notion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject_page_id: subjectPageId, grade }),
    });
    return unwrap<Book>(res);
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
  async listWorkers(): Promise<{ workers: Worker[]; total: number; online: number; stale_after_seconds: number }> {
    const res = await authFetch("/api/v1/workers");
    return unwrap(res);
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
};

export { ApiError };
