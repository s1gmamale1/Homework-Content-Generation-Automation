/** Slide 4/18 — "Darsning tayanch g'oyasi": dark, orange accent, one big
 *  statement + supporting elaboration. No top-right badge (matches template). */
import type { TeacherDeckCoreIdea } from "@/lib/types";
import { DarkSlide, SlideKicker } from "./shared";

export function CoreIdeaSlide({ coreIdea }: { coreIdea: TeacherDeckCoreIdea }) {
  return (
    <DarkSlide accent="orange" className="justify-center">
      <SlideKicker label="Darsning tayanch g'oyasi" accent="orange" badge="none" dark />
      <h1 className="max-w-3xl text-3xl font-extrabold leading-tight tracking-tight text-white sm:text-4xl">
        {coreIdea.statement}
      </h1>
      <span
        aria-hidden
        className="my-6 block h-1 w-14 rounded-full"
        style={{ background: "#f0a83c" }}
      />
      <p className="max-w-2xl text-base leading-relaxed text-white/60 sm:text-lg">
        {coreIdea.elaboration}
      </p>
    </DarkSlide>
  );
}
