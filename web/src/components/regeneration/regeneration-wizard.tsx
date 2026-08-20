import {
  ESTIMATE_SAFETY_NOTE,
  type PhaseSelection,
  type PlanTarget,
  type RegenerationLanguage,
  type WizardState,
  cascadeDisclosure,
  estimateSummary,
  exclusionWarning,
  extractionNotice,
  includedPhases,
  launchGate,
  lessonCountLabel,
} from "@/lib/regeneration-state";
import { CARD, FRAME_OFF, FRAME_ON, PRESSABLE, PRIMARY_BTN } from "@/lib/ui";
import { cn, formatPhaseName } from "@/lib/utils";
/**
 * Campaign wizard (Task 4 shell). Fixture-driven: lessons, phase list and the
 * planner's downstream closures all arrive as props, and nothing here talks to
 * the API — creating or estimating a campaign spends nothing and publishes
 * nothing. Every operator-facing rule (cascade text, exclusion warning, launch
 * gate, estimate arithmetic) comes from `@/lib/regeneration-state` so the test
 * suite can see it.
 */
import { CircleDollarSign, Layers, ListChecks, Rocket, TriangleAlert } from "lucide-react";

/** Indicative per-call band; Task 10 replaces it with the planner's number. */
const COST_PER_CALL_LOW_USD = 0.02;
const COST_PER_CALL_HIGH_USD = 0.05;

const LANGUAGES: { id: RegenerationLanguage; label: string }[] = [
  { id: "uz", label: "Uzbek" },
  { id: "ru", label: "Russian" },
];

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

function Step({
  index,
  title,
  hint,
  children,
}: {
  index: number;
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn(CARD, "space-y-3")}>
      <header className="flex items-start gap-3">
        <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-white/[0.12] bg-white/[0.06] font-mono text-[0.7rem] text-white/70">
          {index}
        </span>
        <div>
          <h3 className="text-sm font-semibold text-white">{title}</h3>
          {hint && <p className="mt-1 max-w-[70ch] text-xs leading-5 text-white/45">{hint}</p>}
        </div>
      </header>
      {children}
    </section>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-xl px-3 py-1.5 text-xs font-medium",
        PRESSABLE,
        active ? FRAME_ON : FRAME_OFF,
      )}
    >
      {children}
    </button>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2">
      <div className="font-mono text-[0.62rem] uppercase tracking-[0.14em] text-white/40">
        {label}
      </div>
      <div className="mt-1 text-sm font-semibold text-white">{value}</div>
    </div>
  );
}

export function RegenerationWizard({
  lessons,
  allPhases,
  phaseClosures,
  state,
  onChange,
  onLaunch,
}: {
  lessons: PlanTarget[];
  allPhases: string[];
  /** Planner-supplied downstream closure per phase (inclusive of the phase). */
  phaseClosures: Record<string, string[]>;
  state: WizardState;
  onChange: (next: WizardState) => void;
  onLaunch: () => void;
}) {
  const patch = (over: Partial<WizardState>) => onChange({ ...state, ...over });

  const visibleLessons = lessons.filter((l) => l.language === state.language);
  const targets = visibleLessons.filter((l) => state.selectedLessonIds.includes(l.lessonId));

  const selection: PhaseSelection = {
    allPhases,
    selected: state.selectedPhases,
    autoIncluded: unique(state.selectedPhases.flatMap((p) => phaseClosures[p] ?? [p])),
    excluded: state.excludedPhases,
    extractionEnabled: state.extractionEnabled,
  };

  const cascade = cascadeDisclosure(selection);
  const included = includedPhases(selection);
  const addedByGraph = included.filter((p) => !state.selectedPhases.includes(p));
  const warning = exclusionWarning(selection);
  const notice = extractionNotice(selection);
  // targetCount is part of the gate: step 1 is lesson selection.
  const gate = launchGate(selection, state.acknowledgedInconsistency, targets.length);
  const canarySize = Math.max(1, Math.min(state.canarySize, Math.max(1, targets.length)));
  const estimate = estimateSummary({
    targets,
    phases: selection,
    canarySize,
    costPerCallLowUsd: COST_PER_CALL_LOW_USD,
    costPerCallHighUsd: COST_PER_CALL_HIGH_USD,
  });

  const toggle = (list: string[], value: string): string[] =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  return (
    <div className="space-y-4">
      <Step
        index={1}
        title="Pick completed lessons and a language"
        hint="Only lessons with a published version can be regenerated. A campaign covers one language."
      >
        <div className="flex flex-wrap gap-1">
          {LANGUAGES.map((l) => (
            <Chip
              key={l.id}
              active={state.language === l.id}
              onClick={() => patch({ language: l.id, selectedLessonIds: [] })}
            >
              {l.label}
            </Chip>
          ))}
        </div>
        <ul className="space-y-1">
          {visibleLessons.map((lesson) => (
            <li key={lesson.lessonId}>
              <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2 text-sm text-white/75 transition-colors hover:bg-white/[0.05]">
                <input
                  type="checkbox"
                  className="size-4 accent-[#7c5cff]"
                  checked={state.selectedLessonIds.includes(lesson.lessonId)}
                  onChange={() =>
                    patch({ selectedLessonIds: toggle(state.selectedLessonIds, lesson.lessonId) })
                  }
                />
                <span className="min-w-0 flex-1 truncate">{lesson.lessonTitle}</span>
                <span className="font-mono text-[0.65rem] text-white/40">
                  V{lesson.sourceVersion} → V{lesson.nextVersion}
                </span>
              </label>
            </li>
          ))}
        </ul>
      </Step>

      <Step
        index={2}
        title="Pick the phases to rebuild"
        hint="Ticking a phase also rebuilds everything downstream of it — step 3 shows the real expansion."
      >
        <div className="flex flex-wrap gap-1">
          {allPhases.map((phase) => (
            <Chip
              key={phase}
              active={state.selectedPhases.includes(phase)}
              onClick={() =>
                patch({
                  selectedPhases: toggle(state.selectedPhases, phase),
                  excludedPhases: [],
                  acknowledgedInconsistency: false,
                })
              }
            >
              {formatPhaseName(phase)}
            </Chip>
          ))}
        </div>
      </Step>

      <Step index={3} title="Review what the dependency graph pulls in">
        <div className="flex items-center gap-2 text-sm font-semibold text-white">
          <Layers className="size-4 text-white/50" />
          {cascade.headline}
        </div>
        <p
          className={cn(
            "max-w-[75ch] text-xs leading-5",
            cascade.scope === "near_full" ? "text-amber-200/85" : "text-white/50",
          )}
        >
          {cascade.detail}
        </p>
        <div className="flex flex-wrap gap-1">
          {included.map((phase) => (
            <span
              key={phase}
              className={cn(
                "rounded-lg border px-2 py-1 text-[0.7rem]",
                state.excludedPhases.includes(phase)
                  ? "border-white/[0.08] text-white/30 line-through"
                  : state.selectedPhases.includes(phase)
                    ? "border-white/20 bg-white/[0.1] text-white"
                    : "border-white/[0.1] bg-white/[0.03] text-white/60",
              )}
            >
              {formatPhaseName(phase)}
              {state.selectedPhases.includes(phase) ? "" : " · added"}
            </span>
          ))}
          {included.length === 0 && (
            <span className="text-xs text-white/40">Nothing selected yet.</span>
          )}
        </div>
      </Step>

      <Step
        index={4}
        title="Optionally drop an auto-included phase"
        hint="Dropping a downstream phase leaves its current text beside rebuilt upstream phases."
      >
        <div className="flex flex-wrap gap-1">
          {addedByGraph.map((phase) => (
            <Chip
              key={phase}
              active={state.excludedPhases.includes(phase)}
              onClick={() =>
                patch({
                  excludedPhases: toggle(state.excludedPhases, phase),
                  acknowledgedInconsistency: false,
                })
              }
            >
              {state.excludedPhases.includes(phase) ? "Excluded" : "Exclude"} ·{" "}
              {formatPhaseName(phase)}
            </Chip>
          ))}
          {addedByGraph.length === 0 && (
            <span className="text-xs text-white/40">
              No auto-included phases to drop for this selection.
            </span>
          )}
        </div>
        {warning && (
          <div className="space-y-2 rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3">
            <div className="flex items-start gap-2 text-xs leading-5 text-amber-100/90">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              <span className="max-w-[72ch]">{warning.message}</span>
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-amber-100/90">
              <input
                type="checkbox"
                className="size-4 accent-[#f5b544]"
                checked={state.acknowledgedInconsistency}
                onChange={() =>
                  patch({ acknowledgedInconsistency: !state.acknowledgedInconsistency })
                }
              />
              {warning.acknowledgementLabel}
            </label>
          </div>
        )}
      </Step>

      <Step
        index={5}
        title="Re-run the source extract"
        hint="Off by default. Turning it on puts every content phase downstream of the new extract."
      >
        <label className="flex cursor-pointer items-center gap-3 text-sm text-white/75">
          <input
            type="checkbox"
            className="size-4 accent-[#7c5cff]"
            checked={state.extractionEnabled}
            onChange={() =>
              patch({
                extractionEnabled: !state.extractionEnabled,
                excludedPhases: [],
                acknowledgedInconsistency: false,
              })
            }
          />
          Re-extract the lesson from the textbook PDF
        </label>
        {notice && (
          <p className="max-w-[75ch] rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3 text-xs leading-5 text-amber-100/90">
            {notice}
          </p>
        )}
      </Step>

      <Step index={6} title="Review the estimate">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="Lessons" value={String(estimate.targetCount)} />
          <Stat label="Rebuilt phases" value={String(estimate.regeneratedPhaseCount)} />
          <Stat label="Copied phases" value={String(estimate.copiedPhaseCount)} />
          <Stat label="Added by graph" value={String(estimate.autoIncludedPhaseCount)} />
          <Stat label="Excluded" value={String(estimate.excludedPhaseCount)} />
          <Stat label="Model calls" value={String(estimate.expectedModelCalls)} />
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm text-white/70">
          <span className="inline-flex items-center gap-2">
            <CircleDollarSign className="size-4 text-white/45" />
            {estimate.costRangeText}
          </span>
          <span className="inline-flex items-center gap-2">
            <ListChecks className="size-4 text-white/45" />
            {estimate.nextVersionText}
          </span>
        </div>
        <label className="flex items-center gap-3 text-xs text-white/55">
          Canary size
          <input
            type="number"
            min={1}
            max={Math.max(1, targets.length)}
            value={canarySize}
            onChange={(e) => patch({ canarySize: Number(e.target.value) || 1 })}
            className="w-20 rounded-lg border border-white/[0.1] bg-white/[0.05] px-2 py-1 text-sm text-white"
          />
          <span>
            {estimate.targetCount === 0
              ? "Pick lessons above to size the canary."
              : `${canarySize} of ${lessonCountLabel(estimate.targetCount)} run first and wait for your review.`}
          </span>
        </label>
        <p className="max-w-[75ch] text-xs leading-5 text-white/45">{ESTIMATE_SAFETY_NOTE}</p>
      </Step>

      <div className={cn(CARD, "flex flex-wrap items-center justify-between gap-3")}>
        <p
          className={cn(
            "max-w-[60ch] text-xs leading-5",
            gate.canLaunch ? "text-emerald-200/80" : "text-amber-200/85",
          )}
        >
          {gate.blockedReason ??
            `Ready: ${lessonCountLabel(estimate.targetCount)}, ` +
              `${estimate.regeneratedPhaseCount} rebuilt phases, ${estimate.costRangeText}.`}
        </p>
        <button type="button" className={PRIMARY_BTN} disabled={!gate.canLaunch} onClick={onLaunch}>
          <Rocket className="size-4" />
          Launch canary
        </button>
      </div>
    </div>
  );
}
