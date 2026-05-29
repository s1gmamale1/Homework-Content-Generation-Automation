import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, Loader2 } from "lucide-react";
import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { BossFight } from "@/components/boss-fight/boss-fight";
import { Eyebrow } from "@/components/eyebrow";
import { FlashcardDeck } from "@/components/flashcards/flashcard-deck";
import { GameCard } from "@/components/games/game-card";
import { MemorySprint } from "@/components/memory-sprint/memory-sprint";
import { ReadingExperience } from "@/components/reading/reading-experience";
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

  // Split the assembled MD into ordered segments around the structured-phase
  // headings. Each segment is either an MD chunk or a structured renderer
  // placeholder. Plain MD wins when the *_json is empty (extraction failed),
  // so users still see *something* per phase.
  type SegmentKind =
    | "md"
    | "flashcards"
    | "memory_sprint"
    | "games"
    | "final_challenge"
    | "reading";

  type Segment = { kind: "md"; content: string } | { kind: Exclude<SegmentKind, "md"> };

  const segments = useMemo<Segment[]>(() => {
    const md = job?.assembled_md ?? "";
    if (!md) return [];

    const has = {
      flashcards: (job?.flashcards_json?.cards ?? []).length > 0,
      memory_sprint: (job?.memory_sprint_json?.items ?? []).length > 0,
      games: (job?.games_json?.games ?? []).length > 0,
      final_challenge: (job?.final_challenge_json?.questions ?? []).length > 0,
      reading: Boolean(job?.reading_json?.passage_md),
    };

    // Heading regex per phase. `.title()` in Python converts e.g.
    // "memory-sprint" → "Memory Sprint", "final-challenge" → "Final Challenge".
    const HEADINGS: Array<[Exclude<SegmentKind, "md">, RegExp]> = [
      ["flashcards", /^##\s+Flashcards\s*$/im],
      ["memory_sprint", /^##\s+Memory\s+Sprint\s*$/im],
      ["games", /^##\s+Game\s+Breaks\s*$/im],
      ["reading", /^##\s+Reading\s*$/im],
      ["final_challenge", /^##\s+Final\s+Challenge\s*$/im],
    ];

    interface Marker {
      idx: number;
      end: number;
      kind: Exclude<SegmentKind, "md">;
    }
    const markers: Marker[] = [];

    for (const [kind, re] of HEADINGS) {
      if (!has[kind]) continue;
      const m = md.match(re);
      if (!m || m.index === undefined) continue;
      const after = md.slice(m.index);
      const nextH = after.search(/\n##\s+\S/);
      const end = nextH >= 0 ? m.index + nextH : md.length;
      markers.push({ idx: m.index, end, kind });
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
  }, [
    job?.assembled_md,
    job?.flashcards_json,
    job?.memory_sprint_json,
    job?.games_json,
    job?.final_challenge_json,
    job?.reading_json,
  ]);

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

      {job.provider && (
        <p className="mt-3 font-mono text-[0.7rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
          {job.subject} · {job.provider}
        </p>
      )}

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
          if (seg.kind === "memory_sprint") {
            return (
              <section key="memory_sprint" className="mt-10">
                <h2 className="mb-4 text-xl font-semibold tracking-tight text-(--color-ink)">
                  Memory Sprint
                </h2>
                <div className="rounded-(--radius-lg) border border-(--color-border) bg-(--color-elevated) p-5">
                  <MemorySprint pack={job.memory_sprint_json ?? { items: [] }} />
                </div>
              </section>
            );
          }
          if (seg.kind === "games") {
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
          }
          if (seg.kind === "reading") {
            return (
              <section key="reading" className="mt-10">
                <h2 className="mb-4 text-xl font-semibold tracking-tight text-(--color-ink)">
                  Reading
                </h2>
                <div className="rounded-(--radius-lg) border border-(--color-border) bg-(--color-elevated) p-5">
                  <ReadingExperience
                    passage={job.reading_json ?? { passage_md: "", checkpoints: [] }}
                  />
                </div>
              </section>
            );
          }
          // final_challenge
          return (
            <section key="final_challenge" className="mt-10">
              <h2 className="mb-4 text-xl font-semibold tracking-tight text-(--color-ink)">
                Final Challenge
              </h2>
              <div className="rounded-(--radius-lg) border border-(--color-accent-border) bg-[linear-gradient(135deg,var(--color-elevated),var(--color-accent-soft))] p-5">
                <BossFight
                  challenge={job.final_challenge_json ?? { starting_hp: 100, questions: [] }}
                />
              </div>
            </section>
          );
        })}
      </article>
    </>
  );
}
