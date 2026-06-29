import { useEffect } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export function MonitorDrawer({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title?: string;
  onClose: () => void;
  children?: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <>
      {/* dim overlay — click closes */}
      <div
        aria-hidden
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* right-side panel */}
      <div
        role="dialog"
        aria-modal="true"
        className={cn(
          "fixed right-0 top-0 z-50 flex h-full w-[28rem] max-w-[90vw] flex-col",
          "border-l border-white/[0.12] bg-[#0e0b1c]/95 shadow-2xl backdrop-blur-xl",
        )}
      >
        <div className="flex items-center justify-between gap-3 border-b border-white/[0.08] px-4 py-3">
          <span className="truncate text-sm font-semibold text-white">{title}</span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-white/50 hover:bg-white/[0.06] hover:text-white"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </>
  );
}
