import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  CircleX,
  Eye,
  Loader2,
  RefreshCcw,
  Sparkles,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Eyebrow } from "@/components/eyebrow";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { JobStatus } from "@/lib/types";
import { cn, formatPages } from "@/lib/utils";

export function SectionPage() {
  const { bookId, sectionId } = useParams<{ bookId: string; sectionId: string }>();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<"new" | "regen" | null>(null);

  const { data: book, isLoading } = useQuery({
    queryKey: ["book", bookId],
    queryFn: () => (bookId ? api.getBook(bookId) : Promise.reject(new Error("no id"))),
    enabled: Boolean(bookId),
    refetchOnWindowFocus: true,
  });

  const section = book?.toc?.find((e) => e.id === sectionId);
  const existingJobId = section?.latest_job_id ?? null;
  const existingStatus = (section?.latest_job_status ?? null) as JobStatus | null;

  async function handleGenerate(force: boolean) {
    if (!bookId || !sectionId) return;
    setBusy(force ? "regen" : "new");
    try {
      const job = await api.generate(bookId, sectionId, force);
      navigate(`/job/${job.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Generate failed";
      toast.error(msg);
      setBusy(null);
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-(--color-ink-muted)">
        <Loader2 className="size-3.5 animate-spin" />
        Loading section…
      </div>
    );
  }

  if (!book || !section) {
    return (
      <>
        <Eyebrow>Section</Eyebrow>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">Section not found</h1>
        <p className="mt-2 text-sm text-(--color-ink-soft)">
          This section may have been removed, or the URL is malformed.
        </p>
        <Button asChild variant="secondary" className="mt-6">
          <Link to="/library">
            <ArrowLeft className="size-3.5" />
            Back to library
          </Link>
        </Button>
      </>
    );
  }

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <Link
          to={`/book/${book.id}`}
          className="inline-flex items-center gap-1.5 font-mono text-[0.7rem] uppercase tracking-[0.14em] text-(--color-ink-muted) transition-colors hover:text-(--color-ink)"
        >
          <ArrowLeft className="size-3.5" />
          {book.original_filename}
        </Link>
        <Badge variant="neutral">{book.subject}</Badge>
      </div>

      {section.chapter_title && (
        <p className="mt-6 font-mono text-[0.7rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
          {section.chapter_title}
        </p>
      )}

      <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
        {section.section_number ? `${section.section_number} · ` : ""}
        {section.section_title}
      </h1>

      {section.page_start && (
        <p className="mt-2 font-mono text-sm text-(--color-ink-muted)">
          {formatPages(section.page_start, section.page_end)}
        </p>
      )}

      {/* Existing-homework-aware action panel */}
      <ActionPanel
        existingJobId={existingJobId}
        existingStatus={existingStatus}
        busy={busy}
        onGenerate={() => handleGenerate(false)}
        onRegenerate={() => handleGenerate(true)}
      />
    </>
  );
}

interface ActionPanelProps {
  existingJobId: string | null;
  existingStatus: JobStatus | null;
  busy: "new" | "regen" | null;
  onGenerate: () => void;
  onRegenerate: () => void;
}

function ActionPanel({
  existingJobId,
  existingStatus,
  busy,
  onGenerate,
  onRegenerate,
}: ActionPanelProps) {
  // No existing job — fresh generate
  if (!existingJobId) {
    return (
      <section className="mt-8 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-5">
        <h2 className="text-sm font-semibold tracking-tight text-(--color-ink)">
          Generate homework
        </h2>
        <p className="mt-1.5 text-sm leading-relaxed text-(--color-ink-soft)">
          Run the curriculum pipeline against this section. It will read the lesson, classify
          difficulty, and produce the assembled study packet.
        </p>
        <Button onClick={onGenerate} disabled={busy !== null} className="mt-4">
          {busy === "new" ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Sending to compositor…
            </>
          ) : (
            <>
              <Sparkles className="size-4" />
              Generate homework
              <ArrowRight className="size-4" />
            </>
          )}
        </Button>
      </section>
    );
  }

  // Existing job → show appropriate primary action by status
  if (existingStatus === "done") {
    return (
      <section className="mt-8 overflow-hidden rounded-(--radius-md) border border-[oklch(0.78_0.10_145_/_30%)] bg-[oklch(0.78_0.10_145_/_6%)]">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[oklch(0.78_0.10_145_/_25%)] px-5 py-3">
          <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-(--color-success)">
            <CheckCircle2 className="size-3.5" />
            Homework already generated
          </span>
        </header>
        <div className="p-5">
          <p className="text-sm leading-relaxed text-(--color-ink-soft)">
            This section has a finished homework session. Open the preview to read or download
            it, or regenerate from scratch with a fresh pipeline run.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button asChild>
              <Link to={`/preview/${existingJobId}`}>
                <Eye className="size-4" />
                Open homework
                <ArrowRight className="size-4" />
              </Link>
            </Button>
            <Button asChild variant="secondary">
              <Link to={`/job/${existingJobId}`}>View pipeline</Link>
            </Button>
            <Button
              variant="ghost"
              onClick={onRegenerate}
              disabled={busy !== null}
              className="ml-auto text-(--color-ink-muted)"
            >
              {busy === "regen" ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  Regenerating…
                </>
              ) : (
                <>
                  <RefreshCcw className="size-3.5" />
                  Regenerate
                </>
              )}
            </Button>
          </div>
        </div>
      </section>
    );
  }

  if (existingStatus === "running" || existingStatus === "pending") {
    return (
      <section className="mt-8 rounded-(--radius-md) border border-(--color-accent-border) bg-(--color-accent-soft) p-5">
        <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-(--color-accent)">
          <Loader2 className="size-3.5 animate-spin" />
          {existingStatus === "running" ? "Generating now" : "Queued"}
        </span>
        <p className="mt-2 text-sm leading-relaxed text-(--color-ink-soft)">
          A homework session is already in flight for this section. Watch the live pipeline.
        </p>
        <Button asChild className="mt-4">
          <Link to={`/job/${existingJobId}`}>
            <Eye className="size-4" />
            Watch progress
            <ArrowRight className="size-4" />
          </Link>
        </Button>
      </section>
    );
  }

  // failed
  return (
    <section
      className={cn(
        "mt-8 rounded-(--radius-md) border p-5",
        "border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_6%)]",
      )}
    >
      <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-(--color-error)">
        <CircleX className="size-3.5" />
        Last run failed
      </span>
      <p className="mt-2 text-sm leading-relaxed text-(--color-ink-soft)">
        The previous generation for this section didn't finish. You can inspect the failed
        pipeline or kick off a fresh attempt.
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button onClick={onGenerate} disabled={busy !== null}>
          {busy === "new" ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Retrying…
            </>
          ) : (
            <>
              <RefreshCcw className="size-4" />
              Try again
            </>
          )}
        </Button>
        <Button asChild variant="secondary">
          <Link to={`/job/${existingJobId}`}>
            <Eye className="size-3.5" />
            See what failed
          </Link>
        </Button>
      </div>
    </section>
  );
}
