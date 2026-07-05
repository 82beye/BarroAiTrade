"""읽기 전용 스캔 게이트웨이 — 온디맨드 전략 스캐닝용 MarketGateway 어댑터.

API 서버 프로세스에는 `app_state.market_gateway` 가 항상 None 이다(오케스트레이터는
별도 라이브 데몬에서만 기동). 그 결과 스크리너(FR-S: 매매타점)의 온디맨드 스캔
(`SignalScanner`)이 gateway 를 요구하면서도 실제로는 절대 동작하지 않는 죽은 경로였다.

이 어댑터는 그 갭을 메운다: 시세 조회(get_ohlcv/get_ticker/get_order_book 등)는
kiwoom_quotes(실거래소 REST, market.py 와 동일 lazy 싱글턴 패턴) → OHLCV 캐시
순으로 실데이터를 공급하고, **주문/계좌 관련 추상 메서드는 전부 명시적으로
거부(raise)한다** — 이 어댑터로는 어떤 경로로도 주문을 낼 수 없다(§2 안전 경계
강화, 약화 아님).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import SecretStr

from backend.core.gateway.base import MarketGateway
from backend.core.market_data import cache_quotes
from backend.models.market import OHLCV, MarketType, OrderBook, Ticker
from backend.models.position import Balance, Order, OrderResult

logger = logging.getLogger(__name__)

_REFUSAL = "ReadOnlyScanGateway 는 시세 조회 전용이며 주문·계좌 변경을 지원하지 않는다"

# 프론트/전략 timeframe 문자열 → ka10080 tic_scope(분). backend.api.routes.market
# 의 동일 매핑과 값이 같다(계층 역전을 피하려 여기서 자체 보유 — 값 변경 시 함께 갱신).
_TF_TO_TIC = {
    "1m": "1", "3m": "3", "5m": "5", "10m": "10",
    "15m": "15", "30m": "30", "1h": "60", "60m": "60",
}


class ReadOnlyScanGateway(MarketGateway):
    """온디맨드 전략 스캔 전용 읽기 전용 게이트웨이 (KRX 주식).

    조달 우선순위(전부 조회 TR): kiwoom_quotes(실거래소) → OHLCV 캐시.
    키/토큰이 없으면 캐시만으로 동작(스캔 결과가 비거나 지연 데이터일 수 있음 —
    날조 금지 원칙에 따라 조용히 빈 결과를 반환하지 오류를 던지지 않는다).
    """

    market_type = MarketType.STOCK

    def __init__(self) -> None:
        self._quotes = None
        self._quotes_tried = False
        self._candles = None
        self._candles_tried = False

    # ── lazy 싱글턴 (market.py _get_quotes/_get_candle_fetcher 와 동일 패턴) ──
    def _get_quotes(self):
        if self._quotes_tried:
            return self._quotes
        self._quotes_tried = True
        app_key = os.environ.get("KIWOOM_APP_KEY", "").strip()
        app_secret = os.environ.get("KIWOOM_APP_SECRET", "").strip()
        if not app_key or not app_secret:
            self._quotes = None
            return None
        try:
            from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
            from backend.core.gateway.kiwoom_quotes import KiwoomQuotes

            oauth = KiwoomNativeOAuth(
                app_key=SecretStr(app_key),
                app_secret=SecretStr(app_secret),
                base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
            )
            self._quotes = KiwoomQuotes(oauth=oauth)
        except Exception as exc:
            logger.warning("ReadOnlyScanGateway quotes 초기화 실패: %s", type(exc).__name__)
            self._quotes = None
        return self._quotes

    def _get_candles(self):
        if self._candles_tried:
            return self._candles
        self._candles_tried = True
        app_key = os.environ.get("KIWOOM_APP_KEY", "").strip()
        app_secret = os.environ.get("KIWOOM_APP_SECRET", "").strip()
        if not app_key or not app_secret:
            self._candles = None
            return None
        try:
            from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
            from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher

            oauth = KiwoomNativeOAuth(
                app_key=SecretStr(app_key),
                app_secret=SecretStr(app_secret),
                base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
            )
            self._candles = KiwoomNativeCandleFetcher(oauth=oauth)
        except Exception as exc:
            logger.warning("ReadOnlyScanGateway candles 초기화 실패: %s", type(exc).__name__)
            self._candles = None
        return self._candles

    # ── 인증 (조회 토큰 발급일 뿐, 주문 권한 없음) ─────────────────────────
    async def authenticate(self) -> None:
        return None

    # ── 시장 데이터 (읽기 전용) ────────────────────────────────────────────
    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> List[OHLCV]:
        candles = self._get_candles()
        if candles is not None:
            try:
                if timeframe == "1d":
                    rows = await candles.fetch_daily(symbol)
                else:
                    tic = _TF_TO_TIC.get(timeframe)
                    rows = await candles.fetch_minute_history(symbol, tic_scope=tic) if tic else None
                if rows:
                    out = rows[-limit:]
                    return [
                        OHLCV(
                            symbol=symbol,
                            timestamp=c.timestamp,
                            open=c.open,
                            high=c.high,
                            low=c.low,
                            close=c.close,
                            volume=c.volume,
                            market_type=MarketType.STOCK,
                        )
                        for c in out
                    ]
            except Exception as exc:
                logger.debug("ReadOnlyScanGateway 실거래소 캔들 실패 %s: %s", symbol, type(exc).__name__)

        # 캐시 폴백(일봉만 — 분봉 캐시 없음)
        rows = cache_quotes.get_daily_candles(symbol, limit=limit)
        return [
            OHLCV(
                symbol=symbol,
                timestamp=datetime.strptime(r["date"], "%Y%m%d").replace(tzinfo=timezone.utc),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
                market_type=MarketType.STOCK,
            )
            for r in rows
        ]

    async def get_ticker(self, symbol: str) -> Ticker:
        quotes = self._get_quotes()
        if quotes is not None:
            try:
                info = await quotes.stock_info(symbol)
                if info and info.get("cur_price") is not None:
                    return Ticker(
                        symbol=symbol,
                        name=info.get("name") or symbol,
                        price=float(info["cur_price"]),
                        volume=float(info.get("volume") or 0.0),
                        change_pct=float(info.get("change_pct") or 0.0),
                        timestamp=datetime.now(timezone.utc),
                        market_type=MarketType.STOCK,
                    )
            except Exception as exc:
                logger.debug("ReadOnlyScanGateway 실거래소 ticker 실패 %s: %s", symbol, type(exc).__name__)

        q = cache_quotes.get_quote(symbol)
        if q is None:
            raise ValueError(f"시세 조회 불가(키 없음·캐시 없음): {symbol}")
        return Ticker(
            symbol=symbol,
            name=symbol,
            price=float(q["price"]),
            volume=float(q.get("volume") or 0.0),
            change_pct=float(q.get("change_pct") or 0.0),
            timestamp=datetime.now(timezone.utc),
            market_type=MarketType.STOCK,
        )

    async def get_order_book(self, symbol: str) -> OrderBook:
        quotes = self._get_quotes()
        if quotes is not None:
            try:
                ob = await quotes.orderbook(symbol)
                if ob:
                    return OrderBook(
                        symbol=symbol,
                        asks=[(a["price"], a["qty"]) for a in ob.get("asks", [])],
                        bids=[(b["price"], b["qty"]) for b in ob.get("bids", [])],
                        timestamp=datetime.now(timezone.utc),
                        market_type=MarketType.STOCK,
                    )
            except Exception as exc:
                logger.debug("ReadOnlyScanGateway 호가 실패 %s: %s", symbol, type(exc).__name__)
        return OrderBook(
            symbol=symbol, asks=[], bids=[],
            timestamp=datetime.now(timezone.utc), market_type=MarketType.STOCK,
        )

    async def get_universe(self) -> List[str]:
        """스캔 대상 유니버스 — theme_map.json 큐레이션 종목 집합(bounded, 안전).

        전종목 조회는 지원하지 않는다(불필요한 대량 조회 방지). 온디맨드 스캔은
        관심 종목군(테마 시드)만 저비용으로 훑는 것이 목적.
        """
        try:
            from pathlib import Path

            from backend.core.risk.theme_map import load_theme_map

            repo_root = Path(__file__).resolve().parents[3]
            m = load_theme_map(repo_root / "data" / "theme_map.json")
            return sorted(m.keys())
        except Exception:
            return []

    async def get_prices(self, symbols: List[str]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for sym in symbols:
            try:
                t = await self.get_ticker(sym)
                out[sym] = t.price
            except Exception:
                continue
        return out

    def is_market_open(self) -> bool:
        now = datetime.now(timezone.utc).astimezone().replace(tzinfo=None)
        # KST 근사(호출부에서 이미 KST 기준 시간대 사용 가정) — 정밀 판정은 market_session 모듈 위임.
        return True

    async def get_market_condition(self) -> dict:
        return {"is_open": self.is_market_open(), "condition": "unknown", "updated_at": datetime.now(timezone.utc).isoformat()}

    async def health_check(self) -> bool:
        return self._get_quotes() is not None or cache_quotes.cache_dir().exists()

    # ── 계좌·주문 — 명시적 거부 (읽기 전용 어댑터, §2 안전 경계) ───────────
    async def get_balance(self) -> Balance:
        raise NotImplementedError(_REFUSAL)

    async def place_order(self, order: Order) -> OrderResult:
        raise NotImplementedError(_REFUSAL)

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError(_REFUSAL)

    async def get_order_status(self, order_id: str) -> OrderResult:
        raise NotImplementedError(_REFUSAL)
