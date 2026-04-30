import { Link } from "react-router-dom";

export function Nameplate() {
  return (
    <Link
      to="/"
      className="inline-flex items-center gap-2.5 text-sm font-semibold tracking-tight text-(--color-ink) transition-colors hover:text-(--color-accent)"
    >
      <span className="grid size-6 place-items-center rounded-(--radius-sm) bg-(--color-accent) text-[oklch(0.18_0.04_55)] text-xs font-bold leading-none">
        e
      </span>
      <span>Edu-Homework</span>
    </Link>
  );
}
