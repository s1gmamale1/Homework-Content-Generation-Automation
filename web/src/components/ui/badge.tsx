import { type VariantProps, cva } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-(--radius-sm) font-mono text-[0.65rem] font-medium uppercase tracking-[0.14em] transition-colors",
  {
    variants: {
      variant: {
        accent:
          "border border-(--color-accent-border) bg-(--color-accent-soft) text-(--color-accent)",
        neutral:
          "border border-(--color-border) bg-(--color-elevated) text-(--color-ink-soft)",
        success:
          "border border-[oklch(0.78_0.10_145_/_30%)] bg-[oklch(0.78_0.10_145_/_10%)] text-(--color-success)",
        error:
          "border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_10%)] text-(--color-error)",
      },
      size: {
        default: "px-2 py-1",
        sm: "px-1.5 py-0.5 text-[0.6rem]",
        lg: "px-2.5 py-1",
      },
    },
    defaultVariants: { variant: "neutral", size: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, size, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant, size, className }))} {...props} />;
}
