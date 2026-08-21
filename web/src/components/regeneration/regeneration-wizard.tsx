import {
  REGENERATION_CREATE_LABEL,
  REGENERATION_NO_SPEND_NOTE,
  REGENERATION_READ_RETRY_LABEL,
  type RegenerationErrorView,
  type RegenerationScopeChange,
  type RegenerationScopeState,
  cascadeFromPlan,
  clampCanarySize,
  phaseSelectionFromPlan,
  regenerationBookFacets,
  regenerationBookOptions,
  regenerationIneligibleLine,
  regenerationKeyedLines,
  regenerationLanguageLabel,
  regenerationNarrowScope,
  regenerationPlanBlockedReason,
  regenerationPlanStepView,
  regenerationSelectablePhases,
  regenerationSourceRow,
  regenerationSourcesView,
  regenerationToggleLesson,
} from "@/lib/api";
import {
  exclusionWarning,
  formatUsd,
  launchGate,
  lessonCountLabel,
} from "@/lib/regeneration-state";
import type {
  Book,
  ProviderModelManifest,
  RegenerationEligibleSource,
  RegenerationEstimateResponse,
  RegenerationIneligibleLineage,
  RegenerationOutputLanguage,
  RegenerationPhasePlan,
} from "@/lib/types";
import { CARD, FRAME_OFF, FRAME_ON, GHOST_BTN, PRESSABLE, PRIMARY_BTN } from "@/lib/ui";
import { cn, formatPhaseName } from "@/lib/utils";
/**
 * Campaign wizard, over the real Task 9 API.
 *
 * Nothing on this screen spends money or writes to Notion: `/phase-plan` and
 * `/estimate` are read-only previews and `POST /campaigns` only freezes a row.
 * The canary launch — the first paid step — lives on the campaign panel, not
 * here, so the create button can never be mistaken for it.
 *
 * The dependency closure is the SERVER's. This component never walks
 * `PHASE_DEPS` in TypeScript: it renders `canonical_phases`,
 * `auto_included_phases`, `regenerated_phases` and `broken_dependency_edges`
 * exactly as the planner returned them, and the cascade headline is derived
 * from that same payload by `cascadeFromPlan`.
 */
import {
  CircleAlert,
  CircleDollarSign,
  Layers,
  ListChecks,
  RotateCcw,
  TriangleAlert,
} from "lucide-react";

const LANGUAGES: RegenerationOutputLanguage[] = ["uz", "ru", "en"];

/** Everything the draft holds. The scope half — subject/grade/book, language,
 *  lessons, phases, acknowledgement, canary — is shared with
 *  `regenerationNarrowScope`, which owns what a narrowing clears. */
export interface RegenerationDraftState extends RegenerationScopeState {
  refreshExtraction: boolean;
  provider: string;
  /** Never defaulted for the operator: the server refuses a campaign with no
   *  content model precisely so nobody freezes a whole campaign onto whatever
   *  happens to be first in the manifest. */
  model: string | null;
}

export function defaultRegenerationDraft(): RegenerationDraftState {
  return {
    subjectFilter: null,
    gradeFilter: null,
    bookId: null,
    language: "uz",
    selectedTocEntryIds: [],
    selectedPhases: [],
    excludedPhases: [],
    refreshExtraction: false,
    acknowledged: false,
    canarySize: 1,
    provider: "gemini",
    model: null,
  };
}

function Step({
  index,
  title,
  hint,
  children,
}: {
  index: number;
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn(CARD, "space-y-3")}>
      <header className="flex items-start gap-3">
        <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-white/[0.12] bg-white/[0.06] font-mono text-[0.7rem] text-white/70">
          {index}
        </span>
        <div>
          <h3 className="text-sm font-semibold text-white">{title}</h3>
          {hint && <p className="mt-1 max-w-[70ch] text-xs leading-5 text-white/45">{hint}</p>}
        </div>
      </header>
      {children}
    </section>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-xl px-3 py-1.5 text-xs font-medium",
        PRESSABLE,
        active ? FRAME_ON : FRAME_OFF,
      )}
    >
      {children}
    </button>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2">
      <div className="font-mono text-[0.62rem] uppercase tracking-[0.14em] text-white/40">
        {label}
      </div>
      <div className="mt-1 text-sm font-semibold text-white">{value}</div>
    </div>
  );
}

/**
 * The one error block for the whole regeneration area — the route, the list,
 * the canary and the report all render through this.
 *
 * It lived as four near-identical local copies, which is how three of them
 * ended up keying `details` on the line text: a validation payload that names
 * the same field twice, or a preflight that blocks two lessons with the same
 * title, silently lost a row. `regenerationKeyedLines` is the tested fix, and
 * having one component is what makes it one fix.
 */
export function RegenerationProblem({
  view,
  onRetry,
  retryLabel = REGENERATION_READ_RETRY_LABEL,
}: {
  view: RegenerationErrorView;
  /** Only for a READ that can simply be run again. A mutation refusal is an
   *  answer, not a glitch — offering to repeat it would invite an operator to
   *  hammer a 409, so the campaign/target actions deliberately pass nothing. */
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const details = regenerationKeyedLines(view.details);
  return (
    <div className="space-y-1 rounded-xl border border-rose-300/25 bg-rose-300/[0.07] p-3 text-xs leading-5 text-rose-100/90">
      <div className="flex items-start gap-2 font-semibold">
        <CircleAlert className="mt-0.5 size-4 shrink-0" />
        <span>{view.title}</span>
      </div>
      <p className="max-w-[75ch]">{view.message}</p>
      {details.length > 0 && (
        <ul className="space-y-0.5 pl-5 [list-style:disc]">
          {details.map((row) => (
            <li key={row.key}>{row.text}</li>
          ))}
        </ul>
      )}
      {view.hint && <p className="max-w-[75ch] text-rose-100/70">{view.hint}</p>}
      {onRetry && (
        <button type="button" onClick={onRetry} className={cn(GHOST_BTN, "mt-1 px-2 py-1 text-xs")}>
          <RotateCcw className="size-3.5" />
          {retryLabel}
        </button>
      )}
    </div>
  );
}

export function RegenerationWizard({
  books,
  booksLoading,
  booksError,
  sources,
  ineligible,
  sourcesLoading,
  sourcesError,
  pickBookReason,
  phaseCatalog,
  plan,
  planLoading,
  planError,
  estimate,
  estimateLoading,
  estimateError,
  manifest,
  manifestError,
  state,
  onChange,
  onCreate,
  creating,
  createError,
}: {
  /** `GET /api/v1/books` — the BOUNDED first step (~246 rows). Lessons are
   *  never listed database-wide; they are listed one book at a time. */
  books: Book[] | undefined;
  booksLoading: boolean;
  booksError: RegenerationErrorView | null;
  /** `GET /eligible?book_id=…` for the SELECTED book only. */
  sources: RegenerationEligibleSource[];
  ineligible: RegenerationIneligibleLineage[];
  sourcesLoading: boolean;
  /** The eligible read FAILED. It belongs to this step — the one where lessons
   *  are picked — and never to the phase step: routed there, a 500 on the
   *  lesson read appeared under "Pick the phases to rebuild" while this step
   *  claimed the book had no regenerable lessons. */
  sourcesError: RegenerationErrorView | null;
  /** Non-null while the eligible query is deliberately switched off. */
  pickBookReason: string | null;
  /** `canonical_phases` for the primary subject, so the operator has something
   *  to tick before any phase is selected. `/phase-plan` refuses an empty
   *  selection outright, so this arrives from its own probe query. */
  phaseCatalog: string[];
  /** `POST /phase-plan` for the primary subject in the selection. */
  plan: RegenerationPhasePlan | null;
  planLoading: boolean;
  planError: RegenerationErrorView | null;
  /** `POST /estimate` — the authority for cost, preflight and per-subject plans. */
  estimate: RegenerationEstimateResponse | null;
  estimateLoading: boolean;
  estimateError: RegenerationErrorView | null;
  manifest: ProviderModelManifest | undefined;
  /** `GET /agent/models` failed. Without it there is no provider list and no
   *  model list, and the server refuses a campaign with no content model — so
   *  this is a blocked wizard, not a step with nothing in it. */
  manifestError: RegenerationErrorView | null;
  state: RegenerationDraftState;
  onChange: (next: RegenerationDraftState) => void;
  onCreate: () => void;
  creating: boolean;
  createError: RegenerationErrorView | null;
}) {
  const patch = (over: Partial<RegenerationDraftState>) => onChange({ ...state, ...over });
  /** Every subject/grade/book/language change goes through the one helper that
   *  decides what it invalidates — a lesson belongs to one book in one
   *  language, and a phase list belongs to one subject's flow. */
  const narrow = (change: RegenerationScopeChange) =>
    onChange(regenerationNarrowScope(state, change));
  const toggle = (list: string[], value: string): string[] =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  const facets = regenerationBookFacets(books, { subject: state.subjectFilter });
  const bookOptions = regenerationBookOptions(books, {
    subject: state.subjectFilter,
    grade: state.gradeFilter,
  });
  // Unfiltered, so a lesson's own book always resolves even when the narrowing
  // chips have moved on.
  const bookById = new Map(regenerationBookOptions(books).map((b) => [b.id, b]));
  const selectedBook = state.bookId ? bookById.get(state.bookId) : undefined;

  const visible = sources.filter((s) => s.output_language === state.language);
  const chosen = visible.filter((s) => state.selectedTocEntryIds.includes(s.toc_entry_id));
  const targetCount = chosen.length;

  // Which of blocked / loading / failed / empty / list step 2 is in is a
  // decision, not a render detail: a failed read used to fall through to "no
  // lesson in this book has a complete published homework job".
  const sourcesView = regenerationSourcesView({
    sources: visible,
    isLoading: sourcesLoading,
    error: sourcesError,
    blockedReason: pickBookReason,
  });

  const selection = plan ? phaseSelectionFromPlan(plan) : null;
  const cascade = plan ? cascadeFromPlan(plan) : null;
  // "Nothing is selected", "the planner has not answered yet" and "the plan
  // read failed" are three different things; only the first is about the
  // operator's own choice.
  const planStep = regenerationPlanStepView({
    plan,
    hasSelection: state.selectedPhases.length > 0 || state.refreshExtraction,
    isLoading: planLoading,
    error: planError,
  });
  // `canonical_phases` includes `extract`, which `selected_phases` refuses —
  // it has its own switch in step 6.
  const selectablePhases = regenerationSelectablePhases(phaseCatalog);
  const warning = selection ? exclusionWarning(selection) : null;
  const localGate = selection
    ? launchGate(selection, state.acknowledged, targetCount)
    : { canLaunch: false, requiresAcknowledgement: false, blockedReason: null };

  // The server decides whether an exclusion really breaks an edge — it depends
  // on the subject's flow — so its answer wins over the local warning.
  const needsAcknowledgement =
    estimate?.acknowledgement_required ?? plan?.acknowledgement_required ?? false;

  const totals = estimate?.estimate ?? null;
  const preflight = estimate?.preflight ?? null;
  // A static-envelope line is missing VOLUME evidence, which is a different
  // (and independently reported) gap from a line missing a RATE.
  const staticLines = (totals?.line_items ?? []).filter((l) => l.is_static_envelope).length;

  const apiProviders = Object.keys(manifest?.providers ?? {})
    .filter((p) => manifest?.api_supported?.[p])
    .sort();
  const models = manifest?.providers?.[state.provider] ?? [];

  const blockedReason: string | null = !state.bookId
    ? "Pick the textbook these lessons come from."
    : targetCount === 0
      ? "Select at least one lesson to regenerate."
      : !plan
        ? regenerationPlanBlockedReason(planStep)
        : !localGate.canLaunch
          ? localGate.blockedReason
          : needsAcknowledgement && !state.acknowledged
            ? "Acknowledge the consistency warning before creating this campaign."
            : !state.model
              ? manifestError
                ? "The model list could not be loaded, so there is no model to freeze this campaign to."
                : "Pick the model this campaign will be frozen to."
              : null;

  const canarySize = clampCanarySize(state.canarySize, targetCount);

  return (
    <div className="space-y-4">
      <Step
        index={1}
        title="Pick the textbook"
        hint="Lessons are listed one book at a time. Narrowing by subject and grade first is what makes two lessons called the same thing tellable apart — and it keeps this screen from asking the server for every lesson lineage in the database."
      >
        {booksError && <RegenerationProblem view={booksError} />}
        {booksLoading && books === undefined && (
          <p className="text-xs text-white/40">Loading textbooks…</p>
        )}

        <div className="flex flex-wrap gap-1">
          <Chip
            active={state.subjectFilter === null}
            onClick={() => narrow({ subjectFilter: null })}
          >
            All subjects
          </Chip>
          {facets.subjects.map((facet) => (
            <Chip
              key={facet.value}
              active={state.subjectFilter === facet.value}
              onClick={() => narrow({ subjectFilter: facet.value })}
            >
              {facet.label} · {facet.count}
            </Chip>
          ))}
        </div>

        <div className="flex flex-wrap gap-1">
          <Chip active={state.gradeFilter === null} onClick={() => narrow({ gradeFilter: null })}>
            All grades
          </Chip>
          {facets.grades.map((facet) => (
            <Chip
              key={facet.value || "no-grade"}
              active={state.gradeFilter === facet.value}
              onClick={() => narrow({ gradeFilter: facet.value })}
            >
              {facet.label} · {facet.count}
            </Chip>
          ))}
        </div>

        {books !== undefined && bookOptions.length === 0 && (
          <p className="text-xs text-white/40">
            {books.length === 0
              ? "No textbook has been uploaded yet, so there is nothing to regenerate from."
              : "No textbook matches this narrowing. Widen the subject or grade above."}
          </p>
        )}
        {bookOptions.length > 0 && (
          <ul className="max-h-64 space-y-1 overflow-y-auto pr-1">
            {bookOptions.map((book) => (
              <li key={book.id}>
                <button
                  type="button"
                  onClick={() => narrow({ bookId: book.id })}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left",
                    PRESSABLE,
                    state.bookId === book.id ? FRAME_ON : FRAME_OFF,
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-white/85">{book.title}</span>
                    <span className="mt-0.5 block font-mono text-[0.62rem] text-white/40">
                      {book.subjectLabel} · {book.gradeLabel}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="text-[0.68rem] leading-5 text-white/35">
          {bookOptions.length} textbook{bookOptions.length === 1 ? "" : "s"} listed
          {selectedBook ? ` · selected: ${selectedBook.label}` : " · none selected yet"}
        </p>
      </Step>

      <Step
        index={2}
        title="Pick completed lessons and an output language"
        hint="Only a finished homework job with a complete snapshot can be a source. Uzbek, Russian and English are independent lineages with their own version sequences."
      >
        <div className="flex flex-wrap gap-1">
          {LANGUAGES.map((language) => (
            <Chip
              key={language}
              active={state.language === language}
              onClick={() => narrow({ language })}
            >
              {regenerationLanguageLabel(language)}
            </Chip>
          ))}
        </div>
        {sourcesView.error && <RegenerationProblem view={sourcesView.error} />}
        {sourcesView.message && (
          <p className="max-w-[75ch] text-xs leading-5 text-white/45">{sourcesView.message}</p>
        )}
        <ul className="space-y-1">
          {sourcesView.sources.map((source) => {
            const row = regenerationSourceRow(source, bookById.get(source.book_id));
            return (
              <li key={row.key}>
                <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2 text-sm text-white/75 transition-colors hover:bg-white/[0.05]">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 accent-[#7c5cff]"
                    checked={state.selectedTocEntryIds.includes(source.toc_entry_id)}
                    onChange={() => onChange(regenerationToggleLesson(state, source.toc_entry_id))}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{row.headline}</span>
                    <span className="mt-0.5 block truncate font-mono text-[0.62rem] text-white/40">
                      {row.bookLine}
                    </span>
                    <span className="mt-0.5 block font-mono text-[0.62rem] text-white/40">
                      {row.contextLine}
                    </span>
                    {row.noPageWarning && (
                      <span className="mt-0.5 block text-[0.62rem] leading-4 text-amber-200/80">
                        {row.noPageWarning}
                      </span>
                    )}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
        {ineligible.length > 0 && (
          <details className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2">
            <summary className="cursor-pointer text-xs text-white/50">
              {ineligible.length} selected {ineligible.length === 1 ? "lineage" : "lineages"} cannot
              be regenerated
            </summary>
            <ul className="mt-1 space-y-0.5 text-xs leading-5 text-white/45">
              {ineligible.map((row) => (
                <li key={`${row.toc_entry_id}:${row.output_language}`}>
                  {regenerationIneligibleLine(row)}
                </li>
              ))}
            </ul>
          </details>
        )}
      </Step>

      <Step
        index={3}
        title="Pick the phases to rebuild"
        hint="Ticking a phase also rebuilds everything downstream of it. Step 3 shows the real expansion the planner returned."
      >
        <div className="flex flex-wrap gap-1">
          {selectablePhases.map((phase) => (
            <Chip
              key={phase}
              active={state.selectedPhases.includes(phase)}
              onClick={() =>
                patch({
                  selectedPhases: toggle(state.selectedPhases, phase),
                  excludedPhases: [],
                  acknowledged: false,
                })
              }
            >
              {formatPhaseName(phase)}
            </Chip>
          ))}
          {selectablePhases.length === 0 && (
            <span className="text-xs text-white/40">
              Pick a lesson first — the phase list comes from that subject's flow.
            </span>
          )}
        </div>
        {planError && <RegenerationProblem view={planError} />}
      </Step>

      <Step index={4} title="Review what the dependency graph pulls in">
        {cascade && planStep.mode === "ready" ? (
          <>
            <div className="flex items-center gap-2 text-sm font-semibold text-white">
              <Layers className="size-4 text-white/50" />
              {cascade.headline}
            </div>
            <p
              className={cn(
                "max-w-[75ch] text-xs leading-5",
                cascade.scope === "near_full" ? "text-amber-200/85" : "text-white/50",
              )}
            >
              {cascade.detail}
            </p>
            <div className="flex flex-wrap gap-1">
              {plan?.canonical_phases.map((phase) => {
                const rebuilt = plan.regenerated_phases.includes(phase);
                const dropped = plan.excluded_affected_phases.includes(phase);
                const added = plan.auto_included_phases.includes(phase);
                return (
                  <span
                    key={phase}
                    className={cn(
                      "rounded-lg border px-2 py-1 text-[0.7rem]",
                      dropped
                        ? "border-white/[0.08] text-white/30 line-through"
                        : rebuilt
                          ? "border-white/20 bg-white/[0.1] text-white"
                          : "border-white/[0.1] bg-white/[0.03] text-white/45",
                    )}
                  >
                    {formatPhaseName(phase)}
                    {added && !dropped ? " · added" : ""}
                    {!rebuilt && !dropped ? " · copied" : ""}
                  </span>
                );
              })}
            </div>
          </>
        ) : (
          planStep.message && <p className="text-xs text-white/40">{planStep.message}</p>
        )}
      </Step>

      <Step
        index={5}
        title="Optionally drop an auto-included phase"
        hint="Dropping a downstream phase leaves its current text beside rebuilt upstream phases."
      >
        <div className="flex flex-wrap gap-1">
          {(plan?.auto_included_phases ?? []).map((phase) => (
            <Chip
              key={phase}
              active={state.excludedPhases.includes(phase)}
              onClick={() =>
                patch({
                  excludedPhases: toggle(state.excludedPhases, phase),
                  acknowledged: false,
                })
              }
            >
              {state.excludedPhases.includes(phase) ? "Excluded" : "Exclude"} ·{" "}
              {formatPhaseName(phase)}
            </Chip>
          ))}
          {(plan?.auto_included_phases ?? []).length === 0 && (
            <span className="text-xs text-white/40">
              No auto-included phase to drop for this selection.
            </span>
          )}
        </div>
        {(plan?.broken_dependency_edges.length ?? 0) > 0 && (
          <ul className="space-y-0.5 font-mono text-[0.66rem] text-amber-100/70">
            {plan?.broken_dependency_edges.map((edge) => (
              <li key={`${edge.upstream}->${edge.downstream}`}>
                {formatPhaseName(edge.downstream)} still reads the old{" "}
                {formatPhaseName(edge.upstream)}
              </li>
            ))}
          </ul>
        )}
        {(warning || needsAcknowledgement) && (
          <div className="space-y-2 rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3">
            <div className="flex items-start gap-2 text-xs leading-5 text-amber-100/90">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              <span className="max-w-[72ch]">
                {warning?.message ?? plan?.acknowledgement_message}
              </span>
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-amber-100/90">
              <input
                type="checkbox"
                className="size-4 accent-[#f5b544]"
                checked={state.acknowledged}
                onChange={() => patch({ acknowledged: !state.acknowledged })}
              />
              {warning?.acknowledgementLabel ??
                "I understand the published homework may be internally inconsistent."}
            </label>
          </div>
        )}
      </Step>

      <Step
        index={6}
        title="Re-run the source extract"
        hint="Off by default. Turning it on puts every content phase downstream of a brand-new extract, so the estimate below jumps to a near-full rebuild."
      >
        <label className="flex cursor-pointer items-center gap-3 text-sm text-white/75">
          <input
            type="checkbox"
            className="size-4 accent-[#7c5cff]"
            checked={state.refreshExtraction}
            onChange={() =>
              patch({
                refreshExtraction: !state.refreshExtraction,
                excludedPhases: [],
                acknowledged: false,
              })
            }
          />
          Re-extract the lesson from the textbook PDF
        </label>
      </Step>

      <Step
        index={7}
        title="Choose the model this campaign is frozen to"
        hint="Resolved once, when the campaign is created, and copied onto every revision. Regeneration runs over the api transport only."
      >
        {manifestError && <RegenerationProblem view={manifestError} />}
        <div className="flex flex-wrap gap-1">
          {apiProviders.map((provider) => (
            <Chip
              key={provider}
              active={state.provider === provider}
              onClick={() => patch({ provider, model: null })}
            >
              {provider}
            </Chip>
          ))}
        </div>
        <div className="flex flex-wrap gap-1">
          {models.map((model) => (
            <Chip key={model} active={state.model === model} onClick={() => patch({ model })}>
              {model}
            </Chip>
          ))}
          {/* An empty chip row has two very different causes, and only one of
              them is the operator's to fix. */}
          {models.length === 0 && !manifestError && (
            <span className="text-xs text-white/40">No model is offered for this provider.</span>
          )}
        </div>
      </Step>

      <Step index={8} title="Review the estimate">
        {estimateLoading && <p className="text-xs text-white/40">Pricing this draft…</p>}
        {estimateError && <RegenerationProblem view={estimateError} />}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="Lesson count" value={String(estimate?.target_count ?? targetCount)} />
          <Stat label="Rebuilt phases" value={String(totals?.regenerated_phase_count ?? 0)} />
          <Stat label="Copied phases" value={String(totals?.copied_phase_count ?? 0)} />
          <Stat label="Added by graph" value={String(plan?.auto_included_phases.length ?? 0)} />
          <Stat label="Excluded" value={String(plan?.excluded_affected_phases.length ?? 0)} />
          <Stat label="New extracts" value={String(totals?.regenerated_extract_count ?? 0)} />
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm text-white/70">
          <span className="inline-flex items-center gap-2">
            <CircleDollarSign className="size-4 text-white/45" />
            {totals
              ? `${formatUsd(totals.low_usd)} – ${formatUsd(totals.high_usd)}`
              : "no estimate yet"}
          </span>
          <span className="inline-flex items-center gap-2">
            <ListChecks className="size-4 text-white/45" />
            {chosen.length > 0
              ? chosen
                  .map((s) => `V${s.source_publication_version} → V${s.next_expected_version}`)
                  .filter((v, i, all) => all.indexOf(v) === i)
                  .join(", ")
              : "no lesson selected"}
          </span>
        </div>

        {totals && !totals.is_complete && totals.incomplete_reason && (
          <p className="max-w-[75ch] rounded-xl border border-rose-300/25 bg-rose-300/[0.07] p-3 text-xs leading-5 text-rose-100/90">
            {totals.unpriced_line_count} priced line
            {totals.unpriced_line_count === 1 ? " has" : "s have"} no rate —{" "}
            {totals.incomplete_reason}
          </p>
        )}
        {staticLines > 0 && (
          <p className="max-w-[75ch] text-xs leading-5 text-amber-100/75">
            {staticLines} of {totals?.line_items.length ?? 0} priced lines fall back to the static
            token envelope because there is no recent history for that phase and model, so their
            volume is a conservative assumption rather than a measurement.
          </p>
        )}
        {(totals?.zero_volume_history_notes.length ?? 0) > 0 && (
          <ul className="space-y-0.5 text-xs leading-5 text-amber-100/75">
            {regenerationKeyedLines(totals?.zero_volume_history_notes).map((row) => (
              <li key={row.key}>{row.text}</li>
            ))}
          </ul>
        )}
        {(totals?.notes.length ?? 0) > 0 && (
          <details className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2">
            <summary className="cursor-pointer text-xs text-white/50">
              How this estimate was built
            </summary>
            <ul className="mt-1 space-y-0.5 text-xs leading-5 text-white/45">
              {regenerationKeyedLines(totals?.notes).map((row) => (
                <li key={row.key}>{row.text}</li>
              ))}
            </ul>
          </details>
        )}
        {preflight && !preflight.ok && (
          <div className="space-y-1 rounded-xl border border-amber-300/25 bg-amber-300/[0.07] p-3 text-xs leading-5 text-amber-100/90">
            <div className="flex items-start gap-2 font-semibold">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              <span>
                {preflight.failure_count} lesson
                {preflight.failure_count === 1 ? "" : "s"} have nowhere to publish
              </span>
            </div>
            <ul className="space-y-0.5">
              {preflight.failures.map((f) => (
                <li key={`${f.toc_entry_id}:${f.output_language}`}>
                  {f.lesson_title} ({f.output_language}) — {f.detail}
                </li>
              ))}
            </ul>
            <p>
              You can still freeze this campaign, but the canary is refused until the Notion
              destination exists.
            </p>
          </div>
        )}

        <label className="flex items-center gap-3 text-xs text-white/55">
          Canary size
          <input
            type="number"
            min={1}
            max={Math.max(1, targetCount)}
            value={canarySize}
            onChange={(e) =>
              patch({ canarySize: clampCanarySize(Number(e.target.value), targetCount) })
            }
            className="w-20 rounded-lg border border-white/[0.1] bg-white/[0.05] px-2 py-1 text-sm text-white"
          />
          <span>
            {targetCount === 0
              ? "Pick lessons above to size the canary."
              : `${canarySize} of ${lessonCountLabel(targetCount)} run first and wait for your review.`}
          </span>
        </label>
        <p className="max-w-[75ch] text-xs leading-5 text-white/45">{REGENERATION_NO_SPEND_NOTE}</p>
      </Step>

      {createError && <RegenerationProblem view={createError} />}

      <div className={cn(CARD, "flex flex-wrap items-center justify-between gap-3")}>
        <p
          className={cn(
            "max-w-[60ch] text-xs leading-5",
            blockedReason ? "text-amber-200/85" : "text-emerald-200/80",
          )}
        >
          {blockedReason ??
            `Ready to freeze: ${lessonCountLabel(targetCount)}, ` +
              `${totals?.regenerated_phase_count ?? 0} rebuilt phases, ` +
              `${totals ? `${formatUsd(totals.low_usd)} – ${formatUsd(totals.high_usd)}` : "unpriced"}.`}
        </p>
        <button
          type="button"
          className={PRIMARY_BTN}
          disabled={blockedReason !== null || creating}
          onClick={onCreate}
        >
          <ListChecks className="size-4" />
          {creating ? "Freezing…" : REGENERATION_CREATE_LABEL}
        </button>
      </div>
    </div>
  );
}
