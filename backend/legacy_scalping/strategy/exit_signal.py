"""
매도/청산 신호 생성기
- 익절: +3% → 50% 매도, +5% → 전량 매도
- 손절: -2% → 전량 매도
- 브레이크이븐 스톱: 1차 익절 후 잔량은 매입가+0.3%에서 보호 매도
- 사전 청산: 14:30에 소폭 손실 종목 정리
- 강제 청산: 14:50 → 전량 시장가 매도
"""

import logging
from datetime import datetime, time
from dataclasses import dataclass
from typing import Optional, Dict
from enum import Enum

logger = logging.getLogger(__name__)


class ExitType(Enum):
    TAKE_PROFIT_1 = "익절_1차"          # +3% 절반 매도
    TAKE_PROFIT_2 = "익절_2차"          # +5% 전량 매도
    STOP_LOSS = "손절"                   # -2% 전량 매도
    FORCE_LIQUIDATION = "강제청산"       # 14:50 전량 매도
    DAILY_LOSS_LIMIT = "일일손실한도"     # 일일 -5% 전량 매도
    ERROR_LIQUIDATION = "오류청산"        # 오류 발생 시 전량 매도
    CARRYOVER_PHASE1 = "미청산_반등1차"  # 미청산 BB(20) 중앙선 50% 매도
    CARRYOVER_PHASE2 = "미청산_반등2차"  # 미청산 BB(20) 상단선 전량 매도
    BREAKEVEN_STOP = "브레이크이븐"     # 1차 익절 후 매입가 보호 매도
    PRE_LIQUIDATION = "사전청산"        # 14:30 소폭 손실 정리
    SCALP_TAKE_PROFIT = "스캘핑_익절"   # 스캘핑 동적 익절
    SCALP_STOP_LOSS = "스캘핑_손절"     # 스캘핑 동적 손절
    SCALP_TIME_EXIT = "스캘핑_시간초과" # 스캘핑 보유 시간 초과
    SCALP_TRAILING_STOP = "스캘핑_트레일링"  # 스캘핑 트레일링 스톱


@dataclass
class ExitSignal:
    """매도 신호"""
    code: str
    name: str
    signal_time: datetime
    exit_type: ExitType
    current_price: float
    entry_price: float
    pnl_pct: float              # 수익률 (%)
    sell_ratio: float           # 매도 비율 (0.5 = 50%, 1.0 = 전량)
    sell_qty: int               # 매도 수량
    reason: str


class ExitSignalGenerator:
    """매도 신호 생성기"""
    
    def __init__(self, config: dict):
        exit_config = config.get('strategy', {}).get('exit', {})
        
        # 익절 설정
        tp1 = exit_config.get('take_profit_1', {})
        self.tp1_pct = tp1.get('pct', 3.0)
        self.tp1_sell_ratio = tp1.get('sell_ratio', 0.5)
        
        tp2 = exit_config.get('take_profit_2', {})
        self.tp2_pct = tp2.get('pct', 5.0)
        self.tp2_sell_ratio = tp2.get('sell_ratio', 1.0)
        
        # 손절 설정
        sl = exit_config.get('stop_loss', {})
        self.sl_pct = sl.get('pct', -2.0)
        
        # 강제 청산 설정
        fl = exit_config.get('force_liquidation', {})
        self.force_liquidation_time = self._parse_time(fl.get('time', '14:50'))

        # 사전 청산 시간 (14:30 — 소폭 손실 종목 정리)
        self.pre_liquidation_time = self._parse_time(
            fl.get('pre_liquidation_time', '14:30'))
        # 사전 청산 대상: PnL이 이 범위 이내면 정리 (이익 반납 방지)
        self.pre_liquidation_pnl_range = fl.get(
            'pre_liquidation_pnl_range', (-2.0, 1.0))

        # 브레이크이븐 스톱: 1차 익절 후 잔량 보호 (매입가 + buffer%)
        self.breakeven_buffer_pct = exit_config.get(
            'breakeven_buffer_pct', 0.3)

        # 일일 손실 한도
        risk = config.get('risk', {})
        self.daily_loss_limit_pct = risk.get('daily_loss_limit_pct', -5.0)

        # 스캘핑 트레일링 스톱 설정
        scalp_cfg = config.get('strategy', {}).get('scalping', {})
        trailing = scalp_cfg.get('trailing_stop', {})
        self.trailing_activation_pct = trailing.get('activation_pct', 1.0)
        self.trailing_trail_pct = trailing.get('trail_pct', -0.8)
        self.trailing_breakeven_pct = trailing.get('breakeven_pct', 0.5)

        # [Case 1] 5단계 변동성 비례 트레일링 스톱
        # 2026-04-07: 프로미천(+29%)/풍산홀딩스(+30%) 놓침 → trail 폭 대폭 확대
        # 기존 3단계(-0.6/-0.8/-1.0) → 5단계(-1.0/-1.2/-1.5/-2.0/-2.5)
        self.trailing_tiers = trailing.get('tiers', [
            {'above_pct': 5.0, 'trail_pct': -1.0},
            {'above_pct': 4.0, 'trail_pct': -1.2},
            {'above_pct': 3.0, 'trail_pct': -1.5},
            {'above_pct': 2.0, 'trail_pct': -2.0},
            {'above_pct': 1.5, 'trail_pct': -2.5},
        ])

        # 변동성 비례 트레일링 설정
        vol_trail = trailing.get('volatility_scaling', {})
        self.trail_vol_enabled = vol_trail.get('enabled', True)
        self.trail_atr_multiplier = vol_trail.get('atr_multiplier', 2.0)
        self.trail_change_threshold = vol_trail.get('change_pct_threshold', 15.0)
        self.trail_change_multiplier = vol_trail.get('change_pct_multiplier', 1.3)

        # [Case 2] 시간별 단계 SL (변동성 비례 확대)
        # 2026-04-07: -1.0/-1.2/-1.5 → -1.5/-2.0/-2.5 (프로미천/풍산홀딩스 조기 손절 방지)
        self.time_based_sl = scalp_cfg.get('time_based_sl', {})
        self.time_sl_enabled = self.time_based_sl.get('enabled', True)
        self.time_sl_tiers = self.time_based_sl.get('tiers', [
            {'within_seconds': 120, 'sl_pct': -1.5},
            {'within_seconds': 300, 'sl_pct': -2.0},
        ])
        self.time_sl_final = self.time_based_sl.get('final_sl_pct', -2.5)

        # 변동성 비례 시간SL 설정
        time_sl_vol = self.time_based_sl.get('volatility_scaling', {})
        self.time_sl_vol_enabled = time_sl_vol.get('enabled', True)
        self.time_sl_atr_multiplier = time_sl_vol.get('atr_multiplier', 2.5)
        self.time_sl_change_threshold = time_sl_vol.get('change_pct_threshold', 15.0)
        self.time_sl_change_multiplier = time_sl_vol.get('change_pct_multiplier', 1.3)

        # 2026-04-07: 최소 보유 시간 (30→90초, 노이즈 손절 방지 강화)
        # 프로미천: 동일 분 매수/매도 → 최소 90초 보호
        self.min_hold_seconds = scalp_cfg.get('min_hold_seconds', 90)
    
    def check_exit(
        self,
        code: str,
        name: str,
        current_price: float,
        position: dict,
        daily_pnl_pct: float,
        stop_loss_override: Optional[float] = None,
    ) -> Optional[ExitSignal]:
        """
        매도 조건 체크

        우선순위:
        1. 강제 청산 시간 (14:50)
        2. 일일 손실 한도
        3. 손절 (-2%, 또는 시장 상태에 따라 override)
        4. 익절 2차 (+5%)
        5. 익절 1차 (+3%)

        Args:
            code: 종목코드
            name: 종목명
            current_price: 현재가
            position: 포지션 정보 {
                'entry_price': float,  # 매수 평균가
                'qty': int,            # 보유 수량
                'tp1_triggered': bool, # 1차 익절 실행 여부
            }
            daily_pnl_pct: 당일 총 손익률
            stop_loss_override: 시장 상태에 따른 손절 오버라이드 (%)
        """
        now = datetime.now()
        entry_price = position['entry_price']
        qty = position['qty']
        tp1_done = position.get('tp1_triggered', False)

        if qty <= 0:
            return None

        pnl_pct = (current_price - entry_price) / entry_price * 100

        # 1. 강제 청산 (14:50)
        if now.time() >= self.force_liquidation_time:
            return ExitSignal(
                code=code,
                name=name,
                signal_time=now,
                exit_type=ExitType.FORCE_LIQUIDATION,
                current_price=current_price,
                entry_price=entry_price,
                pnl_pct=pnl_pct,
                sell_ratio=1.0,
                sell_qty=qty,
                reason=f"14:50 강제 청산 | 수익률: {pnl_pct:+.1f}%",
            )

        # 2. 일일 손실 한도
        if daily_pnl_pct <= self.daily_loss_limit_pct:
            return ExitSignal(
                code=code,
                name=name,
                signal_time=now,
                exit_type=ExitType.DAILY_LOSS_LIMIT,
                current_price=current_price,
                entry_price=entry_price,
                pnl_pct=pnl_pct,
                sell_ratio=1.0,
                sell_qty=qty,
                reason=f"일일 손실 한도 도달 ({daily_pnl_pct:.1f}%) | 종목 수익률: {pnl_pct:+.1f}%",
            )

        # 2.5. 사전 청산 (14:30) — 소폭 손실/미미한 이익 종목 정리
        if now.time() >= self.pre_liquidation_time and now.time() < self.force_liquidation_time:
            pnl_low, pnl_high = self.pre_liquidation_pnl_range
            if pnl_low <= pnl_pct <= pnl_high:
                return ExitSignal(
                    code=code,
                    name=name,
                    signal_time=now,
                    exit_type=ExitType.PRE_LIQUIDATION,
                    current_price=current_price,
                    entry_price=entry_price,
                    pnl_pct=pnl_pct,
                    sell_ratio=1.0,
                    sell_qty=qty,
                    reason=(
                        f"14:30 사전 청산 | 수익률 {pnl_pct:+.1f}% "
                        f"(강제청산 전 정리)"),
                )

        # 2.7. 브레이크이븐 스톱 (1차 익절 후 잔량 보호)
        if tp1_done and pnl_pct <= self.breakeven_buffer_pct:
            return ExitSignal(
                code=code,
                name=name,
                signal_time=now,
                exit_type=ExitType.BREAKEVEN_STOP,
                current_price=current_price,
                entry_price=entry_price,
                pnl_pct=pnl_pct,
                sell_ratio=1.0,
                sell_qty=qty,
                reason=(
                    f"브레이크이븐 스톱 | 1차 익절 후 "
                    f"수익률 {pnl_pct:+.1f}% ≤ +{self.breakeven_buffer_pct}% "
                    f"→ 이익 반납 방지"),
            )

        # 3. 손절 (시장 상태에 따라 더 타이트하게 적용)
        effective_sl = stop_loss_override if stop_loss_override is not None else self.sl_pct
        if pnl_pct <= effective_sl:
            sl_note = f" [시장상태 override]" if stop_loss_override is not None else ""
            return ExitSignal(
                code=code,
                name=name,
                signal_time=now,
                exit_type=ExitType.STOP_LOSS,
                current_price=current_price,
                entry_price=entry_price,
                pnl_pct=pnl_pct,
                sell_ratio=1.0,
                sell_qty=qty,
                reason=f"손절 {pnl_pct:+.1f}% (한도: {effective_sl}%){sl_note}",
            )
        
        # 4. 익절 2차 (+5%)
        if pnl_pct >= self.tp2_pct:
            return ExitSignal(
                code=code,
                name=name,
                signal_time=now,
                exit_type=ExitType.TAKE_PROFIT_2,
                current_price=current_price,
                entry_price=entry_price,
                pnl_pct=pnl_pct,
                sell_ratio=1.0,
                sell_qty=qty,
                reason=f"익절 2차 {pnl_pct:+.1f}% (목표: +{self.tp2_pct}%)",
            )
        
        # 5. 익절 1차 (+3%) - 아직 실행 안 했을 때만
        if not tp1_done and pnl_pct >= self.tp1_pct:
            sell_qty = max(1, int(qty * self.tp1_sell_ratio))
            return ExitSignal(
                code=code,
                name=name,
                signal_time=now,
                exit_type=ExitType.TAKE_PROFIT_1,
                current_price=current_price,
                entry_price=entry_price,
                pnl_pct=pnl_pct,
                sell_ratio=self.tp1_sell_ratio,
                sell_qty=sell_qty,
                reason=f"익절 1차 {pnl_pct:+.1f}% | {sell_qty}주/{qty}주 매도",
            )
        
        return None
    
    def check_scalping_exit(
        self,
        code: str,
        name: str,
        current_price: float,
        position: dict,
        daily_pnl_pct: float,
    ) -> Optional[ExitSignal]:
        """
        스캘핑 포지션 전용 청산 체크

        일반 매매와 다른 점:
          - 동적 TP/SL (에이전트 팀이 종목별로 산출)
          - 분할 매도 없이 전량 매도
          - 보유 시간 제한 (초과 시 강제 청산)

        우선순위:
          1. 강제 청산 (14:50)
          2. 일일 손실 한도
          3. 스캘핑 손절 (동적)
          4. 스캘핑 익절 (동적)
          5. 보유 시간 초과
        """
        now = datetime.now()
        entry_price = position['entry_price']
        qty = position['qty']

        if qty <= 0:
            return None

        pnl_pct = (current_price - entry_price) / entry_price * 100

        # 1. 강제 청산 (14:50)
        if now.time() >= self.force_liquidation_time:
            return ExitSignal(
                code=code, name=name, signal_time=now,
                exit_type=ExitType.FORCE_LIQUIDATION,
                current_price=current_price, entry_price=entry_price,
                pnl_pct=pnl_pct, sell_ratio=1.0, sell_qty=qty,
                reason=f"14:50 강제 청산 (스캘핑) | {pnl_pct:+.1f}%",
            )

        # 2. 일일 손실 한도
        if daily_pnl_pct <= self.daily_loss_limit_pct:
            return ExitSignal(
                code=code, name=name, signal_time=now,
                exit_type=ExitType.DAILY_LOSS_LIMIT,
                current_price=current_price, entry_price=entry_price,
                pnl_pct=pnl_pct, sell_ratio=1.0, sell_qty=qty,
                reason=(
                    f"일일 손실 한도 ({daily_pnl_pct:.1f}%) | "
                    f"스캘핑 {pnl_pct:+.1f}%"),
            )

        # 3. 스캘핑 손절 (동적) — 시간별 단계 SL + 브레이크이븐
        scalp_sl = position.get('scalp_sl_pct', -1.5)
        trailing_active = position.get('scalp_trailing_active', False)

        # 2026-04-07: 최소 보유 시간 가드 (30→90초, 노이즈 손절 방지)
        entry_time = position.get('entry_time')
        if entry_time and self.min_hold_seconds > 0:
            if isinstance(entry_time, str):
                _et = datetime.fromisoformat(entry_time)
            else:
                _et = entry_time
            elapsed = (now - _et).total_seconds()
            if elapsed < self.min_hold_seconds and pnl_pct > -3.0:
                # 극단적 손실(-3% 이상)이 아니면 조기 청산 방지
                # 2026-04-07: -2.0→-3.0% (90초 보호 해제 조건 완화)
                return None

        # [Case 2] 시간별 단계 SL (변동성 비례 확대)
        # 2026-04-07: 고정 SL 대신 ATR 기반 동적 하한선 적용
        effective_sl = scalp_sl
        if self.time_sl_enabled and entry_time and not trailing_active:
            if isinstance(entry_time, str):
                entry_time_dt = datetime.fromisoformat(entry_time)
            else:
                entry_time_dt = entry_time
            elapsed_sec = (now - entry_time_dt).total_seconds()

            # 시간 구간별 SL 적용 (짧은 시간부터 매칭)
            time_sl = self.time_sl_final
            for tier in self.time_sl_tiers:
                if elapsed_sec <= tier['within_seconds']:
                    time_sl = tier['sl_pct']
                    break

            # 변동성 비례 SL: ATR 기반 동적 하한선
            # 고변동 모멘텀 종목의 자연 변동폭을 흡수
            intraday_atr = position.get('intraday_atr', 0)
            change_pct = position.get('change_pct', 0)
            if self.time_sl_vol_enabled and intraday_atr > 0 and entry_price > 0:
                atr_pct = intraday_atr / entry_price * 100
                vol_sl = -(atr_pct * self.time_sl_atr_multiplier)
                # 고모멘텀 종목 (+15% 이상) 추가 완화
                if change_pct >= self.time_sl_change_threshold:
                    vol_sl *= self.time_sl_change_multiplier
                # 변동성 SL이 시간SL보다 넓으면 변동성 SL 적용
                time_sl = min(time_sl, vol_sl)

            # 시간별 SL이 기본 SL보다 타이트하면 적용
            if time_sl > effective_sl:
                effective_sl = time_sl

        # 브레이크이븐: 트레일링 활성화 후 손절선을 본전(+0.3%)으로 이동
        if trailing_active and pnl_pct <= self.trailing_breakeven_pct:
            return ExitSignal(
                code=code, name=name, signal_time=now,
                exit_type=ExitType.SCALP_TRAILING_STOP,
                current_price=current_price, entry_price=entry_price,
                pnl_pct=pnl_pct, sell_ratio=1.0, sell_qty=qty,
                reason=(
                    f"트레일링 브레이크이븐 {pnl_pct:+.1f}% "
                    f"(본전선: +{self.trailing_breakeven_pct}%)"),
            )

        if pnl_pct <= effective_sl:
            sl_note = ""
            if effective_sl != scalp_sl:
                if isinstance(entry_time, str):
                    entry_time_dt = datetime.fromisoformat(entry_time)
                else:
                    entry_time_dt = entry_time
                elapsed_sec = (now - entry_time_dt).total_seconds()
                sl_note = f" [시간SL {elapsed_sec:.0f}초]"
            return ExitSignal(
                code=code, name=name, signal_time=now,
                exit_type=ExitType.SCALP_STOP_LOSS,
                current_price=current_price, entry_price=entry_price,
                pnl_pct=pnl_pct, sell_ratio=1.0, sell_qty=qty,
                reason=f"스캘핑 손절 {pnl_pct:+.1f}% (한도: {effective_sl}%){sl_note}",
            )

        # 3.5. 트레일링 스톱 (5단계 변동성 비례 트레일링)
        # 2026-04-07: 프로미천(+29%)/풍산홀딩스(+30%) 놓침 → trail 폭 대폭 확대
        if trailing_active:
            high_wm = position.get('scalp_high_watermark', entry_price)
            if high_wm > entry_price:
                high_pnl = (high_wm - entry_price) / entry_price * 100
                drop_from_high = (
                    (current_price - high_wm) / high_wm * 100)

                # 수익 구간별 trail 폭 결정 (높은 구간부터 매칭)
                trail_threshold = self.trailing_trail_pct  # 기본값
                for tier in self.trailing_tiers:
                    if high_pnl >= tier['above_pct']:
                        trail_threshold = tier['trail_pct']
                        break

                # 변동성 비례 트레일링: ATR 기반 동적 trail 폭
                intraday_atr = position.get('intraday_atr', 0)
                change_pct = position.get('change_pct', 0)
                if self.trail_vol_enabled and intraday_atr > 0 and entry_price > 0:
                    atr_pct = intraday_atr / entry_price * 100
                    vol_trail = -(atr_pct * self.trail_atr_multiplier)
                    # 고모멘텀 종목 추가 완화
                    if change_pct >= self.trail_change_threshold:
                        vol_trail *= self.trail_change_multiplier
                    # 변동성 trail이 기본보다 넓으면 적용
                    trail_threshold = min(trail_threshold, vol_trail)

                if drop_from_high <= trail_threshold:
                    return ExitSignal(
                        code=code, name=name, signal_time=now,
                        exit_type=ExitType.SCALP_TRAILING_STOP,
                        current_price=current_price, entry_price=entry_price,
                        pnl_pct=pnl_pct, sell_ratio=1.0, sell_qty=qty,
                        reason=(
                            f"트레일링 스톱 | 고점 {high_wm:,.0f}원"
                            f"(+{high_pnl:.1f}%) → 현재 {drop_from_high:+.1f}%"
                            f" [trail {trail_threshold:.1f}%]"),
                    )

        # 4. 스캘핑 익절 (동적, 전량)
        scalp_tp = position.get('scalp_tp_pct', 3.0)
        if pnl_pct >= scalp_tp:
            return ExitSignal(
                code=code, name=name, signal_time=now,
                exit_type=ExitType.SCALP_TAKE_PROFIT,
                current_price=current_price, entry_price=entry_price,
                pnl_pct=pnl_pct, sell_ratio=1.0, sell_qty=qty,
                reason=f"스캘핑 익절 {pnl_pct:+.1f}% (목표: +{scalp_tp}%)",
            )

        # 5. 보유 시간 초과
        hold_limit = position.get('scalp_hold_minutes', 15)
        if hold_limit > 0:
            entry_time = position.get('entry_time')
            if entry_time:
                if isinstance(entry_time, str):
                    entry_time = datetime.fromisoformat(entry_time)
                elapsed = (now - entry_time).total_seconds() / 60
                if elapsed >= hold_limit:
                    return ExitSignal(
                        code=code, name=name, signal_time=now,
                        exit_type=ExitType.SCALP_TIME_EXIT,
                        current_price=current_price, entry_price=entry_price,
                        pnl_pct=pnl_pct, sell_ratio=1.0, sell_qty=qty,
                        reason=(
                            f"스캘핑 시간초과 {elapsed:.0f}분 "
                            f"(한도: {hold_limit}분) | {pnl_pct:+.1f}%"),
                    )

        return None

    def force_liquidate_all(self, positions: Dict[str, dict]) -> list:
        """
        전 포지션 강제 청산 신호 생성 (오류 시 사용)
        """
        signals = []
        now = datetime.now()
        
        for code, pos in positions.items():
            if pos.get('qty', 0) > 0:
                signals.append(ExitSignal(
                    code=code,
                    name=pos.get('name', code),
                    signal_time=now,
                    exit_type=ExitType.ERROR_LIQUIDATION,
                    current_price=pos.get('current_price', pos['entry_price']),
                    entry_price=pos['entry_price'],
                    pnl_pct=0,
                    sell_ratio=1.0,
                    sell_qty=pos['qty'],
                    reason="오류 발생 긴급 청산",
                ))
        
        return signals
    
    @staticmethod
    def _parse_time(time_str: str) -> time:
        h, m = map(int, time_str.split(':'))
        return time(h, m)
