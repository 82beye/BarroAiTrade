#!/usr/bin/env python3
"""eod_recover.py — 누락된 EOD 실현손익(fill_audit) 사후 복구 (BAR-OPS).

이브닝 파이프라인(_eod_fill_backfill)이 실행되지 못한 날 — 예: 2026-06-15, 429 대응으로
intraday_buy_daemon 을 장중 종료해 장마감 후 EOD 시퀀스(L1523-27)가 미실행 — 의 브로커
실측 체결(ka10073)을 **D+2 조회창** 내에 재수집해 data/fill_audit.csv 에 백필한다.
verify_eod_data.py 가 감지한 누락을 복구하는 짝 스크립트.

읽기 전용 조회(ka10073)만 수행 — 주문/체결 없음. 동일 행은 dedup.

사용:
  BARRO_REPO=/repo BARRO_DATA_DIR=/repo/data ./.venv/bin/python scripts/eod_recover.py 20260615
  ./.venv/bin/python scripts/eod_recover.py 20260615 20260615   # 범위
종료코드: 0=성공/이미적재, 1=조회 0행(복구 불가), 2=오류
"""
import asyncio
import csv
import os
import sys
from pathlib import Path

REPO = Path(os.environ.get("BARRO_REPO") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(REPO))
DATA = Path(os.environ.get("BARRO_DATA_DIR") or (REPO / "data"))

HEADERS = ["date", "symbol", "name", "qty", "buy_price", "sell_price",
           "pnl", "pnl_rate", "commission", "tax"]


def load_env_local():
    p = REPO / ".env.local"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.split("#", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


load_env_local()

from pydantic import SecretStr  # noqa: E402
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth  # noqa: E402
from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher  # noqa: E402


def build_oauth() -> KiwoomNativeOAuth:
    ak = os.environ.get("KIWOOM_APP_KEY", "")
    sk = os.environ.get("KIWOOM_APP_SECRET", "")
    base = os.environ.get("KIWOOM_BASE_URL", "https://openapi.kiwoom.com")
    if not ak or not sk:
        raise SystemExit("KIWOOM_APP_KEY / KIWOOM_APP_SECRET 필요 (.env.local)")
    return KiwoomNativeOAuth(app_key=SecretStr(ak), app_secret=SecretStr(sk), base_url=base)


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: eod_recover.py YYYYMMDD [end_YYYYMMDD]")
        return 2
    start = sys.argv[1].replace("-", "")
    end = (sys.argv[2].replace("-", "") if len(sys.argv) > 2 else start)
    base = os.environ.get("KIWOOM_BASE_URL", "")
    print(f"== EOD 실현손익 복구 {start}..{end}  (base={base}, DATA={DATA}) ==")

    oauth = build_oauth()
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    entries = await account.fetch_realized_pnl(start, end)
    print(f"ka10073 반환: {len(entries)}행")
    if not entries:
        print("  실현 내역 0행 — 복구할 데이터 없음(모의서버 미보존 또는 당일 매도 0건).")
        return 1

    path = DATA / "fill_audit.csv"
    existing = set()
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing.add((r.get("date", ""), r.get("symbol", ""), r.get("qty", ""),
                              r.get("sell_price", ""), r.get("pnl", "")))
    new = []
    for e in entries:
        key = (e.date, e.symbol, str(e.qty), str(e.sell_price), str(e.pnl))
        if key in existing:
            continue
        new.append([e.date, e.symbol, e.name, e.qty, str(e.buy_price), str(e.sell_price),
                    str(e.pnl), str(e.pnl_rate), str(e.commission), str(e.tax)])
    if not new:
        print("  신규 행 없음 (이미 적재됨).")
        return 0

    is_new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(HEADERS)
        w.writerows(new)
    net = sum(float(r[6]) for r in new)
    print(f"  적재 {len(new)}행, 실현손익 합 {net:+,.0f}원 → {path}")
    for r in new:
        print(f"    {r[0]} {r[1]} {r[2]} qty={r[3]} 매도가={r[5]} 손익={float(r[6]):+,.0f}({r[7]}%)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
