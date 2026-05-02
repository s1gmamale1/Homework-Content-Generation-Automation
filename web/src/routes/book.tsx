import {
  ArrowRight,
  Check,
  CheckCircle2,
  CircleDot,
  CircleX,
  Loader2,
  Pencil,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Eyebrow } from "@/components/eyebrow";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useEventSource } from "@/hooks/use-event-source";
import { api } from "@/lib/api";
import type { JobStatus, TOCEntry } from "@/lib/types";
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

  function applyEntryUpdate(updated: TOCEntry) {
    setEntries((prev) =>
      prev ? prev.map((e) => (e.id === updated.id ? { ...e, ...updated } : e)) : prev,
    );
  }

  function applyEntryDelete(deletedId: string) {
    setEntries((prev) => (prev ? prev.filter((e) => e.id !== deletedId) : prev));
  }

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

      {filtered && id && (
        <ol className="mt-7 flex flex-col gap-1.5">
          {filtered.map((entry, idx) => (
            <li key={entry.id}>
              <TocRow
                bookId={id}
                entry={entry}
                idx={idx}
                onUpdated={applyEntryUpdate}
                onDeleted={applyEntryDelete}
              />
            </li>
          ))}

          {filtered.length === 0 && (
            <div className="rounded-(--radius-md) border border-dashed border-(--color-border) px-4 py-8 text-center text-sm text-(--color-ink-muted)">
              No sections match "{filter}".
            </div>
          )}
        </ol>
      )}
    </>
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
          "flex flex-col gap-2 rounded-(--radius-md) border bg-(--color-elevated) px-3.5 py-3",
          actionError ? "border-[oklch(0.70_0.16_25_/_50%)]" : "border-(--color-accent)",
        )}
      >
        <div className="flex items-center gap-2 font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          <Pencil className="size-3" />
          Editing section #{idx + 1}
        </div>
        <div className="grid grid-cols-[auto_1fr] items-center gap-2 sm:grid-cols-[auto_minmax(0,1fr)_auto_minmax(0,2fr)]">
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            Number
          </label>
          <Input
            value={draft.section_number}
            onChange={(e) => setDraft((d) => ({ ...d, section_number: e.target.value }))}
            disabled={busy}
            className="h-8"
          />
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            Title
          </label>
          <Input
            value={draft.section_title}
            onChange={(e) => setDraft((d) => ({ ...d, section_title: e.target.value }))}
            disabled={busy}
            className="h-8"
          />
        </div>
        <div className="grid grid-cols-[auto_1fr] items-center gap-2 sm:grid-cols-[auto_minmax(0,2fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)]">
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            Chapter
          </label>
          <Input
            value={draft.chapter_title}
            onChange={(e) => setDraft((d) => ({ ...d, chapter_title: e.target.value }))}
            disabled={busy}
            placeholder="(optional)"
            className="h-8"
          />
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            P.start
          </label>
          <Input
            type="number"
            inputMode="numeric"
            value={draft.page_start}
            onChange={(e) => setDraft((d) => ({ ...d, page_start: e.target.value }))}
            disabled={busy}
            className="h-8"
          />
          <label className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            P.end
          </label>
          <Input
            type="number"
            inputMode="numeric"
            value={draft.page_end}
            onChange={(e) => setDraft((d) => ({ ...d, page_end: e.target.value }))}
            disabled={busy}
            className="h-8"
          />
        </div>
        {actionError && (
          <p className="text-[0.7rem] text-(--color-error)">{actionError}</p>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={saveEdit}
            disabled={busy || !draft.section_title.trim()}
            className="inline-flex items-center gap-1.5 rounded-(--radius-sm) bg-(--color-accent) px-3 py-1.5 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep) disabled:opacity-50"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
            Save
          </button>
          <button
            type="button"
            onClick={cancelEdit}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-3 py-1.5 text-xs font-medium text-(--color-ink) transition-colors hover:bg-(--color-elevated-hover)"
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
          "grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-3.5 py-2.5 text-left transition-colors",
          "hover:bg-(--color-elevated-hover) hover:border-(--color-border-hover)",
          busy && "opacity-50 pointer-events-none",
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
          <SectionStatusBadge status={entry.latest_job_status ?? null} />
          {entry.page_start && (
            <span className="hidden font-mono text-[0.66rem] text-(--color-ink-muted) sm:inline">
              {formatPages(entry.page_start, entry.page_end)}
            </span>
          )}
          <ArrowRight className="size-3.5 text-(--color-ink-muted) group-hover:text-(--color-accent)" />
        </span>
      </Link>

      {/* Floating edit/delete actions; visible on row hover. */}
      <span className="absolute right-2 top-1/2 hidden -translate-y-1/2 items-center gap-1 md:flex md:opacity-0 md:transition-opacity md:[&:has(button:focus)]:opacity-100 group-hover:md:opacity-100">
        <button
          type="button"
          onClick={startEdit}
          disabled={busy}
          title="Edit section"
          className="grid size-7 place-items-center rounded-(--radius-sm) border border-(--color-border) bg-(--color-canvas)/95 text-(--color-ink-muted) backdrop-blur transition-colors hover:border-(--color-accent) hover:text-(--color-accent)"
        >
          <Pencil className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={confirmDelete}
          disabled={busy}
          title="Delete section"
          className="grid size-7 place-items-center rounded-(--radius-sm) border border-(--color-border) bg-(--color-canvas)/95 text-(--color-ink-muted) backdrop-blur transition-colors hover:border-(--color-error) hover:text-(--color-error)"
        >
          {busy ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Trash2 className="size-3.5" />
          )}
        </button>
      </span>

      {actionError && (
        <p className="mt-1 px-3.5 text-[0.7rem] text-(--color-error)">{actionError}</p>
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
      cls: "border-[oklch(0.78_0.10_145_/_30%)] bg-[oklch(0.78_0.10_145_/_10%)] text-(--color-success)",
    },
    running: {
      label: "Running",
      icon: <Loader2 className="size-3 animate-spin" />,
      cls: "border-(--color-accent-border) bg-(--color-accent-soft) text-(--color-accent)",
    },
    pending: {
      label: "Queued",
      icon: <CircleDot className="size-3" />,
      cls: "border-(--color-accent-border) bg-(--color-accent-soft) text-(--color-accent)",
    },
    failed: {
      label: "Failed",
      icon: <CircleX className="size-3" />,
      cls: "border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_10%)] text-(--color-error)",
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
