"""
매수 신호 생성기
장중 실시간으로 감시 리스트 종목의 파란점선 돌파를 감지
"""

import logging
from datetime import datetime, time
from dataclasses import dataclass
from typing import List, Optional, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from scanner.market_condition import MarketCondition

logger = logging.getLogger(__name__)


@dataclass
class EntrySignal:
    """매수 신호"""
    code: str
    name: str
    signal_time: datetime
    current_price: float
    blue_line: float
    volume_ratio: float         # 당일 거래량 / 20일 평균
    has_watermelon: bool
    score: float
    suggested_qty: int          # 제안 매수 수량
    suggested_amount: int       # 제안 매수 금액
    reason: str
    # 전략 태깅 (스캘핑 전략 전용)
    strategy_type: str = "regular"  # "regular" | "scalping"
    scalp_tp_pct: float = 0.0
    scalp_sl_pct: float = 0.0
    scalp_hold_minutes: int = 0
    # 2026-04-07: 변동성 비례 SL/트레일링용
    intraday_atr: float = 0.0      # 1분봉 ATR (진입 시점)
    change_pct: float = 0.0        # 진입 시점 일일 등락률 (%)


class EntrySignalGenerator:
    """매수 신호 생성기"""
    
    def __init__(self, config: dict):
        strategy = config.get('strategy', {}).get('entry', {})
        self.volume_threshold = strategy.get('volume_threshold', 3.0)
        self.require_positive_candle = strategy.get('positive_candle', True)
        self.entry_start = self._parse_time(strategy.get('entry_start', '09:05'))
        self.entry_end = self._parse_time(strategy.get('entry_end', '14:30'))
        
        risk = config.get('risk', {})
        self.max_positions = risk.get('max_positions', 5)
        self.max_per_stock_pct = risk.get('max_per_stock_pct', 10.0)
        self.max_total_exposure_pct = risk.get('max_total_exposure_pct', 50.0)
        self.daily_loss_limit_pct = risk.get('daily_loss_limit_pct', -5.0)

        # 파란점선 돌파율 상한 (과열 추격 방지)
        self.max_breakout_pct = strategy.get('max_breakout_pct', 7.0)
    
    def check_entry(
        self,
        code: str,
        name: str,
        current_price: float,
        open_price: float,
        today_volume: int,
        avg_volume_20d: float,
        blue_line: float,
        has_watermelon: bool,
        score: float,
        current_positions: Dict[str, dict],
        total_equity: float,
        daily_pnl_pct: float,
        market_condition: Optional['MarketCondition'] = None,
    ) -> Optional[EntrySignal]:
        """
        매수 조건 체크

        모든 조건이 충족되면 EntrySignal 반환, 아니면 None
        """
        now = datetime.now()
        reasons = []

        # === 시장 상태 필터 ===
        if market_condition and not market_condition.entry_allowed:
            logger.warning(f"시장 상태 매수 차단: {market_condition.overall_level.value}")
            return None

        # === 시간 필터 ===
        if now.time() < self.entry_start:
            return None
        if now.time() > self.entry_end:
            return None

        # === 리스크 필터 ===
        # 일일 손실 한도 체크
        if daily_pnl_pct <= self.daily_loss_limit_pct:
            logger.warning(f"일일 손실 한도 도달: {daily_pnl_pct:.1f}% <= {self.daily_loss_limit_pct}%")
            return None

        # 최대 포지션 수 체크
        if len(current_positions) >= self.max_positions:
            return None

        # 이미 보유 중인 종목 체크
        if code in current_positions:
            return None

        # 총 투자 비중 체크
        total_invested = sum(p.get('amount', 0) for p in current_positions.values())
        current_exposure_pct = (total_invested / total_equity * 100) if total_equity > 0 else 0
        if current_exposure_pct >= self.max_total_exposure_pct:
            return None

        # === 매수 신호 조건 ===

        # 1. 파란점선 돌파 확인
        if current_price <= blue_line:
            return None
        breakout_pct = (current_price - blue_line) / blue_line * 100
        # 과열 추격 방지: 돌파율이 상한을 초과하면 진입 차단
        if breakout_pct > self.max_breakout_pct:
            logger.info(
                f"과열 진입 차단: [{code}] {name} 돌파 +{breakout_pct:.1f}% > "
                f"한도 +{self.max_breakout_pct:.1f}%")
            return None
        reasons.append(f"파란점선 돌파 +{breakout_pct:.1f}%")

        # 2. 거래량 폭증 확인
        volume_ratio = today_volume / avg_volume_20d if avg_volume_20d > 0 else 0
        if volume_ratio < self.volume_threshold:
            return None
        reasons.append(f"거래량 {volume_ratio:.1f}배")

        # 3. 양봉 확인
        if self.require_positive_candle and current_price <= open_price:
            return None
        reasons.append("양봉 확인")

        # 4. 수박지표 보너스
        if has_watermelon:
            reasons.append("수박지표 발생 종목")

        # === 매수 수량/금액 계산 ===
        max_amount = total_equity * (self.max_per_stock_pct / 100)
        # 남은 투자 가능 금액 체크
        remaining_budget = total_equity * (self.max_total_exposure_pct / 100) - total_invested
        actual_amount = min(max_amount, remaining_budget)

        # 시장 상태에 따른 포지션 크기 조정
        if market_condition and market_condition.position_size_multiplier < 1.0:
            actual_amount *= market_condition.position_size_multiplier
            reasons.append(f"시장상태 x{market_condition.position_size_multiplier}")

        if actual_amount < current_price:
            return None

        qty = int(actual_amount / current_price)
        if qty <= 0:
            return None

        return EntrySignal(
            code=code,
            name=name,
            signal_time=now,
            current_price=current_price,
            blue_line=blue_line,
            volume_ratio=volume_ratio,
            has_watermelon=has_watermelon,
            score=score,
            suggested_qty=qty,
            suggested_amount=int(qty * current_price),
            reason=" | ".join(reasons),
        )
    
    def check_entry_ymgp(
        self,
        code: str,
        name: str,
        current_price: float,
        open_price: float,
        bb20_upper: float,
        score: float,
        current_positions: Dict[str, dict],
        total_equity: float,
        daily_pnl_pct: float,
        market_condition: Optional['MarketCondition'] = None,
    ) -> Optional[EntrySignal]:
        """
        역매공파 전용 매수 조건 체크

        리스크 관리 로직은 기존 check_entry()와 동일.
        진입 조건:
          - 양봉 확인 (현재가 > 시가)
          - BB(20) 상한선 위 재확인 (현재가 >= BB20 상한)
        blue_line 필드에 BB(20) 상한선 값 저장.
        """
        now = datetime.now()
        reasons = []

        # === 시장 상태 필터 ===
        if market_condition and not market_condition.entry_allowed:
            return None

        # === 시간 필터 ===
        if now.time() < self.entry_start or now.time() > self.entry_end:
            return None

        # === 리스크 필터 (기존 동일) ===
        if daily_pnl_pct <= self.daily_loss_limit_pct:
            return None
        if len(current_positions) >= self.max_positions:
            return None
        if code in current_positions:
            return None

        total_invested = sum(p.get('amount', 0) for p in current_positions.values())
        current_exposure_pct = (total_invested / total_equity * 100) if total_equity > 0 else 0
        if current_exposure_pct >= self.max_total_exposure_pct:
            return None

        # === 역매공파 진입 조건 ===

        # 1. 양봉 확인
        if current_price <= open_price:
            return None
        reasons.append("양봉 확인")

        # 2. BB(20) 상한선 위 재확인
        if current_price < bb20_upper:
            return None
        bb_excess_pct = (current_price - bb20_upper) / bb20_upper * 100
        reasons.append(f"BB20 상한 돌파 +{bb_excess_pct:.1f}%")

        # === 매수 수량/금액 계산 (기존 동일) ===
        max_amount = total_equity * (self.max_per_stock_pct / 100)
        remaining_budget = total_equity * (self.max_total_exposure_pct / 100) - total_invested
        actual_amount = min(max_amount, remaining_budget)

        if market_condition and market_condition.position_size_multiplier < 1.0:
            actual_amount *= market_condition.position_size_multiplier
            reasons.append(f"시장상태 x{market_condition.position_size_multiplier}")

        if actual_amount < current_price:
            return None

        qty = int(actual_amount / current_price)
        if qty <= 0:
            return None

        return EntrySignal(
            code=code,
            name=name,
            signal_time=now,
            current_price=current_price,
            blue_line=bb20_upper,       # BB(20) 상한선을 blue_line 필드에 저장
            volume_ratio=0.0,           # 역매공파는 거래량 비율 미사용
            has_watermelon=False,       # 역매공파는 수박지표 미사용
            score=score,
            suggested_qty=qty,
            suggested_amount=int(qty * current_price),
            reason="역매공파 | " + " | ".join(reasons),
        )

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """시간 문자열 파싱 (HH:MM)"""
        h, m = map(int, time_str.split(':'))
        return time(h, m)
