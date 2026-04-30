import { Library, Plus } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { Nameplate } from "./nameplate";
import { cn } from "@/lib/utils";

export function Layout() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 flex items-center justify-between gap-4 border-b border-(--color-border) bg-(--color-canvas)/85 px-6 py-3 backdrop-blur sm:px-8">
        <div className="flex items-center gap-6">
          <Nameplate />
          <nav className="flex items-center gap-1">
            <NavItem to="/library" icon={<Library className="size-3.5" />}>
              Library
            </NavItem>
            <NavItem to="/" icon={<Plus className="size-3.5" />} end>
              Upload
            </NavItem>
          </nav>
        </div>
        <span className="hidden font-mono text-[0.66rem] font-medium uppercase tracking-[0.14em] text-(--color-ink-muted) sm:inline">
          v0
        </span>
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
          "inline-flex items-center gap-1.5 rounded-(--radius-sm) px-2 py-1 text-[0.78rem] font-medium transition-colors",
          isActive
            ? "bg-(--color-elevated) text-(--color-ink)"
            : "text-(--color-ink-muted) hover:bg-(--color-elevated) hover:text-(--color-ink)",
        )
      }
    >
      {icon}
      {children}
    </NavLink>
  );
}
