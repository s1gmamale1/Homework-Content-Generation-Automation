import { cn } from "@/lib/utils";

interface EyebrowProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
}

export function Eyebrow({ className, children, ...props }: EyebrowProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border border-[oklch(0.79_0.13_70_/_25%)] bg-[oklch(0.79_0.13_70_/_14%)] px-3.5 py-1.5 font-mono text-[0.7rem] font-medium uppercase tracking-[0.18em] text-(--color-amber) backdrop-blur-md",
        className,
      )}
      {...props}
    >
      <span className="size-1.5 rounded-full bg-(--color-amber) shadow-[0_0_10px_oklch(0.79_0.13_70_/_60%)] animate-pulse" />
      {children}
    </span>
  );
}
