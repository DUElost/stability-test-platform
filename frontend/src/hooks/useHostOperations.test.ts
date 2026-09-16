import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useHostOperations, type HotUpdateBatchResult } from '@/hooks/useHostOperations';

vi.mock('@/utils/api', () => ({
  api: {
    agentInstall: {
      trigger: vi.fn(),
      status: vi.fn(),
      cancel: vi.fn(),
    },
    hotUpdate: {
      trigger: vi.fn(),
    },
  },
}));

import { api } from '@/utils/api';

describe('useHostOperations', () => {
  beforeEach(() => {
    vi.mocked(api.agentInstall.trigger).mockReset();
    vi.mocked(api.agentInstall.status).mockReset();
    vi.mocked(api.agentInstall.cancel).mockReset();
    vi.mocked(api.hotUpdate.trigger).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('waits for console terminal before releasing concurrency slot', async () => {
    vi.mocked(api.agentInstall.trigger)
      .mockResolvedValueOnce({
        ok: true,
        host_id: 'a',
        log_path: '/var/log/stp/con-test.log',
        console_run_id: 'con-a',
        room: 'console:con-a',
        status: 'running',
        message: 'ok',
      })
      .mockResolvedValueOnce({
        ok: true,
        host_id: 'b',
        log_path: '/var/log/stp/con-test.log',
        console_run_id: 'con-b',
        room: 'console:con-b',
        status: 'running',
        message: 'ok',
      });

    // a finishes on 2nd poll; b finishes on 1st poll after its trigger
    vi.mocked(api.agentInstall.status)
      .mockResolvedValueOnce({
        host_id: 'a',
        log_path: '/var/log/stp/con-test.log',
        status: 'running',
        console_status: 'RUNNING',
        console_found: true,
      })
      .mockResolvedValueOnce({
        host_id: 'a',
        log_path: '/var/log/stp/con-test.log',
        status: 'succeeded',
        console_status: 'SUCCESS',
        console_found: true,
      })
      .mockResolvedValueOnce({
        host_id: 'b',
        log_path: '/var/log/stp/con-test.log',
        status: 'succeeded',
        console_status: 'SUCCESS',
        console_found: true,
      });

    const onTerminal = vi.fn();
    const { result } = renderHook(() =>
      useHostOperations({ concurrency: 1, pollMs: 10, onTerminal }),
    );

    await act(async () => {
      await result.current.startInstallBatch([
        { hostId: 'a', label: 'host-a', agentInstalled: false },
        { hostId: 'b', label: 'host-b', agentInstalled: true },
      ]);
    });

    expect(result.current.ops[0].status).toBe('success');
    expect(result.current.ops[1].status).toBe('success');
    // concurrency=1 → second trigger only after first terminal
    expect(api.agentInstall.trigger).toHaveBeenCalledTimes(2);
    const order = vi.mocked(api.agentInstall.trigger).mock.invocationCallOrder;
    const statusOrder = vi.mocked(api.agentInstall.status).mock.invocationCallOrder;
    expect(order[0]).toBeLessThan(statusOrder[0]);
    expect(order[1]).toBeGreaterThan(statusOrder[1]); // b triggered after a reached terminal
    expect(onTerminal).toHaveBeenCalledTimes(2);
  });

  it('marks failed when trigger rejects without 409 console id', async () => {
    vi.mocked(api.agentInstall.trigger).mockRejectedValueOnce({
      message: 'boom',
      response: { status: 500, data: { detail: 'server error' } },
    });

    const { result } = renderHook(() => useHostOperations({ concurrency: 1, pollMs: 10 }));

    await act(async () => {
      await result.current.startInstallBatch([
        { hostId: 'x', label: 'host-x', agentInstalled: false },
      ]);
    });

    expect(result.current.ops[0].status).toBe('failed');
    expect(result.current.ops[0].error).toContain('server error');
    expect(api.agentInstall.status).not.toHaveBeenCalled();
  });

  it('attaches 409 console_run_id and waits for terminal', async () => {
    vi.mocked(api.agentInstall.trigger).mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            message: 'install already in progress',
            console_run_id: 'con-existing',
          },
        },
      },
    });
    vi.mocked(api.agentInstall.status).mockResolvedValueOnce({
      host_id: 'y',
      log_path: '/var/log/stp/con-test.log',
      status: 'failed',
      console_status: 'FAILED',
      console_found: true,
    });

    const onTerminal = vi.fn();
    const { result } = renderHook(() =>
      useHostOperations({ concurrency: 1, pollMs: 10, onTerminal }),
    );

    await act(async () => {
      await result.current.startInstallBatch([
        { hostId: 'y', label: 'host-y', agentInstalled: true },
      ]);
    });

    expect(result.current.ops[0].consoleRunId).toBe('con-existing');
    expect(result.current.ops[0].status).toBe('failed');
    expect(onTerminal).toHaveBeenCalledWith(
      expect.objectContaining({ hostId: 'y', ok: false, status: 'FAILED' }),
    );
  });

  it('opens panel and tracks per-host hot-update progress', async () => {
    vi.mocked(api.hotUpdate.trigger)
      .mockResolvedValueOnce({
        ok: true,
        host_id: 1,
        message: 'ok',
        deps_refreshed: true,
        code_version: 'abc1234',
      })
      .mockRejectedValueOnce({
        response: { status: 502, data: { detail: 'ssh failed' } },
      });

    const onTerminal = vi.fn();
    const onProgress = vi.fn();
    const { result } = renderHook(() =>
      useHostOperations({ concurrency: 1, onTerminal }),
    );

    const captured: { batch: HotUpdateBatchResult | null } = { batch: null };
    await act(async () => {
      captured.batch = await result.current.startHotUpdateBatch(
        [
          { hostId: 'a', label: 'host-a' },
          { hostId: 'b', label: 'host-b' },
        ],
        {
          skipped: [{ hostId: 'c', label: 'host-c', error: '存在活跃 Job' }],
          onProgress,
        },
      );
    });

    expect(result.current.panelOpen).toBe(true);
    expect(result.current.ops).toEqual(expect.arrayContaining([
      expect.objectContaining({ hostId: 'c', kind: 'hot_update', status: 'skipped' }),
      expect.objectContaining({ hostId: 'a', kind: 'hot_update', status: 'success' }),
      expect.objectContaining({ hostId: 'b', kind: 'hot_update', status: 'failed', error: 'ssh failed' }),
    ]));
    expect(captured.batch?.succeeded).toHaveLength(1);
    expect(captured.batch?.failed).toHaveLength(1);
    expect(captured.batch?.skipped).toHaveLength(1);
    expect(onProgress).toHaveBeenCalledWith(1, 2);
    expect(onProgress).toHaveBeenCalledWith(2, 2);
    expect(onTerminal).toHaveBeenCalledWith(
      expect.objectContaining({ hostId: 'a', kind: 'hot_update', ok: true }),
    );
    expect(api.hotUpdate.trigger).toHaveBeenNthCalledWith(1, 'a', { abortRunningJobs: false });
  });

  it('passes abortRunningJobs and treats 409 as skipped conflict', async () => {
    vi.mocked(api.hotUpdate.trigger).mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            message: 'Host has active jobs',
            active_jobs: [{ id: 1 }, { id: 2 }],
            retry_after_seconds: 12,
          },
        },
      },
    });

    const { result } = renderHook(() => useHostOperations({ concurrency: 1 }));

    const captured: { batch: HotUpdateBatchResult | null } = { batch: null };
    await act(async () => {
      captured.batch = await result.current.startHotUpdateBatch([
        { hostId: 'h1', label: 'host-1', abortRunningJobs: true },
      ]);
    });

    expect(api.hotUpdate.trigger).toHaveBeenCalledWith('h1', { abortRunningJobs: true });
    expect(result.current.ops[0].status).toBe('skipped');
    expect(captured.batch?.skipped[0]).toEqual(expect.objectContaining({
      hostId: 'h1',
      httpStatus: 409,
      activeJobCount: 2,
      retryAfterSeconds: 12,
    }));
  });

  it('uses backend string detail for 409 conflicts', async () => {
    vi.mocked(api.hotUpdate.trigger).mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: 'Host is OFFLINE, hot-update requires ONLINE status' },
      },
    });

    const { result } = renderHook(() => useHostOperations({ concurrency: 1 }));
    const captured: { batch: HotUpdateBatchResult | null } = { batch: null };
    await act(async () => {
      captured.batch = await result.current.startHotUpdateBatch([
        { hostId: 'h2', label: 'host-2' },
      ]);
    });

    expect(result.current.ops[0].error).toBe(
      'Host is OFFLINE, hot-update requires ONLINE status',
    );
    expect(captured.batch?.skipped[0]?.error).toBe(
      'Host is OFFLINE, hot-update requires ONLINE status',
    );
  });

  it('cancelInstall resolves null when the backend accepted the cancel', async () => {
    vi.mocked(api.agentInstall.cancel).mockResolvedValueOnce({
      ok: true,
      host_id: 'h1',
      console_run_id: 'con-1',
      canceled: true,
      status: 'canceling',
      message: 'Cancel requested; the terminal state is recorded from the console.',
    });

    const { result } = renderHook(() => useHostOperations({ concurrency: 1, pollMs: 10 }));
    let ret: string | null = 'unset';
    await act(async () => {
      ret = await result.current.cancelInstall('h1');
    });

    expect(api.agentInstall.cancel).toHaveBeenCalledWith('h1');
    expect(ret).toBeNull();
  });

  it('cancelInstall surfaces the reason when the cancel was not initiated', async () => {
    vi.mocked(api.agentInstall.cancel).mockResolvedValueOnce({
      ok: false,
      host_id: 'h1',
      console_run_id: 'con-1',
      canceled: false,
      status: 'not_canceled',
      message: 'The run could not be canceled (already terminal).',
    });

    const { result } = renderHook(() => useHostOperations({ concurrency: 1, pollMs: 10 }));
    let ret: string | null = 'unset';
    await act(async () => {
      ret = await result.current.cancelInstall('h1');
    });

    expect(ret).toBe('The run could not be canceled (already terminal).');
  });

  it('cancelInstall extracts backend detail from a 409 (nothing running)', async () => {
    vi.mocked(api.agentInstall.cancel).mockRejectedValueOnce({
      message: 'Request failed with status code 409',
      response: {
        status: 409,
        data: {
          detail: {
            code: 'NO_INSTALL_IN_PROGRESS',
            message: 'Host h1 has no Agent installation in progress.',
          },
        },
      },
    });

    const { result } = renderHook(() => useHostOperations({ concurrency: 1, pollMs: 10 }));
    let ret: string | null = 'unset';
    await act(async () => {
      ret = await result.current.cancelInstall('h1');
    });

    expect(ret).toBe('Host h1 has no Agent installation in progress.');
  });
});
