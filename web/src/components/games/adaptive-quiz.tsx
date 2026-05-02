import { Check, RefreshCcw, X } from "lucide-react";
import { useState } from "react";
import { RichText } from "@/components/rich-text";
import type { Game } from "@/lib/types";
import { cn } from "@/lib/utils";

export function AdaptiveQuiz({ game }: { game: Game }) {
  const questions = game.questions ?? [];
  const [idx, setIdx] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);
  const [scored, setScored] = useState<{ correct: number; total: number }>({
    correct: 0,
    total: 0,
  });

  const q = questions[idx];
  if (!q) {
    return <p className="text-sm text-(--color-ink-muted)">No questions.</p>;
  }

  const correctIdx = typeof q.correct_index === "number" ? q.correct_index : null;
  const answered = picked !== null;
  const isCorrect = answered && picked === correctIdx;

  function pick(i: number) {
    if (answered) return;
    setPicked(i);
    setScored((s) => ({
      correct: s.correct + (i === correctIdx ? 1 : 0),
      total: s.total + 1,
    }));
  }

  function next() {
    setPicked(null);
    setIdx((i) => (i + 1) % questions.length);
  }

  function reset() {
    setPicked(null);
    setIdx(0);
    setScored({ correct: 0, total: 0 });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between text-xs text-(--color-ink-muted)">
        <span className="font-mono uppercase tracking-[0.14em]">
          Q {idx + 1} / {questions.length}
        </span>
        <span className="font-mono">
          score · {scored.correct} / {scored.total}
        </span>
      </div>

      <RichText className="text-base font-medium leading-relaxed text-(--color-ink)">
        {q.prompt}
      </RichText>

      <div className="flex flex-col gap-2">
        {(q.options ?? []).map((opt, i) => {
          const isPicked = picked === i;
          const isAnswer = correctIdx !== null && i === correctIdx;
          const reveal = answered && (isPicked || isAnswer);
          return (
            <button
              key={`${idx}-${i}`}
              type="button"
              onClick={() => pick(i)}
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
                {reveal && isAnswer && <Check className="size-3.5 shrink-0" />}
                {reveal && isPicked && !isAnswer && <X className="size-3.5 shrink-0" />}
                <RichText inline>{opt}</RichText>
              </span>
            </button>
          );
        })}
      </div>

      {answered && q.explanation && (
        <div className="rounded-(--radius-md) border border-(--color-border) bg-(--color-canvas) px-3.5 py-2.5 text-sm leading-relaxed text-(--color-ink-soft)">
          <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
            {isCorrect ? "Correct" : "Not quite"} ·{" "}
          </span>
          <RichText inline>{q.explanation}</RichText>
        </div>
      )}

      <div className="flex gap-2">
        {answered && idx < questions.length - 1 && (
          <button
            type="button"
            onClick={next}
            className="rounded-(--radius-sm) bg-(--color-accent) px-3 py-1.5 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep)"
          >
            Next →
          </button>
        )}
        {answered && idx === questions.length - 1 && (
          <button
            type="button"
            onClick={reset}
            className="inline-flex items-center gap-1.5 rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-3 py-1.5 text-xs font-medium text-(--color-ink) transition-colors hover:bg-(--color-elevated-hover)"
          >
            <RefreshCcw className="size-3" /> Restart
          </button>
        )}
      </div>
    </div>
  );
}
