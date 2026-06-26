import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Ban,
  CheckCircle2,
  ChevronDown,
  CircleX,
  Download,
  Eye,
  Loader2,
  RefreshCcw,
  Sparkles,
} from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { useEventSource } from "@/hooks/use-event-source";
import { api } from "@/lib/api";
import { fadeUpItem, staggerContainer } from "@/lib/motion";
import { ApiBadge } from "@/components/fleet/launcher";
import type { JobStatus, Transport } from "@/lib/types";
import { BACK_PILL, GLASS_BTN, PRIMARY_BTN } from "@/lib/ui";
import { cn, formatPhaseName, formatTokens } from "@/lib/utils";

type PhaseUiStatus = "pending" | "running" | "done" | "failed" | "stopped";

interface PhaseUi {
  name: string;
  order: number;
  status: PhaseUiStatus;
  output?: string;
  tokens_input?: number | null;
  tokens_output?: number | null;
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-md bg-white/[0.07] px-2.5 py-1 font-mono text-[0.66rem] uppercase tracking-[0.12em] text-white/60">
      {children}
    </span>
  );
}

export function JobPage() {
  const { id } = useParams<{ id: string }>();
  const [phases, setPhases] = useState<Record<string, PhaseUi>>({});
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [parents, setParents] = useState<{ bookId: string; sectionId: string } | null>(null);
  const [agent, setAgent] = useState<{
    provider: string;
    model: string | null;
    transport: Transport;
  } | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [notionSkip, setNotionSkip] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const queryClient = useQueryClient();

  /**
   * Retry-in-place. Reuses the same job row (and pinned provider/model) — see
   * `POST /api/v1/jobs/<id>/retry`. Distinct from the section-page "Try again"
   * button, which creates a fresh job via `force=true`. After the server
   * resets the row, we clear local error/phase state so the existing
   * `useEventSource` re-enables (its `enabled` gate is `!downloadUrl && !error`)
   * and the worker's phase_started events repopulate the timeline.
   */
  async function handleRetry() {
    if (!id) return;
    setRetrying(true);
    try {
      const updated = await api.retryJob(id);
      queryClient.setQueryData(["job", id], updated);
      // Reset local UI state so the SSE stream takes over again, re-seeding the
      // full planned phase list as "pending" so the retry shows every phase.
      setError(null);
      setPhases(() => {
        const next: Record<string, PhaseUi> = {};
        (updated.planned_phases ?? []).forEach((name, i) => {
          next[name] = { name, order: i + 1, status: "pending" };
        });
        return next;
      });
      setStatus(updated.status);
      toast.success("Retry queued — pipeline restarting");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Retry failed");
    } finally {
      setRetrying(false);
    }
  }

  async function handleRetryArchive() {
    if (!id) return;
    setArchiving(true);
    try {
      const updated = await api.retryArchiveJob(id);
      queryClient.setQueryData(["job", id], updated);
      setNotionSkip(updated.notion_skip_reason ?? null);
      if (updated.notion_skip_reason) {
        toast.error(`Archive failed again: ${updated.notion_skip_reason}`);
      } else {
        toast.success("Archived to Notion");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Archive retry failed");
    } finally {
      setArchiving(false);
    }
  }

  /**
   * Cancel a pending or running job — see `POST /api/v1/jobs/<id>/cancel`.
   * A queued job comes back `cancelled`; a running one comes back
   * `cancelling` while the worker tears the task down. We mirror the
   * returned status locally so the badge/timeline reflect it immediately,
   * and seed the react-query cache the same way the retry path does.
   */
  async function handleCancel() {
    if (!id) return;
    setCancelling(true);
    try {
      const updated = await api.cancelJob(id);
      queryClient.setQueryData(["job", id], updated);
      setStatus(updated.status);
      toast.success("Cancelling…");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Cancel failed");
    } finally {
      setCancelling(false);
    }
  }

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
  }

  useEffect(() => {
    if (!id) return;
    api
      .getJob(id)
      .then((j) => {
        setParents({ bookId: j.book_id, sectionId: j.toc_entry_id });
        if (j.provider)
          setAgent({
            provider: j.provider,
            model: j.model ?? null,
            transport: j.transport,
          });
        for (const p of j.phases) {
          upsert(p.phase_name, {
            order: p.phase_order,
            status: p.status === "done" ? "done" : p.status === "failed" ? "failed" : "running",
            output: p.output_md ?? undefined,
            tokens_input: p.tokens_input,
            tokens_output: p.tokens_output,
          });
        }
        // Seed every phase this job WILL run (incl. ones not yet started) as
        // "pending", so the user sees the full plan up front instead of phases
        // popping in one wave at a time. Only fills gaps — never downgrades a
        // phase that already has a real status. order = index+1 (extract is 0).
        const planned = j.planned_phases ?? [];
        if (planned.length) {
          setPhases((prev) => {
            const next = { ...prev };
            planned.forEach((name, i) => {
              if (!next[name]) next[name] = { name, order: i + 1, status: "pending" };
            });
            return next;
          });
        }
        setStatus(j.status);
        setNotionSkip(j.notion_skip_reason ?? null);
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
      job_completed: (_data: any) => {
        setStatus("done");
        // Always build the download URL client-side so it carries the auth
        // token (?token=). The server's job_completed payload includes a
        // token-LESS download_url; trusting it yields a 401 on click (browser
        // saves the error as "download.json"). The other two setDownloadUrl
        // sites already use the token-aware api.jobDownloadUrl(id).
        setDownloadUrl(id ? api.jobDownloadUrl(id) : null);
      },
      error: (data: any) => {
        if (data?.phase_name) {
          upsert(data.phase_name, { status: "failed" });
        }
        setStatus("failed");
        setError(data?.message ?? "Stream failed.");
      },
    }),
    [id],
  );

  useEventSource(id ? api.jobStreamUrl(id) : null, handlers, {
    enabled: !downloadUrl && !error && status !== "cancelled",
  });

  /**
   * Poll to terminal while a job is `cancelling`. Cancellation publishes no
   * terminal SSE event — `pipeline.run`'s `finally: events_bus.close()` tears
   * the stream down BEFORE the worker commits `cancelled`, so the live stream
   * just ends with `onerror`. A single refetch-on-close would race that commit
   * and could re-read stale `cancelling`; polling until the status is terminal
   * is race-free. Also covers landing on a mid-cancel job via reload.
   */
  useEffect(() => {
    if (!id || status !== "cancelling") return;
    let stopped = false;
    const poll = async () => {
      try {
        const j = await api.getJob(id);
        if (stopped || j.status === "cancelling") return;
        setStatus(j.status);
        if (j.status === "done") setDownloadUrl(api.jobDownloadUrl(id));
      } catch {
        // transient; the interval will retry
      }
    };
    const timer = setInterval(poll, 1500);
    void poll();
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [id, status]);

  /**
   * Once a job is cancelling/cancelled, stop the spinners: a phase that was
   * in flight never receives a terminal SSE event (the stream is torn down on
   * cancel), so without this it keeps showing "Running" and the page looks like
   * it's still generating. Flip any lingering "running" phase to "stopped".
   */
  useEffect(() => {
    if (status !== "cancelling" && status !== "cancelled") return;
    setPhases((prev) => {
      let changed = false;
      const next: Record<string, PhaseUi> = {};
      for (const [name, p] of Object.entries(prev)) {
        // Running phases were killed; pending phases will never start now.
        if (p.status === "running" || p.status === "pending") {
          next[name] = { ...p, status: "stopped" };
          changed = true;
        } else {
          next[name] = p;
        }
      }
      return changed ? next : prev;
    });
  }, [status]);

  const visiblePhases = Object.values(phases)
    .filter((p) => p.name !== "extract" && p.name !== "classify")
    // Show in canonical flow order regardless of which wave finished first.
    .sort((a, b) => a.order - b.order);

  const doneCount = visiblePhases.filter((p) => p.status === "done").length;
  const totalCount = visiblePhases.length;

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10">
        {parents && (
          <Link to={`/book/${parents.bookId}/section/${parents.sectionId}`} className={BACK_PILL}>
            <ArrowLeft className="size-4 shrink-0 transition-transform group-hover:-translate-x-0.5" />
            Back to section
          </Link>
        )}

        <div className="mt-5 flex items-center justify-between gap-3">
          <span className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.16em] text-white/45">
            Composing
          </span>
          <div className="flex items-center gap-2">
            {totalCount > 0 && (
              <Chip>
                {doneCount}/{totalCount}
              </Chip>
            )}
            {(status === "cancelling" || status === "cancelled") && <Chip>{status}</Chip>}
            {(status === "pending" || status === "running") && (
              <button
                type="button"
                onClick={handleCancel}
                disabled={cancelling}
                className={cn(GLASS_BTN, "px-3 py-1.5 text-xs")}
              >
                {cancelling ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin" />
                    Cancelling…
                  </>
                ) : (
                  <>
                    <Ban className="size-3.5" />
                    Cancel
                  </>
                )}
              </button>
            )}
          </div>
        </div>

        {agent && (
          <p className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="font-mono text-[0.7rem] uppercase tracking-[0.16em] text-white/45">
              Agent
            </span>
            <span className="font-mono text-[0.75rem] text-white/70">
              {agent.provider} · {agent.model ?? "default"}
            </span>
            {agent.transport === "api" && <ApiBadge />}
          </p>
        )}

        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          {downloadUrl ? "Homework ready" : "Generating homework"}
        </h1>
        <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-white/55">
          Each phase reads the lesson and produces one section of the assembled study packet.
        </p>

        {visiblePhases.length === 0 && !error && !downloadUrl ? (
          <PipelineWarmup />
        ) : (
          <motion.ol
            className="mt-7 flex flex-col gap-2"
            variants={staggerContainer}
            initial="hidden"
            animate="show"
          >
            {visiblePhases.map((phase) => (
              <motion.li key={phase.name} variants={fadeUpItem}>
                <PhaseRow phase={phase} />
              </motion.li>
            ))}
          </motion.ol>
        )}

        {downloadUrl && id && <DonePanel jobId={id} downloadUrl={downloadUrl} />}

        {error && !downloadUrl && (
          <>
            <div className="mt-6 rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
              {error}
            </div>
            {parents && (
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={handleRetry}
                  disabled={retrying}
                  className={PRIMARY_BTN}
                >
                  {retrying ? (
                    <>
                      <Loader2 className="size-3.5 animate-spin" />
                      Retrying…
                    </>
                  ) : (
                    <>
                      <RefreshCcw className="size-3.5" />
                      Retry this job
                    </>
                  )}
                </button>
                <Link
                  to={`/book/${parents.bookId}/section/${parents.sectionId}`}
                  className={GLASS_BTN}
                >
                  Start fresh
                </Link>
              </div>
            )}
          </>
        )}

        {status === "done" && notionSkip && (
          <div className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-2 text-sm text-white/55">
            Not archived to Notion: {notionSkip}
            <button
              type="button"
              onClick={handleRetryArchive}
              disabled={archiving}
              className="ml-2 inline-flex items-center gap-1.5 rounded-lg border border-white/[0.15] px-2 py-1 text-xs text-white/75 hover:bg-white/[0.08] disabled:opacity-50"
            >
              {archiving ? (
                <>
                  <Loader2 className="size-3 animate-spin" />
                  Archiving…
                </>
              ) : (
                <>
                  <RefreshCcw className="size-3" />
                  Retry archive
                </>
              )}
            </button>
          </div>
        )}

        {status === "cancelled" && !downloadUrl && (
          <>
            <div className="mt-6 inline-flex items-center gap-2 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-2 text-sm text-white/55">
              <Ban className="size-3.5" />
              This job was cancelled.
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleRetry}
                disabled={retrying}
                className={PRIMARY_BTN}
              >
                {retrying ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin" />
                    Resuming…
                  </>
                ) : (
                  <>
                    <RefreshCcw className="size-3.5" />
                    Resume this job
                  </>
                )}
              </button>
              {parents && (
                <Link
                  to={`/book/${parents.bookId}/section/${parents.sectionId}`}
                  className={GLASS_BTN}
                >
                  Start fresh
                </Link>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PipelineWarmup() {
  // Shown after the user clicks Generate and lands on /job/:id, but before
  // the first phase_started event arrives over SSE. We don't yet know HOW MANY
  // phases will run (a 1-phase pick vs the full packet), so we show a single
  // indeterminate loader — never a fixed row count that would flash the wrong
  // number and then collapse.
  const RAINBOW =
    "conic-gradient(from 0deg, #7c5cff, #4d8dff, #22d3ee, #34d399, #f9d65c, #f472b6, #7c5cff)";
  return (
    <div className="mt-7 overflow-hidden rounded-2xl border border-white/[0.09] bg-white/[0.04] px-5 py-12 backdrop-blur-xl">
      <div className="flex flex-col items-center text-center">
        <div className="relative grid size-28 place-items-center">
          {/* Soft RGB glow that breathes behind the ring. */}
          <motion.div
            className="absolute size-28 rounded-full blur-2xl"
            style={{ background: RAINBOW }}
            animate={{ rotate: 360, opacity: [0.25, 0.5, 0.25] }}
            transition={{
              rotate: { duration: 3.5, repeat: Infinity, ease: "linear" },
              opacity: { duration: 2.2, repeat: Infinity, ease: "easeInOut" },
            }}
          />
          {/* The spinning rainbow ring (conic gradient, hollow centre via mask). */}
          <motion.div
            className="size-24 rounded-full"
            style={{
              background: RAINBOW,
              WebkitMask:
                "radial-gradient(farthest-side, transparent calc(100% - 8px), #000 calc(100% - 8px))",
              mask: "radial-gradient(farthest-side, transparent calc(100% - 8px), #000 calc(100% - 8px))",
            }}
            animate={{ rotate: 360 }}
            transition={{ duration: 1.1, repeat: Infinity, ease: "linear" }}
          />
          {/* Pulsing sparkle core. */}
          <motion.div
            className="absolute grid place-items-center"
            animate={{ scale: [1, 1.18, 1] }}
            transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
          >
            <Sparkles className="size-7 text-white/85 drop-shadow-[0_0_10px_rgba(124,92,255,0.8)]" />
          </motion.div>
        </div>

        <p className="mt-7 font-mono text-[0.72rem] font-medium uppercase tracking-[0.18em] text-[#9cc0ff]">
          Warming up the pipeline
        </p>
        <p className="mt-2 max-w-[44ch] text-sm leading-relaxed text-white/55">
          Reserving a worker and reading the lesson. Your phases will appear here as
          soon as the first one starts.
        </p>
      </div>
    </div>
  );
}

function DonePanel({ jobId, downloadUrl }: { jobId: string; downloadUrl: string }) {
  const { data: job } = useQuery({
    queryKey: ["job", jobId, "done"],
    queryFn: () => api.getJob(jobId),
  });

  const stats = useMemo(() => {
    const done = (job?.phases ?? []).filter(
      (p) => p.phase_name !== "extract" && p.status === "done",
    );
    const warnings = done.reduce((n, p) => n + (p.validation_warnings?.length ?? 0), 0);
    return [
      { label: "phases", value: done.length },
      { label: "warnings", value: warnings },
    ].filter((s) => s.value > 0 || s.label === "phases");
  }, [job]);

  return (
    <section className="mt-7 overflow-hidden rounded-2xl border border-emerald-400/25 bg-emerald-400/[0.06] shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-emerald-400/20 px-5 py-3">
        <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-emerald-300">
          <CheckCircle2 className="size-3.5" />
          Homework ready
        </span>
        <div className="flex items-center gap-2">
          <Link
            to={`/preview/${jobId}`}
            className="inline-flex items-center gap-1.5 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-1.5 text-xs font-medium text-white/80 transition-colors hover:bg-white/[0.1] hover:text-white"
          >
            <Eye className="size-3.5" />
            Open full preview
          </Link>
          <a
            href={downloadUrl}
            download
            className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] px-3 py-1.5 text-xs font-medium text-white transition-transform hover:-translate-y-0.5"
          >
            <Download className="size-3.5" />
            Download .zip
          </a>
        </div>
      </header>

      {stats.length > 0 && (
        <div className="px-5 py-4">
          <p className="mb-3 text-xs text-white/50">
            Open the full preview to read each generated phase.
          </p>
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {stats.map((s) => (
              <div
                key={s.label}
                className="rounded-xl border border-white/[0.08] bg-black/20 px-3 py-2"
              >
                <dt className="font-mono text-[0.65rem] uppercase tracking-[0.14em] text-white/45">
                  {s.label}
                </dt>
                <dd className="mt-0.5 text-lg font-semibold tabular-nums text-white">
                  {s.value}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </section>
  );
}

function PhaseRow({ phase }: { phase: PhaseUi }) {
  const [open, setOpen] = useState(false);
  const status = phase.status;

  return (
    <article className="overflow-hidden rounded-2xl border border-white/[0.09] bg-white/[0.04] backdrop-blur-xl">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={!phase.output}
        className="grid w-full grid-cols-[auto_1fr_auto_auto] items-center gap-3 px-3.5 py-3 text-left disabled:cursor-default"
      >
        <span className="w-7 font-mono text-[0.7rem] tabular-nums text-white/40">
          {String(phase.order + 1).padStart(2, "0")}
        </span>

        <span className="text-sm font-medium text-white">{formatPhaseName(phase.name)}</span>

        <PhaseStatus status={status} />

        {phase.output ? (
          <ChevronDown
            className={cn("size-3.5 text-white/40 transition-transform", open && "rotate-180")}
          />
        ) : (
          <span className="size-3.5" />
        )}
      </button>

      {open && phase.output && (
        <div className="border-t border-white/[0.08] px-3.5 py-3">
          <div className="max-h-72 overflow-auto rounded-xl bg-black/30 p-3 leading-relaxed text-white/70 [&>*]:my-1 [&_code]:font-mono [&_code]:text-[0.85em] [&_h1]:mb-2 [&_h1]:text-white [&_h2]:mb-2 [&_h2]:text-white [&_h3]:mb-1 [&_h3]:text-white [&_pre]:rounded-md [&_pre]:bg-black/50 [&_pre]:p-2 [&_strong]:text-white">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
              {phase.output}
            </ReactMarkdown>
          </div>

          <div className="mt-2 flex flex-wrap gap-3 font-mono text-[0.66rem] text-white/45">
            <span>↓ {formatTokens(phase.tokens_input)} in</span>
            <span>↑ {formatTokens(phase.tokens_output)} out</span>
          </div>
        </div>
      )}
    </article>
  );
}

function PhaseStatus({ status }: { status: PhaseUiStatus }) {
  if (status === "pending") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[0.7rem] font-medium text-white/35">
        <span className="size-1.5 rounded-full bg-white/30" />
        Queued
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[0.7rem] font-medium text-white/55">
        <Loader2 className="size-3 animate-spin" />
        Running
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[0.7rem] font-medium text-emerald-300">
        <CheckCircle2 className="size-3.5" />
        Ready
      </span>
    );
  }
  if (status === "stopped") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[0.7rem] font-medium text-white/40">
        <Ban className="size-3.5" />
        Stopped
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-[0.7rem] font-medium text-rose-300">
      <CircleX className="size-3.5" />
      Failed
    </span>
  );
}
