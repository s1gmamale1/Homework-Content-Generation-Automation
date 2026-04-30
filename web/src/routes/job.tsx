import { AnimatePresence, motion } from "motion/react";
import { ArrowDown, CheckCircle2, CircleDashed, CircleX, Loader2 } from "lucide-react";
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

  // Hydrate from REST so refreshes survive
  useEffect(() => {
    if (!id) return;
    api
      .getJob(id)
      .then((j) => {
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
      .catch(() => {
        /* SSE will catch up */
      });
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

  return (
    <>
      <Eyebrow>In Press</Eyebrow>

      <h1 className="mt-6 font-display text-[clamp(2.8rem,6.8vw,4.6rem)] font-normal leading-[1.02] tracking-[-0.025em]">
        <em className="not-italic gradient-text font-display italic">Composing</em> homework.
      </h1>

      <p className="mt-5 max-w-[58ch] text-[1.1rem] leading-[1.65] text-(--color-ink-soft)">
        The compositor reads the lesson, classifies it, and runs each phase of the curriculum
        sequence in turn. Phases appear below as they begin and resolve.
      </p>

      {difficulty && (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="mt-7">
          <Badge variant="amber" size="lg">
            <span className="size-1.5 rounded-full bg-(--color-amber) shadow-[0_0_10px_oklch(0.79_0.13_70_/_60%)]" />
            difficulty · {difficulty}
          </Badge>
        </motion.div>
      )}

      <ol className="mt-8 flex flex-col gap-3">
        <AnimatePresence initial={false}>
          {visiblePhases.map((phase) => (
            <motion.li
              key={phase.name}
              layout
              initial={{ opacity: 0, y: 12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            >
              <PhaseRow phase={phase} />
            </motion.li>
          ))}
        </AnimatePresence>
      </ol>

      <AnimatePresence>
        {downloadUrl && (
          <motion.aside
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            className="relative mt-12 overflow-hidden rounded-(--radius-xl) border border-(--color-amber) p-8 shadow-[0_30px_80px_-30px_oklch(0.79_0.13_70_/_55%)] backdrop-blur-2xl"
            style={{
              background:
                "linear-gradient(135deg, oklch(0.79 0.13 70 / 18%) 0%, oklch(0.66 0.16 35 / 10%) 50%, oklch(0.62 0.16 270 / 8%) 100%), var(--color-surface-strong)",
            }}
          >
            <div className="pointer-events-none absolute inset-0 [background:radial-gradient(circle_at_100%_0%,oklch(0.99_0.005_80_/_12%),transparent_55%),radial-gradient(circle_at_0%_100%,oklch(0.79_0.13_70_/_10%),transparent_55%)]" />
            <div className="relative z-10 flex flex-col gap-3">
              <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.18em] text-(--color-amber)">
                <span className="size-1.5 rounded-full bg-(--color-success) shadow-[0_0_10px_oklch(0.78_0.10_145_/_60%)] animate-pulse" />
                Homework ready
              </span>
              <a
                href={downloadUrl}
                download
                className="group inline-flex items-center gap-3 self-start font-display text-[clamp(1.7rem,4vw,2.3rem)] italic font-normal leading-[1.15] text-(--color-ink) transition-colors hover:text-(--color-amber)"
              >
                <span>Download the assembled session</span>
                <span className="grid size-12 place-items-center rounded-full bg-[linear-gradient(135deg,var(--color-amber),var(--color-rust))] text-[oklch(0.18_0.04_55)] shadow-[0_6px_20px_oklch(0.79_0.13_70_/_55%),inset_0_1px_0_oklch(0.99_0.005_80_/_30%)] transition-transform duration-300 group-hover:translate-y-1 group-hover:rotate-[-12deg] group-hover:scale-[1.08]">
                  <ArrowDown className="size-5" />
                </span>
              </a>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      {error && !downloadUrl && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-8 inline-flex items-center gap-2 rounded-(--radius-md) border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_10%)] px-4 py-3 text-sm text-(--color-error)"
        >
          ⚠ {error}
        </motion.div>
      )}
    </>
  );
}

function PhaseRow({ phase }: { phase: PhaseUi }) {
  const [open, setOpen] = useState(false);
  const status = phase.status;

  return (
    <article
      className={cn(
        "group relative overflow-hidden rounded-(--radius-lg) border bg-(--color-surface) backdrop-blur-md transition-[background,border-color] duration-400 ease-(--ease-soft)",
        status === "running" && "border-[oklch(0.79_0.13_70_/_28%)]",
        status === "done" &&
          "border-(--color-border-subtle) bg-[linear-gradient(135deg,oklch(0.78_0.10_145_/_4%),var(--color-surface))]",
        status === "failed" && "border-[oklch(0.70_0.16_25_/_28%)]",
      )}
    >
      <span
        className={cn(
          "absolute left-0 top-0 bottom-0 w-[3px] rounded-r-sm transition-colors",
          status === "running" &&
            "bg-[linear-gradient(180deg,var(--color-amber),var(--color-rust))] shadow-[0_0_16px_oklch(0.79_0.13_70_/_55%)]",
          status === "done" && "bg-(--color-success)",
          status === "failed" && "bg-(--color-error)",
        )}
      />

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={!phase.output}
        className="grid w-full grid-cols-[auto_1fr_auto] items-center gap-4 p-5 text-left disabled:cursor-default"
      >
        <span
          className={cn(
            "inline-flex min-w-10 items-center justify-center rounded-md border px-2.5 py-1.5 font-mono text-[0.78rem] font-medium tabular-nums transition-colors",
            status === "done"
              ? "border-[oklch(0.79_0.13_70_/_30%)] bg-[oklch(0.79_0.13_70_/_14%)] text-(--color-amber)"
              : "border-(--color-border-subtle) bg-(--color-surface) text-(--color-ink-muted)",
          )}
        >
          {String(phase.order + 1).padStart(2, "0")}
        </span>

        <span className="text-[1.02rem] font-medium leading-[1.3] text-(--color-ink)">
          {formatPhaseName(phase.name)}
        </span>

        <span
          className={cn(
            "inline-flex items-center gap-2 rounded-full border bg-(--color-surface) px-3 py-1.5 font-mono text-[0.65rem] font-medium uppercase tracking-[0.16em] whitespace-nowrap",
            status === "running" && "border-(--color-border-subtle) text-(--color-ink-muted)",
            status === "done" && "border-[oklch(0.78_0.10_145_/_30%)] text-(--color-success)",
            status === "failed" && "border-[oklch(0.70_0.16_25_/_30%)] text-(--color-error)",
          )}
        >
          {status === "running" && (
            <>
              <Loader2 className="size-3 animate-spin" />
              Running
            </>
          )}
          {status === "done" && (
            <>
              <CheckCircle2 className="size-3.5" />
              Ready
            </>
          )}
          {status === "failed" && (
            <>
              <CircleX className="size-3.5" />
              Failed
            </>
          )}
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && phase.output && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden px-5 pb-5"
          >
            <div className="prose prose-invert prose-sm max-h-72 overflow-auto rounded-(--radius-md) border border-(--color-border-subtle) bg-black/30 p-4 leading-[1.6] text-(--color-ink-soft) [&>*]:my-1 [&_h1]:mb-2 [&_h2]:mb-2 [&_h3]:mb-1 [&_pre]:bg-black/40 [&_pre]:p-2 [&_pre]:rounded-md [&_code]:font-mono [&_code]:text-[0.85em]">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{phase.output}</ReactMarkdown>
            </div>

            <div className="mt-3 flex flex-wrap gap-4 font-mono text-[0.66rem] text-(--color-ink-muted)">
              <span className="inline-flex items-center gap-1.5">
                <CircleDashed className="size-3" />↓ {formatTokens(phase.tokens_input)} in
              </span>
              <span className="inline-flex items-center gap-1.5">
                <CircleDashed className="size-3" />↑ {formatTokens(phase.tokens_output)} out
              </span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </article>
  );
}
