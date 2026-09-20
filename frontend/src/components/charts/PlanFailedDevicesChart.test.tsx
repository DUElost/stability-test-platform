import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
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
