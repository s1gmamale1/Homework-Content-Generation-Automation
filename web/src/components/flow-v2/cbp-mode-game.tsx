import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import type {
  CbpModeGame, InteractionPayload, JigsawPayload, MemoryMatchPayload,
  SentenceFillPayload, TicTacToePayload,
} from "@/lib/types";

function Payload({ mode, payload }: { mode: CbpModeGame["interaction_mode"]; payload: InteractionPayload }) {
  switch (mode) {
    case "memory_match": {
      const p = payload as MemoryMatchPayload;
      return (
        <ReviewCard title="Pairs">
          <ul className="flex flex-col gap-1 text-sm text-(--color-ink-soft)">
            {p.pairs.map((pr, i) => <li key={i}><RichText inline>{pr.left}</RichText> ↔ <RichText inline>{pr.right}</RichText></li>)}
          </ul>
        </ReviewCard>
      );
    }
    case "jigsaw": {
      const p = payload as JigsawPayload;
      return (
        <ReviewCard title="Pieces">
          <ul className="flex flex-col gap-1 text-sm text-(--color-ink-soft)">
            {p.pieces.map((pc) => <li key={pc.id}><Badge variant="neutral" size="sm">{pc.id}</Badge> <RichText inline>{pc.content}</RichText></li>)}
          </ul>
          <Labeled label="Assembly types">{p.allowed_assembly_types.join(" · ")}</Labeled>
        </ReviewCard>
      );
    }
    case "sentence_fill": {
      const p = payload as SentenceFillPayload;
      return (
        <ReviewCard title="Sentence">
          <RichText className="text-sm text-(--color-ink)">{p.sentence}</RichText>
          <ul className="mt-2 flex flex-col gap-1">
            {p.chips.map((c, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-(--color-ink-soft)">
                <Badge variant={c.is_correct ? "success" : "neutral"} size="sm">{String.fromCharCode(65 + i)}</Badge>
                <span><RichText inline>{c.label}</RichText>{c.reason && <span className="text-(--color-ink-muted)"> — <RichText inline>{c.reason}</RichText></span>}</span>
              </li>
            ))}
          </ul>
        </ReviewCard>
      );
    }
    case "tictactoe": {
      const p = payload as TicTacToePayload;
      return (
        <ReviewCard title="Grid (3×3)">
          <div className="grid grid-cols-3 gap-1">
            {p.cells.map((c, i) => (
              <div key={i} className={`rounded-(--radius-sm) border p-2 text-center text-sm ${c.is_correct ? "border-(--color-success) text-(--color-success)" : "border-(--color-border) text-(--color-ink-soft)"}`}>
                <RichText inline>{c.label}</RichText>
              </div>
            ))}
          </div>
          {p.cells.some((c) => c.reason) && (
            <AnswerKey>
              {p.cells.filter((c) => c.reason).map((c, i) => <Labeled key={i} label={c.label}>{c.reason ?? ""}</Labeled>)}
            </AnswerKey>
          )}
        </ReviewCard>
      );
    }
    default: {
      const _exhaustive: never = mode;
      return _exhaustive;
    }
  }
}

export function CbpModeGameView({ game }: { game: CbpModeGame }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-(--color-ink)">{game.title}</span>
        <Badge variant="neutral" size="sm">{game.interaction_mode}</Badge>
        {game.source_concept_ids?.map((id) => <Badge key={id} variant="accent" size="sm">{id}</Badge>)}
      </div>
      <RichText className="text-sm leading-relaxed text-(--color-ink-soft)">{game.instruction}</RichText>
      <Payload mode={game.interaction_mode} payload={game.interaction_payload} />
      <ReviewCard title="Explain your reasoning">
        <RichText className="text-sm text-(--color-ink)">{game.why_prompt}</RichText>
        {game.expected_reasoning_keywords && game.expected_reasoning_keywords.length > 0 && (
          <AnswerKey><Labeled label="Keywords">{game.expected_reasoning_keywords.join(" · ")}</Labeled></AnswerKey>
        )}
      </ReviewCard>
    </div>
  );
}
