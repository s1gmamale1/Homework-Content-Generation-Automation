import { Slot } from "@radix-ui/react-slot";
import { type VariantProps, cva } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "relative inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-(--radius-md) text-sm font-medium tracking-tight transition-[transform,box-shadow,filter] disabled:pointer-events-none disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--color-amber)/60 focus-visible:ring-offset-2 focus-visible:ring-offset-(--color-canvas) [&_svg]:pointer-events-none [&_svg]:shrink-0 overflow-hidden",
  {
    variants: {
      variant: {
        primary:
          "text-[oklch(0.18_0.04_55)] bg-[linear-gradient(135deg,var(--color-amber)_0%,var(--color-rust)_100%)] shadow-[0_8px_24px_-8px_oklch(0.79_0.13_70_/_45%),inset_0_1px_0_oklch(0.99_0.005_80_/_25%)] hover:-translate-y-0.5 hover:brightness-[1.06] hover:shadow-[0_16px_36px_-10px_oklch(0.79_0.13_70_/_55%),inset_0_1px_0_oklch(0.99_0.005_80_/_30%)] active:translate-y-0",
        ghost:
          "bg-transparent text-(--color-ink) hover:bg-(--color-surface-hover) border border-transparent hover:border-(--color-border-hover)",
        outline:
          "border border-(--color-border-subtle) bg-(--color-surface) text-(--color-ink) hover:bg-(--color-surface-hover) hover:border-(--color-border-hover) backdrop-blur-md",
        glass:
          "bg-(--color-surface) text-(--color-ink) border border-(--color-border-subtle) backdrop-blur-xl hover:bg-(--color-surface-hover) hover:border-(--color-border-hover)",
      },
      size: {
        default: "h-11 px-5 py-2.5",
        sm: "h-9 px-3 text-xs",
        lg: "h-12 px-6 text-base",
        icon: "size-10",
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
  ({ className, variant, size, asChild = false, children, ...props }, ref) => {
    const Comp: any = asChild ? Slot : "button";
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props}>
        {variant === "primary" || !variant ? (
          <>
            <span className="absolute inset-0 -translate-x-full bg-[linear-gradient(105deg,transparent_30%,oklch(0.99_0.005_80_/_45%)_50%,transparent_70%)] transition-transform duration-700 ease-out group-hover:translate-x-full pointer-events-none" />
            <span className="relative z-10 inline-flex items-center gap-2">{children}</span>
          </>
        ) : (
          children
        )}
      </Comp>
    );
  },
);
Button.displayName = "Button";

export { buttonVariants };
