import { Library, Plus } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { Nameplate } from "./nameplate";
import { cn } from "@/lib/utils";

export function Layout() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-(--color-border) bg-(--color-canvas)/90 backdrop-blur-md">
        <div className="mx-auto flex h-14 w-full max-w-[960px] items-center justify-between gap-6 px-5 sm:px-8">
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
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[720px] flex-1 px-6 pb-24 pt-12 sm:px-8">
        <Outlet />
      </main>

      <footer className="border-t border-(--color-border) py-4 text-center font-mono text-[0.64rem] uppercase tracking-[0.16em] text-(--color-ink-muted)">
        edu-homework · /api/v1
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
          "relative inline-flex h-9 items-center gap-2 rounded-(--radius-sm) px-3 text-sm font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--color-accent)/70 focus-visible:ring-offset-2 focus-visible:ring-offset-(--color-canvas)",
          isActive
            ? "text-(--color-ink) bg-(--color-elevated)"
            : "text-(--color-ink-muted) hover:text-(--color-ink) hover:bg-(--color-elevated)/60",
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
