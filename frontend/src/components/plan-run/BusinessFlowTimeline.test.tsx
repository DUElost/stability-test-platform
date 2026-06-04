import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import BusinessFlowTimeline from './BusinessFlowTimeline';
import type {
  PlanRunEventsPayload,
  PlanRunTimeline,
} from '@/utils/api/types';

const timeline: PlanRunTimeline = {
  plan_run_id: 12,
  current_stage: 'patrol',
  stages: [
    {
      stage: 'init',
      status: 'completed',
      device_total: 8,
      device_succeeded: 8,
      device_failed: 0,
      duration_seconds: 240,
      steps: [
        {
          step_key: 'check_device',
          script_name: 'check_device',
          stage: 'init',
          sort_order: 0,
          device_total: 8,
          device_succeeded: 8,
          device_failed: 0,
          device_running: 0,
        },
      ],
    },
    {
      stage: 'patrol',
      status: 'running',
      device_total: 8,
      device_succeeded: 7,
      device_failed: 1,
      patrol_cycle_index: 142,
      patrol_active_devices: 7,
      patrol_interval_seconds: 60,
      steps: [
        {
          step_key: 'monkey_check',
          script_name: 'monkey_check',
          stage: 'patrol',
          sort_order: 0,
          device_total: 8,
          device_succeeded: 7,
          device_failed: 1,
          device_running: 0,
        },
      ],
    },
    {
      stage: 'teardown',
      status: 'pending',
      device_total: 0,
      device_succeeded: 0,
      device_failed: 0,
      steps: [],
    },
  ],
  triggered_at: '2026-05-08T12:00:00Z',
  triggered_by: 'tester@local',
  run_type: 'MANUAL',
  plan_name: '24h 烧机',
};

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

describe('BusinessFlowTimeline', () => {
  it('renders 3 stages, marks patrol as current and shows cycle info', () => {
    render(
      <BusinessFlowTimeline timeline={timeline} events={events} />,
    );
    expect(screen.getByTestId('stage-row-init')).toHaveTextContent('完成');
    expect(screen.getByTestId('stage-row-patrol')).toHaveTextContent('进行中');
    expect(screen.getByTestId('stage-row-patrol')).toHaveTextContent('#142');
    expect(screen.getByTestId('stage-row-teardown')).toHaveTextContent('等待');
    expect(screen.getByTestId('business-flow-timeline')).toHaveTextContent(
      '共 3 条',
    );
  });

  it('renders events with severity badges and stage chips', () => {
    render(
      <BusinessFlowTimeline timeline={timeline} events={events} />,
    );
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
      <BusinessFlowTimeline
        timeline={timeline}
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
    render(
      <BusinessFlowTimeline timeline={timeline} events={events} />,
    );
    // patrol stage filter shows count = 1
    expect(screen.getByTestId('event-filter-stage-patrol')).toHaveTextContent(
      '1',
    );
    // err severity filter shows count = 1
    expect(screen.getByTestId('event-filter-sev-err')).toHaveTextContent('1');
  });

  it('renders empty state when there are no events under filter', () => {
    render(
      <BusinessFlowTimeline
        timeline={timeline}
        events={{
          plan_run_id: 12,
          total: 0,
          events: [],
          facets: { by_stage: { all: 0 }, by_severity: { all: 0 } },
        }}
      />,
    );
    expect(screen.getByTestId('event-list')).toHaveTextContent(
      '该过滤条件下暂无事件',
    );
  });

  it('shows a truncation notice when events.total exceeds returned rows', () => {
    render(
      <BusinessFlowTimeline timeline={timeline} events={{ ...events, total: 150 }} />,
    );
    const notice = screen.getByTestId('event-truncation-notice');
    expect(notice).toHaveTextContent('150');
    expect(notice).toHaveTextContent('仅显示前 3 条');
  });

  it('hides the truncation notice when all events are shown', () => {
    render(<BusinessFlowTimeline timeline={timeline} events={events} />);
    expect(
      screen.queryByTestId('event-truncation-notice'),
    ).not.toBeInTheDocument();
  });

  it('lifts the stage filter when a left-column stage card is clicked', () => {
    const onStage = vi.fn();
    render(
      <BusinessFlowTimeline
        timeline={timeline}
        events={events}
        onStageFilterChange={onStage}
      />,
    );
    const patrolCard = screen
      .getByTestId('stage-row-patrol')
      .querySelector('button');
    fireEvent.click(patrolCard!);
    expect(onStage).toHaveBeenCalledWith('patrol');
  });
});
