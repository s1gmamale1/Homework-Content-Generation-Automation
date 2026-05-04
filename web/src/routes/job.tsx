import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, ChevronDown, CircleX, Download, Eye, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { Eyebrow } from "@/components/eyebrow";
import { Badge } from "@/components/ui/badge";
import { useEventSource } from "@/hooks/use-event-source";
import { api } from "@/lib/api";
import type { Difficulty } from "@/lib/types";
import { cn, formatPhaseName, formatTokens } from "@/lib/utils";

type PhaseUiStatus = "running" | "done" | "failed";

interface PhaseUi {
  name: string;
  order: number;
  status: PhaseUiStatus;
  output?: string;
  tokens_input?: number | null;
  tokens_output?: number | null;
}

export function JobPage() {
  const { id } = useParams<{ id: string }>();
  const [phases, setPhases] = useState<Record<string, PhaseUi>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [difficulty, setDifficulty] = useState<Difficulty | null>(null);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [parents, setParents] = useState<{ bookId: string; sectionId: string } | null>(null);

  function upsert(name: string, partial: Partial<PhaseUi>) {
    setPhases((prev) => {
      const existing = prev[name];
      const merged: PhaseUi = {
        name,
        order: existing?.order ?? partial.order ?? 0,
        status: existing?.status ?? "running",
        output: existing?.output,
        tokens_input: existing?.tokens_input,
        tokens_output: existing?.tokens_output,
        ...partial,
      };
      return { ...prev, [name]: merged };
    });
    setOrder((prev) => (prev.includes(name) ? prev : [...prev, name]));
  }

  useEffect(() => {
    if (!id) return;
    api
      .getJob(id)
      .then((j) => {
        setParents({ bookId: j.book_id, sectionId: j.toc_entry_id });
        for (const p of j.phases) {
          upsert(p.phase_name, {
            order: p.phase_order,
            status: p.status === "done" ? "done" : p.status === "failed" ? "failed" : "running",
            output: p.output_md ?? undefined,
            tokens_input: p.tokens_input,
            tokens_output: p.tokens_output,
          });
        }
        if (j.difficulty) setDifficulty(j.difficulty);
        if (j.status === "done") setDownloadUrl(api.jobDownloadUrl(id));
        if (j.status === "failed") setError(j.error_message ?? "Job failed.");
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const handlers = useMemo(
    () => ({
      phase_started: (data: any) => {
        upsert(data?.phase_name, { order: data?.phase_order, status: "running" });
      },
      phase_completed: (data: any) => {
        upsert(data?.phase_name, {
          order: data?.phase_order,
          status: "done",
          output: data?.output_md,
          tokens_input: data?.tokens_input,
          tokens_output: data?.tokens_output,
        });
      },
      difficulty_classified: (data: any) => {
        setDifficulty(data?.difficulty ?? null);
      },
      job_completed: (data: any) => {
        setDownloadUrl(data?.download_url ?? (id ? api.jobDownloadUrl(id) : null));
      },
      error: (data: any) => {
        if (data?.phase_name) {
          upsert(data.phase_name, { status: "failed" });
        }
        setError(data?.message ?? "Stream failed.");
      },
    }),
    [id],
  );

  useEventSource(id ? api.jobStreamUrl(id) : null, handlers, {
    enabled: !downloadUrl && !error,
  });

  const visiblePhases = order
    .map((name) => phases[name])
    .filter((p): p is PhaseUi => Boolean(p))
    .filter((p) => p.name !== "extract" && p.name !== "classify");

  const doneCount = visiblePhases.filter((p) => p.status === "done").length;
  const totalCount = visiblePhases.length;

  return (
    <>
      {parents && (
        <Link
          to={`/book/${parents.bookId}/section/${parents.sectionId}`}
          className="inline-flex items-center gap-1.5 font-mono text-[0.7rem] uppercase tracking-[0.14em] text-(--color-ink-muted) transition-colors hover:text-(--color-ink)"
        >
          <ArrowLeft className="size-3.5" />
          Back to section
        </Link>
      )}

      <div className="mt-4 flex items-center justify-between gap-3">
        <Eyebrow>Composing</Eyebrow>
        <div className="flex items-center gap-2">
          {difficulty && <Badge variant="accent">difficulty · {difficulty}</Badge>}
          {totalCount > 0 && (
            <Badge variant="neutral">
              {doneCount}/{totalCount}
            </Badge>
          )}
        </div>
      </div>

      <h1 className="mt-3 text-3xl font-semibold tracking-tight text-(--color-ink)">
        {downloadUrl ? "Homework ready" : "Generating homework"}
      </h1>
      <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-(--color-ink-soft)">
        Each phase reads the lesson and produces one section of the assembled study packet.
      </p>

      {visiblePhases.length === 0 && !error && !downloadUrl ? (
        <PipelineWarmup />
      ) : (
        <ol className="mt-7 flex flex-col gap-1.5">
          {visiblePhases.map((phase) => (
            <li key={phase.name}>
              <PhaseRow phase={phase} />
            </li>
          ))}
        </ol>
      )}

      {downloadUrl && id && <DonePanel jobId={id} downloadUrl={downloadUrl} />}

      {error && !downloadUrl && (
        <div className="mt-6 rounded-(--radius-md) border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)] px-3 py-2 text-sm text-(--color-error)">
          {error}
        </div>
      )}
    </>
  );
}

function PipelineWarmup() {
  // Shown after the user clicks Generate and lands on /job/:id, but before
  // the first phase_started event arrives over SSE. Without this, the page
  // looks blank for the few seconds it takes the worker to pick up the job.
  const placeholders = [0, 1, 2, 3];
  return (
    <div className="mt-7">
      <div className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-(--color-accent)">
        <Loader2 className="size-3.5 animate-spin" />
        Warming up the pipeline
      </div>
      <p className="mt-2 max-w-[55ch] text-sm leading-relaxed text-(--color-ink-soft)">
        Queueing the section, classifying difficulty, and reserving a worker. The first phase
        usually starts within a few seconds.
      </p>
      <ol className="mt-5 flex flex-col gap-1.5" aria-hidden>
        {placeholders.map((i) => (
          <li
            key={i}
            className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-3.5 py-2.5"
          >
            <span className="font-mono text-[0.7rem] tabular-nums w-7 text-(--color-ink-muted)">
              {String(i + 1).padStart(2, "0")}
            </span>
            <span className="h-3 rounded-(--radius-sm) bg-(--color-border)/60 animate-pulse" />
            <Loader2 className="size-3 animate-spin text-(--color-ink-muted)" />
          </li>
        ))}
      </ol>
    </div>
  );
}

function DonePanel({ jobId, downloadUrl }: { jobId: string; downloadUrl: string }) {
  const { data: job } = useQuery({
    queryKey: ["job", jobId, "done"],
    queryFn: () => api.getJob(jobId),
  });

  const counts = useMemo(() => {
    return {
      games: job?.games_json?.games?.length ?? 0,
      flashcards: job?.flashcards_json?.cards?.length ?? 0,
      memorySprint: job?.memory_sprint_json?.items?.length ?? 0,
      finalChallenge: job?.final_challenge_json?.questions?.length ?? 0,
      readingCheckpoints: job?.reading_json?.checkpoints?.length ?? 0,
      assembledChars: job?.assembled_md?.length ?? 0,
    };
  }, [job]);

  const stats: Array<{ label: string; value: number }> = [
    { label: "flashcards", value: counts.flashcards },
    { label: "sprint items", value: counts.memorySprint },
    { label: "games", value: counts.games },
    { label: "boss questions", value: counts.finalChallenge },
    { label: "reading checkpoints", value: counts.readingCheckpoints },
  ].filter((s) => s.value > 0);

  return (
    <section className="mt-7 overflow-hidden rounded-(--radius-md) border border-(--color-accent-border) bg-(--color-accent-soft)">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-(--color-accent-border) px-4 py-3">
        <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-(--color-accent)">
          <CheckCircle2 className="size-3.5 text-(--color-success)" />
          Homework ready
        </span>
        <div className="flex items-center gap-2">
          <Link
            to={`/preview/${jobId}`}
            className="inline-flex items-center gap-1.5 rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-3 py-1.5 text-xs font-medium text-(--color-ink) transition-colors hover:bg-(--color-elevated-hover)"
          >
            <Eye className="size-3.5" />
            Open full preview
          </Link>
          <a
            href={downloadUrl}
            download
            className="inline-flex items-center gap-1.5 rounded-(--radius-sm) bg-(--color-accent) px-3 py-1.5 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep)"
          >
            <Download className="size-3.5" />
            Download .zip
          </a>
        </div>
      </header>

      {stats.length > 0 && (
        <div className="bg-(--color-canvas) px-5 py-4">
          <p className="mb-3 text-xs text-(--color-ink-muted)">
            Interactive components are rendered in the full preview — open it to flip flashcards,
            run the sprint, and fight the boss.
          </p>
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {stats.map((s) => (
              <div
                key={s.label}
                className="rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-3 py-2"
              >
                <dt className="font-mono text-[0.65rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
                  {s.label}
                </dt>
                <dd className="mt-0.5 text-lg font-semibold tabular-nums text-(--color-ink)">
                  {s.value}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 font-mono text-[0.66rem] text-(--color-ink-muted)">
            assembled markdown · {counts.assembledChars.toLocaleString()} chars
          </p>
        </div>
      )}
    </section>
  );
}

function PhaseRow({ phase }: { phase: PhaseUi }) {
  const [open, setOpen] = useState(false);
  const status = phase.status;

  return (
    <article className="rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated)">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={!phase.output}
        className="grid w-full grid-cols-[auto_1fr_auto_auto] items-center gap-3 px-3.5 py-2.5 text-left disabled:cursor-default"
      >
        <span className="font-mono text-[0.7rem] text-(--color-ink-muted) tabular-nums w-7">
          {String(phase.order + 1).padStart(2, "0")}
        </span>

        <span className="text-sm font-medium text-(--color-ink)">
          {formatPhaseName(phase.name)}
        </span>

        <PhaseStatus status={status} />

        {phase.output ? (
          <ChevronDown
            className={cn(
              "size-3.5 text-(--color-ink-muted) transition-transform",
              open && "rotate-180",
            )}
          />
        ) : (
          <span className="size-3.5" />
        )}
      </button>

      {open && phase.output && (
        <div className="border-t border-(--color-border) px-3.5 py-3">
          <div className="prose prose-invert prose-sm max-h-72 overflow-auto rounded-(--radius-sm) bg-(--color-canvas) p-3 leading-relaxed text-(--color-ink-soft) [&>*]:my-1 [&_h1]:mb-2 [&_h2]:mb-2 [&_h3]:mb-1 [&_pre]:bg-black/40 [&_pre]:p-2 [&_pre]:rounded-md [&_code]:font-mono [&_code]:text-[0.85em]">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
              {phase.output}
            </ReactMarkdown>
          </div>

          <div className="mt-2 flex flex-wrap gap-3 font-mono text-[0.66rem] text-(--color-ink-muted)">
            <span>↓ {formatTokens(phase.tokens_input)} in</span>
            <span>↑ {formatTokens(phase.tokens_output)} out</span>
          </div>
        </div>
      )}
    </article>
  );
}

function PhaseStatus({ status }: { status: PhaseUiStatus }) {
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[0.7rem] font-medium text-(--color-ink-muted)">
        <Loader2 className="size-3 animate-spin" />
        Running
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[0.7rem] font-medium text-(--color-success)">
        <CheckCircle2 className="size-3.5" />
        Ready
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-[0.7rem] font-medium text-(--color-error)">
      <CircleX className="size-3.5" />
      Failed
    </span>
  );
}
