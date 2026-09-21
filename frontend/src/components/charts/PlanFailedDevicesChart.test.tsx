import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { PlanFailedDevicesChart } from './PlanFailedDevicesChart';
import { buildFailedDeviceRows } from './failedDeviceRows';
import type { PlanFailedDevicesItem } from '@/utils/api/types';

const items: PlanFailedDevicesItem[] = [
  { plan_id: 1, plan_name: 'stable-plan', total_jobs: 10, failed: 0 },
  { plan_id: 2, plan_name: 'pressured-plan', total_jobs: 10, failed: 4 },
];

describe('PlanFailedDevicesChart (ADR-0048)', () => {
  it('renders skeleton while loading', () => {
    const { container } = render(<PlanFailedDevicesChart data={[]} isLoading />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeInTheDocument();
  });

  it('renders empty state without crash records', () => {
    render(<PlanFailedDevicesChart data={[]} isLoading={false} />);
    expect(screen.getByText('方案失败设备数排行 (30d)')).toBeInTheDocument();
    expect(screen.getByText('近 30 天无失败设备记录')).toBeInTheDocument();
  });

  it('renders title without crashing when data is present', () => {
    render(<PlanFailedDevicesChart data={items} isLoading={false} />);
    expect(screen.getByText('方案失败设备数排行 (30d)')).toBeInTheDocument();
    expect(screen.queryByText('近 30 天无失败设备记录')).not.toBeInTheDocument();
  });

  it('treats undefined data the same as empty', () => {
    render(<PlanFailedDevicesChart isLoading={false} />);
    expect(screen.getByText('近 30 天无失败设备记录')).toBeInTheDocument();
  });
});

// ── #2848：顺序与条数以服务端为权威，前端不再二次排序 / 静默截断 ──────────
//
// 说明（#2987）：本组断言的是**纯映射函数** `buildFailedDeviceRows`。它结构上
// 不可能违反「不重排、不截断」（`.map` 既不丢元素也不改次序），所以这组用例只锁
// 函数自身的契约，**不能**证明调用点没再动手脚——把 `sort(...).slice(0, 10)` 加回
// `PlanFailedDevicesChart` 的 `useMemo`，这四条依然全绿。调用点那半边钉在文件末尾
// 的「#2987」describe 里。

function row(i: number, failed: number, total: number): PlanFailedDevicesItem {
  return {
    plan_id: i,
    plan_name: `plan-${String(i).padStart(2, '0')}`,
    total_jobs: total,
    failed,
  };
}

describe('buildFailedDeviceRows（#2848）', () => {
  it('保持服务端次序：同分条目不被前端按单一键重排', () => {
    // 服务端 ORDER BY failed DESC, total_jobs DESC ⇒ 同 failed 时 total 大的在前。
    // 构造一组「按 failed 单键排会打平、按服务端次序有定序」的输入。
    const serverOrder = [row(2, 3, 90), row(1, 3, 12), row(3, 1, 40)];
    const out = buildFailedDeviceRows(serverOrder);
    expect(out.map((r) => r.plan_id)).toEqual([2, 1, 3]);
  });

  it('不截断：limit>10（接口允许到 50）时前端不得再切回 10 条', () => {
    const many = Array.from({ length: 24 }, (_, i) => row(i + 1, 25 - i, 30));
    expect(buildFailedDeviceRows(many)).toHaveLength(24);
  });

  it('只做标签截断，不改数据本身', () => {
    const [only] = buildFailedDeviceRows([row(1, 2, 3)]);
    expect(only.label).toBe('plan-01');
    expect(only.failed).toBe(2);
    expect(only.total_jobs).toBe(3);
  });

  it('空与 undefined 都走空态（健康期后端只返回有失败的组，#2848）', () => {
    expect(buildFailedDeviceRows([])).toEqual([]);
    expect(buildFailedDeviceRows(undefined)).toEqual([]);
  });
});

// ── #2987：#2848 的不变量必须钉在**调用点**，而不只钉在纯函数上 ──────────────

/** 图表从 `BarChart` 拿到的 `data`（调用点的证据，不是纯函数的返回值）。 */
const captured = vi.hoisted(() => ({ data: undefined as PlanFailedDevicesItem[] | undefined }));

// 桩掉 recharts 的渲染层，只留下「谁拿到了什么数据」：图表内部的几何/动画与本案无关，
// 而 jsdom 里 ResponsiveContainer 量不到宽高，不桩就什么都渲染不出来。
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  BarChart: ({ data, children }: { data?: PlanFailedDevicesItem[]; children?: ReactNode }) => {
    captured.data = data;
    return <div data-testid="bar-chart">{children}</div>;
  },
  Bar: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Cell: () => null,
  LabelList: () => null,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

// 尺寸就绪门也要桩：jsdom 的 offsetHeight 恒 0，而 `src/test/setup.ts` 的 ResizeObserver
// 是 no-op，真容器永远不会挂载子节点——不桩的话下面两条会退化成「渲染了但没数据」的
// 空转断言（#2879 数过的「不可判定被读成通过」）。
vi.mock('./StableResponsiveContainer', () => ({
  StableResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));

describe('#2848 不变量钉在调用点（#2987）', () => {
  it('24 条（limit 允许到 50）整份进图：调用点不得再切回 10 条', () => {
    const many = Array.from({ length: 24 }, (_, i) => row(i + 1, 25 - i, 30));
    render(<PlanFailedDevicesChart data={many} isLoading={false} />);
    expect(captured.data).toHaveLength(24);
  });

  it('次序原样透传：同 failed 时不按单一键重排', () => {
    // 服务端 ORDER BY failed DESC, total_jobs DESC ⇒ 同 failed 时 total 大的在前。
    const serverOrder = [row(2, 3, 90), row(1, 3, 12), row(3, 1, 40)];
    render(<PlanFailedDevicesChart data={serverOrder} isLoading={false} />);
    expect(captured.data?.map((r) => r.plan_id)).toEqual([2, 1, 3]);
  });
});
