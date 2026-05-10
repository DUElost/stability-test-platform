/**
 * Tests for useSocketIO hook — token refresh before Socket.IO handshake.
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
    localStorage.clear();
    // Reset the module registry so each test gets a fresh singleton.
    vi.resetModules();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uses a function-based auth callback (not a static payload)', async () => {
    const ensureFreshAccessToken = vi.fn().mockResolvedValue('fresh-access-token');
    const socket = createFakeSocket();

    let capturedAuthCb: ((payload: Record<string, string>) => void) | null = null;
    const ioMock = vi.fn((_url: string, opts: any) => {
      // Simulate Socket.IO invoking the auth callback during handshake.
      if (typeof opts.auth === 'function') {
        capturedAuthCb = opts.auth;
        opts.auth((payload: Record<string, string>) => {
          socket.auth = payload;
        });
      }
      return socket;
    });

    vi.doMock('@/utils/auth', () => ({ ensureFreshAccessToken }));
    vi.doMock('socket.io-client', () => ({ io: ioMock }));

    const { useSocketIO } = await import('@/hooks/useSocketIO');
    renderHook(() => useSocketIO('/ws/dashboard'));

    await waitFor(() => {
      expect(ioMock).toHaveBeenCalled();
    });

    expect(capturedAuthCb).toBeInstanceOf(Function);
  });

  it('recovers from Invalid token by refreshing and reconnecting', async () => {
    // ensureFreshAccessToken: first call from auth cb, second from recovery.
    const ensureFreshAccessToken = vi.fn()
      .mockResolvedValueOnce('first-token')
      .mockResolvedValueOnce('refreshed-token');

    const socket = createFakeSocket();
    const ioMock = vi.fn((_url: string, opts: any) => {
      // Simulate Socket.IO invoking the auth callback during handshake.
      if (typeof opts.auth === 'function') {
        opts.auth((payload: Record<string, string>) => {
          socket.auth = payload;
        });
      }
      return socket;
    });

    vi.doMock('@/utils/auth', () => ({ ensureFreshAccessToken }));
    vi.doMock('socket.io-client', () => ({ io: ioMock }));

    const { useSocketIO } = await import('@/hooks/useSocketIO');
    renderHook(() => useSocketIO('/ws/dashboard'));

    // Wait for io(...) to have been called.
    await waitFor(() => {
      expect(ioMock).toHaveBeenCalled();
    });

    // The auth callback runs synchronously inside ioMock, so
    // ensureFreshAccessToken should already have been called once.
    expect(ensureFreshAccessToken).toHaveBeenCalledTimes(1);

    // Now emit the Invalid token connect_error.
    act(() => {
      socket.emitLocal('connect_error', new Error('Invalid token'));
    });

    // The recovery path should call ensureFreshAccessToken a second time.
    await waitFor(
      () => {
        expect(ensureFreshAccessToken).toHaveBeenCalledTimes(2);
      },
      { timeout: 3000 }
    );

    expect(socket.disconnect).toHaveBeenCalled();
    expect(socket.connect).toHaveBeenCalled();
  });
});
