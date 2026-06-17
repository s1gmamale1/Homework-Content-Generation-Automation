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
  manaviyat: "Spirituality",
  odobnoma: "Ethics",
  tarbiya: "Upbringing",
  "kelajak-soati": "Future hour",
  chqbt: "Pre-conscription training",
  "tasviriy-sanat": "Fine arts",
  musiqa: "Music",
  texnologiya: "Technology",
  chizmachilik: "Technical drawing",
  "jismoniy-tarbiya": "Physical education",
};

export function subjectLabel(subject: string): string {
  return SUBJECT_LABELS[subject as Subject] ?? subject;
}
