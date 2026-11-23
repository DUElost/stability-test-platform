import { useState } from "react"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "./dialog"

function ControlledDialogHarness() {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        打开弹窗
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogTitle>测试标题</DialogTitle>
          <DialogDescription>测试说明</DialogDescription>
          <button type="button" onClick={() => setOpen(false)}>
            内部关闭
          </button>
        </DialogContent>
      </Dialog>
    </div>
  )
}

describe("Dialog 受控焦点归位（#3259）", () => {
  it("通过 Esc 关闭受控 Dialog 后焦点自动归位至触发按钮", async () => {
    render(<ControlledDialogHarness />)
    const trigger = screen.getByRole("button", { name: "打开弹窗" })
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    fireEvent.click(trigger)
    const dialog = screen.getByRole("dialog")
    expect(dialog).toBeInTheDocument()

    fireEvent.keyDown(dialog, { key: "Escape" })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    await waitFor(() => {
      expect(document.activeElement).toBe(trigger)
    })
  })
})
