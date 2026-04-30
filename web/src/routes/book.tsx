import { ArrowRight, Loader2, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Eyebrow } from "@/components/eyebrow";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useEventSource } from "@/hooks/use-event-source";
import { api } from "@/lib/api";
import type { TOCEntry } from "@/lib/types";
import { cn, formatPages } from "@/lib/utils";

const STATUS_LABEL: Record<string, string> = {
  uploading: "Uploading…",
  toc_extracting: "Indexing chapters and sections…",
};

export function BookPage() {
  const { id } = useParams<{ id: string }>();
  const [statusText, setStatusText] = useState("Reading the volume…");
  const [entries, setEntries] = useState<TOCEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (!id) return;
    api
      .getBook(id)
      .then((b) => {
        if (b.status === "toc_ready" && b.toc) {
          setEntries(b.toc);
          setStatusText("");
        } else if (b.status === "failed") {
          setError(b.error_message ?? "Extraction failed.");
        }
      })
      .catch(() => undefined);
  }, [id]);

  const handlers = useMemo(
    () => ({
      status: (data: any) => {
        setStatusText(STATUS_LABEL[data?.status] ?? data?.status ?? "");
      },
      toc_ready: (data: any) => {
        setEntries(data?.entries ?? []);
        setStatusText("");
      },
      error: (data: any) => {
        setError(data?.message ?? "Stream failed.");
      },
    }),
    [],
  );

  useEventSource(id ? api.bookTocStreamUrl(id) : null, handlers, {
    enabled: !entries && !error,
  });

  const filtered = useMemo(() => {
    if (!entries) return null;
    if (!filter.trim()) return entries;
    const q = filter.toLowerCase();
    return entries.filter(
      (e) =>
        e.section_title.toLowerCase().includes(q) ||
        e.section_number.toLowerCase().includes(q) ||
        (e.chapter_title ?? "").toLowerCase().includes(q),
    );
  }, [entries, filter]);

  return (
    <>
      <Eyebrow>Sections</Eyebrow>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
        Pick a section
      </h1>

      {entries && entries.length > 0 && (
        <div className="relative mt-6 max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-(--color-ink-muted)" />
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={`Filter ${entries.length} section${entries.length === 1 ? "" : "s"}`}
            className="pl-9"
          />
        </div>
      )}

      {!entries && !error && (
        <div className="mt-7 flex items-center gap-2 text-sm text-(--color-ink-muted)">
          <Loader2 className="size-3.5 animate-spin text-(--color-accent)" />
          {statusText}
        </div>
      )}

      {error && (
        <div className="mt-7 rounded-(--radius-md) border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)] px-3 py-2 text-sm text-(--color-error)">
          {error}
        </div>
      )}

      {!entries && !error && (
        <div className="mt-7 flex flex-col gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      )}

      {filtered && (
        <ol className="mt-7 flex flex-col gap-1.5">
          {filtered.map((entry, idx) => (
            <li key={entry.id}>
              <Link
                to={`/book/${id}/section/${entry.id}`}
                className={cn(
                  "group grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-3.5 py-2.5 text-left transition-colors",
                  "hover:bg-(--color-elevated-hover) hover:border-(--color-border-hover)",
                )}
              >
                <span className="font-mono text-[0.7rem] text-(--color-ink-muted) tabular-nums w-7">
                  {String(idx + 1).padStart(2, "0")}
                </span>

                <div className="flex min-w-0 flex-col gap-0.5">
                  {entry.chapter_title && (
                    <span className="truncate font-mono text-[0.6rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                      {entry.chapter_title}
                    </span>
                  )}
                  <span className="truncate text-sm font-medium text-(--color-ink)">
                    {entry.section_number ? `${entry.section_number} · ` : ""}
                    {entry.section_title}
                  </span>
                </div>

                <span className="flex items-center gap-2.5">
                  {entry.page_start && (
                    <span className="hidden font-mono text-[0.66rem] text-(--color-ink-muted) sm:inline">
                      {formatPages(entry.page_start, entry.page_end)}
                    </span>
                  )}
                  <ArrowRight className="size-3.5 text-(--color-ink-muted) group-hover:text-(--color-accent)" />
                </span>
              </Link>
            </li>
          ))}

          {filtered.length === 0 && (
            <div className="rounded-(--radius-md) border border-dashed border-(--color-border) px-4 py-8 text-center text-sm text-(--color-ink-muted)">
              No sections match “{filter}”.
            </div>
          )}
        </ol>
      )}
    </>
  );
}
