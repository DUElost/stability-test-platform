/**
 * SocketIO hook — replaces useWebSocket with socket.io-client.
 *
 * Maintains a shared singleton connection to the /dashboard namespace.
 * Components subscribe to rooms (e.g. "job:123", "plan_run:5") and
 * receive typed events.
 *
 * The return interface mirrors useWebSocket for drop-in replacement.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { io, Socket } from 'socket.io-client';
import { API_BASE_URL } from '@/config';
import { ensureFreshAccessToken } from '@/utils/auth';
import { SOCKET_EVENT_NAMES } from '@/utils/socketEvents';

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

let _authRecoveryInFlight = false;

// 审计 Frontend #3: 旧实现 _dashSocket 永不释放 ── 用户登出 / 关闭所有订阅页面后
// socket 仍持有过期 token + 占用 reconnection 配额。
// Why: SPA 中长期保留共享连接是性能优化,但缺一个"全部 hook unmount 时优雅关闭"的兜底。
// How to apply:
//   - 每个 useSocketIO 调用进入 _hookRefcount,unmount 时 -1
//   - 0 时启动 30s idle timer (兼容 StrictMode 双挂载 / 页面跳转闪断)
//   - 期间任何新 hook 取消 timer
//   - 登出时 disconnectDashSocket() 强制清理
let _hookRefcount = 0;
let _idleDisconnectTimer: ReturnType<typeof setTimeout> | null = null;
const _IDLE_DISCONNECT_MS = 30_000;

function _notifyDashStatus(status: ConnectionStatus) {
  _dashStatus = status;
  _dashStatusListeners.forEach(fn => fn(status));
}

function _cancelIdleDisconnect() {
  if (_idleDisconnectTimer) {
    clearTimeout(_idleDisconnectTimer);
    _idleDisconnectTimer = null;
  }
}

function _scheduleIdleDisconnect() {
  _cancelIdleDisconnect();
  _idleDisconnectTimer = setTimeout(() => {
    _idleDisconnectTimer = null;
    if (_hookRefcount === 0) {
      disconnectDashSocket();
    }
  }, _IDLE_DISCONNECT_MS);
}

/**
 * 审计 Frontend #3: 强制断开共享 dashboard socket 并重置所有内部状态。
 *
 * 调用时机:
 *   - 用户主动登出 (Header / AppShell)
 *   - 401 refresh 失败兜底跳 /login (auth.ts / client.ts)
 *
 * Why: SocketIO 不能携带过期 token 继续重连,且重连配额是无限大,
 *      不显式 disconnect 会造成 server-side 反复鉴权失败 + log noise。
 */
export function disconnectDashSocket(): void {
  _cancelIdleDisconnect();
  const sock = _dashSocket;
  _dashSocket = null;
  _activeRooms.clear();
  _dashEventListeners.clear();
  _authRecoveryInFlight = false;
  _hookRefcount = 0;
  if (sock) {
    try {
      sock.removeAllListeners();
      sock.disconnect();
    } catch {
      // ignore — best-effort teardown
    }
  }
  _notifyDashStatus('disconnected');
}

function _getDashSocket(): Socket {
  if (_dashSocket?.connected) return _dashSocket;

  if (_dashSocket) {
    // already exists but disconnected — let reconnection handle it
    return _dashSocket;
  }

  // Use function-based auth so the token is always fresh at handshake time.
  // Static auth payload is stale the moment localStorage is updated.
  const socket = io(`${API_BASE_URL}/dashboard`, {
    path: '/socket.io',
    transports: ['websocket', 'polling'],
    autoConnect: true,
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 30000,
    auth: (cb: (payload: Record<string, string>) => void) => {
      void ensureFreshAccessToken(60).then((token) => {
        cb(token ? { token } : {});
      });
    },
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

    // If the server rejected our token as invalid, try a one-time refresh
    // and reconnect.  Guard against concurrent recovery loops.
    if (err.message === 'Invalid token' && !_authRecoveryInFlight) {
      _authRecoveryInFlight = true;
      socket.disconnect();
      void ensureFreshAccessToken(0).then((fresh) => {
        if (fresh) {
          (socket as any).auth = { token: fresh };
          socket.connect();
        }
        _authRecoveryInFlight = false;
      });
      return;
    }

    _notifyDashStatus('error');
  });

  // Wire up event forwarding for all known event types
  const EVENTS = [
    SOCKET_EVENT_NAMES.deviceUpdate,
    SOCKET_EVENT_NAMES.stepLog,
    SOCKET_EVENT_NAMES.stepUpdate,
    SOCKET_EVENT_NAMES.jobStatus,
    SOCKET_EVENT_NAMES.planRunStatus,
    SOCKET_EVENT_NAMES.runUpdate,
    SOCKET_EVENT_NAMES.taskUpdate,
    SOCKET_EVENT_NAMES.reportReady,
    SOCKET_EVENT_NAMES.jobUpdate,
    // ADR-0021 C5c — watcher 异常增量推送 (broadcast room: plan_run:{id})
    SOCKET_EVENT_NAMES.watcherSignal,
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
// Map WS URL patterns → room + event list
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
      events: [
        SOCKET_EVENT_NAMES.deviceUpdate,
        SOCKET_EVENT_NAMES.runUpdate,
        SOCKET_EVENT_NAMES.taskUpdate,
        SOCKET_EVENT_NAMES.reportReady,
        SOCKET_EVENT_NAMES.jobUpdate,
        SOCKET_EVENT_NAMES.planRunStatus,
      ],
    };
  }

  // /ws/jobs/{id}/logs
  const jobLogsMatch = url.match(/\/ws\/jobs\/(\d+)\/logs/);
  if (jobLogsMatch) {
    return { room: `job:${jobLogsMatch[1]}`, events: [SOCKET_EVENT_NAMES.stepLog, SOCKET_EVENT_NAMES.stepUpdate] };
  }

  // /ws/logs/{id}
  const logsMatch = url.match(/\/ws\/logs\/(\d+)/);
  if (logsMatch) {
    return { room: `run:${logsMatch[1]}`, events: [SOCKET_EVENT_NAMES.stepLog, SOCKET_EVENT_NAMES.stepUpdate] };
  }

  // /ws/plan-runs/{id}
  const planRunMatch = url.match(/\/ws\/plan-runs\/(\d+)/);
  if (planRunMatch) {
    return {
      room: `plan_run:${planRunMatch[1]}`,
      // ADR-0021 C5c: include watcher_signal so the frontend can invalidate
      // the WatcherSummary query as soon as the agent posts a log_signal.
      events: [SOCKET_EVENT_NAMES.jobStatus, SOCKET_EVENT_NAMES.planRunStatus, SOCKET_EVENT_NAMES.watcherSignal],
    };
  }

  // Fallback: global subscription
  return {
    room: null,
    events: [
      SOCKET_EVENT_NAMES.deviceUpdate,
      SOCKET_EVENT_NAMES.stepLog,
      SOCKET_EVENT_NAMES.stepUpdate,
      SOCKET_EVENT_NAMES.jobStatus,
      SOCKET_EVENT_NAMES.planRunStatus,
      SOCKET_EVENT_NAMES.runUpdate,
      SOCKET_EVENT_NAMES.taskUpdate,
      SOCKET_EVENT_NAMES.reportReady,
    ],
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

    // 审计 Frontend #3: 这个 hook 实例进入活跃集合,取消任何 pending 的 idle 断连。
    _hookRefcount += 1;
    _cancelIdleDisconnect();

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
      _hookRefcount = Math.max(0, _hookRefcount - 1);
      if (_hookRefcount === 0) {
        // 全部 hook unmount → 30s 后空闲断连
        // (覆盖 StrictMode 双挂载 + 页面切换间的瞬时全卸载)
        _scheduleIdleDisconnect();
      }
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
