import type { Book, Job, Subject } from "./types";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || res.statusText);
  }
  return (await res.json()) as T;
}

export const api = {
  async listBooks(): Promise<Book[]> {
    const res = await fetch("/api/v1/books");
    return unwrap<Book[]>(res);
  },

  async uploadBook(file: File, subject: Subject): Promise<Book> {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject);
    const res = await fetch("/api/v1/books", { method: "POST", body: fd });
    return unwrap<Book>(res);
  },

  async getBook(bookId: string): Promise<Book> {
    const res = await fetch(`/api/v1/books/${encodeURIComponent(bookId)}`);
    return unwrap<Book>(res);
  },

  async generate(bookId: string, sectionId: string, force = false): Promise<Job> {
    const res = await fetch(
      `/api/v1/books/${encodeURIComponent(bookId)}/sections/${encodeURIComponent(sectionId)}/generate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force }),
      },
    );
    return unwrap<Job>(res);
  },

  async getJob(jobId: string): Promise<Job> {
    const res = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    return unwrap<Job>(res);
  },

  jobDownloadUrl(jobId: string): string {
    return `/api/v1/jobs/${encodeURIComponent(jobId)}/download`;
  },

  bookTocStreamUrl(bookId: string): string {
    return `/api/v1/books/${encodeURIComponent(bookId)}/toc/stream`;
  },

  jobStreamUrl(jobId: string): string {
    return `/api/v1/jobs/${encodeURIComponent(jobId)}/stream`;
  },
};

export { ApiError };
