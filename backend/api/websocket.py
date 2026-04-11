"""
WebSocket 핸들러 — 실시간 이벤트 스트림

연결: ws://localhost:8000/ws/realtime

클라이언트 → 서버:
  {"type": "ping"}
  {"type": "subscribe", "channels": ["positions", "signals", "risk"]}
  {"type": "unsubscribe", "channels": ["signals"]}

서버 → 클라이언트 이벤트:
  system_status, position_update, entry_signal, exit_signal,
  risk_alert, market_condition, audit_event
"""
from __future__ import annotations

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from backend.core.state import app_state

logger = logging.getLogger(__name__)


async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    app_state.add_client(websocket)
    logger.info("WebSocket 클라이언트 연결: %s", websocket.client)

    # 연결 직후 현재 상태 전송
    await websocket.send_json({"type": "system_status", "data": app_state.to_system_status()})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            # subscribe/unsubscribe는 향후 채널 필터링 구현 시 활용
    except WebSocketDisconnect:
        logger.info("WebSocket 클라이언트 연결 해제")
    finally:
        app_state.remove_client(websocket)
