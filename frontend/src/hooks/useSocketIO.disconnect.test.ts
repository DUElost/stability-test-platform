/**
 * 审计 Frontend #3 — 共享 dashboard socket 的显式断连 + hook 引用计数空闲断连。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('socket.io-client', () => {
  const created: any[] = [];
  function io(_url: string, _opts: any) {
    const listeners = new Map<string, Array<(...args: any[]) => void>>();
    const sock: any = {
      connected: false,
      _listeners: listeners,
      on(event: string, cb: any) {
        let arr = listeners.get(event);
        if (!arr) { arr = []; listeners.set(event, arr); }
        arr.push(cb);
        return sock;
      },
      emit: vi.fn(),
      connect: vi.fn(() => { sock.connected = true; }),
      disconnect: vi.fn(() => { sock.connected = false; }),
      removeAllListeners: vi.fn(() => listeners.clear()),
    };
    created.push(sock);
    return sock;
  }
  (io as any)._created = created;
  return { io, default: { io } };
});

// auth.ts uses axios; mock the post that ensureFreshAccessToken would not actually call without a token
vi.mock('@/utils/auth', () => ({
  ensureFreshAccessToken: vi.fn(async () => null),
  refreshAccessToken: vi.fn(async () => null),
}));

describe('disconnectDashSocket (审计 Frontend #3)', () => {
  beforeEach(async () => {
    vi.resetModules();
    const sioMock = await import('socket.io-client');
    ((sioMock as any).io._created as any[]).length = 0;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('idempotent when no socket has been created', async () => {
    const mod = await import('./useSocketIO');
    expect(() => mod.disconnectDashSocket()).not.toThrow();
  });

  it('tears down singleton + clears rooms/listeners on explicit call', async () => {
    const sioMock = await import('socket.io-client');
    const mod = await import('./useSocketIO');

    // simulate first hook ensuring socket
    // we cheat by calling the private helper via the public connect()
    const { renderHook } = await import('@testing-library/react');
    const { unmount } = renderHook(() => mod.useSocketIO('/ws/dashboard'));

    const created = (sioMock as any).io._created as any[];
    expect(created.length).toBe(1);
    const sock = created[0];

    mod.disconnectDashSocket();
    expect(sock.disconnect).toHaveBeenCalled();
    expect(sock.removeAllListeners).toHaveBeenCalled();

    // After disconnect a subsequent hook should re-create a fresh socket.
    unmount();
    renderHook(() => mod.useSocketIO('/ws/dashboard'));
    expect(created.length).toBe(2);
  });

  it('schedules idle disconnect 30s after last hook unmounts; cancels if new hook mounts', async () => {
    vi.useFakeTimers();
    const sioMock = await import('socket.io-client');
    const mod = await import('./useSocketIO');
    const { renderHook } = await import('@testing-library/react');

    const r1 = renderHook(() => mod.useSocketIO('/ws/dashboard'));
    const created = (sioMock as any).io._created as any[];
    expect(created.length).toBe(1);
    const sock = created[0];

    r1.unmount();
    // not yet disconnected — timer still running
    expect(sock.disconnect).not.toHaveBeenCalled();

    // new hook before 30s elapses cancels the idle timer
    vi.advanceTimersByTime(10_000);
    const r2 = renderHook(() => mod.useSocketIO('/ws/dashboard'));
    vi.advanceTimersByTime(25_000);
    expect(sock.disconnect).not.toHaveBeenCalled();

    // when this one unmounts and no other replaces it within 30s → disconnect fires
    r2.unmount();
    vi.advanceTimersByTime(30_000);
    expect(sock.disconnect).toHaveBeenCalled();
  });
});
