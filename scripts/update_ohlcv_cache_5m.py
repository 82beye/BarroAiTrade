"""OHLCV 5분봉 캐시 증분 업데이트 — 키움 ka10080 API.

사용:
    set -a; . ./.env.local; set +a
    python scripts/update_ohlcv_cache_5m.py

    # 캐시 경로 / 대상 일수 지정
    python scripts/update_ohlcv_cache_5m.py --cache-dir /path/to/5m_cache --days 20
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

TIC_SCOPE = "5"


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


def _symbol_list(daily_cache_dir: str) -> list[str]:
    """일봉 캐시 디렉토리에서 종목 코드 목록 추출."""
    files = [f for f in os.listdir(daily_cache_dir) if f.endswith(".json") and f != "meta.json"]
    return [f.replace(".json", "") for f in sorted(files)]


def load_cache(filepath: str) -> list[dict]:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f).get("data", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_cache(filepath: str, records: list[dict]) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({"data": records}, f)


def get_latest_ts(records: list[dict]) -> datetime | None:
    if not records:
        return None
    latest = max(r["datetime"] for r in records)
    return datetime.strptime(latest, "%Y%m%d%H%M%S")


def needs_update(records: list[dict]) -> bool:
    """오늘 장중 데이터가 이미 있으면 스킵."""
    latest = get_latest_ts(records)
    if latest is None:
        return True
    return latest.date() < date.today()


def merge_records(existing: list[dict], new_ohlcv: list) -> list[dict]:
    by_ts: dict[str, dict] = {}
    for r in existing:
        by_ts[r["datetime"]] = r
    for candle in new_ohlcv:
        ts_str = candle.timestamp.strftime("%Y%m%d%H%M%S")
        by_ts[ts_str] = {
            "datetime": ts_str,
            "date": candle.timestamp.strftime("%Y%m%d"),
            "time": candle.timestamp.strftime("%H%M%S"),
            "open": int(candle.open),
            "high": int(candle.high),
            "low": int(candle.low),
            "close": int(candle.close),
            "volume": int(candle.volume),
        }
    return sorted(by_ts.values(), key=lambda r: r["datetime"])


def trim_old(records: list[dict], keep_days: int) -> list[dict]:
    """keep_days 영업일 이전 데이터 제거 (캐시 비대화 방지)."""
    cutoff = (date.today() - timedelta(days=keep_days + 10)).strftime("%Y%m%d")
    return [r for r in records if r["date"] >= cutoff]


async def run(daily_cache_dir: str, output_dir: str, target_days: int, keep_days: int) -> None:
    oauth = _build_oauth()
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth, rate_limit_seconds=0.55)

    symbols = _symbol_list(daily_cache_dir)
    logger.info(f"대상 종목 수: {len(symbols)}, 목표: {target_days}영업일, 보관: {keep_days}일")

    os.makedirs(output_dir, exist_ok=True)

    updated = 0
    skipped = 0
    failed = 0
    start = _time.time()

    for i, symbol in enumerate(symbols):
        filepath = os.path.join(output_dir, f"{symbol}.json")
        existing = load_cache(filepath)

        if not needs_update(existing):
            skipped += 1
            continue

        if (i + 1) % 50 == 0 or i == 0:
            elapsed = _time.time() - start
            done = updated + skipped + failed
            if done > 0:
                eta = elapsed / done * (len(symbols) - done) / 60
                logger.info(
                    f"진행: {i+1}/{len(symbols)} "
                    f"(업데이트:{updated} 스킵:{skipped} 실패:{failed} ETA:{eta:.0f}분)"
                )

        try:
            candles = await fetcher.fetch_minute_history(
                symbol=symbol,
                tic_scope=TIC_SCOPE,
                target_business_days=target_days,
                max_pages=12,
            )
            if not candles:
                failed += 1
                continue

            merged = merge_records(existing, candles)
            merged = trim_old(merged, keep_days)
            save_cache(filepath, merged)
            updated += 1

        except Exception as e:
            logger.debug(f"[{symbol}] 실패: {e}")
            failed += 1

    elapsed = _time.time() - start

    meta = {
        "updated": date.today().isoformat(),
        "tic_scope": TIC_SCOPE,
        "target_business_days": target_days,
        "count": updated,
        "total_requested": len(symbols),
        "failed": failed,
        "skipped": skipped,
        "elapsed_seconds": round(elapsed, 1),
        "api_method": "ka10080",
    }
    meta_path = os.path.join(output_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info(
        f"5분봉 캐시 업데이트 완료 ({elapsed/60:.1f}분): "
        f"{updated}업데이트 {skipped}스킵 {failed}실패"
    )


def main():
    ap = argparse.ArgumentParser(description="OHLCV 5분봉 캐시 증분 업데이트")
    ap.add_argument(
        "--daily-cache-dir",
        default="/Users/beye82/Workspace/ai-trade/data/ohlcv_cache",
        help="일봉 캐시 디렉토리 (종목 목록 추출용)",
    )
    ap.add_argument(
        "--cache-dir",
        default="/Users/beye82/Workspace/ai-trade/data/ohlcv_cache_5m",
        help="5분봉 캐시 출력 디렉토리",
    )
    ap.add_argument("--days", type=int, default=15, help="수집 대상 영업일 수 (기본 15)")
    ap.add_argument("--keep-days", type=int, default=45, help="캐시 보관 일수 (기본 45)")
    args = ap.parse_args()

    if not os.path.isdir(args.daily_cache_dir):
        raise SystemExit(f"일봉 캐시 디렉토리 없음: {args.daily_cache_dir}")

    asyncio.run(run(args.daily_cache_dir, args.cache_dir, args.days, args.keep_days))


if __name__ == "__main__":
    main()
