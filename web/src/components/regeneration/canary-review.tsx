import {
  type Campaign,
  type JudgeStatus,
  type PhaseProvenance,
  type RegenerationLanguage,
  approvalGate,
  costComparison,
  judgeSignal,
  provenanceLabel,
} from "@/lib/regeneration-state";
import { CARD, GLASS_BTN, PRIMARY_BTN } from "@/lib/ui";
import { cn, formatPhaseName } from "@/lib/utils";
/**
 * Canary packet review + the single campaign-level approval gate (Task 4 shell).
 *
 * The approve/reject wording is NOT written here: it comes from `approvalGate`,
 * which also decides whether a bulk step exists at all. A campaign whose canary
 * already covers every lesson must never see an empty bulk-release gate, and
 * that rule is unit-tested against the pure function rather than this markup.
 */
import {
  CircleCheck,
  CircleDollarSign,
  Clock3,
  Copy,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";

export interface CanaryPhase {
  provenance: PhaseProvenance;
  judge: JudgeStatus;
  warnings: string[];
  excerpt: string;
}

export interface CanaryPacket {
  lessonTitle: string;
  language: RegenerationLanguage;
  sourceVersion: number;
  nextVersion: number;
  latencySeconds: number;
  estimatedCostUsd: number;
  actualCostUsd: number;
  phases: CanaryPhase[];
}

export function CanaryReview({
  campaign,
  packet,
  onApprove,
  onReject,
}: {
  campaign: Campaign;
  packet: CanaryPacket;
  onApprove: () => void;
  onReject: () => void;
}) {
  const gate = approvalGate(campaign);
  const cost = costComparison(packet.estimatedCostUsd, packet.actualCostUsd);
  const warningSignals = packet.phases
    .map((p) => judgeSignal(p.judge))
    .filter((s) => s.severity === "warning");
  // One line per distinct verdict, but count the phases that actually carry it.
  const warningPhaseCount = warningSignals.length;
  const distinctWarnings = [...new Map(warningSignals.map((s) => [s.status, s])).values()];

  return (
    <div className="space-y-4">
      <section className={cn(CARD, "space-y-3")}>
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-white">Canary packet</h2>
            <p className="mt-1 text-sm text-white/60">{packet.lessonTitle}</p>
          </div>
          <span className="font-mono text-[0.68rem] text-white/45">
            {packet.language.toUpperCase()} · V{packet.sourceVersion} → V{packet.nextVersion}
          </span>
        </header>

        <div className="flex flex-wrap items-center gap-4 text-xs text-white/60">
          <span className="inline-flex items-center gap-2">
            <Clock3 className="size-4 text-white/40" />
            {packet.latencySeconds.toFixed(0)}s end to end
          </span>
          <span
            className={cn(
              "inline-flex items-center gap-2",
              cost.direction === "over" ? "text-amber-200/90" : "text-white/60",
            )}
          >
            <CircleDollarSign className="size-4 text-white/40" />
            {cost.text}
          </span>
        </div>

        {warningPhaseCount > 0 && (
          <div className="space-y-2 rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-amber-100">
              <TriangleAlert className="size-4" />
              {warningPhaseCount} judge {warningPhaseCount === 1 ? "warning" : "warnings"} — none of
              these block publication
            </div>
            <ul className="space-y-1 text-xs leading-5 text-amber-100/80">
              {distinctWarnings.map((w) => (
                <li key={w.status}>
                  <span className="font-medium">{w.label}.</span> {w.explanation}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className={cn(CARD, "space-y-2")}>
        <h3 className="text-sm font-semibold text-white">Complete revised homework</h3>
        <ul className="space-y-1">
          {packet.phases.map((phase) => {
            const signal = judgeSignal(phase.judge);
            const rebuilt = phase.provenance.origin === "regenerated";
            return (
              <li
                key={phase.provenance.phase}
                className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm text-white/85">
                    {formatPhaseName(phase.provenance.phase)}
                  </span>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-lg border px-2 py-0.5 text-[0.66rem]",
                      rebuilt
                        ? "border-[#7c5cff]/40 bg-[#7c5cff]/15 text-white"
                        : "border-white/[0.1] bg-white/[0.03] text-white/50",
                    )}
                  >
                    {rebuilt ? <RefreshCw className="size-3" /> : <Copy className="size-3" />}
                    {provenanceLabel(phase.provenance)}
                  </span>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-lg border px-2 py-0.5 text-[0.66rem]",
                      signal.severity === "warning"
                        ? "border-amber-300/30 bg-amber-300/[0.09] text-amber-100"
                        : "border-emerald-300/25 bg-emerald-300/[0.07] text-emerald-100/90",
                    )}
                    title={signal.explanation}
                  >
                    {signal.severity === "warning" ? (
                      <TriangleAlert className="size-3" />
                    ) : (
                      <CircleCheck className="size-3" />
                    )}
                    {signal.label}
                  </span>
                </div>
                <p className="mt-1 line-clamp-2 text-xs leading-5 text-white/45">{phase.excerpt}</p>
                {phase.warnings.length > 0 && (
                  <ul className="mt-1 space-y-0.5 font-mono text-[0.64rem] text-amber-100/70">
                    {phase.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <section className={cn(CARD, "space-y-3")}>
        <h3 className="text-sm font-semibold text-white">Campaign decision</h3>
        <p className="max-w-[75ch] text-xs leading-5 text-white/50">{gate.approveDetail}</p>
        <p className="max-w-[75ch] text-xs leading-5 text-white/50">{gate.rejectDetail}</p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={PRIMARY_BTN}
            disabled={!gate.canApprove}
            onClick={onApprove}
          >
            <CircleCheck className="size-4" />
            {gate.approveLabel}
          </button>
          <button type="button" className={GLASS_BTN} onClick={onReject}>
            {gate.rejectLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
