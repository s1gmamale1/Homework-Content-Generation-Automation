import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import type { MemoryCheckItem, MemoryCheckPack } from "@/lib/types";

function Item({ item, n }: { item: MemoryCheckItem; n: number }) {
  return (
    <ReviewCard>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-mono text-[0.66rem] text-(--color-ink-muted)">#{n}</span>
        <Badge variant="neutral" size="sm">{item.kind}</Badge>
        <Badge variant="accent" size="sm">{item.flashcard_id}</Badge>
      </div>
      <RichText className="text-sm font-medium text-(--color-ink)">{item.prompt}</RichText>

      {item.options && item.options.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {item.options.map((o, i) => (
            <li key={i} className="flex items-start gap-2 text-sm text-(--color-ink-soft)">
              <Badge variant={o.is_correct ? "success" : "neutral"} size="sm">
                {String.fromCharCode(65 + i)}
              </Badge>
              <span className="min-w-0">
                <RichText inline>{o.text}</RichText>
                {o.reason && <span className="text-(--color-ink-muted)"> — <RichText inline>{o.reason}</RichText></span>}
              </span>
            </li>
          ))}
        </ul>
      )}

      {item.blanks && item.blanks.length > 0 && (
        <AnswerKey>
          {item.blanks.map((b, i) => (
            <Labeled key={i} label={`Blank ${i + 1}`}>
              {[b.answer, ...(b.accepted_variations ?? [])].join(" · ")}
            </Labeled>
          ))}
        </AnswerKey>
      )}

      {item.why_prompt && <Labeled label="Why">{item.why_prompt}</Labeled>}
      {(item.correct_feedback || item.wrong_feedback || item.explanation) && (
        <AnswerKey>
          {item.correct_feedback && <Labeled label="Correct">{item.correct_feedback}</Labeled>}
          {item.wrong_feedback && <Labeled label="Wrong">{item.wrong_feedback}</Labeled>}
          {item.explanation && <Labeled label="Explanation">{item.explanation}</Labeled>}
        </AnswerKey>
      )}
    </ReviewCard>
  );
}

export function MemoryCheckView({ pack }: { pack: MemoryCheckPack }) {
  if (!pack.items?.length) return <p className="text-sm text-(--color-ink-muted)">No memory check.</p>;
  return (
    <div className="flex flex-col gap-3">
      {pack.pass_threshold != null && (
        <p className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          Pass ≥ {Math.round(pack.pass_threshold * 100)}%
        </p>
      )}
      {pack.items.map((it, i) => <Item key={i} item={it} n={i + 1} />)}
    </div>
  );
}
