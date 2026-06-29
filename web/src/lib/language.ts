/**
 * Pure language-label and styling helpers for OutputLanguage.
 * No React imports — safe to use in any context (components, tests, utils).
 */
import type { OutputLanguage } from "./types";

/** Human-readable label for each supported output language. */
export const LANG_LABEL: Record<OutputLanguage, string> = {
  uz: "O‘zbek",
  ru: "Русский",
  en: "English",
};

/**
 * Returns a Tailwind chip className string for a language badge.
 * Mirrors the Badge `neutral` variant from components/ui/badge.tsx:
 * rounded-(--radius-sm), font-mono, small tracking, border + elevated bg.
 */
export function langBadge(lang: OutputLanguage): string {
  const accent = langAccent(lang);
  return [
    "inline-flex items-center rounded-(--radius-sm)",
    "border font-mono text-[0.65rem] font-medium uppercase tracking-[0.14em]",
    "px-2 py-1",
    accent,
  ].join(" ");
}

/**
 * Returns a short Tailwind accent-color class set (border + bg + text) per language,
 * using distinct hues consistent with the Sigma dark-glass design system.
 *
 * uz → teal/green  (–color-success palette)
 * ru → blue/indigo (–color-accent palette)
 * en → amber/orange (warm neutral)
 */
export function langAccent(lang: OutputLanguage): string {
  switch (lang) {
    case "uz":
      // teal-green — mirrors Badge `success` variant
      return "border-[oklch(0.78_0.10_145_/_30%)] bg-[oklch(0.78_0.10_145_/_10%)] text-(--color-success)";
    case "ru":
      // indigo/blue — mirrors Badge `accent` variant
      return "border-(--color-accent-border) bg-(--color-accent-soft) text-(--color-accent)";
    case "en":
      // amber — distinct warm tone, no collision with teal or indigo
      return "border-[oklch(0.82_0.12_75_/_30%)] bg-[oklch(0.82_0.12_75_/_10%)] text-[oklch(0.82_0.12_75)]";
  }
}
