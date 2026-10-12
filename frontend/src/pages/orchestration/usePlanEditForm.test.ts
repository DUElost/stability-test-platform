import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from '@/utils/api';
import type { Plan } from '@/utils/api';
import { usePlanEditForm } from './usePlanEditForm';

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  promise: vi.fn(),
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => toast,
}));

vi.mock('@/utils/api', async () => {
  const actual = await vi.importActual<typeof import('@/utils/api')>('@/utils/api');
  return {
    ...actual,
    fetchAllPlans: vi.fn().mockResolvedValue([]),
    api: {
      plans: {
        get: vi.fn(),
        update: vi.fn(),
        create: vi.fn(),
        list: vi.fn().mockResolvedValue({ items: [], total: 0 }),
        listSpecialties: vi.fn().mockResolvedValue([{ key: 'SPEC', display_name: '专项' }]),
        parameterProjection: vi.fn().mockResolvedValue(undefined),
        draftParameterProjection: vi.fn().mockResolvedValue(undefined),
      },
      projects: { listActive: vi.fn().mockResolvedValue([]) },
      suites: { list: vi.fn().mockResolvedValue([]) },
      scripts: { list: vi.fn().mockResolvedValue([]) },
    },
  };
});

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return createElement(
    MemoryRouter,
    null,
    createElement(QueryClientProvider, { client }, children),
  );
}

const basePlan: Plan = {
  id: 42,
  name: 'U6 Plan',
  description: 'desc',
  specialty_key: 'SPEC',
  patrol_interval_seconds: 120,
  timeout_seconds: 3600,
  barrier_timeout_seconds: 600,
  barrier_max_wait_seconds: null,
  auto_archive_interval_seconds: null,
  watcher_policy: {
    enabled: true,
    on_unavailable: 'degraded',
    batch_interval_seconds: 5,
    custom_keep: 'KEEP_ME',
  },
  next_plan_id: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  steps: [
    {
      id: 1,
      step_key: 'step_init_1',
      script_name: 'check_device',
      script_version: '1.0.2',
      stage: 'init',
      sort_order: 0,
      timeout_seconds: 30,
      stall_seconds: null,
      retry: 0,
      enabled: true,
      params: { secret: 'RAW_FACT', nested: { token: 'KEEP' } },
    },
  ],
};

describe('usePlanEditForm U6', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.plans.get).mockResolvedValue(basePlan);
    vi.mocked(api.plans.update).mockResolvedValue({
      ...basePlan,
      updated_at: '2026-01-01T01:00:00Z',
      barrier_max_wait_seconds: 900,
    });
  });

  async function readyHook() {
    const hook = renderHook(() => usePlanEditForm(42), { wrapper });
    await waitFor(() => expect(hook.result.current.planLoading).toBe(false));
    await waitFor(() => expect(hook.result.current.name).toBe('U6 Plan'));
    return hook;
  }

  it('从真实 Plan 加载设置，不把 barrier 硬顶 null 预填成默认数字', async () => {
    const { result } = await readyHook();
    expect(result.current.barrierMaxWaitSeconds).toBeNull();
    expect(result.current.barrierTimeoutSeconds).toBe(600);
    expect(result.current.watcherPolicy).toMatchObject({
      enabled: true,
      custom_keep: 'KEEP_ME',
    });
  });

  it('只改 Plan 设置时省略 steps，并发送 null/{} 与 expected_updated_at', async () => {
    const { result } = await readyHook();
    act(() => {
      result.current.handlePlanSettingsChange({
        patrolIntervalSeconds: result.current.patrolIntervalSeconds,
        timeoutSeconds: result.current.timeoutSeconds,
        barrierTimeoutSeconds: result.current.barrierTimeoutSeconds,
        barrierMaxWaitSeconds: 900,
        autoArchiveIntervalSeconds: null,
        watcherPolicy: {
          ...(result.current.watcherPolicy ?? {}),
          enabled: false,
        },
      });
    });
    expect(result.current.isDirty).toBe(true);

    await act(async () => {
      await result.current.handleSave();
    });

    expect(api.plans.update).toHaveBeenCalledTimes(1);
    const payload = vi.mocked(api.plans.update).mock.calls[0][1];
    expect(payload).toMatchObject({
      expected_updated_at: '2026-01-01T00:00:00Z',
      barrier_max_wait_seconds: 900,
      watcher_policy: expect.objectContaining({
        enabled: false,
        custom_keep: 'KEEP_ME',
        batch_interval_seconds: 5,
      }),
    });
    expect(payload).not.toHaveProperty('steps');
    expect(payload).not.toHaveProperty('patrol_interval_seconds');
  });

  it('清空 watcher 发送空对象；清空硬顶发送 null', async () => {
    vi.mocked(api.plans.get).mockResolvedValue({
      ...basePlan,
      barrier_max_wait_seconds: 1800,
    });
    const { result } = await readyHook();
    expect(result.current.barrierMaxWaitSeconds).toBe(1800);
    act(() => {
      result.current.handlePlanSettingsChange({
        patrolIntervalSeconds: 120,
        timeoutSeconds: 3600,
        barrierTimeoutSeconds: 600,
        barrierMaxWaitSeconds: null,
        autoArchiveIntervalSeconds: null,
        watcherPolicy: {},
      });
    });
    await act(async () => {
      await result.current.handleSave();
    });
    const payload = vi.mocked(api.plans.update).mock.calls[0][1];
    expect(payload.watcher_policy).toEqual({});
    expect(payload.barrier_max_wait_seconds).toBeNull();
  });

  it('改步骤时保留完整未改步骤 params/时序字段', async () => {
    const { result } = await readyHook();
    act(() => {
      result.current.setSelectedStepKey('step_init_1');
    });
    await waitFor(() => expect(result.current.selectedStep?.step_id).toBe('step_init_1'));
    act(() => {
      result.current.handleStepUpdate({
        ...result.current.selectedStep!,
        retry: 2,
      });
    });
    await act(async () => {
      await result.current.handleSave();
    });
    const payload = vi.mocked(api.plans.update).mock.calls[0][1];
    expect(payload.steps).toEqual([
      expect.objectContaining({
        step_key: 'step_init_1',
        retry: 2,
        params: { secret: 'RAW_FACT', nested: { token: 'KEEP' } },
        timeout_seconds: 30,
      }),
    ]);
  });

  it('409 冲突保留草稿并提示，不丢本地修改', async () => {
    vi.mocked(api.plans.update).mockRejectedValueOnce(
      new ApiError('CONFLICT', '版本冲突', {
        status: 409,
        details: { code: 'OPTIMISTIC_LOCK', message: '远端已更新' },
      }),
    );
    const { result } = await readyHook();
    act(() => {
      result.current.handlePlanSettingsChange({
        patrolIntervalSeconds: 120,
        timeoutSeconds: 3600,
        barrierTimeoutSeconds: 600,
        barrierMaxWaitSeconds: 1200,
        autoArchiveIntervalSeconds: null,
        watcherPolicy: result.current.watcherPolicy,
      });
    });
    await act(async () => {
      await result.current.handleSave();
    });
    expect(toast.error).toHaveBeenCalled();
    expect(result.current.barrierMaxWaitSeconds).toBe(1200);
    expect(result.current.isDirty).toBe(true);
  });

  it('草稿投影请求使用表单设置，而非冻结 basePlan', async () => {
    const { result } = await readyHook();
    act(() => {
      result.current.handlePlanSettingsChange({
        patrolIntervalSeconds: 120,
        timeoutSeconds: 3600,
        barrierTimeoutSeconds: 111,
        barrierMaxWaitSeconds: 222,
        autoArchiveIntervalSeconds: 333,
        watcherPolicy: { enabled: false },
      });
    });
    await waitFor(() =>
      expect(api.plans.draftParameterProjection).toHaveBeenCalledWith(
        expect.objectContaining({
          barrier_timeout_seconds: 111,
          barrier_max_wait_seconds: 222,
          auto_archive_interval_seconds: 333,
          watcher_policy: { enabled: false },
        }),
      ),
    );
  });
});
