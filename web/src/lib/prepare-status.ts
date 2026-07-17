import type { AvailableLanguages, BookLinkState, LangPart, NotionCandidate } from "./types";

/** The six chip states the "Prepare a subject" dialog can show for a
 *  resolved language part (worklog 0144 task 5). */
export type PrepareChipKind =
  | "no_textbook"
  | "textbook_ready"
  | "prepared"
  | "preparing"
  | "needs_review"
  | "failed";

export interface PrepareChip {
  kind: PrepareChipKind;
  label: string;
  /** Tailwind color family the consumer maps to border/bg/text classes —
   *  kept as a bare token here so this module stays framework-free. */
  colorFamily: "amber" | "emerald" | "blue" | "red";
  pulse: boolean;
}

export interface RedoAction {
  enabled: boolean;
  disabledReason: string | null;
}

export type PreparePanelProps =
  | { kind: "no_textbook" }
  | { kind: "textbook_ready" }
  | {
      kind: "prepared";
      bookId: string;
      lessons: number;
      preparedAt: string | null;
      redo: RedoAction;
    }
  | { kind: "preparing"; bookId: string }
  | { kind: "needs_review"; bookId: string }
  | { kind: "failed"; bookId: string };

export interface PrepareActions {
  /** Non-mutating: focus the existing book (the same place a fresh prepare
   *  would land) — `prepared` only. */
  useExisting: boolean;
  /** Re-extract the TOC from the PDF, replacing current rows — `prepared`
   *  only, and only when nothing blocks it (`redo_blocked_by_jobs === 0`). */
  redo: boolean;
  /** Deep-link to the existing TOC review surface — `needs_review` only. */
  review: boolean;
  /** Re-run TOC extraction after a failure (same endpoint as `redo`) —
   *  `failed` only. */
  retry: boolean;
  /** Proceed with the normal (unprepared) prepare flow — true only for
   *  `textbook_ready`; every linked state disables it so a click can't
   *  double-fire a fetch on an already-tracked book. */
  proceed: boolean;
}

export interface PrepareStatus {
  chip: PrepareChip;
  panel: PreparePanelProps;
  actions: PrepareActions;
}

const NO_ACTIONS: PrepareActions = {
  useExisting: false, redo: false, review: false, retry: false, proceed: false,
};

// Matches app/api/v1/books.py's `toc_retry_blocked_by_jobs` 409 `message`
// field verbatim (worklog 0144 task 6 rider) — an operator sees the exact
// same wording whether the block surfaces from this synthesized status or
// from a race-condition 409 returned by the retry call itself.
function redoBlockedReason(count: number): string {
  return `${count} homework job(s) reference this book's sections — delete the affected sections first`;
}

/** Shared linked/unlinked resolution for anything carrying `BookLinkState`
 *  fields — a `LangPart`'s part-level rollup, or a single `NotionCandidate`.
 *  Both `partPrepareStatus` and `candidatePrepareStatus` funnel into this
 *  once they've handled their own "is there a textbook at all" check (a part
 *  can be `has_textbook: false`; a candidate IS a textbook file by
 *  construction, so it never needs that check). */
function linkStatus(link: BookLinkState): PrepareStatus {
  const bookId = link.book_id ?? null;
  const status = link.book_status ?? null;
  // Unlinked (no book at all), OR the two-linked-candidates edge case where
  // the backend deliberately omits the part-level rollup (ambiguous which of
  // two books this part now represents) — conservative fallback, never
  // claims a specific book. Per-candidate detail is a different surface.
  if (!bookId || !status) {
    return {
      chip: { kind: "textbook_ready", label: "TEXTBOOK READY", colorFamily: "emerald", pulse: false },
      panel: { kind: "textbook_ready" },
      actions: { ...NO_ACTIONS, proceed: true },
    };
  }

  switch (status) {
    case "toc_ready": {
      const blocked = link.redo_blocked_by_jobs ?? 0;
      const lessons = link.toc_total ?? 0;
      const redo: RedoAction = {
        enabled: blocked === 0,
        disabledReason: blocked > 0 ? redoBlockedReason(blocked) : null,
      };
      return {
        chip: {
          kind: "prepared",
          label: `PREPARED · ${lessons} lessons`,
          colorFamily: "emerald",
          pulse: false,
        },
        panel: {
          kind: "prepared",
          bookId,
          lessons,
          preparedAt: link.toc_ready_at ?? null,
          redo,
        },
        actions: { ...NO_ACTIONS, useExisting: true, redo: redo.enabled },
      };
    }
    case "toc_extracting":
    case "uploading":
      return {
        chip: { kind: "preparing", label: "PREPARING", colorFamily: "blue", pulse: true },
        panel: { kind: "preparing", bookId },
        actions: NO_ACTIONS,
      };
    case "toc_review":
      return {
        chip: { kind: "needs_review", label: "NEEDS REVIEW", colorFamily: "amber", pulse: false },
        panel: { kind: "needs_review", bookId },
        actions: { ...NO_ACTIONS, review: true },
      };
    case "failed":
      return {
        chip: { kind: "failed", label: "FAILED", colorFamily: "red", pulse: false },
        panel: { kind: "failed", bookId },
        actions: { ...NO_ACTIONS, retry: true },
      };
    default: {
      // Exhaustiveness guard: BookStatus growing a new member should fail
      // typecheck here rather than silently mis-rendering a chip.
      const _exhaustive: never = status;
      return _exhaustive;
    }
  }
}

/** Map an availability-enriched part (post `partForResolution`) to the
 *  Prepare dialog's chip + panel + enabled-actions state. Pure — no
 *  fetches, no React — so it's exercised with plain node:assert tests. */
export function partPrepareStatus(part: LangPart | null | undefined): PrepareStatus {
  if (!part || !part.has_textbook) {
    return {
      chip: { kind: "no_textbook", label: "NO TEXTBOOK", colorFamily: "amber", pulse: false },
      panel: { kind: "no_textbook" },
      actions: NO_ACTIONS,
    };
  }
  return linkStatus(part);
}

/** Map a single file-level candidate (BE-19 task 6) to the same chip/panel/
 *  actions shape as `partPrepareStatus` — a candidate has no `has_textbook`
 *  flag of its own (it IS a textbook file by construction, being one of a
 *  part's ranked PDF candidates), so it skips straight to `linkStatus`.
 *  Used when the ambiguous-file picker has an explicit selection: that
 *  candidate's OWN link state governs the panel + the primary Prepare gate,
 *  not the part-level rollup (which the backend omits whenever >1 candidate
 *  resolves to different books — prepare-two-linked-part-redo-1). */
export function candidatePrepareStatus(candidate: NotionCandidate | null | undefined): PrepareStatus {
  if (!candidate) {
    return {
      chip: { kind: "no_textbook", label: "NO TEXTBOOK", colorFamily: "amber", pulse: false },
      panel: { kind: "no_textbook" },
      actions: NO_ACTIONS,
    };
  }
  return linkStatus(candidate);
}

/** The effective status the primary Prepare button + panel must reflect for
 *  a resolved (part, selection) pair: an explicitly SELECTED candidate from
 *  the ambiguous-file picker governs over the part-level rollup (finding 3
 *  of the PR #99 gate) — `null`/absent selection falls back to the part's
 *  own status (the conservative `textbook_ready` fallback for an
 *  as-yet-unresolved two-linked part, documented gap
 *  `prepare-two-linked-part-redo-1`). */
export function resolvedPrepareStatus(
  part: LangPart | null | undefined,
  selectedCandidate: NotionCandidate | null | undefined,
): PrepareStatus {
  return selectedCandidate ? candidatePrepareStatus(selectedCandidate) : partPrepareStatus(part);
}

/** Human tooltip explaining why the PRIMARY prepare action is disabled by
 *  system state (as opposed to disabled for some other reason, e.g. no
 *  subject picked yet). `undefined` when nothing blocks it — `proceed: true`,
 *  or a `no_textbook` chip (callers already gate that case on their own
 *  "usable"/"available" check, so it never needs a system-state tooltip). */
export function proceedBlockedTooltip(status: PrepareStatus): string | undefined {
  if (status.actions.proceed) return undefined;
  switch (status.chip.kind) {
    case "prepared":
      return "Already prepared — use the panel above to open it or redo extraction";
    case "preparing":
      return "Preparation in progress";
    case "needs_review":
      return "Needs review — open it from the panel";
    case "failed":
      return "Preparation failed — retry from the panel";
    default:
      return undefined;
  }
}

/** True when the availability tree has any linked book still mid-flight
 *  (`toc_extracting`/`uploading`) — the poll gate for the Prepare dialog:
 *  keep refetching availability while it's open AND something in it could
 *  still change (mirrors `BatchLessonList`'s `enabled`-gated refetchInterval). */
export function hasMidFlightBook(languages: AvailableLanguages | null | undefined): boolean {
  if (!languages) return false;
  for (const langMap of Object.values(languages)) {
    for (const avail of Object.values(langMap)) {
      for (const part of avail.parts ?? []) {
        if (part.book_status === "toc_extracting" || part.book_status === "uploading") {
          return true;
        }
      }
    }
  }
  return false;
}
