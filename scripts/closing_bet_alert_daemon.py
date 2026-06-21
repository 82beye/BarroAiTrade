"""
종가베팅(종베) 알림 데몬 — 주문 없음, 텔레그램 시그널 알림 전용.

사용자 요청: 종베 전략은 당분간 자동 매수/매도하지 않고, 시그널 발생 시 텔레그램으로
매수/매도 메시지만 보낸다. → **주문 executor 를 일절 붙이지 않음(사고 위험 0).**

기능:
- 매수 알림: 15:00~15:20(KST) 주도주 중 종베 셋업(신고가 돌파 장대양봉 + 분봉 자금유입)
  발생 시 텔레그램 "[종베 매수 시그널]" 전송. (자동매수 X)
- 매도 알림: 등록된 종베 포지션을 모니터링해 +TP/−SL/익일10시/D3 도달 시 텔레그램
  "[종베 매도 시그널]" 전송. (자동매도 X) — 같은 조건은 1회만(중복 방지).
- 포지션은 사용자가 실제 진입분만 등록: --add (정확한 매도 알림의 전제).

포지션 파일: data/closing_bet_positions.json
  [{"symbol","name","entry_price","qty","entry_date","tp_pct","sl_pct","alerted":[]}]

사용:
  # 포지션 등록(실제 진입분)
  python scripts/closing_bet_alert_daemon.py --add 000660 2512300 10 --name 하이닉스
  python scripts/closing_bet_alert_daemon.py --list
  # 매도 스캔(운영 머신, 텔레그램) — 보통 09:00~10:30 가동
  python scripts/closing_bet_alert_daemon.py --mode sell
  # 매수 스캔(15:00~15:20)
  python scripts/closing_bet_alert_daemon.py --mode buy
  # 루프(매수창+매도 모니터 동시, interval 초)
  python scripts/closing_bet_alert_daemon.py --mode loop --interval 60
  # 테스트(텔레그램 대신 stdout, 캐시 현재가)
  python scripts/closing_bet_alert_daemon.py --mode sell --dry-print --from-cache
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.strategy.closing_bet import (  # noqa: E402
    ClosingBetParams, ClosingBetStrategy,
)
from backend.models.market import MarketType, OHLCV  # noqa: E402
from backend.models.strategy import AnalysisContext  # noqa: E402

_KST = timezone(timedelta(hours=9))
# 데이터 경로: 운영/개발 머신 독립 — 기본=레포/data, BARRO_DATA_DIR 로 override(개발 테스트).
_MAIN_DATA = Path(os.environ.get("BARRO_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data")))
POS_FILE = _MAIN_DATA / "closing_bet_positions.json"
BUY_WINDOW = (dtime(15, 0), dtime(15, 20))


def _build_oauth():
    """KIWOOM_APP_KEY/SECRET/BASE_URL(.env.local)로 OAuth 생성 (from_env 부재 대체)."""
    from pydantic import SecretStr
    from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
    return KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )

# 2026-06-22 — 이격도 게이트(disparity_yellow, 5일선 +14.25%) env 토글. default OFF(현행 byte-identical).
#   사용자 dry-run 선택: BARRO_CB_DISPARITY_YELLOW=1 → ON(종베 net 개선, 알림 빈도↓).
_CB_DISPARITY = os.environ.get("BARRO_CB_DISPARITY_YELLOW", "0").strip().lower() in ("1", "true", "yes", "on")
PARAMS = ClosingBetParams(require_eod_window=False, require_money_flow=True,
                          require_zone=False, require_leader_meta=False, min_atr_pct=0.035,
                          require_disparity_yellow=_CB_DISPARITY, disparity_yellow_threshold=0.1425)


def _now() -> datetime:
    return datetime.now(_KST)


# ── 포지션 파일 ──────────────────────────────────────────────────────────────
def load_positions() -> list[dict]:
    if not POS_FILE.exists():
        return []
    try:
        return json.loads(POS_FILE.read_text())
    except Exception:
        return []


def save_positions(pos: list[dict]) -> None:
    POS_FILE.parent.mkdir(parents=True, exist_ok=True)
    POS_FILE.write_text(json.dumps(pos, ensure_ascii=False, indent=2))


def add_position(symbol, entry_price, qty, name, tp_pct, sl_pct, entry_date=None) -> None:
    pos = load_positions()
    pos = [p for p in pos if p["symbol"] != symbol]   # 동일 종목 교체
    pos.append({"symbol": symbol, "name": name or symbol, "entry_price": float(entry_price),
                "qty": int(qty), "entry_date": entry_date or _now().date().isoformat(),
                "tp_pct": float(tp_pct), "sl_pct": float(sl_pct), "alerted": []})
    save_positions(pos)
    print(f"등록: {symbol} {name} @ {float(entry_price):,.0f} x{qty} (TP+{tp_pct}%/SL-{sl_pct}%)")


# ── 알림 전송 ────────────────────────────────────────────────────────────────
async def notify(text: str, dry_print: bool) -> None:
    print(text + "\n" + "─" * 40)
    if dry_print:
        return
    from backend.core.notify.telegram import TelegramNotifier
    try:
        await TelegramNotifier.from_env().send(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[텔레그램 전송 실패] {exc}")


# ── 매도 시그널 판정 ─────────────────────────────────────────────────────────
def sell_signals(pos: dict, cur: float, now: datetime) -> list[tuple[str, str]]:
    e, tp, sl = pos["entry_price"], pos.get("tp_pct", 2.0), pos.get("sl_pct", 3.0)
    out: list[tuple[str, str]] = []
    if cur >= e * (1 + tp / 100):
        out.append(("TP", f"+{tp:.1f}% 익절 도달"))
    if cur <= e * (1 - sl / 100):
        out.append(("SL", f"-{sl:.1f}% 손절 도달"))
    ed = date.fromisoformat(pos["entry_date"])
    if now.date() > ed and now.time() >= dtime(10, 0):
        out.append(("MORNING", "익일 10시 정산 시각 — 아침 슈팅 정리 구간"))
    held = (now.date() - ed).days
    if held >= 3:
        out.append(("D3", f"D{held} 보유한도(달력일) 도달"))
    fired = set(pos.get("alerted", []))
    return [(k, m) for k, m in out if k not in fired]


# ── 데이터 로더(캐시 모드 현재가) ───────────────────────────────────────────
def _cache_price(symbol: str) -> float | None:
    p = _MAIN_DATA / "ohlcv_cache" / f"{symbol}.json"
    if not p.exists():
        return None
    d = json.load(open(p))["data"]
    return float(d[-1]["close"]) if d else None


async def _live_price(fetcher, symbol: str) -> float | None:
    try:
        bars = await fetcher.fetch_minute(symbol=symbol, tic_scope="1")
        return float(bars[-1].close) if bars else None
    except Exception:
        return None


# ── 매도 스캔 ────────────────────────────────────────────────────────────────
async def scan_sell(dry_print: bool, from_cache: bool) -> None:
    positions = load_positions()
    if not positions:
        print("등록된 종베 포지션 없음 (--add 로 등록).")
        return
    fetcher = None
    if not from_cache:
        from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
        fetcher = KiwoomNativeCandleFetcher(oauth=_build_oauth())
    now = _now()
    changed = False
    for p in positions:
        cur = _cache_price(p["symbol"]) if from_cache else await _live_price(fetcher, p["symbol"])
        if cur is None:
            print(f"  {p['symbol']}: 현재가 조회 실패 — skip")
            continue
        pnl = (cur - p["entry_price"]) / p["entry_price"] * 100
        for key, reason in sell_signals(p, cur, now):
            await notify(
                f"🔔 [종베 매도 시그널] {p['name']}({p['symbol']})\n"
                f"사유: {reason}\n"
                f"진입 {p['entry_price']:,.0f} → 현재 {cur:,.0f} ({pnl:+.2f}%)\n"
                f"수량 {p['qty']}주\n"
                f"※ 자동매도 안 함 — 직접 매도 판단하세요.", dry_print)
            p.setdefault("alerted", []).append(key)
            changed = True
    if changed and not from_cache:
        save_positions(positions)   # 캐시 테스트는 상태 저장 안 함


# ── 매수 스캔 ────────────────────────────────────────────────────────────────
def _load_daily(symbol: str) -> list[OHLCV]:
    p = _MAIN_DATA / "ohlcv_cache" / f"{symbol}.json"
    if not p.exists():
        return []
    out = []
    for r in json.load(open(p))["data"]:
        out.append(OHLCV(symbol=symbol, timestamp=datetime.strptime(str(r["date"]), "%Y%m%d"),
                         open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                         close=float(r["close"]), volume=float(r["volume"]), market_type=MarketType.STOCK))
    return out


async def scan_buy(dry_print: bool, from_cache: bool, symbols: list[str], top_n: int) -> None:
    strat = ClosingBetStrategy(PARAMS)
    # [검증버전(paper_scan) 일치, 2026-06-18] 5분봉 + leader_meta 주입 → money_flow 게이트 활성화.
    #   candidates 튜플: (sym, name, daily, m5, meta)
    if from_cache:
        candidates = [(s, s, _load_daily(s), None, None) for s in symbols]
    else:
        from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
        from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
        oauth = _build_oauth()
        leaders = await KiwoomNativeLeaderPicker(oauth=oauth, min_flu_rate=1.0).pick(top_n=top_n)
        f = KiwoomNativeCandleFetcher(oauth=oauth)
        candidates = []
        for lc in leaders:
            try:
                daily = await f.fetch_daily(symbol=lc.symbol)
                m5 = await f.fetch_minute(symbol=lc.symbol, tic_scope="5")   # money_flow 게이트용 5분봉
            except Exception:
                continue
            meta = {"rank_trade_value": getattr(lc, "rank_trade_value", None),
                    "trade_value": getattr(lc, "trade_value", None)}
            candidates.append((lc.symbol, lc.name, daily, m5, meta))
    for sym, name, daily, m5, meta in candidates:
        if not daily or len(daily) < PARAMS.min_candles:
            continue
        sig = strat._analyze_v2(AnalysisContext(symbol=sym, name=name, candles=daily,
                                                market_type=MarketType.STOCK,
                                                intraday_candles=m5 or None, theme_context=meta))
        if sig:
            await notify(
                f"🔔 [종베 매수 시그널] {name}({sym})\n"
                f"{sig.reason}\n"
                f"진입가(종가) ~{sig.price:,.0f}  score={sig.score}\n"
                f"※ 자동매수 안 함 — 직접 매수 판단하세요. 매수하면 --add 로 등록.", dry_print)


# ── 메인 ─────────────────────────────────────────────────────────────────────
async def _run(args) -> None:
    if args.mode == "sell":
        await scan_sell(args.dry_print, args.from_cache)
    elif args.mode == "buy":
        if not args.force and not (BUY_WINDOW[0] <= _now().time() <= BUY_WINDOW[1]):
            print(f"매수창(15:00~15:20) 밖 — skip. --force 로 무시.")
            return
        await scan_buy(args.dry_print, args.from_cache,
                       [s.strip() for s in args.symbols.split(",") if s.strip()], args.top)
    elif args.mode == "loop":
        while True:
            t = _now().time()
            # [2026-06-22] 루프 회복력 — scan_buy/scan_sell 의 API 에러(429/timeout/auth 등)가
            #   루프를 죽이지 않게 격리(launchd KeepAlive 재기동 churn 방지). CancelledError 는
            #   Exception 이 아니므로 SIGTERM/종료 신호는 그대로 전파된다.
            try:
                if BUY_WINDOW[0] <= t <= BUY_WINDOW[1]:
                    await scan_buy(args.dry_print, args.from_cache,
                                   [s.strip() for s in args.symbols.split(",") if s.strip()], args.top)
                if dtime(9, 0) <= t <= dtime(15, 30):
                    await scan_sell(args.dry_print, args.from_cache)
            except Exception as e:  # noqa: BLE001 — 사이클 격리(다음 주기 계속)
                print(f"[cb-loop-err] {type(e).__name__}: {e}")
            await asyncio.sleep(args.interval)


def main() -> None:
    ap = argparse.ArgumentParser(description="종베 알림 데몬 (주문 없음, 텔레그램 전용)")
    ap.add_argument("--mode", choices=["buy", "sell", "loop"], default="sell")
    ap.add_argument("--add", nargs=3, metavar=("SYMBOL", "ENTRY", "QTY"))
    ap.add_argument("--name", default="")
    ap.add_argument("--tp", type=float, default=2.0, help="익절%% (대형주 2)")
    ap.add_argument("--sl", type=float, default=3.0, help="손절%%")
    ap.add_argument("--entry-date", default="", help="실제 진입일 YYYY-MM-DD (기본=오늘). 종베 전일진입→익일등록 시 명시 권장")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--remove", default="")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--dry-print", action="store_true", help="텔레그램 대신 stdout")
    args = ap.parse_args()

    if args.add:
        add_position(args.add[0], args.add[1], args.add[2], args.name, args.tp, args.sl, args.entry_date or None)
        return
    if args.remove:
        save_positions([p for p in load_positions() if p["symbol"] != args.remove])
        print(f"제거: {args.remove}")
        return
    if args.list:
        for p in load_positions():
            print(f"  {p['symbol']} {p['name']} @ {p['entry_price']:,.0f} x{p['qty']} "
                  f"TP+{p.get('tp_pct',2)}%/SL-{p.get('sl_pct',3)}% alerted={p.get('alerted',[])}")
        return
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
