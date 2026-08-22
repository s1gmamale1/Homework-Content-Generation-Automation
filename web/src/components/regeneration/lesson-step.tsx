import {
  type RegenerationErrorView,
  regenerationBookFacets,
  regenerationBookOptions,
  regenerationIneligibleLine,
  regenerationLanguageLabel,
  regenerationNarrowScope,
  regenerationSourceRow,
  regenerationSourcesView,
  regenerationToggleLesson,
} from "@/lib/api";
import type { GuidedRegenerationDraft } from "@/lib/regeneration-draft";
import type {
  Book,
  RegenerationEligibleSource,
  RegenerationIneligibleLineage,
  RegenerationOutputLanguage,
} from "@/lib/types";
import { FRAME_OFF, FRAME_ON, GHOST_BTN, PRESSABLE, PRIMARY_BTN } from "@/lib/ui";
import { cn } from "@/lib/utils";

const LANGUAGES: RegenerationOutputLanguage[] = ["uz", "ru", "en"];

export function LessonStep({
  draft,
  books,
  sources,
  ineligible,
  loading,
  error,
  errorView,
  blockedReason,
  onChange,
  onContinue,
}: {
  draft: GuidedRegenerationDraft;
  books: Book[] | undefined;
  sources: RegenerationEligibleSource[];
  ineligible: RegenerationIneligibleLineage[];
  loading: boolean;
  error: React.ReactNode;
  errorView: RegenerationErrorView | null;
  blockedReason: string | null;
  onChange: (draft: GuidedRegenerationDraft) => void;
  onContinue: () => void;
}) {
  const narrow = (change: Parameters<typeof regenerationNarrowScope>[1]) => {
    const next = regenerationNarrowScope(draft, change);
    const selected = new Set(next.selectedTocEntryIds);
    onChange({
      ...next,
      destinationOverrides: draft.destinationOverrides.filter(
        (item) => selected.has(item.tocEntryId) && item.outputLanguage === next.language,
      ),
    });
  };
  const facets = regenerationBookFacets(books, { subject: draft.subjectFilter });
  const options = regenerationBookOptions(books, {
    subject: draft.subjectFilter,
    grade: draft.gradeFilter,
  });
  const visibleSources = sources.filter((source) => source.output_language === draft.language);
  const bookById = new Map(regenerationBookOptions(books).map((book) => [book.id, book]));
  const sourcesView = regenerationSourcesView({
    sources: visibleSources,
    isLoading: loading,
    error: errorView,
    blockedReason,
  });

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-white">Choose lessons</h2>
        <p className="mt-1 text-xs leading-5 text-white/45">
          Pick one textbook, then select the completed lessons you want to rebuild. The library is
          fully loaded, not limited to the first 100 books.
        </p>
      </div>

      {error}
      <div className="flex flex-wrap gap-2">
        <select
          aria-label="Subject"
          value={draft.subjectFilter ?? ""}
          onChange={(event) => narrow({ subjectFilter: event.target.value || null })}
          className={cn(FRAME_OFF, "min-w-44 rounded-xl bg-[#11131b] px-3 py-2 text-sm text-white")}
        >
          <option value="">All subjects</option>
          {facets.subjects.map((facet) => (
            <option key={facet.value} value={facet.value}>
              {facet.label} · {facet.count}
            </option>
          ))}
        </select>
        <select
          aria-label="Grade"
          value={draft.gradeFilter ?? ""}
          onChange={(event) => narrow({ gradeFilter: event.target.value || null })}
          className={cn(FRAME_OFF, "min-w-36 rounded-xl bg-[#11131b] px-3 py-2 text-sm text-white")}
        >
          <option value="">All grades</option>
          {facets.grades.map((facet) => (
            <option key={facet.value || "none"} value={facet.value}>
              {facet.label} · {facet.count}
            </option>
          ))}
        </select>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((book) => (
          <button
            key={book.id}
            type="button"
            onClick={() => narrow({ bookId: book.id })}
            className={cn(
              "rounded-xl p-3 text-left",
              PRESSABLE,
              draft.bookId === book.id ? FRAME_ON : FRAME_OFF,
            )}
          >
            <span className="block text-sm text-white/85">{book.title}</span>
            <span className="mt-1 block font-mono text-[0.65rem] text-white/40">
              {book.subjectLabel} · {book.gradeLabel}
            </span>
          </button>
        ))}
      </div>
      {loading && <p className="text-xs text-white/40">Loading eligible lessons…</p>}
      {sourcesView.message && (
        <p className="text-xs leading-5 text-white/45">{sourcesView.message}</p>
      )}

      {draft.bookId && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-1">
            {LANGUAGES.map((language) => (
              <button
                key={language}
                type="button"
                className={cn(GHOST_BTN, draft.language === language && FRAME_ON)}
                onClick={() => narrow({ language })}
              >
                {regenerationLanguageLabel(language)}
              </button>
            ))}
          </div>
          <ul className="max-h-[28rem] space-y-2 overflow-y-auto pr-1">
            {sourcesView.sources.map((source) => {
              const row = regenerationSourceRow(source, bookById.get(source.book_id));
              return (
                <li key={row.key}>
                  <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/[0.08] bg-white/[0.025] p-3 hover:bg-white/[0.05]">
                    <input
                      type="checkbox"
                      className="mt-1 size-4 accent-[#7c5cff]"
                      checked={draft.selectedTocEntryIds.includes(source.toc_entry_id)}
                      onChange={() => {
                        const next = regenerationToggleLesson(draft, source.toc_entry_id);
                        const selected = new Set(next.selectedTocEntryIds);
                        onChange({
                          ...next,
                          destinationOverrides: draft.destinationOverrides.filter(
                            (item) =>
                              selected.has(item.tocEntryId) &&
                              item.outputLanguage === next.language,
                          ),
                        });
                      }}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-white/85">
                        {row.headline}
                      </span>
                      <span className="mt-1 block text-xs text-white/45">{row.bookLine}</span>
                      <span className="mt-1 block text-xs text-white/40">
                        {row.contextLine} · requested V{draft.publicationVersion}
                      </span>
                      {row.noPageWarning && (
                        <span className="mt-1 block text-xs text-amber-100/70">
                          No DB-known Lesson Topic pointer yet; Review will resolve it from Notion.
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {ineligible.length > 0 && (
        <details className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
          <summary className="cursor-pointer text-xs text-white/50">
            Why some lesson lineages are unavailable ({ineligible.length})
          </summary>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-white/40">
            {ineligible.map((row) => (
              <li key={`${row.toc_entry_id}:${row.output_language}`}>
                {regenerationIneligibleLine(row)}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="flex justify-end">
        <button
          type="button"
          className={PRIMARY_BTN}
          disabled={draft.selectedTocEntryIds.length === 0}
          onClick={onContinue}
        >
          Continue with {draft.selectedTocEntryIds.length || 0} lesson
          {draft.selectedTocEntryIds.length === 1 ? "" : "s"}
        </button>
      </div>
    </section>
  );
}
