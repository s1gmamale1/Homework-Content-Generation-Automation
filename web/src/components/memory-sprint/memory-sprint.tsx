import { Check, RefreshCcw, Timer, Trophy, X, Zap } from "lucide-react";
import { useMemo, useState } from "react";
import type { MemorySprintPack } from "@/lib/types";
import { cn } from "@/lib/utils";

interface MemorySprintProps {
  pack: MemorySprintPack;
}

export function MemorySprint({ pack }: MemorySprintProps) {
  const items = pack.items;
  const [pos, setPos] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);
  const [score, setScore] = useState({ correct: 0, total: 0 });
  const [done, setDone] = useState(false);

  const item = items[pos];

  // Format mix indicator
  const formats = useMemo(() => {
    const set = new Set<string>();
    items.forEach((it) => set.add(it.kind));
    return Array.from(set);
  }, [items]);

  function judge(idx: number) {
    if (!item || picked !== null) return;
    const correct = idx === item.correct_index;
    setPicked(idx);
    setScore((s) => ({
      correct: s.correct + (correct ? 1 : 0),
      total: s.total + 1,
    }));
  }

  function next() {
    if (pos === items.length - 1) {
      setDone(true);
      return;
    }
    setPos((p) => p + 1);
    setPicked(null);
  }

  function reset() {
    setPos(0);
    setPicked(null);
    setScore({ correct: 0, total: 0 });
    setDone(false);
  }

  if (items.length === 0) {
    return <p className="text-sm text-(--color-ink-muted)">No sprint items.</p>;
  }

  if (done) {
    const pct = Math.round((score.correct / score.total) * 100);
    return (
      <div className="flex flex-col gap-3 rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-5 text-center">
        <Trophy className="mx-auto size-6 text-(--color-accent)" />
        <p className="text-base font-semibold text-(--color-ink)">
          Sprint complete · {score.correct} / {score.total}
        </p>
        <p className="text-sm text-(--color-ink-soft)">{pct}% accuracy</p>
        <button
          type="button"
          onClick={reset}
          className="mx-auto inline-flex items-center gap-1.5 rounded-(--radius-sm) border border-(--color-border-hover) bg-(--color-elevated) px-3 py-1.5 text-xs font-medium text-(--color-ink) transition-colors hover:bg-(--color-elevated-hover)"
        >
          <RefreshCcw className="size-3" /> Run again
        </button>
      </div>
    );
  }

  if (!item) return null;
  const correctIdx = item.correct_index;
  const answered = picked !== null;
  const isCorrect = answered && picked === correctIdx;

  return (
    <div className="flex flex-col gap-4">
      {/* Top meta strip */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="inline-flex items-center gap-2 font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          <Zap className="size-3 text-(--color-accent)" />
          {pos + 1} / {items.length}
          <span className="rounded-full border border-(--color-border) bg-(--color-canvas) px-1.5 py-0.5 text-[0.6rem]">
            {item.kind.toUpperCase()}
          </span>
        </span>
        <span className="inline-flex items-center gap-1.5 font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          <Timer className="size-3" />
          {formats.length} formats · {score.correct}/{score.total}
        </span>
      </div>

      <p className="text-base font-medium leading-relaxed text-(--color-ink)">{item.prompt}</p>

      <div className="flex flex-col gap-2">
        {(item.options ?? []).map((opt, i) => {
          const isPicked = picked === i;
          const isAnswer = i === correctIdx;
          const reveal = answered && (isPicked || isAnswer);
          return (
            <button
              key={i}
              type="button"
              onClick={() => judge(i)}
              disabled={answered}
              className={cn(
                "rounded-(--radius-md) border px-3.5 py-2.5 text-left text-sm transition-colors",
                "disabled:cursor-default",
                !answered &&
                  "border-(--color-border) bg-(--color-elevated) hover:border-(--color-border-hover) hover:bg-(--color-elevated-hover) cursor-pointer",
                reveal &&
                  isAnswer &&
                  "border-[oklch(0.78_0.10_145_/_50%)] bg-[oklch(0.78_0.10_145_/_10%)] text-(--color-success)",
                reveal &&
                  isPicked &&
                  !isAnswer &&
                  "border-[oklch(0.70_0.16_25_/_50%)] bg-[oklch(0.70_0.16_25_/_10%)] text-(--color-error)",
                answered && !reveal && "opacity-50",
              )}
            >
              <span className="flex items-center gap-2.5">
                {reveal && isAnswer && <Check className="size-3.5" />}
                {reveal && isPicked && !isAnswer && <X className="size-3.5" />}
                <span>{opt}</span>
              </span>
            </button>
          );
        })}
      </div>

      {answered && item.explanation && (
        <p className="rounded-(--radius-md) border border-(--color-border) bg-(--color-canvas) px-3 py-2 text-xs leading-relaxed text-(--color-ink-soft)">
          <span className="font-mono uppercase tracking-[0.14em] text-(--color-ink-muted)">
            {isCorrect ? "Correct" : "Heads up"} ·{" "}
          </span>
          {item.explanation}
        </p>
      )}

      {answered && (
        <button
          type="button"
          onClick={next}
          className="self-start rounded-(--radius-sm) bg-(--color-accent) px-3 py-1.5 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep)"
        >
          {pos === items.length - 1 ? "Finish sprint" : "Next →"}
        </button>
      )}
    </div>
  );
}
