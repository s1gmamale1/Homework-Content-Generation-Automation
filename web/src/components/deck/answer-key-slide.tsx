/** The teacher-only key for the QuizSlides — light, green, one row per
 *  `deck.answer_key[]` item (correct label + explanation). Deliberately NOT
 *  tied to any particular `stages[]` entry's index — the assembler no
 *  longer identifies a "the quiz stage" by title/position (that broke on
 *  non-Uzbek decks and any stage count other than 7), so this slide's
 *  kicker is a fixed, generic label instead of "N-bosqich · kviz kaliti". */
import type { TeacherDeckAnswerKeyItem } from "@/lib/types";
import { LightSlide, SlideKicker, SlideTitle, TitleUnderline } from "./shared";

export function AnswerKeySlide({ items }: { items: TeacherDeckAnswerKeyItem[] }) {
  return (
    <LightSlide accent="green">
      <SlideKicker label="Kviz kaliti" accent="green" badge="teacher_only" />
      <SlideTitle>Javoblar va izoh (faqat o'qituvchiga)</SlideTitle>
      <TitleUnderline accent="green" />

      <div className="space-y-2.5">
        {items.map((it, i) => (
          <div key={`${i}-${it.number}`} className="flex items-start gap-4 rounded-xl bg-slate-900/[0.035] p-4">
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
