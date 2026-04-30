import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-11 w-full rounded-(--radius-md) border border-(--color-border-subtle) bg-(--color-surface) px-4 text-sm text-(--color-ink)",
        "backdrop-blur-md transition-[background,border-color,box-shadow] duration-200 ease-(--ease-soft)",
        "placeholder:text-(--color-ink-muted)",
        "hover:bg-(--color-surface-hover) hover:border-(--color-border-hover)",
        "focus:outline-none focus:bg-(--color-surface-hover) focus:border-(--color-amber) focus:ring-2 focus:ring-(--color-amber)/30",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "file:mr-3 file:cursor-pointer file:rounded-md file:border file:border-(--color-border-hover) file:bg-(--color-surface-strong) file:px-3 file:py-1.5 file:text-xs file:font-medium file:uppercase file:tracking-widest file:text-(--color-ink) hover:file:bg-(--color-amber) hover:file:text-[oklch(0.18_0.04_55)] file:transition-colors",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
