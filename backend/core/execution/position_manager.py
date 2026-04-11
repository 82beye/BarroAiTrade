"""
PositionManager — 포지션 딕셔너리 관리 및 손익 계산

포지션 생성/업데이트/청산과 잔고 동기화를 담당.
AppState.positions와 동기화하여 API 레이어에서 조회 가능하게 유지.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.models.market import MarketType
from backend.models.position import Balance, OrderResult, OrderSide, Position

logger = logging.getLogger(__name__)


class PositionManager:
    """포지션 및 잔고 관리"""

    def __init__(self) -> None:
        self._positions: Dict[str, Position] = {}
        self._balance: Optional[Balance] = None
        self._trade_history: List[Dict[str, Any]] = []

    # ── 포지션 CRUD ───────────────────────────────────────────────────────────

    def get_positions(self) -> Dict[str, Position]:
        return self._positions.copy()

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def on_order_filled(self, result: OrderResult, name: str = "", strategy_id: str = "") -> None:
        """주문 체결 이벤트 처리 — 포지션 생성/청산"""
        if result.side == OrderSide.BUY:
            self._open_position(result, name, strategy_id)
        else:
            self._close_position(result)

    def update_prices(self, prices: Dict[str, float]) -> None:
        """현재가 업데이트 및 평가손익 재계산"""
        for symbol, price in prices.items():
            if symbol in self._positions:
                pos = self._positions[symbol]
                unrealized = (price - pos.avg_price) * pos.quantity
                pnl_pct = (price - pos.avg_price) / pos.avg_price if pos.avg_price > 0 else 0.0
                self._positions[symbol] = pos.model_copy(update={
                    "current_price": price,
                    "unrealized_pnl": unrealized,
                    "pnl_pct": pnl_pct,
                })

    # ── 잔고 동기화 ───────────────────────────────────────────────────────────

    def get_balance(self) -> Optional[Balance]:
        return self._balance

    def sync_balance(self, balance: Balance) -> None:
        """게이트웨이에서 가져온 잔고 동기화"""
        self._balance = balance

    # ── 매매 내역 ─────────────────────────────────────────────────────────────

    def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._trade_history[-limit:]

    def get_daily_pnl(self) -> Tuple[float, float]:
        """오늘 실현 손익과 수익률 반환 (pnl, pnl_pct)"""
        total_invested = sum(
            t.get("entry_price", 0) * t.get("quantity", 0)
            for t in self._trade_history
        )
        total_pnl = sum(t.get("pnl", 0) for t in self._trade_history)
        pnl_pct = total_pnl / total_invested if total_invested > 0 else 0.0
        return total_pnl, pnl_pct

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _open_position(self, result: OrderResult, name: str, strategy_id: str) -> None:
        symbol = result.symbol
        if symbol in self._positions:
            # 추가 매수 — 평단가 계산
            existing = self._positions[symbol]
            total_qty = existing.quantity + result.filled_quantity
            avg_price = (
                existing.avg_price * existing.quantity + result.avg_price * result.filled_quantity
            ) / total_qty if total_qty > 0 else result.avg_price
            self._positions[symbol] = existing.model_copy(update={
                "quantity": total_qty,
                "avg_price": avg_price,
                "current_price": result.avg_price,
            })
            logger.info("포지션 추가매수: %s qty=%s avg=%.2f", symbol, total_qty, avg_price)
        else:
            self._positions[symbol] = Position(
                symbol=symbol,
                name=name or symbol,
                quantity=result.filled_quantity,
                avg_price=result.avg_price,
                current_price=result.avg_price,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                pnl_pct=0.0,
                market_type=result.market_type,
                entry_time=datetime.now(),
                strategy_id=strategy_id or result.order_id,
            )
            logger.info("포지션 오픈: %s qty=%s price=%.2f", symbol, result.filled_quantity, result.avg_price)

    def _close_position(self, result: OrderResult) -> None:
        symbol = result.symbol
        if symbol not in self._positions:
            logger.warning("청산 대상 포지션 없음: %s", symbol)
            return

        pos = self._positions[symbol]
        sold_qty = result.filled_quantity
        pnl = (result.avg_price - pos.avg_price) * sold_qty

        trade_record = {
            "symbol": symbol,
            "side": "sell",
            "entry_price": pos.avg_price,
            "exit_price": result.avg_price,
            "quantity": sold_qty,
            "pnl": pnl,
            "pnl_pct": (result.avg_price - pos.avg_price) / pos.avg_price if pos.avg_price > 0 else 0.0,
            "exit_time": datetime.now().isoformat(),
            "strategy_id": pos.strategy_id,
        }
        self._trade_history.append(trade_record)
        if len(self._trade_history) > 10000:
            self._trade_history = self._trade_history[-10000:]

        remaining_qty = pos.quantity - sold_qty
        if remaining_qty <= 0.0001:
            del self._positions[symbol]
            logger.info("포지션 전량 청산: %s pnl=%.0f", symbol, pnl)
        else:
            self._positions[symbol] = pos.model_copy(update={
                "quantity": remaining_qty,
                "realized_pnl": pos.realized_pnl + pnl,
            })
            logger.info("포지션 부분 청산: %s qty=%.4f→%.4f pnl=%.0f", symbol, pos.quantity, remaining_qty, pnl)
