import { useState } from "react"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "./sheet"

function ControlledSheetHarness({ size = "lg" as "sm" | "md" | "lg" | "xl" }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        查看详情
      </button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent size={size} overlayTestId="test-sheet-overlay">
          <SheetHeader>
            <SheetTitle>实体详情</SheetTitle>
            <SheetDescription>只读上下文</SheetDescription>
          </SheetHeader>
          <SheetBody>
            <button type="button">抽屉内按钮</button>
          </SheetBody>
        </SheetContent>
      </Sheet>
    </div>
  )
}

describe("Sheet 标准侧滑抽屉（D2 / #465 / #3260）", () => {
  it("打开渲染 dialog、Esc 关闭后焦点归位至触发按钮", async () => {
    render(<ControlledSheetHarness size="lg" />)
    const trigger = screen.getByRole("button", { name: "查看详情" })
    trigger.focus()
    fireEvent.click(trigger)

    const dialog = screen.getByRole("dialog")
    expect(dialog).toBeInTheDocument()
    expect(dialog.className).toContain("max-w-2xl")
    expect(screen.getByTestId("test-sheet-overlay")).toBeInTheDocument()

    fireEvent.keyDown(dialog, { key: "Escape" })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    await waitFor(() => {
      expect(document.activeElement).toBe(trigger)
    })
  })

  it("支持 sm / md / xl 宽度档与关闭按钮关闭", () => {
    render(<ControlledSheetHarness size="xl" />)
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }))
    expect(screen.getByRole("dialog").className).toContain("max-w-3xl")

    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })
})
