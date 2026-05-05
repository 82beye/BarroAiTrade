"""
전략 최적화 코디네이터

5개 전략 에이전트의 분석 결과를 통합하여
당일 매매에 적용할 최종 전략 파라미터(StrategyParams)를 산출한다.

에이전트별 역할:
  - TradePatternAgent  (30%): 반복 실수 방지 → 쿨다운, 횟수 제한
  - EntryTimingAgent   (20%): 시간대별 승률 → 진입 시간 조정
  - RiskRewardAgent    (20%): 종목별 리스크 → 부스트/페널티
  - SizingAgent        (15%): 변동성/연패 → 포지션 비중
  - ExitOptimizerAgent (15%): 손절/익절 → 동적 조정

통합 규칙:
  - 여러 에이전트가 같은 파라미터를 제안하면 가중 평균 또는 보수적 값
  - confidence가 높은 에이전트의 제안이 더 반영됨
  - 블랙리스트는 합집합 (한 에이전트라도 차단하면 차단)
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from strategy.strategy_team.base_agent import (
    BaseStrategyAgent, StrategySignal, TradeRecord,
)
from strategy.strategy_team.trade_pattern_agent import TradePatternAgent
from strategy.strategy_team.entry_timing_agent import EntryTimingAgent
from strategy.strategy_team.risk_reward_agent import RiskRewardAgent
from strategy.strategy_team.sizing_agent import SizingAgent
from strategy.strategy_team.exit_optimizer_agent import ExitOptimizerAgent

logger = logging.getLogger(__name__)


@dataclass
class StrategyParams:
    """
    전략 팀 에이전트가 산출한 당일 매매 파라미터

    IntradayFilter, EntrySignalGenerator, ExitSignalGenerator에 주입하여
    당일 매매 전략을 동적으로 조정한다.
    """
    # 진입 필터
    cooldown_minutes: int = 10
    max_entries_per_stock: int = 3
    max_bb_excess_pct: float = 8.0
    max_breakout_pct: float = 7.0      # 파란점선 돌파율 상한
    entry_start_delay_minutes: int = 5  # 09:00 + N분 = 매수 시작

    # 손절/익절 (settings.yaml 기준)
    stop_loss_pct: float = -3.5
    take_profit_1_pct: float = 5.0
    take_profit_2_pct: float = 8.0
    breakeven_buffer_pct: float = 0.3   # 1차 익절 후 브레이크이븐 보호

    # 포지션 사이징
    position_size_multiplier: float = 1.0

    # 종목별 조정
    stock_boost: Dict[str, float] = field(default_factory=dict)
    stock_penalty: Dict[str, float] = field(default_factory=dict)
    blacklist_codes: List[str] = field(default_factory=list)

    # 메타
    agent_reports: List[str] = field(default_factory=list)
    confidence: float = 0.0


class StrategyCoordinator:
    """전략 최적화 코디네이터"""

    AGENT_WEIGHTS = {
        'trade_pattern': 0.30,
        'entry_timing': 0.20,
        'risk_reward': 0.20,
        'sizing': 0.15,
        'exit_optimizer': 0.15,
    }

    def __init__(self, config: dict):
        self.config = config
        self.trade_log_path = config.get(
            'logging', {}).get('trade_log', './logs/trades.jsonl')
        cache_dir = config.get(
            'scanner', {}).get('cache_dir', './data/ohlcv_cache')
        self._cache_dir = cache_dir

        self.agents: Dict[str, BaseStrategyAgent] = {
            'trade_pattern': TradePatternAgent(),
            'entry_timing': EntryTimingAgent(),
            'risk_reward': RiskRewardAgent(),
            'sizing': SizingAgent(),
            'exit_optimizer': ExitOptimizerAgent(),
        }

    def optimize(
        self,
        watchlist: List[dict],
        cache_data: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> StrategyParams:
        """
        전략 최적화 실행

        Args:
            watchlist: 오늘의 관심종목
            cache_data: OHLCV 캐시. None이면 자동 로드

        Returns:
            StrategyParams - 당일 매매에 적용할 파라미터
        """
        logger.info("=" * 60)
        logger.info("전략 최적화 팀 에이전트 분석 시작")
        logger.info("=" * 60)

        # ── 1. 매매 기록 로드 ──
        trades = self._load_trades()
        logger.info(f"매매 기록 로드: {len(trades)}건")

        # ── 2. 캐시 로드 ──
        if cache_data is None:
            cache_data = self._load_cache(watchlist)
        logger.info(f"OHLCV 캐시: {len(cache_data)}종목")

        # ── 3. 각 에이전트 독립 분석 ──
        signals: Dict[str, StrategySignal] = {}

        for agent_name, agent in self.agents.items():
            try:
                signal = agent.analyze(trades, cache_data, watchlist)
                if signal:
                    signals[agent_name] = signal
                    logger.info(
                        f"  [{agent_name}] 분석 완료 "
                        f"(신뢰도: {signal.confidence:.0%}, "
                        f"권고: {len(signal.reasons)}건)")
                else:
                    logger.info(f"  [{agent_name}] 데이터 부족 (스킵)")
            except Exception as e:
                logger.error(
                    f"  [{agent_name}] 분석 오류: {e}", exc_info=True)

        # ── 4. 신호 통합 → StrategyParams ──
        params = self._merge_signals(signals)

        logger.info("=" * 60)
        logger.info("전략 최적화 완료")
        logger.info(
            f"  쿨다운: {params.cooldown_minutes}분 | "
            f"종목한도: {params.max_entries_per_stock}회 | "
            f"BB한도: +{params.max_bb_excess_pct:.1f}%")
        logger.info(
            f"  손절: {params.stop_loss_pct}% | "
            f"익절: +{params.take_profit_1_pct}%/+{params.take_profit_2_pct}%")
        logger.info(
            f"  돌파한도: +{params.max_breakout_pct:.1f}% | "
            f"BE버퍼: +{params.breakeven_buffer_pct:.1f}%")
        logger.info(
            f"  포지션배율: {params.position_size_multiplier:.0%} | "
            f"진입시작: 09:{params.entry_start_delay_minutes:02d}")
        if params.blacklist_codes:
            logger.info(f"  블랙리스트: {params.blacklist_codes}")
        logger.info(
            f"  부스트: {len(params.stock_boost)}종목 | "
            f"페널티: {len(params.stock_penalty)}종목")
        logger.info(f"  종합 신뢰도: {params.confidence:.0%}")
        logger.info("=" * 60)

        return params

    def _merge_signals(
        self, signals: Dict[str, StrategySignal],
    ) -> StrategyParams:
        """
        5개 에이전트 신호를 통합하여 최종 파라미터 산출

        가중 합산 규칙:
          - 숫자 파라미터: confidence × weight 기반 가중 평균
          - 불리언/리스트: 합집합 (1개라도 제안하면 적용)
          - 충돌 시: 보수적 방향 (더 안전한 쪽)
        """
        params = StrategyParams()

        if not signals:
            return params

        # ── 가중 평균 헬퍼 ──
        def weighted_avg(
            attr: str,
            default: float,
            conservative: str = 'min',
        ) -> float:
            """신호들의 가중 평균. conservative='min'이면 안전한 쪽"""
            values = []
            weights = []
            for name, sig in signals.items():
                val = getattr(sig, attr, None)
                if val is not None:
                    w = sig.confidence * self.AGENT_WEIGHTS.get(name, 0.1)
                    values.append(val)
                    weights.append(w)

            if not values:
                return default

            total_w = sum(weights)
            if total_w <= 0:
                return default

            avg = sum(v * w for v, w in zip(values, weights)) / total_w

            if conservative == 'min':
                return min(avg, default)
            elif conservative == 'max':
                return max(avg, default)
            return avg

        # ── 진입 필터 파라미터 ──
        cooldowns = [
            s.cooldown_minutes for s in signals.values()
            if s.cooldown_minutes is not None]
        if cooldowns:
            # 가장 보수적인 (큰) 값 사용
            params.cooldown_minutes = max(cooldowns)

        max_entries = [
            s.max_entries_per_stock for s in signals.values()
            if s.max_entries_per_stock is not None]
        if max_entries:
            # 가장 보수적인 (작은) 값 사용
            params.max_entries_per_stock = min(max_entries)

        # BB 과열: 신뢰도 가중 평균 (보수적 → 기본값 이하)
        bb_result = weighted_avg('max_bb_excess_pct', 8.0, 'min')
        if bb_result != 8.0:
            params.max_bb_excess_pct = round(bb_result, 1)

        # 파란점선 돌파율 상한: 보수적 (작은) 값 사용
        breakout_limits = [
            s.max_breakout_pct for s in signals.values()
            if s.max_breakout_pct is not None]
        if breakout_limits:
            params.max_breakout_pct = min(breakout_limits)

        # 브레이크이븐 버퍼: 보수적 (작은) 값 사용
        be_buffers = [
            s.breakeven_buffer_pct for s in signals.values()
            if s.breakeven_buffer_pct is not None]
        if be_buffers:
            params.breakeven_buffer_pct = min(be_buffers)

        delays = [
            s.entry_start_delay_minutes for s in signals.values()
            if s.entry_start_delay_minutes is not None]
        if delays:
            params.entry_start_delay_minutes = max(delays)

        # ── 손절/익절 (신뢰도 가중 평균) ──
        # 손절: settings.yaml 기준(-3.5%)을 하한선으로 존중
        # conservative='min' → 넓은 쪽 (0에서 먼 쪽) 우선
        sl_result = weighted_avg('stop_loss_pct', -3.5, 'min')
        if sl_result != -3.5:
            # 에이전트가 settings.yaml보다 타이트하게 설정하지 못하도록 하한선 적용
            params.stop_loss_pct = round(min(sl_result, -3.5), 1)
        else:
            params.stop_loss_pct = -3.5

        # 익절: settings.yaml 기준 반영
        tp1_result = weighted_avg('take_profit_1_pct', 5.0, 'none')
        if tp1_result != 5.0:
            params.take_profit_1_pct = round(tp1_result, 1)
        else:
            params.take_profit_1_pct = 5.0

        tp2_result = weighted_avg('take_profit_2_pct', 8.0, 'none')
        if tp2_result != 8.0:
            params.take_profit_2_pct = round(tp2_result, 1)
        else:
            params.take_profit_2_pct = 8.0

        # ── 포지션 사이징 ──
        size_mults = [
            s.position_size_multiplier for s in signals.values()
            if s.position_size_multiplier is not None]
        if size_mults:
            # 가장 보수적인 (작은) 값 사용
            params.position_size_multiplier = min(size_mults)

        # ── 종목별 조정 (합산) ──
        for sig in signals.values():
            for code, boost in sig.stock_boost.items():
                params.stock_boost[code] = max(
                    params.stock_boost.get(code, 0), boost)
            for code, penalty in sig.stock_penalty.items():
                params.stock_penalty[code] = max(
                    params.stock_penalty.get(code, 0), penalty)

        # ── 블랙리스트 (합집합) ──
        bl_set = set()
        for sig in signals.values():
            bl_set.update(sig.blacklist_codes)
        params.blacklist_codes = sorted(bl_set)

        # ── 에이전트 리포트 수집 ──
        for name, sig in signals.items():
            if sig.reasons:
                header = f"[{name}] (신뢰도 {sig.confidence:.0%})"
                for reason in sig.reasons:
                    params.agent_reports.append(f"{header}: {reason}")

        # ── 종합 신뢰도 ──
        if signals:
            total_conf = sum(
                sig.confidence * self.AGENT_WEIGHTS.get(name, 0.1)
                for name, sig in signals.items()
            )
            total_weight = sum(
                self.AGENT_WEIGHTS.get(name, 0.1)
                for name in signals.keys()
            )
            params.confidence = total_conf / total_weight if total_weight > 0 else 0

        return params

    def _load_trades(self) -> List[TradeRecord]:
        """최근 5일 매매 기록 로드"""
        path = Path(self.trade_log_path)
        if not path.exists():
            return []

        trades = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    trades.append(TradeRecord(
                        action=d['action'],
                        code=d['code'],
                        name=d.get('name', ''),
                        qty=d['qty'],
                        price=d['price'],
                        timestamp=d['timestamp'],
                        amount=d.get('amount', 0),
                        entry_price=d.get('entry_price', 0),
                        pnl_pct=d.get('pnl_pct', 0),
                        exit_type=d.get('exit_type', ''),
                        reason=d.get('reason', ''),
                        daily_pnl_pct=d.get('daily_pnl_pct', 0),
                    ))
                except (json.JSONDecodeError, KeyError):
                    pass

        return trades

    def _load_cache(
        self, watchlist: List[dict],
    ) -> Dict[str, pd.DataFrame]:
        """관심종목 OHLCV 캐시 로드"""
        from scanner.ohlcv_cache import OHLCVCache
        cache = OHLCVCache(self._cache_dir)
        cache_data = {}
        for stock in watchlist:
            code = stock['code']
            df = cache.load(code)
            if df is not None:
                cache_data[code] = df
        return cache_data

    def save_params(self, params: StrategyParams, path: str = None):
        """
        전략 파라미터를 JSON으로 저장 (다음날 morning scan에서 로드)

        post-market 분석 결과를 영속화하여
        다음 거래일 08:30 스캔에서 자동 적용.
        """
        if path is None:
            log_dir = Path(self.trade_log_path).parent
            path = str(log_dir / 'strategy_params_optimized.json')

        data = {
            'generated_at': datetime.now().isoformat(),
            'cooldown_minutes': params.cooldown_minutes,
            'max_entries_per_stock': params.max_entries_per_stock,
            'max_bb_excess_pct': params.max_bb_excess_pct,
            'max_breakout_pct': params.max_breakout_pct,
            'entry_start_delay_minutes': params.entry_start_delay_minutes,
            'stop_loss_pct': params.stop_loss_pct,
            'take_profit_1_pct': params.take_profit_1_pct,
            'take_profit_2_pct': params.take_profit_2_pct,
            'breakeven_buffer_pct': params.breakeven_buffer_pct,
            'position_size_multiplier': params.position_size_multiplier,
            'stock_boost': params.stock_boost,
            'stock_penalty': params.stock_penalty,
            'blacklist_codes': params.blacklist_codes,
            'confidence': params.confidence,
            'agent_reports': params.agent_reports,
        }

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"전략 파라미터 저장: {path}")

    @staticmethod
    def load_params(path: str = None) -> Optional['StrategyParams']:
        """
        저장된 전략 파라미터 로드

        Returns:
            StrategyParams 또는 None (파일 없음 / 만료)
        """
        if path is None:
            path = './logs/strategy_params_optimized.json'

        p = Path(path)
        if not p.exists():
            return None

        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 1일 이상 지난 파라미터는 무시
            from datetime import datetime as dt
            generated = dt.fromisoformat(data['generated_at'])
            if (dt.now() - generated).days >= 2:
                logger.info(
                    f"전략 파라미터 만료 (생성: {data['generated_at']})")
                return None

            params = StrategyParams(
                cooldown_minutes=data.get('cooldown_minutes', 10),
                max_entries_per_stock=data.get('max_entries_per_stock', 3),
                max_bb_excess_pct=data.get('max_bb_excess_pct', 8.0),
                max_breakout_pct=data.get('max_breakout_pct', 7.0),
                entry_start_delay_minutes=data.get(
                    'entry_start_delay_minutes', 5),
                stop_loss_pct=data.get('stop_loss_pct', -3.5),
                take_profit_1_pct=data.get('take_profit_1_pct', 5.0),
                take_profit_2_pct=data.get('take_profit_2_pct', 8.0),
                breakeven_buffer_pct=data.get('breakeven_buffer_pct', 0.3),
                position_size_multiplier=data.get(
                    'position_size_multiplier', 1.0),
                stock_boost=data.get('stock_boost', {}),
                stock_penalty=data.get('stock_penalty', {}),
                blacklist_codes=data.get('blacklist_codes', []),
                confidence=data.get('confidence', 0.0),
                agent_reports=data.get('agent_reports', []),
            )
            logger.info(
                f"전략 파라미터 로드 성공 (생성: {data['generated_at']})")
            return params
        except Exception as e:
            logger.error(f"전략 파라미터 로드 실패: {e}")
            return None

    def format_report(self, params: StrategyParams) -> str:
        """StrategyParams를 텔레그램 HTML 메시지로 포맷"""
        lines = [
            "<b>전략 최적화 팀 분석 결과</b>",
            f"종합 신뢰도: {params.confidence:.0%}",
            "",
            "<b>당일 파라미터</b>",
            f"  진입 시작: 09:{params.entry_start_delay_minutes:02d}",
            f"  쿨다운: {params.cooldown_minutes}분",
            f"  종목당 한도: {params.max_entries_per_stock}회",
            f"  BB 과열: +{params.max_bb_excess_pct:.1f}%",
            f"  돌파 상한: +{params.max_breakout_pct:.1f}%",
            f"  손절: {params.stop_loss_pct}%",
            f"  익절: +{params.take_profit_1_pct}% / "
            f"+{params.take_profit_2_pct}%",
            f"  BE 스톱: +{params.breakeven_buffer_pct:.1f}%",
            f"  포지션 배율: {params.position_size_multiplier:.0%}",
        ]

        if params.blacklist_codes:
            lines.append(
                f"\n<b>블랙리스트</b>: {', '.join(params.blacklist_codes)}")

        if params.stock_boost:
            boost_str = ", ".join(
                f"{c}(+{v:.0%})"
                for c, v in sorted(
                    params.stock_boost.items(),
                    key=lambda x: x[1], reverse=True,
                )[:5]
            )
            lines.append(f"\n<b>부스트 종목</b>: {boost_str}")

        if params.stock_penalty:
            pen_str = ", ".join(
                f"{c}(-{v:.0%})"
                for c, v in sorted(
                    params.stock_penalty.items(),
                    key=lambda x: x[1], reverse=True,
                )[:5]
            )
            lines.append(f"\n<b>페널티 종목</b>: {pen_str}")

        if params.agent_reports:
            lines.append("\n<b>에이전트 상세</b>")
            for report in params.agent_reports[:10]:
                lines.append(f"  {report}")

        return "\n".join(lines)
