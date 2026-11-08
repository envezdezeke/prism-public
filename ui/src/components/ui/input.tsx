import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"
import { cn } from "cn"

// Border is tertiary (3:1 non-text contrast). The invalid state uses ink-2, never red.
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        "h-8 w-full min-w-0 rounded-sm border border-input bg-surface px-2 text-sm text-ink transition-colors duration-150 placeholder:text-ink-2 disabled:cursor-default disabled:border-border disabled:text-ink-2 aria-invalid:border-ink-2",
        className
      )}
      {...props}
    />
  )
}

export { Input }
