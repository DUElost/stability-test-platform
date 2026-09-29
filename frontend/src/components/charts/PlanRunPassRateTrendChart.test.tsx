import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { PlanRunPassRateTrendChart } from './PlanRunPassRateTrendChart';
import type { PlanRunPassRatePoint } from '@/utils/api/types';

const points: PlanRunPassRatePoint[] = [
  { date: '2026-06-01', avg_pass_rate: 0.8, run_count: 3 },
  { date: '2026-06-02', avg_pass_rate: 0.5, run_count: 2 },
];

describe('PlanRunPassRateTrendChart', () => {
  it('renders skeleton while loading', () => {
    const { container } = render(<PlanRunPassRateTrendChart data={[]} isLoading />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeInTheDocument();
  });

  it('renders empty state when there is no data', () => {
    render(<PlanRunPassRateTrendChart data={[]} isLoading={false} />);
    expect(screen.getByText('运行通过率趋势 (30d)')).toBeInTheDocument();
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });

  it('renders title without crashing when data is present', () => {
    render(<PlanRunPassRateTrendChart data={points} isLoading={false} />);
    expect(screen.getByText('运行通过率趋势 (30d)')).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('treats undefined data the same as empty', () => {
    render(<PlanRunPassRateTrendChart isLoading={false} />);
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
  });
});

// ── #3186（B2-G4，#3497 §1-F3）：零填充日（run_count=0）不得被画成 0% ────────
//
// 断言钉在**调用点**：桩掉 recharts，取 LineChart 实际拿到的 `data`——判零映射发生在
// 组件的 `chartData`，纯函数断言测不出「映射改动没落到调用点」（先例：本目录
// PlanFailedDevicesChart.test.tsx 的 #2987）。jsdom 里 ResponsiveContainer 量不到
// 宽高、StableResponsiveContainer 的尺寸就绪门恒关，两处都桩掉，否则一切渲染断言
// 退化成空转。Tooltip 的 `content` 用真实 chartData 逐点调用，把「tooltip 不显示
// 0%」钉成 DOM 文本；LineChart 的桩先于 children 执行，captured.data 此刻已就绪。

type TrendDatum = PlanRunPassRatePoint & { label: string; ratePct: number | null };

const captured = vi.hoisted(() => ({ data: undefined as TrendDatum[] | undefined }));

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  LineChart: ({ data, children }: { data?: TrendDatum[]; children?: ReactNode }) => {
    captured.data = data;
    return <div>{children}</div>;
  },
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: ({
    content,
  }: {
    content?: (props: { active: boolean; payload: Array<{ payload: TrendDatum }> }) => ReactNode;
  }) => (
    <div>
      {(captured.data ?? []).map((d) => (
        <div key={d.date}>{content?.({ active: true, payload: [{ payload: d }] })}</div>
      ))}
    </div>
  ),
}));

vi.mock('./StableResponsiveContainer', () => ({
  StableResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));

describe('零填充日画 null 不画 0%（#3186 / F3）', () => {
  it('run_count=0 的点 ratePct 为 null（折线断开），有数据的点照常取值', () => {
    render(
      <PlanRunPassRateTrendChart
        data={[
          { date: '2026-06-01', avg_pass_rate: 0.8, run_count: 3 },
          { date: '2026-06-02', avg_pass_rate: 0, run_count: 0 },
        ]}
        isLoading={false}
      />,
    );
    expect(captured.data?.map((d) => d.ratePct)).toEqual([80, null]);
  });

  it('run_count>0 且 avg_pass_rate=0 的点仍是 0——真全失败不被画成无数据', () => {
    render(
      <PlanRunPassRateTrendChart
        data={[{ date: '2026-06-02', avg_pass_rate: 0, run_count: 2 }]}
        isLoading={false}
      />,
    );
    expect(captured.data?.[0]?.ratePct).toBe(0);
  });

  it('tooltip 对 run_count=0 的点不显示 0%，显示「—」', () => {
    render(
      <PlanRunPassRateTrendChart
        data={[{ date: '2026-06-02', avg_pass_rate: 0, run_count: 0 }]}
        isLoading={false}
      />,
    );
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('tooltip 对真实 0%（全失败）照常显示 0%', () => {
    render(
      <PlanRunPassRateTrendChart
        data={[{ date: '2026-06-03', avg_pass_rate: 0, run_count: 2 }]}
        isLoading={false}
      />,
    );
    expect(screen.getByText('0%')).toBeInTheDocument();
    expect(screen.queryByText('—')).not.toBeInTheDocument();
  });
});
