/** The teacher-only key for the 5 QuizSlides — light, green, one row per
 *  `deck.answer_key[]` item (correct label + explanation). `stageIndex` is
 *  the Kviz stage's own `index` (5 in the fixture), reused for the kicker
 *  so the numbering stays consistent with the lesson map / stage slides. */
import type { TeacherDeckAnswerKeyItem } from "@/lib/types";
import { LightSlide, SlideKicker, SlideTitle, TitleUnderline } from "./shared";

export function AnswerKeySlide({
  items,
  stageIndex,
}: {
  items: TeacherDeckAnswerKeyItem[];
  stageIndex: number;
}) {
  return (
    <LightSlide accent="green">
      <SlideKicker label={`${stageIndex}-bosqich · kviz kaliti`} accent="green" badge="teacher_only" />
      <SlideTitle>Javoblar va izoh (faqat o'qituvchiga)</SlideTitle>
      <TitleUnderline accent="green" />

      <div className="space-y-2.5">
        {items.map((it) => (
          <div key={it.number} className="flex items-start gap-4 rounded-xl bg-slate-900/[0.035] p-4">
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[#34d399] text-sm font-bold text-white">
              {it.correct_label}
            </span>
            <div className="min-w-0">
              <p className="font-semibold text-slate-900">{it.number}-savol</p>
              <p className="mt-0.5 text-sm leading-relaxed text-slate-600">{it.explanation}</p>
            </div>
          </div>
        ))}
      </div>
    </LightSlide>
  );
}
