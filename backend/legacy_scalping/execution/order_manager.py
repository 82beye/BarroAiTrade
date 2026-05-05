"""
주문 관리자
매수/매도 실행, 미체결 관리, 포지션 추적을 통합 관리
"""

import json
import logging
from datetime import datetime, date
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict

from strategy.entry_signal import EntrySignal
from strategy.exit_signal import ExitSignal, ExitType

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """보유 포지션"""
    code: str
    name: str
    entry_price: float
    qty: int
    entry_time: datetime
    amount: int
    tp1_triggered: bool = False     # 1차 익절 실행 여부
    current_price: float = 0.0
    pnl_pct: float = 0.0


class OrderManager:
    """주문 관리자"""
    
    def __init__(self, kiwoom_api, config: dict):
        self.api = kiwoom_api
        self.config = config
        self.positions: Dict[str, Position] = {}
        self.trade_log_path = config.get('logging', {}).get('trade_log', './logs/trades.jsonl')
        self._daily_pnl = 0.0        # 당일 실현 손익
        self._daily_trades = 0       # 당일 거래 횟수
        self._total_equity = 0       # 총 평가금액
    
    async def initialize(self):
        """초기화 - 계좌 잔고 동기화"""
        balance = await self.api.get_balance()
        self._total_equity = balance['total_equity']
        
        # 기존 보유 종목 확인 (전일 미청산 있으면 경고)
        for pos in balance['positions']:
            logger.warning(f"기존 보유 종목 발견: [{pos['code']}] {pos['name']} "
                          f"{pos['qty']}주 @ {pos['entry_price']:,.0f}")
            self.positions[pos['code']] = Position(
                code=pos['code'],
                name=pos['name'],
                entry_price=pos['entry_price'],
                qty=pos['qty'],
                entry_time=datetime.now(),
                amount=pos['amount'],
                current_price=pos['current_price'],
                pnl_pct=pos['pnl_pct'],
            )
        
        logger.info(f"계좌 초기화 완료 | 총자산: {self._total_equity:,}원 | "
                     f"예수금: {balance['cash']:,}원 | "
                     f"보유종목: {len(self.positions)}")
    
    @property
    def total_equity(self) -> int:
        return self._total_equity
    
    @property
    def daily_pnl_pct(self) -> float:
        """당일 실현 손익률"""
        if self._total_equity > 0:
            return self._daily_pnl / self._total_equity * 100
        return 0.0
    
    async def execute_buy(self, signal: EntrySignal) -> bool:
        """
        매수 실행
        
        Returns:
            성공 여부
        """
        code = signal.code
        qty = signal.suggested_qty
        
        logger.info(f"매수 실행: [{code}] {signal.name} | {qty}주 최유리지정가")

        result = await self.api.buy_limit_order(code, qty, price=0)

        if result['success']:
            # 실체결가 사용 (없으면 signal 값 폴백)
            filled_price = result.get('filled_price') or signal.current_price
            filled_qty = result.get('filled_qty') or qty
            # 포지션 등록
            self.positions[code] = Position(
                code=code,
                name=signal.name,
                entry_price=filled_price,
                qty=filled_qty,
                entry_time=datetime.now(),
                amount=int(filled_qty * filled_price),
                current_price=filled_price,
            )
            self._daily_trades += 1
            
            # 매매 로그 기록
            self._log_trade({
                'action': 'BUY',
                'code': code,
                'name': signal.name,
                'qty': qty,
                'price': signal.current_price,
                'amount': signal.suggested_amount,
                'reason': signal.reason,
                'order_no': result['order_no'],
            })
            
            logger.info(f"매수 완료: [{code}] {signal.name} | "
                        f"{qty}주 × {signal.current_price:,.0f}원 = "
                        f"{signal.suggested_amount:,}원")
            return True
        else:
            logger.error(f"매수 실패: [{code}] {result['message']}")
            return False
    
    async def execute_sell(self, signal: ExitSignal) -> bool:
        """
        매도 실행
        
        Returns:
            성공 여부
        """
        code = signal.code
        qty = signal.sell_qty
        
        # 강제청산은 시장가, 나머지는 최유리지정가
        is_urgent = signal.exit_type in (
            ExitType.FORCE_LIQUIDATION,
            ExitType.ERROR_LIQUIDATION,
            ExitType.DAILY_LOSS_LIMIT,
        )
        order_label = "시장가" if is_urgent else "최유리지정가"
        logger.info(f"매도 실행: [{code}] {signal.name} | {qty}주 {order_label} | {signal.exit_type.value}")

        if is_urgent:
            result = await self.api.sell_market_order(code, qty)
        else:
            result = await self.api.sell_limit_order(code, qty, price=0)
        
        if result['success']:
            pos = self.positions.get(code)
            if pos:
                # 수수료/세금 반영 실현 손익
                gross_pnl = (signal.current_price - pos.entry_price) * qty
                buy_commission = pos.entry_price * qty * 0.00015
                sell_commission = signal.current_price * qty * 0.00015
                sell_tax = signal.current_price * qty * 0.0018
                realized_pnl = gross_pnl - buy_commission - sell_commission - sell_tax
                self._daily_pnl += realized_pnl
                
                # 포지션 업데이트
                if signal.sell_ratio >= 1.0 or pos.qty <= qty:
                    # 전량 매도
                    del self.positions[code]
                else:
                    # 부분 매도
                    pos.qty -= qty
                    pos.amount = int(pos.qty * pos.entry_price)
                    if signal.exit_type == ExitType.TAKE_PROFIT_1:
                        pos.tp1_triggered = True
            
            self._daily_trades += 1
            
            # 매매 로그 기록
            self._log_trade({
                'action': 'SELL',
                'code': code,
                'name': signal.name,
                'qty': qty,
                'price': signal.current_price,
                'entry_price': signal.entry_price,
                'pnl_pct': signal.pnl_pct,
                'exit_type': signal.exit_type.value,
                'reason': signal.reason,
                'order_no': result['order_no'],
            })
            
            pnl_str = f"{signal.pnl_pct:+.1f}%"
            logger.info(f"매도 완료: [{code}] {signal.name} | "
                        f"{qty}주 × {signal.current_price:,.0f}원 | "
                        f"수익률: {pnl_str} | {signal.exit_type.value}")
            return True
        else:
            logger.error(f"매도 실패: [{code}] {result['message']}")
            return False
    
    async def force_liquidate_all(self) -> int:
        """
        전 포지션 강제 청산 + 미체결 전량 취소
        
        Returns:
            청산된 종목 수
        """
        logger.warning("=" * 60)
        logger.warning("전 포지션 강제 청산 시작")
        logger.warning("=" * 60)
        
        # 1. 미체결 주문 전량 취소
        pending_orders = await self.api.get_pending_orders()
        for order in pending_orders:
            try:
                await self.api.cancel_order(
                    order['order_no'],
                    order['code'],
                    order['remaining_qty'],
                )
            except Exception as e:
                logger.error(f"미체결 취소 실패: {order['order_no']} - {e}")
        
        if pending_orders:
            logger.info(f"미체결 {len(pending_orders)}건 취소 완료")
        
        # 2. 보유 종목 전량 시장가 매도
        liquidated = 0
        for code, pos in list(self.positions.items()):
            if pos.qty > 0:
                try:
                    result = await self.api.sell_market_order(code, pos.qty)
                    if result['success']:
                        liquidated += 1
                        logger.info(f"청산: [{code}] {pos.name} | {pos.qty}주")
                except Exception as e:
                    logger.error(f"청산 실패: [{code}] {pos.name} - {e}")
        
        # 포지션 클리어
        self.positions.clear()
        
        logger.warning(f"강제 청산 완료: {liquidated}종목")
        return liquidated
    
    async def sync_positions(self):
        """계좌 잔고와 포지션 동기화"""
        balance = await self.api.get_balance()
        self._total_equity = balance['total_equity']

        # 실제 잔고 기반으로 포지션 업데이트
        api_positions = {p['code']: p for p in balance['positions']}

        now = datetime.now()
        for code in list(self.positions.keys()):
            if code in api_positions:
                ap = api_positions[code]
                self.positions[code].qty = ap['qty']
                self.positions[code].current_price = ap['current_price']
                self.positions[code].pnl_pct = ap['pnl_pct']
                self.positions[code].amount = ap['amount']
            else:
                # 매수 직후 체결 지연으로 API에 아직 미반영될 수 있음
                # 등록 후 60초 이내 포지션은 삭제하지 않음
                elapsed = (now - self.positions[code].entry_time).total_seconds()
                if elapsed > 60:
                    logger.info(f"포지션 동기화 제거: [{code}] (API 잔고에 없음)")
                    del self.positions[code]
                else:
                    logger.debug(f"포지션 유지: [{code}] (매수 후 {elapsed:.0f}초, 체결 대기)")
    
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
            }
            for code, pos in self.positions.items()
        }
    
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
    
    def _log_trade(self, trade: dict):
        """매매 기록을 JSONL 파일에 기록"""
        trade['timestamp'] = datetime.now().isoformat()
        trade['daily_pnl'] = self._daily_pnl
        trade['daily_pnl_pct'] = self.daily_pnl_pct
        
        try:
            with open(self.trade_log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(trade, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"매매 로그 기록 실패: {e}")
