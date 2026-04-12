#!/usr/bin/env python3
"""
BAR-28: 한국 주식 시장 분석 + 관심종목 리스트
Stock Analyst 에이전트 실행 스크립트

기능:
- KOSPI/KOSDAQ 시장 현황 분석
- 종목 유동성/변동성 필터링
- 기술신호 점수 계산
- 관심종목 30-50개 선정
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import json
from pathlib import Path

# 프로젝트 설정
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.gateway.kiwoom import KiwoomGateway
from backend.core.scanner.stock_screener import DailyScreener, ScreenerSignal
from backend.core.scanner.signal_scanner import SignalScanner
from backend.models.market import MarketType

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class KoreanStockAnalyzer:
    """한국 주식 시장 분석기"""

    def __init__(self):
        self.gateway = KiwoomGateway()
        self.screener = DailyScreener(self.gateway)
        self.signal_scanner = SignalScanner(self.gateway)

        # 필터링 기준
        self.min_trading_volume = 1_000_000  # 1M주 이상
        self.price_range = (5000, 100000)     # 5,000 ~ 100,000원
        self.volatility_range = (5, 30)       # 월간 변동률 5~30%
        self.data_days_required = 60          # 최소 60일 데이터

    async def get_market_overview(self) -> Dict:
        """시장 현황 분석 (KOSPI/KOSDAQ)"""
        logger.info("📊 시장 현황 분석 시작...")

        try:
            # KOSPI 지수 조회
            kospi_data = await self.gateway.get_ohlcv("KOSPI", "1d", limit=60)
            kosdaq_data = await self.gateway.get_ohlcv("KOSDAQ", "1d", limit=60)

            kospi_change = ((kospi_data[-1].close - kospi_data[0].close) / kospi_data[0].close) * 100
            kosdaq_change = ((kosdaq_data[-1].close - kosdaq_data[0].close) / kosdaq_data[0].close) * 100

            overview = {
                "timestamp": datetime.now().isoformat(),
                "kospi": {
                    "current": kospi_data[-1].close,
                    "change_percent": round(kospi_change, 2),
                    "trend": "상승" if kospi_change > 0 else "하락"
                },
                "kosdaq": {
                    "current": kosdaq_data[-1].close,
                    "change_percent": round(kosdaq_change, 2),
                    "trend": "상승" if kosdaq_change > 0 else "하락"
                },
                "market_sentiment": "긍정적" if (kospi_change + kosdaq_change) / 2 > 0 else "부정적"
            }

            logger.info(f"✅ 시장 현황: KOSPI {overview['kospi']['trend']} {overview['kospi']['change_percent']}% | KOSDAQ {overview['kosdaq']['trend']} {overview['kosdaq']['change_percent']}%")
            return overview

        except Exception as e:
            logger.error(f"❌ 시장 현황 분석 실패: {e}")
            return {}

    async def screen_stocks(self, symbols: List[str]) -> List[Dict]:
        """종목 스크리닝 (필터링)"""
        logger.info(f"🔍 {len(symbols)}개 종목 스크리닝 시작...")

        screened_stocks = []

        for symbol in symbols:
            try:
                # 기본 정보 조회
                ohlcv_data = await self.gateway.get_ohlcv(symbol, "1d", limit=self.data_days_required)

                if not ohlcv_data:
                    continue

                # 유동성 확인
                avg_volume = sum(c.volume for c in ohlcv_data) / len(ohlcv_data)
                if avg_volume < self.min_trading_volume:
                    continue

                # 가격대 확인
                current_price = ohlcv_data[-1].close
                if not (self.price_range[0] <= current_price <= self.price_range[1]):
                    continue

                # 변동성 계산
                returns = [(ohlcv_data[i].close - ohlcv_data[i-1].close) / ohlcv_data[i-1].close
                          for i in range(1, len(ohlcv_data))]
                volatility = (max(returns) - min(returns)) * 100

                if not (self.volatility_range[0] <= volatility <= self.volatility_range[1]):
                    continue

                screened_stocks.append({
                    "symbol": symbol,
                    "current_price": current_price,
                    "avg_volume": avg_volume,
                    "volatility": round(volatility, 2),
                    "data_points": len(ohlcv_data)
                })

            except Exception as e:
                logger.warning(f"⚠️ {symbol} 분석 오류: {e}")
                continue

        logger.info(f"✅ 스크리닝 완료: {len(screened_stocks)}개 종목 통과")
        return screened_stocks

    async def calculate_signal_scores(self, symbols: List[str]) -> List[Dict]:
        """기술신호 점수 계산"""
        logger.info(f"📈 {len(symbols)}개 종목 신호 점수 계산 시작...")

        try:
            # SignalScanner를 사용한 신호 분석
            signals = await self.signal_scanner.scan(symbols)

            scored_stocks = []
            for signal in signals:
                scored_stocks.append({
                    "symbol": signal.symbol,
                    "signal_type": signal.signal_type,
                    "score": signal.score,
                    "entry_price": signal.price,
                    "strategy": signal.strategy_id,
                    "timestamp": signal.timestamp.isoformat()
                })

            logger.info(f"✅ 신호 점수 계산 완료: {len(scored_stocks)}개 종목")
            return scored_stocks

        except Exception as e:
            logger.error(f"❌ 신호 점수 계산 실패: {e}")
            return []

    async def build_watchlist(self, screened_stocks: List[Dict], scored_stocks: List[Dict]) -> List[Dict]:
        """최종 관심종목 리스트 구성"""
        logger.info("🎯 최종 관심종목 리스트 구성 중...")

        # 점수 매핑
        score_map = {s["symbol"]: s["score"] for s in scored_stocks}

        # 종목별 최종 점수 계산
        for stock in screened_stocks:
            stock["signal_score"] = score_map.get(stock["symbol"], 0)
            stock["liquidity_score"] = min(100, (stock["avg_volume"] / 5_000_000) * 100)
            stock["final_score"] = (stock.get("signal_score", 0) * 0.6 +
                                   stock["liquidity_score"] * 0.4)

        # 점수순 정렬 및 상위 30-50개 선택
        sorted_stocks = sorted(screened_stocks, key=lambda x: x["final_score"], reverse=True)
        watchlist = sorted_stocks[:50]

        logger.info(f"✅ 최종 관심종목 {len(watchlist)}개 선정 완료")
        return watchlist

    async def generate_report(self, market_overview: Dict, watchlist: List[Dict]) -> str:
        """분석 리포트 생성"""
        logger.info("📋 분석 리포트 생성 중...")

        report = f"""
# BAR-28: 한국 주식 시장 분석 + 관심종목 리스트

생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 1. 시장 현황 분석

### 지수 현황
- **KOSPI**: {market_overview.get('kospi', {}).get('current', 'N/A')} ({market_overview.get('kospi', {}).get('change_percent', 'N/A')}%)
  - 추세: {market_overview.get('kospi', {}).get('trend', 'N/A')}

- **KOSDAQ**: {market_overview.get('kosdaq', {}).get('current', 'N/A')} ({market_overview.get('kosdaq', {}).get('change_percent', 'N/A')}%)
  - 추세: {market_overview.get('kosdaq', {}).get('trend', 'N/A')}

### 시장 심리
- 시장 심리: {market_overview.get('market_sentiment', 'N/A')}

---

## 2. 관심종목 리스트 (상위 30개)

| 순위 | 종목 | 현재가 | 변동성 | 신호점수 | 최종점수 |
|------|------|--------|--------|---------|---------|
"""

        for idx, stock in enumerate(watchlist[:30], 1):
            report += f"| {idx} | {stock['symbol']} | {stock['current_price']:,.0f} | {stock['volatility']:.1f}% | {stock.get('signal_score', 0):.1f} | {stock['final_score']:.1f} |\n"

        report += f"""
---

## 3. 선정 기준

1. **유동성**: 일평균거래량 > 1,000,000주
2. **가격대**: 5,000 ~ 100,000원
3. **변동성**: 월간 변동률 5~30%
4. **기술신호**: 파란점선/수박신호 호응도

---

## 4. 권장사항

- 상위 30개 종목으로 포트폴리오 구성
- 주중 신호 강도 재평가
- 시장 상황 변화 시 리스트 재조정

---

*Report generated by Stock Analyst Agent (BAR-28)*
"""

        return report

    async def run(self, symbols: Optional[List[str]] = None):
        """전체 분석 실행"""
        logger.info("🚀 BAR-28 한국 주식 시장 분석 시작...")

        try:
            # 시장 현황 분석
            market_overview = await self.get_market_overview()

            # 기본 샘플 종목 (실제로는 전체 KOSPI/KOSDAQ 종목)
            if symbols is None:
                symbols = [
                    "005930", "000660", "051910", "035720", "105560",  # 대형주
                    "068270", "207940", "247540", "091990", "034220",  # 중형주
                    # 추가 종목들...
                ]

            # 종목 스크리닝
            screened_stocks = await self.screen_stocks(symbols)

            if screened_stocks:
                # 신호 점수 계산
                symbols_to_score = [s["symbol"] for s in screened_stocks]
                scored_stocks = await self.calculate_signal_scores(symbols_to_score)

                # 최종 관심종목 리스트
                watchlist = await self.build_watchlist(screened_stocks, scored_stocks)

                # 리포트 생성
                report = await self.generate_report(market_overview, watchlist)

                # 파일 저장
                report_path = Path(__file__).parent.parent / "docs" / "01-plan" / "analysis" / "KR-Market-Analysis-2026Q2.md"
                report_path.parent.mkdir(parents=True, exist_ok=True)
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(report)

                logger.info(f"✅ 분석 완료! 리포트 저장: {report_path}")

                # 종목 리스트 CSV 저장
                csv_path = report_path.parent / "KR-Watchlist-30-50.csv"
                with open(csv_path, "w", encoding="utf-8") as f:
                    f.write("순위,종목코드,현재가,변동성,신호점수,최종점수,일평균거래량\n")
                    for idx, stock in enumerate(watchlist[:50], 1):
                        f.write(f"{idx},{stock['symbol']},{stock['current_price']:.0f},{stock['volatility']:.1f},{stock.get('signal_score', 0):.1f},{stock['final_score']:.1f},{stock['avg_volume']:.0f}\n")

                logger.info(f"✅ 종목 리스트 저장: {csv_path}")
            else:
                logger.warning("⚠️ 스크리닝 결과 없음")

        except Exception as e:
            logger.error(f"❌ 분석 실패: {e}", exc_info=True)


async def main():
    """메인 함수"""
    analyzer = KoreanStockAnalyzer()
    await analyzer.run()


if __name__ == "__main__":
    asyncio.run(main())
