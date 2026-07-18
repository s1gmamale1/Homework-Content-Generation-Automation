import { Link } from "react-router-dom";
import type { CoverageEntry } from "@/lib/types";
import {
  coverageState,
  progressOf,
  sortBooksForLang,
  stuckCount,
  STATE_LABEL,
  STATE_TONE,
  type SubjectCoverage,
} from "@/lib/subject-coverage";
import { subjectLabel } from "@/lib/subjects";
import { cn } from "@/lib/utils";

const TONE_CHIP: Record<string, string> = {
  good: "bg-emerald-400/12 text-emerald-200 border-emerald-300/25",
  busy: "bg-sky-400/12 text-sky-200 border-sky-300/25",
  warn: "bg-amber-400/12 text-amber-200 border-amber-300/25",
  idle: "bg-white/[0.06] text-white/55 border-white/10",
};

const TONE_BAR: Record<string, string> = {
  good: "bg-emerald-400/70",
  busy: "bg-sky-400/70",
  warn: "bg-amber-400/70",
  idle: "bg-white/25",
};

function BookLine({ entry }: { entry: CoverageEntry }) {
  const state = coverageState(entry);
  const tone = STATE_TONE[state];
  const { done, total, pct } = progressOf(entry);
  const stuck = stuckCount(entry);
  return (
    <Link
      to={`/book/${entry.book_id}`}
      title={entry.original_filename}
      className="block rounded-xl px-3 py-2.5 transition-colors hover:bg-white/[0.04]"
    >
      <div className="flex items-center gap-3">
        {/* Textbook SOURCE language, always shown: the tabs scope homework
            OUTPUT language, so without this chip a uz textbook generating ru
            homework reads as "the Russian subject" on the Русский tab and a
            click on it feels like a language redirect (traced live, G9
            algebra 2026-07-18). It also disambiguates ru+uz edition twins. */}
        <span className="w-7 shrink-0 rounded-md border border-white/10 bg-white/[0.05] px-1 py-0.5 text-center font-mono text-[0.62rem] uppercase text-white/55">
          {entry.source_language}
        </span>
        <div className="h-2 min-w-24 flex-1 overflow-hidden rounded-full bg-white/[0.07]">
          <div
            className={cn("h-full rounded-full transition-[width]", TONE_BAR[tone])}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="w-24 shrink-0 text-right text-sm tabular-nums text-white/70">
          {total > 0 ? `${done} of ${total}` : "—"}
        </span>
        <span
          className={cn(
            "shrink-0 rounded-lg border px-2 py-0.5 text-xs font-medium",
            TONE_CHIP[tone],
          )}
        >
          {STATE_LABEL[state]}
        </span>
      </div>
      {stuck > 0 && (
        <p className="mt-1 text-xs text-white/45">
          {stuck === 1 ? "1 lesson needs a look" : `${stuck} lessons need a look`}
        </p>
      )}
    </Link>
  );
}

export function SubjectRow({ item, lang }: { item: SubjectCoverage; lang: string }) {
  return (
    <div className="border-t border-white/[0.06] py-2 first:border-t-0">
      <p className="px-3 text-sm font-medium text-white/85">{subjectLabel(item.subject)}</p>
      {item.books.length === 0 ? (
        <p className="px-3 py-2 text-sm text-white/40">No textbook yet</p>
      ) : (
        sortBooksForLang(item.books, lang).map((b) => (
          <BookLine key={b.book_id} entry={b} />
        ))
      )}
    </div>
  );
}
