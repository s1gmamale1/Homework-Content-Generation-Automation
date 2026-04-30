import { Outlet } from "react-router-dom";
import { Aurora } from "./aurora";
import { Nameplate } from "./nameplate";

export function Layout() {
  return (
    <div className="relative flex min-h-screen flex-col">
      <Aurora />

      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-(--color-border-subtle) bg-(--color-canvas)/55 px-6 py-4 backdrop-blur-xl backdrop-saturate-150 sm:px-8">
        <Nameplate />
        <span className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.18em] text-(--color-ink-muted) hidden sm:inline">
          v0 · /api/v1
        </span>
      </header>

      <main className="relative z-10 mx-auto w-full max-w-[760px] flex-1 px-6 pb-28 pt-20 sm:px-8">
        <Outlet />
      </main>

      <footer className="relative z-10 flex items-center justify-center gap-3 border-t border-(--color-border-subtle) bg-(--color-canvas)/45 py-5 text-center font-mono text-[0.62rem] uppercase tracking-[0.2em] text-(--color-ink-muted) backdrop-blur-md">
        <span className="h-px w-6 bg-[linear-gradient(to_right,transparent,var(--color-border-hover),transparent)]" />
        typeset by gemini
        <span className="h-px w-6 bg-[linear-gradient(to_right,transparent,var(--color-border-hover),transparent)]" />
      </footer>
    </div>
  );
}
