"""종목명 마스터 생성 — ka10099 종목정보 리스트로 {code: name} 을 data/stock_names.json 에 저장.

키움 키 보유 머신(운영/개발)에서 실행. 키 없으면 안내만 출력하고 종료.

사용:
    python scripts/build_stock_names.py            # 코스피+코스닥 전 종목
    KIWOOM_BASE_URL=https://api.kiwoom.com python scripts/build_stock_names.py

환경변수: KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_BASE_URL(기본 mock).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# repo_root 를 path 에 추가
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# .env.local 로드(있으면)
try:
    from dotenv import load_dotenv

    for env_file in (".env.local", ".env"):
        p = _ROOT / env_file
        if p.exists():
            load_dotenv(p, override=False)
except Exception:
    pass

_OUT = _ROOT / "data" / "stock_names.json"
# ka10099 mrkt_tp: 0=코스피, 10=코스닥
_MARKETS = [("0", "코스피"), ("10", "코스닥")]


async def _build() -> dict[str, str]:
    from pydantic import SecretStr

    from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
    from backend.core.gateway.kiwoom_quotes import KiwoomQuotes

    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    quotes = KiwoomQuotes(oauth=oauth, rate_limit_seconds=0.3)
    names: dict[str, str] = {}
    for mrkt_tp, label in _MARKETS:
        part = await quotes.stock_names(mrkt_tp=mrkt_tp)
        print(f"  {label}(mrkt_tp={mrkt_tp}): {len(part)} 종목")
        names.update(part)
    return names


def main() -> int:
    if not os.environ.get("KIWOOM_APP_KEY") or not os.environ.get("KIWOOM_APP_SECRET"):
        print("[안내] KIWOOM_APP_KEY / KIWOOM_APP_SECRET 환경변수가 없습니다.")
        print("       키움 키가 있는 머신에서 실행하세요. (기존 stock_names.json 유지)")
        return 1

    print(f"[build_stock_names] ka10099 종목명 마스터 생성 → {_OUT}")
    names = asyncio.run(_build())
    if not names:
        print("[경고] 종목명을 하나도 받지 못했습니다. 파일을 덮어쓰지 않습니다.")
        return 2

    # 기존 파일(직접 시드한 major 종목 등)과 병합 — 신규 API 값 우선
    existing: dict[str, str] = {}
    if _OUT.exists():
        try:
            existing = json.loads(_OUT.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    merged = {**existing, **names}

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(
        json.dumps(merged, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8",
    )
    print(f"[완료] {len(merged)} 종목 저장 (신규 {len(names)}, 기존 병합 {len(existing)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
