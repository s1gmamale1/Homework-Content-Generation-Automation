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
import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { tapScale } from "@/lib/motion";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { subjectLabel } from "@/lib/subjects";
import type { JobStatus } from "@/lib/types";
import { CARD, GLASS_BTN, PRIMARY_BTN, SELECT_TRIGGER } from "@/lib/ui";
import { cn, formatPages } from "@/lib/utils";

export function SectionPage() {
  const { bookId, sectionId } = useParams<{ bookId: string; sectionId: string }>();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<"new" | "regen" | null>(null);
  const [provider, setProvider] = useState<string>("claude");
  const [model, setModel] = useState<string | null>(null);

  const { data: book, isLoading } = useQuery({
    queryKey: ["book", bookId],
    queryFn: () => (bookId ? api.getBook(bookId) : Promise.reject(new Error("no id"))),
    enabled: Boolean(bookId),
    refetchOnWindowFocus: true,
  });

  const { data: manifest, isLoading: manifestLoading } = useQuery({
    queryKey: ["agent-models"],
    queryFn: () => api.getAgentModels(),
    staleTime: 1000 * 60 * 60, // 1h — manifest rarely changes
  });

  // When the manifest loads (or the selected provider changes), reset the
  // model to that provider's first entry. Until the manifest is here we
  // hold model=null so the request body sends "use provider default".
  useEffect(() => {
    if (!manifest) return;
    const firstModel = manifest.providers[provider]?.[0] ?? null;
    setModel(firstModel);
  }, [manifest, provider]);

  const section = book?.toc?.find((e) => e.id === sectionId);
  const existingJobId = section?.latest_job_id ?? null;
  const existingStatus = (section?.latest_job_status ?? null) as JobStatus | null;

  async function handleGenerate(force: boolean) {
    if (!bookId || !sectionId) return;
    setBusy(force ? "regen" : "new");
    // Stable per-click idempotency key. If the user double-clicks Generate
    // (or the network blips and we retry), the server returns the same job
    // both times instead of creating duplicates. crypto.randomUUID is
    // available in all modern browsers; the server treats unknown keys as
    // "first time, create new" anyway, so absence is not a failure mode.
    const idempotencyKey = crypto.randomUUID();
    try {
      const job = await api.generate(bookId, sectionId, {
        force,
        idempotencyKey,
        provider,
        model,
      });
      navigate(`/job/${job.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Generate failed";
      toast.error(msg);
      setBusy(null);
    }
  }

  if (isLoading) {
    return (
      <div className="relative min-h-[calc(100vh-9rem)]">
        <SpaceBackdrop />
        <div className="relative z-10 flex items-center gap-2 text-sm text-white/60">
          <Loader2 className="size-3.5 animate-spin text-[#5b8dff]" />
          Loading section…
        </div>
      </div>
    );
  }

  if (!book || !section) {
    return (
      <div className="relative min-h-[calc(100vh-9rem)]">
        <SpaceBackdrop />
        <div className="relative z-10">
          <h1 className="text-3xl font-semibold tracking-tight text-white">
            Section not found
          </h1>
          <p className="mt-2 text-sm text-white/55">
            This section may have been removed, or the URL is malformed.
          </p>
          <Link to="/library" className={cn(GLASS_BTN, "mt-6")}>
            <ArrowLeft className="size-4" />
            Back to library
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10 space-y-7">
        {/* Back nav + subject */}
        <div className="flex items-center justify-between gap-3">
          <Link
            to={`/book/${book.id}`}
            className="group inline-flex max-w-[70%] items-center gap-2 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-2 text-sm font-medium text-white/75 transition-colors hover:bg-white/[0.1] hover:text-white"
          >
            <ArrowLeft className="size-4 shrink-0 transition-transform group-hover:-translate-x-0.5" />
            <span className="truncate">Back to {book.original_filename}</span>
          </Link>
          <span className="shrink-0 rounded-md bg-white/[0.07] px-2.5 py-1 font-mono text-[0.66rem] uppercase tracking-[0.12em] text-white/60">
            {subjectLabel(book.subject)}
          </span>
        </div>

        {/* Section title */}
        <div>
          {section.chapter_title && (
            <p className="font-mono text-[0.7rem] uppercase tracking-[0.16em] text-white/45">
              {section.chapter_title}
            </p>
          )}
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
            {section.section_number ? `${section.section_number} · ` : ""}
            {section.section_title}
          </h1>
          {section.page_start && (
            <p className="mt-2 font-mono text-sm text-white/50">
              {formatPages(section.page_start, section.page_end)}
            </p>
          )}
        </div>

        {/* Provider + model picker */}
        <AgentPicker
          manifest={manifest}
          manifestLoading={manifestLoading}
          provider={provider}
          onProviderChange={setProvider}
          model={model}
          onModelChange={setModel}
        />

        {/* Existing-homework-aware action panel */}
        <ActionPanel
          existingJobId={existingJobId}
          existingStatus={existingStatus}
          busy={busy}
          manifestLoading={manifestLoading}
          onGenerate={() => handleGenerate(false)}
          onRegenerate={() => handleGenerate(true)}
        />
      </div>
    </div>
  );
}

interface AgentPickerProps {
  manifest: { providers: Record<string, string[]> } | undefined;
  manifestLoading: boolean;
  provider: string;
  onProviderChange: (next: string) => void;
  model: string | null;
  onModelChange: (next: string | null) => void;
}

function AgentPicker({
  manifest,
  manifestLoading,
  provider,
  onProviderChange,
  model,
  onModelChange,
}: AgentPickerProps) {
  const providerNames = manifest ? Object.keys(manifest.providers) : [];
  const modelOptions = manifest?.providers[provider] ?? [];
  const modelDisabled = !manifest || modelOptions.length === 0;

  return (
    <section className={CARD}>
      <p className="font-mono text-[0.7rem] uppercase tracking-[0.16em] text-white/45">
        Agent
      </p>
      <h2 className="mt-1 text-base font-semibold tracking-tight text-white">
        Pick provider and model
      </h2>
      <p className="mt-1.5 text-sm leading-relaxed text-white/55">
        Choose which agent runs the pipeline. The model dropdown lists the options exposed by the
        backend manifest for the selected provider.
      </p>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-white/45">
            Provider
          </span>
          <Select
            value={provider}
            onValueChange={onProviderChange}
            disabled={manifestLoading || providerNames.length === 0}
          >
            <SelectTrigger className={SELECT_TRIGGER}>
              <SelectValue placeholder={manifestLoading ? "Loading…" : "Provider"} />
            </SelectTrigger>
            <SelectContent>
              {providerNames.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-white/45">
            Model
          </span>
          <Select
            value={model ?? ""}
            onValueChange={(value) => onModelChange(value)}
            disabled={modelDisabled}
          >
            <SelectTrigger className={SELECT_TRIGGER}>
              <SelectValue placeholder={manifestLoading ? "Loading…" : "Model"} />
            </SelectTrigger>
            <SelectContent>
              {modelOptions.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </div>
    </section>
  );
}

interface ActionPanelProps {
  existingJobId: string | null;
  existingStatus: JobStatus | null;
  busy: "new" | "regen" | null;
  manifestLoading: boolean;
  onGenerate: () => void;
  onRegenerate: () => void;
}

function ActionPanel({
  existingJobId,
  existingStatus,
  busy,
  manifestLoading,
  onGenerate,
  onRegenerate,
}: ActionPanelProps) {
  // Buttons are disabled while a generate is in flight OR while the agent
  // manifest is still loading — without the manifest we can't send a valid
  // provider/model pair.
  const disabled = busy !== null || manifestLoading;

  // No existing job — fresh generate
  if (!existingJobId) {
    return (
      <section className={CARD}>
        <h2 className="text-base font-semibold tracking-tight text-white">
          Generate homework
        </h2>
        <p className="mt-1.5 text-sm leading-relaxed text-white/55">
          Run the curriculum pipeline against this section. It will read the lesson, classify
          difficulty, and produce the assembled study packet.
        </p>
        <motion.button
          type="button"
          onClick={onGenerate}
          disabled={disabled}
          whileTap={tapScale}
          className={cn(PRIMARY_BTN, "mt-4")}
        >
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
        </motion.button>
      </section>
    );
  }

  // Existing job → show appropriate primary action by status
  if (existingStatus === "done") {
    return (
      <section className="overflow-hidden rounded-2xl border border-emerald-400/25 bg-emerald-400/[0.06] shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-emerald-400/20 px-5 py-3">
          <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-emerald-300">
            <CheckCircle2 className="size-3.5" />
            Homework already generated
          </span>
        </header>
        <div className="p-5">
          <p className="text-sm leading-relaxed text-white/60">
            This section has a finished homework session. Open the preview to read or download it,
            or regenerate from scratch with a fresh pipeline run.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Link to={`/preview/${existingJobId}`} className={PRIMARY_BTN}>
              <Eye className="size-4" />
              Open homework
              <ArrowRight className="size-4" />
            </Link>
            <Link to={`/job/${existingJobId}`} className={GLASS_BTN}>
              View pipeline
            </Link>
            <motion.button
              type="button"
              onClick={onRegenerate}
              disabled={disabled}
              whileTap={tapScale}
              className="ml-auto inline-flex items-center gap-2 rounded-xl px-3 py-2.5 text-sm font-medium text-white/55 transition-colors hover:text-white disabled:opacity-50"
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
            </motion.button>
          </div>
        </div>
      </section>
    );
  }

  if (existingStatus === "running" || existingStatus === "pending") {
    return (
      <section className="rounded-2xl border border-[#5b8dff]/30 bg-[#5b8dff]/[0.07] p-5 shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl">
        <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-[#9cc0ff]">
          <Loader2 className="size-3.5 animate-spin" />
          {existingStatus === "running" ? "Generating now" : "Queued"}
        </span>
        <p className="mt-2 text-sm leading-relaxed text-white/60">
          A homework session is already in flight for this section. Watch the live pipeline.
        </p>
        <Link to={`/job/${existingJobId}`} className={cn(PRIMARY_BTN, "mt-4")}>
          <Eye className="size-4" />
          Watch progress
          <ArrowRight className="size-4" />
        </Link>
      </section>
    );
  }

  // failed (or cancelled)
  return (
    <section className="rounded-2xl border border-rose-500/30 bg-rose-500/[0.07] p-5 shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl">
      <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-rose-300">
        <CircleX className="size-3.5" />
        Last run failed
      </span>
      <p className="mt-2 text-sm leading-relaxed text-white/60">
        The previous generation for this section didn't finish. You can inspect the failed pipeline
        or kick off a fresh attempt.
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <motion.button
          type="button"
          onClick={onGenerate}
          disabled={disabled}
          whileTap={tapScale}
          className={PRIMARY_BTN}
        >
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
        </motion.button>
        <Link to={`/job/${existingJobId}`} className={GLASS_BTN}>
          <Eye className="size-4" />
          See what failed
        </Link>
      </div>
    </section>
  );
}
