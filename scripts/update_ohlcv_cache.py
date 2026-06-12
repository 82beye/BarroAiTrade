"""OHLCV 캐시 증분 업데이트 — ai-trade 캐시를 BarroAiTrade 키움 API로 최신화.

사용:
    source .venv/bin/activate
    set -a; . ./.env.local; set +a
    python scripts/update_ohlcv_cache.py

    # 캐시 경로 지정
    python scripts/update_ohlcv_cache.py --cache-dir /Users/beye82/Workspace/ai-trade/data/ohlcv_cache
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time as _time
from datetime import date, datetime, time as dtime, timedelta, timezone

KST = timezone(timedelta(hours=9))  # [BAR-OPS-39] 장중 가드용
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


def _build_oauth() -> KiwoomNativeOAuth:
    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
    base_url = os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com")
    if not app_key or not app_secret:
        raise SystemExit("KIWOOM_APP_KEY / KIWOOM_APP_SECRET 환경변수 필요")
    return KiwoomNativeOAuth(
        app_key=SecretStr(app_key), app_secret=SecretStr(app_secret),
        base_url=base_url,
    )


def load_cache_file(filepath: str) -> list[dict] | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = json.load(f)
        return content.get("data", [])
    except Exception:
        return None


def save_cache_file(filepath: str, records: list[dict]) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({"data": records}, f)


def get_gap_days(records: list[dict]) -> int:
    """캐시 최신 날짜와 오늘 사이 갭 일수."""
    if not records:
        return 500
    dates = [r["date"] for r in records]
    latest = max(dates)  # YYYYMMDD string
    latest_dt = datetime.strptime(latest, "%Y%m%d").date()
    return (date.today() - latest_dt).days


def merge_records(existing: list[dict], new_ohlcv: list) -> list[dict]:
    """기존 캐시 + 신규 OHLCV → 중복 제거 후 날짜순 정렬."""
    by_date = {}
    for r in existing:
        by_date[r["date"]] = r
    for candle in new_ohlcv:
        dt_str = candle.timestamp.strftime("%Y%m%d")
        by_date[dt_str] = {
            "date": dt_str,
            "open": int(candle.open),
            "high": int(candle.high),
            "low": int(candle.low),
            "close": int(candle.close),
            "volume": int(candle.volume),
        }
    return sorted(by_date.values(), key=lambda r: r["date"])


async def run(cache_dir: str) -> None:
    oauth = _build_oauth()
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth, rate_limit_seconds=0.55)

    # 캐시 디렉토리에서 종목 코드 목록 추출
    json_files = [f for f in os.listdir(cache_dir) if f.endswith(".json") and f != "meta.json"]
    symbols = [f.replace(".json", "") for f in json_files]
    logger.info(f"캐시 종목 수: {len(symbols)}")

    updated = 0
    skipped = 0
    failed = 0
    new_days = 0
    start = _time.time()

    for i, symbol in enumerate(symbols):
        filepath = os.path.join(cache_dir, f"{symbol}.json")
        existing = load_cache_file(filepath) or []
        gap = get_gap_days(existing)

        # [BAR-OPS-39] 스킵 조건 gap<=1 → gap<1 — 08:00 장전 크론 시절(b64dc03)엔
        #   gap=1(캐시에 어제 종가)이면 받을 데이터가 없어 스킵이 옳았으나, 2bdf0b4 가
        #   15:40 장마감後 EOD 잡으로 옮기며 당일분(gap=1)이 영구 스킵됐다(6/11 meta.json:
        #   skipped 2954 / new_days_added 0 — 일봉 캐시 6/10 정지의 원인). 캐시 최신일이
        #   '오늘'(gap<1)일 때만 스킵 — 같은 날 재실행 멱등성은 유지된다.
        if gap < 1:
            skipped += 1
            continue
        # [BAR-OPS-39 리뷰 반영] 장중 가드 — gap=1 인 채로 장중(15:30 이전)에 수동 실행하면
        #   미완성 당일 봉이 캐시돼 이후 EOD 실행(gap=0 스킵)이 덮어쓰지 못하고 영구 잔존.
        #   gap=1 은 장마감(15:30) 이후에만 fetch.
        if gap == 1 and datetime.now(KST).time() < dtime(15, 30):
            skipped += 1
            continue

        if (i + 1) % 100 == 0 or i == 0:
            elapsed = _time.time() - start
            done = updated + skipped + failed
            if done > 0:
                eta = elapsed / done * (len(symbols) - done) / 60
                logger.info(
                    f"진행: {i+1}/{len(symbols)} "
                    f"(업데이트:{updated} 스킵:{skipped} 실패:{failed} ETA:{eta:.0f}분)"
                )

        try:
            candles = await fetcher.fetch_daily(symbol)
            if not candles:
                failed += 1
                continue

            before = len(existing)
            merged = merge_records(existing, candles)
            save_cache_file(filepath, merged)
            added = len(merged) - before
            new_days += max(added, 0)
            updated += 1

        except Exception as e:
            logger.debug(f"[{symbol}] 실패: {e}")
            failed += 1

    elapsed = _time.time() - start

    # meta.json 업데이트
    meta = {
        "updated": date.today().isoformat(),
        "count": updated,
        "total_requested": len(symbols),
        "failed": failed,
        "skipped": skipped,
        "new_days_added": new_days,
        "elapsed_seconds": round(elapsed, 1),
        "api_method": "ka10081",
    }
    meta_path = os.path.join(cache_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info(
        f"OHLCV 캐시 업데이트 완료 ({elapsed/60:.1f}분): "
        f"{updated}업데이트 {skipped}스킵 {failed}실패 (+{new_days}일)"
    )


def main():
    ap = argparse.ArgumentParser(description="OHLCV 캐시 증분 업데이트")
    ap.add_argument(
        "--cache-dir",
        default="/Users/beye82/Workspace/ai-trade/data/ohlcv_cache",
        help="OHLCV 캐시 디렉토리",
    )
    args = ap.parse_args()

    if not os.path.isdir(args.cache_dir):
        raise SystemExit(f"캐시 디렉토리 없음: {args.cache_dir}")

    asyncio.run(run(args.cache_dir))


if __name__ == "__main__":
    main()
