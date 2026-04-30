import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-(--radius-md) bg-gradient-to-r from-(--color-surface) via-(--color-surface-hover) to-(--color-surface)",
        className,
      )}
      {...props}
    />
  );
}
