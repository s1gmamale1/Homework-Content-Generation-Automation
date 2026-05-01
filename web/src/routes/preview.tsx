import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, Loader2 } from "lucide-react";
import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { Eyebrow } from "@/components/eyebrow";
import { FlashcardDeck } from "@/components/flashcards/flashcard-deck";
import { GameCard } from "@/components/games/game-card";
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
  const {
    data: job,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["job", id, "preview"],
    queryFn: () => (id ? api.getJob(id) : Promise.reject(new Error("no id"))),
    enabled: Boolean(id),
  });

  // Split the assembled MD into ordered segments around the "## Flashcards"
  // and "## Game Breaks" headings. Each segment is either a chunk of MD or a
  // structured renderer placeholder. Plain MD wins when the corresponding
  // *_json is empty (e.g., extraction failed) so users still see *something*.
  type Segment = { kind: "md"; content: string } | { kind: "flashcards" } | { kind: "games" };

  const segments = useMemo<Segment[]>(() => {
    const md = job?.assembled_md ?? "";
    if (!md) return [];

    const cards = job?.flashcards_json?.cards ?? [];
    const games = job?.games_json?.games ?? [];

    interface Marker {
      idx: number;
      end: number;
      kind: "flashcards" | "games";
    }
    const markers: Marker[] = [];

    // ## Flashcards heading
    if (cards.length > 0) {
      const m = md.match(/^##\s+Flashcards\s*$/im);
      if (m && m.index !== undefined) {
        const after = md.slice(m.index);
        const nextH = after.search(/\n##\s+\S/);
        const end = nextH >= 0 ? m.index + nextH : md.length;
        markers.push({ idx: m.index, end, kind: "flashcards" });
      }
    }

    // ## Game Breaks heading
    if (games.length > 0) {
      const m = md.match(/^##\s+Game\s+Breaks\s*$/im);
      if (m && m.index !== undefined) {
        const after = md.slice(m.index);
        const nextH = after.search(/\n##\s+\S/);
        const end = nextH >= 0 ? m.index + nextH : md.length;
        markers.push({ idx: m.index, end, kind: "games" });
      }
    }

    if (markers.length === 0) return [{ kind: "md", content: md }];

    markers.sort((a, b) => a.idx - b.idx);
    const out: Segment[] = [];
    let cursor = 0;
    for (const mk of markers) {
      if (mk.idx > cursor) {
        out.push({ kind: "md", content: md.slice(cursor, mk.idx) });
      }
      out.push({ kind: mk.kind });
      cursor = mk.end;
    }
    if (cursor < md.length) out.push({ kind: "md", content: md.slice(cursor) });
    return out;
  }, [job?.assembled_md, job?.flashcards_json, job?.games_json]);

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
        {segments.map((seg, i) => {
          if (seg.kind === "md") {
            return (
              <ReactMarkdown
                // biome-ignore lint/suspicious/noArrayIndexKey: stable segment order
                key={`md-${i}`}
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={MD_COMPONENTS}
              >
                {seg.content}
              </ReactMarkdown>
            );
          }
          if (seg.kind === "flashcards") {
            return (
              <section key="flashcards" className="mt-10">
                <h2 className="mb-4 text-xl font-semibold tracking-tight text-(--color-ink)">
                  Flashcards
                </h2>
                <div className="rounded-(--radius-lg) border border-(--color-border) bg-(--color-elevated) p-5">
                  <FlashcardDeck cards={job.flashcards_json?.cards ?? []} />
                </div>
              </section>
            );
          }
          // games
          return (
            <section key="games" className="mt-10">
              <h2 className="mb-4 text-xl font-semibold tracking-tight text-(--color-ink)">
                Game Breaks
              </h2>
              <div className="flex flex-col gap-5">
                {(job.games_json?.games ?? []).map((g, gi) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: order is stable
                  <GameCard key={gi} game={g} />
                ))}
              </div>
            </section>
          );
        })}
      </article>
    </>
  );
}
