import { AnswerKey, Labeled, ReviewCard } from "@/components/flow-v2/parts";
import { RichText } from "@/components/rich-text";
import { Badge } from "@/components/ui/badge";
import type { CaseBasedPreview, CaseCheckpoint, LearningBlock } from "@/lib/types";

function Checkpoint({ cp, n }: { cp: CaseCheckpoint; n: number }) {
  return (
    <ReviewCard title={`Checkpoint ${n}`}>
      <div className="mb-2 flex flex-wrap gap-2">
        <Badge variant="neutral" size="sm">
          {cp.intent}
        </Badge>
        <Badge variant="neutral" size="sm">
          {cp.form}
        </Badge>
      </div>
      <RichText className="text-sm font-medium text-(--color-ink)">{cp.question}</RichText>
      {cp.options && cp.options.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {cp.options.map((o, i) => {
            const correct = cp.correct_index === i;
            return (
              // biome-ignore lint/suspicious/noArrayIndexKey: stable render order
              <li key={i} className="flex items-start gap-2 text-sm text-(--color-ink-soft)">
                <Badge variant={correct ? "success" : "neutral"} size="sm">
                  {String.fromCharCode(65 + i)}
                </Badge>
                <RichText inline>{o}</RichText>
              </li>
            );
          })}
        </ul>
      )}
      <AnswerKey>
        <Labeled label="Feedback">{cp.feedback}</Labeled>
      </AnswerKey>
    </ReviewCard>
  );
}

function Block({ lb, n }: { lb: LearningBlock; n: number }) {
  return (
    <ReviewCard
      title={`Learning Block ${n}`}
      className="border-(--color-accent-border) bg-(--color-accent-soft)/30"
    >
      {lb.title && <p className="mb-1 text-sm font-semibold text-(--color-ink)">{lb.title}</p>}
      <RichText className="text-sm leading-relaxed text-(--color-ink-soft)">
        {lb.explanation}
      </RichText>
      {lb.source_concept_id && (
        <p className="mt-1 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          ↳ {lb.source_concept_id}
        </p>
      )}
    </ReviewCard>
  );
}

export function CaseBasedPreviewView({ cbp }: { cbp: CaseBasedPreview }) {
  const cps = cbp.checkpoints ?? [];
  const dpe = cbp.decision_process_explanation;
  const sim = cbp.final_simulation;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-(--color-ink)">{cbp.title}</span>
        <Badge variant="neutral" size="sm">
          {cbp.case_type}
        </Badge>
        {cbp.source_concept_ids?.map((id) => (
          <Badge key={id} variant="accent" size="sm">
            {id}
          </Badge>
        ))}
      </div>

      <ReviewCard title="Case setup">
        <Labeled label="Role">{cbp.case_setup.student_role}</Labeled>
        <RichText className="my-1 text-sm leading-relaxed text-(--color-ink-soft)">
          {cbp.case_setup.narrative}
        </RichText>
        <Labeled label="Task">{cbp.case_setup.task}</Labeled>
      </ReviewCard>

      {cps[0] && <Checkpoint cp={cps[0]} n={1} />}
      {cbp.learning_block_1 && <Block lb={cbp.learning_block_1} n={1} />}
      {cps[1] && <Checkpoint cp={cps[1]} n={2} />}
      {cbp.learning_block_2 && <Block lb={cbp.learning_block_2} n={2} />}
      {cps.slice(2).map((cp, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: stable render order
        <Checkpoint key={i + 2} cp={cp} n={i + 3} />
      ))}

      <ReviewCard title="Decision process (open-ended)">
        <RichText className="text-sm font-medium text-(--color-ink)">{dpe.prompt}</RichText>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {dpe.expected_components.map((c) => (
            <Badge key={c} variant="neutral" size="sm">
              {c}
            </Badge>
          ))}
        </div>
        <AnswerKey>
          <Labeled label="Sample answer">{dpe.sample_acceptable_answer}</Labeled>
        </AnswerKey>
      </ReviewCard>

      <ReviewCard title="Final simulation">
        <Labeled label="Correct path">{sim.correct_path}</Labeled>
        <Labeled label="Wrong path">{sim.wrong_path}</Labeled>
        <AnswerKey>
          <Labeled label="Why wrong fails">{sim.why_wrong_fails}</Labeled>
        </AnswerKey>
      </ReviewCard>

      <ReviewCard title="Feedback summary">
        <Labeled label="Understood">{cbp.feedback_summary.understood}</Labeled>
        <Labeled label="Mistake">{cbp.feedback_summary.mistake}</Labeled>
        <Labeled label="Review">{cbp.feedback_summary.review}</Labeled>
      </ReviewCard>

      <ReviewCard title="Completion">
        <Labeled label="Pass">{cbp.completion_rules.pass_condition}</Labeled>
        <Labeled label="Retry">{cbp.completion_rules.retry_condition}</Labeled>
      </ReviewCard>
    </div>
  );
}
