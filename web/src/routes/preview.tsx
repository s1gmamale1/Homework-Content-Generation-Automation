import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, Loader2 } from "lucide-react";
import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { GameCard } from "@/components/games/game-card";
import { Eyebrow } from "@/components/eyebrow";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

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

export function PreviewPage() {
  const { id } = useParams<{ id: string }>();
  const { data: job, isLoading, error } = useQuery({
    queryKey: ["job", id, "preview"],
    queryFn: () => (id ? api.getJob(id) : Promise.reject(new Error("no id"))),
    enabled: Boolean(id),
  });

  // Strip out the "## Game Breaks" section from the assembled MD if we have
  // structured games — we render those interactively instead.
  const { mdBeforeGames, mdAfterGames, hasGames } = useMemo(() => {
    const md = job?.assembled_md ?? "";
    const games = job?.games_json?.games ?? [];
    if (games.length === 0) {
      return { mdBeforeGames: md, mdAfterGames: "", hasGames: false };
    }
    // Find the "Game Breaks" heading and split around it.
    const re = /^##\s+Game\s+Breaks\s*$/im;
    const m = md.match(re);
    if (!m || m.index === undefined) {
      return { mdBeforeGames: md, mdAfterGames: "", hasGames: true };
    }
    const before = md.slice(0, m.index);
    const after = md.slice(m.index);
    // Trim until the next ## heading
    const nextHeading = after.search(/\n##\s+\S/);
    const tail = nextHeading >= 0 ? after.slice(nextHeading) : "";
    return { mdBeforeGames: before, mdAfterGames: tail, hasGames: true };
  }, [job?.assembled_md, job?.games_json]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-(--color-ink-muted)">
        <Loader2 className="size-4 animate-spin" /> Loading homework…
      </div>
    );
  }

  if (error || !job || !job.assembled_md) {
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

      <article className="mt-8 leading-relaxed text-(--color-ink-soft)">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
          {mdBeforeGames}
        </ReactMarkdown>

        {hasGames && (
          <section className="mt-10">
            <h2 className="mb-4 text-xl font-semibold tracking-tight text-(--color-ink)">
              Game Breaks
            </h2>
            <div className="flex flex-col gap-5">
              {(job.games_json?.games ?? []).map((g, i) => (
                <GameCard key={i} game={g} />
              ))}
            </div>
          </section>
        )}

        {mdAfterGames && (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
            {mdAfterGames}
          </ReactMarkdown>
        )}
      </article>
    </>
  );
}
