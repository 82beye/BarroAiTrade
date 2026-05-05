"""
OHLCV 캐시 매니저 (누적 방식 + ka10081 대량 조회)
전종목 일봉 데이터를 JSON 파일로 캐시하여 스캔 속도를 대폭 향상

ka10081(주식일봉차트조회)이 지원되면 첫 실행에 500일+ 확보 가능.
미지원 시 ka10005 폴백으로 매일 1영업일 누적.

캐시 디렉토리 구조:
    data/ohlcv_cache/
    ├── meta.json       # {"updated": "2026-03-05", "count": 2874, ...}
    ├── 005930.json     # 종목별 OHLCV 데이터 (누적)
    ├── 000020.json
    └── ...
"""

import json
import logging
import os
import time as _time
from datetime import date, datetime
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class OHLCVCache:
    """전종목 일봉 OHLCV 캐시 (누적 병합 방식 + 스마트 증분)"""

    def __init__(self, cache_dir: str = "./data/ohlcv_cache"):
        self.cache_dir = cache_dir
        self._meta_path = os.path.join(cache_dir, "meta.json")
        os.makedirs(cache_dir, exist_ok=True)

    # =========================================================================
    # ka10081 지원 여부 탐지
    # =========================================================================

    async def _probe_ka10081(self, api) -> bool:
        """
        삼성전자(005930) 5일 조회로 ka10081 지원 여부 1회 탐지.
        지원 시 True, 미지원(404 등) 시 False.
        """
        try:
            df = await api.get_daily_ohlcv_historical(
                "005930", count=5, max_pages=1,
            )
            if df is not None and len(df) > 0:
                logger.info(
                    f"ka10081 지원 확인: 삼성전자 {len(df)}일 반환"
                )
                return True
            logger.info("ka10081 응답은 있으나 데이터 없음 → 폴백")
            return False
        except Exception as e:
            logger.info(f"ka10081 미지원: {e} → ka10005 폴백")
            return False

    # =========================================================================
    # 종목별 필요 조회량 계산
    # =========================================================================

    def _calc_fetch_count(self, code: str, max_count: int) -> int:
        """
        캐시 상태를 보고 이 종목에 몇 일을 API로 조회해야 하는지 결정.

        Returns:
            0         = 스킵 (충분히 최신)
            N (> 0)   = N일 조회 필요
        """
        existing = self.load(code)

        if existing is None or len(existing) == 0:
            # 캐시 없음 → 전체 조회
            return max_count

        cached_len = len(existing)
        latest_date = existing['date'].max()
        today = pd.Timestamp(date.today())
        gap_days = (today - latest_date).days

        if cached_len >= max_count and gap_days <= 1:
            # 충분하고 최신 → 스킵
            return 0

        if cached_len < max_count:
            # 캐시 부족 → 부족분 조회
            return max_count

        # 캐시 ≥ max_count이지만 갭 있음 → 갭 + 5일 버퍼
        return gap_days + 5

    # =========================================================================
    # update_all — ka10081 우선, ka10005 폴백
    # =========================================================================

    async def update_all(self, api, stock_list: List[dict], ohlcv_count: int = 500):
        """
        전종목 일봉 데이터를 API로 조회하여 캐시에 누적 저장.

        ka10081 지원 시 대량 과거 데이터를 한번에 확보.
        미지원 시 ka10005 폴백 (30일 누적 방식).

        Args:
            api: KiwoomRestAPI 인스턴스
            stock_list: [{"code": "005930", "name": "삼성전자", ...}, ...]
            ohlcv_count: 종목당 목표 일수 (기본 500)

        Returns:
            meta dict
        """
        total = len(stock_list)
        success_count = 0
        fail_count = 0
        skip_count = 0
        new_days_added = 0
        api_calls = 0
        start_time = _time.time()

        # ── ka10081 지원 여부 탐지 ──
        use_ka10081 = await self._probe_ka10081(api)
        api_method = "ka10081" if use_ka10081 else "ka10005"

        logger.info(
            f"OHLCV 캐시 업데이트 시작: {total}종목 "
            f"(목표: {ohlcv_count}일, API: {api_method})"
        )

        for i, stock in enumerate(stock_list):
            code = stock['code']
            name = stock.get('name', '')

            # ── 진행률 로그 + ETA ──
            if (i + 1) % 100 == 0 or i == 0:
                elapsed = _time.time() - start_time
                if i > 0:
                    per_stock = elapsed / i
                    remaining = per_stock * (total - i)
                    eta_min = remaining / 60
                    eta_str = f", ETA: {eta_min:.0f}분"
                else:
                    eta_str = ""

                logger.info(
                    f"캐시 진행: {i + 1}/{total} "
                    f"(성공:{success_count} 스킵:{skip_count} 실패:{fail_count} "
                    f"API호출:{api_calls} 신규일수:{new_days_added}{eta_str})"
                )

            # ── 스마트 조회량 계산 ──
            fetch_count = self._calc_fetch_count(code, ohlcv_count)
            if fetch_count == 0:
                skip_count += 1
                continue

            # ── API 호출: ka10081 우선, 실패 시 ka10005 ──
            df_new = None
            try:
                if use_ka10081:
                    api_calls += 1
                    df_new = await api.get_daily_ohlcv_historical(
                        code, count=fetch_count,
                    )
                    # ka10081 실패 시 ka10005 폴백
                    if df_new is None:
                        api_calls += 1
                        df_new = await api.get_daily_ohlcv(code, count=fetch_count)
                else:
                    api_calls += 1
                    df_new = await api.get_daily_ohlcv(code, count=fetch_count)

                if df_new is not None and len(df_new) > 0:
                    before_len, after_len = self.save_merge(code, df_new)
                    added = after_len - before_len
                    new_days_added += max(added, 0)
                    success_count += 1

                    # 첫 종목에서 데이터 길이 진단
                    if success_count == 1:
                        logger.info(
                            f"[{api_method}] 첫 종목 [{code}] {name}: "
                            f"API 반환 {len(df_new)}일 | "
                            f"캐시 {before_len}일 → {after_len}일 (+{added}일)"
                        )
                        if len(df_new) < fetch_count:
                            logger.warning(
                                f"API가 {fetch_count}일 요청에 {len(df_new)}일만 반환"
                            )
                else:
                    fail_count += 1
            except Exception as e:
                logger.debug(f"캐시 저장 실패 [{code}] {name}: {e}")
                fail_count += 1

        elapsed_seconds = _time.time() - start_time

        # 데이터 깊이 통계
        depth_stats = self.get_data_depth_stats()

        # 메타 정보 저장
        meta = {
            "updated": date.today().isoformat(),
            "count": success_count,
            "total_requested": total,
            "failed": fail_count,
            "skipped": skip_count,
            "new_days_added": new_days_added,
            "api_calls": api_calls,
            "elapsed_seconds": round(elapsed_seconds, 1),
            "api_method": api_method,
            "depth": depth_stats,
        }
        with open(self._meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        elapsed_min = elapsed_seconds / 60
        logger.info(
            f"OHLCV 캐시 업데이트 완료 ({elapsed_min:.1f}분): "
            f"{success_count}성공 {skip_count}스킵 {fail_count}실패 "
            f"(API호출: {api_calls}, 방식: {api_method})"
        )
        if depth_stats:
            logger.info(
                f"캐시 데이터 깊이: "
                f"평균 {depth_stats['avg']:.0f}일, "
                f"최소 {depth_stats['min']}일, "
                f"최대 {depth_stats['max']}일 "
                f"(역매공파 필요: 448일, "
                f"충족: {depth_stats['stocks_over_448']}/{depth_stats['total_stocks']})"
            )

        return meta

    # =========================================================================
    # 저장 / 로드 / 통계
    # =========================================================================

    def save_merge(self, code: str, df_new: pd.DataFrame) -> tuple:
        """
        종목 일봉 데이터를 기존 캐시와 병합하여 저장 (누적 방식)

        Returns:
            (병합 전 길이, 병합 후 길이) 튜플
        """
        existing_df = self.load(code)
        before_len = len(existing_df) if existing_df is not None else 0

        if existing_df is not None and len(existing_df) > 0:
            merged = pd.concat([existing_df, df_new], ignore_index=True)
            merged = merged.drop_duplicates(subset=['date'], keep='last')
            merged = merged.sort_values('date').reset_index(drop=True)
            df_final = merged
        else:
            df_final = df_new.sort_values('date').reset_index(drop=True)

        after_len = len(df_final)

        filepath = os.path.join(self.cache_dir, f"{code}.json")
        records = []
        for _, row in df_final.iterrows():
            records.append({
                "date": row['date'].strftime('%Y%m%d') if hasattr(row['date'], 'strftime') else str(row['date']),
                "open": int(row['open']),
                "high": int(row['high']),
                "low": int(row['low']),
                "close": int(row['close']),
                "volume": int(row['volume']),
            })

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({"data": records}, f)

        return before_len, after_len

    def save(self, code: str, df: pd.DataFrame):
        """종목 일봉 데이터를 JSON 파일로 저장 (덮어쓰기, 호환용)"""
        self.save_merge(code, df)

    def load(self, code: str) -> Optional[pd.DataFrame]:
        """캐시된 종목 데이터를 DataFrame으로 로드"""
        filepath = os.path.join(self.cache_dir, f"{code}.json")

        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = json.load(f)

            records = content.get('data', [])
            if not records:
                return None

            df = pd.DataFrame(records)
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            return df
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"캐시 로드 실패 [{code}]: {e}")
            return None

    def get_data_depth_stats(self) -> Optional[dict]:
        """전체 캐시 파일의 데이터 깊이 통계"""
        lengths = []
        try:
            for fname in os.listdir(self.cache_dir):
                if not fname.endswith('.json') or fname == 'meta.json':
                    continue
                fpath = os.path.join(self.cache_dir, fname)
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = json.load(f)
                records = content.get('data', [])
                if records:
                    lengths.append(len(records))
        except Exception:
            return None

        if not lengths:
            return None

        return {
            "avg": sum(lengths) / len(lengths),
            "min": min(lengths),
            "max": max(lengths),
            "total_stocks": len(lengths),
            "stocks_over_448": sum(1 for l in lengths if l >= 448),
        }

    def is_fresh(self) -> bool:
        """캐시가 오늘 날짜로 업데이트되었는지 확인"""
        return self.is_recent(max_days=0)

    def is_recent(self, max_days: int = 3) -> bool:
        """캐시가 최근 N일 이내 업데이트되었는지 확인 (주말/공휴일 대응)"""
        if not os.path.exists(self._meta_path):
            return False

        try:
            with open(self._meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            updated = date.fromisoformat(meta.get('updated', ''))
            return (date.today() - updated).days <= max_days
        except (json.JSONDecodeError, KeyError, ValueError):
            return False

    def get_meta(self) -> Optional[dict]:
        """메타 정보 반환"""
        if not os.path.exists(self._meta_path):
            return None

        try:
            with open(self._meta_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            return None
