import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ensureFreshAccessToken, upsertWsToken } from '@/utils/auth';

type TimeoutType = ReturnType<typeof setTimeout>;

/** WebSocket 连接状态 */
export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

/** WebSocket 消息格式 */
export interface WebSocketMessage<T = unknown> {
  type: string;
  /** 消息序列号，用于检测丢失消息 */
  seq?: number;
  timestamp?: string;
  payload: T;
}

/** 重连配置 */
export interface ReconnectConfig {
  /** 初始重连延迟（毫秒） */
  initialDelay: number;
  /** 最大重连延迟（毫秒） */
  maxDelay: number;
  /** 退避指数 */
  exponent: number;
  /** 最大重试次数，0 表示无限 */
  maxRetries: number;
}

/** 默认重连配置 */
const DEFAULT_RECONNECT_CONFIG: ReconnectConfig = {
  initialDelay: 1000,
  maxDelay: 30000,
  exponent: 2,
  maxRetries: 0, // 无限重试
};

interface UseWebSocketOptions {
  /** 是否启用 WebSocket */
  enabled?: boolean;
  /** 是否自动附加/刷新鉴权 token */
  authMode?: 'auto' | 'none';
  /** 重连配置 */
  reconnectConfig?: Partial<ReconnectConfig>;
  /** 连接成功回调 */
  onConnect?: () => void;
  /** 连接断开回调 */
  onDisconnect?: () => void;
  /** 消息接收回调 */
  onMessage?: <T>(message: WebSocketMessage<T>) => void;
  /** 重连间隔 */
  reconnectInterval?: number;
  /** 最大重连尝试次数 */
  maxReconnectAttempts?: number;
}

interface UseWebSocketReturn<T = unknown> {
  /** 是否已连接 */
  isConnected: boolean;
  /** 连接状态 */
  connectionStatus: ConnectionStatus;
  /** 最后接收的消息 */
  lastMessage: WebSocketMessage<T> | null;
  /** 发送消息 */
  sendMessage: (msg: object) => void;
  /** 当前重连尝试次数 */
  reconnectAttempt: number;
  /** 手动连接 */
  connect: () => void;
  /** 手动断开 */
  disconnect: () => void;
}

/**
 * 增强版 WebSocket Hook
 * 特性：
 * - 指数退避重连
 * - 消息序列号跟踪
 * - 连接状态管理
 * - 手动连接/断开控制
 * - 防止重复连接
 */
export const useWebSocket = <T = unknown>(
  url: string,
  options: UseWebSocketOptions = {}
): UseWebSocketReturn<T> => {
  const {
    enabled = true,
    authMode = 'auto',
    reconnectConfig: userReconnectConfig,
    onConnect,
    onDisconnect,
    onMessage,
  } = options;

  const reconnectConfig = useMemo(
    () => ({ ...DEFAULT_RECONNECT_CONFIG, ...userReconnectConfig }),
    [
      userReconnectConfig?.initialDelay,
      userReconnectConfig?.maxDelay,
      userReconnectConfig?.exponent,
      userReconnectConfig?.maxRetries,
    ]
  );

  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected');
  const [lastMessage, setLastMessage] = useState<WebSocketMessage<T> | null>(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  const ws = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<TimeoutType | undefined>();
  const urlReconnectTimeout = useRef<TimeoutType | undefined>();
  const urlRef = useRef(url);
  const lastSeqRef = useRef<number>(0);
  const missedMessagesRef = useRef<WebSocketMessage<T>[]>([]);
  const isManualDisconnect = useRef(false);
  const isConnecting = useRef(false);
  const reconnectAttemptRef = useRef(0);
  const connectSeqRef = useRef(0);

  const isConnected = connectionStatus === 'connected';

  // 稳定化的回调引用
  const callbacksRef = useRef({ onConnect, onDisconnect, onMessage });
  useEffect(() => {
    callbacksRef.current = { onConnect, onDisconnect, onMessage };
  }, [onConnect, onDisconnect, onMessage]);

  // 计算重连延迟
  const getReconnectDelay = useCallback((): number => {
    const delay =
      reconnectConfig.initialDelay *
      Math.pow(reconnectConfig.exponent, reconnectAttemptRef.current);
    return Math.min(delay, reconnectConfig.maxDelay);
  }, [reconnectConfig.exponent, reconnectConfig.initialDelay, reconnectConfig.maxDelay]);

  // 断开连接
  const disconnect = useCallback(() => {
    isManualDisconnect.current = true;
    isConnecting.current = false;
    if (reconnectTimeout.current) {
      clearTimeout(reconnectTimeout.current);
      reconnectTimeout.current = undefined;
    }
    if (urlReconnectTimeout.current) {
      clearTimeout(urlReconnectTimeout.current);
      urlReconnectTimeout.current = undefined;
    }
    if (ws.current) {
      // 只关闭连接，不清理事件处理器，让它们自然处理
      if (ws.current.readyState === WebSocket.OPEN || ws.current.readyState === WebSocket.CONNECTING) {
        ws.current.close();
      }
      ws.current = null;
    }
    setConnectionStatus('disconnected');
  }, []);

  // 建立连接
  const connect = useCallback(() => {
    if (
      !enabled ||
      !urlRef.current ||
      isConnecting.current ||
      ws.current?.readyState === WebSocket.OPEN ||
      ws.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    isConnecting.current = true;
    isManualDisconnect.current = false;
    setConnectionStatus('connecting');

    const connectSeq = ++connectSeqRef.current;

    const resolveWsUrl = async (): Promise<string> => {
      const baseUrl = urlRef.current || '';
      if (!baseUrl) return '';
      if (authMode === 'none') return baseUrl;

      const token = await ensureFreshAccessToken();
      if (!token) {
        return import.meta.env.PROD ? '' : baseUrl;
      }
      return upsertWsToken(baseUrl, token);
    };

    void (async () => {
      const resolvedUrl = await resolveWsUrl();
      if (connectSeq !== connectSeqRef.current || isManualDisconnect.current) {
        return;
      }
      if (!resolvedUrl) {
        isConnecting.current = false;
        setConnectionStatus('disconnected');
        return;
      }

      try {
        const socket = new WebSocket(resolvedUrl);
        ws.current = socket;

        socket.onopen = () => {
          isConnecting.current = false;
          setConnectionStatus('connected');
          reconnectAttemptRef.current = 0;
          setReconnectAttempt(0);
          console.log('[WS] Connected to', urlRef.current);

          // 处理重连期间丢失的消息
          if (missedMessagesRef.current.length > 0) {
            console.log(`[WS] Processing ${missedMessagesRef.current.length} missed messages`);
            missedMessagesRef.current.forEach((msg) => {
              setLastMessage(msg);
              callbacksRef.current.onMessage?.(msg);
            });
            missedMessagesRef.current = [];
          }

          callbacksRef.current.onConnect?.();
        };

        socket.onclose = (event) => {
          isConnecting.current = false;
          ws.current = null;
          setConnectionStatus('disconnected');
          callbacksRef.current.onDisconnect?.();

          // 手动断开不重连
          if (isManualDisconnect.current) {
            console.log('[WS] Manually disconnected');
            return;
          }

          // 正常关闭（code 1000）且不是手动断开，不重连
          if (event.code === 1000 && !isManualDisconnect.current) {
            console.log('[WS] Connection closed normally');
            return;
          }

          // 检查是否超过最大重试次数
          if (
            reconnectConfig.maxRetries > 0 &&
            reconnectAttemptRef.current >= reconnectConfig.maxRetries
          ) {
            console.error('[WS] Max reconnect attempts reached');
            setConnectionStatus('error');
            return;
          }

          const delay = getReconnectDelay();
          reconnectAttemptRef.current += 1;
          setReconnectAttempt(reconnectAttemptRef.current);
          console.log(`[WS] Disconnected (code: ${event.code}), reconnecting in ${delay}ms (attempt ${reconnectAttemptRef.current})`);

          reconnectTimeout.current = setTimeout(() => {
            if (!isManualDisconnect.current) {
              connect();
            }
          }, delay);
        };

        socket.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data) as WebSocketMessage<T>;

            // 检查消息序列号
            if (data.seq !== undefined) {
              if (data.seq !== lastSeqRef.current + 1 && lastSeqRef.current !== 0) {
                console.warn(`[WS] Message gap detected: expected ${lastSeqRef.current + 1}, got ${data.seq}`);
              }
              lastSeqRef.current = data.seq;
            }

            setLastMessage(data);
            callbacksRef.current.onMessage?.(data);
          } catch (e) {
            console.error('[WS] Failed to parse message:', e);
          }
        };

        socket.onerror = (error) => {
          console.error('[WS] Error:', error);
          setConnectionStatus('error');
          isConnecting.current = false;
        };
      } catch (error) {
        console.error('[WS] Failed to create connection:', error);
        setConnectionStatus('error');
        isConnecting.current = false;
      }
    })();
  }, [enabled, authMode, reconnectConfig.maxRetries, getReconnectDelay]);

  // 初始连接
  useEffect(() => {
    if (enabled) {
      connect();
    }
    return () => {
      disconnect();
    };
  }, [enabled, connect, disconnect]);

  // URL 变化时重新连接
  useEffect(() => {
    if (urlRef.current !== url) {
      urlRef.current = url;
      if (enabled) {
        // 重置手动断开状态，允许重连
        isManualDisconnect.current = false;
        disconnect();
        // 短暂延迟后重新连接，确保清理完成
        urlReconnectTimeout.current = setTimeout(() => {
          if (!isManualDisconnect.current) {
            connect();
          }
        }, 100);
      }
    }
    return () => {
      if (urlReconnectTimeout.current) {
        clearTimeout(urlReconnectTimeout.current);
        urlReconnectTimeout.current = undefined;
      }
    };
  }, [url, enabled, connect, disconnect]);

  const sendMessage = useCallback((msg: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(msg));
    } else {
      console.warn('[WS] Cannot send message, not connected');
      // Queue message for later if connection is available but not open
      if (ws.current?.readyState === WebSocket.CONNECTING) {
        missedMessagesRef.current.push({ type: 'QUEUED', payload: msg } as any);
      }
    }
  }, []);

  return {
    isConnected,
    connectionStatus,
    lastMessage,
    sendMessage,
    reconnectAttempt,
    connect,
    disconnect,
  };
};
