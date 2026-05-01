import { useMemo, useState } from "react";
import type { Game } from "@/lib/types";
import { cn } from "@/lib/utils";

export function TileMatch({ game }: { game: Game }) {
  const pairs = game.pairs ?? [];
  // Shuffle the right column once on mount.
  const rights = useMemo(
    () => pairs.map((p, i) => ({ key: i, text: p.right })).sort(() => Math.random() - 0.5),
    [pairs],
  );

  const [pickedLeft, setPickedLeft] = useState<number | null>(null);
  const [pickedRight, setPickedRight] = useState<number | null>(null);
  const [solved, setSolved] = useState<Set<number>>(new Set());

  function checkPair(left: number, right: number) {
    if (left === right) {
      setSolved((s) => new Set(s).add(left));
    }
    setTimeout(() => {
      setPickedLeft(null);
      setPickedRight(null);
    }, 600);
  }

  function tapLeft(i: number) {
    if (solved.has(i)) return;
    setPickedLeft(i);
    if (pickedRight !== null) checkPair(i, pickedRight);
  }

  function tapRight(i: number) {
    if (solved.has(i)) return;
    setPickedRight(i);
    if (pickedLeft !== null) checkPair(pickedLeft, i);
  }

  const allSolved = solved.size === pairs.length && pairs.length > 0;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between text-xs text-(--color-ink-muted)">
        <span className="font-mono uppercase tracking-[0.14em]">
          Match · {solved.size} / {pairs.length}
        </span>
        {allSolved && (
          <span className="font-mono uppercase tracking-[0.14em] text-(--color-success)">
            Complete ✓
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-2">
          {pairs.map((p, i) => (
            <Tile
              key={`L${i}`}
              text={p.left}
              picked={pickedLeft === i}
              solved={solved.has(i)}
              onClick={() => tapLeft(i)}
            />
          ))}
        </div>

        <div className="flex flex-col gap-2">
          {rights.map(({ key, text }) => (
            <Tile
              key={`R${key}`}
              text={text}
              picked={pickedRight === key}
              solved={solved.has(key)}
              onClick={() => tapRight(key)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function Tile({
  text,
  picked,
  solved,
  onClick,
}: {
  text: string;
  picked: boolean;
  solved: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={solved}
      className={cn(
        "rounded-(--radius-md) border px-3 py-2 text-left text-sm transition-colors",
        solved &&
          "border-[oklch(0.78_0.10_145_/_40%)] bg-[oklch(0.78_0.10_145_/_8%)] text-(--color-success) cursor-default",
        !solved && picked &&
          "border-(--color-accent) bg-(--color-accent-soft) text-(--color-ink)",
        !solved && !picked &&
          "border-(--color-border) bg-(--color-elevated) text-(--color-ink) hover:border-(--color-border-hover) hover:bg-(--color-elevated-hover) cursor-pointer",
      )}
    >
      {text}
    </button>
  );
}
