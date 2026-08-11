/** The closing "Yakun" stage — dark, blue, EKRANGA badge. Header comes from
 *  the matching `stages[]` entry; the reflection questions come from the
 *  dedicated `deck.conclusion.questions[]`. */
import type { TeacherDeckConclusion, TeacherDeckStage } from "@/lib/types";
import { DarkSlide, NumberedPoint, SlideKicker, SlideTitle, stageKickerLabel } from "./shared";

export function ConclusionSlide({
  stage,
  conclusion,
}: {
  stage: TeacherDeckStage;
  conclusion: TeacherDeckConclusion;
}) {
  return (
    <DarkSlide accent="blue">
      <SlideKicker
        label={stageKickerLabel(stage.index, stage.minutes)}
        accent="blue"
        badge="ekranga"
        dark
      />
      <SlideTitle dark>{stage.title}</SlideTitle>

      <div className="mt-6 space-y-3.5">
        {conclusion.questions.map((q, i) => (
          <NumberedPoint key={q} n={i + 1} title={q} accent="blue" dark />
        ))}
      </div>
    </DarkSlide>
  );
}
