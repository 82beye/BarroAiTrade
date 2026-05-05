"""
스캘핑 타이밍 팀 코디네이터

10명의 트레이딩 전문가 에이전트 분석 결과를 통합하여
각 종목의 최적 스캘핑 진입 타이밍을 종합 판단한다.

에이전트 구성 (총 가중치 1.0):
  1. VWAP전략가       (0.05) - VWAP 대비 위치 기반 진입
  2. 모멘텀폭발전문가 (0.08) - 모멘텀 초기 포착
  3. 눌림목전문가     (0.20) - 되돌림 구간 최적 재진입
  4. 돌파확인전문가   (0.12) - 돌파 유효성 + 당일 고점 돌파
  5. 캔들패턴전문가   (0.08) - 캔들 패턴 기반 시그널
  6. 거래량프로파일    (0.16) - OBV/수급 패턴
  7. 골든타임전문가   (0.08) - 시간대별 최적 진입
  8. 상대강도전문가   (0.08) - 종목간 상대 순위
  9. 리스크보상전문가 (0.07) - R:R 비율 계산
  10. 호가테이프전문가 (0.08) - 체결 데이터 분석
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingAnalysis, ScalpingSignal, StockSnapshot,
)
from strategy.scalping_team.vwap_agent import VWAPAgent
from strategy.scalping_team.momentum_burst_agent import MomentumBurstAgent
from strategy.scalping_team.pullback_agent import PullbackAgent
from strategy.scalping_team.breakout_confirm_agent import BreakoutConfirmAgent
from strategy.scalping_team.candle_pattern_agent import CandlePatternAgent
from strategy.scalping_team.volume_profile_agent import VolumeProfileAgent
from strategy.scalping_team.golden_time_agent import GoldenTimeAgent
from strategy.scalping_team.relative_strength_agent import RelativeStrengthAgent
from strategy.scalping_team.risk_reward_agent import RiskRewardAgent
from strategy.scalping_team.spread_tape_agent import SpreadTapeAgent

logger = logging.getLogger(__name__)


class ScalpingCoordinator:
    """스캘핑 타이밍 전문가 팀 코디네이터"""

    # 가중치 재설계 (2026-03-27 전면 재조정)
    # 핵심 전략: 눌림목(+5~15%) 진입 → 눌림목 에이전트 최우선
    # - 눌림목: 0.14→0.20 (핵심 전략, 30~50% 되돌림 진입)
    # - 거래량: 0.16 유지 (매도 소진 확인 필수)
    # - 상대강도: 0.18→0.12 (과도한 영향 축소, 추격매수 원인)
    # - 골든타임: 0.04→0.08 (시간대 필터 중요도 상승)
    # - 리스크보상: 0.04→0.07 (구간별 R:R 강제)
    # - 모멘텀: 0.06→0.08 (진입 구간 확인용)
    # - 돌파: 0.08→0.12 (당일 고점 돌파 전략 강화, 2026-04-03)
    # - 상대강도: 0.12→0.08 (돌파 가중치 이동)
    # - 호가테이프: 0.12→0.08 (보조 지표)
    # - 캔들: 0.10→0.08 (보조 지표)
    # - VWAP: 0.04→0.05 (눌림목→VWAP 복귀 확인)
    AGENT_WEIGHTS = {
        'VWAP전략가': 0.05,
        '모멘텀폭발전문가': 0.08,
        '눌림목전문가': 0.20,
        '돌파확인전문가': 0.12,
        '캔들패턴전문가': 0.08,
        '거래량프로파일전문가': 0.16,
        '골든타임전문가': 0.08,
        '상대강도전문가': 0.08,
        '리스크보상전문가': 0.07,
        '호가테이프전문가': 0.08,
    }

    TIMING_PRIORITY = {
        '즉시': 4,
        '대기': 3,
        '눌림목대기': 2,
        '관망': 1,
    }

    # 왕복 수수료+세금 (매수 0.015% + 매도 0.015% + 거래세 0.18%)
    ROUND_TRIP_FEE_PCT = 0.21

    def __init__(self, config: dict):
        self.config = config
        self._cache_dir = config.get(
            'scanner', {}).get('cache_dir', './data/ohlcv_cache')

        scalp_cfg = config.get('strategy', {}).get('scalping', {})

        # 스캘핑 SL 하한선 (MAE 분석 기반 -3.0%로 확대)
        self._sl_floor = scalp_cfg.get('default_sl_pct', -3.0)

        # 최소 수익 마진: TP가 이 값 미만이면 진입 차단
        self._min_tp_pct = self.ROUND_TRIP_FEE_PCT + 0.8  # ~1.0%

        # 최소 합의 수준
        self._min_consensus = scalp_cfg.get('min_consensus', '소수합의')

        # ── 진입 구간 필터 (신규) ──
        zone_cfg = scalp_cfg.get('entry_zone', {})
        self._min_change_pct = zone_cfg.get('min_change_pct', 5.0)
        self._max_change_pct = zone_cfg.get('max_change_pct', 25.0)
        self._zone_mid = zone_cfg.get('mid', {'tp_pct': 2.5, 'sl_pct': -1.5})
        self._zone_late = zone_cfg.get('late', {'tp_pct': 1.5, 'sl_pct': -1.0})

        # ── 거래대금 필터 (시간대별) ──
        self._min_trade_value = scalp_cfg.get(
            'min_trade_value', 3_000_000_000)  # 기본 30억 (골든타임)
        self._min_trade_value_morning = scalp_cfg.get(
            'min_trade_value_morning', 5_000_000_000)  # 오전장 50억
        self._min_trade_value_afternoon = scalp_cfg.get(
            'min_trade_value_afternoon', 10_000_000_000)  # 오후장 100억

        # ── 시간대 필터 (신규) ──
        tw = scalp_cfg.get('time_windows', {})
        self._dead_zone_start = self._parse_time(
            tw.get('morning_end', '11:30'))
        self._dead_zone_end = self._parse_time(
            tw.get('dead_zone_end', '12:30'))  # 2026-04-09: 11:30~12:30 (1시간으로 축소)
        self._afternoon_end = self._parse_time(
            tw.get('afternoon_end', '14:00'))

        # ── 재진입 제한 (신규) ──
        self._max_entries_per_stock = scalp_cfg.get(
            'max_entries_per_stock', 2)
        self._loss_cooldown_min = scalp_cfg.get(
            'loss_cooldown_minutes', 15)

        # ── 동시 진입 제한 (2026-04-09: 2→5종목, 매매 빈도 확대) ──
        self._max_simultaneous_entries = scalp_cfg.get(
            'max_simultaneous_entries', 5)
        self._active_positions: set = set()  # 현재 보유 중인 종목 코드

        # ── 10:00-11:00 최소 점수 (완화: 60→50, 매매 빈도 확대) ──
        self._mid_morning_min_score = scalp_cfg.get(
            'mid_morning_min_score', 50)

        # ── 전일 급등주 쿨다운 (신규) ──
        self._prev_runner_cooldown = scalp_cfg.get(
            'prev_runner_cooldown_days', 2)

        # ── 시장 동조성 (리포트: 시장 동조 급등주 고점 유지 확률 높음) ──
        self._kosdaq_change_pct: float = 0.0

        # ── MAE 기반 동적 손절 (리포트 권장) ──
        self._trade_log_path = config.get(
            'logging', {}).get('trade_log', './logs/trades.jsonl')
        self._mae_sl_override: Optional[float] = None
        self._mae_min_trades = 20  # 최소 20건 필요
        self._load_mae_based_sl()

        # ── 일일 손실 한도 (스캘핑 전용, 리포트: -3%) ──
        self._scalp_daily_loss_limit = scalp_cfg.get(
            'daily_loss_limit_pct', -3.0)
        self._daily_scalp_pnl: float = 0.0  # 당일 스캘핑 누적 PnL%

        # 일일 진입 추적기
        self._daily_entries: Dict[str, List[dict]] = defaultdict(list)
        self._last_reset_date: Optional[str] = None

        # 10명의 에이전트 초기화
        self.agents: Dict[str, BaseScalpingAgent] = {
            'VWAP전략가': VWAPAgent(),
            '모멘텀폭발전문가': MomentumBurstAgent(),
            '눌림목전문가': PullbackAgent(),
            '돌파확인전문가': BreakoutConfirmAgent(),
            '캔들패턴전문가': CandlePatternAgent(),
            '거래량프로파일전문가': VolumeProfileAgent(),
            '골든타임전문가': GoldenTimeAgent(),
            '상대강도전문가': RelativeStrengthAgent(),
            '리스크보상전문가': RiskRewardAgent(),
            '호가테이프전문가': SpreadTapeAgent(),
        }

    @staticmethod
    def _parse_time(s: str) -> time:
        """'HH:MM' → time 객체"""
        parts = s.split(':')
        return time(int(parts[0]), int(parts[1]))

    # ─── 사전 필터 (에이전트 분석 전 하드 게이트) ───

    def pre_score_filter(
        self, snapshot: 'StockSnapshot',
        ohlcv: Optional[pd.DataFrame] = None,
        intraday_prices: Optional[List[dict]] = None,
    ) -> Tuple[bool, str]:
        """
        에이전트 분석 전 하드 게이트 필터.
        통과 시 (True, ""), 차단 시 (False, 사유) 반환.
        """
        # 일일 추적기 리셋 (날짜 변경 시)
        today = datetime.now().strftime('%Y-%m-%d')
        if self._last_reset_date != today:
            self._daily_entries.clear()
            self._daily_scalp_pnl = 0.0
            self._last_reset_date = today

        # 0. 일일 스캘핑 손실 한도 체크
        if self._daily_scalp_pnl <= self._scalp_daily_loss_limit:
            return False, (
                f"스캘핑 일일 손실 한도: "
                f"{self._daily_scalp_pnl:.1f}% <= "
                f"{self._scalp_daily_loss_limit}% — 당일 매매 종료")

        code = snapshot.code
        change_pct = snapshot.change_pct

        # 1. 진입 구간 필터
        if change_pct < self._min_change_pct:
            return False, (
                f"진입구간 미달: {change_pct:+.1f}% < "
                f"+{self._min_change_pct}%")
        if change_pct > self._max_change_pct:
            return False, (
                f"과열 구간: {change_pct:+.1f}% > "
                f"+{self._max_change_pct}%")

        # 2. 거래대금 필터 (시간대별 차등)
        trade_value = getattr(snapshot, 'trade_value', 0) or 0
        now_t = datetime.now().time()
        if now_t >= self._dead_zone_end:  # 12:00 이후 → 오후장
            min_tv = self._min_trade_value_afternoon
        elif now_t >= time(9, 30):  # 09:30 이후 → 오전장
            min_tv = self._min_trade_value_morning
        else:  # 09:00~09:30 → 골든타임
            min_tv = self._min_trade_value
        if trade_value < min_tv:
            tv_억 = trade_value / 100_000_000
            min_억 = min_tv / 100_000_000
            return False, (
                f"거래대금 부족: {tv_억:.0f}억 < {min_억:.0f}억")

        # 3. 시간대 필터 (데드존 차단)
        now_t = datetime.now().time()
        if self._dead_zone_start <= now_t < self._dead_zone_end:
            return False, f"데드존({self._dead_zone_start.strftime('%H:%M')}~{self._dead_zone_end.strftime('%H:%M')}): 신규진입 금지"
        if now_t >= self._afternoon_end:
            return False, f"오후장 마감(14시 이후): 신규진입 금지"

        # 4. 재진입 제한
        entries = self._daily_entries.get(code, [])
        if len(entries) >= self._max_entries_per_stock:
            return False, (
                f"재진입 한도: {code} 오늘 {len(entries)}회 진입")
        # 손절 후 쿨다운
        if entries:
            last = entries[-1]
            if last.get('result') == 'loss':
                elapsed = (
                    datetime.now() - last['exit_time']
                ).total_seconds() / 60
                if elapsed < self._loss_cooldown_min:
                    return False, (
                        f"손절 쿨다운: {elapsed:.0f}분 < "
                        f"{self._loss_cooldown_min}분")

        # 5. 전일 급등주 필터 (OHLCV 기반)
        if ohlcv is not None and len(ohlcv) >= 3:
            for d in range(1, min(self._prev_runner_cooldown + 1, len(ohlcv))):
                prev_close = ohlcv['close'].iloc[-(d + 1)]
                prev_high = ohlcv['high'].iloc[-d]
                if prev_close > 0:
                    prev_change = (prev_high - prev_close) / prev_close * 100
                    if prev_change >= 20:
                        return False, (
                            f"전일 급등주: {d}일 전 +{prev_change:.0f}% "
                            f"(쿨다운 {self._prev_runner_cooldown}일)")

        # 6. 시가 대비 하락추세 종목 차단 (2026-04-07: 삼성E&A/엔비알모션 진입 방지)
        # 시가 대비 현재가가 -2% 이하면 하락추세로 판단
        if snapshot.open > 0 and snapshot.price > 0:
            price_vs_open = (snapshot.price - snapshot.open) / snapshot.open * 100
            if price_vs_open <= -2.0:
                return False, (
                    f"시가 대비 하락추세: 시가 {snapshot.open:,}원 → "
                    f"현재 {snapshot.price:,}원 ({price_vs_open:+.1f}%)")

        # 7. 과다 되돌림 필터 (고점→현재 하락이 상승폭의 70% 이상)
        # 2026-04-07: 남선알미늄(고점 2,485→2,310 진입) 방지
        if snapshot.high > snapshot.open and snapshot.open > 0:
            rally = snapshot.high - snapshot.open
            pullback = snapshot.high - snapshot.price
            if rally > 0:
                retracement_ratio = pullback / rally
                if retracement_ratio >= 0.70:
                    return False, (
                        f"과다 되돌림: 시가→고점 +{rally/snapshot.open*100:.1f}% "
                        f"중 {retracement_ratio:.0%} 되돌림 — 추세 훼손")

        # 8. 고점 경과 시간 필터 (고점 후 45분+ 경과 시 차단)
        # 2026-04-09: 30→45틱 완화 (45~60분 사이클 스캘핑 허용)
        if intraday_prices and len(intraday_prices) >= 10:
            prices_list = [t['price'] for t in intraday_prices]
            peak_idx = prices_list.index(max(prices_list))
            total_ticks = len(prices_list)
            ticks_since_peak = total_ticks - 1 - peak_idx
            # 고점 이후 45틱(≈45분) 이상 경과 + 고점 대비 -5% 이상 하락
            if ticks_since_peak >= 45 and snapshot.high > 0:
                drop_from_peak = (snapshot.price - snapshot.high) / snapshot.high * 100
                if drop_from_peak <= -5.0:
                    return False, (
                        f"고점 후 {ticks_since_peak}틱 경과 + "
                        f"고점 대비 {drop_from_peak:.1f}% 하락 — 모멘텀 소실")

        # 9. 캔들 품질 게이트 (10전문가 분석 결과 구현)
        candle_block, candle_reason = self._candle_quality_gate(
            snapshot, intraday_prices)
        if candle_block:
            return False, candle_reason

        # 7. 약한 상한가 감지 (장중 2회+ 상한가 이탈 = 익일 하락 확률 높음)
        if (intraday_prices and len(intraday_prices) >= 5
                and snapshot.prev_close > 0):
            limit_price = snapshot.prev_close * 1.30  # 코스닥 상한가 +30%
            limit_touches = 0
            was_at_limit = False
            for t in intraday_prices:
                p = t.get('price', 0)
                at_limit = p >= limit_price * 0.998  # 상한가 근접 (0.2% 이내)
                if at_limit and not was_at_limit:
                    limit_touches += 1
                was_at_limit = at_limit

            if limit_touches >= 2 and not was_at_limit:
                # 상한가 2회+ 터치 후 현재 이탈 상태 = 약한 상한가
                return False, (
                    f"약한 상한가: 장중 {limit_touches}회 상한가 도달 후 "
                    f"이탈 — 익일 하락 위험")

        return True, ""

    def _candle_quality_gate(
        self,
        snapshot: 'StockSnapshot',
        intraday_prices: Optional[List[dict]],
    ) -> Tuple[bool, str]:
        """캔들 품질 사전 필터 (2026-04-07: 10전문가 분석 기반)

        차단 조건 (intraday 분봉 기반):
          1. 직전 3틱 연속 하락 + 하락폭 -0.5% 이상 — 낙하 중 진입 금지
          2. 8틱+ 연속 상승 후 조정 없음 — 파라볼릭 추격 방지
          3. 직전 5틱 거래량 < 전체 평균 30% — 거래량 극단 부족
        """
        if not intraday_prices or len(intraday_prices) < 5:
            return False, ""

        prices = [t['price'] for t in intraday_prices]
        volumes = [t.get('volume', 0) for t in intraday_prices]

        # 1. 직전 3틱 연속 하락 + 하락폭 체크
        if len(prices) >= 4:
            last4 = prices[-4:]
            if last4[3] < last4[2] < last4[1] < last4[0]:
                drop_pct = (last4[3] - last4[0]) / last4[0] * 100
                if drop_pct <= -0.5:
                    return True, (
                        f"하락 중 진입 차단: 직전 3틱 연속 하락 "
                        f"({drop_pct:.1f}%)")

        # 2. 8틱+ 연속 상승 — 파라볼릭 추격 방지
        consecutive_up = 0
        for i in range(len(prices) - 1, 0, -1):
            if prices[i] >= prices[i - 1]:
                consecutive_up += 1
            else:
                break
        if consecutive_up >= 8:
            return True, (
                f"파라볼릭 차단: {consecutive_up}틱 연속 상승 — "
                f"조정 없이 추격 진입 금지")

        # 3. 직전 5틱 거래량 극단 부족
        if len(volumes) >= 15:
            recent_vol = sum(volumes[-5:]) / 5
            avg_vol = sum(volumes) / len(volumes)
            if avg_vol > 0 and recent_vol < avg_vol * 0.3:
                return True, (
                    f"거래량 극단 부족 차단: 직전5틱 {recent_vol:.0f} < "
                    f"평균 {avg_vol:.0f}의 30%")

        return False, ""

    def record_entry(self, code: str):
        """진입 기록 (외부에서 체결 시 호출)"""
        self._daily_entries[code].append({
            'entry_time': datetime.now(),
            'exit_time': None,
            'result': None,
        })
        self._active_positions.add(code)

    def record_exit(self, code: str, result: str, pnl_pct: float = 0.0):
        """종료 기록 (result: 'win' | 'loss' | 'even')"""
        entries = self._daily_entries.get(code, [])
        if entries and entries[-1]['exit_time'] is None:
            entries[-1]['exit_time'] = datetime.now()
            entries[-1]['result'] = result
        # 일일 스캘핑 PnL 누적
        self._daily_scalp_pnl += pnl_pct
        self._active_positions.discard(code)

    def _load_mae_based_sl(self):
        """과거 스캘핑 승리 매매의 MAE 90th percentile 기반 동적 SL 계산

        리포트: "MAE의 90th percentile 바깥에 손절 설정하면
        불필요한 손절의 70~80% 방지"
        """
        try:
            path = Path(self._trade_log_path)
            if not path.exists():
                return

            win_maes = []
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    t = json.loads(line.strip())
                    if (t.get('action') == 'SELL'
                            and t.get('strategy_type') == 'scalping'
                            and t.get('mae_pct') is not None
                            and t.get('pnl_pct', 0) > 0):
                        win_maes.append(t['mae_pct'])

            if len(win_maes) >= self._mae_min_trades:
                # 90th percentile of winning trades' MAE (most negative)
                mae_90 = float(np.percentile(win_maes, 10))  # 10th = 가장 낮은 쪽
                # 약간의 여유 추가 (-0.2%)
                self._mae_sl_override = round(mae_90 - 0.2, 1)
                logger.info(
                    f"MAE 기반 SL 계산 완료: {self._mae_sl_override:.1f}% "
                    f"(승리 {len(win_maes)}건, 90th MAE: {mae_90:.1f}%)")
            else:
                logger.info(
                    f"MAE 데이터 부족: 승리 {len(win_maes)}건 "
                    f"(최소 {self._mae_min_trades}건 필요)")
        except Exception as e:
            logger.warning(f"MAE SL 로드 실패: {e}")

    @staticmethod
    def classify_surge_type(snapshot: 'StockSnapshot') -> str:
        """상한가/급등 유형 분류 (리포트 2: 유형별 전략 차별화)

        Type 1 (갭상승형): 시가부터 급등 (갭업), 고점 유지 확률 높음
          - 시가 >= 전일종가 * 1.15 (시가부터 +15% 이상 갭업)
          - TP 확대, 보유시간 여유

        Type 2 (장중급등형): 시가 정상, 장중 급등
          - 시가 < 전일종가 * 1.10 인데 현재가 급등
          - 변동성 크고 되돌림 빈번 → TP 축소, SL 타이트
        """
        if not snapshot or snapshot.prev_close <= 0:
            return 'unknown'

        open_gap_pct = (snapshot.open - snapshot.prev_close) / snapshot.prev_close * 100

        if open_gap_pct >= 15:
            return 'gap_up'      # Type 1: 갭상승형
        elif open_gap_pct < 10:
            return 'intraday'    # Type 2: 장중급등형
        else:
            return 'mixed'       # 중간형

    def get_zone_params(
        self, change_pct: float, trade_value: float = 0,
        surge_type: str = 'unknown',
    ) -> dict:
        """진입 구간별 + 거래대금 규모별 TP/SL 파라미터 반환

        리포트 권장:
          - 거래대금 1,000억+ 대형주: TP 확대 (3~5%, 되돌림 작음)
          - 거래대금 500억 미만 소형주: TP 축소 (2% 이하, 기계적 익절)
        """
        if change_pct < 15:
            base_tp = self._zone_mid.get('tp_pct', 2.5)
            base_sl = self._zone_mid.get('sl_pct', -1.5)
            zone = 'mid'
        else:
            base_tp = self._zone_late.get('tp_pct', 1.5)
            base_sl = self._zone_late.get('sl_pct', -1.0)
            zone = 'late'

        # MAE 기반 동적 SL 적용 (충분한 데이터가 있을 때만)
        if self._mae_sl_override is not None:
            base_sl = max(base_sl, self._mae_sl_override)  # 더 타이트한 쪽 사용

        # 시장 동조성 보정 (리포트: 시장과 같은 방향이면 고점 유지 확률 높음)
        if self._kosdaq_change_pct >= 2.0:
            # 시장 동조 상승: TP 확대 (+0.5%)
            base_tp = min(base_tp + 0.5, 5.0)
        elif self._kosdaq_change_pct <= -2.0:
            # 시장 역행 급등: TP 축소 (-0.5%), SL 타이트
            base_tp = max(base_tp - 0.5, 1.0)
            base_sl = max(base_sl, -1.0)  # 최대 -1.0%로 축소

        # 급등 유형별 보정 (리포트 2: 갭상승형 vs 장중급등형)
        if surge_type == 'gap_up':
            # Type 1: 고점 유지 확률 높음 → TP 확대, 보유 여유
            base_tp = min(base_tp + 0.5, 5.0)
        elif surge_type == 'intraday':
            # Type 2: 되돌림 빈번 → TP 축소, SL은 zone 설정 존중
            # 2026-04-01: SL -1.2% 캡 해제 (MAE 분석 → 노이즈 손절 방지)
            base_tp = max(base_tp - 0.3, 1.0)
            base_sl = max(base_sl, -2.5)  # -1.2→-2.5% (변동성 흡수)

        # 2026-04-07: 고모멘텀 종목 TP 대폭 확대
        # 프로미천(+29%), 풍산홀딩스(+30%) 놓침 → +15% 이상 종목 TP 5.0~8.0%
        if change_pct >= 20:
            base_tp = max(base_tp, 6.0)  # +20% 이상: 최소 TP 6%
        elif change_pct >= 15:
            base_tp = max(base_tp, 5.0)  # +15% 이상: 최소 TP 5%

        # 거래대금 규모별 TP 보정
        tv_억 = trade_value / 100_000_000 if trade_value else 0
        if tv_억 >= 1000:
            # 대형주: 되돌림 작으므로 TP 확대 (+0.5~1.0%)
            tp_adj = min(base_tp + 1.0, 8.0)
            return {'tp_pct': tp_adj, 'sl_pct': base_sl, 'zone': zone}
        else:
            # 중소형주: zone 설정 그대로 사용
            return {'tp_pct': base_tp, 'sl_pct': base_sl, 'zone': zone}

    def set_market_change(self, kosdaq_change_pct: float):
        """코스닥 지수 등락률 설정 (시장 동조성 필터용)"""
        self._kosdaq_change_pct = kosdaq_change_pct

    def analyze(
        self,
        snapshots: List[StockSnapshot],
        cache_data: Optional[Dict[str, pd.DataFrame]] = None,
        intraday_data: Optional[Dict[str, List[dict]]] = None,
    ) -> List[ScalpingAnalysis]:
        """
        전체 후보 종목에 대해 10명의 전문가가 스캘핑 분석 수행

        Args:
            snapshots: 상승률 높은 종목 스냅샷 리스트
            cache_data: {code: OHLCV DataFrame}
            intraday_data: {code: [{time, price, volume}, ...]}

        Returns:
            종합 점수 내림차순 ScalpingAnalysis 리스트
        """
        logger.info("=" * 60)
        logger.info(f"스캘핑 타이밍 팀 분석 시작 ({len(snapshots)}종목)")
        logger.info("=" * 60)

        if cache_data is None:
            cache_data = self._load_cache(snapshots)
        if intraday_data is None:
            intraday_data = {}

        # 상대강도 에이전트에 전체 유니버스 주입
        rs_agent = self.agents.get('상대강도전문가')
        if isinstance(rs_agent, RelativeStrengthAgent):
            rs_agent.set_universe(snapshots)

        # ── 종목별 분석 (사전 필터 → 에이전트 분석) ──
        results: List[ScalpingAnalysis] = []
        filtered_count = 0

        for snapshot in snapshots:
            code = snapshot.code
            ohlcv = cache_data.get(code)
            intraday = intraday_data.get(code, [])

            # 사전 필터 (하드 게이트)
            passed, reason = self.pre_score_filter(
                snapshot, ohlcv, intraday)
            if not passed:
                logger.info(f"  [{code}] 사전 필터 차단: {reason}")
                filtered_count += 1
                continue

            analysis = self._analyze_stock(snapshot, ohlcv, intraday)
            if analysis:
                # 급등 유형 분류
                surge_type = self.classify_surge_type(snapshot)
                analysis.surge_type = surge_type

                # 2026-04-07: 1분봉 ATR 계산 (변동성 비례 SL/트레일링용)
                analysis.intraday_atr = self._calc_intraday_atr(intraday)

                # 진입 구간 + 거래대금 규모별 + 급등 유형별 TP/SL 오버라이드
                zone = self.get_zone_params(
                    snapshot.change_pct,
                    getattr(snapshot, 'trade_value', 0) or 0,
                    surge_type,
                )
                analysis.scalp_tp_pct = zone['tp_pct']
                analysis.scalp_sl_pct = zone['sl_pct']

                # ── 수수료 감안 수익 구조 필터 (zone 오버라이드 후) ──
                if analysis.scalp_tp_pct < self._min_tp_pct:
                    logger.info(
                        f"  [{code}] TP +{analysis.scalp_tp_pct:.1f}% < "
                        f"최소 +{self._min_tp_pct:.1f}% → 관망 전환")
                    analysis.timing = "관망"

                # ── 손익비 필터 (zone 오버라이드 후, R:R ≥ 1.0) ──
                if analysis.scalp_sl_pct != 0:
                    rr_ratio = analysis.scalp_tp_pct / abs(analysis.scalp_sl_pct)
                    if rr_ratio < 1.0:
                        logger.info(
                            f"  [{code}] 손익비 {rr_ratio:.2f}:1 < 1.0:1 "
                            f"(TP +{analysis.scalp_tp_pct:.1f}% / "
                            f"SL {analysis.scalp_sl_pct:.1f}%) → 관망 전환")
                        analysis.timing = "관망"

                results.append(analysis)

        if filtered_count > 0:
            logger.info(
                f"  사전 필터: {filtered_count}종목 차단, "
                f"{len(results)}종목 분석 완료")

        # ── 최근 분봉 방향 필터 (2026-04-07: 하락 중/음봉 즉시 진입 방지) ──
        if intraday_data:
            for r in results:
                if r.timing != "즉시":
                    continue
                intra = intraday_data.get(r.code, [])
                if len(intra) >= 3:
                    # (A) 직전 분봉이 음봉이면 즉시→대기 (open > close)
                    last_tick = intra[-1]
                    tick_open = last_tick.get('open', 0)
                    tick_close = last_tick.get('price', 0)
                    if tick_open > 0 and tick_close < tick_open:
                        drop = (tick_open - tick_close) / tick_open * 100
                        if drop >= 0.1:
                            logger.info(
                                f"  [{r.code}] 현재 분봉 음봉 "
                                f"({tick_open:,}→{tick_close:,}, "
                                f"-{drop:.1f}%) → 즉시→대기 전환")
                            r.timing = "대기"
                            r.top_reasons.insert(0,
                                f"⚠ 현재 분봉 음봉 (-{drop:.1f}%)")
                            continue

                if len(intra) >= 5:
                    # (B) 직전 5틱 하락 중
                    recent_5 = [t['price'] for t in intra[-5:]]
                    net_move = (recent_5[-1] - recent_5[0]) / recent_5[0] * 100
                    if net_move <= -0.3:
                        logger.info(
                            f"  [{r.code}] 직전 5틱 하락 {net_move:+.1f}% "
                            f"→ 즉시→대기 전환")
                        r.timing = "대기"
                        r.top_reasons.insert(0,
                            f"⚠ 직전 분봉 하락 중 ({net_move:+.1f}%)")

        # ── 10:00-11:00 최소 점수 필터 (1분봉 분석: 승률 20%) ──
        now_t = datetime.now().time()
        if time(10, 0) <= now_t < time(11, 0):
            for r in results:
                if r.total_score < self._mid_morning_min_score and r.timing != "관망":
                    logger.info(
                        f"  [{r.code}] 10시대 점수 {r.total_score:.0f} < "
                        f"{self._mid_morning_min_score} → 관망 전환")
                    r.timing = "관망"

        # ── 동시 진입 제한: 이미 N종목 보유 중이면 신규 진입 차단 ──
        active_count = len(self._active_positions)
        if active_count >= self._max_simultaneous_entries:
            for r in results:
                if r.code not in self._active_positions and r.timing == "즉시":
                    logger.info(
                        f"  [{r.code}] 동시보유 {active_count}종목 >= "
                        f"{self._max_simultaneous_entries} → 대기 전환")
                    r.timing = "대기"

        # ── 종합 점수 기준 정렬 ──
        results.sort(key=lambda a: a.total_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        logger.info("=" * 60)
        logger.info("스캘핑 타이밍 분석 완료")
        for r in results[:5]:
            surge_label = {'gap_up': '갭상승', 'intraday': '장중급등',
                           'mixed': '혼합', 'unknown': '미분류'}.get(
                           r.surge_type, '미분류')
            logger.info(
                f"  #{r.rank} [{r.code}] {r.name}: "
                f"점수 {r.total_score:.0f} | {r.timing} | "
                f"합의: {r.consensus_level} | "
                f"유형: {surge_label} | "
                f"TP +{r.scalp_tp_pct:.1f}% / SL {r.scalp_sl_pct:.1f}%"
            )
        logger.info("=" * 60)

        return results

    def _analyze_stock(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday: List[dict],
    ) -> Optional[ScalpingAnalysis]:
        """단일 종목에 대해 10명 에이전트 분석 → 종합"""

        signals: Dict[str, ScalpingSignal] = {}

        for agent_name, agent in self.agents.items():
            try:
                sig = agent.analyze(snapshot, ohlcv, intraday)
                if sig:
                    signals[agent_name] = sig
            except Exception as e:
                logger.error(f"  [{agent_name}] 분석 오류 [{snapshot.code}]: {e}")

        if not signals:
            return None

        # ── 시그널 통합 ──
        analysis = ScalpingAnalysis(
            code=snapshot.code,
            name=snapshot.name,
            snapshot=snapshot,
        )

        # 1. 가중 평균 점수
        total_score = 0
        total_weight = 0
        for name, sig in signals.items():
            w = self.AGENT_WEIGHTS.get(name, 0.05) * sig.confidence
            total_score += sig.entry_score * w
            total_weight += w

        analysis.total_score = total_score / total_weight if total_weight > 0 else 0

        # 2. 종합 신뢰도
        analysis.confidence = sum(
            sig.confidence * self.AGENT_WEIGHTS.get(name, 0.05)
            for name, sig in signals.items()
        ) / sum(self.AGENT_WEIGHTS.get(name, 0.05) for name in signals)

        # 3. 타이밍 합의
        timing_votes = {}
        for name, sig in signals.items():
            t = sig.timing or "대기"
            w = self.AGENT_WEIGHTS.get(name, 0.05)
            timing_votes[t] = timing_votes.get(t, 0) + w

        # 가장 많은 가중치를 받은 타이밍
        best_timing = max(timing_votes.items(), key=lambda x: x[1])
        analysis.timing = best_timing[0]

        # 합의 수준
        total_vote_weight = sum(timing_votes.values())
        consensus_pct = best_timing[1] / total_vote_weight if total_vote_weight > 0 else 0
        n_agents = len(signals)
        agree_count = sum(
            1 for sig in signals.values()
            if (sig.timing or "대기") == best_timing[0]
        )

        if agree_count >= n_agents * 0.8:
            analysis.consensus_level = "만장일치"
        elif agree_count >= n_agents * 0.6:
            analysis.consensus_level = "다수합의"
        elif agree_count >= n_agents * 0.3:
            # 2026-04-09: 0.4→0.3 완화 (매매 빈도 확대)
            analysis.consensus_level = "소수합의"
        else:
            analysis.consensus_level = "의견분분"

        # 4. 스캘핑 파라미터 (가중 평균)
        tp_values, sl_values, hold_values = [], [], []
        tp_weights, sl_weights, hold_weights = [], [], []

        for name, sig in signals.items():
            w = self.AGENT_WEIGHTS.get(name, 0.05) * sig.confidence
            if sig.scalp_tp_pct is not None:
                tp_values.append(sig.scalp_tp_pct)
                tp_weights.append(w)
            if sig.scalp_sl_pct is not None:
                sl_values.append(sig.scalp_sl_pct)
                sl_weights.append(w)
            if sig.hold_minutes is not None:
                hold_values.append(sig.hold_minutes)
                hold_weights.append(w)

        if tp_values and sum(tp_weights) > 0:
            analysis.scalp_tp_pct = round(
                sum(v * w for v, w in zip(tp_values, tp_weights))
                / sum(tp_weights), 1)
        if sl_values and sum(sl_weights) > 0:
            merged_sl = round(
                sum(v * w for v, w in zip(sl_values, sl_weights))
                / sum(sl_weights), 1)
            # SL 하한선 적용 (에이전트 SL이 너무 타이트하면 확대)
            analysis.scalp_sl_pct = min(merged_sl, self._sl_floor)
        if hold_values and sum(hold_weights) > 0:
            analysis.hold_minutes = round(
                sum(v * w for v, w in zip(hold_values, hold_weights))
                / sum(hold_weights))

        # 5. 최적 진입가
        entry_prices = [
            sig.entry_price_zone for sig in signals.values()
            if sig.entry_price_zone is not None
        ]
        if entry_prices:
            analysis.optimal_entry_price = round(
                sum(entry_prices) / len(entry_prices))
        else:
            analysis.optimal_entry_price = snapshot.price

        # 6. 핵심 사유 수집 (상위 5개)
        all_reasons = []
        for name, sig in signals.items():
            w = self.AGENT_WEIGHTS.get(name, 0.05)
            for reason in sig.reasons[:2]:  # 에이전트당 최대 2개
                all_reasons.append((w * sig.confidence, f"[{name}] {reason}"))

        all_reasons.sort(key=lambda x: x[0], reverse=True)
        analysis.top_reasons = [r[1] for r in all_reasons[:7]]

        # 7. 에이전트 시그널 저장
        analysis.agent_signals = signals

        # ── 수수료/손익비 필터는 analyze()에서 zone 오버라이드 후 실행 ──
        # (에이전트 계산 SL과 zone SL이 다르므로, zone 적용 후 판단)

        # 의견분분 차단: 합의 수준이 최소 미달이면 관망
        consensus_rank = {
            '만장일치': 4, '다수합의': 3, '소수합의': 2, '의견분분': 1,
        }
        min_rank = consensus_rank.get(self._min_consensus, 2)
        current_rank = consensus_rank.get(analysis.consensus_level, 1)
        if current_rank < min_rank:
            logger.info(
                f"  [{snapshot.code}] 합의 '{analysis.consensus_level}' < "
                f"최소 '{self._min_consensus}' → 관망 전환")
            analysis.timing = "관망"

        return analysis

    @staticmethod
    def _calc_intraday_atr(intraday: List[dict], period: int = 14) -> float:
        """1분봉 기반 ATR 계산 (변동성 비례 SL/트레일링용)

        각 틱의 가격 변동폭(|현재-이전|)의 이동평균으로 근사.
        데이터 부족 시 0 반환.
        """
        if not intraday or len(intraday) < period + 1:
            return 0.0
        prices = [t['price'] for t in intraday if t.get('price', 0) > 0]
        if len(prices) < period + 1:
            return 0.0
        # True Range 근사: |price[i] - price[i-1]|
        true_ranges = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
        # 최근 period개의 평균
        recent_tr = true_ranges[-period:]
        return sum(recent_tr) / len(recent_tr)

    def _load_cache(
        self, snapshots: List[StockSnapshot],
    ) -> Dict[str, pd.DataFrame]:
        """OHLCV 캐시 로드"""
        try:
            from scanner.ohlcv_cache import OHLCVCache
            cache = OHLCVCache(self._cache_dir)
            data = {}
            for s in snapshots:
                df = cache.load(s.code)
                if df is not None:
                    data[s.code] = df
            return data
        except Exception as e:
            logger.error(f"캐시 로드 실패: {e}")
            return {}

    def format_report(self, results: List[ScalpingAnalysis], top_n: int = 10) -> str:
        """텔레그램 HTML 리포트 포맷"""
        if not results:
            return "<b>스캘핑 분석 결과 없음</b>"

        lines = [
            "<b>🎯 스캘핑 타이밍 팀 분석</b>",
            f"분석 종목: {len(results)}개 | 전문가: 10명",
            "",
        ]

        # 타이밍별 아이콘
        timing_icon = {
            '즉시': '🟢',
            '대기': '🟡',
            '눌림목대기': '🔵',
            '관망': '🔴',
        }

        for r in results[:top_n]:
            icon = timing_icon.get(r.timing, '⚪')
            consensus_icon = {
                '만장일치': '🤝',
                '다수합의': '👍',
                '소수합의': '🤔',
                '의견분분': '⚖️',
            }.get(r.consensus_level, '')

            lines.append(
                f"<b>#{r.rank} {icon} [{r.code}] {r.name}</b>"
            )
            lines.append(
                f"  점수: {r.total_score:.0f}/100 | "
                f"{r.timing} | {consensus_icon} {r.consensus_level}"
            )

            if r.snapshot:
                lines.append(
                    f"  현재가: {r.snapshot.price:,.0f}원 "
                    f"({r.snapshot.change_pct:+.1f}%) | "
                    f"거래량 {r.snapshot.volume_ratio:.1f}배"
                )

            lines.append(
                f"  최적진입: {r.optimal_entry_price:,.0f}원 | "
                f"TP +{r.scalp_tp_pct:.1f}% / SL {r.scalp_sl_pct:.1f}% | "
                f"{r.hold_minutes}분 보유"
            )

            # 핵심 사유 (상위 3개)
            for reason in r.top_reasons[:3]:
                lines.append(f"  • {reason}")

            lines.append("")

        # 요약 통계
        entry_count = sum(1 for r in results if r.timing == "즉시")
        wait_count = sum(1 for r in results if r.timing in ("대기", "눌림목대기"))
        avoid_count = sum(1 for r in results if r.timing == "관망")

        lines.append(
            f"<b>요약</b>: 즉시진입 {entry_count} | "
            f"대기 {wait_count} | 관망 {avoid_count}"
        )

        return "\n".join(lines)

    def format_detail(self, analysis: ScalpingAnalysis) -> str:
        """단일 종목 상세 분석 포맷"""
        lines = [
            f"<b>🔍 [{analysis.code}] {analysis.name} 상세 분석</b>",
            f"종합 점수: {analysis.total_score:.0f}/100 | "
            f"신뢰도: {analysis.confidence:.0%}",
            f"타이밍: {analysis.timing} ({analysis.consensus_level})",
            "",
            f"<b>진입 제안</b>",
            f"  최적가: {analysis.optimal_entry_price:,.0f}원",
            f"  익절: +{analysis.scalp_tp_pct:.1f}%",
            f"  손절: {analysis.scalp_sl_pct:.1f}%",
            f"  보유: {analysis.hold_minutes}분",
            "",
            "<b>에이전트별 판단</b>",
        ]

        for name, sig in sorted(
            analysis.agent_signals.items(),
            key=lambda x: x[1].entry_score,
            reverse=True,
        ):
            w = self.AGENT_WEIGHTS.get(name, 0.05) * 100
            icon = {'즉시': '🟢', '대기': '🟡', '눌림목대기': '🔵', '관망': '🔴'
                    }.get(sig.timing, '⚪')
            lines.append(
                f"  {icon} {name} ({w:.0f}%): "
                f"{sig.entry_score:.0f}점 | {sig.timing}"
            )
            if sig.entry_trigger:
                lines.append(f"     → {sig.entry_trigger}")

        if analysis.top_reasons:
            lines.append("")
            lines.append("<b>핵심 분석</b>")
            for reason in analysis.top_reasons:
                lines.append(f"  • {reason}")

        return "\n".join(lines)
