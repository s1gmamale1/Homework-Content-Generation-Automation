import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-9 w-full rounded-(--radius-md) border border-(--color-border) bg-(--color-elevated) px-3 text-sm text-(--color-ink)",
        "transition-colors duration-150 ease-(--ease-soft)",
        "placeholder:text-(--color-ink-muted)",
        "hover:border-(--color-border-hover)",
        "focus:outline-none focus:border-(--color-accent) focus:ring-2 focus:ring-(--color-accent)/30",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "file:mr-3 file:cursor-pointer file:rounded-(--radius-sm) file:border file:border-(--color-border-hover) file:bg-(--color-elevated-hover) file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-(--color-ink) hover:file:bg-(--color-accent) hover:file:text-[oklch(0.18_0.04_55)] file:transition-colors",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
