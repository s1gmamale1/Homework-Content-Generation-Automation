/**
 * Shared building blocks for the teacher-deck slide viewer
 * (`web/src/routes/deck.tsx` + `web/src/components/deck/*-slide.tsx`).
 *
 * The template PDF (18-slide "QOLLANMA" deck) alternates two treatments:
 *  - LIGHT slides ("O'qituvchi uchun" reference pages — passport, objectives,
 *    lesson map, teacher-only stages, answer key, rubric): a near-white card,
 *    a colored kicker label top-left, and a neutral "O'QITUVCHI UCHUN" pill
 *    top-right.
 *  - DARK slides (cover, core idea, "ekranga" — projected-to-screen — stages,
 *    quiz, pair-work, conclusion): a navy card, colored kicker label, and
 *    (except cover/core-idea) a solid colored "EKRANGA" pill top-right.
 *
 * Both share one accent color per slide (blue/orange/green) that mirrors the
 * template's per-section palette. `ACCENT_HEX` is the single source of truth.
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export type Accent = "blue" | "orange" | "green";

export const ACCENT_HEX: Record<Accent, string> = {
  blue: "#4d8dff",
  orange: "#f0a83c",
  green: "#34d399",
};

/** Slide "canvas" — light (paper) treatment. Matches min-height of the pager
 *  card in `routes/preview.tsx` so slide-to-slide transitions don't jump. */
export function LightSlide({
  accent,
  children,
  className,
}: {
  accent: Accent;
  children: ReactNode;
  className?: string;
}) {
  const hex = ACCENT_HEX[accent];
  return (
    <div
      className={cn(
        "relative flex min-h-[60vh] flex-col overflow-hidden rounded-2xl border border-black/[0.06] bg-[#f8fafc] p-8 text-slate-900 shadow-[0_18px_50px_-36px_rgba(0,0,0,0.5)] sm:p-10",
        className,
      )}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-1.5 rounded-t-2xl"
        style={{ background: hex }}
      />
      {children}
    </div>
  );
}

/** Slide "canvas" — dark (projector) treatment. */
export function DarkSlide({
  accent,
  children,
  className,
}: {
  accent: Accent;
  children: ReactNode;
  className?: string;
}) {
  const hex = ACCENT_HEX[accent];
  return (
    <div
      className={cn(
        "relative flex min-h-[60vh] flex-col overflow-hidden rounded-2xl border border-white/[0.08] bg-[#0d1120] p-8 pl-10 text-white shadow-[0_18px_50px_-36px_rgba(0,0,0,0.9)] sm:p-10 sm:pl-12",
        className,
      )}
      style={{ "--accent": hex } as React.CSSProperties}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-y-0 left-0 w-1.5 rounded-l-2xl"
        style={{ background: hex }}
      />
      {children}
    </div>
  );
}

/** Top kicker row: small uppercase accent-colored label (left) + an optional
 *  pill (right) — either the neutral "teacher-only" pill or the solid
 *  colored "ekranga" (on-screen) pill. `badge="none"` renders no pill (cover
 *  / core-idea slides in the template have none). */
export function SlideKicker({
  label,
  accent,
  badge,
  dark,
}: {
  label: string;
  accent: Accent;
  badge: "teacher_only" | "ekranga" | "none";
  dark?: boolean;
}) {
  const hex = ACCENT_HEX[accent];
  return (
    <div className="mb-5 flex items-center justify-between gap-3">
      <span
        className="font-mono text-[0.68rem] font-bold uppercase tracking-[0.2em]"
        style={{ color: hex }}
      >
        {label}
      </span>
      {badge === "teacher_only" && (
        <span
          className={cn(
            "shrink-0 rounded-full px-3 py-1 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.14em]",
            dark
              ? "bg-white/10 text-white/60"
              : "bg-slate-900/[0.06] text-slate-500",
          )}
        >
          O'qituvchi uchun
        </span>
      )}
      {badge === "ekranga" && (
        <span
          className="shrink-0 rounded-full px-3 py-1 font-mono text-[0.62rem] font-bold uppercase tracking-[0.14em] text-white"
          style={{ background: hex }}
        >
          Ekranga
        </span>
      )}
    </div>
  );
}

/** A single numbered row: filled accent circle + bold title + muted detail.
 *  Used for objectives, stage teaching points, pair-work tasks, and the
 *  conclusion's reflection questions. */
export function NumberedPoint({
  n,
  title,
  detail,
  accent,
  dark,
}: {
  n: number;
  title: ReactNode;
  detail?: ReactNode;
  accent: Accent;
  dark?: boolean;
}) {
  const hex = ACCENT_HEX[accent];
  return (
    <div
      className={cn(
        "flex items-start gap-4 rounded-xl border-l-4 px-4 py-3.5",
        dark ? "bg-white/[0.04]" : "bg-slate-900/[0.035]",
      )}
      style={{ borderLeftColor: hex }}
    >
      <span
        className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full text-xs font-bold text-white"
        style={{ background: hex }}
      >
        {n}
      </span>
      <div className="min-w-0">
        <p className={cn("font-semibold leading-snug", dark ? "text-white" : "text-slate-900")}>
          {title}
        </p>
        {detail && (
          <p className={cn("mt-1 text-sm leading-relaxed", dark ? "text-white/60" : "text-slate-500")}>
            {detail}
          </p>
        )}
      </div>
    </div>
  );
}

/** The two-column "teacher does / student does" box pair. Used on every
 *  `teacher_only` StageSlide (bottom-pinned, full-size — the original
 *  template layout) and, in `compact`+`dark` form, as the facilitation note
 *  strip beneath an `ekranga` StageSlide's screen text — those stages carry
 *  `teacher_action`/`student_action` too and previously lost it entirely. */
export function TeacherStudentBoxes({
  teacherAction,
  studentAction,
  accent,
  dark,
  compact,
}: {
  teacherAction: string;
  studentAction: string;
  accent: Accent;
  dark?: boolean;
  compact?: boolean;
}) {
  const hex = ACCENT_HEX[accent];
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-4 sm:grid-cols-2",
        compact ? "mt-6" : "mt-auto pt-6",
      )}
    >
      <div
        className={cn(
          "rounded-xl",
          dark ? "border border-white/10 bg-white/[0.03]" : "border border-slate-900/[0.08]",
          compact ? "p-3.5" : "p-4",
        )}
      >
        <p
          className="mb-1.5 font-mono text-[0.64rem] font-bold uppercase tracking-[0.14em]"
          style={{ color: hex }}
        >
          O'qituvchi nima qiladi
        </p>
        <p className={cn("text-sm leading-relaxed", dark ? "text-white/70" : "text-slate-700")}>
          {teacherAction}
        </p>
      </div>
      <div
        className={cn(
          "rounded-xl",
          dark ? "border border-white/10 bg-white/[0.03]" : "border border-slate-900/[0.08]",
          compact ? "p-3.5" : "p-4",
        )}
      >
        <p
          className={cn(
            "mb-1.5 font-mono text-[0.64rem] font-bold uppercase tracking-[0.14em]",
            dark ? "text-white/40" : "text-slate-400",
          )}
        >
          O'quvchi nima qiladi
        </p>
        <p className={cn("text-sm leading-relaxed", dark ? "text-white/70" : "text-slate-700")}>
          {studentAction}
        </p>
      </div>
    </div>
  );
}

/** Kicker text for a stage-driven slide: "N-BOSQICH · M DAQIQA". */
export function stageKickerLabel(index: number, minutes: number): string {
  return `${index}-bosqich · ${minutes} daqiqa`;
}

/** Short accent underline below a slide's H1 (light slides only — the dark
 *  slides use the left accent bar instead). */
export function TitleUnderline({ accent }: { accent: Accent }) {
  return (
    <span
      aria-hidden
      className="mb-6 mt-4 block h-1 w-14 rounded-full"
      style={{ background: ACCENT_HEX[accent] }}
    />
  );
}

/** Slide H1 — consistent sizing across every slide type. */
export function SlideTitle({ children, dark }: { children: ReactNode; dark?: boolean }) {
  return (
    <h1
      className={cn(
        "text-2xl font-bold leading-tight tracking-tight sm:text-3xl",
        dark ? "text-white" : "text-slate-900",
      )}
    >
      {children}
    </h1>
  );
}
