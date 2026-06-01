import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import type { BossArena, BossArenaQuestion } from "@/lib/types";

function Question({ q, n }: { q: BossArenaQuestion; n: number }) {
  return (
    <ReviewCard title={`Question ${n}`}>
      <div className="mb-2 flex flex-wrap gap-2">
        <Badge variant="neutral" size="sm">
          {q.difficulty}
        </Badge>
        {q.base_damage != null && (
          <Badge variant="error" size="sm">
            −{q.base_damage} HP
          </Badge>
        )}
        {q.bloom_level && (
          <Badge variant="neutral" size="sm">
            Bloom {q.bloom_level}
          </Badge>
        )}
        {q.pisa_level && (
          <Badge variant="neutral" size="sm">
            PISA {q.pisa_level}
          </Badge>
        )}
        {q.concept_ids?.map((id) => (
          <Badge key={id} variant="accent" size="sm">
            {id}
          </Badge>
        ))}
      </div>
      <RichText className="text-sm font-medium text-(--color-ink)">{q.scenario}</RichText>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <ReviewCard title="Why" className="bg-(--color-canvas)">
          <RichText className="text-sm text-(--color-ink-soft)">{q.why}</RichText>
        </ReviewCard>
        <ReviewCard title="How" className="bg-(--color-canvas)">
          <RichText className="text-sm text-(--color-ink-soft)">{q.how}</RichText>
        </ReviewCard>
        <ReviewCard title="What" className="bg-(--color-canvas)">
          <RichText className="text-sm text-(--color-ink-soft)">{q.what}</RichText>
        </ReviewCard>
      </div>
      {q.hints && q.hints.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-sm text-(--color-ink-muted)">
          {q.hints.map((h, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: stable render order
            <li key={i}>
              <RichText inline>{h}</RichText>
            </li>
          ))}
        </ul>
      )}
      <AnswerKey>
        <Labeled label="Correct">{q.correct_feedback}</Labeled>
        <Labeled label="Partial">{q.partial_feedback}</Labeled>
        <Labeled label="Wrong">{q.wrong_feedback}</Labeled>
      </AnswerKey>
    </ReviewCard>
  );
}

export function BossArenaView({ boss }: { boss: BossArena }) {
  if (!boss.questions?.length)
    return <p className="text-sm text-(--color-ink-muted)">No boss arena.</p>;
  return (
    <div className="flex flex-col gap-3">
      {boss.title && <p className="text-sm font-semibold text-(--color-ink)">{boss.title}</p>}
      {boss.questions.map((q, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: stable render order
        <Question key={i} q={q} n={i + 1} />
      ))}
    </div>
  );
}
