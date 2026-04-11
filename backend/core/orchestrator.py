"""
Orchestrator — BarroAiTrade 매매 시스템 오케스트레이터

asyncio.TaskGroup 기반으로 모든 서브시스템을 관리:
  - exit_monitor_task: 보유 포지션 청산 조건 모니터링
  - entry_monitor_task: 신규 진입 신호 처리
  - sync_task: 잔고/포지션 동기화
  - market_task: 시장 상태 업데이트
  - rescan_task: 주기적 종목 스캔

각 태스크는 독립적으로 재시작되며, 하나의 실패가 전체 시스템을 중단시키지 않음.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from backend.core.state import app_state
from backend.models.market import MarketType

logger = logging.getLogger(__name__)

_SYNC_INTERVAL_SEC = 10     # 잔고/포지션 동기화 주기
_MARKET_INTERVAL_SEC = 30   # 시장 상태 업데이트 주기
_RESCAN_INTERVAL_SEC = 3    # 신호 스캔 주기
_EXIT_INTERVAL_SEC = 1      # 청산 조건 체크 주기
_TASK_RESTART_DELAY = 5     # 태스크 재시작 대기


class TradingOrchestrator:
    """비동기 매매 오케스트레이터"""

    def __init__(self) -> None:
        self._running = False
        self._tasks: Dict[str, asyncio.Task] = {}
        self._executor: Optional[Any] = None  # OrderExecutor
        self._position_mgr: Optional[Any] = None  # PositionManager
        self._alert: Optional[Any] = None  # AlertService
        self._report: Optional[Any] = None  # ReportService

    # ── 라이프사이클 ──────────────────────────────────────────────────────────

    async def start(self, mode: str = "simulation", market: str = "stock") -> None:
        """매매 시스템 시작"""
        if self._running:
            logger.warning("Orchestrator 이미 실행 중")
            return

        self._running = True
        app_state.trading_state = "running"
        app_state.mode = mode
        app_state.market = market
        app_state.started_at = datetime.now()
        app_state.error_message = ""

        logger.info("Orchestrator 시작: mode=%s, market=%s", mode, market)

        # 서브시스템 초기화
        await self._init_subsystems(mode, market)

        # 백그라운드 태스크 시작
        task_defs = [
            ("exit_monitor", self._exit_monitor_loop),
            ("entry_monitor", self._entry_monitor_loop),
            ("sync", self._sync_loop),
            ("market", self._market_loop),
            ("rescan", self._rescan_loop),
        ]

        for name, coro_fn in task_defs:
            self._tasks[name] = asyncio.create_task(
                self._supervised_task(name, coro_fn),
                name=f"orchestrator_{name}",
            )

        # AlertService 알림
        if self._alert:
            await self._alert.on_system_start(mode, market)

        logger.info("Orchestrator 모든 태스크 시작 완료")

    async def stop(self, reason: str = "수동 중지") -> None:
        """매매 시스템 중지"""
        if not self._running:
            return

        logger.info("Orchestrator 중지 요청: %s", reason)
        self._running = False

        # 모든 태스크 취소
        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

        # OrderExecutor 중지
        if self._executor:
            await self._executor.stop()

        app_state.trading_state = "stopped"
        app_state.started_at = None

        if self._alert:
            await self._alert.on_system_stop(reason)

        logger.info("Orchestrator 중지 완료")

    async def force_close_all(self, reason: str = "강제청산") -> None:
        """보유 포지션 전량 강제청산"""
        if not app_state.risk_engine or not self._position_mgr:
            logger.warning("강제청산: 시스템 미초기화")
            return

        positions = self._position_mgr.get_positions()
        symbols = list(positions.keys())
        if not symbols:
            return

        logger.warning("강제청산 실행: %s symbols=%s", reason, symbols)
        if self._alert:
            await self._alert.on_force_close(symbols, reason)

        gateway = app_state.market_gateway
        if gateway:
            for symbol in symbols:
                pos = positions[symbol]
                try:
                    from backend.models.position import Order, OrderSide, OrderType
                    order = Order(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        quantity=pos.quantity,
                        market_type=pos.market_type,
                        strategy_id="force_close",
                        risk_approved=True,
                    )
                    result = await gateway.place_order(order)
                    if result:
                        self._position_mgr.on_order_filled(result, pos.name)
                        logger.info("강제청산 완료: %s", symbol)
                except Exception as e:
                    logger.error("강제청산 실패: %s — %s", symbol, e)

    # ── 서브태스크 루프 ───────────────────────────────────────────────────────

    async def _exit_monitor_loop(self) -> None:
        """보유 포지션 청산 조건 주기적 체크"""
        while self._running:
            try:
                if app_state.risk_engine and self._position_mgr:
                    positions = self._position_mgr.get_positions()

                    # 강제청산 체크
                    force_symbols = app_state.risk_engine.get_force_close_symbols(positions)
                    if force_symbols:
                        await self.force_close_all("리스크 한도 초과 또는 강제청산 시간")

                    # 개별 포지션 손익 체크
                    for symbol, pos in positions.items():
                        exit_signal = app_state.risk_engine.check_exit_conditions(pos)
                        if exit_signal:
                            action, reason, qty_pct = exit_signal
                            await self._execute_exit(symbol, pos, qty_pct, reason)

                await asyncio.sleep(_EXIT_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("exit_monitor 오류: %s", e)
                await asyncio.sleep(1)

    async def _entry_monitor_loop(self) -> None:
        """진입 신호 큐 소비"""
        while self._running:
            try:
                await asyncio.sleep(_RESCAN_INTERVAL_SEC)
            except asyncio.CancelledError:
                break

    async def _sync_loop(self) -> None:
        """잔고/포지션 동기화"""
        while self._running:
            try:
                gateway = app_state.market_gateway
                if gateway and self._position_mgr:
                    try:
                        balance = await gateway.get_balance()
                        self._position_mgr.sync_balance(balance)
                        app_state.risk_engine.update_total_value(balance.total_value)

                        # 현재가 업데이트
                        positions = self._position_mgr.get_positions()
                        if positions:
                            prices = await gateway.get_prices(list(positions.keys()))
                            self._position_mgr.update_prices(prices)

                            # AppState 동기화
                            app_state.positions = {
                                k: v.model_dump(mode="json")
                                for k, v in self._position_mgr.get_positions().items()
                            }
                            await app_state.broadcast_risk_status()
                    except Exception as e:
                        logger.warning("잔고 동기화 실패: %s", e)

                await asyncio.sleep(_SYNC_INTERVAL_SEC)
            except asyncio.CancelledError:
                break

    async def _market_loop(self) -> None:
        """시장 상태 업데이트"""
        while self._running:
            try:
                gateway = app_state.market_gateway
                if gateway:
                    try:
                        condition = await gateway.get_market_condition()
                        app_state.market_condition = condition
                        await app_state.broadcast("market_condition", condition)
                    except Exception as e:
                        logger.warning("시장 상태 업데이트 실패: %s", e)

                await asyncio.sleep(_MARKET_INTERVAL_SEC)
            except asyncio.CancelledError:
                break

    async def _rescan_loop(self) -> None:
        """주기적 종목 스캔 (watchlist 대상)"""
        while self._running:
            try:
                gateway = app_state.market_gateway
                if gateway and app_state.watchlist:
                    # watchlist 업데이트 브로드캐스트
                    await app_state.broadcast("watchlist_updated", {
                        "symbols": app_state.watchlist,
                        "timestamp": datetime.now().isoformat(),
                    })

                await asyncio.sleep(_RESCAN_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("rescan 오류: %s", e)
                await asyncio.sleep(3)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    async def _supervised_task(self, name: str, coro_fn) -> None:
        """태스크 감시자 — 예외 발생 시 자동 재시작"""
        while self._running:
            try:
                logger.info("태스크 시작: %s", name)
                await coro_fn()
                if self._running:
                    logger.warning("태스크 종료 (재시작 대기): %s", name)
                    await asyncio.sleep(_TASK_RESTART_DELAY)
            except asyncio.CancelledError:
                logger.info("태스크 취소: %s", name)
                break
            except Exception as e:
                logger.error("태스크 오류 [%s]: %s — %ds 후 재시작", name, e, _TASK_RESTART_DELAY)
                if self._alert:
                    await self._alert.on_error(f"orchestrator.{name}", str(e))
                if self._running:
                    await asyncio.sleep(_TASK_RESTART_DELAY)

    async def _execute_exit(self, symbol: str, pos: Any, qty_pct: float, reason: str) -> None:
        """포지션 청산 주문 실행"""
        gateway = app_state.market_gateway
        if not gateway or not self._executor:
            return

        try:
            from backend.models.position import Order, OrderSide, OrderType
            sell_qty = pos.quantity * qty_pct
            order = Order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=sell_qty,
                market_type=pos.market_type,
                strategy_id=f"exit_{pos.strategy_id}",
                risk_approved=True,
            )
            result = await self._executor.submit(
                order, gateway,
                risk_engine=None,  # 청산은 리스크 승인 불필요
                positions=self._position_mgr.get_positions(),
                balance=self._position_mgr.get_balance(),
            )
            if result and self._alert:
                pnl = (result.avg_price - pos.avg_price) * result.filled_quantity
                pnl_pct = (result.avg_price - pos.avg_price) / pos.avg_price if pos.avg_price > 0 else 0
                await self._alert.on_exit(
                    symbol=symbol,
                    name=pos.name,
                    price=result.avg_price,
                    quantity=result.filled_quantity,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    exit_type=reason.split(":")[0].strip(),
                )
        except Exception as e:
            logger.error("청산 주문 실패: %s — %s", symbol, e)

    async def _init_subsystems(self, mode: str, market: str) -> None:
        """서브시스템 초기화"""
        # OrderExecutor 초기화
        from backend.core.execution.order_executor import OrderExecutor
        self._executor = OrderExecutor()
        await self._executor.start(on_filled=self._on_order_filled)

        # PositionManager 초기화
        from backend.core.execution.position_manager import PositionManager
        self._position_mgr = PositionManager()

        # AlertService 초기화
        from backend.core.monitoring.alert_service import alert_service
        self._alert = alert_service

        # ReportService 초기화
        from backend.core.monitoring.report_service import report_service
        self._report = report_service

        # DB 초기화
        try:
            from backend.db.database import init_db
            await init_db()
        except Exception as e:
            logger.warning("DB 초기화 실패 (인메모리 모드): %s", e)

        logger.info("서브시스템 초기화 완료")

    async def _on_order_filled(self, result: Any) -> None:
        """주문 체결 콜백"""
        if self._position_mgr:
            self._position_mgr.on_order_filled(result)
            # AppState 동기화
            app_state.positions = {
                k: v.model_dump(mode="json")
                for k, v in self._position_mgr.get_positions().items()
            }
            await app_state.broadcast("position_updated", app_state.positions)


# 전역 인스턴스
orchestrator = TradingOrchestrator()
