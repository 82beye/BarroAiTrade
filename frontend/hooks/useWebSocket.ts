import { useEffect, useRef, useState } from 'react';
import { WebSocketClient } from '@/lib/api';

export function useWebSocket(path: string = '/ws/realtime') {
  const wsRef = useRef<WebSocketClient | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<any>(null);

  useEffect(() => {
    const client = new WebSocketClient(path);
    wsRef.current = client;

    client.connect()
      .then(() => {
        setIsConnected(true);

        // 메시지 수신
        client.on('message', (data) => {
          try {
            const parsed = JSON.parse(data);
            setLastMessage(parsed);
          } catch (err) {
            console.error('Failed to parse message:', err);
          }
        });
      })
      .catch((err) => {
        console.error('WebSocket connection failed:', err);
        setIsConnected(false);
      });

    return () => {
      client.close();
    };
  }, [path]);

  const send = (data: any) => {
    if (wsRef.current) {
      wsRef.current.send(data);
    }
  };

  return {
    isConnected,
    send,
    lastMessage,
  };
}
