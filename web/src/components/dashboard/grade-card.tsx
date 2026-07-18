import { useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  coverageState,
  summarizeGrade,
  STATE_ORDER,
  type GradeCoverage,
} from "@/lib/subject-coverage";
import { subjectLabel } from "@/lib/subjects";
import { CARD } from "@/lib/ui";
import { cn } from "@/lib/utils";
import { SubjectRow } from "./subject-row";

function worstRank(item: GradeCoverage["subjects"][number]): number {
  if (item.books.length === 0) return STATE_ORDER.length;
  const states = item.books.map(coverageState);
  const idx = STATE_ORDER.findIndex((s) => states.includes(s));
  return idx === -1 ? STATE_ORDER.length : idx;
}

const LANG_NAME: Record<string, string> = { uz: "Uzbek", en: "English", ru: "Russian" };

export function GradeCard({ grade, lang }: { grade: GradeCoverage; lang: string }) {
  // On non-uz tabs the hidden rows aren't "missing a textbook" — they have no
  // materials IN THIS LANGUAGE (a uz textbook may well exist). Word it so.
  const gapLabel =
    lang === "uz" ? "No textbook yet" : `Nothing in ${LANG_NAME[lang] ?? lang} yet`;
  const [showGaps, setShowGaps] = useState(false);
  const summary = summarizeGrade(grade);
  const present = grade.subjects
    .filter((s) => s.books.length > 0)
    .sort((a, b) => worstRank(a) - worstRank(b) || subjectLabel(a.subject).localeCompare(subjectLabel(b.subject)));
  const missing = grade.subjects
    .filter((s) => s.books.length === 0)
    .map((s) => subjectLabel(s.subject))
    .sort((a, b) => a.localeCompare(b));

  return (
    <section className={cn(CARD, "overflow-hidden")}>
      <header className="border-b border-white/[0.07] px-4 py-3">
        <h2 className="text-lg font-semibold tracking-tight">
          {grade.grade === null ? "Ungraded" : `Grade ${grade.grade}`}
        </h2>
        <p className="mt-0.5 text-sm text-white/50">
          {summary.withTextbook} subject{summary.withTextbook === 1 ? "" : "s"}{lang === "uz" ? " with a textbook" : ` with ${LANG_NAME[lang] ?? lang} materials`}
          {summary.finished > 0 && ` · ${summary.finished} finished`}
          {summary.inProgress > 0 && ` · ${summary.inProgress} in progress`}
          {summary.attention > 0 && ` · ${summary.attention} need attention`}
        </p>
      </header>

      <div className="px-1 py-1">
        {present.length === 0 ? (
          <p className="px-4 py-6 text-sm text-white/40">
            {lang === "uz"
              ? "No textbooks for this grade yet."
              : `Nothing in ${LANG_NAME[lang] ?? lang} for this grade yet.`}
          </p>
        ) : (
          present.map((s) => <SubjectRow key={s.subject} item={s} lang={lang} />)
        )}
      </div>

      {missing.length > 0 && (
        <div className="border-t border-white/[0.07]">
          <button
            type="button"
            onClick={() => setShowGaps((v) => !v)}
            aria-expanded={showGaps}
            className="flex w-full items-center justify-between px-4 py-2.5 text-sm text-white/50 transition-colors hover:text-white/75"
          >
            <span>{gapLabel} ({missing.length})</span>
            <ChevronDown className={cn("size-4 transition-transform", showGaps && "rotate-180")} />
          </button>
          {showGaps && (
            <p className="px-4 pb-3 text-sm leading-relaxed text-white/40">
              {missing.join(" · ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
