"""
익절/손절 최적화 에이전트

과거 매매 결과에서 익절/손절 기준의 적정성을 분석하고
당일 시장 변동성에 맞게 동적 조정을 권고한다.

분석 항목:
  1. 현행 손절(-2%) 적정성 분석
  2. 현행 익절(+3%/+5%) 적정성 분석
  3. 조기 손절 vs 만기 손절 비교
  4. 익절 미도달 청산 비율
  5. 시장 변동성 기반 동적 조정
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from strategy.strategy_team.base_agent import (
    BaseStrategyAgent, StrategySignal, TradeRecord,
)

logger = logging.getLogger(__name__)


class ExitOptimizerAgent(BaseStrategyAgent):

    AGENT_NAME = "exit_optimizer"

    def analyze(
        self,
        trades: List[TradeRecord],
        cache_data: Dict[str, pd.DataFrame],
        watchlist: List[dict],
    ) -> Optional[StrategySignal]:
        sells = [t for t in trades if t.action == 'SELL']
        if len(sells) < 3:
            return None

        signal = StrategySignal(
            agent_name=self.AGENT_NAME,
            confidence=0.0,
        )
        checks_done = 0

        # ── 1. 손절폭 적정성 분석 ──
        sl_analysis = self._analyze_stop_loss(sells)
        if sl_analysis:
            checks_done += 1
            if sl_analysis['suggested_sl'] is not None:
                signal.stop_loss_pct = sl_analysis['suggested_sl']
            signal.reasons.append(sl_analysis['reason'])

        # ── 2. 익절 적정성 분석 ──
        tp_analysis = self._analyze_take_profit(sells)
        if tp_analysis:
            checks_done += 1
            if tp_analysis.get('suggested_tp1') is not None:
                signal.take_profit_1_pct = tp_analysis['suggested_tp1']
            if tp_analysis.get('suggested_tp2') is not None:
                signal.take_profit_2_pct = tp_analysis['suggested_tp2']
            signal.reasons.append(tp_analysis['reason'])

        # ── 3. 조기 손절 패턴 분석 ──
        early_sl = self._analyze_early_stop_loss(trades)
        if early_sl:
            checks_done += 1
            signal.reasons.append(early_sl)

        # ── 4. 시장 변동성 기반 조정 ──
        vol_adjustment = self._market_volatility_adjustment(
            cache_data, watchlist)
        if vol_adjustment:
            checks_done += 1
            if vol_adjustment.get('sl') is not None:
                # 변동성 기반 손절 적용 (넓은 쪽 우선 — 타이트한 손절은 승률 하락 유발)
                if signal.stop_loss_pct is None:
                    signal.stop_loss_pct = vol_adjustment['sl']
                else:
                    signal.stop_loss_pct = min(
                        signal.stop_loss_pct, vol_adjustment['sl'])
            signal.reasons.append(vol_adjustment['reason'])

        # ── 5. 매도 타입별 분포 분석 ──
        dist_analysis = self._analyze_exit_distribution(sells)
        if dist_analysis:
            checks_done += 1
            signal.reasons.append(dist_analysis)

        # ── 6. 강제청산 패턴 분석 (1차 익절 후 이익 반납 방지) ──
        fl_analysis = self._analyze_forced_liquidation(sells)
        if fl_analysis:
            checks_done += 1
            if fl_analysis.get('suggested_tp2') is not None:
                # 기존 TP2 분석과 충돌 시 더 보수적인(낮은) 값
                if signal.take_profit_2_pct is None:
                    signal.take_profit_2_pct = fl_analysis['suggested_tp2']
                else:
                    signal.take_profit_2_pct = min(
                        signal.take_profit_2_pct,
                        fl_analysis['suggested_tp2'])
            if fl_analysis.get('suggested_be_buffer') is not None:
                signal.breakeven_buffer_pct = fl_analysis['suggested_be_buffer']
            signal.reasons.append(fl_analysis['reason'])

        # ── 7. 과열 진입 후 손절 패턴 분석 ──
        breakout_analysis = self._analyze_breakout_stop_loss(trades)
        if breakout_analysis:
            checks_done += 1
            if breakout_analysis.get('suggested_max_breakout') is not None:
                signal.max_breakout_pct = breakout_analysis[
                    'suggested_max_breakout']
            signal.reasons.append(breakout_analysis['reason'])

        signal.confidence = min(checks_done / 7, 1.0)
        return signal if signal.reasons else None

    def _analyze_stop_loss(self, sells: List[TradeRecord]) -> Optional[dict]:
        """
        현행 손절폭 적정성 분석

        손절 매도들의 PnL 분포를 분석하여
        실제 손절이 설정값과 얼마나 차이 나는지 확인
        """
        sl_sells = [s for s in sells if '손절' in s.exit_type]
        if len(sl_sells) < 2:
            return None

        pnl_values = [s.pnl_pct for s in sl_sells]
        avg_sl = sum(pnl_values) / len(pnl_values)
        max_sl = min(pnl_values)  # 가장 큰 손절

        # 실제 손절이 설정값(-3.5%)보다 훨씬 크면 슬리피지가 심하다는 의미
        if avg_sl < -4.0:
            suggested = round(avg_sl + 0.5, 1)  # 슬리피지 감안
            return {
                'suggested_sl': min(suggested, -3.5),  # 하한선: settings.yaml 기준
                'reason': (
                    f"실제 평균 손절 {avg_sl:.1f}% "
                    f"(최대 {max_sl:.1f}%): "
                    f"슬리피지 감안 {suggested:.1f}% 권고"),
            }

        return {
            'suggested_sl': None,
            'reason': (
                f"손절 분포: 평균 {avg_sl:.1f}% / "
                f"최대 {max_sl:.1f}% "
                f"({len(sl_sells)}회)"),
        }

    def _analyze_take_profit(
        self, sells: List[TradeRecord],
    ) -> Optional[dict]:
        """익절 적정성 분석"""
        tp_sells = [
            s for s in sells
            if '익절' in s.exit_type and s.pnl_pct > 0]

        if len(tp_sells) < 2:
            return None

        pnl_values = [s.pnl_pct for s in tp_sells]
        avg_tp = sum(pnl_values) / len(pnl_values)
        max_tp = max(pnl_values)

        # 1차 익절(+3%)에서만 잡히고 2차(+5%)는 거의 안 되면
        tp1_count = sum(1 for s in tp_sells if '1차' in s.exit_type)
        tp2_count = sum(1 for s in tp_sells if '2차' in s.exit_type)

        if tp1_count >= 3 and tp2_count == 0:
            # +5%까지 안 가므로 2차 목표를 +4%로 낮추기
            return {
                'suggested_tp1': None,
                'suggested_tp2': 4.0,
                'reason': (
                    f"익절 1차만 {tp1_count}회, 2차 0회: "
                    f"2차 목표 +5% → +4% 하향 권고"),
            }

        if avg_tp >= 4.0 and tp2_count >= 2:
            # 충분히 올라가는 종목이 많으면 목표 상향
            return {
                'suggested_tp1': 3.5,
                'suggested_tp2': 6.0,
                'reason': (
                    f"평균 익절 +{avg_tp:.1f}%, "
                    f"2차 도달 {tp2_count}회: "
                    f"목표 상향 (+3.5%/+6%) 권고"),
            }

        return {
            'suggested_tp1': None,
            'suggested_tp2': None,
            'reason': (
                f"익절 분포: 평균 +{avg_tp:.1f}% / "
                f"1차 {tp1_count}회 / 2차 {tp2_count}회"),
        }

    def _analyze_early_stop_loss(
        self, trades: List[TradeRecord],
    ) -> Optional[str]:
        """
        조기 손절 패턴: 손절 후 해당 종목이 반등했는지 분석
        (매수→손절→(매도 후 가격 회복) 패턴)
        """
        sorted_trades = sorted(trades, key=lambda t: t.timestamp)

        # 손절 후 동일 종목 재매수가 이익이었던 경우 카운트
        sl_then_profit = 0
        sl_then_loss = 0

        last_sl_code = {}  # code -> True (손절 발생)
        for t in sorted_trades:
            if t.action == 'SELL' and '손절' in t.exit_type:
                last_sl_code[t.code] = True
            elif t.action == 'SELL' and t.code in last_sl_code:
                if t.pnl_pct > 0:
                    sl_then_profit += 1
                else:
                    sl_then_loss += 1
                del last_sl_code[t.code]

        if sl_then_profit + sl_then_loss >= 2:
            total = sl_then_profit + sl_then_loss
            if sl_then_profit > sl_then_loss:
                return (
                    f"손절 후 재진입 성공률 "
                    f"{sl_then_profit}/{total}: "
                    f"손절폭 확대(-2.5%) 고려")
            else:
                return (
                    f"손절 후 재진입 실패율 "
                    f"{sl_then_loss}/{total}: "
                    f"손절 종목 재진입 자제")

        return None

    def _market_volatility_adjustment(
        self,
        cache_data: Dict[str, pd.DataFrame],
        watchlist: List[dict],
    ) -> Optional[dict]:
        """시장(관심종목) 평균 변동성 기반 손절/익절 동적 조정"""
        atr_pcts = []

        for stock in watchlist:
            df = cache_data.get(stock['code'])
            if df is None or len(df) < 15:
                continue

            close = df['close'].values
            high = df['high'].values
            low = df['low'].values

            tr = np.maximum(
                high[-14:] - low[-14:],
                np.maximum(
                    np.abs(high[-14:] - close[-15:-1]),
                    np.abs(low[-14:] - close[-15:-1])
                ),
            )
            atr = float(np.mean(tr))
            if close[-1] > 0:
                atr_pcts.append(atr / close[-1] * 100)

        if len(atr_pcts) < 3:
            return None

        avg_atr = sum(atr_pcts) / len(atr_pcts)

        # 변동성 높은 시장: 손절폭 확대 (정상적인 노이즈로 걸리지 않게)
        if avg_atr >= 5.0:
            return {
                'sl': -5.0,
                'reason': (
                    f"관심종목 평균 ATR {avg_atr:.1f}% (고변동): "
                    f"손절 -5.0% 확대 권고"),
            }
        elif avg_atr >= 3.5:
            return {
                'sl': -4.0,
                'reason': (
                    f"관심종목 평균 ATR {avg_atr:.1f}%: "
                    f"손절 -4.0% 확대 권고"),
            }
        elif avg_atr < 2.0:
            return {
                'sl': -3.0,
                'reason': (
                    f"관심종목 평균 ATR {avg_atr:.1f}% (저변동): "
                    f"손절 -3.0% 축소 권고"),
            }

        return {
            'sl': None,
            'reason': f"관심종목 평균 ATR {avg_atr:.1f}% (적정)",
        }

    def _analyze_forced_liquidation(
        self, sells: List[TradeRecord],
    ) -> Optional[dict]:
        """
        강제청산 패턴 분석

        1차 익절 후 잔량이 강제청산으로 이익 반납하는 패턴을 감지하여
        TP2 하향 및 브레이크이븐 스톱 권고
        """
        fl_sells = [s for s in sells if '강제청산' in s.exit_type]
        if len(fl_sells) < 2:
            return None

        total_sells = len(sells)
        fl_pct = len(fl_sells) / total_sells * 100

        # 강제청산 중 손실인 건수
        fl_loss = [s for s in fl_sells if s.pnl_pct < 0]
        fl_profit = [s for s in fl_sells if s.pnl_pct >= 0]

        # 강제청산 비율이 40% 이상이면 TP2가 너무 높다는 신호
        if fl_pct >= 40:
            # TP1 달성 후 강제청산된 종목 파악 (같은 코드에서 익절1차+강제청산)
            tp1_codes = {
                s.code for s in sells
                if '익절' in s.exit_type and '1차' in s.exit_type}
            fl_after_tp1 = [
                s for s in fl_sells if s.code in tp1_codes]

            if fl_after_tp1:
                avg_fl_pnl = sum(
                    s.pnl_pct for s in fl_after_tp1
                ) / len(fl_after_tp1)
                return {
                    'suggested_tp2': 6.0,
                    'suggested_be_buffer': 0.3,
                    'reason': (
                        f"강제청산 {len(fl_sells)}회({fl_pct:.0f}%), "
                        f"1차익절 후 강제청산 {len(fl_after_tp1)}회 "
                        f"(평균PnL {avg_fl_pnl:+.1f}%): "
                        f"TP2 +8%→+6% 하향, BE스톱 +0.3% 권고"),
                }

            return {
                'suggested_tp2': 6.0,
                'suggested_be_buffer': None,
                'reason': (
                    f"강제청산 {len(fl_sells)}회({fl_pct:.0f}%), "
                    f"손실 {len(fl_loss)}회: "
                    f"TP2 +8%→+6% 하향 권고"),
            }

        return None

    def _analyze_breakout_stop_loss(
        self, trades: List[TradeRecord],
    ) -> Optional[dict]:
        """
        과열 돌파 진입 후 손절 패턴 분석

        파란점선 돌파율이 높은(+5% 이상) 진입이 손절로 끝나는 비율을 분석하여
        max_breakout_pct 동적 조정
        """
        # 매수에서 돌파율 추출
        high_breakout_buys = {}  # code+timestamp -> breakout_pct
        for t in trades:
            if t.action != 'BUY':
                continue
            try:
                if '파란점선 돌파 +' in t.reason:
                    pct_str = t.reason.split('파란점선 돌파 +')[1].split('%')[0]
                    bp = float(pct_str)
                    if bp >= 5.0:
                        high_breakout_buys[t.code] = bp
            except (ValueError, IndexError):
                pass

        if len(high_breakout_buys) < 2:
            return None

        # 해당 종목의 매도 결과 확인
        sell_results = {}
        for t in trades:
            if t.action == 'SELL' and t.code in high_breakout_buys:
                if t.code not in sell_results:
                    sell_results[t.code] = []
                sell_results[t.code].append(t.pnl_pct)

        if not sell_results:
            return None

        # 고돌파 진입 후 손실 비율
        loss_count = 0
        total = 0
        for code, pnls in sell_results.items():
            for pnl in pnls:
                total += 1
                if pnl < 0:
                    loss_count += 1

        if total < 2:
            return None

        loss_rate = loss_count / total * 100
        if loss_rate >= 60:
            return {
                'suggested_max_breakout': 5.0,
                'reason': (
                    f"+5%↑ 돌파 진입 후 손실률 {loss_rate:.0f}% "
                    f"({loss_count}/{total}회): "
                    f"돌파 상한 +5%로 강화 권고"),
            }
        elif loss_rate >= 40:
            return {
                'suggested_max_breakout': 7.0,
                'reason': (
                    f"+5%↑ 돌파 진입 후 손실률 {loss_rate:.0f}%: "
                    f"돌파 상한 +7% 유지 권고"),
            }

        return None

    def _analyze_exit_distribution(
        self, sells: List[TradeRecord],
    ) -> Optional[str]:
        """매도 타입별 분포 분석"""
        dist = defaultdict(int)
        for s in sells:
            dist[s.exit_type] += 1

        if not dist:
            return None

        total = sum(dist.values())
        parts = []
        for exit_type, count in sorted(
            dist.items(), key=lambda x: x[1], reverse=True,
        ):
            pct = count / total * 100
            parts.append(f"{exit_type} {count}회({pct:.0f}%)")

        # 손절 비율이 과반이면 경고
        sl_count = sum(v for k, v in dist.items() if '손절' in k)
        sl_pct = sl_count / total * 100

        if sl_pct >= 60:
            return (
                f"매도 분포: {' | '.join(parts)} "
                f"※ 손절 {sl_pct:.0f}%로 과다")

        return f"매도 분포: {' | '.join(parts)}"
