import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import PlanRunEventStream from './PlanRunEventStream';
import type { PlanRunEventsPayload } from '@/utils/api/types';

const events: PlanRunEventsPayload = {
  plan_run_id: 12,
  total: 3,
  events: [
    {
      ts: '2026-05-08T12:01:30Z',
      stage: 'init',
      severity: 'ok',
      category: 'step',
      title: 'check_device 已就绪',
      description: '8 台设备完成 init',
    },
    {
      ts: '2026-05-08T12:30:00Z',
      stage: 'patrol',
      severity: 'err',
      category: 'step',
      title: 'monkey_check 步骤失败',
      description: 'DEV-3064 连续失败 3 次,已进入退避',
      device_serial: 'DEV-3064',
      job_id: 3064,
    },
    {
      ts: '2026-05-08T12:31:00Z',
      stage: 'system',
      severity: 'warn',
      category: 'audit',
      title: '热更新阻塞',
      description: 'Host #2 拒绝热更新 — 存在 RUNNING Job',
    },
  ],
  facets: {
    by_stage: { all: 3, init: 1, patrol: 1, system: 1, trigger: 0, teardown: 0 },
    by_severity: { all: 3, ok: 1, err: 1, warn: 1, info: 0 },
  },
};

describe('PlanRunEventStream', () => {
  it('renders events with severity badges and stage chips', () => {
    render(<PlanRunEventStream events={events} />);
    const list = screen.getByTestId('event-list');
    expect(list).toHaveTextContent('check_device 已就绪');
    expect(list).toHaveTextContent('monkey_check 步骤失败');
    expect(list).toHaveTextContent('DEV-3064');
    expect(list).toHaveTextContent('Job #3064');
  });

  it('lifts stage and severity filter changes to parent', () => {
    const onStage = vi.fn();
    const onSev = vi.fn();
    render(
      <PlanRunEventStream
        events={events}
        onStageFilterChange={onStage}
        onSeverityFilterChange={onSev}
      />,
    );
    fireEvent.click(screen.getByTestId('event-filter-stage-patrol'));
    expect(onStage).toHaveBeenCalledWith('patrol');
    fireEvent.click(screen.getByTestId('event-filter-sev-err'));
    expect(onSev).toHaveBeenCalledWith('err');
  });

  it('shows facet counts on the filter buttons', () => {
    render(<PlanRunEventStream events={events} />);
    expect(screen.getByTestId('event-filter-stage-patrol')).toHaveTextContent('1');
    expect(screen.getByTestId('event-filter-sev-err')).toHaveTextContent('1');
  });

  it('renders empty state when there are no events under filter', () => {
    render(
      <PlanRunEventStream
        events={{
          plan_run_id: 12,
          total: 0,
          events: [],
          facets: { by_stage: { all: 0 }, by_severity: { all: 0 } },
        }}
      />,
    );
    expect(screen.getByTestId('event-list')).toHaveTextContent('该过滤条件下暂无事件');
  });

  it('expands a long event description on click', () => {
    render(<PlanRunEventStream events={events} />);
    const desc = screen.getByTestId('event-desc-2026-05-08T12:30:00Z-step');
    expect(desc).toHaveClass('line-clamp-2');
    fireEvent.click(desc);
    expect(desc).toHaveClass('whitespace-pre-wrap');
    expect(desc).not.toHaveClass('line-clamp-2');
  });

  it('paginates: shows range/total and fires onPageChange on next', () => {
    const onPageChange = vi.fn();
    render(
      <PlanRunEventStream
        events={{ ...events, total: 150 }}
        page={0}
        pageSize={50}
        onPageChange={onPageChange}
      />,
    );
    const pag = screen.getByTestId('event-pagination');
    expect(pag).toHaveTextContent('150');
    expect(pag).toHaveTextContent('1-50');
    expect(screen.getByTestId('event-page-prev')).toBeDisabled();
    fireEvent.click(screen.getByTestId('event-page-next'));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it('disables next on the last page', () => {
    render(
      <PlanRunEventStream
        events={{ ...events, total: 150 }}
        page={2}
        pageSize={50}
        onPageChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('event-page-next')).toBeDisabled();
    expect(screen.getByTestId('event-page-prev')).not.toBeDisabled();
  });
});
