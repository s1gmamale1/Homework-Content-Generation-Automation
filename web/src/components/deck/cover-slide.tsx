/** Slide 1/18 in the template — dark cover, blue left accent bar, title +
 *  subtitle, a row of stat pills, and a footer-style subject/date line. */
import type { TeacherDeckMeta, TeacherDeckPassport } from "@/lib/types";
import { DarkSlide } from "./shared";

export function CoverSlide({
  meta,
  passport,
  stageCount,
}: {
  meta: TeacherDeckMeta;
  passport: TeacherDeckPassport;
  stageCount: number;
}) {
  const pills = [
    `${meta.duration_min} daqiqa`,
    `${meta.topic_number}-mavzu`,
    `${stageCount} bosqich`,
    meta.video_ref ? `${meta.video_ref} bilan` : null,
  ].filter((p): p is string => Boolean(p));

  return (
    <DarkSlide accent="blue" className="justify-between">
      <div>
        <p className="font-mono text-[0.68rem] font-bold uppercase tracking-[0.24em] text-[#4d8dff]">
          Akademiya.ai · O'qituvchi konspekti
        </p>
        <h1 className="mt-5 text-3xl font-extrabold leading-[1.08] tracking-tight text-white sm:text-5xl">
          {meta.topic_title}
        </h1>
        <p className="mt-5 text-base text-white/55 sm:text-lg">
          O'qituvchi qo'llanmasi — {meta.duration_min} daqiqalik dars
        </p>

        <div className="mt-8 flex flex-wrap gap-2.5">
          {pills.map((p) => (
            <span
              key={p}
              className="rounded-full border border-white/10 bg-white/[0.06] px-4 py-2 text-sm font-semibold text-white/85"
            >
              {p}
            </span>
          ))}
        </div>
      </div>

      <div className="mt-10 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-5 text-xs text-white/45">
        <span>{passport.fan_sinf}</span>
        <span>Sana: ___ / ___ / 20___</span>
      </div>
    </DarkSlide>
  );
}
