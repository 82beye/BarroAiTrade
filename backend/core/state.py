"""
AppState — 공유 애플리케이션 상태 (WebSocket 브로드캐스트 포함)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class AppState:
    """전역 애플리케이션 상태 — API 레이어와 매매 엔진이 공유"""

    def __init__(self) -> None:
        self.trading_state: str = "idle"   # idle | running | stopped | error
        self.mode: str = "simulation"      # simulation | live
        self.market: str = "stock"         # stock | crypto
        self.started_at: Optional[datetime] = None
        self.error_message: str = ""

        self.positions: Dict[str, Any] = {}
        self.watchlist: List[Dict[str, Any]] = []
        self.market_condition: Optional[Dict[str, Any]] = None
        self.config: Optional[Dict[str, Any]] = None
        self.risk_status: Optional[Dict[str, Any]] = None

        self.trading_task: Optional[asyncio.Task] = None
        self._ws_clients: Set[WebSocket] = set()

    # ── WebSocket ──────────────────────────────────────────────────────────────

    def add_client(self, ws: WebSocket) -> None:
        self._ws_clients.add(ws)

    def remove_client(self, ws: WebSocket) -> None:
        self._ws_clients.discard(ws)

    async def broadcast(self, event_type: str, data: Any) -> None:
        """연결된 모든 WebSocket 클라이언트에 이벤트 전송"""
        if not self._ws_clients:
            return
        payload = json.dumps({"type": event_type, "data": data, "ts": datetime.now().isoformat()})
        dead: Set[WebSocket] = set()
        for ws in list(self._ws_clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._ws_clients.discard(ws)

    # ── 직렬화 헬퍼 ────────────────────────────────────────────────────────────

    def to_system_status(self) -> Dict[str, Any]:
        return {
            "state": self.trading_state,
            "mode": self.mode,
            "market": self.market,
            "position_count": len(self.positions),
            "total_pnl": sum(p.get("unrealized_pnl", 0) for p in self.positions.values()),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "error_message": self.error_message,
        }


app_state = AppState()
