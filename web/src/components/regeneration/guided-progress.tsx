import type { RegenerationWizardStep } from "@/lib/regeneration-draft";
import { cn } from "@/lib/utils";

const STEPS: Array<{ id: RegenerationWizardStep; label: string; number: number }> = [
  { id: "lessons", label: "Lessons", number: 1 },
  { id: "content", label: "Content", number: 2 },
  { id: "review", label: "Review", number: 3 },
  { id: "canary", label: "Canary", number: 4 },
];

export function GuidedProgress({
  active,
  highestReachable,
  onSelect,
}: {
  active: RegenerationWizardStep;
  highestReachable: RegenerationWizardStep;
  onSelect: (step: RegenerationWizardStep) => void;
}) {
  const highest = STEPS.findIndex((step) => step.id === highestReachable);
  return (
    <nav
      aria-label="Regeneration steps"
      className="grid grid-cols-4 gap-1 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-1"
    >
      {STEPS.map((step, index) => {
        const enabled = index <= highest;
        const current = step.id === active;
        return (
          <button
            key={step.id}
            type="button"
            disabled={!enabled}
            aria-current={current ? "step" : undefined}
            onClick={() => onSelect(step.id)}
            className={cn(
              "flex min-w-0 items-center justify-center gap-2 rounded-xl px-2 py-2 text-xs font-medium transition-colors",
              current ? "bg-[#7c5cff]/25 text-white" : "text-white/45 hover:bg-white/[0.05]",
              !enabled && "cursor-not-allowed opacity-35",
            )}
          >
            <span className="grid size-5 shrink-0 place-items-center rounded-full border border-current font-mono text-[0.6rem]">
              {step.number}
            </span>
            <span className="truncate">{step.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
