"""
전략 백테스팅 엔진 (Strategy Backtesting Engine)

기능:
  - OHLCV 히스토리 데이터로 전략 시뮬레이션
  - 포지션 진입/청산 시뮬레이션 (분할 익절 + 손절)
  - 성과 지표 계산: 승률, 수익률, MDD, Sharpe
  - 전략별 백테스트 리포트 생성
  - 합성 데이터 생성기 (실 데이터 없을 때 테스트 용도)

지원 전략:
  - FZoneStrategy   (F존/SF존)
  - BlueLineStrategy (블루라인)
  - CryptoBreakoutStrategy (암호화폐 돌파)
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Protocol, Tuple, Type

import numpy as np
import pandas as pd

from backend.models.market import OHLCV, MarketType
from backend.models.signal import EntrySignal

logger = logging.getLogger(__name__)


# ── 전략 프로토콜 ──────────────────────────────────────────────────────────────

class StrategyProtocol(Protocol):
    """백테스터가 사용하는 전략 인터페이스"""
    STRATEGY_ID: str

    def analyze(
        self,
        symbol: str,
        name: str,
        candles: List[OHLCV],
        market_type: MarketType,
    ) -> Optional[EntrySignal]: ...


# ── 전략별 청산 파라미터 ────────────────────────────────────────────────────────

@dataclass
class ExitParams:
    """청산 파라미터"""
    take_profit_1_pct: float   # 1차 익절 수익률
    take_profit_1_ratio: float # 1차 익절 시 청산 비율 (0~1)
    take_profit_2_pct: float   # 2차 익절 수익률
    stop_loss_pct: float       # 손절 기준 (음수)
    max_hold_candles: int = 20 # 최대 보유 기간 (캔들 수)


# 전략별 기본 청산 파라미터
STRATEGY_EXIT_PARAMS: Dict[str, ExitParams] = {
    "f_zone_v1": ExitParams(
        take_profit_1_pct=0.03,
        take_profit_1_ratio=0.5,
        take_profit_2_pct=0.05,
        stop_loss_pct=-0.02,
    ),
    "blue_line_v1": ExitParams(
        take_profit_1_pct=0.05,
        take_profit_1_ratio=0.5,
        take_profit_2_pct=0.08,
        stop_loss_pct=-0.03,
    ),
    "crypto_breakout_v1": ExitParams(
        take_profit_1_pct=0.08,
        take_profit_1_ratio=0.5,
        take_profit_2_pct=0.15,
        stop_loss_pct=-0.04,
    ),
}

DEFAULT_EXIT_PARAMS = ExitParams(
    take_profit_1_pct=0.03,
    take_profit_1_ratio=0.5,
    take_profit_2_pct=0.05,
    stop_loss_pct=-0.02,
)


# ── 거래 결과 ─────────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    """단일 거래 결과"""
    symbol: str
    strategy_id: str
    entry_time: datetime
    entry_price: float
    entry_signal_score: float

    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""           # "tp1", "tp2", "stop_loss", "max_hold", "end_of_data"

    tp1_filled: bool = False        # 1차 익절 완료 여부
    tp1_exit_price: Optional[float] = None
    tp2_exit_price: Optional[float] = None

    hold_candles: int = 0
    pnl_pct: float = 0.0            # 전체 거래 수익률 (가중 평균)
    is_winner: bool = False

    def summary(self) -> str:
        if self.exit_time is None:
            return f"{self.symbol} 미청산 진입가={self.entry_price:.2f}"
        pnl_sign = "+" if self.pnl_pct >= 0 else ""
        return (
            f"{self.symbol} {pnl_sign}{self.pnl_pct*100:.2f}% "
            f"({self.exit_reason}) "
            f"진입={self.entry_price:.2f} 청산={self.exit_price:.2f} "
            f"{self.hold_candles}봉"
        )


# ── 성과 지표 ─────────────────────────────────────────────────────────────────

@dataclass
class BacktestMetrics:
    """백테스트 성과 지표"""
    total_trades: int = 0
    win_trades: int = 0
    lose_trades: int = 0
    win_rate: float = 0.0           # 승률 (0~1)

    total_return_pct: float = 0.0   # 누적 수익률
    avg_return_pct: float = 0.0     # 거래당 평균 수익률
    avg_win_pct: float = 0.0        # 승리 거래 평균 수익률
    avg_loss_pct: float = 0.0       # 패배 거래 평균 손실률
    profit_factor: float = 0.0      # 총이익 / 총손실

    max_drawdown: float = 0.0       # 최대 낙폭 (MDD, 0~1)
    sharpe_ratio: float = 0.0       # 샤프 지수 (연율화)
    annualized_return: float = 0.0  # 연율화 수익률

    avg_hold_candles: float = 0.0   # 평균 보유 기간 (봉)

    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "win_trades": self.win_trades,
            "lose_trades": self.lose_trades,
            "win_rate": round(self.win_rate * 100, 2),
            "total_return_pct": round(self.total_return_pct * 100, 2),
            "avg_return_pct": round(self.avg_return_pct * 100, 2),
            "avg_win_pct": round(self.avg_win_pct * 100, 2),
            "avg_loss_pct": round(self.avg_loss_pct * 100, 2),
            "profit_factor": round(self.profit_factor, 2),
            "max_drawdown": round(self.max_drawdown * 100, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "annualized_return": round(self.annualized_return * 100, 2),
            "avg_hold_candles": round(self.avg_hold_candles, 1),
        }


# ── 백테스트 리포트 ────────────────────────────────────────────────────────────

@dataclass
class BacktestReport:
    """전체 백테스트 결과 리포트"""
    strategy_id: str
    symbol: str
    market_type: MarketType
    period_start: datetime
    period_end: datetime

    initial_capital: float
    final_capital: float

    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)  # 자산 추이

    total_candles: int = 0
    signal_count: int = 0           # 신호 발생 횟수 (실제 진입 포함)

    def print_summary(self) -> None:
        """요약 출력"""
        m = self.metrics
        print(f"\n{'='*60}")
        print(f"📊 백테스트 리포트: {self.strategy_id} / {self.symbol}")
        print(f"{'='*60}")
        print(f"기간:       {self.period_start:%Y-%m-%d} ~ {self.period_end:%Y-%m-%d}")
        print(f"초기자본:   {self.initial_capital:,.0f}원")
        print(f"최종자본:   {self.final_capital:,.0f}원")
        print(f"─{'─'*58}")
        print(f"총 거래:    {m.total_trades}건  (승: {m.win_trades} / 패: {m.lose_trades})")
        print(f"승률:       {m.win_rate*100:.1f}%")
        print(f"누적수익률: {m.total_return_pct*100:+.2f}%")
        print(f"평균수익률: {m.avg_return_pct*100:+.2f}% / 거래")
        print(f"평균승리:   {m.avg_win_pct*100:+.2f}%")
        print(f"평균손실:   {m.avg_loss_pct*100:+.2f}%")
        print(f"Profit Factor: {m.profit_factor:.2f}")
        print(f"MDD:        {m.max_drawdown*100:.2f}%")
        print(f"Sharpe:     {m.sharpe_ratio:.2f}")
        print(f"연율수익률: {m.annualized_return*100:+.2f}%")
        print(f"평균보유:   {m.avg_hold_candles:.1f}봉")
        print(f"{'='*60}\n")

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "market_type": self.market_type.value,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "initial_capital": self.initial_capital,
            "final_capital": round(self.final_capital, 2),
            "total_candles": self.total_candles,
            "signal_count": self.signal_count,
            "metrics": self.metrics.to_dict(),
            "trades": [
                {
                    "entry_time": t.entry_time.isoformat(),
                    "entry_price": t.entry_price,
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "exit_price": t.exit_price,
                    "exit_reason": t.exit_reason,
                    "pnl_pct": round(t.pnl_pct * 100, 4),
                    "hold_candles": t.hold_candles,
                    "is_winner": t.is_winner,
                    "signal_score": t.entry_signal_score,
                }
                for t in self.trades
            ],
        }


# ── 백테스팅 엔진 ──────────────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    """백테스터 설정"""
    initial_capital: float = 10_000_000.0   # 초기 자본 (1천만원)
    position_size_pct: float = 0.1          # 거래당 자본 비율 (10%)
    commission_pct: float = 0.00015         # 수수료 (0.015%)
    slippage_pct: float = 0.001             # 슬리피지 (0.1%)
    min_signal_score: float = 4.0           # 최소 진입 점수
    allow_concurrent_positions: bool = False  # 동시 포지션 허용 여부


class StrategyBacktester:
    """
    전략 백테스팅 엔진

    사용법:
        backtester = StrategyBacktester(strategy, config)
        report = backtester.run(symbol, candles, market_type)
        report.print_summary()
    """

    def __init__(
        self,
        strategy: StrategyProtocol,
        config: Optional[BacktestConfig] = None,
        exit_params: Optional[ExitParams] = None,
    ) -> None:
        self.strategy = strategy
        self.config = config or BacktestConfig()
        self.exit_params = exit_params or STRATEGY_EXIT_PARAMS.get(
            strategy.STRATEGY_ID, DEFAULT_EXIT_PARAMS
        )

    def run(
        self,
        symbol: str,
        candles: List[OHLCV],
        market_type: MarketType,
        name: str = "",
    ) -> BacktestReport:
        """
        백테스트 실행

        Args:
            symbol: 종목 코드
            candles: 오래된 순 OHLCV 리스트 (시계열 순서)
            market_type: STOCK 또는 CRYPTO
            name: 종목명 (생략 가능)

        Returns:
            BacktestReport
        """
        name = name or symbol
        cfg = self.config
        ep = self.exit_params

        n = len(candles)
        if n < 60:
            raise ValueError(f"캔들 수 부족: {n} < 60")

        period_start = candles[0].timestamp
        period_end = candles[-1].timestamp

        capital = cfg.initial_capital
        equity_curve: List[float] = [capital]
        trades: List[BacktestTrade] = []
        signal_count = 0

        # 현재 열린 포지션
        open_trade: Optional[BacktestTrade] = None
        remaining_ratio: float = 1.0   # 남은 포지션 비율

        min_window = 60  # 전략이 요구하는 최소 캔들 수

        for i in range(min_window, n):
            current_candle = candles[i]

            # ── 포지션 청산 체크 ──────────────────────────────────────
            if open_trade is not None:
                closed, capital, remaining_ratio = self._check_exit(
                    open_trade, current_candle, capital, remaining_ratio, ep, cfg
                )
                if closed:
                    trades.append(open_trade)
                    equity_curve.append(capital)
                    open_trade = None
                    remaining_ratio = 1.0

            # ── 진입 신호 체크 ────────────────────────────────────────
            if open_trade is None or cfg.allow_concurrent_positions:
                # 전략은 최신 순 캔들을 기대하므로 역순 슬라이스
                window = list(reversed(candles[max(0, i - 299): i + 1]))

                signal = self.strategy.analyze(symbol, name, window, market_type)

                if signal is not None:
                    signal_count += 1
                    if signal.score >= cfg.min_signal_score:
                        # 수수료 + 슬리피지 반영
                        actual_entry_price = signal.price * (
                            1 + cfg.commission_pct + cfg.slippage_pct
                        )
                        open_trade = BacktestTrade(
                            symbol=symbol,
                            strategy_id=self.strategy.STRATEGY_ID,
                            entry_time=current_candle.timestamp,
                            entry_price=actual_entry_price,
                            entry_signal_score=signal.score,
                        )
                        remaining_ratio = 1.0

        # 기간 종료 시 미청산 포지션 강제 청산
        if open_trade is not None:
            last_price = candles[-1].close
            pnl = self._calc_weighted_pnl(
                open_trade, last_price, remaining_ratio
            )
            open_trade.exit_time = candles[-1].timestamp
            open_trade.exit_price = last_price
            open_trade.exit_reason = "end_of_data"
            open_trade.pnl_pct = pnl
            open_trade.is_winner = pnl > 0
            open_trade.hold_candles = n - min_window
            capital *= (1 + pnl * cfg.position_size_pct)
            trades.append(open_trade)
            equity_curve.append(capital)

        metrics = self._calc_metrics(trades, equity_curve, period_start, period_end)

        return BacktestReport(
            strategy_id=self.strategy.STRATEGY_ID,
            symbol=symbol,
            market_type=market_type,
            period_start=period_start,
            period_end=period_end,
            initial_capital=cfg.initial_capital,
            final_capital=capital,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            total_candles=n,
            signal_count=signal_count,
        )

    # ── 청산 로직 ──────────────────────────────────────────────────────────────

    def _check_exit(
        self,
        trade: BacktestTrade,
        candle: OHLCV,
        capital: float,
        remaining_ratio: float,
        ep: ExitParams,
        cfg: BacktestConfig,
    ) -> Tuple[bool, float, float]:
        """
        청산 조건 확인 및 자본 업데이트

        Returns:
            (포지션 완전 청산 여부, 업데이트된 자본, 남은 포지션 비율)
        """
        entry = trade.entry_price
        high = candle.high
        low = candle.low

        def net_pnl(exit_price: float) -> float:
            """순 수익률 (수수료·슬리피지 차감)"""
            raw = (exit_price - entry) / entry
            return raw - cfg.commission_pct - cfg.slippage_pct

        def apply_to_capital(base_capital: float, pnl: float, ratio: float) -> float:
            """포지션 비율만큼 자본에 pnl 반영"""
            position_capital = base_capital * cfg.position_size_pct * ratio
            return base_capital + position_capital * pnl

        def calc_weighted_pnl(pnl1: float, ratio1: float, pnl2: float, ratio2: float) -> float:
            total = ratio1 + ratio2
            return (pnl1 * ratio1 + pnl2 * ratio2) / total if total > 0 else 0.0

        # 1차 익절: 아직 처리 안 됐고 고가가 TP1 이상
        if not trade.tp1_filled:
            tp1_price = entry * (1 + ep.take_profit_1_pct)
            if high >= tp1_price:
                pnl1 = net_pnl(tp1_price)
                tp1_ratio = ep.take_profit_1_ratio
                new_capital = apply_to_capital(capital, pnl1, tp1_ratio)
                trade.tp1_filled = True
                trade.tp1_exit_price = tp1_price
                remaining_ratio -= tp1_ratio

                # 같은 봉에서 2차 익절도 조건 충족 시
                tp2_price = entry * (1 + ep.take_profit_2_pct)
                if high >= tp2_price and remaining_ratio > 0:
                    pnl2 = net_pnl(tp2_price)
                    final_capital = apply_to_capital(new_capital, pnl2, remaining_ratio)
                    trade.tp2_exit_price = tp2_price
                    trade.exit_time = candle.timestamp
                    trade.exit_price = tp2_price
                    trade.exit_reason = "tp2"
                    trade.pnl_pct = calc_weighted_pnl(pnl1, tp1_ratio, pnl2, remaining_ratio)
                    trade.is_winner = trade.pnl_pct > 0
                    return True, final_capital, 0.0
                else:
                    # 1차만 처리, 포지션 잔여 유지
                    return False, new_capital, remaining_ratio

        # 2차 익절: 1차 완료 후 남은 포지션
        if trade.tp1_filled and remaining_ratio > 0:
            tp2_price = entry * (1 + ep.take_profit_2_pct)
            if high >= tp2_price:
                pnl2 = net_pnl(tp2_price)
                final_capital = apply_to_capital(capital, pnl2, remaining_ratio)
                trade.tp2_exit_price = tp2_price
                pnl1 = net_pnl(trade.tp1_exit_price) if trade.tp1_exit_price else 0.0
                tp1_ratio = ep.take_profit_1_ratio
                trade.pnl_pct = calc_weighted_pnl(pnl1, tp1_ratio, pnl2, remaining_ratio)
                trade.exit_time = candle.timestamp
                trade.exit_price = tp2_price
                trade.exit_reason = "tp2"
                trade.is_winner = trade.pnl_pct > 0
                return True, final_capital, 0.0

        # 손절: 저가가 SL 이하
        sl_price = entry * (1 + ep.stop_loss_pct)
        if low <= sl_price:
            pnl_sl = net_pnl(sl_price)
            final_capital = apply_to_capital(capital, pnl_sl, remaining_ratio)
            trade.exit_time = candle.timestamp
            trade.exit_price = sl_price
            trade.exit_reason = "stop_loss"
            if trade.tp1_filled and trade.tp1_exit_price:
                pnl1 = net_pnl(trade.tp1_exit_price)
                tp1_ratio = ep.take_profit_1_ratio
                trade.pnl_pct = calc_weighted_pnl(pnl1, tp1_ratio, pnl_sl, remaining_ratio)
            else:
                trade.pnl_pct = pnl_sl
            trade.is_winner = trade.pnl_pct > 0
            return True, final_capital, 0.0

        # 최대 보유 기간 초과
        if trade.hold_candles >= ep.max_hold_candles:
            pnl_mh = net_pnl(candle.close)
            final_capital = apply_to_capital(capital, pnl_mh, remaining_ratio)
            trade.exit_time = candle.timestamp
            trade.exit_price = candle.close
            trade.exit_reason = "max_hold"
            if trade.tp1_filled and trade.tp1_exit_price:
                pnl1 = net_pnl(trade.tp1_exit_price)
                tp1_ratio = ep.take_profit_1_ratio
                trade.pnl_pct = calc_weighted_pnl(pnl1, tp1_ratio, pnl_mh, remaining_ratio)
            else:
                trade.pnl_pct = pnl_mh
            trade.is_winner = trade.pnl_pct > 0
            return True, final_capital, 0.0

        # 아직 청산 없음 — hold_candles 증가
        trade.hold_candles += 1
        return False, capital, remaining_ratio

    # ── 성과 지표 계산 ─────────────────────────────────────────────────────────

    @staticmethod
    def _calc_metrics(
        trades: List[BacktestTrade],
        equity_curve: List[float],
        period_start: datetime,
        period_end: datetime,
    ) -> BacktestMetrics:
        m = BacktestMetrics()

        if not trades:
            return m

        m.total_trades = len(trades)
        m.win_trades = sum(1 for t in trades if t.is_winner)
        m.lose_trades = m.total_trades - m.win_trades
        m.win_rate = m.win_trades / m.total_trades if m.total_trades > 0 else 0.0

        pnl_list = [t.pnl_pct for t in trades]
        m.avg_return_pct = sum(pnl_list) / len(pnl_list)

        win_pnl = [p for p in pnl_list if p > 0]
        loss_pnl = [p for p in pnl_list if p <= 0]
        m.avg_win_pct = sum(win_pnl) / len(win_pnl) if win_pnl else 0.0
        m.avg_loss_pct = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0.0

        total_profit = sum(win_pnl) if win_pnl else 0.0
        total_loss = abs(sum(loss_pnl)) if loss_pnl else 0.0
        m.profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

        # 누적 수익률 (자산 곡선 기준)
        if equity_curve:
            initial = equity_curve[0]
            final = equity_curve[-1]
            m.total_return_pct = (final - initial) / initial if initial > 0 else 0.0

        # MDD (Maximum Drawdown)
        m.max_drawdown = StrategyBacktester._calc_mdd(equity_curve)

        # Sharpe Ratio (연율화, 무위험수익률 2%)
        m.sharpe_ratio = StrategyBacktester._calc_sharpe(pnl_list)

        # 연율화 수익률
        days = max((period_end - period_start).days, 1)
        years = days / 365.0
        if years > 0 and equity_curve:
            m.annualized_return = (
                (equity_curve[-1] / equity_curve[0]) ** (1 / years) - 1
                if equity_curve[0] > 0 else 0.0
            )

        m.avg_hold_candles = (
            sum(t.hold_candles for t in trades) / len(trades) if trades else 0.0
        )

        return m

    @staticmethod
    def _calc_mdd(equity_curve: List[float]) -> float:
        """최대 낙폭(MDD) 계산"""
        if len(equity_curve) < 2:
            return 0.0
        peak = equity_curve[0]
        mdd = 0.0
        for value in equity_curve:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak if peak > 0 else 0.0
            if drawdown > mdd:
                mdd = drawdown
        return mdd

    @staticmethod
    def _calc_sharpe(returns: List[float], risk_free_rate: float = 0.02) -> float:
        """샤프 지수 계산 (연율화, 거래 단위)"""
        if len(returns) < 2:
            return 0.0
        arr = np.array(returns, dtype=float)
        mean_ret = np.mean(arr)
        std_ret = np.std(arr, ddof=1)
        if std_ret == 0:
            return 0.0
        # 연율화: 연간 250 거래일 가정
        annualized_sharpe = (mean_ret - risk_free_rate / 250) / std_ret * math.sqrt(250)
        return round(annualized_sharpe, 4)

    @staticmethod
    def _calc_weighted_pnl(
        trade: BacktestTrade, close_price: float, remaining_ratio: float
    ) -> float:
        """미청산 포지션의 가중 평균 pnl 계산"""
        entry = trade.entry_price
        current_pnl = (close_price - entry) / entry if entry > 0 else 0.0
        if trade.tp1_filled and trade.tp1_exit_price:
            ep = ExitParams(
                take_profit_1_pct=0.03,
                take_profit_1_ratio=0.5,
                take_profit_2_pct=0.05,
                stop_loss_pct=-0.02,
            )
            tp1_pnl = (trade.tp1_exit_price - entry) / entry
            return tp1_pnl * ep.take_profit_1_ratio + current_pnl * remaining_ratio
        return current_pnl



# ── 합성 데이터 생성기 ─────────────────────────────────────────────────────────

class SyntheticDataLoader:
    """
    테스트용 합성 OHLCV 데이터 생성기

    실제 시장 데이터 없이 전략을 테스트할 때 사용.
    GBM(Geometric Brownian Motion) 기반으로 현실적인 가격 움직임 생성.
    """

    @staticmethod
    def generate(
        symbol: str = "TEST",
        market_type: MarketType = MarketType.STOCK,
        num_candles: int = 300,
        start_price: float = 50_000.0,
        daily_drift: float = 0.0003,     # 일간 기대 수익률 (0.03%)
        daily_volatility: float = 0.02,  # 일간 변동성 (2%)
        start_date: Optional[datetime] = None,
        seed: Optional[int] = None,
    ) -> List[OHLCV]:
        """
        합성 일봉 OHLCV 데이터 생성

        Args:
            symbol: 종목 코드
            market_type: 시장 타입
            num_candles: 생성할 캔들 수
            start_price: 시작 가격
            daily_drift: 일간 기대 수익률
            daily_volatility: 일간 변동성
            start_date: 시작 날짜 (None이면 오늘 - num_candles일)
            seed: 랜덤 시드 (재현성)

        Returns:
            List[OHLCV] (오래된 순)
        """
        if seed is not None:
            np.random.seed(seed)
            random.seed(seed)

        if start_date is None:
            start_date = datetime.now() - timedelta(days=num_candles + 50)

        candles: List[OHLCV] = []
        price = start_price
        base_volume = 1_000_000.0 if market_type == MarketType.STOCK else 1000.0

        date = start_date
        for _ in range(num_candles):
            # 다음 날짜 (주말 제외 — 주식)
            if market_type == MarketType.STOCK:
                while date.weekday() >= 5:  # 토/일 건너뜀
                    date += timedelta(days=1)

            # GBM으로 종가 생성
            ret = np.random.normal(daily_drift, daily_volatility)
            close = price * (1 + ret)

            # 고/저/시가 생성 (현실적인 캔들 형태)
            intraday_range = abs(np.random.normal(0, daily_volatility * 0.5))
            high = close * (1 + intraday_range * random.uniform(0.3, 1.0))
            low = close * (1 - intraday_range * random.uniform(0.3, 1.0))
            open_price = price * (1 + np.random.normal(0, daily_volatility * 0.2))

            # OHLC 정규화 (논리적 일관성)
            high = max(high, open_price, close)
            low = min(low, open_price, close)

            # 거래량: 기본 + 변동성에 비례
            volume_multiplier = max(0.3, abs(ret) / daily_volatility * 1.5)
            volume = base_volume * volume_multiplier * random.uniform(0.5, 1.5)

            candles.append(
                OHLCV(
                    symbol=symbol,
                    timestamp=date,
                    open=round(open_price, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close, 4),
                    volume=round(volume, 2),
                    market_type=market_type,
                )
            )

            price = close
            date += timedelta(days=1)

        return candles

    @staticmethod
    def generate_with_impulses(
        symbol: str = "TEST_IMPULSE",
        market_type: MarketType = MarketType.STOCK,
        num_candles: int = 300,
        start_price: float = 50_000.0,
        num_impulses: int = 5,
        seed: Optional[int] = None,
    ) -> List[OHLCV]:
        """
        급등-눌림목 패턴이 포함된 합성 데이터 (F존 전략 테스트용)

        num_impulses개의 급등 후 조정 패턴을 삽입.
        """
        candles = SyntheticDataLoader.generate(
            symbol=symbol,
            market_type=market_type,
            num_candles=num_candles,
            start_price=start_price,
            seed=seed,
        )

        if seed is not None:
            random.seed(seed + 1)

        # 급등 패턴 삽입
        interval = num_candles // (num_impulses + 1)
        for k in range(num_impulses):
            impulse_idx = interval * (k + 1) + random.randint(-5, 5)
            impulse_idx = max(70, min(impulse_idx, num_candles - 20))

            base_c = candles[impulse_idx]
            impulse_gain = random.uniform(0.04, 0.10)  # 4~10% 급등
            impulse_volume_mult = random.uniform(2.5, 5.0)

            # 기준봉 (급등)
            candles[impulse_idx] = OHLCV(
                symbol=base_c.symbol,
                timestamp=base_c.timestamp,
                open=base_c.open,
                high=base_c.close * (1 + impulse_gain + 0.01),
                low=base_c.low,
                close=base_c.close * (1 + impulse_gain),
                volume=base_c.volume * impulse_volume_mult,
                market_type=base_c.market_type,
            )

            # 눌림목 (3~5봉 조정)
            pullback_candles = random.randint(3, 5)
            pullback_price = candles[impulse_idx].close
            for j in range(1, pullback_candles + 1):
                if impulse_idx + j >= num_candles:
                    break
                c = candles[impulse_idx + j]
                pullback_factor = random.uniform(-0.015, -0.005)
                pb_close = pullback_price * (1 + pullback_factor)
                candles[impulse_idx + j] = OHLCV(
                    symbol=c.symbol,
                    timestamp=c.timestamp,
                    open=pullback_price,
                    high=max(pullback_price, pb_close) * 1.003,
                    low=pb_close * 0.997,
                    close=pb_close,
                    volume=c.volume * 0.5,
                    market_type=c.market_type,
                )
                pullback_price = pb_close

            # 반등 캔들
            bounce_idx = impulse_idx + pullback_candles + 1
            if bounce_idx < num_candles:
                c = candles[bounce_idx]
                bounce_gain = random.uniform(0.008, 0.025)
                bounce_volume_mult = random.uniform(1.3, 2.5)
                candles[bounce_idx] = OHLCV(
                    symbol=c.symbol,
                    timestamp=c.timestamp,
                    open=pullback_price,
                    high=pullback_price * (1 + bounce_gain + 0.005),
                    low=pullback_price * 0.998,
                    close=pullback_price * (1 + bounce_gain),
                    volume=c.volume * bounce_volume_mult,
                    market_type=c.market_type,
                )

        return candles


# ── 멀티 전략 백테스터 ─────────────────────────────────────────────────────────

def run_multi_strategy_backtest(
    strategies: List[StrategyProtocol],
    candles: List[OHLCV],
    symbol: str,
    market_type: MarketType,
    config: Optional[BacktestConfig] = None,
    name: str = "",
) -> Dict[str, BacktestReport]:
    """
    여러 전략을 동일 데이터로 백테스트하고 비교

    Returns:
        {strategy_id: BacktestReport} 딕셔너리
    """
    results: Dict[str, BacktestReport] = {}
    for strategy in strategies:
        backtester = StrategyBacktester(strategy, config)
        try:
            report = backtester.run(symbol, candles, market_type, name)
            results[strategy.STRATEGY_ID] = report
            logger.info(
                "백테스트 완료: %s | %s | 수익률=%.2f%% | 승률=%.1f%% | MDD=%.2f%%",
                strategy.STRATEGY_ID,
                symbol,
                report.metrics.total_return_pct * 100,
                report.metrics.win_rate * 100,
                report.metrics.max_drawdown * 100,
            )
        except Exception as e:
            logger.error("백테스트 오류: %s | %s | %s", strategy.STRATEGY_ID, symbol, e)

    return results


def print_comparison_table(reports: Dict[str, BacktestReport]) -> None:
    """전략 비교 테이블 출력"""
    if not reports:
        print("백테스트 결과 없음")
        return

    headers = ["전략", "거래수", "승률%", "누적수익%", "MDD%", "Sharpe", "연율수익%"]
    rows = []
    for sid, r in reports.items():
        m = r.metrics
        rows.append([
            sid,
            str(m.total_trades),
            f"{m.win_rate*100:.1f}",
            f"{m.total_return_pct*100:+.2f}",
            f"{m.max_drawdown*100:.2f}",
            f"{m.sharpe_ratio:.2f}",
            f"{m.annualized_return*100:+.2f}",
        ])

    col_widths = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)

    print("\n" + "=" * (sum(col_widths) + 2 * len(headers)))
    print("📊 전략 비교 백테스트 결과")
    print("=" * (sum(col_widths) + 2 * len(headers)))
    print(fmt.format(*headers))
    print("-" * (sum(col_widths) + 2 * len(headers)))
    for row in rows:
        print(fmt.format(*row))
    print("=" * (sum(col_widths) + 2 * len(headers)) + "\n")
