import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DispatchCockpit } from './DispatchCockpit';
import type { ParameterProjection, PlanRunPreview } from '@/utils/api/types';

const projection: ParameterProjection = {
  layer: 'L1',
  context: { read_at: '2026-10-10T00:00:00.000000Z', authority: 'test' },
  steps: [],
  plan_settings: [{
    path: ['barrier_max_wait_seconds'],
    label: '屏障硬顶',
    meaning: '未设置时沿环境变量回落',
    state: 'env_fallback',
    source: null,
    value: null,
    sensitive: false,
    is_set: false,
    fallback_chain: 'Plan → STP_BARRIER_MAX_WAIT_SECONDS → 1800',
  }],
  watcher_policy: { note: '独立策略', items: [] },
  dispatch_decisions: [{ kind: 'device_slot', state: 'pending_dispatch', factors: ['心跳槽位'] }],
  safe_debug: { steps: [], plan_settings: {}, watcher_policy: {} },
};

const fingerprint = `stp-l1-v1:${'ab'.repeat(32)}`;

function renderCockpit(extra: { confirmationStale?: boolean; preview?: PlanRunPreview | null } = {}) {
  return render(
    <DispatchCockpit
      planName="Smoke Plan"
      executableStepCount={1}
      devices={[{ id: 1, serial: 'DEV-1', status: 'ONLINE' }]}
      capacityRows={[{
        hostId: 'h1',
        hostLabel: 'h1',
        selected: 1,
        effectiveSlots: 2,
        immediate: 1,
        queued: 0,
        healthStatus: 'ONLINE',
        healthReasons: [],
      }]}
      readyCount={1}
      blockedCount={0}
      warnings={[]}
      selectedHostActiveJobs={0}
      projection={projection}
      note=""
      preview={extra.preview === undefined ? {
        plan_id: 7,
        plan_name: 'Smoke Plan',
        device_ids: [1],
        device_count: 1,
        job_count: 1,
        total_steps: 1,
        lifecycle: { init: [], teardown: [] },
        confirmation_fingerprint: fingerprint,
        parameter_projection: projection,
      } : extra.preview}
      wallClock={{ averageSeconds: null, sampleCount: 0 }}
      recentRuns={[]}
      recentRunsLoading={false}
      duplicateMatch={null}
      wifiPools={[]}
      wifiPoolId={null}
      onWifiPoolChange={vi.fn()}
      onNoteChange={vi.fn()}
      onEditPlan={vi.fn()}
      onOpenRun={vi.fn()}
      onRemoveBlocked={vi.fn()}
      confirmationStale={extra.confirmationStale}
    />,
  );
}

describe('DispatchCockpit', () => {
  it('shows the projection, the fingerprint binding, and no planTiming copy', () => {
    renderCockpit();
    expect(screen.getByTestId('dispatch-parameter-list')).toBeInTheDocument();
    expect(screen.getByText('未设置时沿环境变量回落')).toBeInTheDocument();
    expect(screen.getByTestId('parameter-value')).not.toHaveTextContent('1800');
    expect(screen.getByTestId('dispatch-fingerprint-ready')).toHaveTextContent('已绑定本次预览的确认指纹');
    expect(screen.getByTestId('dispatch-fingerprint-ready')).toHaveTextContent('预览已生成并冻结 1 台设备');
    expect(screen.queryByRole('button', { name: '巡检时长说明' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '巡检周期说明' })).not.toBeInTheDocument();
    expect(screen.queryByText(fingerprint)).not.toBeInTheDocument();
  });

  it('shows the confirmation-changed prompt without a frozen preview', () => {
    renderCockpit({ confirmationStale: true, preview: null });
    expect(screen.getByTestId('plan-confirmation-changed')).toHaveTextContent(
      '计划配置已变化，请重新预览并确认',
    );
    expect(screen.queryByText(/预览已生成并冻结/)).not.toBeInTheDocument();
  });
});
