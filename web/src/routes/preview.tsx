import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import { ArrowLeft, ArrowRight, Download, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { api } from "@/lib/api";
import { subjectLabel } from "@/lib/subjects";
import type { Job } from "@/lib/types";
import { springSoft } from "@/lib/motion";
import { BACK_PILL, GLASS_BTN, PRIMARY_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";

/* Dark, high-contrast markdown renderer for the assembled phase content. */
const MD_COMPONENTS = {
  h1: ({ children }: any) => (
    <h1 className="mt-6 mb-3 text-2xl font-semibold tracking-tight text-white">{children}</h1>
  ),
  h2: ({ children }: any) => (
    <h2 className="mt-6 mb-2 text-lg font-semibold tracking-tight text-white">{children}</h2>
  ),
  h3: ({ children }: any) => (
    <h3 className="mt-4 mb-1.5 text-base font-semibold tracking-tight text-white/90">
      {children}
    </h3>
  ),
  p: ({ children }: any) => <p className="my-2 leading-relaxed text-white/70">{children}</p>,
  ul: ({ children }: any) => (
    <ul className="my-2 list-disc space-y-1 pl-6 text-white/70">{children}</ul>
  ),
  ol: ({ children }: any) => (
    <ol className="my-2 list-decimal space-y-1 pl-6 text-white/70">{children}</ol>
  ),
  li: ({ children }: any) => <li className="leading-relaxed">{children}</li>,
  blockquote: ({ children }: any) => (
    <blockquote className="my-3 border-l-2 border-[#7c5cff]/70 pl-4 italic text-white/60">
      {children}
    </blockquote>
  ),
  code: ({ inline, children }: any) =>
    inline ? (
      <code className="rounded bg-white/10 px-1 py-0.5 font-mono text-[0.85em] text-white">
        {children}
      </code>
    ) : (
      <code className="font-mono text-[0.85em]">{children}</code>
    ),
  pre: ({ children }: any) => (
    <pre className="my-3 overflow-auto rounded-xl border border-white/10 bg-black/40 p-3 text-[0.85em] text-white/80">
      {children}
    </pre>
  ),
  table: ({ children }: any) => (
    <div className="my-3 overflow-x-auto rounded-xl border border-white/10">
      <table className="w-full text-sm">{children}</table>
    </div>
  ),
  th: ({ children }: any) => (
    <th className="border-b border-white/10 bg-white/[0.06] px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-white/50">
      {children}
    </th>
  ),
  td: ({ children }: any) => (
    <td className="border-b border-white/[0.06] px-3 py-2 text-white/75">{children}</td>
  ),
  a: ({ href, children }: any) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-[#9cc0ff] underline-offset-2 hover:underline"
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

// Rotating accents so consecutive phases read as distinct, isolated blocks.
const PHASE_ACCENTS: [string, string][] = [
  ["#7c5cff", "#4d8dff"],
  ["#57e4a5", "#34d399"],
  ["#f6d365", "#fda085"],
  ["#64a8ff", "#4ee8d5"],
  ["#c18cff", "#8268ff"],
  ["#ff9466", "#ff5f7f"],
  ["#4ee8d5", "#43c6ac"],
];

function phaseTitle(name: string): string {
  return PHASE_TITLES[name] ?? name;
}

// Infra/judge states that are NOT content defects — surfaced distinctly from
// validation_warnings so an ungraded/declined phase doesn't read like a content bug.
const JUDGE_STATUS_LABEL: Record<string, string> = {
  unavailable: "judge unavailable",
  refused: "judge declined",
  major_regen_failed: "regen failed",
  major_shipped: "major issue shipped",
};

// Solver states. mismatch_regen is a SUCCESS (the solver caught a wrong key and
// the phase was regenerated) — render it green, not amber.
const SOLVER_STATUS_LABEL: Record<string, string> = {
  mismatch_regen: "answer-key fixed",
  mismatch_shipped: "key mismatch shipped",
  mismatch_regen_failed: "key regen failed",
  unavailable: "solver unavailable",
  refused: "solver declined",
  // `ok` is the clean case — no chip (mirrors judge, which shows nothing on ok).
};

const SOLVER_STATUS_CLASS: Record<string, string> = {
  mismatch_regen: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300/90",
  mismatch_shipped: "border-rose-400/30 bg-rose-400/10 text-rose-300/90",
  mismatch_regen_failed: "border-rose-400/30 bg-rose-400/10 text-rose-300/90",
  unavailable: "border-amber-400/30 bg-amber-400/10 text-amber-300/90",
  refused: "border-amber-400/30 bg-amber-400/10 text-amber-300/90",
};

function PhasesPreview({ job }: { job: Job }) {
  const phases = job.phases
    .filter((p) => p.phase_name !== "extract" && p.status === "done" && p.output_md)
    .sort((a, b) => a.phase_order - b.phase_order);

  const [active, setActive] = useState(0);
  const [dir, setDir] = useState(1);
  const reduce = useReducedMotion();

  if (phases.length === 0) {
    return (
      <div className="mt-8 rounded-2xl border border-dashed border-white/[0.14] bg-white/[0.03] px-4 py-10 text-center text-sm text-white/50 backdrop-blur-xl">
        No phase content to show.
      </div>
    );
  }

  const idx = Math.min(active, phases.length - 1);
  const p = phases[idx];
  const [from, to] = PHASE_ACCENTS[idx % PHASE_ACCENTS.length];
  const warnings = p.validation_warnings ?? [];
  const prev = idx > 0 ? phases[idx - 1] : null;
  const next = idx < phases.length - 1 ? phases[idx + 1] : null;

  // Switch to a phase "page": record direction (for the slide), jump to top.
  function go(i: number) {
    if (i === idx) return;
    setDir(i > idx ? 1 : -1);
    setActive(i);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // Apple-style enter/exit: gentle spring, direction-aware slide + fade.
  // Reduced-motion users get a pure cross-fade (no travel).
  const offset = reduce ? 0 : 28;
  const variants = {
    enter: (d: number) => ({ opacity: 0, x: d * offset }),
    center: { opacity: 1, x: 0 },
    exit: (d: number) => ({ opacity: 0, x: d * -offset }),
  };

  return (
    <div className="mt-7 space-y-5">
      {/* Phase navigation — pick a phase to open its page */}
      <nav className="flex flex-wrap gap-2" aria-label="Phases">
        {phases.map((ph, i) => {
          const isActive = i === idx;
          return (
            <motion.button
              key={ph.phase_name}
              type="button"
              onClick={() => go(i)}
              aria-current={isActive ? "page" : undefined}
              whileTap={{ scale: 0.95 }}
              transition={{ type: "spring", stiffness: 400, damping: 25 }}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors",
                isActive
                  ? "border-[#5b8dff]/70 bg-[#5b8dff]/15 text-white"
                  : "border-white/[0.1] bg-white/[0.04] text-white/65 hover:border-white/[0.2] hover:bg-white/[0.08] hover:text-white",
              )}
            >
              <span className={cn("font-mono", isActive ? "text-white/70" : "text-white/40")}>
                {String(i + 1).padStart(2, "0")}
              </span>
              {phaseTitle(ph.phase_name)}
            </motion.button>
          );
        })}
      </nav>

      {/* The single active phase — its own page, animated on change */}
      <AnimatePresence mode="wait" custom={dir} initial={false}>
        <motion.section
          key={idx}
          custom={dir}
          variants={variants}
          initial="enter"
          animate="center"
          exit="exit"
          transition={springSoft}
          className="flex min-h-[60vh] flex-col overflow-hidden rounded-2xl border border-white/[0.09] bg-white/[0.04] shadow-[0_18px_50px_-40px_rgba(0,0,0,0.95)] backdrop-blur-xl"
        >
          <header className="flex flex-wrap items-center gap-3 border-b border-white/[0.08] px-5 py-3.5">
            <span
              className="grid size-8 shrink-0 place-items-center rounded-xl text-xs font-bold text-[#16131f]"
              style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
            >
              {idx + 1}
            </span>
            <h2 className="text-lg font-semibold tracking-tight text-white">
              {phaseTitle(p.phase_name)}
            </h2>
            <span className="font-mono text-[0.66rem] uppercase tracking-[0.12em] text-white/35">
              {idx + 1} / {phases.length}
            </span>
            {warnings.length > 0 && (
              <span className="rounded-md bg-amber-400/15 px-2 py-0.5 text-[0.7rem] font-medium text-amber-200">
                ⚠ {warnings.length}
              </span>
            )}
            {p.judge_status && JUDGE_STATUS_LABEL[p.judge_status] && (
              <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 font-mono text-[0.62rem] uppercase tracking-wider text-amber-300/90">
                {JUDGE_STATUS_LABEL[p.judge_status]}
              </span>
            )}
            {p.solver_status && SOLVER_STATUS_LABEL[p.solver_status] && (
              <span className={cn(
                "rounded-full border px-2 py-0.5 font-mono text-[0.62rem] uppercase tracking-wider",
                SOLVER_STATUS_CLASS[p.solver_status] ?? "border-amber-400/30 bg-amber-400/10 text-amber-300/90",
              )}>
                {SOLVER_STATUS_LABEL[p.solver_status]}
              </span>
            )}
          </header>

          {warnings.length > 0 && (
            <ul className="list-disc border-b border-white/[0.06] px-5 py-3 pl-9 text-xs text-amber-200/80">
              {warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}

          <div className="px-5 py-4">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeRaw]}
              components={MD_COMPONENTS}
            >
              {p.output_md ?? ""}
            </ReactMarkdown>
          </div>

          {/* Page navigation: previous / next phase */}
          <footer className="mt-auto flex items-center justify-between gap-3 border-t border-white/[0.08] px-5 py-3.5">
            {prev ? (
              <motion.button
                type="button"
                onClick={() => go(idx - 1)}
                whileTap={{ scale: 0.96 }}
                className={cn(GLASS_BTN, "px-3.5 py-2 text-xs")}
              >
                <ArrowLeft className="size-3.5" />
                {phaseTitle(prev.phase_name)}
              </motion.button>
            ) : (
              <span />
            )}

            <span className="shrink-0 text-xs text-white/40">
              {idx + 1} / {phases.length}
            </span>

            {next ? (
              <motion.button
                type="button"
                onClick={() => go(idx + 1)}
                whileTap={{ scale: 0.96 }}
                className={cn(GLASS_BTN, "px-3.5 py-2 text-xs")}
              >
                {phaseTitle(next.phase_name)}
                <ArrowRight className="size-3.5" />
              </motion.button>
            ) : (
              <motion.a
                href={api.jobDownloadUrl(job.id)}
                download
                whileTap={{ scale: 0.96 }}
                className={cn(PRIMARY_BTN, "px-3.5 py-2 text-xs")}
              >
                <Download className="size-3.5" />
                Download .zip
              </motion.a>
            )}
          </footer>
        </motion.section>
      </AnimatePresence>
    </div>
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
      <div className="relative min-h-[calc(100vh-9rem)]">
        <SpaceBackdrop />
        <div className="relative z-10 flex items-center gap-2 text-sm text-white/60">
          <Loader2 className="size-4 animate-spin text-[#5b8dff]" /> Loading homework…
        </div>
      </div>
    );
  }

  if (error || !job || job.status !== "done") {
    return (
      <div className="relative min-h-[calc(100vh-9rem)]">
        <SpaceBackdrop />
        <div className="relative z-10">
          <h1 className="text-3xl font-semibold tracking-tight text-white">Not ready</h1>
          <p className="mt-2 text-sm text-white/55">
            Homework hasn't been assembled yet for this job.
          </p>
          <Link to={`/job/${id}`} className={cn(BACK_PILL, "mt-6")}>
            <ArrowLeft className="size-4" /> Back to job
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10">
        <div className="flex items-center justify-between gap-3">
          <Link to={`/job/${id}`} className={BACK_PILL}>
            <ArrowLeft className="size-4 shrink-0 transition-transform group-hover:-translate-x-0.5" />
            Back to job
          </Link>
          <a href={api.jobDownloadUrl(job.id)} download className={PRIMARY_BTN}>
            <Download className="size-4" /> Download .zip
          </a>
        </div>

        <h1 className="mt-6 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
          Homework packet
        </h1>
        <p className="mt-2 flex flex-wrap items-center gap-x-2 font-mono text-[0.72rem] uppercase tracking-[0.16em] text-white/50">
          <span>{subjectLabel(job.subject)}</span>
          {job.provider && <span className="text-white/25">·</span>}
          {job.provider && <span>{job.provider}</span>}
        </p>

        <PhasesPreview job={job} />
      </div>
    </div>
  );
}
