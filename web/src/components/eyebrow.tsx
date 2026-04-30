import { cn } from "@/lib/utils";

interface EyebrowProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
}

export function Eyebrow({ className, children, ...props }: EyebrowProps) {
  return (
    <span
      className={cn(
        "font-mono text-[0.68rem] font-medium uppercase tracking-[0.16em] text-(--color-ink-muted)",
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}
