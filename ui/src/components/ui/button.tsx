import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "cn"

// Primary is a navy fill, secondary a navy outline. Every action button is navy or outline:
// red is reserved for a fraud hold (D-05).
const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center rounded-sm border border-transparent text-sm font-semibold whitespace-nowrap transition-colors duration-150 select-none disabled:cursor-default disabled:border-border disabled:bg-surface disabled:text-ink-2 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        outline: "border-primary bg-surface text-primary hover:bg-subtle",
        secondary: "bg-secondary text-secondary-foreground hover:bg-line",
        ghost: "text-ink hover:bg-subtle",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-8 gap-1 px-3",
        sm: "h-7 gap-1 px-2",
        icon: "size-8",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
