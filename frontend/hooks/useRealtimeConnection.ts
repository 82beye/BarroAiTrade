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

    // listeners를 connect()보다 먼저 등록해 재연결 시에도 유효하도록
    wsClient.on('open', () => {
      setConnected(true);
    });
    wsClient.on('message', (data: string) => {
      try {
        const message = JSON.parse(data);
        dispatchWSMessage(message);
      } catch (err) {
        console.error('WebSocket 메시지 파싱 실패:', err);
      }
    });
    wsClient.on('close', () => {
      setConnected(false);
    });

    wsClient
      .connect()
      .catch(() => {
        setError('WebSocket 연결 실패');
      });

    return () => {
      wsClient.close();
    };
  }, [setConnected, setError, setSystemStatus, dispatchWSMessage]);
}
