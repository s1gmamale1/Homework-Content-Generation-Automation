import { Link } from "react-router-dom";

export function Nameplate() {
  return (
    <Link
      to="/"
      className="group inline-flex items-center gap-2.5 text-(--color-ink) text-sm font-medium tracking-tight transition-colors hover:text-(--color-amber)"
    >
      <span className="relative grid size-7 place-items-center overflow-hidden rounded-[9px] bg-[linear-gradient(135deg,var(--color-amber)_0%,var(--color-rust)_100%)] text-[oklch(0.18_0.04_55)] shadow-[0_4px_14px_oklch(0.79_0.13_70_/_45%),inset_0_1px_0_oklch(0.99_0.005_80_/_25%)]">
        <span className="font-display text-[1.05rem] italic leading-none pb-0.5">e</span>
        <span className="absolute inset-0 -translate-x-full bg-[linear-gradient(135deg,transparent_40%,oklch(0.99_0.005_80_/_30%)_50%,transparent_60%)] transition-transform duration-700 ease-out group-hover:translate-x-full" />
      </span>
      <span>Edu-Homework</span>
    </Link>
  );
}
