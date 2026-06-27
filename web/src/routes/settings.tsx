import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Settings } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { SpaceBackdrop } from "@/components/space-backdrop";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import type { LaunchDefaults, RoleTransport } from "@/lib/types";
import { CARD, PRIMARY_BTN, SELECT_TRIGGER } from "@/lib/ui";
import { cn } from "@/lib/utils";

const ROLE_TRANSPORT_OPTIONS: { value: RoleTransport; label: string }[] = [
  { value: "inherit", label: "Auto" },
  { value: "cli", label: "CLI" },
  { value: "api", label: "API" },
];

const TOC_TRANSPORT_OPTIONS: { value: "cli" | "api"; label: string }[] = [
  { value: "cli", label: "CLI" },
  { value: "api", label: "API" },
];

/** A labeled row containing provider + model + transport selects (for Judge/Extract). */
function RoleRow({
  label,
  provider,
  model,
  transport,
  onProvider,
  onModel,
  onTransport,
  providerNames,
  modelOptions,
}: {
  label: string;
  provider: string | null;
  model: string | null;
  transport: RoleTransport;
  onProvider: (v: string | null) => void;
  onModel: (v: string | null) => void;
  onTransport: (v: RoleTransport) => void;
  providerNames: string[];
  modelOptions: string[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="w-16 shrink-0 text-[0.7rem] font-medium uppercase tracking-wide text-white/50">
        {label}
      </span>
      {/* Provider — no Auto/null option; global defaults must be concrete */}
      <Select
        value={provider ?? ""}
        onValueChange={(v) => onProvider(v)}
      >
        <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[7.5rem]")}>
          <SelectValue placeholder="Select…" />
        </SelectTrigger>
        <SelectContent>
          {providerNames.map((p) => (
            <SelectItem key={p} value={p}>
              {p}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {/* Model — no Auto/null option; always shown (provider is always concrete) */}
      {provider ? (
        <Select
          value={model ?? ""}
          onValueChange={(v) => onModel(v)}
        >
          <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[13rem]")}>
            <SelectValue placeholder="Select…" />
          </SelectTrigger>
          <SelectContent>
            {modelOptions.map((m) => (
              <SelectItem key={m} value={m}>
                {m}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <span className="inline-flex h-9 w-[13rem] items-center px-3 text-[0.8rem] text-white/30">
          (select a provider first)
        </span>
      )}
      {/* Transport */}
      <Select
        value={transport}
        onValueChange={(v) => onTransport(v as RoleTransport)}
      >
        <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[6rem]")}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {ROLE_TRANSPORT_OPTIONS.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function SettingsPage() {
  const qc = useQueryClient();

  const defaultsQ = useQuery({
    queryKey: ["launch-defaults"],
    queryFn: () => api.getLaunchDefaults(),
  });

  const modelsQ = useQuery({
    queryKey: ["agent-models"],
    queryFn: () => api.getAgentModels(),
    staleTime: 60_000,
  });

  // Local form state — initialised from DB values when they load.
  const [judgeProvider, setJudgeProvider] = useState<string | null>(null);
  const [judgeModel, setJudgeModel] = useState<string | null>(null);
  const [judgeTransport, setJudgeTransport] = useState<RoleTransport>("inherit");
  const [extractProvider, setExtractProvider] = useState<string | null>(null);
  const [extractModel, setExtractModel] = useState<string | null>(null);
  const [extractTransport, setExtractTransport] = useState<RoleTransport>("inherit");
  const [tocTransport, setTocTransport] = useState<"cli" | "api">("cli");

  const [saveError, setSaveError] = useState<string | null>(null);

  // Sync form from loaded data (once on mount, not on every refetch so user
  // edits aren't overwritten while they're mid-form).
  const data = defaultsQ.data;
  useEffect(() => {
    if (!data) return;
    setJudgeProvider(data.judge_provider ?? null);
    setJudgeModel(data.judge_model ?? null);
    setJudgeTransport((data.judge_transport as RoleTransport) ?? "inherit");
    setExtractProvider(data.extract_provider ?? null);
    setExtractModel(data.extract_model ?? null);
    setExtractTransport((data.extract_transport as RoleTransport) ?? "inherit");
    setTocTransport((data.toc_transport as "cli" | "api") ?? "cli");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data !== undefined]);

  const manifest = modelsQ.data;
  const providerNames = manifest ? Object.keys(manifest.providers) : [];

  const judgeModelOptions = judgeProvider
    ? (manifest?.providers?.[judgeProvider] ?? [])
    : [];
  const extractModelOptions = extractProvider
    ? (manifest?.providers?.[extractProvider] ?? [])
    : [];

  const saveMut = useMutation({
    mutationFn: (patch: Partial<LaunchDefaults>) => api.updateLaunchDefaults(patch),
    onSuccess: () => {
      setSaveError(null);
      toast.success("Launch defaults saved");
      qc.invalidateQueries({ queryKey: ["launch-defaults"] });
      qc.invalidateQueries({ queryKey: ["agent-models"] });
    },
    onError: (e) => {
      const msg =
        e instanceof ApiError
          ? (() => {
              try {
                const parsed = JSON.parse(e.message) as { detail?: string };
                return parsed.detail ?? e.message;
              } catch {
                return e.message;
              }
            })()
          : e instanceof Error
          ? e.message
          : "Save failed";
      setSaveError(msg);
      toast.error(msg);
    },
  });

  function handleSave() {
    setSaveError(null);
    // Global defaults must be fully concrete — the backend enforces this too,
    // but catch it early for a cleaner UX.
    if (!judgeProvider || !judgeModel || !extractProvider || !extractModel) {
      setSaveError(
        "Judge and Extract provider+model must both be set — no Auto allowed for global defaults",
      );
      return;
    }
    saveMut.mutate({
      judge_provider: judgeProvider,
      judge_model: judgeModel,
      judge_transport: judgeTransport,
      extract_provider: extractProvider,
      extract_model: extractModel,
      extract_transport: extractTransport,
      toc_transport: tocTransport,
    });
  }

  const isLoading = defaultsQ.isLoading || modelsQ.isLoading;

  return (
    <div className="relative min-h-[calc(100vh-9rem)]">
      <SpaceBackdrop />

      <div className="relative z-10 space-y-7">
        {/* Header */}
        <header className="flex items-start gap-4">
          <span className="grid size-14 shrink-0 place-items-center rounded-2xl border border-white/[0.12] bg-gradient-to-br from-[#7c5cff]/40 to-[#4d9bff]/30 shadow-[0_18px_40px_-18px_rgba(124,92,255,0.8)]">
            <Settings className="size-7 text-white" />
          </span>
          <div>
            <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-[2.75rem]">
              Settings
            </h1>
            <p className="mt-2 max-w-[58ch] text-sm leading-6 text-white/55">
              Edit the global launch defaults — provider, model, and transport for
              the Judge, Extract, and TOC roles. Applied to every new batch and
              single-job launch unless overridden per-job.
            </p>
          </div>
        </header>

        {/* Error from the initial load */}
        {defaultsQ.error && (
          <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            Failed to load defaults:{" "}
            {defaultsQ.error instanceof Error
              ? defaultsQ.error.message
              : "Unknown error"}
          </div>
        )}

        {/* Form card */}
        <div className={cn(CARD, "space-y-6")}>
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-white">Launch defaults</h2>
            <span className="font-mono text-[0.7rem] uppercase tracking-[0.12em] text-white/40">
              judge · extract · toc
            </span>
          </div>

          {isLoading ? (
            <div className="space-y-3">
              {[0, 1, 2].map((i) => (
                <div
                  // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholder
                  key={i}
                  className="h-9 w-full animate-pulse rounded-xl bg-white/[0.06]"
                />
              ))}
            </div>
          ) : (
            <div className="space-y-5">
              {/* Column headers */}
              <div className="flex flex-wrap items-center gap-3">
                <span className="w-16 shrink-0" />
                <span className="w-[7.5rem] text-[0.65rem] font-medium uppercase tracking-wider text-white/35">
                  Provider
                </span>
                <span className="w-[13rem] text-[0.65rem] font-medium uppercase tracking-wider text-white/35">
                  Model
                </span>
                <span className="w-[6rem] text-[0.65rem] font-medium uppercase tracking-wider text-white/35">
                  Transport
                </span>
              </div>

              {/* Judge row */}
              <RoleRow
                label="Judge"
                provider={judgeProvider}
                model={judgeModel}
                transport={judgeTransport}
                onProvider={setJudgeProvider}
                onModel={setJudgeModel}
                onTransport={setJudgeTransport}
                providerNames={providerNames}
                modelOptions={judgeModelOptions}
              />

              {/* Extract row */}
              <RoleRow
                label="Extract"
                provider={extractProvider}
                model={extractModel}
                transport={extractTransport}
                onProvider={setExtractProvider}
                onModel={setExtractModel}
                onTransport={setExtractTransport}
                providerNames={providerNames}
                modelOptions={extractModelOptions}
              />

              {/* TOC row — transport only (cli|api, no inherit) */}
              <div className="flex flex-wrap items-center gap-3">
                <span className="w-16 shrink-0 text-[0.7rem] font-medium uppercase tracking-wide text-white/50">
                  TOC
                </span>
                <span className="inline-flex h-9 w-[7.5rem] items-center px-3 text-[0.8rem] text-white/30">
                  (via Extract)
                </span>
                <span className="inline-flex h-9 w-[13rem] items-center px-3 text-[0.8rem] text-white/30">
                  —
                </span>
                <Select
                  value={tocTransport}
                  onValueChange={(v) => setTocTransport(v as "cli" | "api")}
                >
                  <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[6rem]")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TOC_TRANSPORT_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* 422 / save error */}
              {saveError && (
                <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[0.8rem] text-rose-300">
                  {saveError}
                </p>
              )}

              {/* Save */}
              <div className="flex justify-end pt-1">
                <button
                  type="button"
                  className={PRIMARY_BTN}
                  disabled={saveMut.isPending}
                  onClick={handleSave}
                >
                  {saveMut.isPending ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
