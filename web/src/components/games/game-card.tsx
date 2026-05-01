import { Gamepad2 } from "lucide-react";
import type { Game } from "@/lib/types";
import { AdaptiveQuiz } from "./adaptive-quiz";
import { MemoryMatch } from "./memory-match";
import { SentenceFill } from "./sentence-fill";
import { TileMatch } from "./tile-match";

const PRETTY: Record<string, string> = {
  adaptive_quiz: "Adaptive Quiz",
  tile_match: "Tile Match",
  memory_match: "Memory Match",
  sentence_fill: "Sentence Fill",
};

export function GameCard({ game }: { game: Game }) {
  return (
    <article className="rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-5">
      <header className="mb-4 flex items-start justify-between gap-3 border-b border-(--color-border) pb-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 grid size-7 place-items-center rounded-(--radius-sm) bg-(--color-canvas) text-(--color-accent)">
            <Gamepad2 className="size-3.5" />
          </span>
          <div>
            <h3 className="text-sm font-semibold tracking-tight text-(--color-ink)">
              {game.title}
            </h3>
            <p className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
              {PRETTY[game.type] ?? game.type}
            </p>
          </div>
        </div>
      </header>

      <Renderer game={game} />
    </article>
  );
}

function Renderer({ game }: { game: Game }) {
  switch (game.type) {
    case "adaptive_quiz":
      return <AdaptiveQuiz game={game} />;
    case "tile_match":
      return <TileMatch game={game} />;
    case "memory_match":
      return <MemoryMatch game={game} />;
    case "sentence_fill":
      return <SentenceFill game={game} />;
    default:
      return (
        <pre className="text-xs text-(--color-ink-muted)">
          Unknown game type: {game.type}
        </pre>
      );
  }
}
