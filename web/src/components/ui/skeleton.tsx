import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-(--radius-md) bg-(--color-elevated) border border-(--color-border)",
        className,
      )}
      {...props}
    />
  );
}
