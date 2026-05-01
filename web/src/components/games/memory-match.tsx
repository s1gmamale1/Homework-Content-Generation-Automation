import { useEffect, useMemo, useState } from "react";
import type { Game } from "@/lib/types";
import { cn } from "@/lib/utils";

export function MemoryMatch({ game }: { game: Game }) {
  const cards = useMemo(
    () =>
      [...(game.cards ?? [])]
        .map((c, i) => ({ idx: i, ...c }))
        .sort(() => Math.random() - 0.5),
    [game.cards],
  );

  const [flipped, setFlipped] = useState<number[]>([]);
  const [matched, setMatched] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (flipped.length !== 2) return;
    const [a, b] = flipped;
    if (cards[a]?.pair_id === cards[b]?.pair_id) {
      const next = new Set(matched);
      next.add(a);
      next.add(b);
      setMatched(next);
      setTimeout(() => setFlipped([]), 350);
    } else {
      setTimeout(() => setFlipped([]), 800);
    }
  }, [flipped, cards, matched]);

  function tap(i: number) {
    if (matched.has(i) || flipped.includes(i) || flipped.length === 2) return;
    setFlipped((f) => [...f, i]);
  }

  const allDone = matched.size === cards.length && cards.length > 0;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between text-xs text-(--color-ink-muted)">
        <span className="font-mono uppercase tracking-[0.14em]">
          Pairs · {matched.size / 2} / {cards.length / 2}
        </span>
        {allDone && (
          <span className="font-mono uppercase tracking-[0.14em] text-(--color-success)">
            Complete ✓
          </span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {cards.map((card, i) => {
          const isUp = matched.has(i) || flipped.includes(i);
          return (
            <button
              key={i}
              type="button"
              onClick={() => tap(i)}
              disabled={matched.has(i)}
              className={cn(
                "min-h-16 rounded-(--radius-md) border p-2 text-center text-xs leading-tight transition-all",
                "flex items-center justify-center",
                matched.has(i)
                  ? "border-[oklch(0.78_0.10_145_/_40%)] bg-[oklch(0.78_0.10_145_/_8%)] text-(--color-success)"
                  : isUp
                    ? "border-(--color-accent) bg-(--color-accent-soft) text-(--color-ink)"
                    : "border-(--color-border) bg-(--color-elevated) text-transparent hover:bg-(--color-elevated-hover) cursor-pointer",
              )}
            >
              <span aria-hidden={!isUp}>{isUp ? card.text : "?"}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
