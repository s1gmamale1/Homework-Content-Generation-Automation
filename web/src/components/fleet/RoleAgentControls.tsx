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
 *  is forced (billing needs an explicit model — mirrors the generator). */
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
}) {
  // Only providers the backend bills via API are pickable for an api role.
  // For provider selection we offer every manifest provider (the role may run
  // on cli too); the api-forces-model rule below handles the api case.
  const providerNames = manifest ? Object.keys(manifest.providers) : [];
  const modelOptions = provider ? (manifest?.providers?.[provider] ?? []) : [];

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

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="shrink-0 text-[0.66rem] uppercase tracking-wide text-white/45">
          {label}
        </span>
        {/* Provider */}
        <Select
          value={provider ?? AUTO}
          onValueChange={(v) => onProvider(v === AUTO ? null : v)}
        >
          <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[7.5rem]")}>
            <SelectValue placeholder="Auto" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={AUTO}>Auto</SelectItem>
            {providerNames.map((p) => (
              <SelectItem key={p} value={p}>
                {p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {/* Model — only shown once a concrete provider is chosen. With provider
            on "Auto" the model is backend-resolved, so a disabled model dropdown
            would just be dead UI; hide it entirely. */}
        {provider && (
          <Select
            value={model ?? AUTO}
            onValueChange={(v) => onModel(v === AUTO ? null : v)}
          >
            <SelectTrigger className={cn(SELECT_TRIGGER, "h-9 w-[10rem]")}>
              <SelectValue placeholder="Auto" />
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
            {ROLE_TRANSPORT_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {warning && (
        <p className="text-[0.7rem] leading-snug text-amber-300/90">{warning}</p>
      )}
    </div>
  );
}
