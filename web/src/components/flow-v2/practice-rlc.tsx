import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import type { RealLifeChallenge, RlcDecision } from "@/lib/types";

function Decision({ d, n }: { d: RlcDecision; n: number }) {
  return (
    <ReviewCard title={`Decision ${n}`}>
      <RichText className="text-sm font-medium text-(--color-ink)">{d.question}</RichText>
      <ul className="mt-2 flex flex-col gap-1">
        {d.options.map((o, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: stable render order
          <li key={i} className="flex items-start gap-2 text-sm text-(--color-ink-soft)">
            <Badge variant={d.correct_option === i ? "success" : "neutral"} size="sm">
              {String.fromCharCode(65 + i)}
            </Badge>
            <RichText inline>{o}</RichText>
          </li>
        ))}
      </ul>
      {d.expected_reasoning && d.expected_reasoning.length > 0 && (
        <Labeled label="Expected reasoning">{d.expected_reasoning.join(" · ")}</Labeled>
      )}
      <AnswerKey>
        <Labeled label="Correct">{d.correct_feedback}</Labeled>
        <Labeled label="Partial">{d.partial_feedback}</Labeled>
        <Labeled label="Wrong">{d.wrong_feedback}</Labeled>
      </AnswerKey>
    </ReviewCard>
  );
}

export function RealLifeChallengeView({ rlc }: { rlc: RealLifeChallenge }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {rlc.concept_ids?.map((id) => (
          <Badge key={id} variant="accent" size="sm">
            {id}
          </Badge>
        ))}
      </div>
      <Labeled label="Role">{rlc.role}</Labeled>
      <Labeled label="Task">{rlc.task}</Labeled>
      <RichText className="text-sm leading-relaxed text-(--color-ink-soft)">{rlc.context}</RichText>
      <Labeled label="Predict">{rlc.prediction_prompt}</Labeled>
      {rlc.decisions.map((d, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: stable render order
        <Decision key={i} d={d} n={i + 1} />
      ))}
      {rlc.red_herring && <Labeled label="Red herring">{rlc.red_herring}</Labeled>}
      <ReviewCard title="Final summary">
        <RichText className="text-sm text-(--color-ink-soft)">{rlc.final_summary}</RichText>
      </ReviewCard>
    </div>
  );
}
