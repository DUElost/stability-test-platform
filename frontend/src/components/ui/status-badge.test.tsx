import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StatusBadge, resolveStatusEntry } from "./status-badge";

describe("StatusBadge", () => {
  it("renders device ONLINE with success variant + 在线 label", () => {
    render(<StatusBadge kind="device" status="ONLINE" />);
    expect(screen.getByText("在线")).toBeInTheDocument();
  });

  it("renders device ERROR with destructive variant + 异常 label", () => {
    render(<StatusBadge kind="device" status="ERROR" />);
    expect(screen.getByText("异常")).toBeInTheDocument();
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

  /**
   * #2494 / ADR-0045 D2·D3：对外风险词表是**级别本身**（S/A/B/UNKNOWN），
   * 「高/中/低」只是这里的一处文案。缺任一键 = 那种风险的徽标恒显「未知」，
   * 而同屏 S/A/B 计数照常渲染 —— #2418 的同型缺陷，只是换了词表。
   */
  it.each([
    ["S", "高", "destructive"],
    ["A", "中", "warning"],
    ["B", "低", "success"],
  ] as const)("renders risk %s as %s（%s）", (level, label, variant) => {
    render(<StatusBadge kind="risk" status={level} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText("未知")).toBeNull();
    // 着色轴同样要钉住：S 与 B 同色 = 把「高/低」压回一个视觉桶（D4 的反面）
    expect(resolveStatusEntry("risk", level).variant).toBe(variant);
  });

  /**
   * UNKNOWN 的文案与 FALLBACK 同形（都叫「未知」），DOM 上分不出它是显式键还是兜底。
   * 真判据是**键存在性**，那条由离线门禁钉：
   * `tests/test_risk_vocabulary_drift.py::test_every_outward_risk_level_has_a_badge_key`
   * 直接解析这张表，缺 S/A/B/UNKNOWN 任一键即红。
   */
  it("renders risk UNKNOWN as 未知", () => {
    render(<StatusBadge kind="risk" status="UNKNOWN" />);
    expect(screen.getByText("未知")).toBeInTheDocument();
  });

  it("keeps HIGH/MEDIUM/LOW for alert severity (D5 另一条轴，值域不动)", () => {
    for (const severity of ["HIGH", "MEDIUM", "LOW"]) {
      const { unmount } = render(<StatusBadge kind="risk" status={severity} />);
      expect(screen.queryByText("未知")).toBeNull();
      unmount();
    }
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

  it("renders device-ui running with warning variant + 运行中 label", () => {
    render(<StatusBadge kind="device-ui" status="running" />);
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("renders device-ui unknown with warning variant + 已断开 label", () => {
    render(<StatusBadge kind="device-ui" status="unknown" />);
    expect(screen.getByText("已断开")).toBeInTheDocument();
  });

  // #786：后端 `_job_exec_status_for_job`（routes/plan_runs.py）含 aborted 分支，该值以
  // kind="device-ui" 渲染；此前缺键 → 落 FALLBACK「未知」，与「已断开」混淆。
  it("renders device-ui aborted with warning variant + 已中止 label（#786）", () => {
    render(<StatusBadge kind="device-ui" status="aborted" />);
    expect(screen.getByText("已中止")).toBeInTheDocument();
    expect(screen.queryByText("未知")).not.toBeInTheDocument();
  });

  it("resolveStatusEntry 对 device-ui/aborted 大小写不敏感（#786）", () => {
    expect(resolveStatusEntry("device-ui", "aborted").label).toBe("已中止");
    expect(resolveStatusEntry("device-ui", "ABORTED").label).toBe("已中止");
  });

  it("renders device-ui backoff with warning variant + 退避 label", () => {
    render(<StatusBadge kind="device-ui" status="backoff" />);
    expect(screen.getByText("退避")).toBeInTheDocument();
  });

  it("renders device-ui pending with secondary variant + 等待 label", () => {
    render(<StatusBadge kind="device-ui" status="pending" />);
    expect(screen.getByText("等待")).toBeInTheDocument();
  });

  it("renders precheck-phase verifying with info variant + 校验中 label", () => {
    render(<StatusBadge kind="precheck-phase" status="verifying" />);
    expect(screen.getByText("校验中")).toBeInTheDocument();
  });

  it("renders precheck-phase ready with success variant + 门禁通过 label", () => {
    render(<StatusBadge kind="precheck-phase" status="ready" />);
    expect(screen.getByText("门禁通过")).toBeInTheDocument();
  });

  it("renders precheck-phase failed with destructive variant + 门禁失败 label", () => {
    render(<StatusBadge kind="precheck-phase" status="failed" />);
    expect(screen.getByText("门禁失败")).toBeInTheDocument();
  });

  it("renders precheck-host ok with success variant + 一致 label", () => {
    render(<StatusBadge kind="precheck-host" status="ok" />);
    expect(screen.getByText("一致")).toBeInTheDocument();
  });

  it("renders precheck-host synced with info variant + 已同步 label", () => {
    render(<StatusBadge kind="precheck-host" status="synced" />);
    expect(screen.getByText("已同步")).toBeInTheDocument();
  });

  it("falls back to 未知 for unknown status string", () => {
    render(<StatusBadge kind="device" status="MELTED" />);
    expect(screen.getByText("未知")).toBeInTheDocument();
  });

  it("renders job-result statuses with Chinese labels", () => {
    render(
      <>
        <StatusBadge kind="job-result" status="QUEUED" />
        <StatusBadge kind="job-result" status="RUNNING" />
        <StatusBadge kind="job-result" status="FINISHED" />
        <StatusBadge kind="job-result" status="FAILED" />
        <StatusBadge kind="job-result" status="CANCELED" />
      </>,
    );
    expect(screen.getByText("排队中")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("完成")).toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("已中止")).toBeInTheDocument();
  });

  it("job-result RUNNING uses warning variant (#356 运行中全站统一)", () => {
    const entry = resolveStatusEntry("job-result", "RUNNING");
    expect(entry.variant).toBe("warning");
  });

  it("job-result CANCELED uses neutral secondary (cancel ≠ fail convention)", () => {
    const entry = resolveStatusEntry("job-result", "CANCELED");
    expect(entry.label).toBe("已中止");
    expect(entry.variant).toBe("secondary");
  });

  it("fallbackToRaw renders raw status for unrecognized values", () => {
    render(<StatusBadge kind="job-result" status="MAGENTA" fallbackToRaw />);
    expect(screen.getByText("MAGENTA")).toBeInTheDocument();
    expect(screen.queryByText("未知")).not.toBeInTheDocument();
  });

  it("fallbackToRaw keeps 未知 for empty status", () => {
    render(<StatusBadge kind="job-result" status={null} fallbackToRaw />);
    expect(screen.getByText("未知")).toBeInTheDocument();
  });

  it("falls back to 未知 by default without fallbackToRaw", () => {
    render(<StatusBadge kind="job-result" status="MAGENTA" />);
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

  it("applies animate-spin to icon when spin=true", () => {
    const { container } = render(
      <StatusBadge kind="device-ui" status="running" spin />,
    );
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg?.getAttribute("class") || "").toContain("animate-spin");
  });

  it("does not apply animate-spin by default", () => {
    const { container } = render(<StatusBadge kind="device-ui" status="running" />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("class") || "").not.toContain("animate-spin");
  });

  it("resolveStatusEntry returns same entry as component output", () => {
    const entry = resolveStatusEntry("plan-run", "RUNNING");
    expect(entry.label).toBe("运行中");
    expect(entry.variant).toBe("warning");
  });
});
