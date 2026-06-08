import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DispatchGateCard, {
  gateElapsedSeconds,
  isGateStale,
} from './DispatchGateCard';
import type { PrecheckState } from '@/utils/api/types';

const precheckFixture: PrecheckState = {
  phase: 'verifying',
  started_at: '2026-05-08T11:00:00Z',
  completed_at: null,
  hosts: {
    'host-101': {
      status: 'pending',
      checked_at: null,
      synced_at: null,
      scripts: [],
      sync_attempts: 0,
      error: null,
    },
  },
  errors: [],
};

describe('DispatchGateCard stale banner', () => {
  const staleNow = new Date('2026-05-08T11:02:00Z').getTime();

  it('shows amber banner when gate active longer than 90s', () => {
    render(
      <DispatchGateCard
        precheck={precheckFixture}
        dispatchState={{
          status: 'queued',
          enqueued_at: '2026-05-08T11:00:00Z',
          started_at: null,
          completed_at: null,
          last_error: null,
        }}
        isTerminal={false}
        nowMs={staleNow}
      />,
    );
    expect(screen.getByTestId('dispatch-gate-stale-banner')).toBeInTheDocument();
    expect(screen.getByTestId('dispatch-gate-stale-banner')).toHaveTextContent('超过 90s');
  });

  it('hides banner when gate completed within threshold', () => {
    render(
      <DispatchGateCard
        precheck={{ ...precheckFixture, phase: 'ready' }}
        dispatchState={{
          status: 'completed',
          enqueued_at: '2026-05-08T11:00:00Z',
          started_at: '2026-05-08T11:00:05Z',
          completed_at: '2026-05-08T11:00:30Z',
          last_error: null,
        }}
        isTerminal={false}
        nowMs={staleNow}
      />,
    );
    expect(screen.queryByTestId('dispatch-gate-stale-banner')).not.toBeInTheDocument();
  });

  it('isGateStale respects enqueued_at vs started_at', () => {
    const elapsed = gateElapsedSeconds(
      { started_at: '2026-05-08T11:00:00Z', enqueued_at: '2026-05-08T10:58:00Z' },
      precheckFixture,
      staleNow,
    );
    expect(elapsed).toBe(120);
    expect(
      isGateStale(
        { status: 'running', started_at: '2026-05-08T11:00:00Z' },
        precheckFixture,
        false,
        staleNow,
      ),
    ).toBe(true);
  });

  it('falls back to precheck started_at before dispatch state is created', () => {
    render(
      <DispatchGateCard
        precheck={precheckFixture}
        dispatchState={null}
        isTerminal={false}
        nowMs={staleNow}
      />,
    );
    expect(screen.getByTestId('dispatch-gate-stale-banner')).toHaveTextContent(
      '派发门禁已运行 120s',
    );
    expect(isGateStale(null, precheckFixture, false, staleNow)).toBe(true);
  });

  it('shows retry button when precheck failed', () => {
    const onRetry = vi.fn();
    render(
      <DispatchGateCard
        precheck={{ ...precheckFixture, phase: 'failed', errors: ['sync_failed'] }}
        dispatchState={{
          status: 'failed',
          enqueued_at: '2026-05-08T11:00:00Z',
          started_at: '2026-05-08T11:00:05Z',
          completed_at: '2026-05-08T11:01:00Z',
          last_error: 'precheck:sync_failed',
        }}
        isTerminal={false}
        onRetryDispatch={onRetry}
      />,
    );
    fireEvent.click(screen.getByTestId('dispatch-gate-retry-button'));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('shows agent_offline host error and failed phase', () => {
    render(
      <DispatchGateCard
        precheck={{
          phase: 'failed',
          started_at: '2026-05-08T11:00:00Z',
          completed_at: '2026-05-08T11:00:30Z',
          final_result: 'failed',
          errors: ['agent_offline: host-202'],
          sync_max_attempts: 1,
          hosts: {
            'host-101': {
              status: 'ok',
              checked_at: '2026-05-08T11:00:10Z',
              synced_at: null,
              scripts: [],
              sync_attempts: 0,
              error: null,
            },
            'host-202': {
              status: 'failed',
              checked_at: '2026-05-08T11:00:11Z',
              synced_at: null,
              scripts: [],
              sync_attempts: 0,
              error: 'agent_offline',
            },
          },
        }}
        dispatchState={{
          status: 'failed',
          enqueued_at: '2026-05-08T11:00:00Z',
          started_at: '2026-05-08T11:00:05Z',
          completed_at: '2026-05-08T11:00:30Z',
          last_error: 'precheck:agent_offline: host-202',
        }}
        isTerminal={true}
      />,
    );
    expect(screen.getByTestId('dispatch-gate-card')).toBeInTheDocument();
    expect(screen.getAllByText(/agent_offline: host-202/).length).toBeGreaterThan(0);
    expect(screen.getByTestId('dispatch-gate-host-host-202')).toHaveTextContent(
      'agent_offline',
    );
  });

  it('shows mixed watcher failure detail with inactive host ids', () => {
    render(
      <DispatchGateCard
        precheck={{
          phase: 'failed',
          started_at: '2026-05-08T11:00:00Z',
          completed_at: '2026-05-08T11:00:30Z',
          final_result: 'failed',
          errors: ['watch激活与不激活的节点不能同时在一个计划中'],
          gate_failure: {
            code: 'MIXED_WATCHER_ACTIVITY',
            message: 'watch激活与不激活的节点不能同时在一个计划中',
            inactive_host_ids: ['host-101', 'host-203'],
          },
          hosts: {
            'host-101': {
              status: 'failed',
              checked_at: null,
              synced_at: null,
              scripts: [],
              sync_attempts: 0,
              error: 'watcher_inactive',
            },
          },
        }}
        dispatchState={{
          status: 'failed',
          enqueued_at: '2026-05-08T11:00:00Z',
          started_at: '2026-05-08T11:00:05Z',
          completed_at: '2026-05-08T11:00:30Z',
          last_error: 'precheck:MIXED_WATCHER_ACTIVITY',
        }}
        isTerminal={true}
      />,
    );
    expect(screen.getByText('watch激活与不激活的节点不能同时在一个计划中')).toBeInTheDocument();
    expect(screen.getByTestId('dispatch-gate-mixed-watcher-detail')).toHaveTextContent(
      '不激活节点ID：host-101, host-203',
    );
  });
});

describe('DispatchGateCard expanded auto-sync', () => {
  // dispatch still running (not completed) keeps isCompactReady=false, so the
  // collapse/expand region renders — the window where expanded sync is visible.
  const runningDispatch = {
    status: 'running',
    enqueued_at: '2026-05-08T11:00:00Z',
    started_at: '2026-05-08T11:00:05Z',
    completed_at: null,
    last_error: null,
  };

  // ready + every host ok + no errors → allHealthy true.
  const readyHealthy: PrecheckState = {
    ...precheckFixture,
    phase: 'ready',
    hosts: {
      'host-101': {
        status: 'ok',
        checked_at: '2026-05-08T11:00:10Z',
        synced_at: null,
        scripts: [],
        sync_attempts: 0,
        error: null,
      },
    },
  };

  it('auto-collapses host details when gate transitions verifying → ready', () => {
    const { rerender } = render(
      <DispatchGateCard
        precheck={precheckFixture}
        dispatchState={runningDispatch}
        isTerminal={false}
      />,
    );
    // verifying → not healthy → default expanded: per-host detail visible
    expect(screen.getByTestId('dispatch-gate-host-host-101')).toBeInTheDocument();
    expect(screen.queryByTestId('dispatch-gate-collapsed')).not.toBeInTheDocument();

    rerender(
      <DispatchGateCard
        precheck={readyHealthy}
        dispatchState={runningDispatch}
        isTerminal={false}
      />,
    );
    // allHealthy flips false → true → useEffect collapses automatically
    expect(screen.getByTestId('dispatch-gate-collapsed')).toBeInTheDocument();
    expect(
      screen.queryByTestId('dispatch-gate-host-host-101'),
    ).not.toBeInTheDocument();
  });

  it('auto-expands host details when a healthy gate later fails', () => {
    const { rerender } = render(
      <DispatchGateCard
        precheck={readyHealthy}
        dispatchState={runningDispatch}
        isTerminal={false}
      />,
    );
    // ready + healthy → default collapsed
    expect(screen.getByTestId('dispatch-gate-collapsed')).toBeInTheDocument();
    expect(
      screen.queryByTestId('dispatch-gate-host-host-101'),
    ).not.toBeInTheDocument();

    rerender(
      <DispatchGateCard
        precheck={{
          ...readyHealthy,
          phase: 'failed',
          errors: ['sync_failed'],
          hosts: {
            'host-101': {
              status: 'failed',
              checked_at: '2026-05-08T11:00:10Z',
              synced_at: null,
              scripts: [],
              sync_attempts: 0,
              error: 'sync_failed',
            },
          },
        }}
        dispatchState={runningDispatch}
        isTerminal={false}
      />,
    );
    // allHealthy flips true → false → useEffect expands automatically
    expect(screen.getByTestId('dispatch-gate-host-host-101')).toBeInTheDocument();
    expect(screen.queryByTestId('dispatch-gate-collapsed')).not.toBeInTheDocument();
  });
});
