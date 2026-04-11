"""
KiwoomGateway — 키움증권 Open API 연동

구현사항:
- OAuth2 토큰 자동 갱신
- 레이트리밋 (초당 5회, 분당 100회)
- 재연결 로직 + health_check
- 모의투자/실거래 모드 분기
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import deque
from urllib.parse import urlencode

from backend.core.gateway.base import MarketGateway
from backend.models.market import OHLCV, OrderBook, Ticker, MarketType
from backend.models.position import Balance, Order, OrderResult, OrderSide, OrderStatus
from backend.models.config import KiwoomConfig

logger = logging.getLogger(__name__)


class RateLimiter:
    """레이트리밋 관리 (초당 5회, 분당 100회)"""

    def __init__(self, per_sec: int = 5, per_min: int = 100):
        self.per_sec = per_sec
        self.per_min = per_min
        self.sec_queue: deque = deque()  # (timestamp, 1)
        self.min_queue: deque = deque()  # (timestamp, 1)

    async def acquire(self) -> None:
        """레이트리밋 대기"""
        now = time.time()

        # 1분 이상 지난 요청 제거
        while self.min_queue and self.min_queue[0] < now - 60:
            self.min_queue.popleft()

        # 1초 이상 지난 요청 제거
        while self.sec_queue and self.sec_queue[0] < now - 1:
            self.sec_queue.popleft()

        # 분당 제한 체크
        if len(self.min_queue) >= self.per_min:
            wait_until = self.min_queue[0] + 60
            await asyncio.sleep(wait_until - now)
            return await self.acquire()

        # 초당 제한 체크
        if len(self.sec_queue) >= self.per_sec:
            wait_until = self.sec_queue[0] + 1
            await asyncio.sleep(wait_until - now)
            return await self.acquire()

        # 요청 기록
        now = time.time()
        self.sec_queue.append(now)
        self.min_queue.append(now)


class KiwoomGateway(MarketGateway):
    """키움증권 Open API 게이트웨이"""

    market_type = MarketType.STOCK

    def __init__(self, config: KiwoomConfig):
        self.config = config
        self.base_url = config.base_url
        self.app_key = config.app_key
        self.app_secret = config.app_secret
        self.account_no = config.account_no
        self.mock = config.mock

        # 토큰 관리
        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.token_lock = asyncio.Lock()

        # 레이트리밋
        self.rate_limiter = RateLimiter()

        # 재연결 설정
        self.max_retries = 3
        self.retry_delay = 2  # 초

        # 마켓 상태
        self._is_market_open = False

    async def __aenter__(self):
        """async with 지원"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """async with 정리"""
        await self.close()

    async def close(self) -> None:
        """리소스 정리"""
        pass

    async def authenticate(self) -> None:
        """OAuth2 토큰 갱신"""
        async with self.token_lock:
            # 토큰 유효 여부 확인
            if self.access_token and self.token_expiry:
                if datetime.now() < self.token_expiry - timedelta(minutes=5):
                    return

            await self._refresh_token()

    async def _refresh_token(self) -> None:
        """토큰 갱신"""
        # 모의투자 모드에서는 임시 토큰 사용
        if self.mock:
            self.access_token = f"mock_token_{int(time.time())}"
            self.token_expiry = datetime.now() + timedelta(hours=1)
            logger.info("Mock token generated for paper trading")
            return

        # 실거래 모드: 실제 API 호출
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )

            # asyncio에서 blocking I/O를 실행
            loop = asyncio.get_event_loop()
            resp_data = await loop.run_in_executor(
                None, self._make_request, request
            )

            response = json.loads(resp_data)
            self.access_token = response["access_token"]
            expires_in = response.get("expires_in", 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in)
            logger.info(f"Token refreshed, expires in {expires_in}s")
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            raise

    def _make_request(self, request: urllib.request.Request) -> str:
        """Blocking HTTP 요청 (executor에서 실행)"""
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            raise Exception(f"HTTP {e.code}: {body}")

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        retry: int = 0,
    ) -> Dict:
        """API 요청 (레이트리밋 + 재시도 포함)"""
        await self.authenticate()
        await self.rate_limiter.acquire()

        url = f"{self.base_url}{endpoint}"
        if params:
            url += "?" + urlencode(params)

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            data = None
            if json_data:
                data = json.dumps(json_data).encode("utf-8")

            request = urllib.request.Request(url, data=data, headers=headers, method=method)

            # asyncio에서 blocking I/O를 실행
            loop = asyncio.get_event_loop()
            resp_text = await loop.run_in_executor(None, self._make_request, request)
            response = json.loads(resp_text)

            return response

        except Exception as e:
            if "401" in str(e) and retry < self.max_retries:
                logger.warning("Token expired, refreshing...")
                async with self.token_lock:
                    self.access_token = None
                    self.token_expiry = None
                await asyncio.sleep(self.retry_delay)
                return await self._request(method, endpoint, params, json_data, retry + 1)

            if retry < self.max_retries:
                logger.warning(f"Request failed: {e}, retrying ({retry + 1}/{self.max_retries})...")
                await asyncio.sleep(self.retry_delay)
                return await self._request(method, endpoint, params, json_data, retry + 1)
            raise

    # ── 시장 데이터 ────────────────────────────────────────────────────────

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> List[OHLCV]:
        """OHLCV 데이터 조회"""
        if self.mock:
            return self._mock_ohlcv(symbol, timeframe, limit)

        endpoint = f"/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        params = {
            "FID_COND_MRKT_DIV_CODE": "0",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": "",
            "FID_INPUT_DATE_2": "",
            "FID_PERIOD_DIV_CODE": self._timeframe_to_code(timeframe),
            "FID_OUTPUT_REC_NUM": str(limit),
        }

        try:
            data = await self._request("GET", endpoint, params=params)
            return self._parse_ohlcv(data, symbol)
        except Exception as e:
            logger.error(f"Failed to get OHLCV for {symbol}: {e}")
            return []

    async def get_ticker(self, symbol: str) -> Ticker:
        """현재가 조회"""
        if self.mock:
            return self._mock_ticker(symbol)

        endpoint = f"/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "0",
            "FID_INPUT_ISCD": symbol,
        }

        data = await self._request("GET", endpoint, params=params)
        return self._parse_ticker(data, symbol)

    async def get_order_book(self, symbol: str) -> OrderBook:
        """호가 조회"""
        if self.mock:
            return self._mock_order_book(symbol)

        endpoint = f"/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
        params = {
            "FID_COND_MRKT_DIV_CODE": "0",
            "FID_INPUT_ISCD": symbol,
        }

        data = await self._request("GET", endpoint, params=params)
        return self._parse_order_book(data, symbol)

    # ── 계좌 ──────────────────────────────────────────────────────────────

    async def get_balance(self) -> Balance:
        """잔고 조회"""
        if self.mock:
            return self._mock_balance()

        endpoint = f"/uapi/domestic-stock/v1/trading/inquire-account"
        params = {
            "CANO": self.account_no[:8],
            "ACNT_PRDT_CD": self.account_no[8:],
            "INQR_DVSN_1": "0",
            "INQR_DVSN_2": "0",
            "UNPR_DVSN": "01",
            "FUND_SELT_SEN_DVSN": "01",
            "FNCG_AMT_AUTO_RDPT_YN": "Y",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        data = await self._request("GET", endpoint, params=params)
        return self._parse_balance(data)

    # ── 주문 ──────────────────────────────────────────────────────────────

    async def place_order(self, order: Order) -> OrderResult:
        """주문 실행"""
        if self.mock:
            return self._mock_place_order(order)

        endpoint = f"/uapi/domestic-stock/v1/trading/order-cash"
        payload = {
            "CANO": self.account_no[:8],
            "ACNT_PRDT_CD": self.account_no[8:],
            "PDNO": order.symbol,
            "ORD_QTY": str(int(order.quantity)),
            "ORD_UNPR": str(int(order.price)) if order.price else "0",
            "ORD_DVSN": "00" if order.order_type.value == "market" else "01",
            "CMA_EVALU_AMT_ICLD_YN": "Y",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_MGNO": "",
            "CTAC_TLNO": "",
            "CTAC_TLNO2": "",
            "NEW_PRODRUCT_YN": "N",
            "HTS_FILLER": "",
            "BF_ORD_ORGNO": "",
        }

        # 주문 방향 결정
        if order.side == OrderSide.BUY:
            payload["ORD_DVSN"] = "00" if order.order_type.value == "market" else "01"
        else:
            payload["ORD_DVSN"] = "02" if order.order_type.value == "market" else "03"

        data = await self._request("POST", endpoint, json_data=payload)
        return self._parse_order_result(data, order)

    async def cancel_order(self, order_id: str) -> bool:
        """주문 취소"""
        if self.mock:
            return True

        endpoint = f"/uapi/domestic-stock/v1/trading/order-rvsecnd"
        payload = {
            "CANO": self.account_no[:8],
            "ACNT_PRDT_CD": self.account_no[8:],
            "ORD_ORGNO": order_id.split("-")[0],
            "ORD_ORGNO_ISCD": order_id.split("-")[1] if "-" in order_id else "00",
            "KRX_FWDG_ORD_ORGNO": "",
            "ORD_ISNO": order_id,
            "ORD_PRC": "",
            "QTY_CANCL_TYPE": "0",
            "CFRM_DVSN": "00",
            "RSVN_ORD_ODNO": "",
        }

        try:
            await self._request("POST", endpoint, json_data=payload)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_order_status(self, order_id: str) -> OrderResult:
        """주문 상태 조회"""
        if self.mock:
            return self._mock_order_status(order_id)

        endpoint = f"/uapi/domestic-stock/v1/trading/inquire-order"
        params = {
            "CANO": self.account_no[:8],
            "ACNT_PRDT_CD": self.account_no[8:],
            "ORD_SELN_DT": "",
            "SRT_ID": "",
            "SRT_ID2": "",
            "ORD_STS_DVSN": "00",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "ORD_ISNO": order_id,
            "INQR_DVSN": "00",
        }

        data = await self._request("GET", endpoint, params=params)
        return self._parse_order_result_from_status(data, order_id)

    # ── 유니버스 ──────────────────────────────────────────────────────────

    async def get_universe(self) -> List[str]:
        """전종목 리스트 (KOSPI+KOSDAQ)"""
        if self.mock:
            return [
                "005930",  # 삼성전자
                "000660",  # SK하이닉스
                "051910",  # LG화학
                "035420",  # NAVER
                "035720",  # 카카오
            ]

        endpoint = f"/uapi/domestic-stock/v1/quotations/search-stock-info"
        params = {"MKSC_SHRN_ISCD": "0"}

        try:
            data = await self._request("GET", endpoint, params=params)
            return self._parse_universe(data)
        except Exception as e:
            logger.error(f"Failed to get universe: {e}")
            return []

    # ── 시장 상태 ─────────────────────────────────────────────────────────

    def is_market_open(self) -> bool:
        """현재 거래 시간 여부 (한국시간 09:00~15:30)"""
        if self.mock:
            return True

        now = datetime.now()
        weekday = now.weekday()

        # 월-금 (0-4)
        if weekday > 4:
            return False

        hour = now.hour
        minute = now.minute

        # 09:00~15:30
        if hour < 9:
            return False
        if hour == 9 and minute < 0:
            return False
        if hour >= 15 and (hour > 15 or minute > 30):
            return False

        return True

    async def health_check(self) -> bool:
        """API 연결 상태 확인"""
        try:
            if self.mock:
                return True

            await self.authenticate()
            # 간단한 API 호출로 연결 확인
            _ = await self.get_ticker("005930")
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    # ── 헬퍼 메서드 ──────────────────────────────────────────────────────────

    def _timeframe_to_code(self, timeframe: str) -> str:
        """timeframe을 키움 API 코드로 변환"""
        mapping = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "1d": "D",
            "1w": "W",
            "1M": "M",
        }
        return mapping.get(timeframe, "D")

    def _parse_ohlcv(self, data: Dict, symbol: str) -> List[OHLCV]:
        """응답 데이터를 OHLCV로 변환"""
        try:
            candles = data.get("output2", [])
            result = []
            for candle in candles:
                result.append(
                    OHLCV(
                        symbol=symbol,
                        timestamp=datetime.strptime(candle["stck_bsop_date"], "%Y%m%d"),
                        open=float(candle["stck_oprc"]),
                        high=float(candle["stck_hgpr"]),
                        low=float(candle["stck_lwpr"]),
                        close=float(candle["stck_clpr"]),
                        volume=float(candle["acml_vol"]),
                        market_type=MarketType.STOCK,
                    )
                )
            return result
        except Exception as e:
            logger.error(f"Failed to parse OHLCV: {e}")
            return []

    def _parse_ticker(self, data: Dict, symbol: str) -> Ticker:
        """응답 데이터를 Ticker로 변환"""
        output = data.get("output", {})
        return Ticker(
            symbol=symbol,
            name=output.get("hts_kor_isnm", ""),
            price=float(output.get("stck_prpr", 0)),
            volume=float(output.get("acml_vol", 0)),
            change_pct=float(output.get("prdy_ctrt", 0)) / 100,
            timestamp=datetime.now(),
            market_type=MarketType.STOCK,
        )

    def _parse_order_book(self, data: Dict, symbol: str) -> OrderBook:
        """응답 데이터를 OrderBook으로 변환"""
        output = data.get("output", {})

        asks = []
        bids = []

        for i in range(1, 11):
            ask_price = float(output.get(f"askp{i}", 0))
            ask_qty = float(output.get(f"askp_rsqn{i}", 0))
            if ask_price > 0:
                asks.append((ask_price, ask_qty))

            bid_price = float(output.get(f"bidp{i}", 0))
            bid_qty = float(output.get(f"bidp_rsqn{i}", 0))
            if bid_price > 0:
                bids.append((bid_price, bid_qty))

        return OrderBook(
            symbol=symbol,
            asks=asks,
            bids=bids,
            timestamp=datetime.now(),
            market_type=MarketType.STOCK,
        )

    def _parse_balance(self, data: Dict) -> Balance:
        """응답 데이터를 Balance로 변환"""
        output = data.get("output1", {})
        output2 = data.get("output2", [])

        total_value = float(output.get("scts_evlu_amt", 0))
        available_cash = float(output.get("cashavl_amt", 0))
        invested_value = float(output.get("scts_tot_evlu_amt", 0))

        total_pnl = total_value - (available_cash + invested_value)
        total_pnl_pct = (total_pnl / total_value * 100) if total_value > 0 else 0

        return Balance(
            total_value=total_value,
            available_cash=available_cash,
            invested_value=invested_value,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            market_type=MarketType.STOCK,
            updated_at=datetime.now(),
        )

    def _parse_order_result(self, data: Dict, order: Order) -> OrderResult:
        """응답 데이터를 OrderResult로 변환 (주문 직후)"""
        output = data.get("output", {})
        return OrderResult(
            order_id=output.get("odno", ""),
            symbol=order.symbol,
            side=order.side,
            status=OrderStatus.PENDING,
            filled_quantity=0,
            avg_price=order.price or 0,
            market_type=MarketType.STOCK,
            timestamp=datetime.now(),
        )

    def _parse_order_result_from_status(self, data: Dict, order_id: str) -> OrderResult:
        """응답 데이터를 OrderResult로 변환 (상태 조회)"""
        outputs = data.get("output", [])
        if not outputs:
            return OrderResult(
                order_id=order_id,
                symbol="",
                side=OrderSide.BUY,
                status=OrderStatus.FAILED,
                filled_quantity=0,
                avg_price=0,
                market_type=MarketType.STOCK,
                timestamp=datetime.now(),
            )

        output = outputs[0]
        status_code = output.get("ord_st", "")
        status_map = {
            "00": OrderStatus.PENDING,
            "01": OrderStatus.FILLED,
            "02": OrderStatus.CANCELLED,
            "03": OrderStatus.PARTIAL,
        }

        return OrderResult(
            order_id=order_id,
            symbol=output.get("pdno", ""),
            side=OrderSide.BUY if output.get("sll_buy_dvsn") == "02" else OrderSide.SELL,
            status=status_map.get(status_code, OrderStatus.PENDING),
            filled_quantity=float(output.get("qty_filled", 0)),
            avg_price=float(output.get("avg_price", 0)),
            market_type=MarketType.STOCK,
            timestamp=datetime.now(),
        )

    def _parse_universe(self, data: Dict) -> List[str]:
        """응답 데이터를 종목 리스트로 변환"""
        symbols = []
        outputs = data.get("output", [])
        for output in outputs:
            symbol = output.get("symbol", "")
            if symbol:
                symbols.append(symbol)
        return symbols

    # ── Mock 메서드 (테스트용) ──────────────────────────────────────────────

    def _mock_ohlcv(self, symbol: str, timeframe: str, limit: int) -> List[OHLCV]:
        """Mock OHLCV 데이터"""
        result = []
        base_price = 50000.0
        for i in range(limit):
            date = datetime.now() - timedelta(days=limit - i)
            result.append(
                OHLCV(
                    symbol=symbol,
                    timestamp=date,
                    open=base_price + i * 100,
                    high=base_price + i * 100 + 500,
                    low=base_price + i * 100 - 500,
                    close=base_price + i * 100 + 250,
                    volume=1000000,
                    market_type=MarketType.STOCK,
                )
            )
        return result

    def _mock_ticker(self, symbol: str) -> Ticker:
        """Mock 현재가 데이터"""
        return Ticker(
            symbol=symbol,
            name="Sample Stock",
            price=70000.0,
            volume=1000000.0,
            change_pct=0.5,
            timestamp=datetime.now(),
            market_type=MarketType.STOCK,
        )

    def _mock_order_book(self, symbol: str) -> OrderBook:
        """Mock 호가 데이터"""
        return OrderBook(
            symbol=symbol,
            asks=[(70100, 1000), (70200, 2000), (70300, 3000)],
            bids=[(70000, 1000), (69900, 2000), (69800, 3000)],
            timestamp=datetime.now(),
            market_type=MarketType.STOCK,
        )

    def _mock_balance(self) -> Balance:
        """Mock 잔고 데이터"""
        return Balance(
            total_value=10000000.0,
            available_cash=5000000.0,
            invested_value=5000000.0,
            total_pnl=500000.0,
            total_pnl_pct=5.0,
            market_type=MarketType.STOCK,
            updated_at=datetime.now(),
        )

    def _mock_place_order(self, order: Order) -> OrderResult:
        """Mock 주문 결과"""
        return OrderResult(
            order_id=f"MOCK_{int(time.time())}",
            symbol=order.symbol,
            side=order.side,
            status=OrderStatus.PENDING,
            filled_quantity=0,
            avg_price=order.price or 0,
            market_type=MarketType.STOCK,
            timestamp=datetime.now(),
        )

    def _mock_order_status(self, order_id: str) -> OrderResult:
        """Mock 주문 상태"""
        return OrderResult(
            order_id=order_id,
            symbol="005930",
            side=OrderSide.BUY,
            status=OrderStatus.FILLED,
            filled_quantity=10.0,
            avg_price=70000.0,
            market_type=MarketType.STOCK,
            timestamp=datetime.now(),
        )
