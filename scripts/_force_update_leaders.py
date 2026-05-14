"""운영 종목 8개 일봉 강제 갱신 (gap 무시) — 5/14 캔들 시뮬레이션용 임시 스크립트.

update_ohlcv_cache.py 는 gap<=1 이면 skip 하므로 당일 캔들을 못 받는다.
이 스크립트는 운영 종목만 무조건 fetch_daily 호출해 캐시에 merge 한다.

사용:
    set -a; . ./.env.local; set +a
    venv/bin/python scripts/_force_update_leaders.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "ohlcv_cache"

SYMBOLS = {
    "319400": "현대무벡스",
    "066570": "LG전자",
    "090710": "휴림로봇",
    "010170": "대한광통신",
    "003280": "흥아해운",
    "012200": "계양전기",
    "356680": "엑스게이트",
    "012860": "모베이스전자",
}


async def main() -> None:
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth, rate_limit_seconds=0.55)

    for sym, name in SYMBOLS.items():
        try:
            candles = await fetcher.fetch_daily(symbol=sym)
        except Exception as e:
            print(f"[ERR ] {sym} {name}: {type(e).__name__}: {e}")
            continue

        p = CACHE / f"{sym}.json"
        existing = json.loads(p.read_text())["data"] if p.exists() else []
        by_date = {r["date"]: r for r in existing}
        before_last = max(by_date) if by_date else "—"

        for c in candles:
            d = c.timestamp.strftime("%Y%m%d")
            by_date[d] = {
                "date": d,
                "open": int(c.open),
                "high": int(c.high),
                "low": int(c.low),
                "close": int(c.close),
                "volume": int(c.volume),
            }
        merged = sorted(by_date.values(), key=lambda r: r["date"])
        p.write_text(json.dumps({"data": merged}))
        print(
            f"[OK  ] {sym} {name:<10} {before_last} -> {merged[-1]['date']} "
            f"({len(existing)}->{len(merged)}봉, +{len(merged) - len(existing)})"
        )


if __name__ == "__main__":
    asyncio.run(main())
