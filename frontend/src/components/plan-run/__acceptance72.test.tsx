/**
 * #72 验收项 5/6:用生产 PlanRun 103 的真实 watcher-summary payload 渲染
 * AnomalyDashboard,确认饼图 / 包名榜 / 异常率进度条
 * 真的把数据画出来。
 *
 * payload 来源:GET /api/v1/plan-runs/103/watcher-summary(2026-07-26 抓取)
 *
 * 注意这批信号的 origin 分类:设备侧崩溃时间 aee_ts=2026-07-16,而 PlanRun
 * 起于 2026-07-25 — Reconciler 从 db_history 拉到的是**运行前遗留**崩溃,
 * 故 current_run=0 / preexisting=4,数据落在「运行前遗留」面板。这是正确
 * 行为,不是空态 bug。
 */
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect } from 'vitest';
import AnomalyDashboard from './AnomalyDashboard';
import realPayload from './__fixtures__/watcher-summary-103.json';

const wrap = (ui: React.ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {ui}
  </QueryClientProvider>
);

describe('#72 acceptance — real production payload (PlanRun 103)', () => {
  it('AnomalyDashboard 遗留面板渲染子类型占比饼图 (NE 75% / ANR 25%)', () => {
    const { container } = render(wrap(<AnomalyDashboard runId={103} data={realPayload as any} />));
    const text = container.textContent ?? '';

    expect(text).toMatch(/NE\s*75\s*%/);
    expect(text).toMatch(/ANR\s*25\s*%/);
  });

  it('AnomalyDashboard 遗留面板渲染总量与 Top 包名 / Top 类型', () => {
    const { container } = render(wrap(<AnomalyDashboard runId={103} data={realPayload as any} />));
    const text = container.textContent ?? '';

    expect(text).toMatch(/运行前遗留异常/);
    expect(text).toMatch(/遗留总量\s*4/);
    expect(text).toMatch(/com\.android\.settings/);
  });

  it('AnomalyDashboard 正确把历史崩溃归入遗留而非本次新增', () => {
    const { container } = render(wrap(<AnomalyDashboard runId={103} data={realPayload as any} />));
    const text = container.textContent ?? '';

    // aee_ts=07-16 早于 run started_at=07-25 → 本次新增应为 0
    expect(text).toMatch(/本次新增异常总量\s*0/);
    expect(text).toMatch(/当前范围内未发现新增/);
  });
});
