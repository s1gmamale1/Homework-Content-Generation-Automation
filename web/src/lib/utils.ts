import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export function formatPhaseName(s: string): string {
  return s.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatPages(start?: number | null, end?: number | null): string {
  if (!start) return "";
  if (!end || end === start) return `p. ${start}`;
  return `p. ${start}–${end}`;
}

// Curriculum metadata tags (Bloom/PISA/Damage/Difficulty) belong in schema
// fields, not in student-facing strings. Older jobs and occasional model
// slips put them inline; strip them at render time.
const META_TAG_RE = /\[\s*(Bloom|PISA|Damage|Difficulty|HP)\s*[:|][^\]]*\]/gi;

export function stripCurriculumTags(text: string): string {
  if (!text) return text;
  return text
    .replace(META_TAG_RE, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}
