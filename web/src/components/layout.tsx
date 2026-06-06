import { Gauge, Library, Moon, Plus } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Nameplate } from "./nameplate";
import { cn } from "@/lib/utils";

export function Layout() {
  const { pathname } = useLocation();
  const wide = pathname.startsWith("/usage");

  return (
    <div className="flex min-h-screen flex-col bg-(--color-canvas)">
      <header className="sticky top-3 z-20 px-3 sm:px-5">
        <div
          className={cn(
            "mx-auto flex h-14 w-full items-center justify-between gap-6 rounded-2xl border px-4 shadow-[0_18px_50px_-32px_rgba(0,0,0,0.75)] backdrop-blur-xl sm:px-5",
            wide
              ? "max-w-[1200px] border-white/[0.09] bg-white/[0.065]"
              : "max-w-[960px] border-(--color-border) bg-(--color-elevated)/90",
          )}
        >
          <div className="flex min-w-0 items-center gap-5">
            <Nameplate />
            <span
              aria-hidden
              className="hidden h-5 w-px bg-(--color-border) sm:block"
            />

            <nav aria-label="Primary" className="flex items-center gap-1">
              <NavItem to="/" end icon={<Plus className="size-4" />}>
                Upload
              </NavItem>
              <NavItem to="/library" icon={<Library className="size-4" />}>
                Library
              </NavItem>
              <NavItem to="/usage" icon={<Gauge className="size-4" />}>
                Usage
              </NavItem>
            </nav>
          </div>

          <div className="flex items-center gap-3">
            <a
              href="/docs"
              target="_blank"
              rel="noreferrer"
              className="hidden rounded-(--radius-sm) px-2 py-1 font-mono text-[0.7rem] font-medium uppercase tracking-[0.14em] text-(--color-ink-muted) transition-colors hover:text-(--color-ink) focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--color-accent)/60 sm:inline-block"
            >
              API
            </a>
            <span className="hidden font-mono text-[0.66rem] font-medium uppercase tracking-[0.14em] text-(--color-ink-muted) sm:inline">
              v0
            </span>
            <button
              type="button"
              aria-label="Dark theme"
              title="Dark theme"
              className="grid size-9 place-items-center rounded-full border border-white/[0.12] bg-white/[0.06] text-white/70 transition-colors hover:bg-white/[0.1] hover:text-white"
            >
              <Moon className="size-4" />
            </button>
          </div>
        </div>
      </header>

      <main
        className={cn(
          "mx-auto w-full flex-1 px-6 pb-24 pt-10 sm:px-8",
          wide ? "max-w-[1200px]" : "max-w-[720px]",
        )}
      >
        <Outlet />
      </main>

      <footer className="border-t border-(--color-border) py-4 text-center font-mono text-[0.64rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
        class-homework-builder · /api/v1
      </footer>
    </div>
  );
}

function NavItem({
  to,
  icon,
  children,
  end,
}: {
  to: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  end?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          "relative inline-flex h-9 items-center gap-2 rounded-xl px-3 text-sm font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--color-accent)/70 focus-visible:ring-offset-2 focus-visible:ring-offset-(--color-canvas)",
          isActive
            ? "bg-white/[0.11] text-(--color-ink)"
            : "text-(--color-ink-muted) hover:bg-white/[0.07] hover:text-(--color-ink)",
        )
      }
    >
      {({ isActive }) => (
        <>
          <span
            className={cn(
              "transition-colors",
              isActive ? "text-(--color-accent)" : "text-current",
            )}
          >
            {icon}
          </span>
          <span>{children}</span>
          {isActive && (
            <span
              aria-hidden
              className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-(--color-accent)"
            />
          )}
        </>
      )}
    </NavLink>
  );
}
