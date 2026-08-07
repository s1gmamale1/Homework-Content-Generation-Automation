import type { PhaseOut } from "./types";

/** Warnings from the `extract` row — the ONLY place source-side checks land
 *  (`extract_coverage:` from the completeness check, `lint:coverage_thin` from
 *  the packet-vs-contract lint). The phase pager hides the extract row itself,
 *  so without this they render nowhere. */
export function sourceCheckWarnings(phases: PhaseOut[]): string[] {
  return (phases ?? [])
    .filter((p) => p.phase_name === "extract" && p.status === "done")
    .flatMap((p) => p.validation_warnings ?? []);
}

/** Every done phase's warnings, extract included. */
export function totalWarningCount(phases: PhaseOut[]): number {
  return (phases ?? [])
    .filter((p) => p.status === "done")
    .reduce((n, p) => n + (p.validation_warnings?.length ?? 0), 0);
}
