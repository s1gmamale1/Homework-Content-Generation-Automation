import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, BookOpen, Loader2, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Eyebrow } from "@/components/eyebrow";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useEventSource } from "@/hooks/use-event-source";
import { api } from "@/lib/api";
import type { TOCEntry } from "@/lib/types";
import { cn, formatPages } from "@/lib/utils";

const STATUS_LABEL: Record<string, string> = {
  uploading: "Uploading manuscript…",
  toc_extracting: "Indexing chapters and sections…",
};

export function BookPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [statusText, setStatusText] = useState("Reading the volume…");
  const [entries, setEntries] = useState<TOCEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [generatingId, setGeneratingId] = useState<string | null>(null);

  // Hydrate from REST first so a refreshed page works without depending on SSE.
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
      .catch(() => {
        /* SSE will provide */
      });
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

  async function pickSection(entry: TOCEntry) {
    if (!id) return;
    setGeneratingId(entry.id);
    try {
      const job = await api.generate(id, entry.id);
      toast.success("Sent to compositor.");
      navigate(`/job/${job.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Generate failed";
      toast.error(msg);
      setGeneratingId(null);
    }
  }

  return (
    <>
      <Eyebrow>Pick a Section</Eyebrow>

      <h1 className="mt-6 font-display text-[clamp(2.8rem,6.8vw,4.6rem)] font-normal leading-[1.02] tracking-[-0.025em]">
        Choose what to <em className="not-italic gradient-text font-display italic">study</em>.
      </h1>

      {entries && entries.length > 0 && (
        <div className="mt-7 max-w-md">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-(--color-ink-muted)" />
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={`Filter ${entries.length} section${entries.length === 1 ? "" : "s"}…`}
              className="pl-10"
            />
          </div>
        </div>
      )}

      {!entries && !error && (
        <div className="mt-8 flex items-center gap-2.5 text-sm text-(--color-ink-muted)">
          <Loader2 className="size-4 animate-spin text-(--color-amber)" />
          {statusText}
        </div>
      )}

      {error && (
        <div className="mt-8 rounded-(--radius-md) border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_10%)] px-4 py-3 text-sm text-(--color-error)">
          ⚠ {error}
        </div>
      )}

      {!entries && !error && (
        <div className="mt-9 flex flex-col gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
            <Skeleton key={i} className="h-[5.5rem] w-full" />
          ))}
        </div>
      )}

      {filtered && (
        <ol className="mt-9 flex flex-col gap-2.5">
          <AnimatePresence initial={false}>
            {filtered.map((entry, idx) => (
              <motion.li
                key={entry.id}
                layout
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{
                  duration: 0.45,
                  ease: [0.16, 1, 0.3, 1],
                  delay: Math.min(idx * 0.025, 0.5),
                }}
              >
                <button
                  type="button"
                  onClick={() => pickSection(entry)}
                  disabled={generatingId !== null}
                  className={cn(
                    "group relative grid w-full grid-cols-[auto_1fr_auto] items-center gap-5 overflow-hidden rounded-(--radius-lg) border border-(--color-border-subtle) bg-(--color-surface) p-4 sm:p-5 text-left backdrop-blur-md",
                    "transition-[background,border-color,box-shadow,transform] duration-300 ease-(--ease-out-expo)",
                    "hover:bg-(--color-surface-hover) hover:border-(--color-border-hover) hover:shadow-[0_12px_30px_-12px_oklch(0_0_0_/_50%)]",
                    "disabled:pointer-events-none",
                    generatingId === entry.id && "opacity-60",
                  )}
                >
                  <span className="absolute inset-0 bg-[radial-gradient(circle_at_var(--mx,50%)_var(--my,50%),oklch(0.79_0.13_70_/_14%),transparent_45%)] opacity-0 transition-opacity duration-300 group-hover:opacity-100" />

                  <span className="relative z-10 inline-flex min-w-9 items-center justify-center rounded-[9px] border border-[oklch(0.79_0.13_70_/_25%)] bg-[oklch(0.79_0.13_70_/_14%)] px-2.5 py-2 font-mono text-[0.78rem] font-medium text-(--color-amber) tabular-nums">
                    {String(idx + 1).padStart(2, "0")}
                  </span>

                  <div className="relative z-10 flex min-w-0 flex-col gap-1">
                    {entry.chapter_title && (
                      <span className="truncate font-mono text-[0.62rem] font-medium uppercase tracking-[0.16em] text-(--color-ink-muted)">
                        {entry.chapter_title}
                      </span>
                    )}
                    <span className="truncate text-[1.02rem] font-medium leading-[1.35] text-(--color-ink)">
                      {entry.section_number ? `${entry.section_number} · ` : ""}
                      {entry.section_title}
                    </span>
                  </div>

                  <span className="relative z-10 flex items-center gap-2">
                    {entry.page_start && (
                      <span className="hidden whitespace-nowrap rounded-md border border-(--color-border-subtle) bg-(--color-surface) px-2 py-1 font-mono text-[0.66rem] text-(--color-ink-muted) sm:inline">
                        {formatPages(entry.page_start, entry.page_end)}
                      </span>
                    )}
                    {generatingId === entry.id ? (
                      <Loader2 className="size-4 animate-spin text-(--color-amber)" />
                    ) : (
                      <ArrowRight className="size-4 text-(--color-ink-muted) transition-all duration-300 group-hover:translate-x-0.5 group-hover:text-(--color-amber)" />
                    )}
                  </span>
                </button>
              </motion.li>
            ))}
          </AnimatePresence>

          {filtered.length === 0 && (
            <div className="flex flex-col items-center gap-3 rounded-(--radius-lg) border border-dashed border-(--color-border-subtle) px-6 py-12 text-center">
              <BookOpen className="size-6 text-(--color-ink-muted)" />
              <span className="text-sm text-(--color-ink-muted)">
                No sections match “{filter}”.
              </span>
            </div>
          )}
        </ol>
      )}
    </>
  );
}
