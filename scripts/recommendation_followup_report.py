#!/usr/bin/env python3
"""추천 종목 2주 추적 리포트 — 스캔/예측 추천의 사후 성과 통계.

사용자 요청 (2026-09-28)
------------------------
데일리 종목 스캔 + 팀 에이전트 상승예측 종목의 **추천일 포함 2주간** 상승/하락·
최고상승·최저하락 통계를 장 마감 후 텔레그램으로 받는다.

원천
----
- `logs/watchlist_<date>.json`    — daily_screener 스캔 (BARRO_PREMARKET_MAX_WATCHLIST)
- `logs/predictions_<date>.json`  — 멀티에이전트 예측 (BARRO_PREMARKET_TOP_N)
  두 파일 모두 평일 08:25 프리마켓 브리핑이 생성한다.
- `data/ohlcv_cache/<code>.json`  — 일봉 (EOD 15:40 갱신)

측정 규칙
--------
- **기준가 = 추천일 시가(open)**. 추천이 08:25 장 시작 전에 나오므로 실제 진입 가능한
  첫 가격이 그날 시가다. 전일 종가를 쓰면 시초갭이 성과로 잡혀 과대·과소가 된다.
- **관측 창 = 추천일 포함 N 거래일**(기본 10 = 2주). 캔들이 있는 거래일만 센다.
- **최고상승 = max(high)/기준가 − 1**, **최저하락 = min(low)/기준가 − 1** — 장중 극값
  기준이라 "2주 안에 얼마까지 올랐나/빠졌나" 를 본다.
- **종료수익 = 창 마지막 거래일 종가/기준가 − 1**. 창이 아직 안 찬 추천은 `진행중(k/N)`.
- 추천일 캔들이 없으면(휴장·상장폐지·캐시 미수록) 그 건은 제외하고 집계에 표시한다.

주의: 이 리포트는 **관측 전용**이다. 주문·전략 설정에 일절 영향을 주지 않는다.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

KST = timezone(timedelta(hours=9))
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _reco_dir() -> Path:
    return Path(os.environ.get("BARRO_AI_TRADE_DIR", str(_REPO / "logs")))


def _cache_dir() -> Path:
    """일봉 캐시 경로. env 우선 → 리포 기본. 워크트리 실행 시 `_REPO` 가 워크트리라
    캐시가 없어 **조용히 0건** 이 나오던 문제가 있어 `_require_cache()` 로 검증한다."""
    for env_key in ("BARRO_OHLCV_CACHE_DIR", "BARRO_PREMARKET_CACHE_DIR"):
        v = (os.environ.get(env_key) or "").strip()
        if v:
            return Path(v)
    return _REPO / "data" / "ohlcv_cache"


def _require_cache() -> Path:
    """캐시가 없으면 조용히 0건을 내지 말고 명확히 실패한다."""
    d = _cache_dir()
    n = len(list(d.glob("*.json"))) if d.is_dir() else 0
    if n < 100:
        raise SystemExit(
            f"[ERR] 일봉 캐시를 찾을 수 없다: {d} (json {n}개)\n"
            "      BARRO_OHLCV_CACHE_DIR 를 설정하거나 리포 루트에서 실행할 것.\n"
            "      (워크트리에서 실행하면 캐시가 없어 전 건이 '추적 제외' 로 빠진다)"
        )
    return d


def _load_reco(prefix: str, d: str) -> list[dict]:
    """추천 파일 → [{code, name, meta}]. 없으면 빈 목록."""
    f = _reco_dir() / f"{prefix}_{d}.json"
    try:
        rows = json.loads(f.read_text(encoding="utf-8")).get("stocks") or []
    except Exception:
        return []
    out = []
    for r in rows:
        code = str(r.get("code") or r.get("symbol") or "").lstrip("A").zfill(6)
        if not code or code == "000000":
            continue
        out.append({"code": code, "name": r.get("name") or "", "raw": r})
    return out


_candle_cache: dict[str, list[dict]] = {}


def _candles(code: str) -> list[dict]:
    if code in _candle_cache:
        return _candle_cache[code]
    try:
        d = json.loads((_cache_dir() / f"{code}.json").read_text(encoding="utf-8"))
        rows = d if isinstance(d, list) else (d.get("candles") or d.get("data") or [])
        rows = [r for r in rows if r.get("date")]
        rows.sort(key=lambda r: str(r["date"]))
    except Exception:
        rows = []
    _candle_cache[code] = rows
    return rows


def track(code: str, reco_day: str, window: int) -> dict | None:
    """추천일 포함 window 거래일 추적. 추천일 캔들 없으면 None."""
    rows = _candles(code)
    key = reco_day.replace("-", "")
    idx = next((i for i, r in enumerate(rows) if str(r["date"]) == key), None)
    if idx is None:
        return None
    seg = rows[idx: idx + window]
    try:
        base = float(seg[0]["open"])
    except Exception:
        base = 0.0
    if base <= 0:
        return None
    highs = [float(r["high"]) for r in seg if r.get("high") is not None]
    lows = [float(r["low"]) for r in seg if r.get("low") is not None]
    last_close = float(seg[-1]["close"])
    return {
        "code": code,
        "days": len(seg),
        "complete": len(seg) >= window,
        "base": base,
        "max_up": (max(highs) / base - 1) * 100 if highs else 0.0,
        "max_dn": (min(lows) / base - 1) * 100 if lows else 0.0,
        "ret": (last_close / base - 1) * 100,
        "last_close": last_close,
        "last_date": str(seg[-1]["date"]),
    }


def _fmt_pct(v: float) -> str:
    return f"{v:+.1f}%"


def summarize(items: list[dict]) -> dict:
    if not items:
        return {}
    rets = [x["ret"] for x in items]
    ups = [x for x in items if x["ret"] > 0]
    return {
        "n": len(items),
        "up": len(ups),
        "down": len(items) - len(ups),
        "win": len(ups) / len(items) * 100,
        "ret_avg": statistics.mean(rets),
        "ret_med": statistics.median(rets),
        "max_up_avg": statistics.mean(x["max_up"] for x in items),
        "max_dn_avg": statistics.mean(x["max_dn"] for x in items),
        "best": max(items, key=lambda x: x["max_up"]),
        "worst": min(items, key=lambda x: x["max_dn"]),
        "complete": sum(1 for x in items if x["complete"]),
    }


def _block(title: str, items: list[dict], top: int) -> list[str]:
    s = summarize(items)
    if not s:
        return [f"*{title}*", "  추적 가능한 추천 없음", ""]
    L = [
        f"*{title}*  (추천 {s['n']}건 · 창완료 {s['complete']})",
        f"  상승 {s['up']} / 하락 {s['down']}  (상승률 {s['win']:.0f}%)",
        f"  종료수익  평균 {_fmt_pct(s['ret_avg'])} · 중앙 {_fmt_pct(s['ret_med'])}",
        f"  최고상승  평균 {_fmt_pct(s['max_up_avg'])}",
        f"  최저하락  평균 {_fmt_pct(s['max_dn_avg'])}",
    ]
    # 같은 종목이 여러 날 추천되면 Top N 을 독점한다 → 종목당 최고 1건만 남긴다.
    def _uniq(rows: list[dict]) -> list[dict]:
        seen: set[str] = set()
        out = []
        for x in rows:
            if x["code"] in seen:
                continue
            seen.add(x["code"])
            out.append(x)
            if len(out) >= top:
                break
        return out
    up = _uniq(sorted(items, key=lambda x: -x["max_up"]))
    dn = _uniq(sorted(items, key=lambda x: x["max_dn"]))
    L.append(f"  ▲ 최고상승 Top{len(up)}")
    for x in up:
        L.append(f"    {x['code']} {x['name'][:10]:<11}{_fmt_pct(x['max_up'])}"
                 f"  (종료 {_fmt_pct(x['ret'])}, {x['reco']})")
    L.append(f"  ▼ 최저하락 Top{len(dn)}")
    for x in dn:
        L.append(f"    {x['code']} {x['name'][:10]:<11}{_fmt_pct(x['max_dn'])}"
                 f"  (종료 {_fmt_pct(x['ret'])}, {x['reco']})")
    L.append("")
    return L


def build_report(lookback_days: int, window: int, top: int) -> tuple[str, dict]:
    _require_cache()
    today = datetime.now(KST).date()
    since = (today - timedelta(days=lookback_days)).isoformat()
    dates = sorted({
        m.group(1) for f in glob.glob(str(_reco_dir() / "watchlist_*.json"))
        if (m := _DATE_RE.search(f)) and m.group(1) >= since
    })
    # 휴장일 추천은 집계에서 뺀다 — 프리마켓 잡이 평일 08:25 에 무조건 돌아 휴장일에도
    #   파일을 만들지만 그 날은 거래가 없어 추적 자체가 성립하지 않는다(캔들 부재).
    #   캘린더(#232)로 사전 판정한다. 판정 불가 시 남겨두고 "캔들 없음" 으로 빠진다.
    try:
        from backend.core.market_session.market_calendar import is_market_holiday
        holiday_dates = {d for d in dates if is_market_holiday(date.fromisoformat(d))[0]}
    except Exception:
        holiday_dates = set()
    dates = [d for d in dates if d not in holiday_dates]

    scan: list[dict] = []
    pred: list[dict] = []
    missing = {"scan": 0, "pred": 0}
    per_day: list[tuple[str, int, int]] = []
    for d in dates:
        ds = _load_reco("watchlist", d)
        dp = _load_reco("predictions", d)
        cs = cp = 0
        for src, rows, bucket in (("scan", ds, scan), ("pred", dp, pred)):
            for r in rows:
                t = track(r["code"], d, window)
                if t is None:
                    missing[src] += 1
                    continue
                t.update(name=r["name"], reco=d[5:], src=src)
                bucket.append(t)
                if src == "scan":
                    cs += 1
                else:
                    cp += 1
        per_day.append((d, cs, cp))

    L = [
        f"📊 *추천 종목 {window}거래일 추적 리포트*",
        f"_{today} 장 마감 기준 · 추천일 포함 {window}거래일(≈2주) 관측_",
        f"_기준가 = 추천일 시가 · 최고/최저는 장중 고가·저가_",
        "",
    ]
    L += _block("🔍 데일리 종목 스캔", scan, top)
    L += _block("🤖 팀 에이전트 상승예측", pred, top)

    # 종목 단위 요약 — 같은 종목이 여러 날 추천되면 건수 집계가 그 종목에 치우친다.
    def _by_symbol(rows: list[dict]) -> list[dict]:
        best: dict[str, dict] = {}
        for x in rows:
            cur = best.get(x["code"])
            if cur is None or x["max_up"] > cur["max_up"]:
                best[x["code"]] = x
        return list(best.values())

    for lab, rows in (("🔍 스캔", scan), ("🤖 예측", pred)):
        u = _by_symbol(rows)
        if not u:
            continue
        su = summarize(u)
        L += [f"*{lab} — 종목 단위* (고유 {su['n']}종목, 중복추천 1회로 축약)",
              f"  상승 {su['up']} / 하락 {su['down']} ({su['win']:.0f}%)"
              f" · 최고상승 평균 {_fmt_pct(su['max_up_avg'])}"
              f" · 최저하락 평균 {_fmt_pct(su['max_dn_avg'])}", ""]

    both = scan + pred
    if both:
        s = summarize(both)
        L += [
            "*합계*",
            f"  추천 {s['n']}건 · 상승 {s['up']} / 하락 {s['down']} ({s['win']:.0f}%)",
            f"  종료수익 평균 {_fmt_pct(s['ret_avg'])} · 최고상승 평균 {_fmt_pct(s['max_up_avg'])}"
            f" · 최저하락 평균 {_fmt_pct(s['max_dn_avg'])}",
            "",
        ]
    L.append("*추천일별 추적 건수* (스캔/예측)")
    for d, cs, cp in per_day:
        L.append(f"  {d[5:]}  {cs}/{cp}")
    if holiday_dates:
        L += ["", f"_휴장일 추천 제외: {', '.join(sorted(x[5:] for x in holiday_dates))}"
                  " (프리마켓 잡이 휴장일에도 파일을 만들지만 거래가 없어 추적 불가)_"]
    if missing["scan"] or missing["pred"]:
        L += ["", f"_추적 제외: 스캔 {missing['scan']} · 예측 {missing['pred']}"
                  " (추천일 캔들 없음 — 당일 장중·상장폐지·캐시 미수록)_"]
    L += ["", "_관측 전용 리포트 — 주문·전략 설정에 영향 없음_"]
    return "\n".join(L), {"scan": len(scan), "pred": len(pred), "dates": len(dates)}


async def _send(text: str) -> None:
    from backend.core.notify.telegram import TelegramNotifier
    notifier = TelegramNotifier.from_env()
    res = await notifier.send_chunks(text)
    print(f"  텔레그램 발송: {len(res)}개 메시지")


def main() -> int:
    ap = argparse.ArgumentParser(description="추천 종목 2주 추적 리포트")
    ap.add_argument("--window", type=int, default=10,
                    help="관측 거래일 수 (기본 10 = 약 2주, 추천일 포함)")
    ap.add_argument("--lookback", type=int, default=21,
                    help="며칠 전 추천까지 볼지 (달력일, 기본 21)")
    ap.add_argument("--top", type=int, default=5, help="Top N 표시 (기본 5)")
    ap.add_argument("--telegram", action="store_true", help="텔레그램 발송")
    ap.add_argument("--save", metavar="PATH", help="리포트를 파일로 저장")
    args = ap.parse_args()

    text, meta = build_report(args.lookback, args.window, args.top)
    print(text)
    print(f"\n[집계] 추천일 {meta['dates']}일 · 스캔 {meta['scan']}건 · 예측 {meta['pred']}건")
    if args.save:
        Path(args.save).write_text(text, encoding="utf-8")
        print(f"  저장: {args.save}")
    if args.telegram:
        asyncio.run(_send(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
