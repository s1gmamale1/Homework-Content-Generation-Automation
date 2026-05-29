import { clearToken, getToken } from "./auth";
import type {
  AgentStats,
  Book,
  Job,
  ProviderModelManifest,
  Subject,
  TOCEntry,
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

  async uploadBook(file: File, subject: Subject): Promise<Book> {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject);
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
    } = {},
  ): Promise<Job> {
    const { force = false, idempotencyKey, provider = "gemini", model = null } = opts;
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const res = await authFetch(
      `/api/v1/books/${encodeURIComponent(bookId)}/sections/${encodeURIComponent(sectionId)}/generate`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ force, provider, model }),
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
