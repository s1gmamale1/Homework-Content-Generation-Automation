/** Slide 2/18 — "Dars pasporti": light card, 2-column grid of labeled facts. */
import type { TeacherDeckPassport } from "@/lib/types";
import { LightSlide, SlideKicker, SlideTitle, TitleUnderline } from "./shared";

const FIELDS: { key: keyof TeacherDeckPassport; label: string }[] = [
  { key: "fan_sinf", label: "Fan / sinf" },
  { key: "mavzu", label: "Mavzu" },
  { key: "dars_turi", label: "Dars turi" },
  { key: "metod", label: "Metod" },
  { key: "kerakli_vosita", label: "Kerakli vosita" },
  { key: "baholash", label: "Baholash" },
];

export function PassportSlide({ passport }: { passport: TeacherDeckPassport }) {
  return (
    <LightSlide accent="blue">
      <SlideKicker label="Dars pasporti" accent="blue" badge="teacher_only" />
      <SlideTitle>Dars haqida qisqacha</SlideTitle>
      <TitleUnderline accent="blue" />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {FIELDS.map(({ key, label }) => (
          <div key={key} className="rounded-xl bg-slate-900/[0.035] p-4">
            <p className="mb-1 font-mono text-[0.62rem] font-bold uppercase tracking-[0.14em] text-slate-400">
              {label}
            </p>
            <p className="font-semibold text-slate-900">{passport[key]}</p>
          </div>
        ))}
      </div>
    </LightSlide>
  );
}
