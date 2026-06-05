import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import PatrolLogPanel from './PatrolLogPanel';
import type { PlanRunEventsPayload, EventSeverity, EventStage, EventCategory } from '@/utils/api/types';

const makeEvent = (
  id: number,
  severity: EventSeverity,
  ts: string,
  device?: string,
) => ({
  ts,
  stage: 'patrol' as EventStage,
  severity,
  category: 'log_signal' as EventCategory,
  title: `事件 ${id}`,
  description: '',
  job_id: id,
  device_id: id,
  device_serial: device ?? null,
  ref: null,
});

const EVENTS: PlanRunEventsPayload = {
  plan_run_id: 1,
  total: 3,
  facets: { by_stage: { all: 3 }, by_severity: { all: 3 } },
  events: [
    makeEvent(1, 'info', '2026-05-01T10:00:00Z', 'A1'),
    makeEvent(2, 'err',  '2026-05-01T10:05:00Z', 'A1'),
    makeEvent(3, 'warn', '2026-05-01T10:10:00Z', 'A2'),
  ],
};

describe('PatrolLogPanel', () => {
  it('renders log entries', () => {
    render(<PatrolLogPanel events={EVENTS} />);
    expect(screen.getAllByText(/事件/).length).toBeGreaterThan(0);
  });

  it('shows loading state', () => {
    render(<PatrolLogPanel isLoading />);
    expect(screen.getByText('加载中…')).toBeTruthy();
  });

  it('shows error state', () => {
    render(<PatrolLogPanel isError />);
    expect(screen.getByText('加载失败')).toBeTruthy();
  });

  it('shows empty state when no events', () => {
    const empty: PlanRunEventsPayload = {
      plan_run_id: 1,
      total: 0,
      facets: { by_stage: {}, by_severity: {} },
      events: [],
    };
    render(<PatrolLogPanel events={empty} />);
    expect(screen.getByText(/暂无巡检日志/)).toBeTruthy();
  });

  it('calls onSeverityChange when severity button clicked', () => {
    const fn = vi.fn();
    render(<PatrolLogPanel events={EVENTS} onSeverityChange={fn} />);
    fireEvent.click(screen.getByTestId('severity-btn-err'));
    expect(fn).toHaveBeenCalledWith('err');
  });

  it('shows pagination when totalPages > 1', () => {
    const manyEvents: PlanRunEventsPayload = { ...EVENTS, total: 200 };
    render(<PatrolLogPanel events={manyEvents} pageSize={50} page={2} />);
    expect(screen.getByTestId('patrol-prev-page')).toBeTruthy();
    expect(screen.getByTestId('patrol-next-page')).toBeTruthy();
  });

  it('shows cycle accordion', () => {
    render(<PatrolLogPanel events={EVENTS} />);
    expect(screen.getByTestId('cycle-accordion-0')).toBeTruthy();
  });
});
