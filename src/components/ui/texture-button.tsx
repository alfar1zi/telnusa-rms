import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariantsOuter = cva("inline-flex items-center justify-center transition duration-200 select-none cursor-pointer disabled:pointer-events-none disabled:opacity-50", {
  variants: {
    variant: {
      primary:
        "border border-border/80 bg-primary/90 p-[1px] hover:bg-primary active:scale-[0.98] shadow-sm",
      accent:
        "border border-border/80 bg-[var(--chart-1)]/90 p-[1px] hover:bg-[var(--chart-1)] active:scale-[0.98] shadow-sm",
      destructive:
        "border border-destructive/30 bg-destructive/90 p-[1px] hover:bg-destructive active:scale-[0.98] shadow-sm",
      secondary:
        "border border-border bg-secondary/80 p-[1px] hover:bg-secondary active:scale-[0.98] shadow-sm",
      minimal:
        "group/texture-button border border-border/70 bg-card p-[1px] hover:bg-accent/60 active:scale-[0.98] shadow-xs",
      icon: "group/texture-button rounded-full border border-border/70 bg-card p-[1px] hover:bg-accent/70 active:scale-[0.95]",
      ghost: "border-transparent bg-transparent hover:bg-accent hover:text-accent-foreground",
    },
    size: {
      sm: "rounded-[6px]",
      default: "rounded-[8px]",
      lg: "rounded-[10px]",
      icon: "rounded-full",
    },
  },
  defaultVariants: {
    variant: "primary",
    size: "default",
  },
});

const innerDivVariants = cva(
  "flex items-center justify-center font-medium transition duration-200",
  {
    variants: {
      variant: {
        primary:
          "gap-2 bg-primary text-primary-foreground hover:bg-primary/95 text-xs sm:text-sm",
        accent:
          "gap-2 bg-[var(--chart-1)] text-white hover:bg-[var(--chart-1)]/90 text-xs sm:text-sm font-semibold",
        destructive:
          "gap-2 bg-destructive text-destructive-foreground hover:bg-destructive/90 text-xs sm:text-sm",
        secondary:
          "gap-2 bg-secondary text-secondary-foreground hover:bg-secondary/90 text-xs sm:text-sm",
        minimal:
          "gap-2 bg-card text-foreground group-hover/texture-button:bg-accent/40 text-xs sm:text-sm",
        icon: "rounded-full bg-card text-foreground group-hover/texture-button:bg-accent/50",
        ghost: "gap-2 text-foreground text-xs sm:text-sm",
      },
      size: {
        sm: "rounded-[5px] px-2.5 py-1 text-xs",
        default: "rounded-[7px] px-3.5 py-1.5 text-xs sm:text-sm",
        lg: "rounded-[9px] px-4 py-2 text-sm sm:text-base",
        icon: "rounded-full p-1.5",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "default",
    },
  }
);

export interface TextureButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariantsOuter> {
  asChild?: boolean;
}

const TextureButton = React.forwardRef<HTMLButtonElement, TextureButtonProps>(
  (
    {
      children,
      variant = "primary",
      size = "default",
      asChild = false,
      className,
      ...props
    },
    ref
  ) => {
    const Comp = asChild ? Slot : "button";

    if (variant === "ghost") {
      // The ghost branch has no inner wrapper, so the button carries the inner
      // variant's own classes. It must NOT inherit the wrapper's h-full/w-full:
      // a button that fills its container is what stretched the header's user
      // menu across the whole bar and pushed its content to the middle.
      return (
        <Comp
          className={cn(buttonVariantsOuter({ variant, size }), innerDivVariants({ variant, size }), className)}
          ref={ref}
          {...props}
        >
          {children}
        </Comp>
      );
    }

    return (
      <Comp
        className={cn(buttonVariantsOuter({ variant, size }), className)}
        ref={ref}
        {...props}
      >
        <div className={cn("h-full w-full", innerDivVariants({ variant, size }))}>
          {children}
        </div>
      </Comp>
    );
  }
);

TextureButton.displayName = "TextureButton";

export { TextureButton, buttonVariantsOuter, innerDivVariants };
