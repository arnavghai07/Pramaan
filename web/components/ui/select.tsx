import { cn } from "@/lib/utils"

/**
 * A plain native <select>, styled to match Input.
 *
 * Deliberately not the @base-ui listbox: a native select is what a phone
 * renders as the OS picker, which is the right control for an inspector
 * filtering history one-handed in the field. It also keeps this component
 * free of portal/z-index behaviour it does not need.
 */
function Select({ className, children, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      data-slot="select"
      className={cn(
        "flex h-9 w-full min-w-0 rounded-lg border border-border bg-background px-3 py-1 text-sm shadow-xs transition-colors outline-none",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        "dark:bg-input/30",
        className
      )}
      {...props}
    >
      {children}
    </select>
  )
}

export { Select }
