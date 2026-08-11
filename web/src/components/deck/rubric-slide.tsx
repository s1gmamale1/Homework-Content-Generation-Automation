/** Final slide — "Baholash": light, orange, 3 component cards (each with a
 *  point value, title, detail) + a dark total/grade-band footer row. */
import type { TeacherDeckRubric } from "@/lib/types";
import { LightSlide, SlideKicker, SlideTitle, TitleUnderline } from "./shared";

export function RubricSlide({ rubric }: { rubric: TeacherDeckRubric }) {
  return (
    <LightSlide accent="orange">
      <SlideKicker label="Baholash" accent="orange" badge="teacher_only" />
      <SlideTitle>Dars uchun {rubric.total} ballik mezon</SlideTitle>
      <TitleUnderline accent="orange" />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {rubric.components.map((c) => (
          <div
            key={c.title}
            className="rounded-xl border-t-[3px] bg-slate-900/[0.03] p-4"
            style={{ borderTopColor: "#f0a83c" }}
          >
            <p className="text-3xl font-extrabold text-slate-900">{c.points}</p>
            <p className="mt-2 font-semibold text-slate-900">{c.title}</p>
            <p className="mt-1 text-sm leading-relaxed text-slate-500">{c.detail}</p>
          </div>
        ))}
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl bg-[#0d1120] px-5 py-4 text-sm font-semibold text-white">
        <span>Jami {rubric.total} ball</span>
        {rubric.bands.map((b) => (
          <span key={b.range} className="text-white/70">
            {b.range} = «{b.grade}»
          </span>
        ))}
      </div>
    </LightSlide>
  );
}
