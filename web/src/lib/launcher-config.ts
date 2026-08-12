/**
 * Per-book launcher selection persistence (launcher-persist-selections-1).
 * Pure localStorage helpers — no React. Keyed launcher-config:<book_id> so two
 * books keep independent configs. All access is try/catch-wrapped: private mode,
 * quota, or a corrupt blob degrade to a no-op / empty object, never throw.
 */
import type { JobKind, OutputLanguage, SessionLimitStrategy, Transport } from "./types";

export interface LauncherConfig {
  provider: string;
  transport: Transport;
  sessionLimitStrategy: SessionLimitStrategy;
  model: string | null;
  /** Output language override — undefined/null means inherit global default. */
  outputLanguage: OutputLanguage | null;
  /** Launch mode — "homework" (default) or "teacher_material". See `JobKind`. */
  launchMode: JobKind;
}

const keyFor = (bookId: string) => `launcher-config:${bookId}`;

/**
 * Returns the saved selections for a book as a Partial (so the caller merges
 * onto its own current defaults — forward-compatible when fields are added).
 * Returns {} when storage is unavailable, empty, or the blob is unparseable.
 */
export function loadLauncherConfig(bookId: string): Partial<LauncherConfig> {
  try {
    const raw = localStorage.getItem(keyFor(bookId));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") return parsed as Partial<LauncherConfig>;
    return {};
  } catch {
    return {};
  }
}

/** Persists the selection fields for a book. No-op on any storage error. */
export function saveLauncherConfig(bookId: string, cfg: LauncherConfig): void {
  try {
    localStorage.setItem(keyFor(bookId), JSON.stringify(cfg));
  } catch {
    /* unavailable / quota — ignore, persistence is best-effort */
  }
}
