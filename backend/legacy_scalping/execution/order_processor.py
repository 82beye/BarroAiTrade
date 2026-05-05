"""
중앙 주문 프로세서

모든 매수/매도 신호를 단일 큐에서 순차 처리하여
중복 매수를 원천 차단하고 리스크 관리를 일원화한다.

구조:
  전략 모듈 (entry_monitor, exit_monitor 등)
      ↓ submit_buy() / submit_sell()
  asyncio.Queue
      ↓
  OrderProcessor._run_loop()  ← 유일한 주문 실행 지점
      ↓
  KiwoomRestAPI (실제 주문)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, date
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Union

from strategy.entry_signal import EntrySignal
from strategy.exit_signal import ExitSignal, ExitType

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 큐 메시지 타입
# ─────────────────────────────────────────────────────────────────────────────

class OrderAction(Enum):
    BUY = "BUY"
    ADD_BUY = "ADD_BUY"
    SELL = "SELL"
    FORCE_LIQUIDATE = "FORCE_LIQUIDATE"
    STOP = "STOP"


@dataclass
class OrderRequest:
    """큐에 전달되는 주문 요청"""
    action: OrderAction
    signal: Optional[Union[EntrySignal, ExitSignal]] = None
    # 완료 통보용 (submit 측에서 결과를 기다릴 때 사용)
    result_future: Optional[asyncio.Future] = field(default=None, repr=False)


# ─────────────────────────────────────────────────────────────────────────────
# 포지션 데이터
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Position:
    """보유 포지션"""
    code: str
    name: str
    entry_price: float
    qty: int
    entry_time: datetime
    amount: int
    tp1_triggered: bool = False
    current_price: float = 0.0
    pnl_pct: float = 0.0
    is_carryover: bool = False      # 전일 미청산 포지션 여부
    # 전략 태깅 (스캘핑 vs 일반)
    strategy_type: str = "regular"  # "regular" | "scalping"
    scalp_tp_pct: float = 0.0      # 스캘핑 익절 %
    scalp_sl_pct: float = 0.0      # 스캘핑 손절 %
    scalp_hold_minutes: int = 0    # 스캘핑 최대 보유 시간 (분)
    scalp_high_watermark: float = 0.0  # 스캘핑 트레일링 스톱용 고점
    scalp_trailing_active: bool = False  # 트레일링 스톱 활성화 여부
    scalp_min_price: float = 0.0   # 스캘핑 MAE 추적용 최저가
    add_buy_count: int = 0             # 추가 매수 횟수
    # 2026-04-07: 변동성 비례 SL/트레일링용
    intraday_atr: float = 0.0          # 1분봉 ATR (진입 시점 기준)
    change_pct: float = 0.0            # 진입 시점 일일 등락률 (%)


class OrderProcessor:
    """
    중앙 주문 프로세서

    - asyncio.Queue 기반 순차 처리 → 동시 주문으로 인한 중복 원천 차단
    - pending_codes 추적 → 체결 대기 중인 종목도 중복 방지
    - 리스크 필터 일원화 (포지션 수, 노출 한도, 일일 손실)
    """

    def __init__(self, kiwoom_api, config: dict):
        self.api = kiwoom_api
        self.config = config

        # 포지션 상태
        self.positions: Dict[str, Position] = {}
        self._pending_codes: Set[str] = set()   # 주문 중 (체결 대기)

        # 리스크 설정
        risk = config.get('risk', {})
        self._max_positions = risk.get('max_positions', 5)
        self._max_per_stock_pct = risk.get('max_per_stock_pct', 10.0)
        self._max_total_exposure_pct = risk.get('max_total_exposure_pct', 50.0)
        self._daily_loss_limit_pct = risk.get('daily_loss_limit_pct', -5.0)

        # 통계
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._total_equity = 0

        # 큐
        self._queue: asyncio.Queue[OrderRequest] = asyncio.Queue()
        self._running = False

        # 매매 로그
        self._trade_log_path = config.get(
            'logging', {}).get('trade_log', './logs/trades.jsonl')

    # ─── 초기화 ───

    async def initialize(self):
        """계좌 잔고 동기화"""
        balance = await self.api.get_balance()
        self._total_equity = balance['total_equity']

        # trades.jsonl에서 미청산 종목의 원래 strategy_type + 스캘핑 파라미터 복원
        import re
        last_buy_info = {}  # {code: {strategy_type, tp, sl, hold}}
        try:
            with open(self._trade_log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    t = json.loads(line.strip())
                    if t.get('action') == 'BUY':
                        info = {
                            'strategy_type': t.get('strategy_type', 'regular'),
                            'tp': 0.0, 'sl': 0.0, 'hold': 0,
                        }
                        # reason에서 TP/SL/hold 파싱: "스캘핑 59점 ... | TP +2.0% SL -1.2% 12분"
                        reason = t.get('reason', '')
                        tp_m = re.search(r'TP \+?([\d.]+)%', reason)
                        sl_m = re.search(r'SL (-?[\d.]+)%', reason)
                        hm = re.search(r'(\d+)분', reason)
                        if tp_m:
                            info['tp'] = float(tp_m.group(1))
                        if sl_m:
                            info['sl'] = float(sl_m.group(1))
                        if hm:
                            info['hold'] = int(hm.group(1))
                        last_buy_info[t['code']] = info
        except FileNotFoundError:
            pass

        for pos in balance['positions']:
            info = last_buy_info.get(pos['code'], {})
            st = info.get('strategy_type', 'regular')
            logger.warning(
                f"미청산 포지션 발견: [{pos['code']}] {pos['name']} "
                f"{pos['qty']}주 @ {pos['entry_price']:,.0f} "
                f"(현재가: {pos['current_price']:,} | "
                f"손익: {pos['pnl_pct']:+.1f}% | 전략: {st})")
            self.positions[pos['code']] = Position(
                code=pos['code'],
                name=pos['name'],
                entry_price=pos['entry_price'],
                qty=pos['qty'],
                entry_time=datetime.now(),
                amount=pos['amount'],
                current_price=pos['current_price'],
                pnl_pct=pos['pnl_pct'],
                is_carryover=True,
                strategy_type=st,
                scalp_tp_pct=info.get('tp', 0.0),
                scalp_sl_pct=info.get('sl', 0.0),
                scalp_hold_minutes=info.get('hold', 0),
            )

        carryover_count = sum(
            1 for p in self.positions.values() if p.is_carryover)
        logger.info(
            f"OrderProcessor 초기화 | 총자산: {self._total_equity:,}원 | "
            f"보유: {len(self.positions)}종목 "
            f"(미청산: {carryover_count}종목)")

    # ─── 속성 ───

    @property
    def total_equity(self) -> int:
        return self._total_equity

    @property
    def daily_pnl_pct(self) -> float:
        if self._total_equity > 0:
            return self._daily_pnl / self._total_equity * 100
        return 0.0

    def is_held_or_pending(self, code: str) -> bool:
        """보유 중이거나 주문 진행 중인 종목인지 확인"""
        return code in self.positions or code in self._pending_codes

    # ─── 신호 제출 (전략 모듈이 호출) ───

    async def submit_buy(self, signal: EntrySignal) -> bool:
        """
        매수 신호 제출. 결과(성공 여부)를 기다려서 반환한다.
        큐에 넣기 전에 빠른 중복 체크를 수행한다.
        """
        if self.is_held_or_pending(signal.code):
            logger.debug(f"중복 매수 차단: [{signal.code}] (보유/대기 중)")
            return False

        future = asyncio.get_event_loop().create_future()
        await self._queue.put(OrderRequest(
            action=OrderAction.BUY,
            signal=signal,
            result_future=future,
        ))
        return await future

    async def submit_add_buy(self, signal: EntrySignal) -> bool:
        """
        추가 매수 신호 제출 (기존 보유 종목에 수량 추가).
        is_held_or_pending 체크를 우회하고, 기존 포지션에 평균단가 갱신.
        """
        if signal.code not in self.positions:
            logger.warning(f"추가매수 거부: [{signal.code}] 보유 포지션 없음")
            return False

        future = asyncio.get_event_loop().create_future()
        await self._queue.put(OrderRequest(
            action=OrderAction.ADD_BUY,
            signal=signal,
            result_future=future,
        ))
        return await future

    async def submit_sell(self, signal: ExitSignal) -> bool:
        """매도 신호 제출. 결과를 기다려서 반환한다."""
        future = asyncio.get_event_loop().create_future()
        await self._queue.put(OrderRequest(
            action=OrderAction.SELL,
            signal=signal,
            result_future=future,
        ))
        return await future

    async def submit_force_liquidate(self) -> int:
        """전 포지션 강제 청산 요청. 청산된 종목 수를 반환."""
        future = asyncio.get_event_loop().create_future()
        await self._queue.put(OrderRequest(
            action=OrderAction.FORCE_LIQUIDATE,
            result_future=future,
        ))
        return await future

    async def stop(self):
        """프로세서 중지 요청"""
        if self._running:
            await self._queue.put(OrderRequest(action=OrderAction.STOP))
        self._running = False

    # ─── 프로세서 메인 루프 (asyncio.create_task로 실행) ───

    async def run(self):
        """큐에서 주문 요청을 꺼내 순차 처리하는 메인 루프"""
        self._running = True
        logger.info("OrderProcessor 시작")

        while self._running:
            try:
                req = await self._queue.get()
            except asyncio.CancelledError:
                break

            try:
                if req.action == OrderAction.STOP:
                    self._running = False
                    if req.result_future:
                        req.result_future.set_result(True)
                    break

                elif req.action == OrderAction.BUY:
                    ok = await self._process_buy(req.signal)
                    if req.result_future:
                        req.result_future.set_result(ok)

                elif req.action == OrderAction.ADD_BUY:
                    ok = await self._process_add_buy(req.signal)
                    if req.result_future:
                        req.result_future.set_result(ok)

                elif req.action == OrderAction.SELL:
                    ok = await self._process_sell(req.signal)
                    if req.result_future:
                        req.result_future.set_result(ok)

                elif req.action == OrderAction.FORCE_LIQUIDATE:
                    count = await self._process_force_liquidate()
                    if req.result_future:
                        req.result_future.set_result(count)

            except Exception as e:
                logger.error(f"주문 처리 오류: {e}", exc_info=True)
                if req.result_future and not req.result_future.done():
                    req.result_future.set_result(False)

            finally:
                self._queue.task_done()

        logger.info("OrderProcessor 종료")

    # ─── 내부 실행 로직 ───

    async def _process_buy(self, signal: EntrySignal) -> bool:
        """
        매수 처리 — 큐 내부에서만 호출 (순차 보장)

        리스크 체크 → pending 등록 → API 주문 → 포지션 등록 → pending 해제
        """
        code = signal.code

        # 이중 체크 (큐 대기 중 상태 변경 대비)
        if self.is_held_or_pending(code):
            logger.debug(f"중복 매수 최종 차단: [{code}]")
            return False

        # 리스크 체크
        if not self._check_buy_risk(signal):
            return False

        # pending 등록 (이 시점부터 다른 신호가 동일 종목 걸러짐)
        self._pending_codes.add(code)

        try:
            # 최유리지정가 주문 → 슬리피지 방지
            logger.info(
                f"매수 실행: [{code}] {signal.name} | "
                f"{signal.suggested_qty}주 최유리지정가")

            result = await self.api.buy_limit_order(
                code, signal.suggested_qty, price=0)  # 최유리지정가

            if result['success']:
                # 실제 체결가/수량 사용 (API 응답 기반, 없으면 signal 값 폴백)
                filled_price = result.get('filled_price') or signal.current_price
                api_qty = result.get('filled_qty') or 0
                ordered_qty = signal.suggested_qty
                # 부분체결 보정: API 수량이 주문의 50% 미만이면 주문 수량 사용
                if api_qty <= 0 or api_qty < ordered_qty * 0.5:
                    filled_qty = ordered_qty
                    if api_qty > 0 and api_qty != ordered_qty:
                        logger.warning(
                            f"체결수량 보정: [{code}] API={api_qty} → "
                            f"주문수량={ordered_qty} (부분체결 의심)")
                else:
                    filled_qty = api_qty
                filled_amount = int(filled_qty * filled_price)

                # 슬리피지 체크: signal 대비 체결가 차이가 과도하면 즉시 매도
                max_slip = self.config.get(
                    'strategy', {}).get('scalping', {}).get(
                    'max_slippage_pct', 2.0)
                slip_pct = (
                    (filled_price - signal.current_price)
                    / signal.current_price * 100
                ) if signal.current_price > 0 else 0
                if slip_pct > max_slip:
                    logger.warning(
                        f"슬리피지 초과: [{code}] 체결 {filled_price:,} vs "
                        f"신호 {signal.current_price:,} (+{slip_pct:.1f}% > "
                        f"{max_slip}%) → 즉시 손절 매도")
                    # 즉시 시장가 매도
                    try:
                        sell_result = await self.api.sell_market_order(
                            code, filled_qty)
                        sell_price = (sell_result.get('filled_price')
                                      or filled_price)
                        slip_loss = (sell_price - filled_price) * filled_qty
                        self._daily_pnl += slip_loss
                        self._daily_trades += 1
                        self._log_trade({
                            'action': 'SELL',
                            'code': code,
                            'name': signal.name,
                            'qty': filled_qty,
                            'price': sell_price,
                            'entry_price': filled_price,
                            'pnl_pct': round(
                                (sell_price - filled_price)
                                / filled_price * 100, 2),
                            'exit_type': '슬리피지_즉시청산',
                            'reason': (
                                f"슬리피지 +{slip_pct:.1f}% 초과 "
                                f"(한도 {max_slip}%) → 즉시 청산"),
                            'order_no': sell_result.get('order_no', ''),
                            'strategy_type': getattr(
                                signal, 'strategy_type', 'regular'),
                            'filled_time': sell_result.get('filled_time', ''),
                        })
                        logger.info(
                            f"슬리피지 즉시 청산: [{code}] {signal.name} | "
                            f"매수 {filled_price:,} → 매도 {sell_price:,}")
                    except Exception as e:
                        logger.error(
                            f"슬리피지 즉시 청산 실패: [{code}] {e}")
                    return True  # 매수는 성공했으나 즉시 청산

                # 거래소 체결시간 기반 entry_time (없으면 로컬 시간)
                api_entry_time = self._parse_filled_time(
                    result.get('filled_time', ''))
                entry_time = api_entry_time or datetime.now()

                self.positions[code] = Position(
                    code=code,
                    name=signal.name,
                    entry_price=filled_price,
                    qty=filled_qty,
                    entry_time=entry_time,
                    amount=filled_amount,
                    current_price=filled_price,
                    strategy_type=getattr(signal, 'strategy_type', 'regular'),
                    scalp_tp_pct=getattr(signal, 'scalp_tp_pct', 0.0),
                    scalp_sl_pct=getattr(signal, 'scalp_sl_pct', 0.0),
                    scalp_hold_minutes=getattr(
                        signal, 'scalp_hold_minutes', 0),
                    # 2026-04-07: 변동성 비례 SL/트레일링용
                    intraday_atr=getattr(signal, 'intraday_atr', 0.0),
                    change_pct=getattr(signal, 'change_pct', 0.0),
                )
                self._daily_trades += 1

                self._log_trade({
                    'action': 'BUY',
                    'code': code,
                    'name': signal.name,
                    'qty': filled_qty,
                    'price': filled_price,
                    'amount': filled_amount,
                    'signal_price': signal.current_price,
                    'reason': signal.reason,
                    'order_no': result['order_no'],
                    'strategy_type': getattr(
                        signal, 'strategy_type', 'regular'),
                    'filled_time': result.get('filled_time', ''),
                })

                logger.info(
                    f"매수 완료: [{code}] {signal.name} | "
                    f"{filled_qty}주 × {filled_price:,.0f}원 = "
                    f"{filled_amount:,}원")
                return True
            else:
                logger.error(f"매수 실패: [{code}] {result['message']}")
                return False
        finally:
            self._pending_codes.discard(code)

    async def _process_add_buy(self, signal: EntrySignal) -> bool:
        """
        추가 매수 처리 — 기존 포지션에 수량/금액 추가, 평균단가 갱신.
        is_held_or_pending 체크 없이 진행.
        """
        code = signal.code
        pos = self.positions.get(code)
        if not pos:
            logger.warning(f"추가매수 실패: [{code}] 포지션 없음")
            return False

        # 일일 손실 한도만 체크 (포지션 수, 총 노출은 기존 포지션이므로 스킵)
        if self.daily_pnl_pct <= self._daily_loss_limit_pct:
            logger.warning(
                f"추가매수 거부 - 일일 손실 한도: {self.daily_pnl_pct:.1f}%")
            return False

        # 종목 비중 체크
        new_total = pos.amount + signal.suggested_amount
        if self._total_equity > 0:
            new_pct = new_total / self._total_equity * 100
            if new_pct > self._max_per_stock_pct:
                logger.debug(
                    f"추가매수 거부 - 종목 비중 초과: [{code}] "
                    f"{new_pct:.1f}% > {self._max_per_stock_pct}%")
                return False

        try:
            logger.info(
                f"추가매수 실행: [{code}] {signal.name} | "
                f"{signal.suggested_qty}주 최유리지정가")

            result = await self.api.buy_limit_order(
                code, signal.suggested_qty, price=0)

            if result['success']:
                filled_price = result.get('filled_price') or signal.current_price
                api_qty = result.get('filled_qty') or 0
                ordered_qty = signal.suggested_qty
                if api_qty <= 0 or api_qty < ordered_qty * 0.5:
                    filled_qty = ordered_qty
                else:
                    filled_qty = api_qty
                filled_amount = int(filled_qty * filled_price)

                # 평균단가 갱신
                old_total_cost = pos.entry_price * pos.qty
                new_total_cost = old_total_cost + filled_price * filled_qty
                new_total_qty = pos.qty + filled_qty
                pos.entry_price = new_total_cost / new_total_qty
                pos.qty = new_total_qty
                pos.amount = int(pos.entry_price * pos.qty)
                pos.add_buy_count += 1

                self._daily_trades += 1
                self._log_trade({
                    'action': 'ADD_BUY',
                    'code': code,
                    'name': signal.name,
                    'qty': filled_qty,
                    'price': filled_price,
                    'amount': filled_amount,
                    'avg_price': round(pos.entry_price),
                    'total_qty': pos.qty,
                    'signal_price': signal.current_price,
                    'reason': signal.reason,
                    'order_no': result['order_no'],
                    'strategy_type': pos.strategy_type,
                    'filled_time': result.get('filled_time', ''),
                })

                logger.info(
                    f"추가매수 완료: [{code}] {signal.name} | "
                    f"{filled_qty}주 × {filled_price:,.0f}원 | "
                    f"평단: {pos.entry_price:,.0f}원 총 {pos.qty}주")
                return True
            else:
                logger.error(f"추가매수 실패: [{code}] {result['message']}")
                return False
        except Exception as e:
            logger.error(f"추가매수 오류: [{code}] {e}", exc_info=True)
            return False

    def _check_buy_risk(self, signal: EntrySignal) -> bool:
        """중앙 리스크 필터 (매수 전 최종 확인)"""
        # 일일 손실 한도
        if self.daily_pnl_pct <= self._daily_loss_limit_pct:
            logger.warning(
                f"매수 거부 - 일일 손실 한도: {self.daily_pnl_pct:.1f}%")
            return False

        # 최대 포지션 수 (미청산 포지션은 별도 관리이므로 제외)
        day_trade_count = sum(
            1 for p in self.positions.values() if not p.is_carryover)
        active_count = day_trade_count + len(self._pending_codes)
        if active_count >= self._max_positions:
            logger.debug(
                f"매수 거부 - 최대 포지션: {active_count}/{self._max_positions}")
            return False

        # 총 노출 한도
        total_invested = sum(p.amount for p in self.positions.values())
        new_exposure_pct = (
            (total_invested + signal.suggested_amount)
            / self._total_equity * 100
        ) if self._total_equity > 0 else 100
        if new_exposure_pct > self._max_total_exposure_pct:
            logger.debug(
                f"매수 거부 - 노출 한도: {new_exposure_pct:.1f}% > "
                f"{self._max_total_exposure_pct}%")
            return False

        return True

    async def _process_sell(self, signal: ExitSignal) -> bool:
        """매도 처리"""
        code = signal.code
        qty = signal.sell_qty

        # 긴급 매도(강제청산/손절 등)는 시장가, 나머지는 최유리지정가
        is_urgent = signal.exit_type in (
            ExitType.FORCE_LIQUIDATION,
            ExitType.ERROR_LIQUIDATION,
            ExitType.DAILY_LOSS_LIMIT,
            ExitType.SCALP_STOP_LOSS,
            ExitType.SCALP_TRAILING_STOP,
        )
        order_label = "시장가" if is_urgent else "최유리지정가"
        logger.info(
            f"매도 실행: [{code}] {signal.name} | "
            f"{qty}주 {order_label} | {signal.exit_type.value}")

        if is_urgent:
            result = await self.api.sell_market_order(code, qty)
        else:
            result = await self.api.sell_limit_order(code, qty, price=0)

        if result['success']:
            # 실제 체결가 사용 (API 응답 기반, 없으면 signal 값 폴백)
            filled_price = result.get('filled_price') or signal.current_price
            api_qty = result.get('filled_qty') or 0
            if api_qty <= 0 or api_qty < qty * 0.5:
                filled_qty = qty
                if api_qty > 0 and api_qty != qty:
                    logger.warning(
                        f"매도 체결수량 보정: [{code}] API={api_qty} → 주문수량={qty}")
            else:
                filled_qty = api_qty

            pos = self.positions.get(code)
            st = pos.strategy_type if pos else 'regular'
            entry_price = pos.entry_price if pos else signal.entry_price
            if pos:
                # 수수료/세금 반영 실현 손익
                gross_pnl = (filled_price - pos.entry_price) * filled_qty
                buy_commission = pos.entry_price * filled_qty * 0.00015
                sell_commission = filled_price * filled_qty * 0.00015
                sell_tax = filled_price * filled_qty * 0.0018  # KOSDAQ 거래세
                net_pnl = gross_pnl - buy_commission - sell_commission - sell_tax
                realized_pnl = net_pnl
                self._daily_pnl += realized_pnl

                if signal.sell_ratio >= 1.0 or pos.qty <= filled_qty:
                    del self.positions[code]
                else:
                    pos.qty -= filled_qty
                    pos.amount = int(pos.qty * pos.entry_price)
                    if signal.exit_type == ExitType.TAKE_PROFIT_1:
                        pos.tp1_triggered = True

            pnl_pct = ((filled_price - entry_price) / entry_price * 100
                       if entry_price > 0 else 0)
            # 수수료/세금 반영 순수익률 (단위: %)
            fee_pct = 0.015 + 0.015 + 0.18  # 매수0.015% + 매도0.015% + 거래세0.18% = 0.21%
            net_pnl_pct = pnl_pct - fee_pct if entry_price > 0 else 0

            self._daily_trades += 1
            self._log_trade({
                'action': 'SELL',
                'code': code,
                'name': signal.name,
                'qty': filled_qty,
                'price': filled_price,
                'entry_price': entry_price,
                'pnl_pct': pnl_pct,
                'net_pnl_pct': net_pnl_pct,
                'commission': round(
                    entry_price * filled_qty * 0.00015
                    + filled_price * filled_qty * 0.00015, 0),
                'tax': round(filled_price * filled_qty * 0.0018, 0),
                'signal_price': signal.current_price,
                'exit_type': signal.exit_type.value,
                'reason': signal.reason,
                'order_no': result['order_no'],
                'strategy_type': st,
                'mae_pct': (
                    (pos.scalp_min_price - entry_price) / entry_price * 100
                    if pos and pos.scalp_min_price > 0 and entry_price > 0
                    else None
                ),
                'filled_time': result.get('filled_time', ''),
            })

            pnl_str = f"{pnl_pct:+.1f}%"
            logger.info(
                f"매도 완료: [{code}] {signal.name} | "
                f"{filled_qty}주 × {filled_price:,.0f}원 | "
                f"수익률: {pnl_str} | {signal.exit_type.value}")
            return True
        else:
            logger.error(f"매도 실패: [{code}] {result['message']}")
            return False

    async def _process_force_liquidate(self) -> int:
        """
        전 포지션 강제 청산 + 미체결 취소

        1. 미체결 주문 전량 취소 → 취소 체결 대기 (2초)
        2. API 잔고 재조회로 실제 보유 수량 확인
        3. 보유 종목 전량 시장가 매도 + 손익 기록
        4. 실패 종목은 재시도 1회
        """
        logger.warning("=" * 60)
        logger.warning("전 포지션 강제 청산 시작")
        logger.warning("=" * 60)

        # 1. 미체결 주문 전량 취소
        pending_orders = await self.api.get_pending_orders()
        for order in pending_orders:
            try:
                await self.api.cancel_order(
                    order['order_no'], order['code'], order['remaining_qty'])
            except Exception as e:
                logger.error(f"미체결 취소 실패: {order['order_no']} - {e}")

        if pending_orders:
            logger.info(f"미체결 {len(pending_orders)}건 취소 요청 완료")
            await asyncio.sleep(2)  # 취소 체결 대기

        # 2. API 잔고 재조회 → 실제 보유 수량 기준으로 매도
        try:
            balance = await self.api.get_balance()
            api_positions = {p['code']: p for p in balance['positions']}
            # 로컬에 없지만 API에 있는 종목도 포함 (안전장치)
            for code, ap in api_positions.items():
                if code not in self.positions and ap['qty'] > 0:
                    self.positions[code] = Position(
                        code=code, name=ap.get('name', code),
                        entry_price=ap['entry_price'],
                        qty=ap['qty'], entry_time=datetime.now(),
                        amount=ap['amount'],
                        current_price=ap['current_price'],
                    )
                elif code in self.positions:
                    # 실제 잔량으로 보정
                    self.positions[code].qty = ap['qty']
                    self.positions[code].current_price = ap['current_price']
        except Exception as e:
            logger.error(f"잔고 재조회 실패, 로컬 포지션 기준 청산: {e}")

        # 3. 전량 시장가 매도 (최대 3회 시도)
        MAX_RETRY = 3
        liquidated = 0
        pending_codes = [
            code for code, pos in self.positions.items() if pos.qty > 0]
        # qty <= 0 정리
        for code in list(self.positions.keys()):
            if self.positions[code].qty <= 0:
                del self.positions[code]

        for attempt in range(1, MAX_RETRY + 1):
            if not pending_codes:
                break

            if attempt > 1:
                logger.warning(
                    f"청산 재시도 {attempt}/{MAX_RETRY} "
                    f"({len(pending_codes)}종목)...")
                await asyncio.sleep(1)

            still_failed = []
            for code in pending_codes:
                pos = self.positions.get(code)
                if not pos or pos.qty <= 0:
                    continue
                try:
                    # 매도 직전 현재가 조회 (정확한 손익 계산)
                    try:
                        price_data = await self.api.get_current_price(code)
                        pos.current_price = price_data['price']
                    except Exception:
                        pass  # 조회 실패 시 기존 current_price 사용

                    result = await self.api.sell_market_order(code, pos.qty)
                    if result['success']:
                        # 실제 체결가로 갱신
                        filled_price = result.get('filled_price')
                        if filled_price:
                            pos.current_price = filled_price
                        # 성공 시에만 손익 기록
                        self._record_liquidation(
                            pos, result['order_no'],
                            result.get('filled_time', ''))
                        liquidated += 1
                        del self.positions[code]
                    else:
                        logger.error(
                            f"청산 실패 ({attempt}/{MAX_RETRY}): "
                            f"[{code}] {pos.name} - {result['message']}")
                        still_failed.append(code)
                except Exception as e:
                    logger.error(
                        f"청산 오류 ({attempt}/{MAX_RETRY}): "
                        f"[{code}] {pos.name} - {e}")
                    still_failed.append(code)

            pending_codes = still_failed

        # 3회 모두 실패한 종목
        if pending_codes:
            for code in pending_codes:
                pos = self.positions.get(code)
                if pos:
                    logger.critical(
                        f"청산 {MAX_RETRY}회 실패 — 수동 확인 필요: "
                        f"[{code}] {pos.name} {pos.qty}주")

        # 남은 포지션 경고
        remaining = [c for c, p in self.positions.items() if p.qty > 0]
        if remaining:
            logger.critical(
                f"미청산 종목 {len(remaining)}건 — 수동 확인 필요: "
                f"{remaining}")
        else:
            self.positions.clear()

        self._pending_codes.clear()
        logger.warning(f"강제 청산 완료: {liquidated}종목 시장가 매도")
        return liquidated

    # ─── 잔고 동기화 ───

    async def sync_positions(self):
        """계좌 잔고와 포지션 동기화"""
        balance = await self.api.get_balance()
        self._total_equity = balance['total_equity']

        api_positions = {p['code']: p for p in balance['positions']}
        now = datetime.now()

        for code in list(self.positions.keys()):
            if code in api_positions:
                ap = api_positions[code]
                self.positions[code].qty = ap['qty']
                self.positions[code].current_price = ap['current_price']
                self.positions[code].pnl_pct = ap['pnl_pct']
                self.positions[code].amount = ap['amount']
            elif code in self._pending_codes:
                # 주문 진행 중이면 삭제하지 않음
                logger.debug(f"포지션 유지: [{code}] (주문 진행 중)")
            else:
                elapsed = (now - self.positions[code].entry_time).total_seconds()
                if elapsed > 60:
                    logger.info(f"포지션 동기화 제거: [{code}] (API 잔고에 없음)")
                    del self.positions[code]
                else:
                    logger.debug(
                        f"포지션 유지: [{code}] "
                        f"(매수 후 {elapsed:.0f}초, 체결 대기)")

    # ─── 유틸리티 ───

    def _record_liquidation(self, pos: Position, order_no: str,
                            filled_time: str = ''):
        """강제 청산 종목의 손익 계산 + 매매 로그 기록"""
        realized_pnl = (pos.current_price - pos.entry_price) * pos.qty
        self._daily_pnl += realized_pnl
        self._daily_trades += 1

        pnl_pct = ((pos.current_price - pos.entry_price)
                   / pos.entry_price * 100
                   if pos.entry_price > 0 else 0)

        self._log_trade({
            'action': 'SELL',
            'code': pos.code,
            'name': pos.name,
            'qty': pos.qty,
            'price': pos.current_price,
            'entry_price': pos.entry_price,
            'pnl_pct': round(pnl_pct, 2),
            'pnl_amount': round(realized_pnl),
            'exit_type': '강제청산',
            'reason': '14:50 장마감 강제 시장가 청산',
            'order_no': order_no,
            'filled_time': filled_time,
        })

        logger.info(
            f"청산: [{pos.code}] {pos.name} | {pos.qty}주 시장가 | "
            f"매수가: {pos.entry_price:,.0f} → 현재가: {pos.current_price:,.0f} | "
            f"손익: {realized_pnl:+,.0f}원 ({pnl_pct:+.1f}%)")

    def get_positions_dict(self) -> Dict[str, dict]:
        """포지션을 dict 형태로 반환 (전략 모듈용)"""
        return {
            code: {
                'entry_price': pos.entry_price,
                'qty': pos.qty,
                'tp1_triggered': pos.tp1_triggered,
                'current_price': pos.current_price,
                'name': pos.name,
                'amount': pos.amount,
                'is_carryover': pos.is_carryover,
                'strategy_type': pos.strategy_type,
                'scalp_tp_pct': pos.scalp_tp_pct,
                'scalp_sl_pct': pos.scalp_sl_pct,
                'scalp_hold_minutes': pos.scalp_hold_minutes,
                'scalp_high_watermark': pos.scalp_high_watermark,
                'scalp_trailing_active': pos.scalp_trailing_active,
                'add_buy_count': pos.add_buy_count,
                'entry_time': pos.entry_time,
                # 2026-04-07: 변동성 비례 SL/트레일링용
                'intraday_atr': pos.intraday_atr,
                'change_pct': pos.change_pct,
            }
            for code, pos in self.positions.items()
        }

    def update_scalp_high_watermark(self, code: str, current_price: float):
        """스캘핑 포지션의 고점/저점 업데이트 (트레일링 + MAE 추적)"""
        pos = self.positions.get(code)
        if pos and pos.strategy_type == 'scalping':
            if current_price > pos.scalp_high_watermark:
                pos.scalp_high_watermark = current_price
            if pos.scalp_min_price == 0 or current_price < pos.scalp_min_price:
                pos.scalp_min_price = current_price

    def activate_scalp_trailing(self, code: str):
        """트레일링 스톱 활성화"""
        pos = self.positions.get(code)
        if pos and pos.strategy_type == 'scalping':
            pos.scalp_trailing_active = True

    def get_daily_summary(self) -> dict:
        """당일 매매 요약"""
        return {
            'date': date.today().isoformat(),
            'total_equity': self._total_equity,
            'daily_pnl': self._daily_pnl,
            'daily_pnl_pct': self.daily_pnl_pct,
            'total_trades': self._daily_trades,
            'open_positions': len(self.positions),
        }

    @staticmethod
    def _parse_filled_time(filled_time: str) -> Optional[datetime]:
        """API 체결시간 문자열 → datetime 변환

        거래소 체결시간 포맷:
          - "HH:MM:SS" (kt00009 형식)
          - "HHMMSS" (6자리)
          - "" (미반환)
        """
        if not filled_time:
            return None
        t = filled_time.strip().replace(':', '')
        if len(t) >= 6 and t[:6].isdigit():
            today = datetime.now().strftime('%Y-%m-%d')
            try:
                return datetime.strptime(
                    f"{today} {t[:2]}:{t[2:4]}:{t[4:6]}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        return None

    def _log_trade(self, trade: dict):
        """매매 기록을 JSONL 파일에 기록

        timestamp 우선순위:
          1. trade['filled_time'] → 거래소 API 체결시간 (가장 정확)
          2. datetime.now() → 로컬 시간 (폴백)
        """
        # 거래소 체결시간이 있으면 그것을 timestamp로 사용
        api_time = self._parse_filled_time(trade.pop('filled_time', ''))
        if api_time:
            trade['timestamp'] = api_time.isoformat()
            trade['timestamp_source'] = 'exchange'
        else:
            trade['timestamp'] = datetime.now().isoformat()
            trade['timestamp_source'] = 'local'
        trade['daily_pnl'] = self._daily_pnl
        trade['daily_pnl_pct'] = self.daily_pnl_pct
        try:
            with open(self._trade_log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(trade, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"매매 로그 기록 실패: {e}")
