import { type VariantProps, cva } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full font-mono text-[0.65rem] font-medium uppercase tracking-[0.18em] backdrop-blur-md transition-colors",
  {
    variants: {
      variant: {
        amber:
          "border border-[oklch(0.79_0.13_70_/_30%)] bg-[oklch(0.79_0.13_70_/_14%)] text-(--color-amber)",
        glass:
          "border border-(--color-border-subtle) bg-(--color-surface) text-(--color-ink-soft)",
        success:
          "border border-[oklch(0.78_0.10_145_/_30%)] bg-[oklch(0.78_0.10_145_/_12%)] text-(--color-success)",
        error:
          "border border-[oklch(0.70_0.16_25_/_30%)] bg-[oklch(0.70_0.16_25_/_12%)] text-(--color-error)",
      },
      size: {
        default: "px-3 py-1",
        sm: "px-2 py-0.5 text-[0.6rem]",
        lg: "px-4 py-1.5 text-xs",
      },
    },
    defaultVariants: { variant: "glass", size: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, size, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant, size, className }))} {...props} />;
}
