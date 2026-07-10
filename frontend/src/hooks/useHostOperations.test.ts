import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useHostOperations } from '@/hooks/useHostOperations';

vi.mock('@/utils/api', () => ({
  api: {
    agentInstall: {
      trigger: vi.fn(),
      status: vi.fn(),
    },
  },
}));

import { api } from '@/utils/api';

describe('useHostOperations', () => {
  beforeEach(() => {
    vi.mocked(api.agentInstall.trigger).mockReset();
    vi.mocked(api.agentInstall.status).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('waits for console terminal before releasing concurrency slot', async () => {
    vi.mocked(api.agentInstall.trigger)
      .mockResolvedValueOnce({
        ok: true,
        host_id: 'a',
        saq_key: 'install:a',
        console_run_id: 'con-a',
        room: 'console:con-a',
        status: 'running',
        message: 'ok',
      })
      .mockResolvedValueOnce({
        ok: true,
        host_id: 'b',
        saq_key: 'install:b',
        console_run_id: 'con-b',
        room: 'console:con-b',
        status: 'running',
        message: 'ok',
      });

    // a finishes on 2nd poll; b finishes on 1st poll after its trigger
    vi.mocked(api.agentInstall.status)
      .mockResolvedValueOnce({
        host_id: 'a',
        saq_key: 'install:a',
        status: 'active',
        console_status: 'RUNNING',
      })
      .mockResolvedValueOnce({
        host_id: 'a',
        saq_key: 'install:a',
        status: 'complete',
        console_status: 'SUCCESS',
        result: { ok: true, rc: 0, message: 'ok' },
      })
      .mockResolvedValueOnce({
        host_id: 'b',
        saq_key: 'install:b',
        status: 'complete',
        console_status: 'SUCCESS',
        result: { ok: true, rc: 0, message: 'ok' },
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
      saq_key: 'install:y',
      status: 'complete',
      console_status: 'FAILED',
      result: { ok: false, rc: 1, message: 'ansible exit 1' },
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
});
