import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { SpaceBackdrop } from "@/components/space-backdrop";
import { CoverageSummary } from "@/components/dashboard/coverage-summary";
import { GradeCard } from "@/components/dashboard/grade-card";
import { api } from "@/lib/api";
import { LANG_LABEL } from "@/lib/language";
import { groupByGrade, type GradeCoverage } from "@/lib/subject-coverage";
import { SUBJECTS, type OutputLanguage } from "@/lib/types";
import { FRAME_OFF, FRAME_ON, PRESSABLE } from "@/lib/ui";
import { cn } from "@/lib/utils";

const LANGS: OutputLanguage[] = ["uz", "en", "ru"];
const GRADES = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"];

/** Fill the fixed grade axis and, within each grade, every registry subject —
 *  so a missing textbook is visibly a gap rather than an absent row. */
function withFullCurriculum(groups: GradeCoverage[]): GradeCoverage[] {
  const byGrade = new Map(groups.map((g) => [g.grade, g]));
  const ungraded = byGrade.get(null);
  const axis: (string | null)[] = [...GRADES, ...(ungraded ? [null] : [])];
  return axis.map((grade) => {
    const found = byGrade.get(grade);
    const bySubject = new Map((found?.subjects ?? []).map((s) => [s.subject, s]));
    return {
      grade,
      subjects: SUBJECTS.map((subject) => bySubject.get(subject) ?? { subject, books: [] }),
    };
  });
}

export function DashboardPage() {
  const [lang, setLang] = useState<OutputLanguage>("uz");
  const [grade, setGrade] = useState<string | null>("9");

  const q = useQuery({
    queryKey: ["coverage", lang],
    queryFn: () => api.getCoverage(lang),
    refetchInterval: 10_000,
  });

  const grades = useMemo(
    () => withFullCurriculum(groupByGrade(q.data?.entries ?? [])),
    [q.data],
  );
  const selected = grades.find((g) => g.grade === grade) ?? grades[0];

  return (
    <>
      <SpaceBackdrop />
      <div className="relative z-10 space-y-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Subject dashboard</h1>
          <p className="mt-1 text-white/55">
            How homework generation is going, grade by grade.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {LANGS.map((l) => (
            <button
              key={l}
              type="button"
              onClick={() => setLang(l)}
              className={cn(
                "rounded-xl px-3 py-1.5 text-xs font-medium",
                PRESSABLE,
                l === lang ? FRAME_ON : FRAME_OFF,
              )}
            >
              {LANG_LABEL[l]}
            </button>
          ))}
        </div>

        {q.isLoading ? (
          <p className="text-white/50">Loading…</p>
        ) : q.isError ? (
          <p className="text-amber-200">Could not load the dashboard. Retrying…</p>
        ) : (
          <>
            <CoverageSummary grades={grades} />

            <div className="flex flex-wrap items-center gap-2">
              {grades.map((g) => (
                <button
                  key={g.grade ?? "ungraded"}
                  type="button"
                  onClick={() => setGrade(g.grade)}
                  className={cn(
                    "rounded-xl px-3 py-1.5 text-xs font-medium",
                    PRESSABLE,
                    g.grade === selected?.grade ? FRAME_ON : FRAME_OFF,
                  )}
                >
                  {g.grade === null ? "Ungraded" : `Grade ${g.grade}`}
                </button>
              ))}
            </div>

            {selected && <GradeCard key={selected.grade ?? "ungraded"} grade={selected} />}
          </>
        )}
      </div>
    </>
  );
}
