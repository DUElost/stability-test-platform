/**
 * SocketIO hook — replaces useWebSocket with socket.io-client.
 *
 * Maintains a shared singleton connection to the /dashboard namespace.
 * Components subscribe to rooms (e.g. "job:123", "workflow:5") and
 * receive typed events.
 *
 * The return interface mirrors useWebSocket for drop-in replacement.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { io, Socket } from 'socket.io-client';
import { API_BASE_URL } from '@/config';

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export interface SocketIOMessage<T = unknown> {
  type: string;
  seq?: number;
  timestamp?: string;
  payload: T;
}

// ---------------------------------------------------------------------------
// Singleton connection manager
// ---------------------------------------------------------------------------

let _dashSocket: Socket | null = null;
let _dashStatus: ConnectionStatus = 'disconnected';
const _dashStatusListeners = new Set<(s: ConnectionStatus) => void>();
const _dashEventListeners = new Map<string, Set<(data: any) => void>>();

function _notifyDashStatus(status: ConnectionStatus) {
  _dashStatus = status;
  _dashStatusListeners.forEach(fn => fn(status));
}

function _getDashSocket(): Socket {
  if (_dashSocket?.connected) return _dashSocket;

  if (_dashSocket) {
    // already exists but disconnected — let reconnection handle it
    return _dashSocket;
  }

  // First call: create the namespace socket
  const authPayload: Record<string, string> = {};

  // Try to get token synchronously from localStorage first for initial connect
  const storedToken = localStorage.getItem('access_token');
  if (storedToken) {
    authPayload.token = storedToken;
  }

  const socket = io(`${API_BASE_URL}/dashboard`, {
    path: '/socket.io',
    transports: ['websocket', 'polling'],
    autoConnect: true,
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 30000,
    auth: authPayload,
    forceNew: false,
  });

  _dashSocket = socket;
  _notifyDashStatus('connecting');

  socket.on('connect', () => {
    console.log('[SIO/dashboard] Connected');
    _notifyDashStatus('connected');
    // Re-subscribe to all active rooms after reconnect
    _activeRooms.forEach(room => {
      socket.emit('subscribe', { room });
    });
  });

  socket.on('disconnect', () => {
    console.log('[SIO/dashboard] Disconnected');
    _notifyDashStatus('disconnected');
  });

  socket.on('connect_error', (err) => {
    console.error('[SIO/dashboard] Connection error:', err.message);
    _notifyDashStatus('error');
  });

  // Wire up event forwarding for all known event types
  const EVENTS = [
    'device_update', 'step_log', 'step_update',
    'job_status', 'workflow_status',
    'run_update', 'task_update', 'report_ready',
    'job_update',
  ];
  for (const event of EVENTS) {
    socket.on(event, (data: any) => {
      const listeners = _dashEventListeners.get(event);
      if (listeners) {
        listeners.forEach(fn => fn(data));
      }
    });
  }

  return socket;
}

const _activeRooms = new Map<string, number>(); // room -> refcount

function _subscribeRoom(room: string): void {
  const count = _activeRooms.get(room) || 0;
  _activeRooms.set(room, count + 1);
  if (count === 0) {
    const socket = _getDashSocket();
    if (socket.connected) {
      socket.emit('subscribe', { room });
    }
  }
}

function _unsubscribeRoom(room: string): void {
  const count = _activeRooms.get(room) || 0;
  if (count <= 1) {
    _activeRooms.delete(room);
    const socket = _getDashSocket();
    if (socket.connected) {
      socket.emit('unsubscribe', { room });
    }
  } else {
    _activeRooms.set(room, count - 1);
  }
}

function _addEventListener(event: string, fn: (data: any) => void): void {
  let set = _dashEventListeners.get(event);
  if (!set) {
    set = new Set();
    _dashEventListeners.set(event, set);
  }
  set.add(fn);
}

function _removeEventListener(event: string, fn: (data: any) => void): void {
  const set = _dashEventListeners.get(event);
  if (set) {
    set.delete(fn);
    if (set.size === 0) {
      _dashEventListeners.delete(event);
    }
  }
}

// ---------------------------------------------------------------------------
// Map old WS URL patterns → room + event list
// ---------------------------------------------------------------------------

interface SubscriptionConfig {
  room: string | null;    // null = global (no room, receive all namespace events)
  events: string[];       // which events to listen for
}

function parseWsUrl(url: string): SubscriptionConfig {
  if (!url) return { room: null, events: [] };

  // /ws/dashboard or just dashboard-level
  if (url.includes('/ws/dashboard') || url.endsWith('/dashboard')) {
    return {
      room: null,
      events: ['device_update', 'run_update', 'task_update', 'report_ready', 'job_update'],
    };
  }

  // /ws/jobs/{id}/logs
  const jobLogsMatch = url.match(/\/ws\/jobs\/(\d+)\/logs/);
  if (jobLogsMatch) {
    return { room: `job:${jobLogsMatch[1]}`, events: ['step_log', 'step_update'] };
  }

  // /ws/logs/{id}
  const logsMatch = url.match(/\/ws\/logs\/(\d+)/);
  if (logsMatch) {
    return { room: `run:${logsMatch[1]}`, events: ['step_log', 'step_update'] };
  }

  // /ws/workflow-runs/{id}
  const wfMatch = url.match(/\/ws\/workflow-runs\/(\d+)/);
  if (wfMatch) {
    return { room: `workflow:${wfMatch[1]}`, events: ['job_status', 'workflow_status'] };
  }

  // Fallback: global subscription
  return {
    room: null,
    events: ['device_update', 'step_log', 'step_update', 'job_status', 'workflow_status', 'run_update', 'task_update', 'report_ready'],
  };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

interface UseSocketIOOptions {
  enabled?: boolean;
  authMode?: 'auto' | 'none';
  onConnect?: () => void;
  onDisconnect?: () => void;
  onMessage?: <T>(message: SocketIOMessage<T>) => void;
  reconnectConfig?: unknown; // accepted but ignored (SocketIO handles reconnection)
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
}

interface UseSocketIOReturn<T = unknown> {
  isConnected: boolean;
  connectionStatus: ConnectionStatus;
  lastMessage: SocketIOMessage<T> | null;
  sendMessage: (msg: object) => void;
  reconnectAttempt: number;
  connect: () => void;
  disconnect: () => void;
}

/**
 * Drop-in replacement for useWebSocket.
 *
 * Accepts the same `url` parameter (legacy WS URLs like `/ws/jobs/123/logs`)
 * and automatically maps them to SocketIO room subscriptions.
 */
export function useSocketIO<T = unknown>(
  url: string,
  options: UseSocketIOOptions = {}
): UseSocketIOReturn<T> {
  const { enabled = true, onConnect, onDisconnect, onMessage } = options;

  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>(_dashStatus);
  const [lastMessage, setLastMessage] = useState<SocketIOMessage<T> | null>(null);
  const callbacksRef = useRef({ onConnect, onDisconnect, onMessage });

  useEffect(() => {
    callbacksRef.current = { onConnect, onDisconnect, onMessage };
  }, [onConnect, onDisconnect, onMessage]);

  // Track connection status
  useEffect(() => {
    if (!enabled) return;

    const handler = (status: ConnectionStatus) => {
      setConnectionStatus(status);
      if (status === 'connected') callbacksRef.current.onConnect?.();
      if (status === 'disconnected') callbacksRef.current.onDisconnect?.();
    };
    _dashStatusListeners.add(handler);

    // Ensure socket is created
    _getDashSocket();
    setConnectionStatus(_dashStatus);

    return () => {
      _dashStatusListeners.delete(handler);
    };
  }, [enabled]);

  // Subscribe to room and events based on URL
  useEffect(() => {
    if (!enabled || !url) return;

    const config = parseWsUrl(url);

    if (config.room) {
      _subscribeRoom(config.room);
    }

    const eventHandler = (data: any) => {
      const msg = data as SocketIOMessage<T>;
      setLastMessage(msg);
      callbacksRef.current.onMessage?.(msg);
    };

    for (const event of config.events) {
      _addEventListener(event, eventHandler);
    }

    return () => {
      for (const event of config.events) {
        _removeEventListener(event, eventHandler);
      }
      if (config.room) {
        _unsubscribeRoom(config.room);
      }
    };
  }, [url, enabled]);

  const sendMessage = useCallback((msg: object) => {
    const socket = _getDashSocket();
    if (socket.connected) {
      socket.emit('message', msg);
    } else {
      console.warn('[SIO] Cannot send message, not connected');
    }
  }, []);

  const connect = useCallback(() => {
    const socket = _getDashSocket();
    if (!socket.connected) socket.connect();
  }, []);

  const disconnectFn = useCallback(() => {
    // no-op: shared socket, individual components don't disconnect
  }, []);

  return {
    isConnected: connectionStatus === 'connected',
    connectionStatus,
    lastMessage,
    sendMessage,
    reconnectAttempt: 0,
    connect,
    disconnect: disconnectFn,
  };
}

// Re-export types for compatibility
export type { SocketIOMessage as WebSocketMessage };
