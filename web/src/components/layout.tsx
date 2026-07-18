import { Activity, Gauge, Library, LayoutDashboard, Rocket, Settings } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { NavLink, useLocation, useOutlet } from "react-router-dom";
import { Nameplate } from "./nameplate";
import { cn } from "@/lib/utils";

export function Layout() {
  const { pathname } = useLocation();
  const reduce = useReducedMotion();
  // Snapshot the matched route element. Rendering this frozen element (rather
  // than <Outlet/>, which re-reads context to the *current* route) lets the
  // exiting copy keep the OLD page during its cross-fade — so each route
  // mounts exactly once and SSE/query effects don't double-subscribe.
  const outlet = useOutlet();
  const wide =
    pathname === "/" ||
    pathname.startsWith("/usage") ||
    pathname.startsWith("/library") ||
    pathname.startsWith("/monitor") ||
    pathname.startsWith("/dashboard");

  return (
    <div className="flex min-h-screen flex-col bg-(--color-canvas)">
      <header className="sticky top-3 z-20 px-3 sm:px-5">
        {/* Navbar width is CONSTANT (max-w-[1200px]) on every route so the
            centered bar never shifts when content width changes. The `wide`
            toggle below applies only to <main> content, not this chrome. */}
        <div className="mx-auto flex h-14 w-full max-w-[1200px] items-center justify-between gap-6 rounded-2xl border border-white/[0.09] bg-white/[0.065] px-4 shadow-[0_18px_50px_-32px_rgba(0,0,0,0.75)] backdrop-blur-xl sm:px-5">
          <div className="flex min-w-0 items-center gap-5">
            <Nameplate />
            <span
              aria-hidden
              className="hidden h-5 w-px bg-(--color-border) sm:block"
            />

            <nav aria-label="Primary" className="flex items-center gap-1">
              <NavItem to="/" end icon={<Rocket className="size-4" />}>
                Fleet
              </NavItem>
              <NavItem to="/monitor" icon={<Activity className="size-4" />}>
                Monitor
              </NavItem>
              <NavItem to="/dashboard" icon={<LayoutDashboard className="size-4" />}>
                Dashboard
              </NavItem>
              <NavItem to="/library" icon={<Library className="size-4" />}>
                Library
              </NavItem>
              <NavItem to="/usage" icon={<Gauge className="size-4" />}>
                Usage
              </NavItem>
              <NavItem to="/settings" icon={<Settings className="size-4" />}>
                Settings
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

      <main
        className={cn(
          "mx-auto w-full flex-1 px-6 pb-24 pt-10 sm:px-8",
          wide ? "max-w-[1200px]" : "max-w-[720px]",
        )}
      >
        {/* Cross-fade between routes. Opacity only — a transform here would
            create a containing block and break the fixed SpaceBackdrop. */}
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={pathname}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduce ? 0 : 0.18, ease: "easeOut" }}
          >
            {outlet}
          </motion.div>
        </AnimatePresence>
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
          "relative inline-flex h-9 items-center gap-2 rounded-xl px-3 text-sm font-medium transition-[color,background-color,transform] active:scale-95",
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
