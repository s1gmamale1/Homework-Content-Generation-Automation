import { ChevronLeft, ChevronRight, Lightbulb, RefreshCcw, Shuffle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { RichText } from "@/components/rich-text";
import type { Flashcard } from "@/lib/types";
import { cn } from "@/lib/utils";

interface FlashcardDeckProps {
  cards: Flashcard[];
}

export function FlashcardDeck({ cards }: FlashcardDeckProps) {
  // Order with optional shuffle
  const [order, setOrder] = useState<number[]>(() => cards.map((_, i) => i));
  const [pos, setPos] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [showHint, setShowHint] = useState(false);

  useEffect(() => {
    setOrder(cards.map((_, i) => i));
    setPos(0);
    setFlipped(false);
    setShowHint(false);
  }, [cards]);

  const card = cards[order[pos] ?? 0];

  function go(delta: number) {
    setFlipped(false);
    setShowHint(false);
    setPos((p) => Math.max(0, Math.min(cards.length - 1, p + delta)));
  }

  function shuffle() {
    setOrder((o) => [...o].sort(() => Math.random() - 0.5));
    setPos(0);
    setFlipped(false);
    setShowHint(false);
  }

  function reset() {
    setOrder(cards.map((_, i) => i));
    setPos(0);
    setFlipped(false);
    setShowHint(false);
  }

  // Group cards by cluster for the index strip
  const grouped = useMemo(() => {
    const m = new Map<string, number[]>();
    cards.forEach((c, i) => {
      const key = c.cluster ?? "—";
      if (!m.has(key)) m.set(key, []);
      m.get(key)!.push(i);
    });
    return m;
  }, [cards]);

  if (cards.length === 0) {
    return <p className="text-sm text-(--color-ink-muted)">No flashcards.</p>;
  }

  if (!card) return null;

  return (
    <div className="flex flex-col gap-4">
      {/* Top meta row */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-mono uppercase tracking-[0.14em] text-(--color-ink-muted)">
            {pos + 1} / {cards.length}
          </span>
          {card.cluster && (
            <span className="rounded-full border border-(--color-border) bg-(--color-elevated) px-2 py-0.5 font-mono text-[0.6rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
              {card.cluster}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={shuffle}
            className="inline-flex items-center gap-1 rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-2 py-1 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-(--color-ink-muted) transition-colors hover:bg-(--color-elevated-hover) hover:text-(--color-ink)"
          >
            <Shuffle className="size-3" /> Shuffle
          </button>
          <button
            type="button"
            onClick={reset}
            className="inline-flex items-center gap-1 rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-2 py-1 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-(--color-ink-muted) transition-colors hover:bg-(--color-elevated-hover) hover:text-(--color-ink)"
          >
            <RefreshCcw className="size-3" /> Reset
          </button>
        </div>
      </div>

      {/* The card itself — flippable */}
      <div className="relative" style={{ perspective: 1600 }}>
        <button
          type="button"
          onClick={() => setFlipped((v) => !v)}
          className="relative block w-full"
          aria-label={flipped ? "Show prompt side" : "Reveal answer"}
        >
          <div
            className="relative h-72 w-full transition-transform duration-500"
            style={{
              transformStyle: "preserve-3d",
              transform: flipped ? "rotateY(180deg)" : "rotateY(0deg)",
            }}
          >
            {/* Front */}
            <Side kind="front" text={card.front} hint={showHint ? (card.hint ?? null) : null} />
            {/* Back */}
            <Side kind="back" text={card.back} />
          </div>
        </button>

        {/* Hint toggle (front only) */}
        {!flipped && card.hint && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setShowHint((v) => !v);
            }}
            className="absolute right-3 top-3 inline-flex items-center gap-1 rounded-(--radius-sm) border border-(--color-border-hover) bg-(--color-elevated) px-2 py-1 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-(--color-ink-muted) backdrop-blur transition-colors hover:bg-(--color-accent-soft) hover:text-(--color-accent)"
          >
            <Lightbulb className="size-3" />
            {showHint ? "Hide hint" : "Hint"}
          </button>
        )}
      </div>

      {/* Bottom controls */}
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => go(-1)}
          disabled={pos === 0}
          className="inline-flex items-center gap-1.5 rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-3 py-1.5 text-xs font-medium text-(--color-ink) transition-colors hover:bg-(--color-elevated-hover) disabled:opacity-40"
        >
          <ChevronLeft className="size-3.5" /> Prev
        </button>

        <button
          type="button"
          onClick={() => setFlipped((v) => !v)}
          className="rounded-(--radius-sm) bg-(--color-accent) px-3 py-1.5 text-xs font-medium text-[oklch(0.18_0.04_55)] transition-colors hover:bg-(--color-accent-deep)"
        >
          {flipped ? "Show front" : "Reveal answer"}
        </button>

        <button
          type="button"
          onClick={() => go(1)}
          disabled={pos === cards.length - 1}
          className="inline-flex items-center gap-1.5 rounded-(--radius-sm) border border-(--color-border) bg-(--color-elevated) px-3 py-1.5 text-xs font-medium text-(--color-ink) transition-colors hover:bg-(--color-elevated-hover) disabled:opacity-40"
        >
          Next <ChevronRight className="size-3.5" />
        </button>
      </div>

      {/* Pagination strip — one horizontal row of card numbers in deck
          order. Cluster boundaries get a subtle extra gap so grouping is
          still visible without dedicating a row per cluster. The active
          card's cluster name is shown above for context. */}
      {cards.length > 1 && (
        <div className="mt-1 flex flex-col gap-1.5">
          {grouped.size > 1 && card.cluster && (
            <div className="flex items-center justify-between gap-2 font-mono text-[0.6rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
              <span>
                Cluster · <span className="text-(--color-ink-soft)">{card.cluster}</span>
              </span>
              <span>
                {pos + 1} / {cards.length}
              </span>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1">
            {cards.map((c, idx) => {
              const isActive = order[pos] === idx;
              const prevCluster = idx > 0 ? (cards[idx - 1].cluster ?? null) : null;
              const isClusterBoundary = idx > 0 && grouped.size > 1 && c.cluster !== prevCluster;
              return (
                <button
                  key={idx}
                  type="button"
                  title={c.cluster ?? undefined}
                  onClick={() => {
                    const newPos = order.indexOf(idx);
                    if (newPos >= 0) {
                      setPos(newPos);
                      setFlipped(false);
                      setShowHint(false);
                    }
                  }}
                  className={cn(
                    "size-6 shrink-0 rounded-(--radius-sm) border text-[0.62rem] font-mono leading-none tabular-nums transition-colors",
                    isActive
                      ? "border-(--color-accent) bg-(--color-accent) text-[oklch(0.18_0.04_55)]"
                      : "border-(--color-border) bg-(--color-elevated) text-(--color-ink-muted) hover:border-(--color-border-hover) hover:text-(--color-ink)",
                    isClusterBoundary && "ml-2",
                  )}
                >
                  {idx + 1}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function Side({
  kind,
  text,
  hint,
}: {
  kind: "front" | "back";
  text: string;
  hint?: string | null;
}) {
  const isBack = kind === "back";
  return (
    <div
      className={cn(
        "absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-(--radius-lg) border p-6 text-center",
        "shadow-[0_2px_0_oklch(0_0_0_/_30%),inset_0_1px_0_oklch(0.99_0.005_80_/_4%)]",
        isBack
          ? "border-(--color-accent-border) bg-[linear-gradient(135deg,var(--color-accent-soft),var(--color-elevated))]"
          : "border-(--color-border) bg-(--color-elevated)",
      )}
      style={{
        backfaceVisibility: "hidden",
        WebkitBackfaceVisibility: "hidden",
        transform: isBack ? "rotateY(180deg)" : "rotateY(0deg)",
      }}
    >
      <span
        className={cn(
          "font-mono text-[0.62rem] uppercase tracking-[0.18em]",
          isBack ? "text-(--color-accent)" : "text-(--color-ink-muted)",
        )}
      >
        {isBack ? "Answer" : "Prompt"}
      </span>
      <RichText
        className={cn(
          "max-w-[36ch] font-display text-[clamp(1.1rem,2.6vw,1.5rem)] leading-snug text-(--color-ink)",
          isBack && "italic",
        )}
      >
        {text}
      </RichText>
      {hint && (
        <div className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-(--color-accent-border) bg-(--color-accent-soft) px-3 py-1 text-[0.7rem] text-(--color-accent)">
          <Lightbulb className="size-3 shrink-0" /> <RichText inline>{hint}</RichText>
        </div>
      )}
    </div>
  );
}
