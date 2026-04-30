import { ArrowDown, CheckCircle2, ChevronDown, CircleX, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
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
        for (const p of j.phases) {
          upsert(p.phase_name, {
            order: p.phase_order,
            status:
              p.status === "done" ? "done" : p.status === "failed" ? "failed" : "running",
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
      <div className="flex items-center justify-between gap-3">
        <Eyebrow>Composing</Eyebrow>
        <div className="flex items-center gap-2">
          {difficulty && (
            <Badge variant="accent">difficulty · {difficulty}</Badge>
          )}
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

      <ol className="mt-7 flex flex-col gap-1.5">
        {visiblePhases.map((phase) => (
          <li key={phase.name}>
            <PhaseRow phase={phase} />
          </li>
        ))}
      </ol>

      {downloadUrl && (
        <a
          href={downloadUrl}
          download
          className="mt-7 inline-flex items-center justify-between gap-3 rounded-(--radius-md) border border-(--color-accent-border) bg-(--color-accent-soft) px-4 py-3 text-sm transition-colors hover:bg-(--color-accent) hover:text-[oklch(0.18_0.04_55)]"
        >
          <span className="flex items-center gap-2.5">
            <CheckCircle2 className="size-4 text-(--color-success)" />
            <span className="font-medium text-(--color-ink) group-hover:text-[oklch(0.18_0.04_55)]">
              Download homework.md
            </span>
          </span>
          <ArrowDown className="size-4 text-(--color-accent)" />
        </a>
      )}

      {error && !downloadUrl && (
        <div className="mt-6 rounded-(--radius-md) border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)] px-3 py-2 text-sm text-(--color-error)">
          {error}
        </div>
      )}
    </>
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
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{phase.output}</ReactMarkdown>
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
