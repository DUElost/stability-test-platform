import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StatusBadge, resolveStatusEntry } from "./status-badge";

describe("StatusBadge", () => {
  it("renders device ONLINE with success variant + 在线 label", () => {
    render(<StatusBadge kind="device" status="ONLINE" />);
    expect(screen.getByText("在线")).toBeInTheDocument();
  });

  it("renders host DEGRADED with warning variant", () => {
    render(<StatusBadge kind="host" status="DEGRADED" />);
    expect(screen.getByText("降级")).toBeInTheDocument();
  });

  it("renders job RUNNING with info variant + 运行中 label", () => {
    const { container } = render(<StatusBadge kind="job" status="RUNNING" />);
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(container.querySelector('[data-status="RUNNING"]')).toBeTruthy();
  });

  it("renders plan-run PARTIAL_SUCCESS with warning variant", () => {
    render(<StatusBadge kind="plan-run" status="PARTIAL_SUCCESS" />);
    expect(screen.getByText("部分成功")).toBeInTheDocument();
  });

  it("renders risk HIGH with destructive variant", () => {
    render(<StatusBadge kind="risk" status="HIGH" />);
    expect(screen.getByText("高")).toBeInTheDocument();
  });

  it("renders priority Critical with destructive variant", () => {
    render(<StatusBadge kind="priority" status="Critical" />);
    expect(screen.getByText("Critical")).toBeInTheDocument();
  });

  it("renders device-ui testing with info variant + 测试中 label", () => {
    render(<StatusBadge kind="device-ui" status="testing" />);
    expect(screen.getByText("测试中")).toBeInTheDocument();
  });

  it("renders device-ui error with destructive variant", () => {
    render(<StatusBadge kind="device-ui" status="error" />);
    expect(screen.getByText("错误")).toBeInTheDocument();
  });

  it("falls back to 未知 for unknown status string", () => {
    render(<StatusBadge kind="device" status="MELTED" />);
    expect(screen.getByText("未知")).toBeInTheDocument();
  });

  it("falls back to 未知 for null status", () => {
    render(<StatusBadge kind="job" status={null} />);
    expect(screen.getByText("未知")).toBeInTheDocument();
  });

  it("handles lowercase status input", () => {
    render(<StatusBadge kind="device" status="online" />);
    expect(screen.getByText("在线")).toBeInTheDocument();
  });

  it("omits icon when showIcon=false", () => {
    const { container } = render(
      <StatusBadge kind="device" status="ONLINE" showIcon={false} />,
    );
    expect(container.querySelector("svg")).toBeNull();
  });

  it("resolveStatusEntry returns same entry as component output", () => {
    const entry = resolveStatusEntry("plan-run", "RUNNING");
    expect(entry.label).toBe("运行中");
    expect(entry.variant).toBe("info");
  });
});
