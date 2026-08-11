/** Slide 3/18 — "Dars maqsadi": three numbered outcome rows
 *  (bilib oladi / qila oladi / tushunadi). */
import type { TeacherDeckObjectives } from "@/lib/types";
import { LightSlide, NumberedPoint, SlideKicker, SlideTitle, TitleUnderline } from "./shared";

export function ObjectivesSlide({ objectives }: { objectives: TeacherDeckObjectives }) {
  const rows: { title: string; detail: string }[] = [
    { title: "Bilib oladi", detail: objectives.bilib_oladi },
    { title: "Qila oladi", detail: objectives.qila_oladi },
    { title: "Tushunadi", detail: objectives.tushunadi },
  ];

  return (
    <LightSlide accent="blue">
      <SlideKicker label="Dars maqsadi" accent="blue" badge="teacher_only" />
      <SlideTitle>Dars oxirida o'quvchi</SlideTitle>
      <TitleUnderline accent="blue" />

      <div className="space-y-3.5">
        {rows.map((r, i) => (
          <NumberedPoint key={r.title} n={i + 1} title={r.title} detail={r.detail} accent="blue" />
        ))}
      </div>
    </LightSlide>
  );
}
