import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import PlanRunKpiGrid from './PlanRunKpiGrid';
import type { PlanRunDevicesPayload } from '@/utils/api/types';

const makeDevices = (summary: Record<string, number>): PlanRunDevicesPayload => ({
  plan_run_id: 1,
  total: summary.total ?? 0,
  by_status: summary,
  by_host: {},
  devices: [],
} as unknown as PlanRunDevicesPayload);

describe('PlanRunKpiGrid', () => {
  it('renders all 6 cells', () => {
    render(<PlanRunKpiGrid devices={makeDevices({ total: 10, running: 3, completed: 5, failed: 2, unknown: 0, backoff: 0 })} currentStage="patrol" patrolCycle={4} />);
    expect(screen.getByTestId('kpi-total').textContent).toContain('10');
    expect(screen.getByTestId('kpi-running').textContent).toContain('3');
    expect(screen.getByTestId('kpi-completed').textContent).toContain('5');
    expect(screen.getByTestId('kpi-failed').textContent).toContain('2');
    expect(screen.getByTestId('kpi-disconnected-backoff')).toHaveTextContent('已断开/退避');
    expect(screen.getByTestId('kpi-disconnected-backoff').textContent).toContain('0');
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
    expect(cell.querySelector('.text-red-600')).toBeTruthy();
  });
});
