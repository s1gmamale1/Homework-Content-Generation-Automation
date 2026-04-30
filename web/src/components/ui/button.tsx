import { Slot } from "@radix-ui/react-slot";
import { type VariantProps, cva } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-(--radius-md) text-sm font-medium tracking-tight transition-colors disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--color-accent)/60 focus-visible:ring-offset-2 focus-visible:ring-offset-(--color-canvas) [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-(--color-accent) text-[oklch(0.18_0.04_55)] hover:bg-(--color-accent-deep)",
        secondary:
          "bg-(--color-elevated) text-(--color-ink) border border-(--color-border) hover:bg-(--color-elevated-hover) hover:border-(--color-border-hover)",
        ghost:
          "bg-transparent text-(--color-ink) hover:bg-(--color-elevated)",
        outline:
          "border border-(--color-border) bg-transparent text-(--color-ink) hover:bg-(--color-elevated) hover:border-(--color-border-hover)",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 px-3 text-xs",
        lg: "h-10 px-5",
        icon: "size-9",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp: any = asChild ? Slot : "button";
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  },
);
Button.displayName = "Button";

export { buttonVariants };
