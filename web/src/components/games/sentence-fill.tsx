import { Check, X } from "lucide-react";
import { useState } from "react";
import type { Game } from "@/lib/types";
import { cn } from "@/lib/utils";

export function SentenceFill({ game }: { game: Game }) {
  const items = game.questions ?? [];
  const [idx, setIdx] = useState(0);
  const [value, setValue] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [score, setScore] = useState({ correct: 0, total: 0 });

  const item = items[idx];
  if (!item) {
    return <p className="text-sm text-(--color-ink-muted)">No items.</p>;
  }

  const expected = (item.answer ?? "").trim().toLowerCase();
  const actual = value.trim().toLowerCase();
  const isCorrect = submitted && expected && actual === expected;

  function submit() {
    if (submitted || !value.trim()) return;
    setSubmitted(true);
    setScore((s) => ({
      correct: s.correct + (actual === expected ? 1 : 0),
      total: s.total + 1,
    }));
  }

  function next() {
    setSubmitted(false);
    setValue("");
    setIdx((i) => (i + 1) % items.length);
  }

  // Replace _ / ___ / ____ with a styled blank.
  const sentenceParts = item.prompt.split(/_+/g);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between text-xs text-(--color-ink-muted)">
        <span className="font-mono uppercase tracking-[0.14em]">
          {idx + 1} / {items.length}
        </span>
        <span className="font-mono">
          score · {score.correct} / {score.total}
        </span>
      </div>

      <p className="text-base leading-relaxed text-(--color-ink)">
        {sentenceParts.map((part, i) => (
          <span key={i}>
            {part}
            {i < sentenceParts.length - 1 && (
              <span className="mx-1 inline-block min-w-16 border-b border-dashed border-(--color-border-hover) text-(--color-accent)">
                {submitted ? item.answer : value || "____"}
              </span>
            )}
          </span>
        ))}
      </p>

      {!submitted && (
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Type the missing word…"
          className="rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-3.5 py-2.5 text-sm text-(--color-ink) outline-none transition-colors hover:border-(--color-border-hover) focus:border-(--color-accent) focus:ring-2 focus:ring-(--color-accent)/30"
          autoFocus
        />
      )}

      {submitted && (
        <div
          className={cn(
            "rounded-(--radius-md) border px-3.5 py-2.5 text-sm",
            isCorrect
              ? "border-[oklch(0.78_0.10_145_/_30%)] bg-[oklch(0.78_0.10_145_/_8%)] text-(--color-success)"
              : "border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_8%)] text-(--color-error)",
          )}
        >
          <span className="inline-flex items-center gap-1.5 font-medium">
            {isCorrect ? <Check className="size-3.5" /> : <X className="size-3.5" />}
            {isCorrect ? "Correct" : `Answer: ${item.answer}`}
          </span>
          {item.explanation && (
            <p className="mt-1.5 text-(--color-ink-soft)">{item.explanation}</p>
          )}
        </div>
      )}

      <div className="flex gap-2">
        {!submitted && (
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim()}
            className="rounded-(--radius-sm) bg-(--color-accent) px-3 py-1.5 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep) disabled:opacity-50"
          >
            Check
          </button>
        )}
        {submitted && idx < items.length - 1 && (
          <button
            type="button"
            onClick={next}
            className="rounded-(--radius-sm) bg-(--color-accent) px-3 py-1.5 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep)"
          >
            Next →
          </button>
        )}
      </div>
    </div>
  );
}
