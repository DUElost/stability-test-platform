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
    renderHook(() => useSocketIO('/ws/dashboard'));

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
    renderHook(() => useSocketIO('/ws/dashboard'));

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
});
