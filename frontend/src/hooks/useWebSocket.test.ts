import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useWebSocket } from './useWebSocket';

describe('useWebSocket', () => {
  const wsUrl = 'ws://localhost:8000/ws';

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('initializes with default state when disabled', () => {
    const { result } = renderHook(() => useWebSocket(wsUrl, { enabled: false }));

    expect(result.current.isConnected).toBe(false);
    expect(result.current.connectionStatus).toBe('disconnected');
    expect(result.current.lastMessage).toBe(null);
    expect(result.current.reconnectAttempt).toBe(0);
  });

  it('does not send messages when disconnected', () => {
    const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { result } = renderHook(() => useWebSocket(wsUrl, { enabled: false }));

    const testMsg = { action: 'ping' };

    act(() => {
      result.current.sendMessage(testMsg);
    });

    expect(consoleSpy).toHaveBeenCalledWith('[WS] Cannot send message, not connected');
    consoleSpy.mockRestore();
  });

  it('respects enabled option to disable connection', () => {
    const WebSocketMock = vi.fn();
    vi.stubGlobal('WebSocket', WebSocketMock);

    const { result } = renderHook(() => useWebSocket(wsUrl, { enabled: false }));

    expect(result.current.connectionStatus).toBe('disconnected');
    expect(WebSocketMock).not.toHaveBeenCalled();
  });

  it('does not connect when enabled is false and connect() is called', () => {
    const WebSocketMock = vi.fn();
    vi.stubGlobal('WebSocket', WebSocketMock);

    const { result } = renderHook(() => useWebSocket(wsUrl, { enabled: false }));

    expect(result.current.connectionStatus).toBe('disconnected');

    act(() => {
      result.current.connect();
    });

    // Should remain disconnected because enabled is false
    expect(result.current.connectionStatus).toBe('disconnected');
    expect(WebSocketMock).not.toHaveBeenCalled();
  });

  // Note: Additional tests for connection handling, message receiving,
  // and callbacks are omitted due to complexity with React hook testing
  // and WebSocket mocking. The component integration tests in
  // DeviceMonitorPanel.test.tsx cover the actual WebSocket functionality.
});
