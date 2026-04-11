import { useEffect } from 'react';
import { api, WebSocketClient } from '@/lib/api';
import { useTradingStore } from '@/lib/store';

export function useRealtimeConnection() {
  const { setConnected, setError, setSystemStatus, dispatchWSMessage } =
    useTradingStore();

  useEffect(() => {
    // REST API 상태 조회
    const fetchStatus = async () => {
      try {
        const response = await api.getStatus();
        setSystemStatus(response.data);
      } catch (err) {
        console.error('API 연결 실패:', err);
      }
    };

    fetchStatus();

    // WebSocket 연결
    const wsClient = new WebSocketClient();
    wsClient
      .connect()
      .then(() => {
        setConnected(true);
        wsClient.on('message', (data: string) => {
          try {
            const message = JSON.parse(data);
            dispatchWSMessage(message);
          } catch (err) {
            console.error('WebSocket 메시지 파싱 실패:', err);
          }
        });
      })
      .catch(() => {
        setError('WebSocket 연결 실패');
      });

    return () => {
      wsClient.close();
    };
  }, [setConnected, setError, setSystemStatus, dispatchWSMessage]);
}
