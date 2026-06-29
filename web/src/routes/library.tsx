import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  CheckCheck,
  HardDrive,
  Layers,
  Library,
  Loader2,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { motion } from "motion/react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  CategoryBrowser,
  compareGradeGroups,
  gradeAccent,
  gradeBadge,
  gradeKey,
  gradeLabel,
} from "@/components/category-browser";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { fadeUpItem, staggerContainer } from "@/lib/motion";
import { LANG_LABEL, langBadge } from "@/lib/language";
import { accentOf, subjectLabel, subjectLabelWithVariant } from "@/lib/subjects";
import type { Book, BookStatus, OutputLanguage } from "@/lib/types";
import { cn } from "@/lib/utils";

export function LibraryPage() {
  const {
    data: books,
    isLoading,
    error,
  } = useQuery({
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

  /** "all" means no language filter applied. */
  const [langFilter, setLangFilter] = useState<OutputLanguage | "all">("all");

  const totalBooks = books?.length ?? 0;
  const readyCount = books?.filter((b) => b.status === "toc_ready").length ?? 0;
  const totalBytes =
    books?.reduce((sum, b) => sum + (b.file_size_bytes ?? 0), 0) ?? 0;
  const totalSections =
    books?.reduce((sum, b) => sum + (b.toc?.length ?? 0), 0) ?? 0;

  /** Per-language book counts for the summary caption. */
  const langCounts = {
    uz: books?.filter((b) => b.source_language === "uz").length ?? 0,
    ru: books?.filter((b) => b.source_language === "ru").length ?? 0,
    en: books?.filter((b) => b.source_language === "en").length ?? 0,
  };

  /** Books visible after applying the language facet. */
  const visibleBooks =
    langFilter === "all" ? books : books?.filter((b) => b.source_language === langFilter);

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10 space-y-7">
        {/* Hero */}
        <header className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-start gap-4">
            <span className="grid size-14 shrink-0 place-items-center rounded-2xl border border-white/[0.12] bg-gradient-to-br from-[#7c5cff]/40 to-[#4d9bff]/30 shadow-[0_18px_40px_-18px_rgba(124,92,255,0.8)]">
              <Library className="size-7 text-white" />
            </span>
            <div>
              <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-[2.75rem]">
                Library
              </h1>
              <p className="mt-2 max-w-[58ch] text-sm leading-6 text-white/55">
                Every uploaded textbook. Open one to browse its table of contents,
                or hover a card to rename or delete it.
              </p>
            </div>
          </div>

          <Link
            to="/"
            className="inline-flex shrink-0 items-center gap-2 rounded-2xl bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] px-4 py-2.5 text-sm font-medium text-white shadow-[0_10px_26px_-12px_rgba(99,102,241,0.9)] transition-transform hover:-translate-y-0.5"
          >
            <Plus className="size-4" />
            Upload book
          </Link>
        </header>

        {error && (
          <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            Failed to load library: {(error as Error).message}
          </div>
        )}

        {/* Summary strip */}
        {books && books.length > 0 && (
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            <SummaryStat
              icon={<BookOpen className="size-5" />}
              tint="#4d9bff"
              label="Total Books"
              value={String(totalBooks)}
              caption={
                totalBooks > 0
                  ? `UZ ${langCounts.uz} · RU ${langCounts.ru} · EN ${langCounts.en}`
                  : "In the library"
              }
            />
            <SummaryStat
              icon={<CheckCheck className="size-5" />}
              tint="#34d399"
              label="Ready"
              value={`${readyCount} / ${totalBooks}`}
              caption="TOC extracted"
            />
            <SummaryStat
              icon={<HardDrive className="size-5" />}
              tint="#fb923c"
              label="Total Size"
              value={formatSize(totalBytes)}
              caption="On-disk PDFs"
            />
            <SummaryStat
              icon={<Layers className="size-5" />}
              tint="#a78bfa"
              label="Sections"
              value={formatNum(totalSections)}
              caption="Indexed across books"
            />
          </div>
        )}

        {isLoading && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
              <Skeleton key={i} className="h-[150px] w-full rounded-2xl" />
            ))}
          </div>
        )}

        {books && books.length === 0 && (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-white/[0.14] bg-white/[0.03] px-6 py-16 text-center backdrop-blur-xl">
            <BookOpen className="size-6 text-white/40" />
            <span className="text-sm text-white/55">No books yet.</span>
            <Link
              to="/"
              className="mt-1 inline-flex items-center gap-2 rounded-2xl bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] px-4 py-2.5 text-sm font-medium text-white shadow-[0_10px_26px_-12px_rgba(99,102,241,0.9)] transition-transform hover:-translate-y-0.5"
            >
              <Plus className="size-4" />
              Upload your first book
            </Link>
          </div>
        )}

        {books && books.length > 0 && (
          <>
            {/* Language filter facet */}
            <div className="flex flex-wrap gap-1">
              {(["all", "uz", "ru", "en"] as const).map((lang) => (
                <button
                  key={lang}
                  type="button"
                  onClick={() => setLangFilter(lang)}
                  className={cn(
                    "rounded-xl px-3 py-1.5 text-xs font-medium transition-colors",
                    langFilter === lang
                      ? "bg-white/[0.12] text-white"
                      : "text-white/45 hover:text-white/70",
                  )}
                >
                  {lang === "all"
                    ? `All (${totalBooks})`
                    : `${lang.toUpperCase()} (${langCounts[lang]})`}
                </button>
              ))}
            </div>

            <CategoryBrowser
            items={visibleBooks ?? []}
            getGroupKey={(b) => gradeKey(b.grade)}
            groupLabel={gradeLabel}
            groupAccent={gradeAccent}
            groupBadge={gradeBadge}
            sortGroups={compareGradeGroups}
            backLabel="All grades"
            countLabel={(items) => {
              const n = new Set(items.map((b) => b.subject)).size;
              return `${n} subject${n === 1 ? "" : "s"}`;
            }}
            renderItems={(gradeBooks) => (
              // Within a grade, drill down once more by subject.
              <CategoryBrowser
                items={gradeBooks}
                getGroupKey={(b) => b.subject}
                groupLabel={subjectLabel}
                groupAccent={accentOf}
                backLabel="All subjects"
                countLabel={(items) =>
                  `${items.length} book${items.length === 1 ? "" : "s"}`
                }
                renderItems={(group) => (
                  <motion.div
                    className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
                    variants={staggerContainer}
                    initial="hidden"
                    animate="show"
                  >
                    {group.map((book) => (
                      <motion.div key={book.id} variants={fadeUpItem} className="h-full">
                        <BookCard book={book} />
                      </motion.div>
                    ))}
                  </motion.div>
                )}
              />
            )}
          />
          </>
        )}
      </div>
    </div>
  );
}

/* ── Summary stat card ──────────────────────────────────────────────── */

function SummaryStat({
  icon,
  tint,
  label,
  value,
  caption,
}: {
  icon: ReactNode;
  tint: string;
  label: string;
  value: string;
  caption: string;
}) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/[0.09] bg-white/[0.045] p-4 shadow-[0_18px_50px_-34px_rgba(0,0,0,0.9)] backdrop-blur-xl">
      <div className="flex items-center gap-2.5">
        <span
          className="grid size-9 place-items-center rounded-xl"
          style={{ background: `${tint}22`, color: tint, border: `1px solid ${tint}33` }}
        >
          {icon}
        </span>
        <span className="text-sm font-medium text-white/70">{label}</span>
      </div>
      <div className="mt-3 font-mono text-3xl font-bold tabular-nums tracking-tight text-white">
        {value}
      </div>
      <p className="mt-1 text-xs text-white/45">{caption}</p>
    </div>
  );
}

/* ── Book card ──────────────────────────────────────────────────────── */

function BookCard({ book }: { book: Book }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(book.original_filename);
  const [actionError, setActionError] = useState<string | null>(null);

  const renameMutation = useMutation({
    mutationFn: (name: string) => api.updateBook(book.id, { original_filename: name }),
    onSuccess: () => {
      setEditing(false);
      setActionError(null);
      queryClient.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (err: Error) => setActionError(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteBook(book.id),
    onSuccess: () => {
      setActionError(null);
      queryClient.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (err: Error) => setActionError(err.message),
  });

  const [from, to] = accentOf(book.subject);
  const ready = book.status === "toc_ready";
  const inFlight = ["uploading", "toc_extracting"].includes(book.status);
  const busy = renameMutation.isPending || deleteMutation.isPending;
  const sections = book.toc?.length ?? 0;

  function startEdit(e: React.MouseEvent | React.KeyboardEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDraftName(book.original_filename);
    setEditing(true);
    setActionError(null);
  }

  function cancelEdit(e: React.MouseEvent | React.KeyboardEvent) {
    e.preventDefault();
    e.stopPropagation();
    setEditing(false);
    setActionError(null);
  }

  function saveEdit(e: React.MouseEvent | React.KeyboardEvent) {
    e.preventDefault();
    e.stopPropagation();
    const trimmed = draftName.trim();
    if (!trimmed || trimmed === book.original_filename) {
      setEditing(false);
      return;
    }
    renameMutation.mutate(trimmed);
  }

  function confirmDelete(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (
      !window.confirm(
        `Delete "${book.original_filename}" and every homework job derived from it?\n\nThis cannot be undone.`,
      )
    ) {
      return;
    }
    deleteMutation.mutate();
  }

  // Edit mode renders a form (not a Link) so input clicks don't navigate.
  if (editing) {
    return (
      <div
        className={cn(
          "flex flex-col gap-3 rounded-2xl border bg-white/[0.06] p-4 shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl",
          actionError ? "border-rose-500/50" : "border-[#5b8dff]/70",
        )}
      >
        <div className="flex items-center gap-2.5">
          <span
            className="grid size-9 shrink-0 place-items-center rounded-xl text-[#16131f]"
            style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
          >
            <Pencil className="size-4" />
          </span>
          <input
            // biome-ignore lint/a11y/noAutofocus: rename field should focus on open
            autoFocus
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveEdit(e);
              if (e.key === "Escape") cancelEdit(e);
            }}
            disabled={busy}
            placeholder="book filename"
            className="h-9 min-w-0 flex-1 rounded-xl border border-white/[0.12] bg-black/30 px-3 text-sm text-white outline-none transition-colors placeholder:text-white/30 focus:border-[#5b8dff]/70"
          />
        </div>
        <div className="flex items-center justify-end gap-1.5">
          <button
            type="button"
            onClick={saveEdit}
            disabled={busy || !draftName.trim()}
            title="Save (Enter)"
            className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] px-3 py-1.5 text-[0.78rem] font-medium text-white transition-transform hover:-translate-y-0.5 disabled:opacity-50"
          >
            {renameMutation.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <CheckCheck className="size-3.5" />
            )}
            Save
          </button>
          <button
            type="button"
            onClick={cancelEdit}
            disabled={busy}
            title="Cancel (Esc)"
            className="inline-flex items-center gap-1.5 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-1.5 text-[0.78rem] font-medium text-white/70 transition-colors hover:bg-white/[0.1]"
          >
            <X className="size-3.5" />
            Cancel
          </button>
        </div>
        {actionError && (
          <span className="text-[0.7rem] text-rose-300">{actionError}</span>
        )}
      </div>
    );
  }

  return (
    <div className="group relative h-full">
      <Link
        to={`/book/${book.id}`}
        className={cn(
          "flex h-full flex-col overflow-hidden rounded-2xl border border-white/[0.09] bg-white/[0.04] p-4 shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl transition-all hover:-translate-y-0.5 hover:border-white/[0.16] hover:bg-white/[0.06]",
          deleteMutation.isPending && "pointer-events-none opacity-50",
        )}
      >
        <div className="flex items-start gap-2.5">
          <span
            className="grid size-10 shrink-0 place-items-center rounded-xl text-sm font-bold text-[#16131f]"
            style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
          >
            {subjectLabel(book.subject).charAt(0)}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="line-clamp-2 text-sm font-semibold leading-snug text-white">
              {book.original_filename}
            </h2>
            <span className="mt-0.5 block font-mono text-[0.6rem] uppercase tracking-[0.14em] text-white/45">
              {subjectLabelWithVariant(book.subject, book.subject_variant)}
            </span>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between gap-2 border-t border-white/[0.07] pt-3">
          <div className="flex items-center gap-1.5">
            <StatusBadge status={book.status} />
            <span className={langBadge(book.source_language)}>
              {LANG_LABEL[book.source_language]}
            </span>
          </div>
          <span className="flex items-center gap-2 font-mono text-[0.66rem] text-white/45">
            {book.created_at && <span>{formatRelative(book.created_at)}</span>}
            {book.file_size_bytes != null && (
              <span>· {formatSize(book.file_size_bytes)}</span>
            )}
            {ready && sections > 0 && <span>· {sections} sec</span>}
            {inFlight && <Loader2 className="size-3 animate-spin text-[#5b8dff]" />}
          </span>
        </div>
      </Link>

      {/* Action buttons — fade in on card hover (top-right). */}
      <span className="absolute right-3 top-3 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <button
          type="button"
          onClick={startEdit}
          disabled={busy}
          title="Rename"
          className="grid size-7 place-items-center rounded-lg border border-white/[0.12] bg-black/40 text-white/60 backdrop-blur transition-colors hover:border-[#5b8dff]/70 hover:text-white"
        >
          <Pencil className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={confirmDelete}
          disabled={busy}
          title="Delete"
          className="grid size-7 place-items-center rounded-lg border border-white/[0.12] bg-black/40 text-white/60 backdrop-blur transition-colors hover:border-rose-500/70 hover:text-rose-300"
        >
          {deleteMutation.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Trash2 className="size-3.5" />
          )}
        </button>
      </span>

      {actionError && (
        <p className="mt-1 px-1 text-[0.7rem] text-rose-300">{actionError}</p>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: BookStatus }) {
  const map: Record<BookStatus, { label: string; cls: string }> = {
    toc_ready: { label: "ready", cls: "bg-emerald-400/15 text-emerald-300" },
    failed: { label: "failed", cls: "bg-rose-500/15 text-rose-300" },
    uploading: { label: "uploading", cls: "bg-sky-400/15 text-sky-300" },
    toc_extracting: { label: "indexing", cls: "bg-amber-400/15 text-amber-200" },
  };
  // Fall back to "indexing" for any status outside the known union (matches the
  // pre-rewrite catch-all; guards against a backend status the FE type predates).
  const { label, cls } = map[status] ?? map.toc_extracting;
  return (
    <span
      className={cn(
        "rounded-md px-2 py-0.5 text-[0.62rem] font-semibold uppercase tracking-wide",
        cls,
      )}
    >
      {label}
    </span>
  );
}

/* ── helpers ────────────────────────────────────────────────────────── */

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatSize(bytes: number): string {
  if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
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
