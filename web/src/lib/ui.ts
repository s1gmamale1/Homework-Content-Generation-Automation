/**
 * Shared Tailwind class strings for the dark "space dashboard" theme
 * (Library, Usage, Section, Book, Job, Preview, Upload, Login). Single
 * source of truth so every page's glass cards, buttons, and form fields
 * stay visually identical. Compose with `cn(CARD, "extra-classes")`.
 */

export const CARD =
  "rounded-2xl border border-white/[0.09] bg-white/[0.04] p-5 shadow-[0_18px_50px_-36px_rgba(0,0,0,0.95)] backdrop-blur-xl";

export const PRIMARY_BTN =
  "inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#7c5cff] to-[#4d8dff] px-4 py-2.5 text-sm font-medium text-white shadow-[0_10px_26px_-12px_rgba(99,102,241,0.9)] transition-transform hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0";

export const GLASS_BTN =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-white/[0.12] bg-white/[0.05] px-4 py-2.5 text-sm font-medium text-white/80 transition-colors hover:bg-white/[0.1] hover:text-white disabled:opacity-50";

export const GHOST_BTN =
  "inline-flex items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm font-medium text-white/55 transition-colors hover:text-white disabled:opacity-50";

export const INPUT_GLASS =
  "border-white/[0.12] bg-black/25 text-white placeholder:text-white/35";

export const SELECT_TRIGGER =
  "h-10 border-white/[0.12] bg-black/25 text-white data-[placeholder]:text-white/40";

/** Clear bordered back-nav pill with a hover arrow nudge (apply to a Link). */
export const BACK_PILL =
  "group inline-flex items-center gap-2 rounded-xl border border-white/[0.12] bg-white/[0.05] px-3 py-2 text-sm font-medium text-white/75 transition-colors hover:bg-white/[0.1] hover:text-white";
