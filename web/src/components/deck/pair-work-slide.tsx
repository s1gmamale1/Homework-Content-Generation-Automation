/** The pair-work stage — dark, green, EKRANGA badge. Header (bosqich #/
 *  minutes/title) comes from the matching `stages[]` entry; body content
 *  comes from the dedicated `deck.pair_work` object (intro + tasks[]),
 *  which is the structured source of truth (not the stage's screen_text,
 *  which is a paraphrase of the same content for the on-screen projector). */
import type { TeacherDeckPairWork, TeacherDeckStage } from "@/lib/types";
import { DarkSlide, NumberedPoint, SlideKicker, SlideTitle, stageKickerLabel } from "./shared";

export function PairWorkSlide({
  stage,
  pairWork,
}: {
  stage: TeacherDeckStage;
  pairWork: TeacherDeckPairWork;
}) {
  return (
    <DarkSlide accent="green">
      <SlideKicker
        label={stageKickerLabel(stage.index, stage.minutes)}
        accent="green"
        badge="ekranga"
        dark
      />
      <SlideTitle dark>{stage.title}</SlideTitle>

      <p className="mt-5 mb-6 text-white/70">{pairWork.intro}</p>

      <div className="space-y-3.5">
        {pairWork.tasks.map((t, i) => (
          <NumberedPoint key={`${i}-${t.title}`} n={i + 1} title={t.title} detail={t.prompt} accent="green" dark />
        ))}
      </div>
    </DarkSlide>
  );
}
