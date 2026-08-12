/** One quiz slide per `deck.quiz[]` item (5 in the fixture) — dark, green,
 *  EKRANGA badge, A/B/C/D option cards, italic hint line at the bottom.
 *  `correct_label` is intentionally NOT surfaced here — this is the
 *  student-facing "on screen" slide; the key lives in AnswerKeySlide. */
import type { TeacherDeckQuizItem } from "@/lib/types";
import { DarkSlide, SlideKicker } from "./shared";

export function QuizSlide({ item, total }: { item: TeacherDeckQuizItem; total: number }) {
  return (
    <DarkSlide accent="green">
      <SlideKicker
        label={`Kviz · savol ${item.number}/${total}`}
        accent="green"
        badge="ekranga"
        dark
      />
      <h1 className="text-2xl font-bold leading-snug tracking-tight text-white sm:text-3xl">
        {item.question}
      </h1>

      <div className="mt-8 grid grid-cols-1 gap-3.5 sm:grid-cols-2">
        {item.options.map((opt) => (
          <div
            key={opt.label}
            className="flex items-center gap-4 rounded-xl bg-white/[0.05] px-4 py-4"
          >
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-white/10 text-sm font-bold text-white">
              {opt.label}
            </span>
            <p className="text-white/90">{opt.text}</p>
          </div>
        ))}
      </div>

      <p className="mt-auto pt-8 text-sm italic text-white/45">{item.hint}</p>
    </DarkSlide>
  );
}
