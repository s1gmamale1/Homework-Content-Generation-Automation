import { useEffect } from "react";
import { createPortal } from "react-dom";
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
  // Portal to <body> so the modal escapes the page subtree's stacking context
  // (otherwise the root nav at z-20 paints over it). Overlay covers the nav.
  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      {/* centered modal — stopPropagation so clicks inside don't close it */}
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "relative flex max-h-[85vh] w-[90vw] max-w-3xl flex-col",
          "rounded-2xl border border-white/[0.12] bg-[#0e0b1c]/95 shadow-[0_30px_80px_-30px_rgba(0,0,0,0.9)] backdrop-blur-xl",
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
    </div>,
    document.body,
  );
}
