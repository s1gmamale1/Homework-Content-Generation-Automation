import {
  ArrowLeft,
  ArrowRight,
  Ban,
  Check,
  CheckCircle2,
  CircleDot,
  CircleX,
  Loader2,
  Pencil,
  RotateCcw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useEventSource } from "@/hooks/use-event-source";
import { api } from "@/lib/api";
import { fadeUpItem, staggerContainer } from "@/lib/motion";
import { subjectLabel } from "@/lib/subjects";
import type { JobStatus, Subject, TOCEntry } from "@/lib/types";
import { INPUT_GLASS } from "@/lib/ui";
import { cn, formatPages } from "@/lib/utils";

const STATUS_LABEL: Record<string, string> = {
  uploading: "Uploading…",
  toc_extracting: "Indexing chapters and sections…",
  toc_review: "Needs review",
};

// Shown in the review panel when the validator flagged the TOC but returned no
// specific issue text (a mismatch with an empty issues list). Guarantees a
// toc_review book ALWAYS renders the panel + Accept/Retry affordance, so a
// soft-gated book is never stuck without a way to act on it.
const REVIEW_FALLBACK =
  "The validator flagged this table of contents — review the entries below before using it.";

export function BookPage() {
  const { id } = useParams<{ id: string }>();
  const [statusText, setStatusText] = useState("Reading the volume…");
  const [entries, setEntries] = useState<TOCEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [meta, setMeta] = useState<{ name: string; subject: Subject } | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [accepting, setAccepting] = useState(false);
  // Non-null when the book is in toc_review: holds the joined validation issues text.
  const [tocReviewDetail, setTocReviewDetail] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api
      .getBook(id)
      .then((b) => {
        setMeta({ name: b.original_filename, subject: b.subject });
        if (b.status === "toc_ready" && b.toc) {
          setEntries(b.toc);
          setStatusText("");
        } else if (b.status === "toc_review") {
          // Show the entries so the operator can inspect them; surface review detail.
          if (b.toc) setEntries(b.toc);
          setTocReviewDetail(b.toc_validation_detail || REVIEW_FALLBACK);
          setStatusText("");
        } else if (b.status === "failed") {
          setError(b.error_message ?? "Extraction failed.");
        }
      })
      .catch(() => undefined);
  }, [id]);

  /**
   * Re-run book preparation (TOC extraction) on a `failed` or stuck book — see
   * `POST /api/v1/books/<id>/toc/retry`. Mirrors job.tsx's handleRetry: after
   * the server resets the book, we clear local error/entries state so the
   * `useEventSource` re-enables (its `enabled` gate is `!entries && !error`)
   * and the worker's status/toc_ready events repopulate the page.
   */
  async function handleRetry() {
    if (!id) return;
    setRetrying(true);
    try {
      await api.retryBookToc(id);
      setError(null);
      setEntries(null);
      setTocReviewDetail(null);
      setStatusText("Re-preparing… extracting chapters");
      toast.success("Re-preparing… extracting chapters");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Retry failed");
    } finally {
      setRetrying(false);
    }
  }

  /**
   * Redo TOC extraction for an already-`toc_ready` book (destructive —
   * REPLACES the current TOC rows). `POST /toc/retry` allows this status
   * (Task 3, prepare-status-redo) but the book page never surfaced it —
   * the only way to redo a two-linked-part book was through the Prepare
   * dialog's `PrepareStatusPanel`, which doesn't cover a directly-opened
   * book page. Confirm copy is duplicated from `prepare-status-panel.tsx`'s
   * `confirmAndRedo` rather than extracted into a shared helper — sharing
   * one `window.confirm` string across a route file and a component isn't
   * worth the import/plumbing churn for a single literal; keep both in
   * sync by hand if the copy ever changes.
   */
  function handleRedoReady() {
    if (
      !window.confirm(
        "Redo TOC extraction?\n\nThis re-extracts the table of contents from the source PDF and REPLACES the current TOC rows.",
      )
    ) {
      return;
    }
    void handleRetry();
  }

  /**
   * Accept the TOC for a book in `toc_review` status — transitions it to
   * `toc_ready` without re-extracting. Mirrors handleRetry's state reset pattern.
   */
  async function handleAccept() {
    if (!id) return;
    setAccepting(true);
    try {
      const b = await api.acceptToc(id);
      // Server flipped to toc_ready; reflect that locally.
      setTocReviewDetail(null);
      setStatusText("");
      if (b.toc) setEntries(b.toc);
      toast.success("TOC accepted — book is ready to launch.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Accept failed");
    } finally {
      setAccepting(false);
    }
  }

  const handlers = useMemo(
    () => ({
      status: (data: any) => {
        setStatusText(STATUS_LABEL[data?.status] ?? data?.status ?? "");
      },
      toc_ready: (data: any) => {
        setEntries(data?.entries ?? []);
        setTocReviewDetail(null);
        setStatusText("");
      },
      toc_review: (data: any) => {
        // Terminal SSE event: extraction done but validator flagged it.
        // Populate entries now; drive the issue text from a REST refetch so
        // we always get the canonical toc_validation_detail (avoids depending
        // on whether the live payload uses .detail or .issues array).
        if (data?.entries) setEntries(data.entries);
        setStatusText("");
        // Refetch book to get the authoritative toc_validation_detail.
        if (id) {
          api
            .getBook(id)
            .then((b) => setTocReviewDetail(b.toc_validation_detail || REVIEW_FALLBACK))
            .catch(() => {
              // Fallback: read from SSE payload defensively
              const detail =
                data?.validation?.detail ??
                (Array.isArray(data?.validation?.issues)
                  ? (data.validation.issues as string[]).join("; ")
                  : null);
              setTocReviewDetail(detail || REVIEW_FALLBACK);
            });
        }
      },
      error: (data: any) => {
        setError(data?.message ?? "Stream failed.");
      },
    }),
    [id],
  );

  useEventSource(id ? api.bookTocStreamUrl(id) : null, handlers, {
    // Disable once we have terminal state: entries (toc_ready), an error (failed),
    // OR a review panel (toc_review — entries may be populated alongside it).
    enabled: !entries && !error && tocReviewDetail === null,
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

  function applyEntryUpdate(updated: TOCEntry) {
    setEntries((prev) =>
      prev ? prev.map((e) => (e.id === updated.id ? { ...e, ...updated } : e)) : prev,
    );
  }

  function applyEntryDelete(deletedId: string) {
    setEntries((prev) => (prev ? prev.filter((e) => e.id !== deletedId) : prev));
  }

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10">
        {/* Back nav + subject */}
        <div className="flex items-center justify-between gap-3">
          <Link
            to="/library"
            className="group inline-flex items-center gap-2 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-2 text-sm font-medium text-white/75 transition-colors hover:bg-white/[0.1] hover:text-white"
          >
            <ArrowLeft className="size-4 shrink-0 transition-transform group-hover:-translate-x-0.5" />
            Library
          </Link>
          {meta && (
            <span className="shrink-0 rounded-md bg-white/[0.07] px-2.5 py-1 font-mono text-[0.66rem] uppercase tracking-[0.12em] text-white/60">
              {subjectLabel(meta.subject)}
            </span>
          )}
        </div>

        {meta && (
          <p className="mt-6 truncate font-mono text-[0.72rem] uppercase tracking-[0.16em] text-white/50">
            {meta.name}
          </p>
        )}
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          Pick a section
        </h1>

        {entries && entries.length > 0 && (
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <div className="relative max-w-md flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-white/40" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder={`Filter ${entries.length} section${entries.length === 1 ? "" : "s"}`}
                className={cn(INPUT_GLASS, "pl-9")}
              />
            </div>
            {/* toc_ready redo — a steady, error-free, non-review book (the
                other two statuses have their own retry affordance above). */}
            {error === null && tocReviewDetail === null && (
              <button
                type="button"
                onClick={handleRedoReady}
                disabled={retrying}
                title="Re-extracts the table of contents from the source PDF and replaces the current TOC rows"
                className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-200 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {retrying ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <RotateCcw className="size-3.5" />
                )}
                Redo TOC extraction
              </button>
            )}
          </div>
        )}

        {!entries && !error && tocReviewDetail === null && (
          <div className="mt-7 flex items-center gap-2 text-sm text-white/60">
            <Loader2 className="size-3.5 animate-spin text-[#5b8dff]" />
            {statusText}
          </div>
        )}

        {tocReviewDetail !== null && (
          <div className="mt-7 flex flex-col gap-3 rounded-2xl border border-amber-400/30 bg-amber-400/[0.08] px-4 py-3 text-sm text-amber-200">
            <div className="flex flex-col gap-1">
              <span className="font-medium text-amber-100">
                The validator flagged this table of contents — review the entries below before using it.
              </span>
              {tocReviewDetail && (
                <span className="text-xs text-amber-200/80">{tocReviewDetail}</span>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleAccept}
                disabled={accepting || retrying}
                className="inline-flex w-fit items-center gap-1.5 rounded-xl bg-gradient-to-r from-amber-500/70 to-amber-400/60 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:from-amber-500/90 hover:to-amber-400/80 disabled:opacity-50"
              >
                {accepting ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Check className="size-3.5" />
                )}
                Accept anyway
              </button>
              <button
                type="button"
                onClick={handleRetry}
                disabled={retrying || accepting}
                className="inline-flex w-fit items-center gap-1.5 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-1.5 text-xs font-medium text-white/80 transition-colors hover:bg-white/[0.1] hover:text-white disabled:opacity-50"
              >
                {retrying ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <RotateCcw className="size-3.5" />
                )}
                Retry extraction
              </button>
            </div>
          </div>
        )}

        {error && (
          <div className="mt-7 flex flex-col gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            <span>{error}</span>
            <button
              type="button"
              onClick={handleRetry}
              disabled={retrying}
              className="inline-flex w-fit items-center gap-1.5 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-1.5 text-xs font-medium text-white/80 transition-colors hover:bg-white/[0.1] hover:text-white disabled:opacity-50"
            >
              {retrying ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RotateCcw className="size-3.5" />
              )}
              Retry preparation
            </button>
          </div>
        )}

        {!entries && !error && tocReviewDetail === null && (
          <div className="mt-7 flex flex-col gap-2">
            {Array.from({ length: 6 }).map((_, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
              <Skeleton key={i} className="h-14 w-full rounded-2xl" />
            ))}
          </div>
        )}

        {filtered && id && (
          <motion.ol
            className="mt-7 flex flex-col gap-2"
            variants={staggerContainer}
            initial="hidden"
            animate="show"
          >
            {filtered.map((entry, idx) => (
              <motion.li key={entry.id} variants={fadeUpItem}>
                <TocRow
                  bookId={id}
                  entry={entry}
                  idx={idx}
                  onUpdated={applyEntryUpdate}
                  onDeleted={applyEntryDelete}
                />
              </motion.li>
            ))}

            {filtered.length === 0 && (
              <div className="rounded-2xl border border-dashed border-white/[0.14] bg-white/[0.03] px-4 py-8 text-center text-sm text-white/50 backdrop-blur-xl">
                No sections match "{filter}".
              </div>
            )}
          </motion.ol>
        )}
      </div>
    </div>
  );
}

interface TocRowProps {
  bookId: string;
  entry: TOCEntry;
  idx: number;
  onUpdated: (entry: TOCEntry) => void;
  onDeleted: (id: string) => void;
}

function TocRow({ bookId, entry, idx, onUpdated, onDeleted }: TocRowProps) {
  const [editing, setEditing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Inline edit form state — initialized from entry, reset whenever editing reopens.
  const [draft, setDraft] = useState({
    section_number: entry.section_number ?? "",
    section_title: entry.section_title ?? "",
    chapter_title: entry.chapter_title ?? "",
    page_start: entry.page_start ?? "",
    page_end: entry.page_end ?? "",
  });

  function startEdit(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDraft({
      section_number: entry.section_number ?? "",
      section_title: entry.section_title ?? "",
      chapter_title: entry.chapter_title ?? "",
      page_start: entry.page_start ?? "",
      page_end: entry.page_end ?? "",
    });
    setActionError(null);
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
    setActionError(null);
  }

  async function saveEdit() {
    setBusy(true);
    setActionError(null);
    try {
      const patch = {
        section_number: draft.section_number.trim() || undefined,
        section_title: draft.section_title.trim() || undefined,
        chapter_title: draft.chapter_title.trim() || null,
        page_start:
          draft.page_start === "" ? null : Number(draft.page_start) || null,
        page_end: draft.page_end === "" ? null : Number(draft.page_end) || null,
      } as Parameters<typeof api.updateTocEntry>[2];
      const updated = await api.updateTocEntry(bookId, entry.id, patch);
      onUpdated(updated);
      setEditing(false);
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (
      !window.confirm(
        `Delete section "${entry.section_number} ${entry.section_title}"?\n\nAny homework jobs derived from this section will also be deleted.`,
      )
    ) {
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api.deleteTocEntry(bookId, entry.id);
      onDeleted(entry.id);
    } catch (err) {
      setActionError((err as Error).message);
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <div
        className={cn(
          "flex flex-col gap-2 rounded-2xl border bg-white/[0.06] px-3.5 py-3 backdrop-blur-xl",
          actionError ? "border-rose-500/50" : "border-[#5b8dff]/70",
        )}
      >
        <div className="flex items-center gap-2 font-mono text-[0.66rem] uppercase tracking-[0.14em] text-white/55">
          <Pencil className="size-3" />
          Editing section #{idx + 1}
        </div>
        <div className="grid grid-cols-[auto_1fr] items-center gap-2 sm:grid-cols-[auto_minmax(0,1fr)_auto_minmax(0,2fr)]">
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-white/45">
            Number
          </label>
          <Input
            value={draft.section_number}
            onChange={(e) => setDraft((d) => ({ ...d, section_number: e.target.value }))}
            disabled={busy}
            className={cn(INPUT_GLASS, "h-8")}
          />
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-white/45">
            Title
          </label>
          <Input
            value={draft.section_title}
            onChange={(e) => setDraft((d) => ({ ...d, section_title: e.target.value }))}
            disabled={busy}
            className={cn(INPUT_GLASS, "h-8")}
          />
        </div>
        <div className="grid grid-cols-[auto_1fr] items-center gap-2 sm:grid-cols-[auto_minmax(0,2fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)]">
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-white/45">
            Chapter
          </label>
          <Input
            value={draft.chapter_title}
            onChange={(e) => setDraft((d) => ({ ...d, chapter_title: e.target.value }))}
            disabled={busy}
            placeholder="(optional)"
            className={cn(INPUT_GLASS, "h-8")}
          />
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-white/45">
            P.start
          </label>
          <Input
            type="number"
            inputMode="numeric"
            value={draft.page_start}
            onChange={(e) => setDraft((d) => ({ ...d, page_start: e.target.value }))}
            disabled={busy}
            className={cn(INPUT_GLASS, "h-8")}
          />
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-white/45">
            P.end
          </label>
          <Input
            type="number"
            inputMode="numeric"
            value={draft.page_end}
            onChange={(e) => setDraft((d) => ({ ...d, page_end: e.target.value }))}
            disabled={busy}
            className={cn(INPUT_GLASS, "h-8")}
          />
        </div>
        {actionError && (
          <p className="text-[0.7rem] text-rose-300">{actionError}</p>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={saveEdit}
            disabled={busy || !draft.section_title.trim()}
            className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] px-3 py-1.5 text-xs font-medium text-white transition-transform hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
            Save
          </button>
          <button
            type="button"
            onClick={cancelEdit}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-1.5 text-xs font-medium text-white/70 transition-colors hover:bg-white/[0.1] hover:text-white"
          >
            <X className="size-3.5" />
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="group relative">
      <Link
        to={`/book/${bookId}/section/${entry.id}`}
        className={cn(
          "grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 rounded-2xl border border-white/[0.09] bg-white/[0.04] px-3.5 py-3 text-left shadow-[0_18px_50px_-40px_rgba(0,0,0,0.95)] backdrop-blur-xl transition-all",
          "hover:-translate-y-0.5 hover:border-white/[0.16] hover:bg-white/[0.06]",
          busy && "pointer-events-none opacity-50",
        )}
      >
        <span className="w-7 font-mono text-[0.7rem] tabular-nums text-white/40">
          {String(idx + 1).padStart(2, "0")}
        </span>

        <div className="flex min-w-0 flex-col gap-0.5">
          {entry.chapter_title && (
            <span className="truncate font-mono text-[0.6rem] uppercase tracking-[0.14em] text-white/45">
              {entry.chapter_title}
            </span>
          )}
          <span className="truncate text-sm font-medium text-white">
            {entry.section_number ? `${entry.section_number} · ` : ""}
            {entry.section_title}
          </span>
        </div>

        <span className="flex items-center gap-2.5">
          <SectionStatusBadge status={entry.latest_job_status ?? null} />
          {entry.page_start && (
            <span className="hidden font-mono text-[0.66rem] text-white/45 sm:inline">
              {formatPages(entry.page_start, entry.page_end)}
            </span>
          )}
          <ArrowRight className="size-3.5 text-white/35 transition-colors group-hover:text-[#9cc0ff]" />
        </span>
      </Link>

      {/* Floating edit/delete actions; visible on row hover. */}
      <span className="absolute right-2.5 top-1/2 hidden -translate-y-1/2 items-center gap-1 md:flex md:opacity-0 md:transition-opacity md:[&:has(button:focus)]:opacity-100 group-hover:md:opacity-100">
        <button
          type="button"
          onClick={startEdit}
          disabled={busy}
          title="Edit section"
          className="grid size-7 place-items-center rounded-lg border border-white/[0.12] bg-black/40 text-white/60 backdrop-blur transition-colors hover:border-[#5b8dff]/70 hover:text-white"
        >
          <Pencil className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={confirmDelete}
          disabled={busy}
          title="Delete section"
          className="grid size-7 place-items-center rounded-lg border border-white/[0.12] bg-black/40 text-white/60 backdrop-blur transition-colors hover:border-rose-500/70 hover:text-rose-300"
        >
          {busy ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Trash2 className="size-3.5" />
          )}
        </button>
      </span>

      {actionError && (
        <p className="mt-1 px-3.5 text-[0.7rem] text-rose-300">{actionError}</p>
      )}
    </div>
  );
}

function SectionStatusBadge({ status }: { status: JobStatus | null }) {
  if (!status) return null;
  const map: Record<
    JobStatus,
    {
      label: string;
      icon: React.ReactNode;
      cls: string;
    }
  > = {
    done: {
      label: "Ready",
      icon: <CheckCircle2 className="size-3" />,
      cls: "border-emerald-400/25 bg-emerald-400/10 text-emerald-300",
    },
    running: {
      label: "Running",
      icon: <Loader2 className="size-3 animate-spin" />,
      cls: "border-[#5b8dff]/30 bg-[#5b8dff]/10 text-[#9cc0ff]",
    },
    pending: {
      label: "Queued",
      icon: <CircleDot className="size-3" />,
      cls: "border-[#5b8dff]/30 bg-[#5b8dff]/10 text-[#9cc0ff]",
    },
    failed: {
      label: "Failed",
      icon: <CircleX className="size-3" />,
      cls: "border-rose-500/30 bg-rose-500/10 text-rose-300",
    },
    cancelling: {
      label: "Cancelling",
      icon: <Loader2 className="size-3 animate-spin" />,
      cls: "border-white/[0.12] bg-white/[0.05] text-white/50",
    },
    cancelled: {
      label: "Cancelled",
      icon: <Ban className="size-3" />,
      cls: "border-white/[0.12] bg-white/[0.05] text-white/50",
    },
  };
  const m = map[status];
  if (!m) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[0.6rem] font-medium uppercase tracking-[0.12em]",
        m.cls,
      )}
      title={`Latest job: ${status}`}
    >
      {m.icon}
      {m.label}
    </span>
  );
}
