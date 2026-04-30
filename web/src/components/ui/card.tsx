import * as React from "react";
import { cn } from "@/lib/utils";

export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-(--radius-lg) border border-(--color-border-subtle) bg-(--color-surface) backdrop-blur-xl",
        "transition-[background,border-color,box-shadow,transform] duration-300 ease-(--ease-out-expo)",
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = "Card";

export const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("p-5", className)} {...props} />
  ),
);
CardContent.displayName = "CardContent";

export const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("px-5 pt-5 pb-3", className)} {...props} />
  ),
);
CardHeader.displayName = "CardHeader";
