import { AnswerKey, Labeled } from "@/components/flow-v2/parts";
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import type { ErrorDetection } from "@/lib/types";

export function ErrorDetectionView({ ed }: { ed: ErrorDetection }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Badge variant="neutral" size="sm">
          {ed.pattern}
        </Badge>
        {ed.concept_ids?.map((id) => (
          <Badge key={id} variant="accent" size="sm">
            {id}
          </Badge>
        ))}
      </div>
      <div className="flex flex-col gap-1">
        {ed.blocks.map((b) => (
          <div
            key={b.id}
            className={`flex items-start gap-2 rounded-(--radius-sm) border px-3 py-2 text-sm ${b.is_error ? "border-(--color-error) text-(--color-error)" : "border-(--color-border) text-(--color-ink-soft)"}`}
          >
            <Badge variant={b.is_error ? "error" : "neutral"} size="sm">
              {b.id}
            </Badge>
            <RichText inline>{b.content}</RichText>
          </div>
        ))}
      </div>
      <Labeled label="Hint">{ed.hint}</Labeled>
      {ed.why_prompt && <Labeled label="Why">{ed.why_prompt}</Labeled>}
      <AnswerKey>
        <Labeled label="Correct fix">
          {[ed.correct_answer_for_error_block, ...(ed.accepted_variants ?? [])].join(" · ")}
        </Labeled>
        <Labeled label="Correct fb">{ed.correct_feedback}</Labeled>
        <Labeled label="Wrong fb">{ed.wrong_correction_feedback}</Labeled>
        <Labeled label="Reveal fb">{ed.reveal_feedback}</Labeled>
      </AnswerKey>
    </div>
  );
}
