import { useCallback, useEffect, useRef, useState } from 'react';

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
  const reconnectTimeout = useRef<NodeJS.Timeout>();

  const connect = useCallback(() => {
    ws.current = new WebSocket(url);

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
        const data = JSON.parse(event.data) as WebSocketMessage;
        setLastMessage(data);
      } catch (e) {
        console.error('Failed to parse WS message', e);
      }
    };
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      ws.current?.close();
      clearTimeout(reconnectTimeout.current);
    };
  }, [connect]);

  const sendMessage = useCallback((msg: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(msg));
    }
  }, []);

  return { isConnected, lastMessage, sendMessage };
};
