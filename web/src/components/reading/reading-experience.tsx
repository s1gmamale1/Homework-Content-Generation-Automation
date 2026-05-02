import { BookOpen, Check, X } from "lucide-react";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { RichText } from "@/components/rich-text";
import type { ReadingCheckpoint, ReadingPassage } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ReadingExperienceProps {
  passage: ReadingPassage;
}

export function ReadingExperience({ passage }: ReadingExperienceProps) {
  // Split the passage into paragraphs on blank-line boundaries
  const paragraphs = useMemo(
    () =>
      passage.passage_md
        .split(/\n{2,}/)
        .map((p) => p.trim())
        .filter(Boolean),
    [passage.passage_md],
  );

  // Group checkpoints by the paragraph index they live AFTER
  const byPara = useMemo(() => {
    const m = new Map<number, ReadingCheckpoint[]>();
    for (const cp of passage.checkpoints) {
      const arr = m.get(cp.after_paragraph) ?? [];
      arr.push(cp);
      m.set(cp.after_paragraph, arr);
    }
    return m;
  }, [passage.checkpoints]);

  if (paragraphs.length === 0) {
    return <p className="text-sm text-(--color-ink-muted)">No reading passage.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2 font-mono text-[0.66rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
        <BookOpen className="size-3.5" />
        Reading
        {passage.cefr_level && (
          <span className="rounded-full border border-(--color-accent-border) bg-(--color-accent-soft) px-2 py-0.5 text-(--color-accent)">
            CEFR {passage.cefr_level}
          </span>
        )}
        <span>·</span>
        <span>{paragraphs.length} paragraphs</span>
      </div>

      <article className="flex flex-col gap-4 leading-relaxed text-(--color-ink-soft)">
        {paragraphs.map((para, idx) => (
          <div key={idx} className="flex flex-col gap-3">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
              {para}
            </ReactMarkdown>

            {(byPara.get(idx) ?? []).map((cp, ci) => (
              <CheckpointBlock key={`${idx}-${ci}`} checkpoint={cp} />
            ))}
          </div>
        ))}
      </article>
    </div>
  );
}

function CheckpointBlock({ checkpoint }: { checkpoint: ReadingCheckpoint }) {
  const [picked, setPicked] = useState<number | null>(null);
  const [openValue, setOpenValue] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const isOpen = !checkpoint.options || checkpoint.options.length === 0;
  const correctIdx = checkpoint.correct_index ?? null;
  const expected = (checkpoint.correct_answer ?? "").trim().toLowerCase();
  const actualOpen = openValue.trim().toLowerCase();

  const isCorrect =
    submitted && (isOpen ? Boolean(expected) && actualOpen === expected : picked === correctIdx);

  function pickMc(i: number) {
    if (submitted) return;
    setPicked(i);
    setSubmitted(true);
  }

  function submitOpen() {
    if (submitted || !openValue.trim()) return;
    setSubmitted(true);
  }

  return (
    <div className="my-2 rounded-(--radius-md) border border-(--color-accent-border) bg-(--color-accent-soft) p-3">
      <p className="mb-3 inline-flex items-center gap-1.5 font-mono text-[0.66rem] font-medium uppercase tracking-[0.16em] text-(--color-accent)">
        Checkpoint
      </p>
      <RichText className="mb-3 text-sm font-medium text-(--color-ink)">
        {checkpoint.prompt}
      </RichText>

      {isOpen ? (
        <div className="flex flex-col gap-2">
          <input
            value={openValue}
            onChange={(e) => setOpenValue(e.target.value)}
            disabled={submitted}
            onKeyDown={(e) => e.key === "Enter" && submitOpen()}
            placeholder="Type your answer…"
            className="rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-3 py-1.5 text-sm text-(--color-ink) outline-none transition-colors hover:border-(--color-border-hover) focus:border-(--color-accent) disabled:opacity-60"
          />
          {!submitted && (
            <button
              type="button"
              onClick={submitOpen}
              disabled={!openValue.trim()}
              className="self-start rounded-(--radius-sm) bg-(--color-accent) px-3 py-1 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep) disabled:opacity-50"
            >
              Check
            </button>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {(checkpoint.options ?? []).map((opt, i) => {
            const isPicked = picked === i;
            const isAnswer = correctIdx !== null && i === correctIdx;
            const reveal = submitted && (isPicked || isAnswer);
            return (
              <button
                key={i}
                type="button"
                onClick={() => pickMc(i)}
                disabled={submitted}
                className={cn(
                  "rounded-(--radius-sm) border px-3 py-1.5 text-left text-sm transition-colors",
                  !submitted &&
                    "border-(--color-border) bg-(--color-elevated) hover:bg-(--color-elevated-hover) cursor-pointer",
                  reveal &&
                    isAnswer &&
                    "border-[oklch(0.78_0.10_145_/_50%)] bg-[oklch(0.78_0.10_145_/_10%)] text-(--color-success)",
                  reveal &&
                    isPicked &&
                    !isAnswer &&
                    "border-[oklch(0.70_0.16_25_/_50%)] bg-[oklch(0.70_0.16_25_/_10%)] text-(--color-error)",
                  submitted && !reveal && "opacity-50",
                )}
              >
                <span className="inline-flex items-center gap-1.5">
                  {reveal && isAnswer && <Check className="size-3 shrink-0" />}
                  {reveal && isPicked && !isAnswer && <X className="size-3 shrink-0" />}
                  <RichText inline>{opt}</RichText>
                </span>
              </button>
            );
          })}
        </div>
      )}

      {submitted && (checkpoint.explanation || isOpen) && (
        <div
          className={cn(
            "mt-2 rounded-sm px-2 py-1 text-xs leading-relaxed",
            isCorrect ? "text-(--color-success)" : "text-(--color-error)",
          )}
        >
          <span className="font-mono uppercase tracking-[0.14em]">
            {isCorrect
              ? "Correct"
              : isOpen
                ? `Expected: ${checkpoint.correct_answer ?? "—"}`
                : "Not quite"}
          </span>
          {checkpoint.explanation && (
            <>
              {" · "}
              <RichText inline>{checkpoint.explanation}</RichText>
            </>
          )}
        </div>
      )}
    </div>
  );
}
