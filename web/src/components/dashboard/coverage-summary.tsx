import { summarizeGrade, type GradeCoverage } from "@/lib/subject-coverage";
import { CARD } from "@/lib/ui";
import { cn } from "@/lib/utils";

function Tile({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className={cn(CARD, "px-4 py-3")}>
      <p className={cn("text-2xl font-semibold tabular-nums", tone)}>{value}</p>
      <p className="mt-0.5 text-xs text-white/50">{label}</p>
    </div>
  );
}

const LANG_NAME: Record<string, string> = { uz: "Uzbek", en: "English", ru: "Russian" };

export function CoverageSummary({ grades, lang }: { grades: GradeCoverage[]; lang: string }) {
  const totals = grades.reduce(
    (acc, g) => {
      const s = summarizeGrade(g);
      return {
        finished: acc.finished + s.finished,
        inProgress: acc.inProgress + s.inProgress,
        attention: acc.attention + s.attention,
        missing: acc.missing + s.missing,
      };
    },
    { finished: 0, inProgress: 0, attention: 0, missing: 0 },
  );
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile label="Finished" value={totals.finished} tone="text-emerald-300" />
      <Tile label="In progress" value={totals.inProgress} tone="text-sky-300" />
      <Tile label="Need attention" value={totals.attention} tone="text-amber-300" />
      <Tile label={lang === "uz" ? "No textbook yet" : `Nothing in ${LANG_NAME[lang] ?? lang} yet`} value={totals.missing} tone="text-white/70" />
    </div>
  );
}
