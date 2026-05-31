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
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.state import app_state
from backend.models.market import MarketType

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

logger = logging.getLogger(__name__)

_SYNC_INTERVAL_SEC = 10          # 잔고/포지션 동기화 주기
_MARKET_INTERVAL_SEC = 30        # 시장 상태 업데이트 주기
_RESCAN_INTERVAL_SEC = 3         # 신호 스캔 주기
_EXIT_INTERVAL_SEC = 1           # 청산 조건 체크 주기
_TASK_RESTART_DELAY = 5          # 태스크 재시작 대기
_DAILY_SCAN_INTERVAL_SEC = 3600  # 당일 스캔 주기 (1시간)
_SUPERTREND_INTERVAL_SEC = 300   # 슈퍼트렌드 5분봉 진입/청산 평가 주기 (5분)
_SUPERTREND_UNIVERSE_MAX = 80    # 슈퍼트렌드 스캔 유니버스 종목 상한

# ── 슈퍼트렌드 주문 배선 (2026-06-01) — ⚠️ LEGACY / 비활성 ─────────────────────
# 이 orchestrator 경로의 자동주문 배선은 운영 봇(scripts/run_telegram_bot.py)이
# 사용하지 않는 헛배선이다. 실 운영 슈퍼트렌드 자동매매는
# backend/core/supertrend_auto_trader.py (SupertrendAutoTrader) 로 일원화됨(2026-06-01).
#
# orchestrator 는 API 경로(backend/api/routes/trading.py → orchestrator.start)로 여전히
# 살아있으므로, 본 배선이 활성(True)이면 텔레그램 봇 자동매매와 같은 신호로 '중복 매수'
# 위험이 있다. 따라서 False 로 고정해 signal-only(알림만)로 회귀시킨다.
#   - 비활성 시 _supertrend_enter() 및 _supertrend_cycle 청산 자동주문 분기가 호출되지 않음.
#   - 추출된 배선 코드 스냅샷: backend/legacy/supertrend_orchestrator_wiring.py.bak
_SUPERTREND_AUTO_TRADE = False   # ⚠️ LEGACY 비활성 — 자동매매는 supertrend_auto_trader 로 일원화
_SUPERTREND_MAX_POSITIONS = 10   # 슈퍼트렌드 동시 보유 상한
_SUPERTREND_ALLOC_PCT = 0.08     # 종목당 가용예수금 배분 (예수금 80% ÷ 10종목 = 8%)
_SUPERTREND_MIN_SCORE = 0.0      # 진입 최소 신호점수 (0 = 모든 신호 허용)


def _record_balance_snapshot(balance: Any, today: date, position_count: int = 0) -> None:
    """balance_history.json에 오늘 잔고 스냅샷 추가 (당일 중복 시 덮어쓰기).

    프론트엔드 BalancePoint 인터페이스: {date, cash, eval_total, total, position_count}
    """
    history_path = _DATA_DIR / "balance_history.json"
    try:
        if history_path.exists():
            data: list = json.loads(history_path.read_text(encoding="utf-8"))
        else:
            data = []
    except Exception:
        data = []

    date_str = today.isoformat()
    total = float(getattr(balance, "total_value", 0))
    cash = float(getattr(balance, "available_cash", getattr(balance, "cash", 0)))
    eval_total = total - cash
    point = {
        "date": date_str,
        "total": total,
        "cash": cash,
        "eval_total": eval_total,
        "position_count": position_count,
    }
    # 당일 기존 항목 교체 또는 추가
    data = [p for p in data if p.get("date") != date_str]
    data.append(point)
    data.sort(key=lambda p: p["date"])

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class TradingOrchestrator:
    """비동기 매매 오케스트레이터"""

    def __init__(self) -> None:
        self._running = False
        self._tasks: Dict[str, asyncio.Task] = {}
        self._executor: Optional[Any] = None  # OrderExecutor
        self._position_mgr: Optional[Any] = None  # PositionManager
        self._alert: Optional[Any] = None  # AlertService
        self._report: Optional[Any] = None  # ReportService
        # 진입 주문의 strategy_id 를 체결 콜백까지 전달하기 위한 symbol→strategy_id 맵.
        #   place_order/OrderResult 에 strategy_id 가 없어(_on_order_filled 가 유실),
        #   슈퍼트렌드 청산 식별(strategy_id startswith "supertrend")이 깨지는 것을 방지.
        self._pending_strategy_ids: Dict[str, str] = {}

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
            ("supertrend", self._supertrend_loop),
            ("daily_report", self._daily_report_loop),
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
        _balance_history_date: Optional[date] = None

        while self._running:
            try:
                gateway = app_state.market_gateway
                if gateway and self._position_mgr:
                    try:
                        balance = await gateway.get_balance()
                        self._position_mgr.sync_balance(balance)
                        if app_state.risk_engine:
                            app_state.risk_engine.update_total_value(balance.total_value)
                            _, daily_pnl_pct = self._position_mgr.get_daily_pnl()
                            app_state.risk_engine.update_daily_pnl(daily_pnl_pct)

                        # 현재가 업데이트
                        positions = self._position_mgr.get_positions()

                        # 잔고 히스토리 — 하루 1회 스냅샷
                        today = date.today()
                        if _balance_history_date != today:
                            try:
                                _record_balance_snapshot(balance, today, len(positions))
                                _balance_history_date = today
                            except Exception as hist_err:
                                logger.warning("잔고 히스토리 기록 실패: %s", hist_err)
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
        """주기적 종목 스캔 (watchlist 대상) + Telegram 전송"""
        last_scan_time: Optional[float] = None

        while self._running:
            try:
                gateway = app_state.market_gateway
                if gateway and app_state.watchlist:
                    # watchlist 업데이트 브로드캐스트
                    await app_state.broadcast("watchlist_updated", {
                        "symbols": app_state.watchlist,
                        "timestamp": datetime.now().isoformat(),
                    })

                    # 첫 실행 또는 1시간 경과 시 스캔 + Telegram 전송
                    now = asyncio.get_running_loop().time()
                    if last_scan_time is None or (now - last_scan_time) >= _DAILY_SCAN_INTERVAL_SEC:
                        logger.info("당일 분석 스캔 시작: %d종목", len(app_state.watchlist))
                        try:
                            from datetime import time as _dtime
                            from backend.core.scanner import SignalScanner
                            from backend.core.strategy.f_zone import FZoneParams
                            from backend.core.strategy.blue_line import BlueLineParams
                            # BAR-OPS-09 Phase 2/3: 변동성 필터 운영 경로 적용 — ATR% < 3.5% 차단 (저변동·고가주).
                            # BAR-OPS-09 Phase 8e/8f: 진입 시간 게이트 — 14:00 이후 운영 신규 진입 차단 (장 후반 청산 여유 부족 손실 방지).
                            # 2026-05-29: gold_zone 1m+0.035 일관화(제안1) 원복 — 격자 백테스트상 근거 없음(1m+0.035=신호 전멸). gold default 유지.
                            # 2026-05-31: 슈퍼트렌드 전용 검증 모드 — 사용자 요청으로 SignalScanner
                            #   전 전략(sf_zone/f_zone/gold_zone) 일시 비활성, _supertrend_loop 만 운용.
                            #   슈퍼트렌드 검증 완료 후 아래 enabled_strategies override 한 줄을 제거해 복원.
                            scanner = SignalScanner(
                                gateway,
                                f_zone_params=FZoneParams(min_atr_pct=0.035, entry_time_cutoff=_dtime(14, 0)),
                                blue_line_params=BlueLineParams(min_atr_pct=0.035, entry_time_cutoff=_dtime(14, 0)),
                                enabled_strategies={"sf_zone": False, "f_zone": False, "gold_zone": False},
                            )
                            signals = await scanner.scan(app_state.watchlist)
                            last_scan_time = now
                            logger.info("당일 분석 스캔 완료: %d개 신호", len(signals))
                            if self._alert:
                                await self._alert.on_daily_scan_result(signals)
                        except Exception as scan_err:
                            logger.error("당일 분석 스캔 실패: %s", scan_err)

                await asyncio.sleep(_RESCAN_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("rescan 오류: %s", e)
                await asyncio.sleep(3)

    async def _supertrend_loop(self) -> None:
        """슈퍼트렌드 5분봉 진입/청산 평가 (시그널+로깅+알림, 실주문 없음).

        BAR-OPS — 슈퍼트렌드 운영 배선 (2026-05-31):
          1) 진입: 당일 순위 유니버스(RankUniverseProvider, 실패 시 watchlist fallback)
             → SupertrendScanner 5분봉 상승 추세전환 신호 산출 → app_state 저장 + 알림.
          2) 청산: 보유 포지션 중 strategy_id=supertrend 진입분을 SupertrendExitWatcher
             가 추적 → 하락 추세전환 시 청산 시그널 산출 → 알림.

        signal-only: 실제 매수/매도 주문은 송출하지 않는다. 슈퍼트렌드 진입 주문 경로가
        운영에 별도 합의되기 전까지 관찰·알림 단계로 운용 (기존 가격기반 ExitEngine/
        HoldingEvaluator·RiskEngine 청산과 독립). 청산 시그널을 실주문으로 승격하려면
        _execute_exit() 를 호출하도록 후속 합의 후 연결.
        """
        last_run: Optional[float] = None
        oauth = self._build_native_oauth()  # None 가능 (settings 미설정 시 watchlist fallback)

        while self._running:
            try:
                gateway = app_state.market_gateway
                if gateway:
                    now = asyncio.get_running_loop().time()
                    if last_run is None or (now - last_run) >= _SUPERTREND_INTERVAL_SEC:
                        await self._supertrend_cycle(gateway, oauth)
                        last_run = now
                await asyncio.sleep(_SUPERTREND_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("supertrend_loop 오류: %s", e)
                await asyncio.sleep(_SUPERTREND_INTERVAL_SEC)

    async def _supertrend_cycle(self, gateway: Any, oauth: Any) -> None:
        """슈퍼트렌드 진입 스캔 + 보유분 청산 평가 1회 실행."""
        from backend.core.scanner import (
            RankUniverseProvider,
            SupertrendExitWatcher,
            SupertrendScanner,
        )

        # 1) 유니버스 — 당일 순위 합집합 (실패/미설정 시 watchlist fallback)
        universe: List[str] = []
        if oauth is not None:
            try:
                provider = RankUniverseProvider(oauth)
                universe = await provider.fetch_universe(max_symbols=_SUPERTREND_UNIVERSE_MAX)
            except Exception as e:
                logger.warning("슈퍼트렌드 유니버스 조회 실패 — watchlist fallback: %s", e)
        if not universe:
            universe = list(app_state.watchlist)
        if not universe:
            return

        # 2) 진입 스캔 (5분봉 상승 추세전환)
        try:
            scanner = SupertrendScanner(gateway)
            entry_signals = await scanner.scan(universe)
            app_state.supertrend_signals = [
                {
                    "symbol": s.symbol, "name": s.name, "price": s.price,
                    "score": s.score, "reason": s.reason,
                    "timestamp": s.timestamp.isoformat(),
                }
                for s in entry_signals
            ]
            if entry_signals:
                logger.info("슈퍼트렌드 진입 신호 %d건: %s",
                            len(entry_signals), [s.symbol for s in entry_signals])
                if self._alert:
                    for s in entry_signals:
                        try:
                            await self._alert.on_signal(s)
                        except Exception:
                            pass
                # 진입 주문 배선 — 점수 내림차순(스캐너가 이미 정렬)으로 매수 송출.
                if _SUPERTREND_AUTO_TRADE:
                    await self._supertrend_enter(gateway, entry_signals)
        except Exception as e:
            logger.error("슈퍼트렌드 진입 스캔 실패: %s", e)

        # 3) 청산 평가 (보유 슈퍼트렌드 진입분의 하락 추세전환)
        if not self._position_mgr:
            return
        try:
            positions_map = self._position_mgr.get_positions()
            watcher = SupertrendExitWatcher(gateway)
            exit_signals = await watcher.check(list(positions_map.values()))
            if exit_signals:
                logger.warning("슈퍼트렌드 청산 시그널 %d건: %s",
                               len(exit_signals), [e.symbol for e in exit_signals])
                for ex in exit_signals:
                    pos = positions_map.get(ex.symbol)
                    if _SUPERTREND_AUTO_TRADE and pos is not None and self._executor:
                        # SELL 시그널 → 전량 시장가 청산 (기존 _execute_exit 재사용).
                        #   reason 의 ':' 앞이 alert exit_type 으로 쓰이므로 prefix 부여.
                        await self._execute_exit(
                            ex.symbol, pos, 1.0, f"reverse_signal: {ex.reason}",
                        )
                    elif self._alert:
                        # AUTO_TRADE off 또는 미보유 — signal-only 알림(기존 동작).
                        try:
                            await self._alert.on_exit(
                                symbol=ex.symbol, name=ex.name, price=ex.price,
                                quantity=0, pnl=0.0, pnl_pct=ex.pnl_pct,
                                exit_type=ex.exit_type,
                            )
                        except Exception:
                            pass
        except Exception as e:
            logger.error("슈퍼트렌드 청산 평가 실패: %s", e)

    async def _supertrend_enter(self, gateway: Any, entry_signals: List[Any]) -> None:
        """슈퍼트렌드 진입 신호 → 매수 주문 배선 (2026-06-01).

        규칙 (사용자 확정):
          - 점수 내림차순(스캐너 정렬 유지)으로 평가, 미보유 종목만 진입.
          - 슈퍼트렌드 보유분이 _SUPERTREND_MAX_POSITIONS(10) 이상이면 신규 진입 중단.
          - 종목당 수량 = floor(가용예수금 × _SUPERTREND_ALLOC_PCT(8%) / 진입가).
          - 주문은 RiskEngine.approve 통과 후 송출(OrderExecutor.submit). 체결 시
            strategy_id 가 포지션에 부여되도록 _pending_strategy_ids 에 먼저 기록.
        signal-only 회귀: _SUPERTREND_AUTO_TRADE=False 면 본 메서드는 호출되지 않음.
        """
        import math
        if not self._executor or not self._position_mgr:
            return
        balance = self._position_mgr.get_balance()
        if balance is None:
            logger.warning("슈퍼트렌드 진입 보류 — 잔고 미동기화")
            return
        positions = self._position_mgr.get_positions()
        # 현재 슈퍼트렌드 전략으로 보유 중인 종목 수
        held_supertrend = sum(
            1 for p in positions.values()
            if (p.strategy_id or "").lower().startswith("supertrend")
        )
        available_cash = float(getattr(balance, "available_cash", 0) or 0)
        risk_engine = app_state.risk_engine

        placed = 0
        for s in entry_signals:
            if held_supertrend + placed >= _SUPERTREND_MAX_POSITIONS:
                logger.info("슈퍼트렌드 진입 상한(%d) 도달 — 추가 진입 중단",
                            _SUPERTREND_MAX_POSITIONS)
                break
            if s.score < _SUPERTREND_MIN_SCORE:
                continue
            if self._position_mgr.has_position(s.symbol):
                continue  # 이미 보유 — 중복 진입 방지
            if s.symbol in self._pending_strategy_ids:
                continue  # 직전 사이클 진입 주문 미체결 — 중복 송출 방지
            price = float(s.price or 0)
            if price <= 0:
                continue
            alloc = available_cash * _SUPERTREND_ALLOC_PCT
            qty = math.floor(alloc / price)
            if qty < 1:
                logger.info("슈퍼트렌드 %s 진입 스킵 — 배분금(%.0f) < 1주가(%.0f)",
                            s.symbol, alloc, price)
                continue
            try:
                from backend.models.position import Order, OrderSide, OrderType
                order = Order(
                    symbol=s.symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=float(qty),
                    price=price,
                    market_type=s.market_type,
                    strategy_id=s.strategy_id,  # "supertrend_v1"
                    risk_approved=False,
                )
                # 체결 콜백이 strategy_id 를 포지션에 부여하도록 사전 기록.
                self._pending_strategy_ids[s.symbol] = s.strategy_id
                result = await self._executor.submit(
                    order, gateway,
                    risk_engine=risk_engine,
                    positions=positions,
                    balance=balance,
                )
                if result is None:
                    # 리스크 거부 또는 실패 — 예약 strategy_id 회수.
                    self._pending_strategy_ids.pop(s.symbol, None)
                    continue
                placed += 1
                logger.info("슈퍼트렌드 진입 주문 체결: %s qty=%d @%.0f (%.1f점)",
                            s.symbol, qty, price, s.score)
                # 진입 알림은 스캔 블록(_supertrend_cycle)에서 이미 전체 신호에 1회 발송함.
                #   여기서 다시 on_signal 하면 체결 종목만 중복 알림 → 발송하지 않음.
            except Exception as e:
                self._pending_strategy_ids.pop(s.symbol, None)
                logger.error("슈퍼트렌드 진입 주문 실패: %s — %s", s.symbol, e)
        if placed:
            logger.info("슈퍼트렌드 진입 주문 %d건 송출 (보유 %d → %d)",
                        placed, held_supertrend, held_supertrend + placed)

    @staticmethod
    def _build_native_oauth() -> Optional[Any]:
        """RankUniverseProvider 용 키움 네이티브 OAuth — settings 미설정 시 None.

        실패해도 슈퍼트렌드 루프는 watchlist fallback 으로 동작 (가용성 우선).
        """
        try:
            from pydantic import SecretStr
            from backend.config.settings import get_settings
            from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
            _s = get_settings()
            key = getattr(_s, "kiwoom_app_key", "") or ""
            secret = getattr(_s, "kiwoom_app_secret", "") or ""
            if not key or not secret:
                logger.info("슈퍼트렌드: 키움 키 미설정 — 순위 유니버스 비활성(watchlist 사용)")
                return None
            return KiwoomNativeOAuth(
                app_key=SecretStr(key), app_secret=SecretStr(secret),
            )
        except Exception as e:
            logger.warning("슈퍼트렌드 OAuth 초기화 실패 — watchlist fallback: %s", e)
            return None

    async def _daily_report_loop(self) -> None:
        """매일 15:00 이후 일일 P&L 리포트 자동 전송"""
        _REPORT_HOUR = 15
        _REPORT_MINUTE = 5  # 15:05 전송 (장 마감 후 5분 대기)
        _CHECK_INTERVAL_SEC = 60  # 1분마다 시간 확인
        last_report_date: Optional[date] = None

        while self._running:
            try:
                now = datetime.now()
                today = now.date()
                if (
                    (now.hour > _REPORT_HOUR or now.minute >= _REPORT_MINUTE)
                    and now.hour >= _REPORT_HOUR
                    and last_report_date != today
                    and self._report
                    and self._position_mgr
                ):
                    trades = self._position_mgr.get_trade_history()
                    report = self._report.build_daily_report(trades, today)
                    if self._alert:
                        await self._alert.on_daily_report(report)
                    last_report_date = today
                    logger.info("일일 리포트 전송 완료: %s", today.isoformat())

                await asyncio.sleep(_CHECK_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("일일 리포트 오류: %s", e)
                await asyncio.sleep(60)

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

        # RiskEngine 초기화 (simulation 모드 → Phase 1 보수적 파라미터 적용)
        from backend.core.risk.risk_engine import RiskEngine
        from backend.core.risk.compliance import ComplianceService
        from backend.models.risk import RiskLimits
        if mode == "simulation":
            from backend.config.phase1_config import PHASE1_RISK_LIMITS, PHASE1_WATCHLIST
            risk_limits = RiskLimits(**PHASE1_RISK_LIMITS)
            app_state.watchlist = list(PHASE1_WATCHLIST)
            logger.info("Phase 1 모의투자 파라미터 적용 — 관심종목 %d개 로드", len(app_state.watchlist))
        else:
            risk_limits = RiskLimits()
        app_state.risk_engine = RiskEngine(limits=risk_limits)
        app_state.compliance = ComplianceService()
        logger.info("RiskEngine 초기화 완료: %s", app_state.risk_engine.limits.model_dump())

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

        # KiwoomGateway 초기화
        from backend.core.gateway.kiwoom import KiwoomGateway
        from backend.models.config import KiwoomConfig
        from backend.config.settings import get_settings
        _s = get_settings()
        kiwoom_config = KiwoomConfig(
            app_key=_s.kiwoom_app_key,
            app_secret=_s.kiwoom_app_secret,
            account_no=getattr(_s, "kiwoom_account_no", ""),
            mock=_s.kiwoom_mock,
        )
        app_state.market_gateway = KiwoomGateway(config=kiwoom_config)
        logger.info("KiwoomGateway 초기화 완료: mock=%s", kiwoom_config.mock)

        logger.info("서브시스템 초기화 완료")

    async def _on_order_filled(self, result: Any) -> None:
        """주문 체결 콜백"""
        if self._position_mgr:
            # strategy_id 전파 — 진입 주문 시 보관해 둔 값을 체결 포지션에 부여.
            #   (OrderResult 에 strategy_id 가 없어 유실되던 버그 보정. 슈퍼트렌드 등
            #    전략별 청산 watcher 가 strategy_id 로 보유분을 식별하는 데 필수.)
            sid = self._pending_strategy_ids.pop(result.symbol, "") if result else ""
            name = ""
            try:
                from backend.models.position import OrderSide as _OS
                if result.side == _OS.BUY:
                    self._position_mgr.on_order_filled(result, name=name, strategy_id=sid)
                else:
                    self._position_mgr.on_order_filled(result)
            except Exception:
                self._position_mgr.on_order_filled(result)
            # AppState 동기화
            app_state.positions = {
                k: v.model_dump(mode="json")
                for k, v in self._position_mgr.get_positions().items()
            }
            await app_state.broadcast("position_updated", app_state.positions)


# 전역 인스턴스
orchestrator = TradingOrchestrator()
