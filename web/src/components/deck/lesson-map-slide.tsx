/** Slide 5/18 — "Dars xaritasi": light, table of the 7 lesson-map rows with
 *  a minutes pill each, and a "Jami: N daqiqa" total footer line. */
import type { TeacherDeckLessonMapItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { LightSlide, SlideKicker, SlideTitle, TitleUnderline } from "./shared";

export function LessonMapSlide({ items }: { items: TeacherDeckLessonMapItem[] }) {
  const total = items.reduce((sum, it) => sum + it.minutes, 0);

  return (
    <LightSlide accent="blue">
      <SlideKicker label="Dars xaritasi" accent="blue" badge="teacher_only" />
      <SlideTitle>{total} daqiqa: qadam-baqadam</SlideTitle>
      <TitleUnderline accent="blue" />

      <div className="overflow-hidden rounded-xl border border-slate-900/[0.06]">
        {items.map((it, i) => (
          <div
            key={it.index}
            className={cn(
              "flex items-center gap-4 px-4 py-3",
              i % 2 === 0 ? "bg-slate-900/[0.03]" : "bg-transparent",
              i > 0 && "border-t border-slate-900/[0.06]",
            )}
          >
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-slate-900/[0.08] text-xs font-bold text-slate-500">
              {it.index}
            </span>
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-slate-900">{it.title}</p>
            </div>
            <p className="hidden max-w-xs shrink-0 truncate text-sm text-slate-500 sm:block">
              {it.description}
            </p>
            <span className="shrink-0 rounded-full border border-slate-900/[0.1] px-2.5 py-1 font-mono text-[0.68rem] font-semibold text-slate-500">
              {it.minutes} daq
            </span>
          </div>
        ))}
      </div>

      <p className="mt-4 text-right text-sm font-bold text-[#4d8dff]">Jami: {total} daqiqa</p>
    </LightSlide>
  );
}
