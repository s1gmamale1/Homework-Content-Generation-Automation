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
