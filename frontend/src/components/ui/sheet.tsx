import * as React from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { cva, type VariantProps } from "class-variance-authority"
import { X } from "lucide-react"

import { cn } from "@/lib/utils"

const SheetFocusContext = React.createContext<React.RefObject<HTMLElement | null> | null>(null)

/**
 * 标准侧滑抽屉根组件（D2 / #465 / #3260）。
 *
 * 与 `Dialog` 同机制：在受控 `open` 切为 `true` 时记录当前活动焦点元素，
 * 关闭时若原触发元素仍挂载在 DOM 中，则自动归位焦点，解决受控模式缺 `Trigger`
 * 导致焦点回落 `<body>` 的问题。
 */
function Sheet({
  open,
  defaultOpen,
  onOpenChange,
  modal = true,
  children,
}: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Root>) {
  const triggerElementRef = React.useRef<HTMLElement | null>(null)
  const prevOpenRef = React.useRef<boolean>(Boolean(open ?? defaultOpen))

  React.useLayoutEffect(() => {
    const isOpen = Boolean(open)
    if (isOpen && !prevOpenRef.current) {
      const active = document.activeElement
      triggerElementRef.current = active instanceof HTMLElement ? active : null
    }
    prevOpenRef.current = isOpen
  }, [open])

  // 首次即以 open=true 挂载时，在首次 layout 捕获触发元素
  React.useLayoutEffect(() => {
    if (open && !triggerElementRef.current) {
      const active = document.activeElement
      if (active instanceof HTMLElement && active !== document.body) {
        triggerElementRef.current = active
      }
    }
    // 仅挂载时检查一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <SheetFocusContext.Provider value={triggerElementRef}>
      <DialogPrimitive.Root
        open={open}
        defaultOpen={defaultOpen}
        onOpenChange={onOpenChange}
        modal={modal}
      >
        {children}
      </DialogPrimitive.Root>
    </SheetFocusContext.Provider>
  )
}

const SheetTrigger = DialogPrimitive.Trigger
const SheetClose = DialogPrimitive.Close
const SheetPortal = DialogPrimitive.Portal

const SheetOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-40 bg-foreground/30 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className,
    )}
    {...props}
  />
))
SheetOverlay.displayName = "SheetOverlay"

const sheetContentVariants = cva(
  "fixed z-50 flex flex-col overflow-hidden bg-card text-card-foreground shadow-2xl transition ease-in-out focus:outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:duration-200 data-[state=open]:duration-200",
  {
    variants: {
      side: {
        right:
          "inset-y-0 right-0 h-full w-full border-l border-border data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right",
        left:
          "inset-y-0 left-0 h-full w-full border-r border-border data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left",
      },
      size: {
        sm: "max-w-md",
        md: "max-w-lg",
        lg: "max-w-2xl",
        xl: "max-w-3xl",
      },
    },
    defaultVariants: {
      side: "right",
      size: "lg",
    },
  },
)

export interface SheetContentProps
  extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>,
    VariantProps<typeof sheetContentVariants> {
  /** 隐藏默认右上角关闭按钮（当消费者在自定义 header 内提供关闭按钮时使用） */
  hideCloseButton?: boolean
  /** 透传给遮罩层的 data-testid，便于测试点击遮罩关闭 */
  overlayTestId?: string
  /** 自定义遮罩层 className */
  overlayClassName?: string
}

const SheetContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  SheetContentProps
>(
  (
    {
      side = "right",
      size = "lg",
      className,
      children,
      hideCloseButton = false,
      overlayTestId,
      overlayClassName,
      onOpenAutoFocus,
      onCloseAutoFocus,
      ...props
    },
    ref,
  ) => {
    const triggerElementRef = React.useContext(SheetFocusContext)

    const handleOpenAutoFocus = (event: Event) => {
      if (triggerElementRef && !triggerElementRef.current) {
        const active = document.activeElement
        if (active instanceof HTMLElement && active !== document.body) {
          triggerElementRef.current = active
        }
      }
      onOpenAutoFocus?.(event)
    }

    const handleCloseAutoFocus = (event: Event) => {
      onCloseAutoFocus?.(event)
      if (event.defaultPrevented) return
      const target = triggerElementRef?.current
      if (target && target.isConnected && target !== document.body) {
        event.preventDefault()
        target.focus()
      }
    }

    return (
      <SheetPortal>
        <SheetOverlay data-testid={overlayTestId} className={overlayClassName} />
        <DialogPrimitive.Content
          ref={ref}
          aria-modal={props["aria-modal"] ?? true}
          onOpenAutoFocus={handleOpenAutoFocus}
          onCloseAutoFocus={handleCloseAutoFocus}
          className={cn(sheetContentVariants({ side, size }), className)}
          {...props}
        >
          {children}
          {!hideCloseButton && (
            <DialogPrimitive.Close
              aria-label="关闭抽屉"
              className="absolute right-4 top-3.5 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="h-4 w-4" />
              <span className="sr-only">关闭</span>
            </DialogPrimitive.Close>
          )}
        </DialogPrimitive.Content>
      </SheetPortal>
    )
  },
)
SheetContent.displayName = "SheetContent"

const SheetHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn("flex shrink-0 flex-col gap-1 border-b border-border px-5 py-3.5", className)}
    {...props}
  />
)
SheetHeader.displayName = "SheetHeader"

const SheetBody = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex-1 overflow-y-auto px-5 py-4", className)} {...props} />
)
SheetBody.displayName = "SheetBody"

const SheetFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      "flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border bg-muted/20 px-5 py-3",
      className,
    )}
    {...props}
  />
)
SheetFooter.displayName = "SheetFooter"

const SheetTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn("text-base font-semibold leading-tight tracking-[-0.02em] text-foreground", className)}
    {...props}
  />
))
SheetTitle.displayName = "SheetTitle"

const SheetDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-xs text-muted-foreground", className)}
    {...props}
  />
))
SheetDescription.displayName = "SheetDescription"

export {
  Sheet,
  SheetTrigger,
  SheetClose,
  SheetPortal,
  SheetOverlay,
  SheetContent,
  SheetHeader,
  SheetBody,
  SheetFooter,
  SheetTitle,
  SheetDescription,
}
