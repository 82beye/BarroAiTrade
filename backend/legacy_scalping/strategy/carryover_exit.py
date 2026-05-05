"""
미청산 포지션 전용 청산 전략 (gradual + stop_loss_wide 혼합)

전일 미청산으로 넘어온 포지션에 대해:
  1. 확대 손절: 당일 시가 기준 -5% 추가 하락 시 전량 손절
  2. 반등 분할 청산:
     - BB(20) 중앙선 도달 → 50% 매도
     - BB(20) 상단선 도달 → 나머지 전량 매도
  3. 장 마감(14:50) 시 잔량 전량 청산 (당일매매 동일)

당일 매수 포지션의 -2% 손절과 완전히 분리하여
미청산 포지션이 시작과 동시에 즉시 손절되는 문제를 방지한다.
"""

import logging
from datetime import datetime, time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CarryoverState:
    """미청산 포지션의 당일 추적 상태"""
    code: str
    name: str
    entry_price: float          # 원래 매수가
    qty: int                    # 보유 수량 (분할 매도 시 감소)
    open_price: float           # 당일 시가 (확대 손절 기준)
    bb20_mid: float = 0.0      # BB(20) 중앙선
    bb20_upper: float = 0.0    # BB(20) 상단선
    phase1_done: bool = False   # 1차 분할 매도 완료 여부


class CarryoverExitStrategy:
    """
    미청산 포지션 전용 청산 전략

    gradual (반등 분할 청산):
      - phase1: 현재가 >= BB(20) 중앙선 → 50% 매도
      - phase2: 현재가 >= BB(20) 상단선 → 나머지 전량 매도

    stop_loss_wide (확대 손절):
      - 당일 시가 기준 additional_sl_pct% 추가 하락 시 전량 손절
      - 기본값: -5% (시가 10,000원이면 9,500원에 손절)
    """

    def __init__(self, config: dict):
        co_config = config.get('risk', {}).get('carryover', {})
        self.additional_sl_pct = co_config.get('additional_stop_loss_pct', -5.0)
        self.phase1_sell_ratio = co_config.get('phase1_sell_ratio', 0.5)
        self.force_liquidation_time = time(14, 50)

    def check_exit(
        self,
        current_price: float,
        state: CarryoverState,
        daily_pnl_pct: float,
        daily_loss_limit_pct: float = -5.0,
    ) -> Optional[dict]:
        """
        미청산 포지션 매도 조건 체크

        Returns:
            None — 매도 조건 미충족
            dict — {'exit_type', 'sell_ratio', 'sell_qty', 'reason'}
        """
        now = datetime.now()
        qty = state.qty
        if qty <= 0:
            return None

        entry_price = state.entry_price
        open_price = state.open_price
        pnl_from_entry = (current_price - entry_price) / entry_price * 100
        pnl_from_open = (
            (current_price - open_price) / open_price * 100
            if open_price > 0 else 0)

        # ── 1. 14:50 강제 청산 (당일매매 동일) ──
        if now.time() >= self.force_liquidation_time:
            return {
                'exit_type': '미청산_강제청산',
                'sell_ratio': 1.0,
                'sell_qty': qty,
                'reason': (
                    "14:50 장마감 미청산 청산 | "
                    "매수가 대비: %+.1f%% | 시가 대비: %+.1f%%"
                    % (pnl_from_entry, pnl_from_open)),
            }

        # ── 2. 일일 손실 한도 (계좌 전체) ──
        if daily_pnl_pct <= daily_loss_limit_pct:
            return {
                'exit_type': '미청산_일일손실한도',
                'sell_ratio': 1.0,
                'sell_qty': qty,
                'reason': (
                    "일일 손실 한도 (%.1f%%) | 미청산 종목 전량 청산"
                    % daily_pnl_pct),
            }

        # ── 3. 확대 손절: 당일 시가 기준 추가 하락 ──
        if open_price > 0 and pnl_from_open <= self.additional_sl_pct:
            return {
                'exit_type': '미청산_확대손절',
                'sell_ratio': 1.0,
                'sell_qty': qty,
                'reason': (
                    "확대 손절 | 시가(%s) 대비 %+.1f%% (한도: %.1f%%) | "
                    "매수가 대비: %+.1f%%"
                    % (format(int(open_price), ','),
                       pnl_from_open, self.additional_sl_pct,
                       pnl_from_entry)),
            }

        # ── 4. 반등 분할 청산 (BB 기반) ──
        # BB 값이 아직 설정되지 않았으면 분할 매도 스킵
        if state.bb20_mid <= 0 or state.bb20_upper <= 0:
            return None

        # phase2: BB(20) 상단 도달 → 전량 매도
        if current_price >= state.bb20_upper:
            return {
                'exit_type': '미청산_반등청산_2차',
                'sell_ratio': 1.0,
                'sell_qty': qty,
                'reason': (
                    "BB(20) 상단(%s) 도달 → 전량 청산 | "
                    "매수가 대비: %+.1f%% | 시가 대비: %+.1f%%"
                    % (format(int(state.bb20_upper), ','),
                       pnl_from_entry, pnl_from_open)),
            }

        # phase1: BB(20) 중앙선 도달 → 50% 매도 (1회만)
        if not state.phase1_done and current_price >= state.bb20_mid:
            sell_qty = max(1, int(qty * self.phase1_sell_ratio))
            return {
                'exit_type': '미청산_반등청산_1차',
                'sell_ratio': self.phase1_sell_ratio,
                'sell_qty': sell_qty,
                'reason': (
                    "BB(20) 중앙(%s) 도달 → %d%%매도 (%d주/%d주) | "
                    "매수가 대비: %+.1f%%"
                    % (format(int(state.bb20_mid), ','),
                       int(self.phase1_sell_ratio * 100),
                       sell_qty, qty, pnl_from_entry)),
            }

        return None
