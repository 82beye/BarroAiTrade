"""
주식단테 수박지표 기반 당일 자동매매 시스템
메인 실행 파일

사용법:
    python main.py --mode simulation    # 모의투자 모드
    python main.py --mode live          # 실거래 모드 (주의!)
    python main.py --scan-only          # 스캔만 실행 (매매 없음)
    python main.py --update-cache       # 전종목 OHLCV 캐시 업데이트
    python main.py --account-status     # 계좌 현황 텔레그램 발송
    python main.py --market-status      # 시장 상태 분석 (ATR 기반)
    python main.py --leading-stocks     # 당일 주도주 실시간 순위
    python main.py --leading-stocks --top 30  # 상위 30종목
    python main.py --predict            # 팀 에이전트 상승 예측
    python main.py --predict --top 30   # 상위 30종목 예측
    python main.py --post-market        # 장 마감 후 매매 분석 + 전략 고도화
    python main.py --scalping           # 스캘핑 타이밍 팀 분석 (당일 상승 종목)
    python main.py --scalping --top 30  # 상위 30종목 스캘핑 분석
"""

import os
import sys

# Patch BAR-40: dry-run early exit (BarroAiTrade integration)
# Reference: docs/02-design/features/bar-40-monorepo-absorption.design.md §3.3
if os.environ.get("DRY_RUN") == "1":
    print("[BAR-40] DRY_RUN: import-only mode — skipping main()")
    sys.exit(0)

import atexit
import signal
import asyncio
import argparse
import logging
from datetime import datetime, time, date, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from execution.kiwoom_api import KiwoomRestAPI
from execution.order_manager import OrderManager
from execution.order_processor import OrderProcessor
from scanner.daily_screener import DailyScreener, load_watchlist
from scanner.market_condition import MarketConditionAnalyzer
from scanner.ohlcv_cache import OHLCVCache
from scanner.agents import PredictionCoordinator
from scanner.leading_stocks import LeadingStocksAnalyzer, LeadingStockResult
from strategy.scalping_team.coordinator import ScalpingCoordinator
from strategy.scalping_team.base_agent import StockSnapshot
from scanner.realtime_screener import RealtimeScreener
from strategy.entry_signal import EntrySignal, EntrySignalGenerator
from strategy.exit_signal import ExitSignalGenerator, ExitSignal, ExitType
from strategy.carryover_exit import CarryoverExitStrategy, CarryoverState
from strategy.intraday_filter import IntradayFilter
from strategy.trade_analyzer import PostTradeAnalyzer
from strategy.strategy_team import StrategyCoordinator, StrategyParams
from monitoring.telegram_bot import TelegramBot
from monitoring.notion_sync import NotionTradeSync
from monitoring.scalping_report import ScalpingDailyReport
from monitoring.scalping_pdf_report import ScalpingPDFReport


# ─────────────────────────────────────────────────────────────────────────────
# 설정 로드
# ─────────────────────────────────────────────────────────────────────────────

def load_config(mode: str = None) -> dict:
    """settings.yaml + .env 로드"""
    config_path = PROJECT_ROOT / "config" / "settings.yaml"
    env_path = PROJECT_ROOT / "config" / ".env"

    if env_path.exists():
        load_dotenv(env_path)

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    if mode:
        config['mode'] = mode

    return config


def setup_logging(config: dict):
    """로깅 설정"""
    log_config = config.get('logging', {})
    log_dir = log_config.get('dir', './logs')
    os.makedirs(log_dir, exist_ok=True)

    today_str = date.today().isoformat()
    log_file = f"{log_dir}/ai-trade_{today_str}.log"

    logging.basicConfig(
        level=getattr(logging, log_config.get('level', 'INFO')),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(),
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# KRX 거래일 체크
# ─────────────────────────────────────────────────────────────────────────────

def is_krx_trading_day() -> bool:
    """한국 거래소 거래일 여부 (간이 판정 - 주말만 체크)"""
    today = date.today()
    if today.weekday() >= 5:
        return False
    # TODO: 실제 서비스에서는 KRX 공휴일 API 연동 권장
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 스캔 전용 모드
# ─────────────────────────────────────────────────────────────────────────────

async def run_scan_only(config: dict):
    """스캔만 실행 (매매 없음) - 관심종목 + 역매공파 검색 결과 확인용"""
    logger = logging.getLogger('scan')

    api = KiwoomRestAPI(config)
    telegram = TelegramBot(config)
    screener = DailyScreener(api, config)
    ymgp_screener = RealtimeScreener(api, config)

    try:
        await api.initialize()
        await telegram.initialize()

        # 관심종목 스캔 (기존)
        watchlist = await screener.run_scan()
        await telegram.notify_scan_result([
            {
                'code': r.code, 'name': r.name,
                'blue_line_status': r.blue_line_status,
                'watermelon_signal': r.watermelon_signal,
                'score': r.score,
            }
            for r in watchlist
        ])
        logger.info(f"관심종목 스캔 완료: {len(watchlist)}종목")

        # 역매공파 검색
        ymgp_screener.set_stock_universe(screener.filtered_codes)
        ymgp_dict = await ymgp_screener.run_scan(force=True)

        if ymgp_dict:
            await telegram.notify_ymgp_scan_result(list(ymgp_dict.values()))
            logger.info(f"역매공파 검색 완료: {len(ymgp_dict)}종목 통과")
            for r in ymgp_dict.values():
                logger.info(
                    f"  [{r.code}] {r.name} | "
                    f"{r.current_price:,.0f}원 | "
                    f"점수:{r.score:.0f} | "
                    f"조건:{','.join(r.conditions_met)}"
                )
        else:
            logger.info("역매공파 검색 완료: 통과 종목 없음")
    finally:
        await api.close()
        await telegram.close()


# ─────────────────────────────────────────────────────────────────────────────
# 실시간 계좌 현황
# ─────────────────────────────────────────────────────────────────────────────

async def run_account_status(config: dict):
    """계좌 현황 조회 → 텔레그램 발송"""
    logger = logging.getLogger('account')

    api = KiwoomRestAPI(config)
    telegram = TelegramBot(config)

    try:
        await api.initialize()
        await telegram.initialize()

        balance = await api.get_balance()
        await telegram.notify_account_status(balance)

        logger.info(
            f"계좌 현황 발송 | 총자산: {balance['total_equity']:,}원 | "
            f"예수금: {balance['cash']:,}원 | "
            f"보유: {len(balance['positions'])}종목"
        )
    except Exception as e:
        logger.error(f"계좌 현황 조회 실패: {e}", exc_info=True)
    finally:
        await api.close()
        await telegram.close()


# ─────────────────────────────────────────────────────────────────────────────
# 시장 상태 분석 (단독 실행)
# ─────────────────────────────────────────────────────────────────────────────

async def run_analysis(
    config: dict,
    top_n: int = 20,
    do_predict: bool = False,
    do_leading: bool = False,
):
    """
    복합 분석 실행 — 예측/주도주를 단일 세션에서 동시 수행

    유니버스 확보(API 호출)를 1회만 하고 결과를 공유하여 효율적으로 동작.
    --predict --leading-stocks 동시 지정 시 한 번에 모두 실행.
    """
    logger = logging.getLogger('analysis')

    api = KiwoomRestAPI(config)
    telegram = TelegramBot(config)
    screener = DailyScreener(api, config)

    try:
        await api.initialize()
        await telegram.initialize()

        # ── 유니버스 1회 확보 (공유) ──
        all_codes = await screener._get_all_stock_codes()
        filtered = await screener._apply_basic_filters(all_codes)
        logger.info(f"유니버스: {len(filtered)}종목")

        # ── 1. 팀 에이전트 상승 예측 (캐시 기반) ──
        if do_predict:
            coordinator = PredictionCoordinator(config)
            pred_results = coordinator.predict(filtered, top_n=top_n)
            _print_prediction(pred_results)
            await telegram.notify_prediction(pred_results)
            logger.info(f"상승 예측 완료: {len(pred_results)}종목")

        # ── 2. 당일 주도주 실시간 순위 (API 현재가 조회) ──
        if do_leading:
            analyzer = LeadingStocksAnalyzer(api, config)
            lead_results = await analyzer.analyze(filtered, top_n=top_n)
            _print_leading_stocks(lead_results)
            await telegram.notify_leading_stocks(lead_results)
            logger.info(f"주도주 순위 완료: {len(lead_results)}종목")

    except Exception as e:
        logger.error(f"분석 실패: {e}", exc_info=True)
    finally:
        await api.close()
        await telegram.close()


def _print_prediction(results):
    """팀 에이전트 예측 결과 콘솔 출력"""
    print(f"\n{'='*80}")
    print(f"  팀 에이전트 상승 예측 (상위 {len(results)}종목)")
    print(f"  에이전트: momentum(30%) | volume(25%) | technical(25%) | breakout(20%)")
    print(f"{'='*80}")
    print(
        f"{'순위':>4} {'종목코드':<8} {'종목명':<12} {'점수':>6} "
        f"{'신뢰도':>6} {'합의':>8} {'MOM':>5} {'VOL':>5} "
        f"{'TEC':>5} {'BRK':>5}  핵심 사유"
    )
    print(f"{'─'*80}")

    for r in results:
        mom = r.agent_scores.get('momentum', 0)
        vol = r.agent_scores.get('volume', 0)
        tec = r.agent_scores.get('technical', 0)
        brk = r.agent_scores.get('breakout', 0)
        reason = r.top_reasons[0] if r.top_reasons else ""

        print(
            f"{r.rank:>4} [{r.code}] {r.name:<10} "
            f"{r.total_score:>6.1f} "
            f"{r.confidence:>5.0%} "
            f"{r.consensus_level:>8} "
            f"{mom:>5.0f} {vol:>5.0f} {tec:>5.0f} {brk:>5.0f}"
            f"  {reason}"
        )

    if results:
        consensus_counts = {}
        for r in results:
            consensus_counts[r.consensus_level] = (
                consensus_counts.get(r.consensus_level, 0) + 1
            )
        print(f"{'─'*80}")
        stats = " | ".join(f"{k}: {v}종목" for k, v in consensus_counts.items())
        print(f"  합의 통계: {stats}")
        avg_score = sum(r.total_score for r in results) / len(results)
        avg_conf = sum(r.confidence for r in results) / len(results)
        print(f"  평균 점수: {avg_score:.1f} | 평균 신뢰도: {avg_conf:.0%}")
    print(f"{'='*80}")


def _print_leading_stocks(results):
    """주도주 순위 콘솔 출력"""
    print(f"\n{'='*70}")
    print(f"  당일 주도주 실시간 순위 (상위 {len(results)}종목)")
    print(f"{'='*70}")
    print(f"{'순위':>4} {'종목코드':<8} {'종목명':<12} {'현재가':>10} "
          f"{'등락률':>8} {'시가대비':>8} {'거래대금':>10} "
          f"{'거래량배수':>8} {'점수':>6} {'유형':<8}")
    print(f"{'─'*70}")

    for r in results:
        tv_billions = r.trade_value / 1_000_000_000
        print(
            f"{r.rank:>4} [{r.code}] {r.name:<10} "
            f"{r.current_price:>10,} "
            f"{r.change_pct:>+7.1f}% "
            f"{r.open_to_cur_pct:>+7.1f}% "
            f"{tv_billions:>8.0f}억 "
            f"x{r.volume_ratio:>6.1f} "
            f"{r.score:>6.1f} "
            f"{r.category}"
        )

    if results:
        avg_change = sum(r.change_pct for r in results) / len(results)
        total_tv = sum(r.trade_value for r in results) / 1_000_000_000
        print(f"{'─'*70}")
        print(f"  평균 상승률: {avg_change:+.1f}% | "
              f"총 거래대금: {total_tv:,.0f}억")
    print(f"{'='*70}\n")


async def run_market_status(config: dict):
    """시장 상태 분석 → 콘솔 + 텔레그램 발송"""
    logger = logging.getLogger('market')

    api = KiwoomRestAPI(config)
    telegram = TelegramBot(config)
    analyzer = MarketConditionAnalyzer(config)

    try:
        await api.initialize()
        await telegram.initialize()

        condition = await analyzer.analyze(api)

        # 콘솔 출력
        print(f"\n{'='*50}")
        print(f"  시장 상태: {condition.overall_level.value}")
        print(f"{'='*50}")
        for idx in [condition.kospi, condition.kosdaq]:
            if idx is None:
                continue
            ma_arrow = "▼" if idx.below_ma20 else "▲"
            print(f"  {idx.name}: {idx.close:,.1f} ({idx.daily_change_pct:+.1f}%)")
            print(f"    MA20: {idx.ma20:,.1f} {ma_arrow}")
            print(f"    ATR: {idx.atr_short:.1f}/{idx.atr_long:.1f} = {idx.atr_ratio:.2f} → {idx.level.value}")
        print(f"{'─'*50}")
        print(f"  매수허용: {'예' if condition.entry_allowed else '아니오'}")
        print(f"  포지션배율: x{condition.position_size_multiplier}")
        if condition.stop_loss_override is not None:
            print(f"  손절 오버라이드: {condition.stop_loss_override}%")
        print(f"{'='*50}\n")

        # 텔레그램 발송
        await telegram.notify_market_condition(condition)

        logger.info(f"시장 상태 분석 완료: {condition.overall_level.value}")
    except Exception as e:
        logger.error(f"시장 상태 분석 실패: {e}", exc_info=True)
    finally:
        await api.close()
        await telegram.close()


# ─────────────────────────────────────────────────────────────────────────────
# OHLCV 캐시 업데이트
# ─────────────────────────────────────────────────────────────────────────────

async def run_update_cache(config: dict):
    """전종목 일봉 데이터를 캐시에 저장 (장 마감 후 실행)"""
    logger = logging.getLogger('cache')

    api = KiwoomRestAPI(config)
    telegram = TelegramBot(config)
    screener = DailyScreener(api, config)

    cache_dir = config.get('scanner', {}).get('cache_dir', './data/ohlcv_cache')
    cache = OHLCVCache(cache_dir)

    try:
        await api.initialize()
        await telegram.initialize()

        # 전종목 코드 수집
        logger.info("전종목 코드 수집 시작...")
        all_codes = await screener._get_all_stock_codes()
        logger.info(f"전체 종목 수: {len(all_codes)}")

        # 기본 필터링 (ETF/관리종목 등 제외)
        filtered = await screener._apply_basic_filters(all_codes)
        logger.info(f"필터 통과 종목: {len(filtered)}")

        # 캐시 업데이트
        ohlcv_count = config.get('scanner', {}).get('ohlcv_count', 500)
        meta = await cache.update_all(api, filtered, ohlcv_count=ohlcv_count)

        # 텔레그램 알림
        elapsed_min = meta.get('elapsed_seconds', 0) / 60
        depth = meta.get('depth') or {}
        depth_avg = depth.get('avg', 0)
        depth_min = depth.get('min', 0)
        depth_max = depth.get('max', 0)
        over_448 = depth.get('stocks_over_448', 0)
        total_stocks = depth.get('total_stocks', 0)

        msg_lines = [
            f"<b>OHLCV 캐시 업데이트 완료</b>",
            f"날짜: {meta['updated']}",
            f"API: {meta.get('api_method', 'ka10005')} ({meta.get('api_calls', 0)}호출)",
            f"성공: {meta['count']}/{meta['total_requested']}종목"
            f" (스킵: {meta.get('skipped', 0)})",
            f"실패: {meta['failed']}종목",
            f"소요: {elapsed_min:.1f}분",
        ]
        if depth:
            msg_lines.append(
                f"깊이: 평균{depth_avg:.0f}일 "
                f"(최소{depth_min} 최대{depth_max})"
            )
            msg_lines.append(
                f"448일 충족: {over_448}/{total_stocks}종목"
            )

        await telegram.send_message("\n".join(msg_lines))

        logger.info("캐시 업데이트 완료")
    except Exception as e:
        logger.error(f"캐시 업데이트 실패: {e}", exc_info=True)
        try:
            await telegram.notify_error(f"캐시 업데이트 실패: {str(e)[:200]}")
        except Exception:
            pass
    finally:
        await api.close()
        await telegram.close()


# ─────────────────────────────────────────────────────────────────────────────
# 스캘핑 타이밍 팀 에이전트 분석 (--scalping)
# ─────────────────────────────────────────────────────────────────────────────


async def run_scalping_analysis(config: dict, top_n: int = 20):
    """
    당일 상승 종목 스캘핑 타이밍 분석

    1. 주도주 분석으로 상승 종목 확보
    2. 10명의 스캘핑 전문가 에이전트가 각 종목 분석
    3. 종합 점수/타이밍/파라미터 텔레그램 발송
    """
    logger = logging.getLogger('scalping')

    api = KiwoomRestAPI(config)
    telegram = TelegramBot(config)
    screener = DailyScreener(api, config)

    try:
        await api.initialize()
        await telegram.initialize()

        # ── 1. 주도주 상승 종목 확보 ──
        all_codes = await screener._get_all_stock_codes()
        filtered = await screener._apply_basic_filters(all_codes)

        analyzer = LeadingStocksAnalyzer(api, config)
        lead_results = await analyzer.analyze(filtered, top_n=top_n)

        if not lead_results:
            logger.warning("상승 종목 없음 — 스캘핑 분석 스킵")
            return

        logger.info(f"상승 종목 {len(lead_results)}개 확보")

        # ── 2. LeadingStockResult → StockSnapshot 변환 ──
        snapshots = []
        for r in lead_results:
            # 현재가 상세 조회 (시가/고가/저가 포함)
            try:
                price_data = await api.get_current_price(r.code)
            except Exception:
                price_data = {}

            snapshots.append(StockSnapshot(
                code=r.code,
                name=r.name,
                price=price_data.get('price', r.current_price),
                open=price_data.get('open', r.current_price),
                high=price_data.get('high', r.current_price),
                low=price_data.get('low', r.current_price),
                prev_close=r.prev_close,
                volume=price_data.get('volume', 0),
                change_pct=r.change_pct,
                trade_value=r.trade_value,
                volume_ratio=r.volume_ratio,
                category=r.category,
                score=r.score,
            ))

        # ── 3. 스캘핑 팀 분석 ──
        coordinator = ScalpingCoordinator(config)
        results = coordinator.analyze(snapshots)

        # ── 4. 콘솔 출력 ──
        _print_scalping_results(results)

        # ── 5. 텔레그램 발송 ──
        report = coordinator.format_report(results, top_n=min(top_n, 10))
        await telegram.send_message(report, parse_mode='HTML')

        # 상위 3종목 상세 분석
        for r in results[:3]:
            detail = coordinator.format_detail(r)
            await telegram.send_message(detail, parse_mode='HTML')

        logger.info(f"스캘핑 분석 완료: {len(results)}종목")

    except Exception as e:
        logger.error(f"스캘핑 분석 실패: {e}", exc_info=True)
    finally:
        await api.close()
        await telegram.close()


def _print_scalping_results(results):
    """스캘핑 분석 결과 콘솔 출력"""
    timing_icon = {'즉시': '🟢', '대기': '🟡', '눌림목대기': '🔵', '관망': '🔴'}

    print(f"\n{'='*90}")
    print(f"  스캘핑 타이밍 팀 분석 (10명 전문가 × {len(results)}종목)")
    print(f"{'='*90}")
    print(
        f"{'순위':>4} {'종목코드':<8} {'종목명':<12} {'현재가':>10} "
        f"{'등락률':>7} {'점수':>6} {'타이밍':<6} {'합의':<8} "
        f"{'TP':>5} {'SL':>6} {'보유':>4}  진입 근거"
    )
    print(f"{'─'*90}")

    for r in results:
        icon = timing_icon.get(r.timing, '⚪')
        price = r.snapshot.price if r.snapshot else 0
        change = r.snapshot.change_pct if r.snapshot else 0
        reason = r.top_reasons[0] if r.top_reasons else ""
        # 에이전트명 제거하여 간결하게
        if reason.startswith('[') and ']' in reason:
            reason = reason[reason.index(']') + 2:]

        print(
            f"{r.rank:>4} [{r.code}] {r.name:<10} "
            f"{price:>10,} "
            f"{change:>+6.1f}% "
            f"{r.total_score:>5.0f} "
            f"{icon}{r.timing:<5} "
            f"{r.consensus_level:<8} "
            f"+{r.scalp_tp_pct:>4.1f}% "
            f"{r.scalp_sl_pct:>5.1f}% "
            f"{r.hold_minutes:>3}분"
            f"  {reason}"
        )

    if results:
        print(f"{'─'*90}")
        entry_count = sum(1 for r in results if r.timing == "즉시")
        wait_count = sum(1 for r in results if r.timing in ("대기", "눌림목대기"))
        avoid_count = sum(1 for r in results if r.timing == "관망")
        avg_score = sum(r.total_score for r in results) / len(results)
        print(
            f"  즉시진입: {entry_count} | 대기: {wait_count} | "
            f"관망: {avoid_count} | 평균점수: {avg_score:.0f}")
    print(f"{'='*90}")


# ─────────────────────────────────────────────────────────────────────────────
# 장 마감 후 매매 분석 + 전략 고도화 (--post-market)
# ─────────────────────────────────────────────────────────────────────────────

async def run_post_market(config: dict):
    """
    장 마감 후 당일 매매 분석 + 전략 팀 에이전트 고도화

    1. 계좌 현황 조회 → 텔레그램
    2. 당일 매매 분석 (PostTradeAnalyzer) → 텔레그램
    3. 전략 팀 에이전트 최적화 (StrategyCoordinator) → 텔레그램
       - 5인 팀이 최근 5일 매매를 분석하여 내일 전략 파라미터 도출
    4. Notion 매매 캘린더 동기화
    5. 종합 리포트 발송
    """
    logger = logging.getLogger('post_market')
    today_str = date.today().isoformat()

    api = KiwoomRestAPI(config)
    telegram = TelegramBot(config)
    trade_analyzer = PostTradeAnalyzer(config)
    strategy_coordinator = StrategyCoordinator(config)
    notion = NotionTradeSync(config)

    try:
        await api.initialize()
        await telegram.initialize()
        await notion.initialize()

        await telegram.send_message(
            f"<b>장 마감 후 분석 시작</b> ({today_str})")

        # ── 1. 계좌 현황 ──
        try:
            balance = await api.get_balance()
            await telegram.notify_account_status(balance)
            total_equity = balance.get('total_equity', 0)
            positions = balance.get('positions', [])
            logger.info(
                f"계좌 현황: 총자산 {total_equity:,}원 | "
                f"보유 {len(positions)}종목")
        except Exception as e:
            logger.error(f"계좌 현황 조회 실패: {e}")

        # ── 2. 당일 매매 분석 ──
        try:
            report = trade_analyzer.analyze(target_date=today_str)
            if report.total_trades > 0:
                report_msg = trade_analyzer.format_report(report)
                await telegram.send_message(report_msg)
                logger.info(
                    f"당일 매매 분석: {report.total_trades}건 | "
                    f"승률 {report.win_rate:.1f}% | "
                    f"손익비 {report.profit_factor:.2f}")

                # 콘솔 요약 출력
                print(f"\n{'='*60}")
                print(f"  당일 매매 분석 ({today_str})")
                print(f"{'='*60}")
                print(f"  총 거래: {report.total_trades}건")
                print(f"  승률: {report.win_rate:.1f}%")
                print(f"  손익비: {report.profit_factor:.2f}")
                print(f"  평균 이익: {report.avg_win_pnl:+.1f}%")
                print(f"  평균 손실: {report.avg_loss_pnl:+.1f}%")
                if report.problems:
                    print(f"\n  [문제점]")
                    for p in report.problems:
                        print(f"    - {p}")
                if report.recommendations:
                    print(f"\n  [권장사항]")
                    for r in report.recommendations:
                        print(f"    - {r}")
                print(f"{'='*60}\n")
            else:
                await telegram.send_message(
                    f"<b>매매 분석</b> ({today_str})\n매매 기록 없음")
                logger.info("당일 매매 기록 없음")
        except Exception as e:
            logger.error(f"매매 분석 실패: {e}", exc_info=True)

        # ── 3. 전략 팀 에이전트 고도화 ──
        try:
            # 내일 적용할 전략 파라미터 도출
            strategy_params = strategy_coordinator.optimize(watchlist=[])
            strategy_report = strategy_coordinator.format_report(
                strategy_params)
            await telegram.send_message(strategy_report)

            # 전략 파라미터 저장 (다음 거래일 자동 로드)
            strategy_coordinator.save_params(strategy_params)

            logger.info(
                f"전략 고도화 완료 | 신뢰도: {strategy_params.confidence:.0%}")

            # 콘솔 요약 출력
            print(f"\n{'='*60}")
            print(f"  전략 팀 에이전트 고도화 결과")
            print(f"{'='*60}")
            print(f"  신뢰도: {strategy_params.confidence:.0%}")
            print(f"  진입 시작: 09:{strategy_params.entry_start_delay_minutes:02d}")
            print(f"  쿨다운: {strategy_params.cooldown_minutes}분")
            print(f"  종목당 한도: {strategy_params.max_entries_per_stock}회")
            print(f"  손절: {strategy_params.stop_loss_pct}%")
            print(f"  익절: +{strategy_params.take_profit_1_pct}% / "
                  f"+{strategy_params.take_profit_2_pct}%")
            print(f"  포지션 배율: {strategy_params.position_size_multiplier:.0%}")
            if strategy_params.blacklist_codes:
                print(f"  블랙리스트: {', '.join(strategy_params.blacklist_codes)}")
            if strategy_params.agent_reports:
                print(f"\n  [에이전트 분석]")
                for ar in strategy_params.agent_reports:
                    print(f"    - {ar}")
            print(f"{'='*60}\n")

        except Exception as e:
            logger.error(f"전략 고도화 실패: {e}", exc_info=True)

        # ── 4. Notion 매매 캘린더 동기화 ──
        try:
            await notion.sync_date(today_str)
            await notion.sync_month(today_str[:7])
            logger.info(f"Notion 매매 캘린더 동기화 완료: {today_str} (월간 요약 포함)")
        except Exception as e:
            logger.error(f"Notion 동기화 실패: {e}")

        # ── 5. 종합 완료 알림 ──
        await telegram.send_message(
            f"<b>장 마감 후 분석 완료</b> ({today_str})\n"
            f"내일 전략 파라미터가 자동 반영됩니다.")
        logger.info("장 마감 후 분석 완료")

    except Exception as e:
        logger.error(f"장 마감 후 분석 오류: {e}", exc_info=True)
        try:
            await telegram.notify_error(
                f"장 마감 후 분석 실패: {str(e)[:200]}")
        except Exception:
            pass
    finally:
        await api.close()
        await telegram.close()
        await notion.close()


# ─────────────────────────────────────────────────────────────────────────────
# 메인 트레이딩 루프
# ─────────────────────────────────────────────────────────────────────────────

async def run_trading_loop(config: dict):
    """
    메인 매매 루프 — 독립 async 태스크 구조

    아키텍처:
      OrderProcessor (큐 기반 단일 주문 실행자)
        ← exit_monitor_task   (매도 감시, 2초 주기)
        ← entry_monitor_task  (매수 감시, 3초 주기)
        ← sync_task           (잔고 동기화 + 텔레그램 알림)
        ← market_task         (시장 상태 갱신, 30분 주기)
        ← rescan_task         (역매공파 재스캔)

    타임라인:
      08:30  Pre-Market 스캔 → 관심종목 (텔레그램 표시)
      09:00  장 시작 → 초기 역매공파 스캔 → 태스크 시작
      09:05~ 매수 가능 시작 (역매공파 결과 기반)
      14:30  신규 매수 마감
      14:50  강제 청산 → 태스크 종료
      15:10  일일 리포트 발송
    """
    logger = logging.getLogger('main')

    logger.info("=" * 70)
    logger.info(f"주식단테 데이트레이딩 시스템 시작 (모드: {config['mode']})")
    logger.info("=" * 70)

    # ─── 장 시간 체크 ───
    now_time = datetime.now().time()
    if now_time >= time(15, 30):
        logger.warning("장 마감 후 실행 — 매매 불가. --scan-only 또는 --update-cache 사용 권장.")
        return
    if now_time >= time(14, 50):
        logger.warning("14:50 이후 실행 — 신규 매매 불가. 종료합니다.")
        return

    if config['mode'] == 'live':
        logger.warning("실거래 모드! 실제 자금이 투입됩니다!")
        logger.warning("5초 내 Ctrl+C 로 취소 가능...")
        await asyncio.sleep(5)

    # ─── 컴포넌트 초기화 ───
    api = KiwoomRestAPI(config)
    processor = OrderProcessor(api, config)
    entry_gen = EntrySignalGenerator(config)
    exit_gen = ExitSignalGenerator(config)
    telegram = TelegramBot(config)
    screener = DailyScreener(api, config)
    market_analyzer = MarketConditionAnalyzer(config)
    ymgp_screener = RealtimeScreener(api, config)

    # 태스크 간 공유 상태 (asyncio는 단일 스레드 → lock 불필요)
    shared = _SharedState()
    intraday_filter = IntradayFilter(config)
    shared.intraday_filter = intraday_filter

    # 전략 최적화 팀 에이전트 + 매매 분석기
    strategy_coordinator = StrategyCoordinator(config)
    trade_analyzer = PostTradeAnalyzer(config)
    notion = NotionTradeSync(config)

    try:
        await api.initialize()
        await processor.initialize()
        await telegram.initialize()
        await notion.initialize()

        # ─── Pre-Market 스캔 ───
        logger.info("Pre-Market 종목 스캔 시작...")
        watchlist = await screener.run_scan()
        shared.watchlist = watchlist  # 장중 매수 감시용 저장
        shared.filtered_codes = screener.filtered_codes  # 스캘핑 분석용
        await telegram.notify_scan_result([
            {
                'code': r.code, 'name': r.name,
                'blue_line_status': r.blue_line_status,
                'watermelon_signal': r.watermelon_signal,
                'score': r.score,
            }
            for r in watchlist
        ])

        # ─── 팀 에이전트 상승 예측 ───
        try:
            coordinator = PredictionCoordinator(config)
            pred_results = coordinator.predict(screener.filtered_codes, top_n=50)
            shared.prediction_scores = {
                r.code: r.total_score for r in pred_results}
            if pred_results:
                await telegram.notify_prediction(pred_results[:20])
                logger.info(f"팀 에이전트 예측 완료: {len(pred_results)}종목")
            else:
                logger.info("팀 에이전트 예측: 유효 종목 없음")
        except Exception as e:
            logger.error(f"팀 에이전트 예측 실패 (무시): {e}")

        # ─── 역매공파 유니버스 설정 ───
        ymgp_screener.set_stock_universe(screener.filtered_codes)

        # ─── 전략 최적화 팀 에이전트 ───
        try:
            strategy_params = strategy_coordinator.optimize(
                watchlist=[
                    {'code': r.code, 'name': r.name}
                    for r in watchlist
                ])
            shared.strategy_params = strategy_params

            # 팀 결과를 IntradayFilter에 반영
            intraday_filter.cooldown_minutes = strategy_params.cooldown_minutes
            intraday_filter.max_entries_per_stock = (
                strategy_params.max_entries_per_stock)
            intraday_filter.max_bb_excess_pct = (
                strategy_params.max_bb_excess_pct)

            # 팀 결과를 ExitSignalGenerator에 반영
            exit_gen.sl_pct = strategy_params.stop_loss_pct
            exit_gen.tp1_pct = strategy_params.take_profit_1_pct
            exit_gen.tp2_pct = strategy_params.take_profit_2_pct
            exit_gen.breakeven_buffer_pct = strategy_params.breakeven_buffer_pct

            # 팀 결과를 EntrySignalGenerator에 반영 (돌파 상한)
            entry_gen.max_breakout_pct = strategy_params.max_breakout_pct

            # 팀 결과를 EntrySignalGenerator에 반영 (포지션 사이징)
            if strategy_params.position_size_multiplier < 1.0:
                original_pct = entry_gen.max_per_stock_pct
                entry_gen.max_per_stock_pct = round(
                    original_pct * strategy_params.position_size_multiplier, 1)
                logger.info(
                    f"포지션 사이징 조정: "
                    f"{original_pct}% → {entry_gen.max_per_stock_pct}% "
                    f"(배율 {strategy_params.position_size_multiplier:.0%})")

            # 텔레그램 발송
            await telegram.send_message(
                strategy_coordinator.format_report(strategy_params))
            logger.info(
                f"전략 팀 분석 완료: 신뢰도 {strategy_params.confidence:.0%}")
        except Exception as e:
            logger.error(f"전략 팀 분석 실패: {e}")
            # 전일 post-market에서 저장한 파라미터 로드 시도
            saved_params = StrategyCoordinator.load_params()
            if saved_params:
                strategy_params = saved_params
                logger.info(
                    f"전일 저장 파라미터 로드 (신뢰도: "
                    f"{saved_params.confidence:.0%})")
            else:
                strategy_params = StrategyParams()
                logger.info("기본 파라미터 사용")
            shared.strategy_params = strategy_params

        # ─── 전일 매매 분석 리포트 (거래소 대조 포함) ───
        try:
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            report = await trade_analyzer.analyze_with_exchange(
                target_date=yesterday, api=api)
            if report.total_trades > 0:
                report_msg = trade_analyzer.format_report(report)
                await telegram.send_message(report_msg)
        except Exception as e:
            logger.error(f"매매 분석 실패 (무시): {e}")

        # ─── 시장 상태 분석 ───
        shared.market_condition = await market_analyzer.analyze(api)
        await telegram.notify_market_condition(shared.market_condition)

        # ─── 장 시작 대기 ───
        while datetime.now().time() < time(9, 0):
            logger.debug("장 시작 대기 중...")
            await asyncio.sleep(10)

        # ─── 미청산 포지션 초기화 ───
        carryover_strategy = CarryoverExitStrategy(config)
        carryover_states = {}  # {code: CarryoverState}

        for code, pos in processor.positions.items():
            if pos.is_carryover:
                # 스캘핑 포지션은 반등청산 대신 스캘핑 exit 로직 유지
                if pos.strategy_type == 'scalping':
                    pos.is_carryover = False  # 일반 exit_monitor에서 관리
                    logger.warning(
                        f"스캘핑 미청산 → exit_monitor 유지: [{code}] "
                        f"{pos.name} {pos.qty}주 @ "
                        f"{pos.entry_price:,.0f} | "
                        f"TP: +{pos.scalp_tp_pct}% SL: {pos.scalp_sl_pct}%")
                    continue

                try:
                    price_data = await api.get_current_price(code)
                    open_price = price_data['open']
                except Exception:
                    open_price = pos.current_price

                # BB(20) 계산을 위한 일봉 조회
                bb20_mid, bb20_upper = 0.0, 0.0
                try:
                    df = await api.get_daily_ohlcv(code, count=25)
                    if df is not None and len(df) >= 20:
                        closes = df['close'].tail(20)
                        bb20_mid = closes.mean()
                        bb20_upper = bb20_mid + 2 * closes.std()
                except Exception as e:
                    logger.error(f"미청산 BB 조회 실패 [{code}]: {e}")

                state = CarryoverState(
                    code=code,
                    name=pos.name,
                    entry_price=pos.entry_price,
                    qty=pos.qty,
                    open_price=open_price,
                    bb20_mid=bb20_mid,
                    bb20_upper=bb20_upper,
                )
                carryover_states[code] = state

                pnl_from_entry = (
                    (pos.current_price - pos.entry_price)
                    / pos.entry_price * 100
                    if pos.entry_price > 0 else 0)
                logger.warning(
                    f"미청산 관리 등록: [{code}] {pos.name} | "
                    f"매수가: {pos.entry_price:,.0f} | "
                    f"시가: {open_price:,} | "
                    f"손익: {pnl_from_entry:+.1f}% | "
                    f"BB20 중앙: {bb20_mid:,.0f} | "
                    f"BB20 상단: {bb20_upper:,.0f}")

        if carryover_states:
            await telegram.send_message(
                "<b>미청산 포지션 %d종목 감지</b>\n"
                "전략: 반등 분할 청산 + 확대 손절\n"
                "- BB(20) 중앙선 → 50%% 매도\n"
                "- BB(20) 상단선 → 전량 매도\n"
                "- 시가 대비 %.0f%% 추가 하락 → 전량 손절"
                % (len(carryover_states),
                   carryover_strategy.additional_sl_pct))

        shared.carryover_states = carryover_states

        # ─── 일반 매매(역매공파/파란점선) 활성화 여부 ───
        # TODO: 파라미터 정상화(lookback 224) + TP/SL 재설계 + 백테스트 후 재활성화
        regular_trading_enabled = config.get(
            'strategy', {}).get('regular_trading_enabled', False)

        if regular_trading_enabled:
            # 초기 역매공파 스캔
            logger.info("장 시작 - 초기 역매공파 스캔 실행")
            ymgp_dict = await ymgp_screener.run_scan(force=True)
            if ymgp_dict:
                shared.ymgp_dict = ymgp_dict
                await telegram.notify_ymgp_scan_result(
                    list(ymgp_dict.values()))
                logger.info(
                    f"역매공파 초기 스캔: {len(ymgp_dict)}종목 통과")
            else:
                logger.info("역매공파 초기 스캔: 통과 종목 없음")
        else:
            logger.info(
                "일반 매매(역매공파/파란점선) 비활성화 — "
                "스캘핑 전략 전용 모드")

        # ─── 독립 태스크 시작 ───
        logger.info("장중 매매 — 독립 태스크 구조 진입")

        # OrderProcessor 큐 루프
        processor_task = asyncio.create_task(
            processor.run(), name="order_processor")

        # 매도 감시 (2초 주기 — 매수보다 빠르게, 기존 포지션 청산용으로 항상 필요)
        exit_task = asyncio.create_task(
            _exit_monitor(
                processor, api, exit_gen, telegram, shared),
            name="exit_monitor")

        # 잔고 동기화 + 텔레그램 포지션 알림
        pos_interval = config.get('telegram', {}).get(
            'notifications', {}).get('position_update_interval', 5) * 60
        sync_task = asyncio.create_task(
            _sync_loop(processor, telegram, pos_interval, shared),
            name="sync_loop")

        # 시장 상태 갱신 (30분 주기)
        market_task = asyncio.create_task(
            _market_condition_loop(
                market_analyzer, api, telegram, shared),
            name="market_condition")

        # 스캘핑 매수 감시 (45초 주기)
        scalping_task = asyncio.create_task(
            _scalping_entry_monitor(
                processor, api, entry_gen, telegram, shared, config),
            name="scalping_entry_monitor")

        worker_tasks = [
            exit_task, sync_task, market_task, scalping_task]

        # 일반 매매 태스크 (활성화 시에만)
        if regular_trading_enabled:
            # 매수 감시 — 역매공파 (3초 주기)
            entry_task = asyncio.create_task(
                _entry_monitor(
                    processor, api, entry_gen, telegram, shared),
                name="entry_monitor")

            # 매수 감시 — 파란점선 감시 리스트 (5초 주기)
            wl_entry_task = asyncio.create_task(
                _watchlist_entry_monitor(
                    processor, api, entry_gen, telegram, shared),
                name="watchlist_entry_monitor")

            # 역매공파 재스캔 (주기 + 포지션 종료 시)
            rescan_task = asyncio.create_task(
                _rescan_loop(
                    ymgp_screener, telegram, processor, shared),
                name="rescan_loop")

            worker_tasks.extend([
                entry_task, wl_entry_task, rescan_task])

        # 미청산 포지션 전용 감시 (있을 때만)
        if carryover_states:
            carryover_task = asyncio.create_task(
                _carryover_monitor(
                    processor, api, carryover_strategy,
                    telegram, shared),
                name="carryover_monitor")
            worker_tasks.append(carryover_task)

        # ─── 14:50 강제 청산 대기 ───
        while datetime.now().time() < time(14, 50):
            await asyncio.sleep(5)

        # ─── 태스크 종료 ───
        logger.warning("14:50 - 매매 태스크 종료 시작")
        shared.shutdown = True

        # 워커 태스크 취소 및 대기
        for t in worker_tasks:
            t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)

        # 강제 청산 (프로세서를 통해)
        logger.warning("강제 청산 실행")
        count = await processor.submit_force_liquidate()
        await telegram.notify_force_liquidation(count)

        # 프로세서 종료
        await processor.stop()
        await asyncio.gather(processor_task, return_exceptions=True)

        # ─── 일일 리포트 ───
        logger.info("장 마감 - 체결 완료 대기 60초...")
        await asyncio.sleep(60)
        await processor.sync_positions()

        summary = processor.get_daily_summary()
        await telegram.notify_daily_report(summary)

        logger.info(
            f"일일 결산 | 손익: {summary['daily_pnl']:+,.0f}원 "
            f"({summary['daily_pnl_pct']:+.2f}%) | "
            f"거래: {summary['total_trades']}회"
        )

        # ─── 스캘핑 타이밍 팀 일일 리포트 ───
        try:
            scalp_reporter = ScalpingDailyReport(config)
            today_str = date.today().isoformat()
            scalp_report = scalp_reporter.generate(today_str)

            # 텔레그램 발송
            scalp_text = scalp_reporter.format_telegram(scalp_report)
            await telegram.send_message(scalp_text)

            # PDF 보고서 생성
            pdf_gen = ScalpingPDFReport(config)
            pdf_path = pdf_gen.generate_pdf(scalp_report)
            await telegram.send_document(pdf_path)

            logger.info(
                f"스캘핑 리포트 생성 완료: "
                f"{scalp_report.get('performance', {}).get('total_pairs', 0)}건 분석 "
                f"| PDF: {pdf_path}")
        except Exception as e:
            logger.error(f"스캘핑 리포트 생성 실패: {e}")

        # ─── Notion 매매일지 동기화 ───
        try:
            today_str = date.today().isoformat()
            await notion.sync_date(today_str)
            await notion.sync_month(today_str[:7])
            logger.info(f"Notion 매매일지 동기화 완료: {today_str}")
        except Exception as e:
            logger.error(f"Notion 동기화 실패: {e}")

    except KeyboardInterrupt:
        logger.info("사용자 중단 - 포지션 청산 중...")
        shared.shutdown = True
        try:
            await processor.submit_force_liquidate()
        except Exception:
            logger.critical("긴급 청산 실패!")

    except Exception as e:
        logger.critical(f"치명적 오류: {e}", exc_info=True)
        await telegram.notify_error(f"치명적 오류: {str(e)[:200]}")

        shared.shutdown = True
        try:
            await processor.submit_force_liquidate()
            logger.warning("긴급 청산 완료")
        except Exception:
            logger.critical("긴급 청산도 실패! 수동 확인 필요!")

    finally:
        await processor.stop()
        await notion.close()
        await api.close()
        await telegram.close()
        logger.info("시스템 종료 완료")


# ─────────────────────────────────────────────────────────────────────────────
# 공유 상태 (태스크 간 데이터 교환)
# ─────────────────────────────────────────────────────────────────────────────

class _SharedState:
    """태스크 간 공유 상태. asyncio 단일 스레드이므로 lock 불필요."""

    def __init__(self):
        self.market_condition = None        # MarketCondition
        self.ymgp_dict: dict = {}           # {code: YmgpResult}
        self.watchlist: list = []           # [IndicatorResult, ...] 파란점선 감시 리스트
        self.prediction_scores: dict = {}   # {code: float}
        self.carryover_states: dict = {}    # {code: CarryoverState}
        self.intraday_filter: IntradayFilter = None
        self.strategy_params: StrategyParams = None
        self.shutdown: bool = False
        self.prev_position_count: int = 0
        self.rescan_needed: bool = False
        self.filtered_codes: list = []     # 기본 필터 통과 종목 (스캘핑용)
        self.scalp_coordinator: 'ScalpingCoordinator' = None  # 스캘핑 coordinator 공유


# ─────────────────────────────────────────────────────────────────────────────
# 매도 감시 태스크 (스캘핑 1초 / 일반 2초)
# ─────────────────────────────────────────────────────────────────────────────

async def _exit_monitor(
    processor: OrderProcessor,
    api: KiwoomRestAPI,
    exit_gen: ExitSignalGenerator,
    telegram: TelegramBot,
    shared: _SharedState,
):
    """보유 종목 매도 조건 감시 — 매수와 독립적으로 빠르게 반응"""
    logger = logging.getLogger('exit_monitor')

    while not shared.shutdown:
        try:
            positions = processor.get_positions_dict()
            for code, pos in list(positions.items()):
                if shared.shutdown:
                    break
                # 미청산 포지션은 전용 태스크(_carryover_monitor)에서 관리
                if pos.get('is_carryover', False):
                    continue
                try:
                    price_data = await api.get_current_price(code)

                    # 스캘핑 vs 일반 전략 분기
                    if pos.get('strategy_type') == 'scalping':
                        cur_price = price_data['price']
                        # 트레일링 스톱: 고점 업데이트 + 활성화 체크
                        processor.update_scalp_high_watermark(code, cur_price)
                        ep = pos['entry_price']
                        if ep > 0:
                            cur_pnl = (cur_price - ep) / ep * 100
                            if (cur_pnl >= exit_gen.trailing_activation_pct
                                    and not pos.get('scalp_trailing_active')):
                                processor.activate_scalp_trailing(code)
                                logger.info(
                                    f"  [{code}] 트레일링 스톱 활성화 "
                                    f"(+{cur_pnl:.1f}%)")

                        # 트레일링 상태 반영된 최신 포지션 재조회
                        pos = processor.get_positions_dict().get(code, pos)
                        exit_signal = exit_gen.check_scalping_exit(
                            code=code,
                            name=pos['name'],
                            current_price=cur_price,
                            position=pos,
                            daily_pnl_pct=processor.daily_pnl_pct,
                        )
                    else:
                        exit_signal = exit_gen.check_exit(
                            code=code,
                            name=pos['name'],
                            current_price=price_data['price'],
                            position=pos,
                            daily_pnl_pct=processor.daily_pnl_pct,
                            stop_loss_override=(
                                shared.market_condition.stop_loss_override
                                if shared.market_condition else None),
                        )

                    if exit_signal:
                        ok = await processor.submit_sell(exit_signal)
                        if ok:
                            # 스캘핑 coordinator에 종료 기록 (일일 PnL 누적 + 재진입 제한)
                            if (pos.get('strategy_type') == 'scalping'
                                    and shared.scalp_coordinator):
                                result = ('win' if exit_signal.pnl_pct > 0
                                          else 'loss' if exit_signal.pnl_pct < 0
                                          else 'even')
                                shared.scalp_coordinator.record_exit(
                                    code, result, exit_signal.pnl_pct)
                        if ok and shared.intraday_filter:
                            # 손절 시 쿨다운 등록
                            if exit_signal.exit_type in (
                                    ExitType.STOP_LOSS,
                                    ExitType.SCALP_STOP_LOSS):
                                shared.intraday_filter.record_stop_loss(code)
                            # 트레일링 스톱: 수익 여부에 따라 분류
                            elif exit_signal.exit_type == ExitType.SCALP_TRAILING_STOP:
                                if exit_signal.pnl_pct > 0:
                                    shared.intraday_filter.record_take_profit(code)
                                else:
                                    shared.intraday_filter.record_stop_loss(code)
                            # 익절 시 쿨다운 등록 (고점 추격 재진입 방지)
                            elif exit_signal.exit_type in (
                                    ExitType.TAKE_PROFIT_1,
                                    ExitType.TAKE_PROFIT_2,
                                    ExitType.SCALP_TAKE_PROFIT):
                                shared.intraday_filter.record_take_profit(code)
                            await telegram.notify_exit(
                                exit_signal.code, exit_signal.name,
                                exit_signal.current_price,
                                exit_signal.sell_qty,
                                exit_signal.pnl_pct,
                                exit_signal.exit_type.value,
                                exit_signal.reason,
                            )
                except Exception as e:
                    logger.error(f"매도 체크 오류 [{code}]: {e}")

            # 포지션 종료 감지 → 재스캔 플래그
            current_count = len(processor.positions)
            if (current_count < shared.prev_position_count
                    and current_count == 0):
                shared.rescan_needed = True
                logger.info("포지션 완전 종료 → 역매공파 재스캔 예약")
            shared.prev_position_count = current_count

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"매도 감시 루프 오류: {e}")

        # 스캘핑 포지션이 있으면 1초, 없으면 2초
        has_scalp = any(
            p.get('strategy_type') == 'scalping'
            for p in positions.values()
            if not p.get('is_carryover', False)
        )
        await asyncio.sleep(1 if has_scalp else 2)


# ─────────────────────────────────────────────────────────────────────────────
# 매수 감시 태스크 (3초 주기)
# ─────────────────────────────────────────────────────────────────────────────

async def _entry_monitor(
    processor: OrderProcessor,
    api: KiwoomRestAPI,
    entry_gen: EntrySignalGenerator,
    telegram: TelegramBot,
    shared: _SharedState,
):
    """역매공파 후보 종목 매수 조건 감시"""
    logger = logging.getLogger('entry_monitor')

    while not shared.shutdown:
        try:
            now = datetime.now()
            # 전략 팀 에이전트 파라미터 기반 진입 시간
            sp = shared.strategy_params
            entry_delay = sp.entry_start_delay_minutes if sp else 5
            # entry_delay가 60 이상이면 시/분 변환
            entry_hour = 9 + entry_delay // 60
            entry_min = entry_delay % 60
            entry_start = time(entry_hour, entry_min)

            if not (entry_start <= now.time() <= time(14, 30)):
                await asyncio.sleep(3)
                continue

            ymgp_dict = shared.ymgp_dict
            if not ymgp_dict:
                await asyncio.sleep(3)
                continue

            # 블랙리스트 필터
            blacklist = set(sp.blacklist_codes) if sp else set()

            # 예측 점수 높은 순 정렬
            sorted_codes = sorted(
                ymgp_dict.keys(),
                key=lambda c: shared.prediction_scores.get(c, 0),
                reverse=True,
            )

            for code in sorted_codes:
                if shared.shutdown:
                    break

                # 블랙리스트 체크
                if code in blacklist:
                    continue

                # 빠른 사전 필터 (API 호출 전)
                if processor.is_held_or_pending(code):
                    continue

                ymgp_result = ymgp_dict[code]
                try:
                    price_data = await api.get_current_price(code)
                    current_price = price_data['price']

                    # 가격 추적 업데이트 (추세 분석용)
                    if shared.intraday_filter:
                        shared.intraday_filter.update_price(
                            code, current_price)

                    # ── IntradayFilter 체크 (쿨다운, 횟수, 추세, BB 과열) ──
                    if shared.intraday_filter:
                        reject = shared.intraday_filter.check(
                            code=code,
                            name=ymgp_result.name,
                            current_price=current_price,
                            bb20_upper=ymgp_result.bb20_upper,
                            daily_pnl_pct=processor.daily_pnl_pct,
                        )
                        if reject:
                            logger.debug(
                                f"진입 필터 거부 [{code}]: {reject}")
                            continue

                    # 예측 부스트 + 전략 팀 부스트/페널티
                    boosted_score = ymgp_result.score
                    pred_score = shared.prediction_scores.get(code, 0)
                    if pred_score > 0:
                        boosted_score *= 1.0 + min(pred_score / 100, 0.2)

                    if sp:
                        if code in sp.stock_boost:
                            boosted_score *= 1.0 + sp.stock_boost[code]
                        if code in sp.stock_penalty:
                            boosted_score *= 1.0 - sp.stock_penalty[code]

                    entry_signal = entry_gen.check_entry_ymgp(
                        code=code,
                        name=ymgp_result.name,
                        current_price=current_price,
                        open_price=price_data['open'],
                        bb20_upper=ymgp_result.bb20_upper,
                        score=boosted_score,
                        current_positions=processor.get_positions_dict(),
                        total_equity=processor.total_equity,
                        daily_pnl_pct=processor.daily_pnl_pct,
                        market_condition=shared.market_condition,
                    )

                    if entry_signal:
                        if pred_score > 0:
                            entry_signal.reason += f" | 예측:{pred_score:.0f}점"
                        ok = await processor.submit_buy(entry_signal)
                        if ok:
                            # 매수 성공 → 필터에 기록
                            if shared.intraday_filter:
                                shared.intraday_filter.record_buy(code)
                            await telegram.notify_entry(
                                entry_signal.code, entry_signal.name,
                                entry_signal.current_price,
                                entry_signal.suggested_qty,
                                entry_signal.reason,
                            )
                except Exception as e:
                    logger.debug(f"매수 체크 오류 [{code}]: {e}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"매수 감시 루프 오류: {e}")

        await asyncio.sleep(3)


# ─────────────────────────────────────────────────────────────────────────────
# 파란점선 감시 리스트 매수 감시 태스크 (5초 주기)
# ─────────────────────────────────────────────────────────────────────────────

async def _watchlist_entry_monitor(
    processor: OrderProcessor,
    api: KiwoomRestAPI,
    entry_gen: EntrySignalGenerator,
    telegram: TelegramBot,
    shared: _SharedState,
):
    """파란점선+수박지표 감시 리스트 종목 매수 조건 감시"""
    logger = logging.getLogger('watchlist_entry')

    while not shared.shutdown:
        try:
            now = datetime.now()
            sp = shared.strategy_params
            entry_delay = sp.entry_start_delay_minutes if sp else 5
            entry_hour = 9 + entry_delay // 60
            entry_min = entry_delay % 60
            entry_start = time(entry_hour, entry_min)

            if not (entry_start <= now.time() <= time(14, 30)):
                await asyncio.sleep(5)
                continue

            watchlist = shared.watchlist
            if not watchlist:
                await asyncio.sleep(5)
                continue

            # 블랙리스트 필터
            blacklist = set(sp.blacklist_codes) if sp else set()

            # 예측 점수 높은 순 정렬
            sorted_wl = sorted(
                watchlist,
                key=lambda r: shared.prediction_scores.get(r.code, 0),
                reverse=True,
            )

            for item in sorted_wl:
                if shared.shutdown:
                    break

                code = item.code

                # 블랙리스트 체크
                if code in blacklist:
                    continue

                # 역매공파에서 이미 감시 중이면 중복 방지
                if code in shared.ymgp_dict:
                    continue

                # 빠른 사전 필터 (API 호출 전)
                if processor.is_held_or_pending(code):
                    continue

                try:
                    price_data = await api.get_current_price(code)
                    current_price = price_data['price']
                    open_price = price_data['open']
                    today_volume = price_data.get('volume', 0)

                    # 가격 추적 업데이트
                    if shared.intraday_filter:
                        shared.intraday_filter.update_price(
                            code, current_price)

                    # IntradayFilter 체크 (쿨다운, 횟수, 추세)
                    if shared.intraday_filter:
                        reject = shared.intraday_filter.check(
                            code=code,
                            name=item.name,
                            current_price=current_price,
                            bb20_upper=item.blue_line,
                            daily_pnl_pct=processor.daily_pnl_pct,
                        )
                        if reject:
                            logger.debug(
                                f"감시 진입 필터 거부 [{code}]: {reject}")
                            continue

                    # 예측 부스트 + 전략 팀 부스트/페널티
                    boosted_score = item.score
                    pred_score = shared.prediction_scores.get(code, 0)
                    if pred_score > 0:
                        boosted_score *= 1.0 + min(pred_score / 100, 0.2)

                    if sp:
                        if code in sp.stock_boost:
                            boosted_score *= 1.0 + sp.stock_boost[code]
                        if code in sp.stock_penalty:
                            boosted_score *= 1.0 - sp.stock_penalty[code]

                    # 20일 평균 거래량 역산 (volume_ratio가 사전 계산됨)
                    avg_volume_20d = (
                        today_volume / item.volume_ratio
                        if item.volume_ratio > 0 else 1
                    )

                    entry_signal = entry_gen.check_entry(
                        code=code,
                        name=item.name,
                        current_price=current_price,
                        open_price=open_price,
                        today_volume=today_volume,
                        avg_volume_20d=avg_volume_20d,
                        blue_line=item.blue_line,
                        has_watermelon=item.watermelon_signal,
                        score=boosted_score,
                        current_positions=processor.get_positions_dict(),
                        total_equity=processor.total_equity,
                        daily_pnl_pct=processor.daily_pnl_pct,
                        market_condition=shared.market_condition,
                    )

                    if entry_signal:
                        if pred_score > 0:
                            entry_signal.reason += f" | 예측:{pred_score:.0f}점"
                        ok = await processor.submit_buy(entry_signal)
                        if ok:
                            if shared.intraday_filter:
                                shared.intraday_filter.record_buy(code)
                            await telegram.notify_entry(
                                entry_signal.code, entry_signal.name,
                                entry_signal.current_price,
                                entry_signal.suggested_qty,
                                entry_signal.reason,
                            )
                except Exception as e:
                    logger.debug(f"감시 매수 체크 오류 [{code}]: {e}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"감시 매수 감시 루프 오류: {e}")

        await asyncio.sleep(5)


# ─────────────────────────────────────────────────────────────────────────────
# 잔고 동기화 + 포지션 알림 태스크
# ─────────────────────────────────────────────────────────────────────────────

async def _sync_loop(
    processor: OrderProcessor,
    telegram: TelegramBot,
    interval_seconds: int,
    shared: _SharedState,
):
    """주기적 잔고 동기화 및 텔레그램 포지션 알림"""
    logger = logging.getLogger('sync')

    # 계좌 잔고 알림: 15분 주기 (포지션 알림 주기 기준 카운트)
    BALANCE_INTERVAL = 15 * 60  # 15분
    elapsed_since_balance = BALANCE_INTERVAL  # 첫 루프에서 즉시 발송

    while not shared.shutdown:
        try:
            await processor.sync_positions()
            pos_list = []
            for code, pos in processor.get_positions_dict().items():
                ep = pos['entry_price']
                cp = pos.get('current_price', ep)
                pnl = (cp - ep) / ep * 100 if ep > 0 else 0
                pos_list.append({
                    'code': code, 'name': pos['name'],
                    'qty': pos['qty'], 'pnl_pct': round(pnl, 1),
                })
            if pos_list:
                await telegram.notify_position_update(
                    pos_list, processor.daily_pnl_pct)

            # ── 계좌 잔고 15분 주기 발송 (매매 전용 봇) ──
            elapsed_since_balance += interval_seconds
            if elapsed_since_balance >= BALANCE_INTERVAL:
                elapsed_since_balance = 0
                try:
                    balance = await processor.api.get_balance()
                    await telegram.notify_account_status(balance)
                except Exception as eb:
                    logger.error(f"계좌 잔고 알림 오류: {eb}")

            # ── 대시보드 상태 파일 작성 ──
            try:
                _write_dashboard_status(processor, shared)
            except Exception as e2:
                logger.debug(f"대시보드 상태 파일 작성 오류: {e2}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"포지션 동기화 오류: {e}")

        await asyncio.sleep(interval_seconds)


def _write_dashboard_status(processor: OrderProcessor, shared: _SharedState):
    """대시보드용 상태 JSON 파일 원자적 작성"""
    summary = processor.get_daily_summary()
    positions = []
    for code, pos in processor.get_positions_dict().items():
        ep = pos['entry_price']
        cp = pos.get('current_price', ep)
        pnl = (cp - ep) / ep * 100 if ep > 0 else 0
        positions.append({
            'code': code,
            'name': pos['name'],
            'qty': pos['qty'],
            'entry_price': ep,
            'current_price': cp,
            'pnl_pct': round(pnl, 2),
            'amount': pos.get('amount', 0),
            'strategy_type': pos.get('strategy_type', 'regular'),
            'tp1_triggered': pos.get('tp1_triggered', False),
            'scalp_tp_pct': pos.get('scalp_tp_pct'),
            'scalp_sl_pct': pos.get('scalp_sl_pct'),
            'entry_time': (
                pos['entry_time'].isoformat()
                if hasattr(pos.get('entry_time'), 'isoformat')
                else str(pos.get('entry_time', ''))
            ),
        })

    mc = None
    if shared.market_condition:
        mc = shared.market_condition.overall_level.value

    status = {
        'timestamp': datetime.now().isoformat(),
        'mode': 'simulation',
        'system_running': not shared.shutdown,
        'market_condition': mc or 'UNKNOWN',
        'total_equity': summary.get('total_equity', 0),
        'daily_pnl': summary.get('daily_pnl', 0),
        'daily_pnl_pct': summary.get('daily_pnl_pct', 0),
        'total_trades': summary.get('total_trades', 0),
        'open_positions': summary.get('open_positions', 0),
        'positions': positions,
    }

    tmp_path = './logs/dashboard_status.json.tmp'
    final_path = './logs/dashboard_status.json'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, final_path)


# ─────────────────────────────────────────────────────────────────────────────
# 시장 상태 갱신 태스크 (30분 주기)
# ─────────────────────────────────────────────────────────────────────────────

async def _market_condition_loop(
    market_analyzer: MarketConditionAnalyzer,
    api: KiwoomRestAPI,
    telegram: TelegramBot,
    shared: _SharedState,
):
    """30분 주기로 시장 상태를 갱신"""
    logger = logging.getLogger('market_condition')

    while not shared.shutdown:
        await asyncio.sleep(1800)  # 30분
        if shared.shutdown:
            break
        try:
            prev_level = (shared.market_condition.overall_level
                          if shared.market_condition else None)
            shared.market_condition = await market_analyzer.analyze(api)
            new_level = shared.market_condition.overall_level
            if prev_level and new_level != prev_level:
                logger.warning(
                    f"시장 상태 변경: {prev_level.value} → {new_level.value}")
                await telegram.notify_market_condition(shared.market_condition)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"시장 상태 갱신 실패: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 역매공파 재스캔 태스크
# ─────────────────────────────────────────────────────────────────────────────

async def _rescan_loop(
    ymgp_screener: RealtimeScreener,
    telegram: TelegramBot,
    processor: OrderProcessor,
    shared: _SharedState,
):
    """포지션 종료 또는 주기 도래 시 역매공파 재스캔"""
    logger = logging.getLogger('rescan')

    while not shared.shutdown:
        try:
            should_scan = (
                shared.rescan_needed or ymgp_screener.should_scan())

            if should_scan:
                new_ymgp = await ymgp_screener.run_scan(force=True)
                if new_ymgp:
                    shared.ymgp_dict = new_ymgp
                    await telegram.notify_ymgp_scan_result(
                        list(new_ymgp.values()))
                    logger.info(
                        f"역매공파 재스캔: {len(new_ymgp)}종목 통과")
                else:
                    shared.ymgp_dict = {}
                    logger.info("역매공파 재스캔: 통과 종목 없음")
                shared.rescan_needed = False

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"역매공파 재스캔 오류: {e}")

        await asyncio.sleep(5)


# ─────────────────────────────────────────────────────────────────────────────
# 스캘핑 추가 매수 (분할 매수 30-30-40 중 2차)
# ─────────────────────────────────────────────────────────────────────────────


async def _scalp_add_buy(
    processor: 'OrderProcessor',
    coordinator: 'ScalpingCoordinator',
    analysis: 'ScalpingAnalysis',
    scalp_config: dict,
    shared: '_SharedState',
    telegram: 'TelegramBot',
    logger,
):
    """
    이미 보유 중인 스캘핑 종목에 대해 눌림목 추가 매수.
    조건: 현재가 < 매수가 (눌림 발생) + 에이전트 점수 유지 + 추가 매수 여력
    """
    code = analysis.code
    pos_dict = processor.get_positions_dict().get(code)
    if not pos_dict or pos_dict.get('strategy_type') != 'scalping':
        return

    entry_price = pos_dict['entry_price']
    # 추가 매수 설정
    add_buy_enabled = scalp_config.get('add_buy_enabled', True)
    add_buy_pullback_pct = scalp_config.get('add_buy_pullback_pct', -1.5)
    max_add_buys = scalp_config.get('max_add_buys', 1)

    if not add_buy_enabled:
        return

    # 이미 추가 매수한 종목은 스킵
    pos = processor.positions.get(code)
    if not pos:
        return
    if pos.add_buy_count >= max_add_buys:
        return

    # 눌림 체크: 현재가가 매수가 대비 일정% 이상 하락
    market_price = analysis.snapshot.price if analysis.snapshot else 0
    if market_price <= 0 or entry_price <= 0:
        return
    pnl_from_entry = (market_price - entry_price) / entry_price * 100
    if pnl_from_entry > add_buy_pullback_pct:
        return  # 아직 충분히 안 빠짐

    # 추가 매수 수량 (초기 매수의 약 50%)
    add_qty = max(1, pos.qty // 2)
    add_amount = int(add_qty * market_price)

    # 총 비중 체크
    total_equity = processor._total_equity
    max_pct = scalp_config.get('max_per_stock_pct', 5.0)
    current_amount = pos.qty * entry_price
    if (current_amount + add_amount) > total_equity * max_pct / 100:
        return

    signal = EntrySignal(
        code=code,
        name=analysis.name,
        signal_time=datetime.now(),
        current_price=market_price,
        blue_line=0,
        volume_ratio=(
            analysis.snapshot.volume_ratio if analysis.snapshot else 0),
        has_watermelon=False,
        score=analysis.total_score,
        suggested_qty=add_qty,
        suggested_amount=add_amount,
        reason=(
            f"스캘핑 추가매수 (눌림 {pnl_from_entry:+.1f}%) | "
            f"{analysis.total_score:.0f}점"),
        strategy_type="scalping",
        scalp_tp_pct=pos.scalp_tp_pct,
        scalp_sl_pct=pos.scalp_sl_pct,
        scalp_hold_minutes=pos.scalp_hold_minutes,
        intraday_atr=pos.intraday_atr,
        change_pct=pos.change_pct,
    )

    # 추가매수 전용 메서드 (is_held_or_pending 우회 + 평단 갱신)
    ok = await processor.submit_add_buy(signal)
    if ok:
        logger.info(
            f"스캘핑 추가매수: [{code}] {analysis.name} | "
            f"{add_qty}주 × {market_price:,}원 | "
            f"눌림 {pnl_from_entry:+.1f}%")
        await telegram.notify_entry(
            code, analysis.name, market_price, add_qty, add_amount,
            signal.reason)


# ─────────────────────────────────────────────────────────────────────────────
# 스캘핑 매수 감시 태스크 (45초 주기)
# ─────────────────────────────────────────────────────────────────────────────


async def _scalping_entry_monitor(
    processor: OrderProcessor,
    api: KiwoomRestAPI,
    entry_gen: EntrySignalGenerator,
    telegram: TelegramBot,
    shared: _SharedState,
    config: dict,
):
    """
    스캘핑 전략 매수 감시

    1. 주도주 분석으로 당일 상승 종목 확보
    2. 10명의 스캘핑 전문가 에이전트 분석
    3. 점수 60+, 타이밍 "즉시"인 종목만 매수 시그널 생성
    4. OrderProcessor를 통해 매수 주문 제출
    """
    logger = logging.getLogger('scalping_monitor')

    scalp_config = config.get('strategy', {}).get('scalping', {})
    if not scalp_config.get('enabled', True):
        logger.info("스캘핑 전략 비활성화 — 태스크 종료")
        return

    poll_interval = scalp_config.get('poll_interval_seconds', 45)
    min_score = scalp_config.get('min_score', 60)
    required_timing = scalp_config.get('required_timing', '즉시')
    scalp_max_pct = scalp_config.get('max_per_stock_pct', 5.0)
    top_n = scalp_config.get('top_n', 20)
    entry_start = time(
        *map(int, scalp_config.get('entry_start', '09:10').split(':')))
    entry_end = time(
        *map(int, scalp_config.get('entry_end', '14:00').split(':')))
    default_tp = scalp_config.get('default_tp_pct', 3.0)
    default_sl = scalp_config.get('default_sl_pct', -1.5)
    default_hold = scalp_config.get('default_hold_minutes', 15)

    coordinator = ScalpingCoordinator(config)
    shared.scalp_coordinator = coordinator  # exit_monitor에서 접근 가능
    analyzer = LeadingStocksAnalyzer(api, config)

    # ── 1분봉 캐시 (종목별 최근 분봉 데이터) ──
    intraday_cache = {}  # {code: [{time,price,volume,...}]}
    intraday_max_candles = scalp_config.get('intraday_max_candles', 60)
    intraday_fetch_count = scalp_config.get('intraday_fetch_top_n', 10)

    logger.info(
        f"스캘핑 감시 시작 | 주기: {poll_interval}초 | "
        f"최소점수: {min_score} | 비중: {scalp_max_pct}% | "
        f"1분봉: 상위{intraday_fetch_count}종목")

    while not shared.shutdown:
        try:
            now = datetime.now()

            # 시간 제한 확인
            if now.time() < entry_start or now.time() >= entry_end:
                await asyncio.sleep(10)
                continue

            # 시장 상태 EXTREME이면 스킵
            if (shared.market_condition
                    and not shared.market_condition.entry_allowed):
                await asyncio.sleep(poll_interval)
                continue

            # API 한도 초과 시 장시간 대기
            if api.is_quota_exhausted('ka10001'):
                logger.warning("ka10001 API 한도 초과 → 5분 대기")
                await asyncio.sleep(300)
                continue

            # ── 1. 주도주 상승 종목 확보 (ka00198 초고속 → 기존 방식 폴백) ──
            use_fast = scalp_config.get('use_fast_rank', True)
            lead_results = None

            if use_fast:
                try:
                    qry_tp = scalp_config.get('fast_rank_qry_tp', '2')
                    lead_results = await analyzer.analyze_fast(
                        top_n=top_n, qry_tp=qry_tp)
                except Exception as e:
                    logger.warning(f"ka00198 초고속 스캔 실패 → 기존 방식 폴백: {e}")

            if not lead_results:
                filtered = shared.filtered_codes
                if not filtered:
                    await asyncio.sleep(poll_interval)
                    continue
                lead_results = await analyzer.analyze(filtered, top_n=top_n)

            if not lead_results:
                await asyncio.sleep(poll_interval)
                continue

            # ── 2. StockSnapshot 변환 (leading_stocks 데이터 재사용, 추가 API 호출 없음) ──
            snapshots = []
            for r in lead_results:
                snapshots.append(StockSnapshot(
                    code=r.code, name=r.name,
                    price=r.current_price,
                    open=r.open_price or r.current_price,
                    high=r.high_price or r.current_price,
                    low=r.low_price or r.current_price,
                    prev_close=r.prev_close,
                    volume=r.volume,
                    change_pct=r.change_pct,
                    trade_value=r.trade_value,
                    volume_ratio=r.volume_ratio,
                    category=r.category,
                    score=r.score,
                ))

            # ── 2.5. 상위 종목 1분봉 수집 (에이전트 분석용) ──
            # 변동률 상위 N종목만 API 호출하여 캐시 업데이트
            sorted_snaps = sorted(
                snapshots, key=lambda s: s.change_pct, reverse=True)
            fetch_codes = [s.code for s in sorted_snaps[:intraday_fetch_count]]

            for code in fetch_codes:
                try:
                    candles = await api.get_intraday_chart(
                        code, tick_scope=1, count=intraday_max_candles)
                    if candles:
                        # 시간순 정렬 (오래된 것 먼저)
                        candles.sort(
                            key=lambda c: c.get('time', ''))
                        intraday_cache[code] = candles[-intraday_max_candles:]
                except Exception as e:
                    logger.debug(f"1분봉 조회 실패 [{code}]: {e}")

            # ── 3. 시장 동조성 업데이트 + 스캘핑 팀 분석 ──
            if shared.market_condition and shared.market_condition.kosdaq:
                coordinator.set_market_change(
                    shared.market_condition.kosdaq.daily_change_pct)
            results = coordinator.analyze(
                snapshots, intraday_data=intraday_cache)

            # ── 4. 진입 가능 종목 필터 ──
            for analysis in results:
                if shared.shutdown:
                    break
                if analysis.total_score < min_score:
                    continue
                if analysis.timing != required_timing:
                    continue

                code = analysis.code
                name = analysis.name

                # 이미 보유 중인 종목: 추가 매수 조건 체크
                if processor.is_held_or_pending(code):
                    await _scalp_add_buy(
                        processor, coordinator, analysis,
                        scalp_config, shared, telegram, logger)
                    continue

                # 인트라데이 필터
                if shared.intraday_filter:
                    snap = analysis.snapshot
                    reject = shared.intraday_filter.check(
                        code, name,
                        snap.price if snap else 0,
                        0,  # bb20_upper (스캘핑은 BB 과열 체크 생략 가능)
                        processor.daily_pnl_pct,
                    )
                    if reject:
                        logger.debug(f"스캘핑 필터 거부 [{code}]: {reject}")
                        continue

                # ── 5. 포지션 사이징 ──
                total_equity = processor._total_equity
                if total_equity <= 0:
                    continue

                # 분할 매수: 초기 투입 비율 (리포트 권장 30-30-40 중 1차)
                initial_ratio = scalp_config.get(
                    'initial_entry_ratio', 0.6)  # 기본 60%
                max_amount = int(
                    total_equity * scalp_max_pct / 100 * initial_ratio)
                # 실제 현재가 사용 (optimal_entry_price는 에이전트 이상 진입가로 시장가와 괴리)
                market_price = (
                    analysis.snapshot.price if analysis.snapshot else 0)
                if market_price <= 0:
                    continue

                # 슬리피지 사전 차단: 에이전트 제안가 대비 현재가 괴리가 크면 스킵
                max_slip = scalp_config.get('max_slippage_pct', 2.0)
                optimal = analysis.optimal_entry_price or market_price
                pre_slip = (market_price - optimal) / optimal * 100 if optimal > 0 else 0
                if pre_slip > max_slip:
                    logger.info(
                        f"스캘핑 슬리피지 사전차단 [{code}] "
                        f"제안가:{optimal:,} vs 현재가:{market_price:,} "
                        f"(+{pre_slip:.1f}% > {max_slip}%)")
                    continue

                qty = max_amount // int(market_price)
                if qty <= 0:
                    continue

                # 시장 상태 배율 적용
                if (shared.market_condition
                        and shared.market_condition.position_size_multiplier < 1):
                    qty = max(1, int(
                        qty * shared.market_condition.position_size_multiplier))

                amount = int(qty * market_price)

                # ── 6. 매수 시그널 생성 ──
                tp_pct = analysis.scalp_tp_pct or default_tp
                sl_pct = analysis.scalp_sl_pct or default_sl
                hold_min = analysis.hold_minutes or default_hold

                signal = EntrySignal(
                    code=code,
                    name=name,
                    signal_time=datetime.now(),
                    current_price=market_price,
                    blue_line=0,
                    volume_ratio=(
                        analysis.snapshot.volume_ratio
                        if analysis.snapshot else 0),
                    has_watermelon=False,
                    score=analysis.total_score,
                    suggested_qty=qty,
                    suggested_amount=amount,
                    reason=(
                        f"스캘핑 {analysis.total_score:.0f}점 "
                        f"({analysis.consensus_level}) | "
                        f"TP +{tp_pct:.1f}% SL {sl_pct:.1f}% "
                        f"{hold_min}분"
                    ),
                    strategy_type="scalping",
                    scalp_tp_pct=tp_pct,
                    scalp_sl_pct=sl_pct,
                    scalp_hold_minutes=hold_min,
                    # 2026-04-07: 변동성 비례 SL/트레일링용
                    intraday_atr=getattr(analysis, 'intraday_atr', 0.0),
                    change_pct=getattr(analysis, 'snapshot', None)
                        and analysis.snapshot.change_pct or 0.0,
                )

                ok = await processor.submit_buy(signal)
                if ok:
                    coordinator.record_entry(code)
                    if shared.intraday_filter:
                        shared.intraday_filter.record_buy(code)
                    await telegram.notify_entry(
                        code, name, market_price, qty,
                        signal.reason)
                    logger.info(
                        f"스캘핑 매수: [{code}] {name} | "
                        f"{qty}주 × {market_price:,}원 | "
                        f"TP +{tp_pct}% SL {sl_pct}% {hold_min}분")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"스캘핑 감시 오류: {e}", exc_info=True)

        await asyncio.sleep(poll_interval)


# ─────────────────────────────────────────────────────────────────────────────
# 미청산 포지션 전용 감시 태스크 (2초 주기)
# ─────────────────────────────────────────────────────────────────────────────

async def _carryover_monitor(
    processor: OrderProcessor,
    api: KiwoomRestAPI,
    carryover_strategy: CarryoverExitStrategy,
    telegram: TelegramBot,
    shared: _SharedState,
):
    """
    미청산(전일 미청산) 포지션 전용 매도 감시

    당일 매수 포지션의 -2% 손절과 완전히 분리하여
    gradual(BB 기반 분할 청산) + stop_loss_wide(시가 기준 확대 손절) 혼합 전략 적용.
    """
    logger = logging.getLogger('carryover_monitor')

    # 종목별 연속 매도 실패 카운터 (무한 반복 방지)
    MAX_SELL_FAILURES = 5
    sell_fail_counts = {}  # {code: 연속 실패 횟수}

    while not shared.shutdown:
        try:
            carryover_states = shared.carryover_states
            if not carryover_states:
                break  # 미청산 전량 처리 완료 → 태스크 종료

            for code in list(carryover_states.keys()):
                if shared.shutdown:
                    break

                state = carryover_states.get(code)
                if not state or state.qty <= 0:
                    carryover_states.pop(code, None)
                    sell_fail_counts.pop(code, None)
                    continue

                # 연속 실패 한도 초과 시 스킵 (API 낭비 방지)
                if sell_fail_counts.get(code, 0) >= MAX_SELL_FAILURES:
                    continue

                try:
                    price_data = await api.get_current_price(code)
                    current_price = price_data['price']
                except Exception as e:
                    logger.error(f"미청산 현재가 조회 실패 [{code}]: {e}")
                    continue

                # 청산 조건 체크
                result = carryover_strategy.check_exit(
                    current_price=current_price,
                    state=state,
                    daily_pnl_pct=processor.daily_pnl_pct,
                )

                if result is None:
                    continue

                # ExitSignal 생성 → 프로세서에 제출
                pnl_pct = (
                    (current_price - state.entry_price)
                    / state.entry_price * 100
                    if state.entry_price > 0 else 0)

                # 미청산 exit_type 매핑 (반등청산은 전용 타입 사용)
                _carryover_exit_map = {
                    '미청산_강제청산': ExitType.FORCE_LIQUIDATION,
                    '미청산_일일손실한도': ExitType.DAILY_LOSS_LIMIT,
                    '미청산_확대손절': ExitType.STOP_LOSS,
                    '미청산_반등청산_1차': ExitType.CARRYOVER_PHASE1,
                    '미청산_반등청산_2차': ExitType.CARRYOVER_PHASE2,
                }
                mapped_type = _carryover_exit_map.get(
                    result['exit_type'], ExitType.FORCE_LIQUIDATION)

                exit_signal = ExitSignal(
                    code=code,
                    name=state.name,
                    signal_time=datetime.now(),
                    exit_type=mapped_type,
                    current_price=current_price,
                    entry_price=state.entry_price,
                    pnl_pct=pnl_pct,
                    sell_ratio=result['sell_ratio'],
                    sell_qty=result['sell_qty'],
                    reason=result['reason'],
                )

                ok = await processor.submit_sell(exit_signal)

                if ok:
                    # 성공 시 실패 카운터 초기화
                    sell_fail_counts.pop(code, None)

                    await telegram.notify_exit(
                        code, state.name,
                        current_price,
                        result['sell_qty'],
                        pnl_pct,
                        result['exit_type'],
                        result['reason'],
                    )

                    if result['sell_ratio'] >= 1.0:
                        # 전량 매도 → 상태 제거
                        carryover_states.pop(code, None)
                        logger.info(
                            f"미청산 전량 청산 완료: [{code}] {state.name} | "
                            f"{result['exit_type']}")
                    else:
                        # 부분 매도 (phase1) → 수량 갱신 + 플래그
                        state.qty -= result['sell_qty']
                        state.phase1_done = True
                        logger.info(
                            f"미청산 1차 분할 매도: [{code}] {state.name} | "
                            f"잔량: {state.qty}주")
                else:
                    fail_count = sell_fail_counts.get(code, 0) + 1
                    sell_fail_counts[code] = fail_count
                    logger.error(
                        f"미청산 매도 실패 ({fail_count}/{MAX_SELL_FAILURES}): "
                        f"[{code}] {state.name} | {result['exit_type']}")
                    if fail_count >= MAX_SELL_FAILURES:
                        logger.critical(
                            f"미청산 매도 {MAX_SELL_FAILURES}회 연속 실패 — "
                            f"수동 확인 필요: [{code}] {state.name} "
                            f"{state.qty}주")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"미청산 감시 루프 오류: {e}")

        await asyncio.sleep(2)


# ─────────────────────────────────────────────────────────────────────────────
# PID Lock (simulation 모드 중복 실행 방지)
# ─────────────────────────────────────────────────────────────────────────────

PID_LOCK_FILE = "/tmp/ai-trade-simulation.pid"


def _remove_pid_lock():
    """PID lock file 삭제"""
    try:
        os.remove(PID_LOCK_FILE)
    except OSError:
        pass


def _is_process_alive(pid: int) -> bool:
    """PID가 실행 중인지 확인"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_pid_lock() -> bool:
    """
    PID lock 획득. 이미 실행 중인 프로세스가 있으면 False 반환.
    """
    if os.path.exists(PID_LOCK_FILE):
        try:
            with open(PID_LOCK_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if _is_process_alive(old_pid):
                return False
            # stale lock file — 이전 프로세스가 이미 종료됨
        except (ValueError, OSError):
            pass  # 손상된 lock file → 덮어씀

    with open(PID_LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))

    atexit.register(_remove_pid_lock)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    return True


# ─────────────────────────────────────────────────────────────────────────────
# CLI 엔트리포인트
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="주식단테 수박지표 기반 당일 자동매매 시스템")
    parser.add_argument(
        '--mode', choices=['simulation', 'live'], default='simulation',
        help='실행 모드 (기본: simulation = 모의투자)')
    parser.add_argument(
        '--scan-only', action='store_true',
        help='스캔만 실행 (매매 없음)')
    parser.add_argument(
        '--update-cache', action='store_true',
        help='전종목 OHLCV 캐시 업데이트 (장 마감 후 실행)')
    parser.add_argument(
        '--account-status', action='store_true',
        help='계좌 현황 조회 → 텔레그램 발송')
    parser.add_argument(
        '--market-status', action='store_true',
        help='시장 상태 분석 (ATR 기반) -> 콘솔 + 텔레그램')
    parser.add_argument(
        '--leading-stocks', action='store_true',
        help='당일 주도주 실시간 순위 -> 콘솔 + 텔레그램')
    parser.add_argument(
        '--predict', action='store_true',
        help='팀 에이전트 상승 예측 -> 콘솔 + 텔레그램')
    parser.add_argument(
        '--post-market', action='store_true',
        help='장 마감 후 매매 분석 + 전략 고도화 → 텔레그램')
    parser.add_argument(
        '--notion-sync', action='store_true',
        help='전체 매매 기록을 Notion 캘린더에 동기화')
    parser.add_argument(
        '--scalping', action='store_true',
        help='스캘핑 타이밍 팀 에이전트 분석 (당일 상승 종목)')
    parser.add_argument(
        '--top', type=int, default=20,
        help='상위 N종목 (기본: 20, --leading-stocks/--predict 공용)')
    args = parser.parse_args()

    # ── simulation 모드 PID lock 조기 체크 ──
    # config 로드/로깅 설정 전에 먼저 체크하여
    # cron이 LLM 에이전트를 통해 호출해도 즉시 종료되도록 함
    is_simulation_run = (
        args.mode == 'simulation'
        and not (args.account_status or args.market_status
                 or args.update_cache or args.scan_only
                 or args.leading_stocks or args.predict
                 or args.post_market or args.notion_sync
                 or args.scalping)
    )
    if is_simulation_run and not acquire_pid_lock():
        print(f"simulation 이미 실행 중 (PID lock: {PID_LOCK_FILE}), 종료")
        return

    config = load_config(mode=args.mode)
    setup_logging(config)
    logger = logging.getLogger('main')

    if is_simulation_run:
        logger.info(f"PID lock 획득 (pid={os.getpid()})")

    # 계좌 현황 / 시장 상태 / 주도주는 거래일 무관하게 실행 가능
    if args.account_status:
        asyncio.run(run_account_status(config))
        return

    if args.market_status:
        asyncio.run(run_market_status(config))
        return

    if args.predict or args.leading_stocks:
        asyncio.run(run_analysis(
            config,
            top_n=args.top,
            do_predict=args.predict,
            do_leading=args.leading_stocks,
        ))
        return

    if args.scalping:
        asyncio.run(run_scalping_analysis(config, top_n=args.top))
        return

    if args.post_market:
        asyncio.run(run_post_market(config))
        return

    if args.notion_sync:
        async def _notion_sync_all():
            notion = NotionTradeSync(config)
            await notion.initialize()
            try:
                await notion.sync_all()
                # 월간 요약도 동기화
                months = set()
                for d in notion._get_all_trade_dates():
                    months.add(d[:7])
                for m in sorted(months):
                    await notion.sync_month(m)
            finally:
                await notion.close()
        asyncio.run(_notion_sync_all())
        return

    # 거래일 체크
    if not is_krx_trading_day():
        logger.info("오늘은 거래일이 아닙니다 (주말/공휴일).")
        return

    # 실행
    if args.update_cache:
        asyncio.run(run_update_cache(config))
    elif args.scan_only:
        asyncio.run(run_scan_only(config))
    else:
        asyncio.run(run_trading_loop(config))


if __name__ == "__main__":
    main()
