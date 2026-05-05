"""
키움 REST API 클라이언트
api.kiwoom.com / mockapi.kiwoom.com 기반 HTTP REST API 래퍼

참고: 키움 REST API는 2025년 3월 출시, Python/크로스플랫폼 지원
- 별도 프로그램 설치 없이 HTTP 요청으로 주식 매매 가능
- IP 화이트리스트 기반 보안 (openapi.kiwoom.com에서 등록)
- 모든 데이터 조회/주문이 POST 방식
"""

import os
import time
import logging
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any, Set

import httpx
import pandas as pd

logger = logging.getLogger(__name__)


class KiwoomRestAPI:
    """키움 REST API 클라이언트"""

    # 엔드포인트
    ENDPOINTS = {
        'token':       '/oauth2/token',
        'stock_info':  '/api/dostk/stkinfo',   # ka10001(현재가), ka10099(종목리스트), ka10003(체결)
        'market_cond': '/api/dostk/mrkcond',    # ka10005(일봉)
        'chart':       '/api/dostk/chart',      # ka10081(일봉차트조회) — 대량 과거 데이터
        'order':       '/api/dostk/ordr',       # kt10000(매수), kt10001(매도), kt10002(정정), kt10003(취소)
        'account':     '/api/dostk/acnt',       # kt00004(계좌평가), kt00009(주문체결현황)
        'sector':      '/api/dostk/sect',       # ka20001(업종현재가), ka20009(업종일별)
    }

    def __init__(self, config: dict):
        kiwoom_config = config.get('kiwoom', {})
        self.mode = config.get('mode', 'simulation')

        # 모의투자 / 실거래 URL 분기
        if self.mode == 'simulation':
            self.base_url = kiwoom_config.get(
                'sim_base_url', 'https://mockapi.kiwoom.com')
        else:
            self.base_url = kiwoom_config.get(
                'base_url', 'https://api.kiwoom.com')

        self.app_key = os.getenv(
            'KIWOOM_APP_KEY', kiwoom_config.get('app_key', ''))
        self.app_secret = os.getenv(
            'KIWOOM_APP_SECRET', kiwoom_config.get('app_secret', ''))
        self.account_no = os.getenv(
            'KIWOOM_ACCOUNT_NO', kiwoom_config.get('account_no', ''))

        # Rate limiting
        rate_limit = kiwoom_config.get('rate_limit', {})
        self._rate_per_second = rate_limit.get('per_second', 5)
        self._last_request_time = 0.0

        # Access token
        self._access_token: Optional[str] = None
        self._token_expires: float = 0.0
        self._token_retrying: bool = False

        # HTTP client
        self._client: Optional[httpx.AsyncClient] = None

        # API 호출 카운터 (일일 한도 관리)
        self._api_call_counts: dict = {}  # {api_id: count}
        self._call_count_date: str = ''   # 카운트 리셋 날짜
        self._quota_exhausted: Set[str] = set()  # 한도 초과된 API ID

    async def initialize(self):
        """API 초기화 및 토큰 발급"""
        self._client = httpx.AsyncClient(timeout=30.0)
        await self._get_access_token()
        logger.info(f"키움 REST API 초기화 완료 (모드: {self.mode}, URL: {self.base_url})")

    async def close(self):
        """리소스 정리"""
        if self._client:
            await self._client.aclose()

    # =========================================================================
    # 인증
    # =========================================================================

    async def _get_access_token(self):
        """OAuth2 토큰 발급"""
        url = f"{self.base_url}{self.ENDPOINTS['token']}"
        data = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }

        resp = await self._client.post(url, json=data)
        resp.raise_for_status()
        result = resp.json()

        self._access_token = result.get('token')
        # expires_dt: "YYYYMMDDHHmmss" 형식
        expires_dt = result.get('expires_dt', '')
        if expires_dt:
            try:
                exp_time = datetime.strptime(expires_dt, '%Y%m%d%H%M%S')
                self._token_expires = exp_time.timestamp() - 60
            except ValueError:
                self._token_expires = time.time() + 86400 - 60
        else:
            self._token_expires = time.time() + 86400 - 60

        logger.info("키움 API 토큰 발급 완료")

    async def _ensure_token(self):
        """토큰 유효성 확인 및 갱신"""
        if time.time() >= self._token_expires:
            await self._get_access_token()

    def _headers(self, api_id: str, cont_yn: str = "N",
                 next_key: str = "") -> dict:
        """API 요청 헤더 생성"""
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
        }
        if next_key:
            headers["next-key"] = next_key
        return headers

    async def _rate_limit(self):
        """호출 제한 (모의투자 서버 기준 초당 2회 안전 마진)"""
        elapsed = time.time() - self._last_request_time
        # 설정값과 관계없이 최소 0.5초 간격 유지 (mockapi 429 방지)
        min_interval = max(1.0 / self._rate_per_second, 0.5)
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

    def _increment_api_count(self, api_id: str) -> int:
        """API 호출 카운트 증가, 현재 카운트 반환"""
        today = datetime.now().strftime('%Y%m%d')
        if self._call_count_date != today:
            self._api_call_counts.clear()
            self._quota_exhausted.clear()
            self._call_count_date = today
        self._api_call_counts[api_id] = self._api_call_counts.get(api_id, 0) + 1
        return self._api_call_counts[api_id]

    def get_api_call_count(self, api_id: str = None) -> dict:
        """API 호출 통계 조회 (외부에서 모니터링 용도)"""
        today = datetime.now().strftime('%Y%m%d')
        if self._call_count_date != today:
            return {}
        if api_id:
            return {api_id: self._api_call_counts.get(api_id, 0)}
        return dict(self._api_call_counts)

    def is_quota_exhausted(self, api_id: str) -> bool:
        """특정 API의 한도 초과 여부"""
        return api_id in self._quota_exhausted

    async def _post(self, endpoint: str, api_id: str, data: dict,
                    cont_yn: str = "N", next_key: str = "") -> dict:
        """POST 요청 (키움 REST API는 모두 POST, 429 자동 재시도, 한도 초과 서킷 브레이커)"""
        # 서킷 브레이커: 한도 초과된 API는 즉시 빈 응답 반환
        if api_id in self._quota_exhausted:
            return {'return_code': 5, 'return_msg': '일일 한도 초과 (서킷 브레이커)',
                    '_cont_yn': 'N', '_next_key': ''}

        await self._ensure_token()
        await self._rate_limit()

        url = f"{self.base_url}{endpoint}"
        headers = self._headers(api_id, cont_yn, next_key)

        max_retries = 5
        for attempt in range(max_retries):
            resp = await self._client.post(url, headers=headers, json=data)
            if resp.status_code == 429:
                wait = min(2 ** attempt, 16)
                logger.warning(f"429 Rate limit [{api_id}], {wait}s 후 재시도 ({attempt+1}/{max_retries})")
                await asyncio.sleep(wait)
                self._last_request_time = time.time()
                continue
            resp.raise_for_status()
            self._last_request_time = time.time()
            break
        else:
            resp.raise_for_status()

        count = self._increment_api_count(api_id)
        result = resp.json()

        # 공통 에러 체크
        return_code = result.get('return_code')
        if return_code is not None and int(return_code) != 0:
            return_msg = result.get('return_msg', '')
            rc = int(return_code)

            # 한도 초과(code=5) → 서킷 브레이커 활성화
            if rc == 5:
                self._quota_exhausted.add(api_id)
                logger.error(
                    f"API 일일 한도 초과 [{api_id}] (호출 {count}회) → 서킷 브레이커 활성화. "
                    f"자정까지 해당 API 차단됨")
            # 인증 실패(code=3) → 토큰 재발급 후 1회 재시도
            elif rc == 3 and not self._token_retrying:
                self._token_retrying = True
                logger.info(f"토큰 무효화 감지 → 재발급 후 재시도 [{api_id}] (호출 {count}회)")
                try:
                    await self._get_access_token()
                    headers = self._headers(api_id, cont_yn, next_key)
                    await self._rate_limit()
                    resp = await self._client.post(url, headers=headers, json=data)
                    resp.raise_for_status()
                    self._last_request_time = time.time()
                    result = resp.json()
                    rc2 = result.get('return_code')
                    if rc2 is not None and int(rc2) != 0:
                        logger.warning(f"토큰 재발급 후에도 오류 [{api_id}]: code={rc2}")
                        # 재발급 후에도 code=5면 서킷 브레이커
                        if int(rc2) == 5:
                            self._quota_exhausted.add(api_id)
                            logger.error(f"토큰 재발급 후 한도 초과 확인 [{api_id}] → 서킷 브레이커 활성화")
                        # 재발급 후에도 code=3이면 한도 문제일 수 있음 (연속 3 실패 시 서킷 브레이커)
                        elif int(rc2) == 3:
                            auth_fail_key = f'_auth_fail_{api_id}'
                            fails = getattr(self, auth_fail_key, 0) + 1
                            setattr(self, auth_fail_key, fails)
                            if fails >= 3:
                                self._quota_exhausted.add(api_id)
                                logger.error(
                                    f"연속 인증 실패 {fails}회 [{api_id}] → 서킷 브레이커 활성화 "
                                    f"(일일 한도 초과 의심)")
                finally:
                    self._token_retrying = False
                result['_cont_yn'] = resp.headers.get('cont-yn', 'N')
                result['_next_key'] = resp.headers.get('next-key', '')
                return result
            else:
                logger.warning(f"API 응답 오류 [{api_id}]: code={return_code}, msg={return_msg}")

        # 인증 성공 시 연속 실패 카운트 리셋
        if return_code is None or int(return_code) == 0:
            auth_fail_key = f'_auth_fail_{api_id}'
            if getattr(self, auth_fail_key, 0) > 0:
                setattr(self, auth_fail_key, 0)

        # 호출 수 마일스톤 로깅
        if count in (500, 1000, 1500, 2000):
            logger.warning(f"API 호출 수 경고 [{api_id}]: {count}회 (일일 누적)")

        # 응답 헤더의 페이징 정보를 결과에 주입
        result['_cont_yn'] = resp.headers.get('cont-yn', 'N')
        result['_next_key'] = resp.headers.get('next-key', '')

        return result

    @staticmethod
    def _strip_code_prefix(code: str) -> str:
        """종목코드에서 알파벳 접두어 제거 (예: 'A005860' → '005860')"""
        return code.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ') if code else code

    @staticmethod
    def _parse_int(value: Any, default: int = 0) -> int:
        """문자열 → 정수 변환 (부호 포함)"""
        if value is None:
            return default
        try:
            return int(str(value).replace(',', ''))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _parse_float(value: Any, default: float = 0.0) -> float:
        """문자열 → 실수 변환"""
        if value is None:
            return default
        try:
            return float(str(value).replace(',', ''))
        except (ValueError, TypeError):
            return default

    # =========================================================================
    # 시세 조회
    # =========================================================================

    async def get_stock_codes(self, market_code: str = "0") -> List[tuple]:
        """
        전종목 코드 조회

        Args:
            market_code: "0" = KOSPI, "10" = KOSDAQ

        Returns:
            [(code, name), ...]
        """
        all_items = []
        cont_yn = "N"
        next_key = ""

        while True:
            result = await self._post(
                self.ENDPOINTS['stock_info'],
                api_id="ka10099",
                data={"mrkt_tp": market_code},
                cont_yn=cont_yn,
                next_key=next_key,
            )

            items = result.get('list', [])
            all_items.extend(items)

            # 페이징 처리
            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        return [(item.get('code', ''), item.get('name', '')) for item in all_items]

    async def get_stock_list_with_meta(self, market_code: str = "0") -> List[dict]:
        """
        전종목 코드 + 메타정보 조회 (ka10099)

        Args:
            market_code: "0" = KOSPI, "10" = KOSDAQ

        Returns:
            [{"code", "name", "state", "auditInfo", "orderWarning", "lastPrice", ...}, ...]
        """
        all_items = []
        cont_yn = "N"
        next_key = ""

        while True:
            result = await self._post(
                self.ENDPOINTS['stock_info'],
                api_id="ka10099",
                data={"mrkt_tp": market_code},
                cont_yn=cont_yn,
                next_key=next_key,
            )

            items = result.get('list', [])
            for item in items:
                all_items.append({
                    'code': item.get('code', ''),
                    'name': item.get('name', ''),
                    'state': item.get('state', ''),
                    'auditInfo': item.get('auditInfo', ''),
                    'orderWarning': item.get('orderWarning', ''),
                    'lastPrice': self._parse_int(item.get('lastPrice')),
                })

            # 페이징 처리
            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        return all_items

    async def get_realtime_stock_rank(
        self,
        market: str = "0",
        qry_tp: str = "2",
    ) -> List[dict]:
        """
        실시간 종목 조회 순위 (ka00198)

        단일 API 호출로 거래량/상승률 상위 종목을 한 번에 조회.
        leading_stocks의 200회 개별 API 호출을 대체하여 스캔 속도 극대화.

        Args:
            market: "0"=KOSPI, "10"=KOSDAQ
            qry_tp: 조회 기간 — "1"=1분, "2"=10분, "3"=1시간, "4"=당일누적, "5"=30초

        Returns:
            [{"code": str, "name": str, "price": int, "change_pct": float,
              "volume": int, "trade_value": float, "open": int,
              "high": int, "low": int, "prev_close": int}, ...]
        """
        result = await self._post(
            self.ENDPOINTS['stock_info'],
            api_id="ka00198",
            data={"mrkt_tp": market, "qry_tp": qry_tp},
        )

        items = result.get('list', [])
        if not items:
            # 응답 키가 다를 수 있으므로 리스트 타입 탐색
            for key in ('output', 'data', 'stk_rank', 'rank_list'):
                if key in result and isinstance(result[key], list):
                    items = result[key]
                    break

        parsed = []
        for item in items:
            code = item.get('stk_cd') or item.get('code') or ''
            code = self._strip_code_prefix(code)
            if not code or len(code) != 6:
                continue

            cur_price = abs(self._parse_int(
                item.get('cur_prc') or item.get('price')))
            if cur_price <= 0:
                continue

            parsed.append({
                'code': code,
                'name': item.get('stk_nm') or item.get('name') or '',
                'price': cur_price,
                'open': abs(self._parse_int(
                    item.get('open_pric') or item.get('open') or 0)),
                'high': abs(self._parse_int(
                    item.get('high_pric') or item.get('high') or 0)),
                'low': abs(self._parse_int(
                    item.get('low_pric') or item.get('low') or 0)),
                'prev_close': abs(self._parse_int(
                    item.get('prev_close') or item.get('ysdy_clpr') or 0)),
                'change_pct': self._parse_float(
                    item.get('flu_rt') or item.get('change_pct')),
                'volume': self._parse_int(
                    item.get('trde_qty') or item.get('volume')),
                'trade_value': self._parse_float(
                    item.get('trde_amt') or item.get('trade_value')),
            })

        logger.info(
            f"ka00198 실시간 순위 조회 완료: {len(parsed)}종목 "
            f"(market={market}, qry_tp={qry_tp})")
        return parsed

    async def get_current_price(self, code: str) -> dict:
        """
        현재가 조회

        Returns:
            {
                'price': int,         # 현재가
                'open': int,          # 시가
                'high': int,          # 고가
                'low': int,           # 저가
                'volume': int,        # 거래량
                'change_pct': float,  # 등락률
            }
        """
        result = await self._post(
            self.ENDPOINTS['stock_info'],
            api_id="ka10001",
            data={"stk_cd": code},
        )

        return {
            'price': abs(self._parse_int(result.get('cur_prc'))),
            'open': abs(self._parse_int(result.get('open_pric'))),
            'high': abs(self._parse_int(result.get('high_pric'))),
            'low': abs(self._parse_int(result.get('low_pric'))),
            'volume': self._parse_int(result.get('trde_qty')),
            'change_pct': self._parse_float(result.get('flu_rt')),
        }

    async def get_daily_ohlcv(self, code: str, count: int = 500) -> Optional[pd.DataFrame]:
        """
        일봉 OHLCV 데이터 조회

        Args:
            code: 종목코드
            count: 조회 일수

        Returns:
            DataFrame with columns: date, open, high, low, close, volume
        """
        all_items = []
        cont_yn = "N"
        next_key = ""

        while len(all_items) < count:
            result = await self._post(
                self.ENDPOINTS['market_cond'],
                api_id="ka10005",
                data={"stk_cd": code},
                cont_yn=cont_yn,
                next_key=next_key,
            )

            items = result.get('stk_ddwkmm', [])
            if not items:
                break

            all_items.extend(items)

            # 페이징 처리
            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        if not all_items:
            return None

        records = []
        for item in all_items[:count]:
            try:
                records.append({
                    'date': item.get('date', ''),
                    'open': abs(self._parse_int(item.get('open_pric'))),
                    'high': abs(self._parse_int(item.get('high_pric'))),
                    'low': abs(self._parse_int(item.get('low_pric'))),
                    'close': abs(self._parse_int(item.get('close_pric'))),
                    'volume': self._parse_int(item.get('trde_qty')),
                })
            except (ValueError, TypeError):
                continue

        if not records:
            return None

        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        if len(df) < count:
            logger.debug(
                f"[{code}] OHLCV 요청 {count}일 → 실제 {len(df)}일 반환"
            )

        return df

    async def get_intraday_chart(
        self,
        code: str,
        tick_scope: int = 1,
        count: int = 120,
        base_dt: Optional[str] = None,
        max_pages: int = 10,
    ) -> List[dict]:
        """
        주식 분봉 차트 조회 (ka10080 — 주식분봉차트조회)

        Args:
            code: 종목코드
            tick_scope: 분봉 단위 (1=1분봉, 3=3분봉, 5=5분봉)
            count: 최대 조회 봉 수
            base_dt: 기준일자 YYYYMMDD (미지정 시 오늘 기준)
            max_pages: 연속조회 최대 페이지 (1페이지 ≈ 900건)

        Returns:
            [{'time': str, 'open': int, 'high': int, 'low': int,
              'price': int, 'volume': int}, ...]
        """
        all_records: List[dict] = []
        cont_yn = "N"
        next_key = ""
        items_key = None

        req_data = {
            "stk_cd": code,
            "tic_scope": str(tick_scope),
            "upd_stkpc_tp": "1",
        }
        if base_dt:
            req_data["base_dt"] = base_dt

        for page in range(max_pages):
            try:
                result = await self._post(
                    self.ENDPOINTS['chart'],
                    api_id="ka10080",
                    data=req_data,
                    cont_yn=cont_yn,
                    next_key=next_key,
                )
            except Exception as e:
                logger.warning(f"분봉차트 조회 실패 [{code}] page={page}: {e}")
                break

            # 첫 페이지에서 리스트 키 탐색
            if items_key is None:
                for key in ("stk_min_pole_chart_qry", "stk_tic_pole_chart_qry",
                             "output", "list", "data"):
                    if key in result and isinstance(result[key], list):
                        items_key = key
                        break
                if items_key is None:
                    logger.debug(f"[{code}] ka10080 응답 키 없음: {list(result.keys())}")
                    break

            items = result.get(items_key, [])
            if not items:
                break

            for item in items:
                try:
                    t = (item.get('cntr_tm') or item.get('time')
                         or item.get('stck_bsop_date') or '')
                    all_records.append({
                        'time': t,
                        'open': abs(self._parse_int(
                            item.get('open_pric') or item.get('strt_pric') or 0)),
                        'high': abs(self._parse_int(
                            item.get('high_pric') or item.get('hgpr') or 0)),
                        'low': abs(self._parse_int(
                            item.get('low_pric') or item.get('lwpr') or 0)),
                        'price': abs(self._parse_int(
                            item.get('cur_prc') or item.get('close_pric')
                            or item.get('clpr') or 0)),
                        'volume': self._parse_int(
                            item.get('trde_qty') or item.get('acml_vol') or 0),
                    })
                except (ValueError, TypeError):
                    continue

            if len(all_records) >= count:
                break

            # 연속조회 체크
            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        return all_records[:count]

    async def get_daily_ohlcv_historical(
        self, code: str, count: int = 500,
        base_dt: Optional[str] = None, max_pages: int = 20,
    ) -> Optional[pd.DataFrame]:
        """
        ka10081 (주식일봉차트조회) — 대량 과거 데이터 페이징 조회

        Args:
            code: 종목코드 (6자리)
            count: 필요한 일수
            base_dt: 기준일자 YYYYMMDD (미지정 시 오늘)
            max_pages: 최대 페이지 수 안전장치

        Returns:
            DataFrame(date, open, high, low, close, volume) or None (404/미지원)
        """
        if base_dt is None:
            base_dt = datetime.now().strftime('%Y%m%d')

        all_items: List[dict] = []
        cont_yn = "N"
        next_key = ""
        items_key = None  # 첫 응답에서 결정

        for page in range(max_pages):
            try:
                result = await self._post(
                    self.ENDPOINTS['chart'],
                    api_id="ka10081",
                    data={
                        "stk_cd": code,
                        "base_dt": base_dt,
                        "upd_stkpc_tp": "1",
                    },
                    cont_yn=cont_yn,
                    next_key=next_key,
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.debug(f"[{code}] ka10081 404 — 미지원 엔드포인트")
                    return None
                raise

            # 첫 페이지에서 리스트 키 탐색
            if items_key is None:
                for key in ("stk_dt_pole_chart_qry", "output", "list", "stk_ddwkmm", "data"):
                    if key in result and isinstance(result[key], list):
                        items_key = key
                        break
                if items_key is None:
                    logger.debug(f"[{code}] ka10081 응답에 리스트 키 없음: {list(result.keys())}")
                    return None

            items = result.get(items_key, [])
            if not items:
                break

            all_items.extend(items)

            if len(all_items) >= count:
                break

            # 페이징 계속
            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        if not all_items:
            return None

        # 필드 다중 폴백 매핑
        records = []
        for item in all_items[:count]:
            try:
                dt = (item.get('date') or item.get('stdr_dt')
                      or item.get('stck_bsop_date') or item.get('dt') or '')
                open_p = (item.get('open_pric') or item.get('strt_pric')
                          or item.get('oprc') or item.get('open') or 0)
                high_p = (item.get('high_pric') or item.get('hgpr')
                          or item.get('high') or 0)
                low_p = (item.get('low_pric') or item.get('lwpr')
                         or item.get('low') or 0)
                close_p = (item.get('close_pric') or item.get('cur_prc')
                           or item.get('clpr') or item.get('stck_clpr')
                           or item.get('close') or 0)
                vol = (item.get('trde_qty') or item.get('acml_vol')
                       or item.get('volume') or 0)

                if not dt:
                    continue

                records.append({
                    'date': dt,
                    'open': abs(self._parse_int(open_p)),
                    'high': abs(self._parse_int(high_p)),
                    'low': abs(self._parse_int(low_p)),
                    'close': abs(self._parse_int(close_p)),
                    'volume': self._parse_int(vol),
                })
            except (ValueError, TypeError):
                continue

        if not records:
            return None

        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        if len(df) < count:
            logger.debug(f"[{code}] ka10081 요청 {count}일 → 실제 {len(df)}일")

        return df

    async def get_index_ohlcv(self, index_code: str, count: int = 30) -> Optional[pd.DataFrame]:
        """지수 일봉 조회 (코스닥: 101, 코스피: 001)"""
        # 코스닥=101→mrkt_tp="1", 코스피=001→mrkt_tp="0"
        mrkt_tp = "1" if index_code == "101" else "0"

        all_items = []
        cont_yn = "N"
        next_key = ""

        while len(all_items) < count:
            result = await self._post(
                self.ENDPOINTS['sector'],
                api_id="ka20009",
                data={"mrkt_tp": mrkt_tp, "inds_cd": index_code},
                cont_yn=cont_yn,
                next_key=next_key,
            )

            items = result.get('list', [])
            if not items:
                break

            all_items.extend(items)

            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        if not all_items:
            return None

        records = []
        for item in all_items[:count]:
            try:
                records.append({
                    'date': item.get('date', ''),
                    'open': self._parse_float(item.get('open_pric')),
                    'high': self._parse_float(item.get('high_pric')),
                    'low': self._parse_float(item.get('low_pric')),
                    'close': self._parse_float(item.get('close_pric')),
                })
            except (ValueError, TypeError):
                continue

        if not records:
            return None

        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        return df.sort_values('date').reset_index(drop=True)

    # =========================================================================
    # 주문
    # =========================================================================

    async def _get_filled_price(self, order_no: str, code: str,
                                qty: int, max_retries: int = 3) -> tuple:
        """
        체결 내역(ka10076) 조회로 정확한 체결가 확인

        Args:
            order_no: 주문번호
            code: 종목코드
            qty: 원래 주문 수량 (부분체결 검증용)
            max_retries: 최대 재시도 횟수

        Returns:
            (filled_price, filled_qty) — 실패 시 (0, 0)
        """
        best_price = 0
        best_qty = 0
        best_time = ''

        for attempt in range(1, max_retries + 1):
            await asyncio.sleep(0.5 * attempt)
            try:
                result = await self._post(
                    self.ENDPOINTS['account'],
                    api_id="ka10076",
                    data={
                        "stk_cd": code,
                        "qry_tp": "1",
                        "sell_tp": "0",
                        "ord_no": "",
                        "stex_tp": "0",
                    },
                )
                # 동일 주문번호의 모든 부분체결을 합산 (가중평균가)
                total_qty = 0
                total_amount = 0
                last_cntr_time = ''
                for item in result.get('cntr', []):
                    if str(item.get('ord_no', '')) == str(order_no):
                        p = abs(self._parse_int(item.get('cntr_pric')))
                        q = self._parse_int(item.get('cntr_qty'))
                        if p > 0 and q > 0:
                            total_qty += q
                            total_amount += p * q
                        # 체결시간 추출 (cntr_tm, cntr_dtm 등)
                        t = (item.get('cntr_tm') or item.get('cntr_dtm')
                             or item.get('time') or '')
                        if t:
                            last_cntr_time = str(t).strip()
                if total_qty > 0:
                    avg_price = int(total_amount / total_qty)
                    # 가장 많이 체결된 결과를 보존
                    if total_qty > best_qty:
                        best_price = avg_price
                        best_qty = total_qty
                        best_time = last_cntr_time

                    # 전량 체결 확인 → 즉시 반환
                    if total_qty >= qty:
                        logger.info(
                            f"체결가 조회 성공 ({attempt}회): "
                            f"[{code}] 주문#{order_no} "
                            f"{avg_price:,}원 × {total_qty}주"
                            f"{f' 체결시간: {last_cntr_time}' if last_cntr_time else ''}")
                        return avg_price, total_qty, last_cntr_time

                    # 부분체결 → 다음 시도에서 전량 체결 대기
                    logger.debug(
                        f"부분체결 감지 ({attempt}회): "
                        f"[{code}] 주문#{order_no} "
                        f"{total_qty}/{qty}주 — 재시도")
            except Exception as e:
                logger.debug(f"체결가 조회 시도 {attempt} 실패: {e}")

        # 재시도 소진: 부분체결이라도 가격은 확보된 경우
        # → 체결가는 API 값, 수량은 원래 주문 수량 사용
        #   (모의투자에서 시장가 주문은 전량 체결이 보장되므로)
        if best_price > 0:
            if best_qty < qty:
                logger.warning(
                    f"부분체결 수량 보정: [{code}] 주문#{order_no} "
                    f"API 반환 {best_qty}주 → 주문수량 {qty}주로 보정 "
                    f"(체결가: {best_price:,}원)")
            else:
                logger.info(
                    f"체결가 조회 성공 (최종): "
                    f"[{code}] 주문#{order_no} "
                    f"{best_price:,}원 × {best_qty}주")
            # 수량은 max(API 반환, 원래 주문) — 전량 체결 보장 전제
            return best_price, max(best_qty, qty), best_time

        # ka10076 완전 실패 시 현재가 폴백
        try:
            price_data = await self.get_current_price(code)
            if price_data and price_data['price'] > 0:
                logger.warning(
                    f"체결가 조회 실패 → 현재가 폴백: [{code}] {price_data['price']:,}원")
                return price_data['price'], qty, ''
        except Exception:
            pass

        logger.warning(
            f"체결가 확인 불가: [{code}] 주문#{order_no} — signal 폴백 예정")
        return 0, 0, ''

    async def buy_market_order(self, code: str, qty: int) -> dict:
        """
        시장가 매수 주문

        Args:
            code: 종목코드
            qty: 매수 수량

        Returns:
            {
                'order_no': str, 'success': bool, 'message': str,
                'filled_price': int,  # 실제 체결 평균가 (잔고 조회 기반)
                'filled_qty': int,    # 실제 체결 수량
            }
        """
        result = await self._post(
            self.ENDPOINTS['order'],
            api_id="kt10000",
            data={
                "dmst_stex_tp": "KRX",
                "stk_cd": code,
                "ord_qty": str(qty),
                "ord_uv": "0",
                "trde_tp": "3",   # 시장가
            },
        )

        success = self._parse_int(result.get('return_code')) == 0
        order_no = str(result.get('ord_no', ''))
        message = result.get('return_msg', '')

        # 주문 응답에 체결가 포함 시 우선 사용
        filled_price = self._parse_int(result.get('cntr_prc', 0))
        filled_qty = self._parse_int(result.get('cntr_qty', 0))
        filled_time = ''

        # 체결가 미반환 시 ka10076 체결 조회로 정확한 체결가 확인
        if success and filled_price == 0:
            filled_price, filled_qty, filled_time = await self._get_filled_price(
                order_no, code, qty)

        logger.info(f"매수 주문 {'성공' if success else '실패'}: "
                     f"[{code}] {qty}주 시장가 | 주문번호: {order_no} | "
                     f"체결가: {filled_price:,}원 | {message}")

        return {
            'order_no': order_no, 'success': success, 'message': message,
            'filled_price': filled_price, 'filled_qty': filled_qty,
            'filled_time': filled_time,
        }

    async def buy_limit_order(self, code: str, qty: int, price: int) -> dict:
        """
        지정가 매수 주문

        Args:
            code: 종목코드
            qty: 매수 수량
            price: 지정가 (0이면 최유리지정가)

        Returns:
            buy_market_order와 동일한 형식
        """
        # price=0 → 최유리지정가(trde_tp="5"), 아니면 보통가(trde_tp="1")
        # 모의투자는 최유리지정가 미지원 → 시장가 폴백
        if price == 0:
            if self.mode == 'simulation':
                trde_tp = "3"   # 모의투자: 시장가 폴백
                ord_uv = "0"
            else:
                trde_tp = "5"   # 실거래: 최유리지정가
                ord_uv = "0"
        else:
            trde_tp = "1"   # 보통가 (지정가)
            ord_uv = str(price)

        result = await self._post(
            self.ENDPOINTS['order'],
            api_id="kt10000",
            data={
                "dmst_stex_tp": "KRX",
                "stk_cd": code,
                "ord_qty": str(qty),
                "ord_uv": ord_uv,
                "trde_tp": trde_tp,
            },
        )

        success = self._parse_int(result.get('return_code')) == 0
        order_no = str(result.get('ord_no', ''))
        message = result.get('return_msg', '')

        filled_price = self._parse_int(result.get('cntr_prc', 0))
        filled_qty = self._parse_int(result.get('cntr_qty', 0))
        filled_time = ''

        # 체결가 미반환 시 ka10076 체결 조회로 정확한 체결가 확인
        if success and filled_price == 0:
            filled_price, filled_qty, filled_time = await self._get_filled_price(
                order_no, code, qty)

        sim_label = "(모의→시장가)" if self.mode == 'simulation' and price == 0 else ""
        order_label = f"지정가 {price:,}원" if price > 0 else f"최유리지정가{sim_label}"
        logger.info(f"매수 주문 {'성공' if success else '실패'}: "
                     f"[{code}] {qty}주 {order_label} | 주문번호: {order_no} | "
                     f"체결가: {filled_price:,}원 | {message}")

        return {
            'order_no': order_no, 'success': success, 'message': message,
            'filled_price': filled_price, 'filled_qty': filled_qty,
            'filled_time': filled_time,
        }

    async def sell_market_order(self, code: str, qty: int) -> dict:
        """
        시장가 매도 주문

        Returns:
            {
                'order_no': str, 'success': bool, 'message': str,
                'filled_price': int,  # 실제 체결가 (현재가 조회 기반)
                'filled_qty': int,    # 체결 수량
            }
        """
        result = await self._post(
            self.ENDPOINTS['order'],
            api_id="kt10001",
            data={
                "dmst_stex_tp": "KRX",
                "stk_cd": code,
                "ord_qty": str(qty),
                "ord_uv": "0",
                "trde_tp": "3",   # 시장가
            },
        )

        success = self._parse_int(result.get('return_code')) == 0
        order_no = str(result.get('ord_no', ''))
        message = result.get('return_msg', '')

        # 주문 응답에 체결가 포함 시 우선 사용
        filled_price = self._parse_int(result.get('cntr_prc', 0))
        filled_qty = self._parse_int(result.get('cntr_qty', 0)) or qty
        filled_time = ''

        # 체결가 미반환 시 ka10076 체결 조회로 정확한 체결가 확인
        if success and filled_price == 0:
            filled_price, filled_qty, filled_time = await self._get_filled_price(
                order_no, code, qty)
            filled_qty = filled_qty or qty

        logger.info(f"매도 주문 {'성공' if success else '실패'}: "
                     f"[{code}] {qty}주 시장가 | 주문번호: {order_no} | "
                     f"체결가: {filled_price:,}원 | {message}")

        return {
            'order_no': order_no, 'success': success, 'message': message,
            'filled_price': filled_price, 'filled_qty': filled_qty,
            'filled_time': filled_time,
        }

    async def sell_limit_order(self, code: str, qty: int, price: int) -> dict:
        """
        지정가 매도 주문

        Args:
            code: 종목코드
            qty: 매도 수량
            price: 지정가 (0이면 최유리지정가)

        Returns:
            sell_market_order와 동일한 형식
        """
        # 모의투자는 최유리지정가 미지원 → 시장가 폴백
        if price == 0:
            if self.mode == 'simulation':
                trde_tp = "3"   # 모의투자: 시장가 폴백
                ord_uv = "0"
            else:
                trde_tp = "5"   # 실거래: 최유리지정가
                ord_uv = "0"
        else:
            trde_tp = "1"   # 보통가 (지정가)
            ord_uv = str(price)

        result = await self._post(
            self.ENDPOINTS['order'],
            api_id="kt10001",
            data={
                "dmst_stex_tp": "KRX",
                "stk_cd": code,
                "ord_qty": str(qty),
                "ord_uv": ord_uv,
                "trde_tp": trde_tp,
            },
        )

        success = self._parse_int(result.get('return_code')) == 0
        order_no = str(result.get('ord_no', ''))
        message = result.get('return_msg', '')

        filled_price = self._parse_int(result.get('cntr_prc', 0))
        filled_qty = self._parse_int(result.get('cntr_qty', 0)) or qty
        filled_time = ''

        # 체결가 미반환 시 ka10076 체결 조회로 정확한 체결가 확인
        if success and filled_price == 0:
            filled_price, filled_qty, filled_time = await self._get_filled_price(
                order_no, code, qty)
            filled_qty = filled_qty or qty

        sim_label = "(모의→시장가)" if self.mode == 'simulation' and price == 0 else ""
        order_label = f"지정가 {price:,}원" if price > 0 else f"최유리지정가{sim_label}"
        logger.info(f"매도 주문 {'성공' if success else '실패'}: "
                     f"[{code}] {qty}주 {order_label} | 주문번호: {order_no} | "
                     f"체결가: {filled_price:,}원 | {message}")

        return {
            'order_no': order_no, 'success': success, 'message': message,
            'filled_price': filled_price, 'filled_qty': filled_qty,
            'filled_time': filled_time,
        }

    async def get_position_detail(self, code: str) -> Optional[dict]:
        """
        특정 종목의 잔고 상세 조회 (kt00004)

        Returns:
            {'code': str, 'avg_price': int, 'qty': int, 'current_price': int}
            또는 None (해당 종목 미보유)
        """
        try:
            result = await self._post(
                self.ENDPOINTS['account'],
                api_id="kt00004",
                data={"qry_tp": "1", "dmst_stex_tp": "KRX"},
            )
            for item in result.get('stk_acnt_evlt_prst', []):
                item_code = self._strip_code_prefix(item.get('stk_cd', ''))
                if item_code == code:
                    qty = self._parse_int(item.get('rmnd_qty'))
                    if qty > 0:
                        return {
                            'code': item_code,
                            'avg_price': abs(self._parse_int(
                                item.get('avg_prc'))),
                            'qty': qty,
                            'current_price': abs(self._parse_int(
                                item.get('cur_prc'))),
                        }
        except Exception as e:
            logger.warning(f"종목 잔고 조회 실패 [{code}]: {e}")
        return None

    async def cancel_order(self, order_no: str, code: str, qty: int) -> dict:
        """주문 취소"""
        result = await self._post(
            self.ENDPOINTS['order'],
            api_id="kt10003",
            data={
                "dmst_stex_tp": "KRX",
                "stk_cd": code,
                "orig_ord_no": order_no,
                "cncl_qty": "0",
            },
        )

        success = self._parse_int(result.get('return_code')) == 0
        message = result.get('return_msg', '')

        logger.info(f"주문 취소 {'성공' if success else '실패'}: 주문번호 {order_no}")

        return {'success': success, 'message': message}

    # =========================================================================
    # 계좌 조회
    # =========================================================================

    async def get_balance(self) -> dict:
        """
        계좌 잔고 조회

        Returns:
            {
                'total_equity': int,      # 총 평가금액
                'cash': int,              # 예수금
                'total_pnl': int,         # 총 평가손익
                'total_pnl_pct': float,   # 총 수익률
                'positions': [            # 보유 종목 리스트
                    {
                        'code': str,
                        'name': str,
                        'qty': int,
                        'entry_price': float,
                        'current_price': float,
                        'pnl_pct': float,
                        'amount': int,
                    },
                    ...
                ]
            }
        """
        result = await self._post(
            self.ENDPOINTS['account'],
            api_id="kt00004",
            data={
                "qry_tp": "1",
                "dmst_stex_tp": "KRX",
            },
        )

        # 계좌 요약
        total_equity = abs(self._parse_int(result.get('aset_evlt_amt')))
        cash = abs(self._parse_int(result.get('entr')))
        total_pnl = self._parse_int(result.get('tdy_lspft'))
        total_pnl_pct = self._parse_float(result.get('tdy_lspft_rt'))

        # 종목별 잔고
        stock_list = result.get('stk_acnt_evlt_prst', [])
        positions = []
        for item in stock_list:
            qty = self._parse_int(item.get('rmnd_qty'))
            if qty > 0:
                positions.append({
                    'code': self._strip_code_prefix(item.get('stk_cd', '')),
                    'name': item.get('stk_nm', ''),
                    'qty': qty,
                    'entry_price': self._parse_float(item.get('avg_prc')),
                    'current_price': abs(self._parse_int(item.get('cur_prc'))),
                    'pnl_pct': self._parse_float(item.get('pl_rt')),
                    'amount': abs(self._parse_int(item.get('evlt_amt',
                                  item.get('cur_prc', 0)))),
                })

        return {
            'total_equity': total_equity,
            'cash': cash,
            'total_pnl': total_pnl,
            'total_pnl_pct': total_pnl_pct,
            'positions': positions,
        }

    async def get_pending_orders(self) -> List[dict]:
        """미체결 주문 조회 (kt00009 qry_tp=0 전체)"""
        try:
            result = await self._post(
                self.ENDPOINTS['account'],
                api_id="kt00009",
                data={
                    "stk_bond_tp": "1",    # 1:주식
                    "mrkt_tp": "0",        # 0:전체
                    "sell_tp": "0",        # 0:전체
                    "qry_tp": "0",         # 0:전체 (체결+미체결)
                    "dmst_stex_tp": "KRX",
                },
            )

            orders = []
            for item in result.get(
                    'acnt_ord_cntr_prst_array',
                    result.get('list', [])):
                # 미체결: 주문수량 > 체결수량
                ord_qty = self._parse_int(item.get('ord_qty'))
                cntr_qty = self._parse_int(item.get('cntr_qty'))
                if cntr_qty >= ord_qty and ord_qty > 0:
                    continue  # 이미 전량 체결된 주문 스킵

                orders.append({
                    'order_no': item.get('ord_no', ''),
                    'code': self._strip_code_prefix(item.get('stk_cd', '')),
                    'name': item.get('stk_nm', '').strip(),
                    'order_type': item.get('io_tp_nm', '').strip(),
                    'trade_type': item.get('trde_tp', '').strip(),
                    'qty': ord_qty,
                    'filled_qty': cntr_qty,
                    'remaining_qty': ord_qty - cntr_qty,
                    'price': self._parse_int(item.get('ord_uv')),
                    'filled_price': self._parse_int(item.get('cntr_uv')),
                    'status': item.get('acpt_tp', '').strip(),
                    'time': item.get('cntr_tm', ''),
                })

            return orders
        except Exception as e:
            logger.error(f"미체결 조회 실패: {e}")
            return []

    async def get_order_executions(
        self,
        sell_tp: str = "0",
        qry_tp: str = "1",
        ord_dt: str = "",
        stk_cd: str = "",
    ) -> List[dict]:
        """
        계좌별 주문체결현황 조회 (kt00009)

        Args:
            sell_tp: "0"=전체, "1"=매도, "2"=매수
            qry_tp: "0"=전체, "1"=체결만
            ord_dt: 주문일자 YYYYMMDD (빈 문자열=당일)
            stk_cd: 종목코드 (빈 문자열=전체)

        Returns:
            [{order_no, code, name, trade_type, order_type,
              qty, filled_qty, price, filled_price, time, ...}]
        """
        all_items = []
        cont_yn = "N"
        next_key = ""

        while True:
            result = await self._post(
                self.ENDPOINTS['account'],
                api_id="kt00009",
                data={
                    "ord_dt": ord_dt,
                    "stk_bond_tp": "1",    # 1:주식
                    "mrkt_tp": "0",        # 0:전체
                    "sell_tp": sell_tp,
                    "qry_tp": qry_tp,
                    "stk_cd": stk_cd,
                    "dmst_stex_tp": "KRX",
                },
                cont_yn=cont_yn,
                next_key=next_key,
            )

            items = result.get(
                'acnt_ord_cntr_prst_array',
                result.get('list', []))
            for item in items:
                code = self._strip_code_prefix(item.get('stk_cd', ''))
                if not code:
                    continue
                all_items.append({
                    'order_no': item.get('ord_no', ''),
                    'code': code,
                    'name': item.get('stk_nm', '').strip(),
                    'trade_type': item.get('trde_tp', '').strip(),
                    'order_type': item.get('io_tp_nm', '').strip(),
                    'qty': self._parse_int(item.get('ord_qty')),
                    'filled_qty': self._parse_int(item.get('cntr_qty')),
                    'price': self._parse_int(item.get('ord_uv')),
                    'filled_price': self._parse_int(item.get('cntr_uv')),
                    'confirm_qty': self._parse_int(item.get('cnfm_qty')),
                    'status': item.get('acpt_tp', '').strip(),
                    'settle_type': item.get('setl_tp', '').strip(),
                    'modify_cancel': item.get('mdfy_cncl_tp', '').strip(),
                    'time': item.get('cntr_tm', ''),
                    'exchange': item.get('dmst_stex_tp', '').strip(),
                    'orig_order_no': item.get('orig_ord_no', ''),
                })

            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        return all_items

    # =========================================================================
    # 매매 기록 조회 (검증용)
    # =========================================================================

    async def get_trade_journal(self, base_dt: str) -> List[dict]:
        """
        당일매매일지 조회 (ka10170)

        Args:
            base_dt: 조회일자 YYYYMMDD

        Returns:
            [{
                'code': str, 'name': str,
                'buy_avg_price': int, 'buy_qty': int,
                'sell_avg_price': int, 'sell_qty': int,
                'commission_tax': int, 'pnl_amount': int,
                'profit_rate': float,
            }, ...]
        """
        all_items = []
        cont_yn = "N"
        next_key = ""

        while True:
            result = await self._post(
                self.ENDPOINTS['account'],
                api_id="ka10170",
                data={
                    "base_dt": base_dt,
                    "ottks_tp": "1",
                    "ch_crd_tp": "0",
                },
                cont_yn=cont_yn,
                next_key=next_key,
            )

            items = result.get('tdy_trde_diary', [])
            for item in items:
                code = self._strip_code_prefix(item.get('stk_cd', ''))
                if not code:
                    continue
                all_items.append({
                    'code': code,
                    'name': item.get('stk_nm', '').strip(),
                    'buy_avg_price': abs(self._parse_int(
                        item.get('buy_avg_pric'))),
                    'buy_qty': self._parse_int(item.get('buy_qty')),
                    'sell_avg_price': abs(self._parse_int(
                        item.get('sel_avg_pric', item.get('sell_avg_pric')))),
                    'sell_qty': self._parse_int(
                        item.get('sell_qty', item.get('sel_qty'))),
                    'commission_tax': abs(self._parse_int(
                        item.get('cmsn_alm_tax'))),
                    'pnl_amount': self._parse_int(item.get('pl_amt')),
                    'profit_rate': self._parse_float(item.get('prft_rt')),
                })

            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        return all_items

    async def get_executions(self, stk_cd: str = "",
                             sell_tp: str = "0") -> List[dict]:
        """
        체결 내역 조회 (ka10076)

        Args:
            stk_cd: 종목코드 (빈 문자열=전체)
            sell_tp: "0"=전체, "1"=매도, "2"=매수

        Returns:
            [{
                'order_no': str, 'code': str, 'name': str,
                'action': str,  # "매수" or "매도"
                'price': int, 'qty': int,
                'commission': int, 'tax': int,
                'time': str,
            }, ...]
        """
        all_items = []
        cont_yn = "N"
        next_key = ""

        while True:
            result = await self._post(
                self.ENDPOINTS['account'],
                api_id="ka10076",
                data={
                    "stk_cd": stk_cd,
                    "qry_tp": "1",
                    "sell_tp": sell_tp,
                    "ord_no": "",
                    "stex_tp": "0",
                },
                cont_yn=cont_yn,
                next_key=next_key,
            )

            items = result.get('cntr', [])
            for item in items:
                code = self._strip_code_prefix(
                    item.get('stk_cd', item.get('shtn_pdno', '')))
                if not code:
                    continue
                all_items.append({
                    'order_no': str(item.get('ord_no', '')),
                    'code': code,
                    'name': item.get('stk_nm', item.get('prdt_name', '')
                                     ).strip(),
                    'action': item.get('io_tp_nm',
                                       item.get('sell_tp_nm', '')
                                       ).strip(),
                    'price': abs(self._parse_int(
                        item.get('cntr_pric', item.get('avg_prvs')))),
                    'qty': self._parse_int(
                        item.get('cntr_qty', item.get('tot_ccld_qty'))),
                    'commission': abs(self._parse_int(
                        item.get('tdy_trde_cmsn'))),
                    'tax': abs(self._parse_int(
                        item.get('tdy_trde_tax'))),
                    'time': item.get('cntr_tm',
                                     item.get('ord_tmd', '')),
                })

            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        return all_items

    async def get_daily_realized_pnl(self, start_dt: str,
                                     end_dt: str = "") -> List[dict]:
        """
        일자별 실현손익 조회 (ka10074)

        Args:
            start_dt: 시작일자 YYYYMMDD
            end_dt: 종료일자 YYYYMMDD (빈 문자열=start_dt와 동일)

        Returns:
            [{
                'date': str, 'pnl_amount': int,
                'commission': int, 'tax': int,
                'net_pnl': int,
            }, ...]
        """
        if not end_dt:
            end_dt = start_dt

        all_items = []
        cont_yn = "N"
        next_key = ""

        while True:
            result = await self._post(
                self.ENDPOINTS['account'],
                api_id="ka10074",
                data={
                    "strt_dt": start_dt,
                    "end_dt": end_dt,
                },
                cont_yn=cont_yn,
                next_key=next_key,
            )

            items = result.get('list', result.get('output', []))
            if isinstance(items, list):
                for item in items:
                    dt = item.get('date', item.get('stdr_dt', ''))
                    if not dt:
                        continue
                    all_items.append({
                        'date': dt,
                        'pnl_amount': self._parse_int(item.get('pl_amt')),
                        'commission': abs(self._parse_int(
                            item.get('cmsn_amt', item.get('cmsn')))),
                        'tax': abs(self._parse_int(
                            item.get('tax_amt', item.get('tax')))),
                        'net_pnl': self._parse_int(
                            item.get('net_pl_amt', item.get('rlzt_pfls'))),
                    })

            if result.get('_cont_yn', 'N') == 'Y' and result.get('_next_key'):
                cont_yn = "Y"
                next_key = result['_next_key']
            else:
                break

        return all_items
