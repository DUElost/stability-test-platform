import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import PlanRunKpiGrid from './PlanRunKpiGrid';
import type { PlanRunDevicesPayload } from '@/utils/api/types';

const makeDevices = (
  summary: Record<string, number>,
  byLinkStatus?: Record<string, number>,
): PlanRunDevicesPayload => ({
  plan_run_id: 1,
  total: summary.total ?? 0,
  by_status: summary,
  ...(byLinkStatus ? { by_link_status: byLinkStatus } : {}),
  by_host: {},
  devices: [],
} as unknown as PlanRunDevicesPayload);

describe('PlanRunKpiGrid', () => {
  it('renders all 7 cells', () => {
    render(<PlanRunKpiGrid devices={makeDevices({ total: 10, running: 3, completed: 5, failed: 2, unknown: 0, backoff: 0 })} currentStage="patrol" patrolCycle={4} />);
    expect(screen.getByTestId('kpi-total').textContent).toContain('10');
    expect(screen.getByTestId('kpi-running').textContent).toContain('3');
    expect(screen.getByTestId('kpi-completed').textContent).toContain('5');
    expect(screen.getByTestId('kpi-failed').textContent).toContain('2');
    expect(screen.getByTestId('kpi-disconnected-backoff')).toHaveTextContent('Job 失联/退避');
    expect(screen.getByTestId('kpi-disconnected-backoff').textContent).toContain('0');
    expect(screen.getByTestId('kpi-link-abnormal')).toHaveTextContent('设备连接异常');
  });

  it('shows patrol stage label in Chinese', () => {
    render(<PlanRunKpiGrid currentStage="patrol" />);
    expect(screen.getByTestId('kpi-stage').textContent).toContain('巡检');
  });

  it('shows patrol cycle when provided', () => {
    render(<PlanRunKpiGrid currentStage="patrol" patrolCycle={7} />);
    expect(screen.getByTestId('kpi-stage').textContent).toContain('7');
  });

  it('shows 0s when no data', () => {
    render(<PlanRunKpiGrid />);
    expect(screen.getByTestId('kpi-total').textContent).toContain('0');
  });

  it('applies red tone to failed when > 0', () => {
    render(<PlanRunKpiGrid devices={makeDevices({ total: 5, failed: 2, running: 0, completed: 3, unknown: 0, backoff: 0 })} />);
    const cell = screen.getByTestId('kpi-failed');
    expect(cell.querySelector('.text-destructive')).toBeTruthy();
  });

  // ── 连接维度 ────────────────────────────────────────────────────────
  //
  // 回归背景:by_status 改按 job_exec_status 聚合后,「RUNNING 但 ADB 断连」
  // 的设备计入 running 而非 unknown。KPI 若仍只看 by_status,断连设备就会
  // 被显示成「运行中」,操作员完全看不到需要现场处理的机器。

  it('断连设备计入运行中时，连接异常仍如实计数', () => {
    render(
      <PlanRunKpiGrid
        devices={makeDevices(
          // 执行维度:3 台都在 running,没有 unknown/backoff
          { total: 3, running: 3, completed: 0, failed: 0, unknown: 0, backoff: 0 },
          // 连接维度:其中 2 台其实 ADB 不可达
          { all: 3, online: 1, offline: 1, adb_error: 1 },
        )}
      />,
    );
    expect(screen.getByTestId('kpi-running').textContent).toContain('3');
    // 执行维度确实没有失联/退避 —— 这不是 bug
    expect(screen.getByTestId('kpi-disconnected-backoff').textContent).toContain('0');
    // 但连接维度必须暴露出那 2 台
    expect(screen.getByTestId('kpi-link-abnormal').textContent).toContain('2');
    expect(screen.getByTestId('kpi-link-abnormal').querySelector('.text-warning')).toBeTruthy();
  });

  it('全部在线时连接异常为 0', () => {
    render(
      <PlanRunKpiGrid
        devices={makeDevices(
          { total: 4, running: 4, completed: 0, failed: 0, unknown: 0, backoff: 0 },
          { all: 4, online: 4 },
        )}
      />,
    );
    expect(screen.getByTestId('kpi-link-abnormal').textContent).toContain('0');
  });

  it('老后端无 by_link_status 时退回 ui_status 的 unknown', () => {
    render(
      <PlanRunKpiGrid
        devices={makeDevices({ total: 5, running: 3, completed: 0, failed: 0, unknown: 2, backoff: 0 })}
      />,
    );
    // 那时 by_status 基于 ui_status，unknown 恰好表示断连
    expect(screen.getByTestId('kpi-link-abnormal').textContent).toContain('2');
  });
});
