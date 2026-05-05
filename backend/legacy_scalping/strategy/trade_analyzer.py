"""
매매 기록 분석기 (PostTradeAnalyzer)

trades.jsonl을 분석하여 매매 패턴의 강점/약점을 식별하고
전략 고도화를 위한 인사이트를 생성한다.

분석 항목:
  - 시간대별 승률
  - 종목별 반복 매수 패턴
  - 손절 후 재진입 패턴
  - BB 돌파율 대비 실제 수익
  - 포지션 사이징 효율성
  - [신규] 슬리피지 분석 (signal_price vs 체결가)
  - [신규] 필터 차단 통계 (로그 기반)
  - [신규] 반등청산 조기 매도 분석
  - [신규] 거래소 대조 (API 연동 시)
"""

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """개별 매매 기록"""
    action: str
    code: str
    name: str
    qty: int
    price: float
    timestamp: datetime
    amount: int = 0
    entry_price: float = 0.0
    pnl_pct: float = 0.0
    exit_type: str = ""
    reason: str = ""
    daily_pnl_pct: float = 0.0
    signal_price: float = 0.0
    strategy_type: str = "regular"
    order_no: str = ""


@dataclass
class StockTradeProfile:
    """종목별 매매 프로파일"""
    code: str
    name: str
    buy_count: int = 0
    sell_count: int = 0
    stop_loss_count: int = 0
    take_profit_count: int = 0
    total_pnl: float = 0.0
    avg_hold_seconds: float = 0.0
    avg_bb_excess: float = 0.0
    re_entries_after_sl: int = 0     # 손절 후 재진입 횟수


@dataclass
class TimingInsight:
    """시간대별 분석 인사이트"""
    hour: int
    minute_bucket: int  # 0, 15, 30, 45
    buy_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    avg_pnl_pct: float = 0.0
    win_rate: float = 0.0


@dataclass
class SlippageRecord:
    """슬리피지 기록"""
    code: str
    name: str
    action: str
    signal_price: float
    fill_price: float
    slip_pct: float
    slip_amount: float  # 슬리피지 금액 (price_diff × qty)
    qty: int


@dataclass
class FilterStats:
    """필터 차단 통계"""
    total_blocked: int = 0
    by_reason: Dict[str, int] = field(default_factory=dict)
    total_passed: int = 0
    total_executed: int = 0


@dataclass
class CarryoverExitRecord:
    """반등청산 기록"""
    code: str
    name: str
    exit_type: str
    pnl_pct: float
    reason: str
    strategy_type: str


@dataclass
class ExchangeReconciliation:
    """거래소 대조 결과"""
    price_mismatches: int = 0
    qty_mismatches: int = 0
    exchange_pnl: float = 0.0
    system_pnl: float = 0.0
    pnl_diff: float = 0.0
    details: List[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """종합 분석 리포트"""
    analysis_date: str
    total_trades: int = 0
    total_buys: int = 0
    total_sells: int = 0
    win_rate: float = 0.0
    avg_win_pnl: float = 0.0
    avg_loss_pnl: float = 0.0
    profit_factor: float = 0.0      # 총이익 / 총손실

    # 핵심 문제점
    problems: List[str] = field(default_factory=list)
    # 개선 권장사항
    recommendations: List[str] = field(default_factory=list)

    # 상세 분석
    stock_profiles: Dict[str, StockTradeProfile] = field(default_factory=dict)
    timing_insights: List[TimingInsight] = field(default_factory=list)
    optimal_entry_window: str = ""
    worst_entry_window: str = ""

    # 전략 파라미터 제안
    suggested_cooldown_minutes: int = 0
    suggested_max_entries_per_stock: int = 0
    suggested_min_bb_excess: float = 0.0

    # [신규] 슬리피지 분석
    slippage_records: List[SlippageRecord] = field(default_factory=list)
    total_slippage_amount: float = 0.0

    # [신규] 필터 차단 통계
    filter_stats: Optional[FilterStats] = None

    # [신규] 반등청산 분석
    carryover_exits: List[CarryoverExitRecord] = field(default_factory=list)

    # [신규] 거래소 대조
    exchange_recon: Optional[ExchangeReconciliation] = None


class PostTradeAnalyzer:
    """
    매매 기록 사후 분석기

    trades.jsonl을 파싱하여 패턴을 분석하고
    전략 고도화를 위한 구체적 권장사항을 생성한다.
    """

    def __init__(self, config: dict):
        self.config = config
        self.trade_log_path = config.get(
            'logging', {}).get('trade_log', './logs/trades.jsonl')
        self.log_dir = Path(config.get(
            'logging', {}).get('log_dir', './logs'))

    def analyze(
        self,
        target_date: Optional[str] = None,
        api=None,
    ) -> AnalysisReport:
        """
        매매 기록 분석 실행

        Args:
            target_date: 분석 대상 날짜 (YYYY-MM-DD). None이면 전체 분석.
            api: KiwoomRestAPI 인스턴스 (거래소 대조용, 선택)

        Returns:
            AnalysisReport
        """
        trades = self._load_trades(target_date)
        if not trades:
            logger.warning("분석할 매매 기록이 없습니다")
            return AnalysisReport(analysis_date=target_date or "전체")

        report = AnalysisReport(
            analysis_date=target_date or "전체",
            total_trades=len(trades),
        )

        buys = [t for t in trades if t.action == 'BUY']
        sells = [t for t in trades if t.action == 'SELL']
        report.total_buys = len(buys)
        report.total_sells = len(sells)

        # 기존 분석
        self._calc_win_rate(sells, report)
        self._calc_stock_profiles(trades, report)
        self._calc_timing(buys, sells, report)
        self._calc_re_entry_pattern(trades, report)
        self._calc_bb_excess_correlation(buys, sells, report)

        # [신규] 슬리피지 분석
        self._calc_slippage(trades, report)
        # [신규] 반등청산 분석
        self._calc_carryover_exits(sells, report)
        # [신규] 필터 차단 통계
        self._calc_filter_stats(target_date, report)

        # 문제점 및 권장사항 생성 (마지막에 호출)
        self._generate_insights(report)

        return report

    async def analyze_with_exchange(
        self,
        target_date: str,
        api,
    ) -> AnalysisReport:
        """거래소 대조 포함 분석 (async - API 호출 필요)"""
        report = self.analyze(target_date=target_date)
        if api and target_date:
            await self._calc_exchange_recon(target_date, api, report)
        return report

    def _load_trades(self, target_date: Optional[str]) -> List[TradeRecord]:
        """trades.jsonl 파싱"""
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
                    ts = datetime.fromisoformat(d['timestamp'])
                    if target_date and ts.strftime('%Y-%m-%d') != target_date:
                        continue
                    trades.append(TradeRecord(
                        action=d['action'],
                        code=d['code'],
                        name=d.get('name', ''),
                        qty=d['qty'],
                        price=d['price'],
                        timestamp=ts,
                        amount=d.get('amount', 0),
                        entry_price=d.get('entry_price', 0),
                        pnl_pct=d.get('pnl_pct', 0),
                        exit_type=d.get('exit_type', ''),
                        reason=d.get('reason', ''),
                        daily_pnl_pct=d.get('daily_pnl_pct', 0),
                        signal_price=d.get('signal_price', 0),
                        strategy_type=d.get('strategy_type', 'regular'),
                        order_no=d.get('order_no', ''),
                    ))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"매매 기록 파싱 오류: {e}")
        return trades

    # ── 기존 분석 메서드 ──

    def _calc_win_rate(self, sells: List[TradeRecord], report: AnalysisReport):
        """승률 및 손익비 계산"""
        if not sells:
            return

        wins = [s for s in sells if s.pnl_pct > 0]
        losses = [s for s in sells if s.pnl_pct <= 0]

        report.win_rate = len(wins) / len(sells) * 100 if sells else 0

        total_win = sum(s.pnl_pct for s in wins) if wins else 0
        total_loss = abs(sum(s.pnl_pct for s in losses)) if losses else 0

        report.avg_win_pnl = total_win / len(wins) if wins else 0
        report.avg_loss_pnl = -total_loss / len(losses) if losses else 0
        report.profit_factor = total_win / total_loss if total_loss > 0 else 0

    def _calc_stock_profiles(
        self, trades: List[TradeRecord], report: AnalysisReport,
    ):
        """종목별 매매 프로파일 분석"""
        profiles = {}

        for t in trades:
            if t.code not in profiles:
                profiles[t.code] = StockTradeProfile(
                    code=t.code, name=t.name)
            p = profiles[t.code]

            if t.action == 'BUY':
                p.buy_count += 1
            elif t.action == 'SELL':
                p.sell_count += 1
                p.total_pnl += t.pnl_pct
                if '손절' in t.exit_type:
                    p.stop_loss_count += 1
                elif '익절' in t.exit_type:
                    p.take_profit_count += 1

        report.stock_profiles = profiles

    def _calc_timing(
        self,
        buys: List[TradeRecord],
        sells: List[TradeRecord],
        report: AnalysisReport,
    ):
        """시간대별 매매 분석"""
        buckets = defaultdict(lambda: {
            'buy_count': 0, 'pnl_list': []})

        for b in buys:
            key = (b.timestamp.hour, b.timestamp.minute // 15 * 15)
            buckets[key]['buy_count'] += 1

        buy_queue = defaultdict(list)
        for t in sorted(buys + sells, key=lambda x: x.timestamp):
            if t.action == 'BUY':
                buy_queue[t.code].append(t)
            elif t.action == 'SELL' and buy_queue[t.code]:
                matched_buy = buy_queue[t.code].pop(0)
                key = (matched_buy.timestamp.hour,
                       matched_buy.timestamp.minute // 15 * 15)
                buckets[key]['pnl_list'].append(t.pnl_pct)

        insights = []
        for (hour, minute), data in sorted(buckets.items()):
            pnl_list = data['pnl_list']
            wins = [p for p in pnl_list if p > 0]
            losses = [p for p in pnl_list if p <= 0]
            avg_pnl = sum(pnl_list) / len(pnl_list) if pnl_list else 0

            insights.append(TimingInsight(
                hour=hour,
                minute_bucket=minute,
                buy_count=data['buy_count'],
                win_count=len(wins),
                loss_count=len(losses),
                avg_pnl_pct=round(avg_pnl, 2),
                win_rate=round(
                    len(wins) / len(pnl_list) * 100, 1
                ) if pnl_list else 0,
            ))

        report.timing_insights = insights

        with_pnl = [i for i in insights if (i.win_count + i.loss_count) > 0]
        if with_pnl:
            best = max(with_pnl, key=lambda i: i.avg_pnl_pct)
            worst = min(with_pnl, key=lambda i: i.avg_pnl_pct)
            report.optimal_entry_window = (
                f"{best.hour:02d}:{best.minute_bucket:02d} "
                f"(승률: {best.win_rate:.0f}%, 평균PnL: {best.avg_pnl_pct:+.1f}%)")
            report.worst_entry_window = (
                f"{worst.hour:02d}:{worst.minute_bucket:02d} "
                f"(승률: {worst.win_rate:.0f}%, 평균PnL: {worst.avg_pnl_pct:+.1f}%)")

    def _calc_re_entry_pattern(
        self, trades: List[TradeRecord], report: AnalysisReport,
    ):
        """손절 후 재진입 패턴 분석"""
        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        last_sl_time = {}

        for t in sorted_trades:
            if t.action == 'SELL' and '손절' in t.exit_type:
                last_sl_time[t.code] = t.timestamp
            elif t.action == 'BUY' and t.code in last_sl_time:
                gap = (t.timestamp - last_sl_time[t.code]).total_seconds()
                if gap < 300:
                    profile = report.stock_profiles.get(t.code)
                    if profile:
                        profile.re_entries_after_sl += 1

    def _calc_bb_excess_correlation(
        self,
        buys: List[TradeRecord],
        sells: List[TradeRecord],
        report: AnalysisReport,
    ):
        """BB 돌파율과 실제 수익의 상관관계 분석"""
        bb_excess_pnl = []

        for b in buys:
            if 'BB20 상한 돌파' in b.reason:
                try:
                    parts = b.reason.split('BB20 상한 돌파 ')
                    if len(parts) > 1:
                        pct_str = parts[1].split('%')[0].replace('+', '')
                        bb_excess = float(pct_str)
                        bb_excess_pnl.append({
                            'code': b.code,
                            'bb_excess': bb_excess,
                            'price': b.price,
                            'timestamp': b.timestamp,
                        })
                except (ValueError, IndexError):
                    pass

        if bb_excess_pnl:
            high_bb = [e for e in bb_excess_pnl if e['bb_excess'] >= 5.0]
            if high_bb:
                report.problems.append(
                    f"BB20 +5%% 이상에서 {len(high_bb)}회 매수 "
                    f"(과열 진입 위험)")
            report.suggested_min_bb_excess = 0.5

    # ── [신규] 슬리피지 분석 ──

    def _calc_slippage(
        self, trades: List[TradeRecord], report: AnalysisReport,
    ):
        """signal_price vs 체결가 슬리피지 분석"""
        total_slip = 0.0
        records = []

        for t in trades:
            if t.signal_price and t.signal_price > 0:
                diff = t.price - t.signal_price
                slip_pct = diff / t.signal_price * 100
                slip_amount = diff * t.qty

                # 매수: 높게 체결 = 불리, 매도: 낮게 체결 = 불리
                if t.action == 'BUY':
                    total_slip += slip_amount  # 양수 = 불리
                else:
                    total_slip -= slip_amount  # 음수 = 불리

                if abs(slip_pct) > 0.3:  # 0.3% 이상만 기록
                    records.append(SlippageRecord(
                        code=t.code,
                        name=t.name,
                        action=t.action,
                        signal_price=t.signal_price,
                        fill_price=t.price,
                        slip_pct=round(slip_pct, 2),
                        slip_amount=round(slip_amount),
                        qty=t.qty,
                    ))

        report.slippage_records = records
        report.total_slippage_amount = round(total_slip)

        if abs(total_slip) > 10000:
            report.problems.append(
                f"슬리피지 총액: {total_slip:+,.0f}원 "
                f"(주요 {len(records)}건)")

    # ── [신규] 반등청산 분석 ──

    def _calc_carryover_exits(
        self, sells: List[TradeRecord], report: AnalysisReport,
    ):
        """반등청산으로 조기 매도된 건 분석"""
        for s in sells:
            if '미청산' in s.exit_type or '반등' in s.exit_type:
                report.carryover_exits.append(CarryoverExitRecord(
                    code=s.code,
                    name=s.name,
                    exit_type=s.exit_type,
                    pnl_pct=s.pnl_pct,
                    reason=s.reason,
                    strategy_type=s.strategy_type,
                ))

        # 스캘핑 포지션이 반등청산된 건 = 문제
        scalp_carryovers = [
            c for c in report.carryover_exits
            if c.strategy_type == 'scalping'
            or '스캘핑' in c.reason
        ]
        if scalp_carryovers:
            report.problems.append(
                f"스캘핑 포지션 반등청산 {len(scalp_carryovers)}건 "
                f"(BB 기준 즉시 청산 — 스캘핑 exit 로직 미적용)")

        # 소폭 수익/손실로 반등청산된 건
        small_pnl = [
            c for c in report.carryover_exits
            if -1.0 < c.pnl_pct < 1.0
        ]
        if small_pnl:
            report.recommendations.append(
                f"반등청산 {len(small_pnl)}건이 ±1% 이내 수익으로 종료 "
                f"— 홀딩 시 더 높은 수익 가능성")

    # ── [신규] 필터 차단 통계 ──

    def _calc_filter_stats(
        self,
        target_date: Optional[str],
        report: AnalysisReport,
    ):
        """로그 파일에서 사전 필터 차단 통계 추출"""
        if not target_date:
            return

        # 로그 파일 경로: ai-trade_YYYY-MM-DD.log
        date_str = target_date.replace('-', '-')
        log_file = self.log_dir / f"ai-trade_{date_str}.log"
        if not log_file.exists():
            return

        stats = FilterStats()

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if '사전 필터 차단' in line:
                        stats.total_blocked += 1
                        # 사유 추출: "사전 필터 차단: <사유>"
                        m = re.search(r'사전 필터 차단: (.+?)$', line)
                        if m:
                            raw = m.group(1).strip()
                            # 카테고리 추출 (첫 번째 ':' 또는 숫자 앞까지)
                            category = re.split(r'[:(]', raw)[0].strip()
                            stats.by_reason[category] = (
                                stats.by_reason.get(category, 0) + 1)

                    elif '분석 완료' in line and '사전 필터' in line:
                        # "사전 필터: N종목 차단, M종목 분석 완료"
                        m = re.search(r'(\d+)종목 분석 완료', line)
                        if m:
                            stats.total_passed += int(m.group(1))

                    elif '매수 실행' in line:
                        stats.total_executed += 1
        except Exception as e:
            logger.debug(f"필터 통계 로그 파싱 실패: {e}")
            return

        if stats.total_blocked > 0:
            report.filter_stats = stats

            # 차단 비율이 높은 사유 = 문제점
            top_reasons = sorted(
                stats.by_reason.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:3]
            reason_str = ", ".join(
                f"{r}({c}건)" for r, c in top_reasons)
            report.problems.append(
                f"필터 차단 {stats.total_blocked}건 "
                f"(실행 {stats.total_executed}건) — "
                f"주요: {reason_str}")

    # ── [신규] 거래소 대조 (async) ──

    async def _calc_exchange_recon(
        self,
        target_date: str,
        api,
        report: AnalysisReport,
    ):
        """거래소 ka10170/ka10076 vs trades.jsonl 대조"""
        recon = ExchangeReconciliation()
        date_compact = target_date.replace('-', '')

        try:
            journal = await api.get_trade_journal(date_compact)
            executions = await api.get_executions()
        except Exception as e:
            logger.warning(f"거래소 대조 실패: {e}")
            return

        if not journal and not executions:
            return

        # trades.jsonl 매매 요약 (종목별 합산)
        trades = self._load_trades(target_date)
        sys_summary = defaultdict(lambda: {
            'buy_qty': 0, 'buy_total': 0,
            'sell_qty': 0, 'sell_total': 0,
        })
        for t in trades:
            s = sys_summary[t.code]
            if t.action == 'BUY':
                s['buy_qty'] += t.qty
                s['buy_total'] += int(t.price * t.qty)
            else:
                s['sell_qty'] += t.qty
                s['sell_total'] += int(t.price * t.qty)

        # 거래소 vs 시스템 종목별 비교
        for item in journal:
            code = item['code']
            sys = sys_summary.get(code, {})

            # 매수 비교
            ex_buy_qty = item.get('buy_qty', 0)
            sys_buy_qty = sys.get('buy_qty', 0)
            if ex_buy_qty != sys_buy_qty:
                recon.qty_mismatches += 1
                recon.details.append(
                    f"[{code}] {item['name']} 매수수량: "
                    f"거래소 {ex_buy_qty} vs 시스템 {sys_buy_qty}")

            ex_buy_avg = item.get('buy_avg_price', 0)
            sys_buy_avg = (
                int(sys['buy_total'] / sys_buy_qty)
                if sys_buy_qty > 0 else 0)
            if sys_buy_avg > 0 and abs(ex_buy_avg - sys_buy_avg) > 50:
                recon.price_mismatches += 1
                diff = ex_buy_avg - sys_buy_avg
                recon.details.append(
                    f"[{code}] {item['name']} 매수가: "
                    f"거래소 {ex_buy_avg:,} vs 시스템 {sys_buy_avg:,} "
                    f"(차이: {diff:+,})")

        # 손익 비교
        ex_pnl = sum(item.get('pnl_amount', 0) for item in journal)
        sys_pnl_trade = trades[-1].daily_pnl_pct if trades else 0
        recon.exchange_pnl = ex_pnl

        # 시스템 일간 손익은 마지막 거래의 daily_pnl 사용
        sys_daily_pnl = 0
        for t in reversed(trades):
            if hasattr(t, 'daily_pnl_pct') and t.daily_pnl_pct != 0:
                sys_daily_pnl = t.daily_pnl_pct
                break

        recon.pnl_diff = ex_pnl  # 거래소 기준 실제 손익

        report.exchange_recon = recon

        if recon.price_mismatches > 0 or recon.qty_mismatches > 0:
            report.problems.append(
                f"거래소 대조 불일치: "
                f"가격 {recon.price_mismatches}건, "
                f"수량 {recon.qty_mismatches}건 | "
                f"거래소 손익: {recon.exchange_pnl:+,.0f}원")

    # ── 문제점/권장사항 종합 ──

    def _generate_insights(self, report: AnalysisReport):
        """문제점 식별 및 전략 권장사항 생성"""
        # 1. 동일 종목 과도 매수 체크
        for code, profile in report.stock_profiles.items():
            if profile.buy_count > 5:
                report.problems.append(
                    f"[{code}] {profile.name}: {profile.buy_count}회 반복 매수 "
                    f"(손절: {profile.stop_loss_count}회)")
            if profile.re_entries_after_sl > 2:
                report.problems.append(
                    f"[{code}] {profile.name}: 손절 후 5분 내 "
                    f"{profile.re_entries_after_sl}회 재진입")

        # 2. 승률 기반 권장사항
        if report.win_rate < 40:
            report.recommendations.append(
                f"승률 {report.win_rate:.0f}%: 진입 조건 강화 필요 "
                f"(양봉 크기, 거래량 추가 확인)")

        if report.profit_factor < 1.0:
            report.recommendations.append(
                f"손익비 {report.profit_factor:.2f}: "
                f"손절 기준(-2%) 유지하되 진입 정확도를 높여야 함")

        # 3. 종목당 최대 매수 횟수 제안
        max_buys = max(
            (p.buy_count for p in report.stock_profiles.values()),
            default=0)
        if max_buys > 3:
            report.suggested_max_entries_per_stock = 3
            report.recommendations.append(
                f"종목당 최대 매수 3회로 제한 "
                f"(현재 최대: {max_buys}회)")

        # 4. 손절 후 쿨다운 제안
        total_re_entries = sum(
            p.re_entries_after_sl
            for p in report.stock_profiles.values())
        if total_re_entries > 0:
            report.suggested_cooldown_minutes = 10
            report.recommendations.append(
                f"손절 후 10분 쿨다운 적용 권장 "
                f"(재진입 {total_re_entries}회 감지)")

        # 5. 시간대 분석 기반
        worst_times = [
            i for i in report.timing_insights
            if i.avg_pnl_pct < -1.0 and i.buy_count >= 2]
        if worst_times:
            for wt in worst_times:
                report.recommendations.append(
                    f"{wt.hour:02d}:{wt.minute_bucket:02d} 시간대 "
                    f"매수 회피 권장 "
                    f"(평균 PnL: {wt.avg_pnl_pct:+.1f}%)")

    # ── 리포트 포맷 ──

    def format_report(self, report: AnalysisReport) -> str:
        """분석 리포트를 텔레그램 HTML 메시지로 포맷"""
        lines = [
            f"<b>매매 분석 리포트 ({report.analysis_date})</b>",
            "",
            f"총 거래: {report.total_trades}회 "
            f"(매수 {report.total_buys} / 매도 {report.total_sells})",
            f"승률: {report.win_rate:.1f}% | "
            f"손익비: {report.profit_factor:.2f}",
            f"평균 이익: {report.avg_win_pnl:+.1f}% | "
            f"평균 손실: {report.avg_loss_pnl:+.1f}%",
        ]

        if report.optimal_entry_window:
            lines.append(f"\n<b>최적 진입</b>: {report.optimal_entry_window}")
        if report.worst_entry_window:
            lines.append(f"<b>최악 진입</b>: {report.worst_entry_window}")

        # [신규] 슬리피지 요약
        if report.slippage_records or report.total_slippage_amount:
            lines.append(
                f"\n<b>슬리피지</b>: {report.total_slippage_amount:+,.0f}원 "
                f"({len(report.slippage_records)}건 주요)")
            for sr in report.slippage_records[:5]:
                lines.append(
                    f"  {sr.name} {sr.action}: "
                    f"{sr.signal_price:,.0f}→{sr.fill_price:,.0f} "
                    f"({sr.slip_pct:+.1f}%, {sr.slip_amount:+,.0f}원)")

        # [신규] 필터 차단 통계
        if report.filter_stats:
            fs = report.filter_stats
            lines.append(
                f"\n<b>필터 통계</b>: "
                f"차단 {fs.total_blocked}건 / 실행 {fs.total_executed}건")
            top3 = sorted(
                fs.by_reason.items(), key=lambda x: x[1], reverse=True
            )[:3]
            for reason, count in top3:
                lines.append(f"  {reason}: {count}건")

        # [신규] 반등청산 요약
        if report.carryover_exits:
            lines.append(
                f"\n<b>반등청산</b>: {len(report.carryover_exits)}건")
            for ce in report.carryover_exits[:5]:
                lines.append(
                    f"  [{ce.code}] {ce.name}: "
                    f"{ce.pnl_pct:+.1f}% | {ce.exit_type}")

        # [신규] 거래소 대조
        if report.exchange_recon:
            er = report.exchange_recon
            lines.append(
                f"\n<b>거래소 대조</b>: "
                f"가격 불일치 {er.price_mismatches}건, "
                f"수량 불일치 {er.qty_mismatches}건")
            if er.exchange_pnl != 0:
                lines.append(
                    f"  거래소 손익: {er.exchange_pnl:+,.0f}원")
            for d in er.details[:3]:
                lines.append(f"  {d}")

        # 문제점
        if report.problems:
            lines.append("\n<b>문제점</b>")
            for p in report.problems:
                lines.append(f"  - {p}")

        # 권장사항
        if report.recommendations:
            lines.append("\n<b>권장사항</b>")
            for r in report.recommendations:
                lines.append(f"  - {r}")

        # 종목별 매매
        if report.stock_profiles:
            lines.append("\n<b>종목별 매매</b>")
            for code, p in sorted(
                report.stock_profiles.items(),
                key=lambda x: x[1].buy_count,
                reverse=True,
            )[:5]:
                lines.append(
                    f"  [{code}] {p.name}: "
                    f"매수 {p.buy_count}회 | "
                    f"손절 {p.stop_loss_count} | "
                    f"익절 {p.take_profit_count}")

        return "\n".join(lines)
