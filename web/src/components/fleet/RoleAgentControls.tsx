import { useEffect } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ProviderModelManifest, RoleTransport } from "@/lib/types";
import { SELECT_TRIGGER } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { serveability, providerServeableAnyMode, resolveRoleTransport } from "@/lib/serveability";
import { resolveTransport } from "@/lib/model-transport";

/** Sentinel <Select> value for "Auto" (null at the data boundary). Radix
 *  Select dislikes empty/null values, so we map null <-> "auto" here. */
const AUTO = "auto";

const ROLE_TRANSPORT_OPTIONS: { value: RoleTransport; label: string }[] = [
  { value: "inherit", label: "Auto" },
  { value: "cli", label: "CLI" },
  { value: "api", label: "API" },
];

/** Per-role agent controls (Extract / Judge): provider + model + transport.
 *  Provider/model default to "Auto" (null = backend default); transport
 *  defaults to "cli". When the role's own transport is api, a concrete model
 *  is forced (billing needs an explicit model — mirrors the generator).
 *
 *  `resolvedDefault` is the global launch-defaults row for this role. When the
 *  provider picker is on Auto (null), the trigger shows
 *  `Auto → <resolvedDefault.provider>` so operators always see what will run. */
export function RoleAgentControls({
  label,
  manifest,
  provider,
  model,
  transport,
  onProvider,
  onModel,
  onTransport,
  warning,
  jobTransport,
  resolvedDefault,
}: {
  label: string;
  manifest?: ProviderModelManifest;
  provider: string | null;
  model: string | null;
  transport: RoleTransport;
  onProvider: (v: string | null) => void;
  onModel: (v: string | null) => void;
  onTransport: (v: RoleTransport) => void;
  warning?: string | null;
  jobTransport: "cli" | "api";
  resolvedDefault?: { provider: string | null; model: string | null; transport: string | null };
}) {
  // Only providers the backend bills via API are pickable for an api role.
  // For provider selection we offer every manifest provider (the role may run
  // on cli too); the api-forces-model rule below handles the api case.
  const fleet = manifest?.fleet;
  const providerNames = manifest ? Object.keys(manifest.providers) : [];
  const modelOptions = provider ? (manifest?.providers?.[provider] ?? []) : [];
  // Provider-level api-only (a whole provider with no cli lane, e.g. clodex)
  // OR model-level api-only (gemini-3.x-flash — 404s/ModelNotFoundError on
  // the cli catalog even though gemini itself supports cli). Both force this
  // role's transport to api and hide cli/inherit below — same shared
  // resolver every other picker uses (model-transport.ts).
  const modelForcedApi = provider
    ? resolveTransport({
        provider,
        model,
        currentTransport: transport,
        parentTransport: jobTransport,
        apiOnlyModels: manifest?.api_only_models,
      }).forced
    : false;
  const apiOnly = provider ? (manifest?.api_only?.[provider] ?? false) || modelForcedApi : false;
  const isExtract = label.toLowerCase() === "extract";

  // Fleet reason for the currently-effective transport (only when a concrete provider is set).
  // resolveRoleTransport resolves "inherit" to the job transport.
  const effectiveTransport = resolveRoleTransport(transport, jobTransport);
  const fleetCheck = provider
    ? serveability(fleet, provider, effectiveTransport)
    : { ok: true, reason: null };
  const fleetReason = fleetCheck.reason;

  // Auto provider => model must be Auto (no provider to list models for).
  useEffect(() => {
    if (!provider && model !== null) onModel(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  // api on this role forces a concrete model (no "provider default" billing
  // path). Scoped to this role's own transport select; if provider is Auto we
  // leave model Auto and let the backend resolve it.
  useEffect(() => {
    if (transport === "api" && provider) {
      if (!model || !modelOptions.includes(model)) {
        onModel(modelOptions[0] ?? null);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transport, provider, manifest]);

  useEffect(() => {
    if (provider && apiOnly && transport !== "api") onTransport("api");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, apiOnly, transport]);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="shrink-0 text-[0.66rem] uppercase tracking-wide text-white/45">
          {label}
        </span>
        {/* Provider — when on Auto, show the resolved global default so the
            operator can see what will actually run ("Auto → gemini"). */}
        <Select
          value={provider ?? AUTO}
          onValueChange={(v) => onProvider(v === AUTO ? null : v)}
        >
          <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[7.5rem]")}>
            {provider == null ? (
              <SelectValue>
                {resolvedDefault?.provider
                  ? `Auto → ${resolvedDefault.provider}`
                  : "Auto → …"}
              </SelectValue>
            ) : (
              <SelectValue placeholder="Auto" />
            )}
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={AUTO}>Auto</SelectItem>
            {providerNames.map((p) => {
              const candidateApiOnly = manifest?.api_only?.[p] ?? false;
              const unsupportedRole = isExtract && candidateApiOnly;
              const serveable = candidateApiOnly || providerServeableAnyMode(fleet, p);
              return (
                <SelectItem key={p} value={p} disabled={!serveable || unsupportedRole}>
                  {unsupportedRole ? `${p} — no vision CLI` : serveable ? p : `${p} — no worker runs it`}
                </SelectItem>
              );
            })}
          </SelectContent>
        </Select>
        {/* Model — only shown once a concrete provider is chosen. With provider
            on "Auto" the model is backend-resolved, so a disabled model dropdown
            would just be dead UI; hide it entirely.
            When model is on Auto and a resolved default is available, show
            "Auto → <model>" so the operator sees the backend default. */}
        {provider && (
          <Select
            value={model ?? AUTO}
            onValueChange={(v) => onModel(v === AUTO ? null : v)}
          >
            <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[10rem]")}>
              {model == null ? (
                <SelectValue>
                  {resolvedDefault?.model
                    ? `Auto → ${resolvedDefault.model}`
                    : "Auto → …"}
                </SelectValue>
              ) : (
                <SelectValue placeholder="Auto" />
              )}
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={AUTO}>Auto</SelectItem>
              {modelOptions.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {/* Transport (Auto / CLI / API) */}
        <Select
          value={transport}
          onValueChange={(v) => onTransport(v as RoleTransport)}
        >
          <SelectTrigger
            className={cn(SELECT_TRIGGER, "h-9 w-[6rem]")}
            title={`${label} billing — Auto = follow job billing`}
          >
            <SelectValue placeholder="Auto" />
          </SelectTrigger>
          <SelectContent>
            {ROLE_TRANSPORT_OPTIONS.map((o) => {
              // Only gate when a concrete provider is selected (skip for Auto).
              let disabled = false;
              if (provider) {
                if (apiOnly) {
                  disabled = o.value !== "api" || !serveability(fleet, provider, "api").ok;
                } else if (o.value === "api") {
                  disabled = !serveability(fleet, provider, "api").ok;
                } else if (o.value === "inherit") {
                  // inherit resolves to the job transport; disable if that
                  // transport is not serveable for this provider.
                  disabled = !serveability(
                    fleet,
                    provider,
                    resolveRoleTransport("inherit", jobTransport),
                  ).ok;
                }
                // API-only providers keep CLI/inherit disabled.
              }
              return (
                <SelectItem key={o.value} value={o.value} disabled={disabled}>
                  {o.label}
                </SelectItem>
              );
            })}
          </SelectContent>
        </Select>
      </div>
      {warning && (
        <p className="text-[0.7rem] leading-snug text-amber-300/90">{warning}</p>
      )}
      {fleetReason && (
        <p className="text-[0.7rem] leading-snug text-amber-300/90">{fleetReason}</p>
      )}
    </div>
  );
}
