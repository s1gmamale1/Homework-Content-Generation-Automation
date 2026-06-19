import type { Subject } from "./types";

/**
 * Human-friendly display names for the internal subject slugs. The slugs
 * (e.g. "geometriya-g7-11", "kimyo-g7-11") are storage/routing keys — never
 * show them raw in the UI. Single source of truth so Library, Section,
 * Preview, and Upload all render the same label.
 */
export const SUBJECT_LABELS: Record<Subject, string> = {
  biology: "Biology",
  english: "English",
  "geometriya-g7-11": "Geometry",
  history: "History",
  "kimyo-g7-11": "Chemistry",
  "math-algebra": "Algebra",
  physics: "Physics",
  matematika: "Mathematics",
  "ona-tili": "Uzbek",
  adabiyot: "Literature",
  russian: "Russian",
  "oqish-savodxonligi": "Reading literacy",
  alifbe: "Alphabet",
  "tabiiy-fanlar": "Natural sciences",
  astronomiya: "Astronomy",
  geografiya: "Geography",
  informatika: "Informatics",
  "atrof-muhit": "Environmental studies",
  huquq: "Law",
  iqtisodiyot: "Economics",
  chizmachilik: "Technical drawing",
  musiqa: "Music",
  "tasviriy-sanat": "Fine arts",
  texnologiya: "Technology",
  tarbiya: "Upbringing",
  chqbt: "Pre-conscription training",
};

export function subjectLabel(subject: string): string {
  return SUBJECT_LABELS[subject as Subject] ?? subject;
}

export const VARIANT_LABELS: Record<string, string> = {
  jahon: "Jahon",
  ozbekiston: "O'zbekiston",
};

/** "History · O'zbekiston" when a history variant is present, else "History". */
export function subjectLabelWithVariant(
  subject: string,
  variant?: string | null,
): string {
  const base = subjectLabel(subject);
  const v = variant ? VARIANT_LABELS[variant] ?? variant : null;
  return v ? `${base} · ${v}` : base;
}

/**
 * Which single game phase each subject's pipeline runs. Mirror of the backend
 * registry (`app/services/subjects.py` → `flows.SUBJECT_GAME`). Each subject
 * runs exactly ONE of the four game variants; the picker greys out the rest.
 * Keep in sync with the backend when adding subjects.
 */
export const SUBJECT_GAME: Record<Subject, string> = {
  biology: "practice-memory-match",
  english: "practice-sentence",
  "geometriya-g7-11": "practice-jigsaw",
  history: "practice-memory-match",
  "kimyo-g7-11": "practice-tictactoe",
  "math-algebra": "practice-tictactoe",
  physics: "practice-tictactoe",
  matematika: "practice-tictactoe",
  "ona-tili": "practice-sentence",
  adabiyot: "practice-sentence",
  russian: "practice-sentence",
  "oqish-savodxonligi": "practice-sentence",
  alifbe: "practice-sentence",
  "tabiiy-fanlar": "practice-tictactoe",
  astronomiya: "practice-tictactoe",
  geografiya: "practice-memory-match",
  informatika: "practice-memory-match",
  "atrof-muhit": "practice-tictactoe",
  huquq: "practice-memory-match",
  iqtisodiyot: "practice-memory-match",
  chizmachilik: "practice-jigsaw",
  musiqa: "practice-memory-match",
  "tasviriy-sanat": "practice-memory-match",
  texnologiya: "practice-memory-match",
  tarbiya: "practice-memory-match",
  chqbt: "practice-memory-match",
};

/** The game phase a subject runs, or "" if unknown (then all games grey out). */
export function gameForSubject(subject: string): string {
  return SUBJECT_GAME[subject as Subject] ?? "";
}

/**
 * Per-subject signature gradient `[from, to]` — mirrors Usage's provider
 * accents. Single source of truth so Library cards and the Fleet launcher tint
 * a subject identically. Unknown subjects fall back to a neutral slate.
 */
export const SUBJECT_ACCENTS: Record<string, [string, string]> = {
  biology: ["#57e4a5", "#34d399"],
  english: ["#64a8ff", "#4d8dff"],
  "geometriya-g7-11": ["#c18cff", "#8268ff"],
  history: ["#f6d365", "#fda085"],
  "kimyo-g7-11": ["#4ee8d5", "#43c6ac"],
  "math-algebra": ["#ff9466", "#ff5f7f"],
  physics: ["#7c8cff", "#5fb0ff"],
};

export function accentOf(subject: string): [string, string] {
  return SUBJECT_ACCENTS[subject] ?? ["#8aa0c6", "#5f6f93"];
}

/** All content phases that exist in `prompts/_general/` (extract is the
 *  always-on head, not a selectable phase). Listed in canonical flow order.
 *  `game: true` marks the four mutually-exclusive game variants — a subject
 *  runs exactly one (see SUBJECT_GAME); the picker greys out the others.
 *  `icon` + `blurb` are display-only, to make the picker self-explanatory. */
export interface PhaseMeta {
  key: string;
  label: string;
  icon: string;
  blurb: string;
  game?: boolean;
}

export const CONTENT_PHASES: PhaseMeta[] = [
  { key: "case-based-preview", label: "Preview", icon: "🎬", blurb: "Story-style case that sets up the lesson" },
  { key: "flashcards", label: "Flashcards", icon: "🃏", blurb: "Key terms & facts to memorize" },
  { key: "memory-check", label: "Memory sprint", icon: "⏱️", blurb: "Quick timed recall drill" },
  { key: "practice-rlc", label: "Real-life practice", icon: "🌍", blurb: "Apply the idea to everyday situations" },
  { key: "practice-error-detection", label: "Spot the mistake", icon: "🔍", blurb: "Find and fix the deliberate error" },
  { key: "practice-memory-match", label: "Memory match", icon: "🧠", blurb: "Game: match the pairs from memory", game: true },
  { key: "practice-tictactoe", label: "Tic-tac-toe", icon: "⭕", blurb: "Game: apply the concept to win the board", game: true },
  { key: "practice-jigsaw", label: "Jigsaw", icon: "🧩", blurb: "Game: reassemble the pieces in order", game: true },
  { key: "practice-sentence", label: "Sentence builder", icon: "✍️", blurb: "Game: build correct sentences", game: true },
  { key: "boss-arena", label: "Boss fight", icon: "🏆", blurb: "Final mixed-skills quiz" },
  { key: "reflection", label: "Reflection", icon: "💭", blurb: "Wrap-up: what did you learn?" },
];
