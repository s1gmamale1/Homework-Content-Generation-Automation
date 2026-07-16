import type { AvailableLanguages, LangPart } from "./types";

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

function redoBlockedReason(count: number): string {
  return `${count} job(s) reference this TOC — delete affected sections first`;
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

  const bookId = part.book_id ?? null;
  const status = part.book_status ?? null;
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
      const blocked = part.redo_blocked_by_jobs ?? 0;
      const lessons = part.toc_total ?? 0;
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
          preparedAt: part.toc_ready_at ?? null,
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
