import { Eyebrow } from "@/components/eyebrow";
import { api } from "@/lib/api";
import type { Job } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";

const MD_COMPONENTS = {
  h1: ({ children }: any) => (
    <h1 className="mt-8 mb-3 font-display text-3xl font-semibold tracking-tight">{children}</h1>
  ),
  h2: ({ children }: any) => (
    <h2 className="mt-7 mb-3 text-xl font-semibold tracking-tight text-(--color-ink)">
      {children}
    </h2>
  ),
  h3: ({ children }: any) => (
    <h3 className="mt-5 mb-2 text-base font-semibold tracking-tight text-(--color-ink)">
      {children}
    </h3>
  ),
  p: ({ children }: any) => <p className="my-2 leading-relaxed">{children}</p>,
  ul: ({ children }: any) => <ul className="my-2 list-disc pl-6 space-y-1">{children}</ul>,
  ol: ({ children }: any) => <ol className="my-2 list-decimal pl-6 space-y-1">{children}</ol>,
  li: ({ children }: any) => <li className="leading-relaxed">{children}</li>,
  blockquote: ({ children }: any) => (
    <blockquote className="my-3 border-l-2 border-(--color-accent) pl-4 italic text-(--color-ink-soft)">
      {children}
    </blockquote>
  ),
  code: ({ inline, children }: any) =>
    inline ? (
      <code className="rounded-(--radius-xs) bg-(--color-canvas) px-1 py-0.5 font-mono text-[0.85em]">
        {children}
      </code>
    ) : (
      <code className="font-mono text-[0.85em]">{children}</code>
    ),
  pre: ({ children }: any) => (
    <pre className="my-3 overflow-auto rounded-(--radius-md) border border-(--color-border) bg-(--color-canvas) p-3 text-[0.85em]">
      {children}
    </pre>
  ),
  table: ({ children }: any) => (
    <div className="my-3 overflow-x-auto rounded-(--radius-md) border border-(--color-border)">
      <table className="w-full text-sm">{children}</table>
    </div>
  ),
  th: ({ children }: any) => (
    <th className="border-b border-(--color-border) bg-(--color-elevated) px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-(--color-ink-muted)">
      {children}
    </th>
  ),
  td: ({ children }: any) => (
    <td className="border-b border-(--color-border)/50 px-3 py-2">{children}</td>
  ),
  a: ({ href, children }: any) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-(--color-accent) underline-offset-2 hover:underline"
    >
      {children}
    </a>
  ),
};

const PHASE_TITLES: Record<string, string> = {
  "case-based-preview": "Case-Based Preview",
  flashcards: "Flashcards",
  "memory-check": "Memory Check",
  "practice-rlc": "Real-Life Challenge",
  "practice-error-detection": "Error Detection",
  "practice-memory-match": "Memory Matching",
  "practice-tictactoe": "TicTacToe",
  "practice-jigsaw": "Jigsaw Matching",
  "practice-sentence": "Sentence Filling",
  "boss-arena": "Boss Arena",
  reflection: "Reflection",
};

function PhasesPreview({ job }: { job: Job }) {
  const phases = job.phases
    .filter((p) => p.phase_name !== "extract" && p.status === "done" && p.output_md)
    .sort((a, b) => a.phase_order - b.phase_order);

  return (
    <article className="mt-8 flex flex-col gap-10">
      {phases.map((p) => (
        <section key={p.phase_name}>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold tracking-tight text-(--color-ink)">
              {PHASE_TITLES[p.phase_name] ?? p.phase_name}
            </h2>
            {p.validation_warnings && p.validation_warnings.length > 0 && (
              <span className="rounded-(--radius-xs) bg-(--color-warn-soft,#fef3c7) px-2 py-0.5 text-[0.7rem] font-medium text-(--color-warn,#92400e)">
                ⚠ {p.validation_warnings.length}
              </span>
            )}
          </div>
          {p.validation_warnings && p.validation_warnings.length > 0 && (
            <ul className="mb-3 list-disc pl-5 text-xs text-(--color-ink-muted)">
              {p.validation_warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          <div className="rounded-(--radius-lg) border border-(--color-border) bg-(--color-elevated) p-5 leading-relaxed text-(--color-ink-soft)">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]} components={MD_COMPONENTS}>
              {p.output_md ?? ""}
            </ReactMarkdown>
          </div>
        </section>
      ))}
    </article>
  );
}

export function PreviewPage() {
  const { id } = useParams<{ id: string }>();
  const {
    data: job,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["job", id, "preview"],
    queryFn: () => (id ? api.getJob(id) : Promise.reject(new Error("no id"))),
    enabled: Boolean(id),
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-(--color-ink-muted)">
        <Loader2 className="size-4 animate-spin" /> Loading homework…
      </div>
    );
  }

  if (error || !job || job.status !== "done") {
    return (
      <>
        <Eyebrow>Preview</Eyebrow>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">Not ready</h1>
        <p className="mt-2 text-sm text-(--color-ink-soft)">
          Homework hasn't been assembled yet for this job.
        </p>
        <Link
          to={`/job/${id}`}
          className="mt-4 inline-flex items-center gap-1.5 text-sm text-(--color-accent) hover:underline"
        >
          <ArrowLeft className="size-3.5" /> Back to job
        </Link>
      </>
    );
  }

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <Link
          to={`/job/${id}`}
          className="inline-flex items-center gap-1.5 font-mono text-[0.7rem] uppercase tracking-[0.14em] text-(--color-ink-muted) transition-colors hover:text-(--color-ink)"
        >
          <ArrowLeft className="size-3.5" /> Back to job
        </Link>
        <a
          href={api.jobDownloadUrl(job.id)}
          download
          className={cn(
            "inline-flex items-center gap-2 rounded-(--radius-md) bg-(--color-accent) px-4 py-2 text-sm font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep)",
          )}
        >
          <Download className="size-4" /> Download .zip
        </a>
      </div>

      {job.provider && (
        <p className="mt-3 font-mono text-[0.7rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
          {job.subject} · {job.provider}
        </p>
      )}

      <PhasesPreview job={job} />
    </>
  );
}
