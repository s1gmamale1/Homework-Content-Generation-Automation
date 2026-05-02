import { Link } from "react-router-dom";

export function Nameplate() {
  return (
    <Link
      to="/"
      aria-label="Class Homework Builder — go to upload"
      className="inline-flex items-center gap-2.5 rounded-(--radius-sm) text-[0.95rem] font-semibold tracking-tight text-(--color-ink) transition-colors hover:text-(--color-accent) focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--color-accent)/70 focus-visible:ring-offset-2 focus-visible:ring-offset-(--color-canvas)"
    >
      <span
        aria-hidden
        className="grid size-7 place-items-center rounded-(--radius-sm) bg-(--color-accent) text-[oklch(0.18_0.04_55)] text-sm font-bold leading-none shadow-[0_2px_6px_oklch(0.78_0.13_75_/_28%)]"
      >
        e
      </span>
      <span className="leading-none">Class Homework Builder</span>
    </Link>
  );
}
