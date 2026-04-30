import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpen, Loader2, Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { Eyebrow } from "@/components/eyebrow";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Book, BookStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

export function LibraryPage() {
  const { data: books, isLoading, error } = useQuery({
    queryKey: ["books"],
    queryFn: () => api.listBooks(),
    refetchInterval: (query) => {
      const list = query.state.data;
      const anyInFlight = list?.some((b) =>
        ["uploading", "toc_extracting"].includes(b.status),
      );
      return anyInFlight ? 4_000 : false;
    },
  });

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <Eyebrow>Library</Eyebrow>
        <Button asChild size="sm" variant="secondary">
          <Link to="/">
            <Plus className="size-3.5" />
            Upload book
          </Link>
        </Button>
      </div>

      <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
        Uploaded books
      </h1>
      <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-(--color-ink-soft)">
        Pick a book to open its table of contents.
      </p>

      {error && (
        <div className="mt-7 rounded-(--radius-md) border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)] px-3 py-2 text-sm text-(--color-error)">
          Failed to load library: {(error as Error).message}
        </div>
      )}

      {isLoading && (
        <div className="mt-7 flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
            <Skeleton key={i} className="h-[60px] w-full" />
          ))}
        </div>
      )}

      {books && books.length === 0 && (
        <div className="mt-7 flex flex-col items-center gap-3 rounded-(--radius-md) border border-dashed border-(--color-border) px-6 py-12 text-center">
          <BookOpen className="size-5 text-(--color-ink-muted)" />
          <span className="text-sm text-(--color-ink-soft)">No books yet.</span>
          <Button asChild size="sm" className="mt-1">
            <Link to="/">
              <Plus className="size-3.5" />
              Upload your first book
            </Link>
          </Button>
        </div>
      )}

      {books && books.length > 0 && (
        <ul className="mt-7 flex flex-col gap-1.5">
          {books.map((book) => (
            <li key={book.id}>
              <BookRow book={book} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function BookRow({ book }: { book: Book }) {
  const ready = book.status === "toc_ready";
  const inFlight = ["uploading", "toc_extracting"].includes(book.status);

  return (
    <Link
      to={`/book/${book.id}`}
      className={cn(
        "group grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-3.5 py-2.5 transition-colors",
        "hover:bg-(--color-elevated-hover) hover:border-(--color-border-hover)",
      )}
    >
      <span className="grid size-8 place-items-center rounded-(--radius-sm) bg-(--color-canvas) text-(--color-ink-muted)">
        <BookOpen className="size-4" />
      </span>

      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-(--color-ink)">
            {book.original_filename}
          </span>
          <span className="hidden font-mono text-[0.6rem] uppercase tracking-[0.14em] text-(--color-ink-muted) sm:inline">
            · {book.subject}
          </span>
        </div>
        <div className="flex items-center gap-2 font-mono text-[0.66rem] text-(--color-ink-muted)">
          {book.created_at && <span>{formatRelative(book.created_at)}</span>}
          {book.file_size_bytes && (
            <span>· {(book.file_size_bytes / 1024 / 1024).toFixed(1)} MB</span>
          )}
        </div>
      </div>

      <span className="flex items-center gap-2.5">
        <StatusBadge status={book.status} />
        {ready && (
          <ArrowRight className="size-3.5 text-(--color-ink-muted) group-hover:text-(--color-accent)" />
        )}
        {inFlight && <Loader2 className="size-3.5 animate-spin text-(--color-accent)" />}
      </span>
    </Link>
  );
}

function StatusBadge({ status }: { status: BookStatus }) {
  if (status === "toc_ready") return <Badge variant="success">ready</Badge>;
  if (status === "failed") return <Badge variant="error">failed</Badge>;
  if (status === "uploading") return <Badge variant="accent">uploading</Badge>;
  return <Badge variant="accent">indexing</Badge>;
}

function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = Date.now() - t;
  const m = Math.floor(diff / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}
