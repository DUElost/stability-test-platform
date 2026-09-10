/**
 * Tests for useSocketIO hook — cookie-based Socket.IO handshake + refresh recovery.
 *
 * Because useSocketIO maintains module-level singletons (_dashSocket,
 * _authRecoveryInFlight), each test must reset them.  We do this by
 * importing the module once and directly nullifying the internal state
 * via a helper that reaches into the module's exported interface or
 * by re-importing after vi.resetModules().
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

// ---------------------------------------------------------------------------
// Fake socket factory
// ---------------------------------------------------------------------------

function createFakeSocket() {
  const listeners: Record<string, Array<(...args: any[]) => void>> = {};
  const socket: any = {
    connected: false,
    auth: {} as Record<string, string>,
    on: vi.fn((event: string, fn: (...args: any[]) => void) => {
      (listeners[event] ??= []).push(fn);
    }),
    emit: vi.fn(),
    emitLocal(event: string, ...args: any[]) {
      (listeners[event] ?? []).forEach((fn) => fn(...args));
    },
    connect: vi.fn(),
    disconnect: vi.fn(),
  };
  return socket;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useSocketIO — token auth', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset the module registry so each test gets a fresh singleton.
    vi.resetModules();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uses cookie credentials for the Socket.IO handshake', async () => {
    const refreshAccessToken = vi.fn().mockResolvedValue(true);
    const socket = createFakeSocket();

    const ioMock = vi.fn((_url: string, _opts: any) => {
      return socket;
    });

    vi.doMock('@/utils/auth', () => ({ refreshAccessToken }));
    vi.doMock('socket.io-client', () => ({ io: ioMock }));

    const { useSocketIO } = await import('@/hooks/useSocketIO');
    const { DASHBOARD_SUBSCRIPTION } = await import('@/config');
    renderHook(() => useSocketIO(DASHBOARD_SUBSCRIPTION));

    await waitFor(() => {
      expect(ioMock).toHaveBeenCalled();
    });

    expect(ioMock).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        withCredentials: true,
      }),
    );
  });

  it('recovers from Invalid token by refreshing cookie session and reconnecting', async () => {
    const refreshAccessToken = vi.fn().mockResolvedValue(true);

    const socket = createFakeSocket();
    const ioMock = vi.fn(() => socket);

    vi.doMock('@/utils/auth', () => ({ refreshAccessToken }));
    vi.doMock('socket.io-client', () => ({ io: ioMock }));

    const { useSocketIO } = await import('@/hooks/useSocketIO');
    const { DASHBOARD_SUBSCRIPTION } = await import('@/config');
    renderHook(() => useSocketIO(DASHBOARD_SUBSCRIPTION));

    // Wait for io(...) to have been called.
    await waitFor(() => {
      expect(ioMock).toHaveBeenCalled();
    });

    act(() => {
      socket.emitLocal('connect_error', new Error('Invalid token'));
    });

    await waitFor(
      () => {
        expect(refreshAccessToken).toHaveBeenCalledTimes(1);
      },
      { timeout: 3000 }
    );

    expect(socket.disconnect).toHaveBeenCalled();
    expect(socket.connect).toHaveBeenCalled();
  });

  it('resubscribes with room names (not refcounts) on reconnect (#1112)', async () => {
    const refreshAccessToken = vi.fn().mockResolvedValue(true);
    const socket = createFakeSocket();
    const ioMock = vi.fn(() => socket);

    vi.doMock('@/utils/auth', () => ({ refreshAccessToken }));
    vi.doMock('socket.io-client', () => ({ io: ioMock }));

    const { useSocketIO } = await import('@/hooks/useSocketIO');

    // Two subscribers on the same room → refcount 2; plus a distinct room.
    renderHook(() => useSocketIO('plan_run:5'));
    renderHook(() => useSocketIO('plan_run:5'));
    renderHook(() => useSocketIO('job:9'));

    await waitFor(() => {
      expect(ioMock).toHaveBeenCalled();
    });

    socket.emit.mockClear();

    // Simulate (re)connect while rooms are already tracked by refcount.
    act(() => {
      socket.emitLocal('connect');
    });

    const resubRooms = socket.emit.mock.calls
      .filter((c: any[]) => c[0] === 'subscribe')
      .map((c: any[]) => c[1]?.room);

    expect(resubRooms).toHaveLength(2);
    expect(new Set(resubRooms)).toEqual(new Set(['plan_run:5', 'job:9']));
    expect(resubRooms.every((r: unknown) => typeof r === 'string')).toBe(true);
  });

  it('recovers from Authentication required when access cookie is missing (#1119)', async () => {
    const refreshAccessToken = vi.fn().mockResolvedValue(true);
    const socket = createFakeSocket();
    const ioMock = vi.fn(() => socket);

    vi.doMock('@/utils/auth', () => ({ refreshAccessToken }));
    vi.doMock('socket.io-client', () => ({ io: ioMock }));

    const { useSocketIO } = await import('@/hooks/useSocketIO');
    const { DASHBOARD_SUBSCRIPTION } = await import('@/config');
    renderHook(() => useSocketIO(DASHBOARD_SUBSCRIPTION));

    await waitFor(() => {
      expect(ioMock).toHaveBeenCalled();
    });

    act(() => {
      socket.emitLocal('connect_error', new Error('Authentication required'));
    });

    await waitFor(() => {
      expect(refreshAccessToken).toHaveBeenCalledTimes(1);
    });
    expect(socket.disconnect).toHaveBeenCalled();
    expect(socket.connect).toHaveBeenCalled();
  });

  it('does not infinite-refresh when refresh fails for Authentication required (#1119)', async () => {
    const refreshAccessToken = vi.fn().mockResolvedValue(false);
    const socket = createFakeSocket();
    const ioMock = vi.fn(() => socket);

    vi.doMock('@/utils/auth', () => ({ refreshAccessToken }));
    vi.doMock('socket.io-client', () => ({ io: ioMock }));

    const { useSocketIO } = await import('@/hooks/useSocketIO');
    const { DASHBOARD_SUBSCRIPTION } = await import('@/config');
    const { result } = renderHook(() => useSocketIO(DASHBOARD_SUBSCRIPTION));

    await waitFor(() => {
      expect(ioMock).toHaveBeenCalled();
    });

    for (let i = 0; i < 5; i += 1) {
      act(() => {
        socket.emitLocal('connect_error', new Error('Authentication required'));
      });
      await waitFor(() => {
        expect(refreshAccessToken.mock.calls.length).toBeGreaterThan(0);
      });
      // Allow the in-flight promise to settle between attempts.
      await act(async () => {
        await Promise.resolve();
      });
    }

    // Bounded: at most _AUTH_RECOVERY_MAX (2) refresh attempts.
    expect(refreshAccessToken.mock.calls.length).toBeLessThanOrEqual(2);
    await waitFor(() => {
      expect(result.current.connectionStatus).toBe('error');
    });
  });

  it('does not refresh on non-recoverable handshake errors (#1119)', async () => {
    const refreshAccessToken = vi.fn().mockResolvedValue(true);
    const socket = createFakeSocket();
    const ioMock = vi.fn(() => socket);

    vi.doMock('@/utils/auth', () => ({ refreshAccessToken }));
    vi.doMock('socket.io-client', () => ({ io: ioMock }));

    const { useSocketIO } = await import('@/hooks/useSocketIO');
    const { DASHBOARD_SUBSCRIPTION } = await import('@/config');
    renderHook(() => useSocketIO(DASHBOARD_SUBSCRIPTION));

    await waitFor(() => {
      expect(ioMock).toHaveBeenCalled();
    });

    act(() => {
      socket.emitLocal('connect_error', new Error('Origin not allowed'));
    });

    await act(async () => {
      await Promise.resolve();
    });
    expect(refreshAccessToken).not.toHaveBeenCalled();
  });

  it('schedules a bounded reconnect when the refresh fails (#1279)', async () => {
    vi.useFakeTimers();
    try {
      const refreshAccessToken = vi.fn().mockResolvedValue(false);
      const socket = createFakeSocket();
      const ioMock = vi.fn(() => socket);

      vi.doMock('@/utils/auth', () => ({ refreshAccessToken }));
      vi.doMock('socket.io-client', () => ({ io: ioMock }));

      const { useSocketIO } = await import('@/hooks/useSocketIO');
      const { DASHBOARD_SUBSCRIPTION } = await import('@/config');
      renderHook(() => useSocketIO(DASHBOARD_SUBSCRIPTION));
      await act(async () => {
        await Promise.resolve();
      });
      expect(ioMock).toHaveBeenCalled();

      await act(async () => {
        socket.emitLocal('connect_error', new Error('Invalid token'));
      });
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(refreshAccessToken).toHaveBeenCalledTimes(1);
      expect(socket.disconnect).toHaveBeenCalled();
      // 刷新失败时不得停在断开态：先不上连，但要有排定的退避重连。
      expect(socket.connect).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_000);
      });
      expect(socket.connect).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('reconnects immediately when the browser goes back online (#1279)', async () => {
    vi.useFakeTimers();
    try {
      const refreshAccessToken = vi.fn().mockResolvedValue(false);
      const socket = createFakeSocket();
      const ioMock = vi.fn(() => socket);

      vi.doMock('@/utils/auth', () => ({ refreshAccessToken }));
      vi.doMock('socket.io-client', () => ({ io: ioMock }));

      const { useSocketIO } = await import('@/hooks/useSocketIO');
      const { DASHBOARD_SUBSCRIPTION } = await import('@/config');
      renderHook(() => useSocketIO(DASHBOARD_SUBSCRIPTION));
      await act(async () => {
        await Promise.resolve();
      });

      await act(async () => {
        socket.emitLocal('connect_error', new Error('Invalid token'));
      });
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(socket.connect).not.toHaveBeenCalled();

      // 网络恢复事件：立即重连，且取消已排定的退避重试。
      await act(async () => {
        window.dispatchEvent(new Event('online'));
      });
      expect(socket.connect).toHaveBeenCalledTimes(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(socket.connect).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
