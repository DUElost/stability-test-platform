import { useCallback, useEffect, useRef, useState } from 'react';

type TimeoutType = ReturnType<typeof setTimeout>;

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
  /** 重连配置 */
  reconnectConfig?: Partial<ReconnectConfig>;
  /** 连接成功回调 */
  onConnect?: () => void;
  /** 连接断开回调 */
  onDisconnect?: () => void;
  /** 消息接收回调 */
  onMessage?: <T>(message: WebSocketMessage<T>) => void;
}

interface UseWebSocketReturn<T = unknown> {
  isConnected: boolean;
  lastMessage: WebSocketMessage<T> | null;
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
 */
export const useWebSocket = <T = unknown>(
  url: string,
  options: UseWebSocketOptions = {}
): UseWebSocketReturn<T> => {
  const {
    reconnectConfig: userReconnectConfig,
    onConnect,
    onDisconnect,
    onMessage,
  } = options;

  const reconnectConfig = { ...DEFAULT_RECONNECT_CONFIG, ...userReconnectConfig };

  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WebSocketMessage<T> | null>(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  const ws = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<TimeoutType | undefined>();
  const urlRef = useRef(url);
  const lastSeqRef = useRef<number>(0);
  const missedMessagesRef = useRef<WebSocketMessage<T>[]>([]);
  const isManualDisconnect = useRef(false);

  // 计算重连延迟
  const getReconnectDelay = useCallback((): number => {
    const delay =
      reconnectConfig.initialDelay *
      Math.pow(reconnectConfig.exponent, reconnectAttempt);
    return Math.min(delay, reconnectConfig.maxDelay);
  }, [reconnectAttempt, reconnectConfig]);

  // 更新 URL ref
  useEffect(() => {
    urlRef.current = url;
  }, [url]);

  const disconnect = useCallback(() => {
    isManualDisconnect.current = true;
    if (ws.current) {
      ws.current.close();
    }
    if (reconnectTimeout.current) {
      clearTimeout(reconnectTimeout.current);
    }
  }, []);

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      return;
    }

    isManualDisconnect.current = false;
    ws.current = new WebSocket(urlRef.current);

    ws.current.onopen = () => {
      setIsConnected(true);
      setReconnectAttempt(0);
      console.log('[WS] Connected');

      // 处理重连期间丢失的消息（如果服务器支持）
      if (missedMessagesRef.current.length > 0) {
        console.log(`[WS] Processing ${missedMessagesRef.current.length} missed messages`);
        missedMessagesRef.current.forEach((msg) => {
          setLastMessage(msg);
          onMessage?.(msg);
        });
        missedMessagesRef.current = [];
      }

      onConnect?.();
    };

    ws.current.onclose = (event) => {
      setIsConnected(false);
      onDisconnect?.();

      // 手动断开不重连
      if (isManualDisconnect.current) {
        console.log('[WS] Manually disconnected');
        return;
      }

      // 检查是否超过最大重试次数
      if (
        reconnectConfig.maxRetries > 0 &&
        reconnectAttempt >= reconnectConfig.maxRetries
      ) {
        console.error('[WS] Max reconnect attempts reached');
        return;
      }

      const delay = getReconnectDelay();
      console.log(`[WS] Disconnected, reconnecting in ${delay}ms (attempt ${reconnectAttempt + 1})`);

      setReconnectAttempt((prev) => prev + 1);
      reconnectTimeout.current = setTimeout(connect, delay);
    };

    ws.current.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WebSocketMessage<T>;

        // 检查消息序列号
        if (data.seq !== undefined) {
          if (data.seq !== lastSeqRef.current + 1 && lastSeqRef.current !== 0) {
            console.warn(`[WS] Message gap detected: expected ${lastSeqRef.current + 1}, got ${data.seq}`);
            // 这里可以实现消息回补逻辑
          }
          lastSeqRef.current = data.seq;
        }

        setLastMessage(data);
        onMessage?.(data);
      } catch (e) {
        console.error('[WS] Failed to parse message:', e);
      }
    };

    ws.current.onerror = (error) => {
      console.error('[WS] Error:', error);
    };
  }, [getReconnectDelay, reconnectAttempt, reconnectConfig.maxRetries, onConnect, onDisconnect, onMessage]);

  useEffect(() => {
    connect();
    return () => {
      disconnect();
    };
  }, [connect, disconnect]);

  const sendMessage = useCallback((msg: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(msg));
    } else {
      console.warn('[WS] Cannot send message, not connected');
    }
  }, []);

  return {
    isConnected,
    lastMessage,
    sendMessage,
    reconnectAttempt,
    connect,
    disconnect,
  };
};
