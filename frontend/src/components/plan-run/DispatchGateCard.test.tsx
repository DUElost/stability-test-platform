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
});
