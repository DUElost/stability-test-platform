import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import PlanRunKpiBar from './PlanRunKpiBar';
import type { PlanRunDevicesPayload } from '@/utils/api/types';

const devices: PlanRunDevicesPayload = {
  plan_run_id: 12,
  total: 48,
  by_status: { all: 48, running: 40, failed: 5, unknown: 2 },
  by_host: { 'host-101': 24, 'host-202': 24 },
  devices: [],
};

describe('PlanRunKpiBar', () => {
  it('renders device distribution (total/running/failed/unknown/hosts)', () => {
    render(
      <PlanRunKpiBar devices={devices} currentStage="patrol" patrolCycle={142} />,
    );
    expect(screen.getByTestId('kpi-total')).toHaveTextContent('48');
    expect(screen.getByTestId('kpi-running')).toHaveTextContent('40');
    expect(screen.getByTestId('kpi-failed')).toHaveTextContent('5');
    expect(screen.getByTestId('kpi-unknown')).toHaveTextContent('已断开');
    expect(screen.getByTestId('kpi-unknown')).toHaveTextContent('2');
    expect(screen.getByTestId('kpi-hosts')).toHaveTextContent('2');
  });

  it('falls back to 0 when a by_status key is absent', () => {
    render(
      <PlanRunKpiBar devices={{ ...devices, by_status: { all: 5, running: 5 } }} />,
    );
    expect(screen.getByTestId('kpi-failed')).toHaveTextContent('0');
    expect(screen.getByTestId('kpi-unknown')).toHaveTextContent('0');
  });

  it('renders current stage label and patrol cycle', () => {
    render(
      <PlanRunKpiBar devices={devices} currentStage="patrol" patrolCycle={142} />,
    );
    const stage = screen.getByTestId('kpi-stage');
    expect(stage).toHaveTextContent('PATROL');
    expect(stage).toHaveTextContent('#142');
  });

  it('tolerates missing devices (renders zeros, no crash)', () => {
    render(<PlanRunKpiBar />);
    expect(screen.getByTestId('kpi-total')).toHaveTextContent('0');
  });

  it('does not render an abnormal-rate KPI (it lives in WatcherSummaryCard)', () => {
    render(<PlanRunKpiBar devices={devices} />);
    expect(screen.queryByTestId('kpi-abnormal-rate')).not.toBeInTheDocument();
  });
});
