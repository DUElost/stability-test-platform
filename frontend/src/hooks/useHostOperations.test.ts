import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useHostOperations } from '@/hooks/useHostOperations';

vi.mock('@/utils/api', () => ({
  api: {
    agentInstall: {
      trigger: vi.fn(),
    },
  },
}));

import { api } from '@/utils/api';

describe('useHostOperations', () => {
  beforeEach(() => {
    vi.mocked(api.agentInstall.trigger).mockReset();
  });

  it('starts batch install with concurrency and console_run_id', async () => {
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

    const { result } = renderHook(() => useHostOperations({ concurrency: 2 }));

    await act(async () => {
      await result.current.startInstallBatch([
        { hostId: 'a', label: 'host-a', agentInstalled: false },
        { hostId: 'b', label: 'host-b', agentInstalled: true },
      ]);
    });

    expect(result.current.panelOpen).toBe(true);
    expect(result.current.ops).toHaveLength(2);
    expect(result.current.ops[0].kind).toBe('install');
    expect(result.current.ops[1].kind).toBe('reinstall');
    expect(result.current.ops[0].consoleRunId).toBe('con-a');
    expect(result.current.ops[1].consoleRunId).toBe('con-b');
    expect(api.agentInstall.trigger).toHaveBeenCalledTimes(2);
  });

  it('marks failed when trigger rejects without 409 console id', async () => {
    vi.mocked(api.agentInstall.trigger).mockRejectedValueOnce({
      message: 'boom',
      response: { status: 500, data: { detail: 'server error' } },
    });

    const { result } = renderHook(() => useHostOperations({ concurrency: 1 }));

    await act(async () => {
      await result.current.startInstallBatch([
        { hostId: 'x', label: 'host-x', agentInstalled: false },
      ]);
    });

    expect(result.current.ops[0].status).toBe('failed');
    expect(result.current.ops[0].error).toContain('server error');
  });
});
