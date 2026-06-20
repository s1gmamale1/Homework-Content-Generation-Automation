import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Ban,
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
import { RoleAgentControls } from "@/components/fleet/RoleAgentControls";
import { api } from "@/lib/api";
import { safeUUID } from "@/lib/uuid";
import { subjectLabel, CONTENT_PHASES } from "@/lib/subjects";
import type {
  JobStatus,
  ProviderModelManifest,
  RoleTransport,
  Transport,
} from "@/lib/types";
import { CARD, GLASS_BTN, PRIMARY_BTN, SELECT_TRIGGER } from "@/lib/ui";
import { cn, formatPages } from "@/lib/utils";

export function SectionPage() {
  const { bookId, sectionId } = useParams<{ bookId: string; sectionId: string }>();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<"new" | "regen" | null>(null);
  const [provider, setProvider] = useState<string>("claude");
  const [model, setModel] = useState<string | null>(null);
  const [transport, setTransport] = useState<Transport>("cli");
  const [extractTransport, setExtractTransport] = useState<RoleTransport>("inherit");
  const [judgeTransport, setJudgeTransport] = useState<RoleTransport>("inherit");
  // Per-role provider/model overrides (null = role default).
  const [extractProvider, setExtractProvider] = useState<string | null>(null);
  const [extractModel, setExtractModel] = useState<string | null>(null);
  const [judgeProvider, setJudgeProvider] = useState<string | null>(null);
  const [judgeModel, setJudgeModel] = useState<string | null>(null);
  // "all" = generate the full packet (send selected_phases=null). "pick" = only
  // the checked phases. Default "all" so most users never touch it.
  const [phaseMode, setPhaseMode] = useState<"all" | "pick">("all");
  // In "pick" mode the checked set. Starts empty — the user deliberately picks
  // the phases to run and (required) uploads an md prompt for each one.
  const [selectedPhases, setSelectedPhases] = useState<Set<string>>(new Set());
  // Per-phase custom prompt {filename, text}, read in-browser — never uploaded as a file.
  const [customPrompts, setCustomPrompts] = useState<Record<string, { name: string; text: string }>>(
    {},
  );

  function togglePhase(key: string) {
    setSelectedPhases((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  function handlePhasePromptFile(key: string, e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      if (!text.trim()) {
        toast.error(`${file.name} is empty`);
        return;
      }
      setCustomPrompts((prev) => ({ ...prev, [key]: { name: file.name, text } }));
    };
    reader.onerror = () => toast.error("Couldn't read that file");
    reader.readAsText(file);
  }

  function removeCustomPrompt(key: string) {
    setCustomPrompts((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  // In "pick" mode at least one phase must be checked (the backend 400s on []).
  const noPhasePicked = phaseMode === "pick" && selectedPhases.size === 0;
  // …and every checked phase needs its own uploaded md — without it we don't
  // generate (mirrors the backend 400). These are the still-uncovered keys.
  const missingPromptKeys =
    phaseMode === "pick" ? [...selectedPhases].filter((k) => !customPrompts[k]) : [];

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

  // Only claude/gemini bill via the pay-per-token API transport; the toggle is
  // hidden for everything else. If the provider switches to one that can't do
  // api, drop back to cli so we never send an invalid transport.
  const apiSupported = manifest?.api_supported?.[provider] ?? false;
  useEffect(() => {
    if (!apiSupported && transport === "api") setTransport("cli");
  }, [apiSupported, transport]);

  // Advisory (non-blocking) note when the judge model is weaker (higher tier
  // number) than the generator. Lower tier int = stronger.
  const tiers = manifest?.tiers;
  const genTier = tiers?.[provider]?.[model ?? ""];
  const judgeTier =
    judgeProvider && judgeModel ? tiers?.[judgeProvider]?.[judgeModel] : undefined;
  const judgeWarning =
    genTier != null && judgeTier != null && judgeTier > genTier
      ? "Judge is weaker than the generator — grading may be unreliable."
      : null;

  const section = book?.toc?.find((e) => e.id === sectionId);
  const existingJobId = section?.latest_job_id ?? null;
  const existingStatus = (section?.latest_job_status ?? null) as JobStatus | null;

  // Every content phase is selectable — the backend generates all four game
  // variants for every subject, so nothing is locked by subject.
  const validPhaseKeys = CONTENT_PHASES.map((p) => p.key);

  async function handleGenerate(force: boolean) {
    if (!bookId || !sectionId) return;
    setBusy(force ? "regen" : "new");
    // Stable per-click idempotency key. If the user double-clicks Generate
    // (or the network blips and we retry), the server returns the same job
    // both times instead of creating duplicates. Use safeUUID (not
    // crypto.randomUUID directly): the latter is undefined in a non-secure
    // context — plain HTTP on a LAN IP — which silently broke this flow on the
    // fleet head. The server treats unknown keys as "first time" anyway.
    const idempotencyKey = safeUUID();
    try {
      // Only ever send phases this subject can run (defensive: a non-applicable
      // game can't be checked, but never let one slip through to a 400).
      const selected_phases =
        phaseMode === "pick"
          ? [...selectedPhases].filter((k) => validPhaseKeys.includes(k))
          : null;
      const customEntries = Object.entries(customPrompts);
      const custom_prompts = customEntries.length
        ? Object.fromEntries(customEntries.map(([k, v]) => [k, v.text]))
        : null;
      const job = await api.generate(bookId, sectionId, {
        force,
        idempotencyKey,
        provider,
        model,
        transport,
        extract_transport: extractTransport,
        judge_transport: judgeTransport,
        custom_prompts,
        selected_phases,
        extract_provider: extractProvider,
        extract_model: extractModel,
        judge_provider: judgeProvider,
        judge_model: judgeModel,
      });
      if (job.added_phases && job.added_phases.length > 0) {
        toast.info(`Also generating dependencies: ${job.added_phases.join(", ")}`);
      }
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
          apiSupported={apiSupported}
          transport={transport}
          onTransportChange={setTransport}
          extractProvider={extractProvider}
          onExtractProviderChange={setExtractProvider}
          extractModel={extractModel}
          onExtractModelChange={setExtractModel}
          extractTransport={extractTransport}
          onExtractTransportChange={setExtractTransport}
          judgeProvider={judgeProvider}
          onJudgeProviderChange={setJudgeProvider}
          judgeModel={judgeModel}
          onJudgeModelChange={setJudgeModel}
          judgeTransport={judgeTransport}
          onJudgeTransportChange={setJudgeTransport}
          judgeWarning={judgeWarning}
        />

        {/* What to generate: full packet (default) or a hand-picked subset, with
            optional per-phase prompt overrides. A custom .md replaces that phase's
            built-in prompt for this run only — never saved. */}
        <PhasePicker
          mode={phaseMode}
          onModeChange={setPhaseMode}
          selectedPhases={selectedPhases}
          onToggle={togglePhase}
          onSelectAll={() => setSelectedPhases(new Set(validPhaseKeys))}
          onClear={() => setSelectedPhases(new Set())}
          customPrompts={customPrompts}
          onUpload={handlePhasePromptFile}
          onRemoveCustom={removeCustomPrompt}
        />

        {/* Existing-homework-aware action panel */}
        <ActionPanel
          existingJobId={existingJobId}
          existingStatus={existingStatus}
          busy={busy}
          manifestLoading={manifestLoading}
          blocked={
            (transport === "api" && !model) ||
            noPhasePicked ||
            missingPromptKeys.length > 0
          }
          onGenerate={() => handleGenerate(false)}
          onRegenerate={() => handleGenerate(true)}
        />
      </div>
    </div>
  );
}

interface PhasePickerProps {
  mode: "all" | "pick";
  onModeChange: (next: "all" | "pick") => void;
  selectedPhases: Set<string>;
  onToggle: (key: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
  customPrompts: Record<string, { name: string; text: string }>;
  onUpload: (key: string, e: React.ChangeEvent<HTMLInputElement>) => void;
  onRemoveCustom: (key: string) => void;
}

/** "What should we generate?" — a full-packet/pick-phases toggle over a
 *  self-describing checklist. In "pick" mode every chosen phase REQUIRES an
 *  uploaded md prompt; generation is blocked until each one has it. */
function PhasePicker({
  mode,
  onModeChange,
  selectedPhases,
  onToggle,
  onSelectAll,
  onClear,
  customPrompts,
  onUpload,
  onRemoveCustom,
}: PhasePickerProps) {
  // Every phase is selectable — the backend runs all four game variants for
  // every subject, so nothing is locked by subject.
  const total = CONTENT_PHASES.length;
  const count = selectedPhases.size;
  // Checked phases still missing their required md prompt.
  const missing = CONTENT_PHASES.filter(
    (p) => selectedPhases.has(p.key) && !customPrompts[p.key],
  );

  return (
    <section className={CARD}>
      <p className="font-mono text-[0.7rem] uppercase tracking-[0.16em] text-white/45">
        Phases & content
      </p>
      <h2 className="mt-1 text-base font-semibold tracking-tight text-white">
        What should we generate for this lesson?
      </h2>

      {/* Full packet vs. pick — default is the full packet, so most users
          never leave this row. */}
      <div className="mt-4 inline-flex w-full max-w-md rounded-xl border border-white/[0.1] bg-white/[0.04] p-1">
        {(
          [
            { value: "all", label: "Full packet", sub: `all ${total} activities` },
            { value: "pick", label: "Pick phases", sub: "choose your own" },
          ] as const
        ).map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onModeChange(opt.value)}
            className={cn(
              "flex-1 rounded-lg px-3 py-2 text-center transition-all",
              mode === opt.value
                ? "bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] text-white shadow-[0_10px_26px_-12px_rgba(99,102,241,0.9)]"
                : "text-white/55 hover:bg-white/[0.06] hover:text-white",
            )}
          >
            <span className="block text-sm font-medium">{opt.label}</span>
            <span className="block text-[0.66rem] opacity-80">{opt.sub}</span>
          </button>
        ))}
      </div>

      {mode === "all" ? (
        <p className="mt-3 text-sm leading-relaxed text-white/55">
          We'll generate the complete study packet — all {total} activities, in order.
          Want to run only some, or replace a phase's instructions? Switch to{" "}
          <span className="font-medium text-white/75">Pick phases</span>.
        </p>
      ) : (
        <div className="mt-4 space-y-3">
          {/* Count + bulk actions */}
          <div className="flex items-center justify-between gap-3">
            <span
              className={cn(
                "text-sm",
                count === 0 || missing.length > 0
                  ? "font-medium text-rose-300"
                  : "text-white/60",
              )}
            >
              {count === 0
                ? "Pick at least one phase to generate"
                : missing.length > 0
                  ? `Upload a prompt for: ${missing.map((p) => p.label).join(", ")}`
                  : `${count} of ${total} selected — ready`}
            </span>
            <div className="flex items-center gap-1.5 text-xs">
              <button
                type="button"
                onClick={onSelectAll}
                className="rounded-lg border border-white/[0.12] px-2.5 py-1 font-medium text-white/65 transition-colors hover:bg-white/[0.06] hover:text-white"
              >
                Select all
              </button>
              <button
                type="button"
                onClick={onClear}
                className="rounded-lg border border-white/[0.12] px-2.5 py-1 font-medium text-white/65 transition-colors hover:bg-white/[0.06] hover:text-white"
              >
                Clear
              </button>
            </div>
          </div>

          {/* Phase checklist */}
          <div className="space-y-2">
            {CONTENT_PHASES.map((phase) => {
              const { key, label, icon, blurb } = phase;
              const checked = selectedPhases.has(key);
              const custom = customPrompts[key];
              // A checked phase with no uploaded md blocks generation.
              const needsPrompt = checked && !custom;
              return (
                <div
                  key={key}
                  className={cn(
                    "rounded-xl border px-3 py-2.5 transition-colors",
                    needsPrompt
                      ? "border-rose-400/30 bg-rose-400/[0.05]"
                      : checked
                        ? "border-white/[0.12] bg-white/[0.05]"
                        : "border-white/[0.06] bg-white/[0.02] opacity-60",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <label className="flex cursor-pointer items-start gap-3">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => onToggle(key)}
                        className="mt-0.5 size-4 shrink-0 accent-[#7c5cff]"
                      />
                      <span>
                        <span className="flex items-center gap-2 text-sm font-medium text-white/90">
                          <span className="text-base leading-none">{icon}</span>
                          {label}
                        </span>
                        <span className="mt-0.5 block text-xs text-white/50">{blurb}</span>
                      </span>
                    </label>

                    {/* Per-phase prompt — REQUIRED for a checked phase. */}
                    {custom ? (
                      <span className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-emerald-400/25 bg-emerald-400/[0.08] px-2 py-1 text-xs text-emerald-200">
                        <span className="max-w-[7rem] truncate font-mono">✎ {custom.name}</span>
                        <button
                          type="button"
                          onClick={() => onRemoveCustom(key)}
                          className="text-emerald-200/70 transition-colors hover:text-white"
                          aria-label={`Remove custom prompt for ${label}`}
                        >
                          ✕
                        </button>
                      </span>
                    ) : (
                      <label
                        className={cn(
                          "shrink-0 cursor-pointer whitespace-nowrap rounded-lg border px-2 py-1 text-xs font-medium transition-colors",
                          needsPrompt
                            ? "border-rose-400/40 bg-rose-400/[0.08] text-rose-200 hover:bg-rose-400/[0.14]"
                            : "border-white/[0.12] text-white/60 hover:bg-white/[0.06] hover:text-white",
                        )}
                        title="Upload the .md prompt this phase will run with (this run only)"
                      >
                        {needsPrompt ? "⬆ Upload .md (required)" : "✎ Upload .md"}
                        <input
                          type="file"
                          accept=".md,.markdown,text/markdown"
                          onChange={(e) => onUpload(key, e)}
                          className="hidden"
                        />
                      </label>
                    )}
                  </div>
                  {custom ? (
                    <p className="mt-1.5 pl-7 text-[0.7rem] text-emerald-200/70">
                      Runs this phase with your uploaded prompt (this run only — not saved).
                    </p>
                  ) : needsPrompt ? (
                    <p className="mt-1.5 pl-7 text-[0.7rem] text-rose-200/80">
                      Upload an md prompt to run this phase — without it we won't generate.
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>

          <p className="text-xs text-white/40">
            We run exactly the phases you check — each with the md prompt you upload for it.
            Every checked phase needs its own prompt before you can generate.
          </p>
        </div>
      )}
    </section>
  );
}

interface AgentPickerProps {
  manifest: ProviderModelManifest | undefined;
  manifestLoading: boolean;
  provider: string;
  onProviderChange: (next: string) => void;
  model: string | null;
  onModelChange: (next: string | null) => void;
  apiSupported: boolean;
  transport: Transport;
  onTransportChange: (next: Transport) => void;
  extractProvider: string | null;
  onExtractProviderChange: (next: string | null) => void;
  extractModel: string | null;
  onExtractModelChange: (next: string | null) => void;
  extractTransport: RoleTransport;
  onExtractTransportChange: (next: RoleTransport) => void;
  judgeProvider: string | null;
  onJudgeProviderChange: (next: string | null) => void;
  judgeModel: string | null;
  onJudgeModelChange: (next: string | null) => void;
  judgeTransport: RoleTransport;
  onJudgeTransportChange: (next: RoleTransport) => void;
  judgeWarning: string | null;
}

function AgentPicker({
  manifest,
  manifestLoading,
  provider,
  onProviderChange,
  model,
  onModelChange,
  apiSupported,
  transport,
  onTransportChange,
  extractProvider,
  onExtractProviderChange,
  extractModel,
  onExtractModelChange,
  extractTransport,
  onExtractTransportChange,
  judgeProvider,
  onJudgeProviderChange,
  judgeModel,
  onJudgeModelChange,
  judgeTransport,
  onJudgeTransportChange,
  judgeWarning,
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

      {/* CLI | API transport toggle — only for providers the backend bills via
          the pay-per-token API (claude/gemini). Hidden otherwise; transport
          pins to cli. On API the Model select above is required (a concrete
          model must be chosen — there is no "provider default" billing path). */}
      {apiSupported && (
        <div className="mt-4 flex flex-col gap-1.5">
          <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-white/45">
            Transport
          </span>
          <div className="inline-flex w-fit rounded-xl border border-white/[0.1] bg-white/[0.04] p-1">
            {(["api", "cli"] as Transport[]).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => onTransportChange(t)}
                className={cn(
                  "rounded-lg px-4 py-2 text-sm font-medium uppercase tracking-wide transition-all",
                  t === transport
                    ? "bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] text-white shadow-[0_10px_26px_-12px_rgba(99,102,241,0.9)]"
                    : "text-white/55 hover:bg-white/[0.06] hover:text-white",
                )}
              >
                {t}
              </button>
            ))}
          </div>
          <p className="mt-1 text-xs text-white/45">
            {transport === "api"
              ? "Pay-per-token API — pick a concrete model above (billed)."
              : "Local CLI subprocess — no per-token billing."}
          </p>
        </div>
      )}

      {/* Per-role provider/model/billing overrides — always visible (even on a
          cli-only generator a job can pin its extract/judge calls to api, and
          vice versa). "Auto" = backend default (provider/model) or inherit
          (transport) of the run above. */}
      <div className="mt-4 flex flex-col gap-3">
        <RoleAgentControls
          label="Extract"
          manifest={manifest}
          provider={extractProvider}
          model={extractModel}
          transport={extractTransport}
          onProvider={onExtractProviderChange}
          onModel={onExtractModelChange}
          onTransport={onExtractTransportChange}
        />
        <RoleAgentControls
          label="Judge"
          manifest={manifest}
          provider={judgeProvider}
          model={judgeModel}
          transport={judgeTransport}
          onProvider={onJudgeProviderChange}
          onModel={onJudgeModelChange}
          onTransport={onJudgeTransportChange}
          warning={judgeWarning}
        />
      </div>
      <p className="mt-1.5 text-xs text-white/45">
        Auto = backend default / follow job billing. Pin Extract or Judge to a provider, model, or
        CLI/API independently of the run above.
      </p>
    </section>
  );
}

interface ActionPanelProps {
  existingJobId: string | null;
  existingStatus: JobStatus | null;
  busy: "new" | "regen" | null;
  manifestLoading: boolean;
  blocked?: boolean;
  onGenerate: () => void;
  onRegenerate: () => void;
}

function ActionPanel({
  existingJobId,
  existingStatus,
  busy,
  manifestLoading,
  blocked = false,
  onGenerate,
  onRegenerate,
}: ActionPanelProps) {
  // Buttons are disabled while a generate is in flight OR while the agent
  // manifest is still loading — without the manifest we can't send a valid
  // provider/model pair — OR while `blocked` (e.g. API transport with no
  // concrete model picked yet).
  const disabled = busy !== null || manifestLoading || blocked;

  // No existing job — fresh generate
  if (!existingJobId) {
    return (
      <section className={CARD}>
        <h2 className="text-base font-semibold tracking-tight text-white">
          Generate homework
        </h2>
        <p className="mt-1.5 text-sm leading-relaxed text-white/55">
          Run the curriculum pipeline against this section. It will read the lesson
          and produce the assembled study packet.
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

  // Cancelled — NOT a failure. The user stopped it on purpose, so offer a clean
  // fresh "Generate" (not "Try again"). `cancelling` is the brief tear-down
  // window; treat it the same so the panel doesn't flash the failure state.
  if (existingStatus === "cancelled" || existingStatus === "cancelling") {
    return (
      <section className={CARD}>
        <span className="inline-flex items-center gap-2 font-mono text-[0.7rem] font-medium uppercase tracking-[0.16em] text-white/55">
          <Ban className="size-3.5" />
          Previous run cancelled
        </span>
        <p className="mt-2 text-sm leading-relaxed text-white/60">
          You stopped the last generation for this section. Start a fresh run whenever you're ready.
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
          <Link to={`/job/${existingJobId}`} className={GLASS_BTN}>
            <Eye className="size-4" />
            View cancelled run
          </Link>
        </div>
      </section>
    );
  }

  // failed
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
