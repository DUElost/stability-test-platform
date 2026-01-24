import { useCallback, useEffect, useRef, useState } from 'react';

type TimeoutType = ReturnType<typeof setTimeout>;

interface WebSocketMessage<T = unknown> {
  type: string;
  payload: T;
}

interface UseWebSocketReturn<T = unknown> {
  isConnected: boolean;
  lastMessage: WebSocketMessage<T> | null;
  sendMessage: (msg: object) => void;
}

export const useWebSocket = <T = unknown>(url: string): UseWebSocketReturn<T> => {
  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WebSocketMessage<T> | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<TimeoutType | undefined>();
  const urlRef = useRef(url);

  // 更新 URL ref（避免依赖 url）
  useEffect(() => {
    urlRef.current = url;
  }, [url]);

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      return; // 已经连接，不重复连接
    }

    ws.current = new WebSocket(urlRef.current);

    ws.current.onopen = () => {
      setIsConnected(true);
      console.log('WS Connected');
    };

    ws.current.onclose = () => {
      setIsConnected(false);
      console.log('WS Disconnected, retrying in 3s...');
      reconnectTimeout.current = setTimeout(connect, 3000);
    };

    ws.current.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WebSocketMessage<T>;
        setLastMessage(data);
      } catch (e) {
        console.error('Failed to parse WS message', e);
      }
    };

    ws.current.onerror = (error) => {
      console.error('WS Error:', error);
    };
  }, []); // 空依赖数组，只创建一次

  useEffect(() => {
    connect();
    return () => {
      if (ws.current) {
        ws.current.close();
      }
      if (reconnectTimeout.current) {
        clearTimeout(reconnectTimeout.current);
      }
    };
  }, [connect]); // 依赖 connect

  const sendMessage = useCallback((msg: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(msg));
    }
  }, []);

  return { isConnected, lastMessage, sendMessage };
};
