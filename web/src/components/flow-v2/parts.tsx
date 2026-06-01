import { RichText } from "@/components/rich-text";
import { cn } from "@/lib/utils";
import { KeyRound } from "lucide-react";
import type { ReactNode } from "react";

/** A titled review card — the standard wrapper for one piece of phase content. */
export function ReviewCard({
  title,
  children,
  className,
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) p-4",
        className,
      )}
    >
      {title && (
        <h4 className="mb-2 font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
          {title}
        </h4>
      )}
      {children}
    </div>
  );
}

/** Answer key / teacher-only content — always visible (review tool) but flagged. */
export function AnswerKey({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2 flex gap-2 rounded-(--radius-sm) border border-(--color-accent-border) bg-(--color-accent-soft)/40 px-3 py-2 text-sm text-(--color-ink-soft)">
      <KeyRound className="mt-0.5 size-3.5 shrink-0 text-(--color-accent)" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** A "Label: value" row where the value may be markdown-ish. */
export function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <p className="my-1 text-sm leading-relaxed text-(--color-ink-soft)">
      <span className="font-mono text-[0.66rem] uppercase tracking-[0.14em] text-(--color-ink-muted)">
        {label}:{" "}
      </span>
      {typeof children === "string" ? <RichText inline>{children}</RichText> : children}
    </p>
  );
}
