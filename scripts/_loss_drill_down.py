"""Phase 1 — 손실 종목 1개 단계별 진단.

진입 직전 시그널 · 보유 구간 1분봉 추적 · 청산 시점 비교 → 손실 원인 분류.
_daily_evening_pipeline.py 가 import_dir 에 남긴 executions.json 을 사용한다.

사용:
    python scripts/_loss_drill_down.py --symbol 027360 --date 2026-05-21
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from backend.core.backtester import IntradaySimulator, load_csv_candles
from backend.core.backtester.intraday_simulator import _build_strategies
from backend.models.market import MarketType
from backend.models.strategy import AnalysisContext

IMPORTS_ROOT = _REPO_ROOT / "analysis" / "imports"
STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "swing_38"]


# ─── 로딩 ────────────────────────────────────────────────────────────────


def latest_import_dir() -> Optional[Path]:
    if not IMPORTS_ROOT.is_dir():
        return None
    dirs = sorted(p for p in IMPORTS_ROOT.iterdir() if p.is_dir())
    return dirs[-1] if dirs else None


def find_subdir(import_dir: Path, name: str) -> Optional[Path]:
    if (import_dir / name).is_dir():
        return import_dir / name
    for p in import_dir.rglob(name):
        if p.is_dir():
            return p
    return None


def load_candles(ohlcv_dir: Path, code: str):
    """ohlcv_cache 에서 종목 CSV 중 캔들 수가 가장 많은 것 (= 1분봉) 로드."""
    best = []
    for path in sorted(ohlcv_dir.rglob(f"*{code}*")):
        if path.suffix.lower() != ".csv":
            continue
        try:
            candles = load_csv_candles(path, symbol=code)
        except Exception:
            continue
        if len(candles) > len(best):
            best = candles
    return best


def load_executions(import_dir: Path, code: str) -> list[dict]:
    path = import_dir / "executions.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [e for e in data if e.get("code") == code]


def _hms(ts) -> int:
    return int(ts.strftime("%H%M%S"))


def _candle_index_at(candles, hms: int, default: int) -> int:
    """HHMMSS 시각 이상인 첫 캔들 인덱스."""
    for i, c in enumerate(candles):
        if _hms(c.timestamp) >= hms:
            return i
    return default


# ─── 진단 ────────────────────────────────────────────────────────────────


def diagnose(candles, entry_idx, exit_idx, entry_price, exit_price) -> list[str]:
    """휴리스틱 손실 원인 분류."""
    tags: list[str] = []
    hold = candles[entry_idx:exit_idx + 1] if exit_idx >= entry_idx else []
    if not hold:
        return ["데이터 부족 — 보유 구간 캔들 없음"]

    peak = max(Decimal(str(c.high)) for c in hold)
    peak_gain = (peak - entry_price) / entry_price
    exit_gain = (exit_price - entry_price) / entry_price

    if entry_idx < 6:
        tags.append("시초가 노이즈 — 09시 초반 진입 (시초가 변동성 구간)")
    if peak_gain >= Decimal("0.03") and exit_gain < peak_gain - Decimal("0.02"):
        tags.append(
            f"청산 늦음 — 보유 중 +{peak_gain:.1%} 도달했으나 "
            f"{exit_gain:+.1%} 에 청산 (peak 대비 {exit_gain - peak_gain:.1%})"
        )
    if peak_gain < Decimal("0.01"):
        tags.append(
            f"매물대 미인식 가능 — 진입 후 고점이 +{peak_gain:.1%} 에 그침 "
            "(저항 즉시 직면)"
        )
    if Decimal(str(hold[0].close)) < entry_price * Decimal("0.99"):
        tags.append("진입 직후 즉시 하락 — 진입 타이밍/시그널 점검 필요")
    if not tags:
        tags.append("뚜렷한 단일 원인 미검출 — 진입 score·청산 정책 함께 검토")
    return tags


# ─── 출력 섹션 ────────────────────────────────────────────────────────────


def section_entry_signal(candles, entry_idx, symbol) -> None:
    print("\n[1] 진입 직전 시그널 재검증")
    window = candles[:entry_idx]
    if len(window) < 31:
        print(f"    캔들 부족 (entry_idx={entry_idx}, window={len(window)}) — 분석 생략")
        return
    ctx = AnalysisContext(
        symbol=symbol, candles=window, market_type=MarketType.STOCK,
    )
    fired = False
    for strat, sid in zip(_build_strategies(STRATEGIES), STRATEGIES):
        try:
            signal = strat.analyze(ctx)
        except Exception as exc:
            print(f"    {sid:<12} analyze 오류: {exc}")
            continue
        if signal is None:
            print(f"    {sid:<12} 시그널 없음")
        else:
            fired = True
            score = getattr(signal, "score", "?")
            stype = getattr(signal, "signal_type", sid)
            print(f"    {sid:<12} ▶ 시그널 발생  type={stype}  score={score}")
    if not fired:
        print("    ⚠ 어떤 전략도 진입 시그널을 재현하지 못함 — 진입 근거 점검 필요")


def section_pre_entry(candles, entry_idx, n: int = 8) -> None:
    print(f"\n[2] 진입 직전 {n}분봉 시퀀스")
    start = max(0, entry_idx - n)
    seq = candles[start:entry_idx + 1]
    if not seq:
        print("    데이터 없음")
        return
    base = seq[0].open
    print(f"    {'시각':>8} {'종가':>10} {'시초가대비':>10} {'거래량':>12}")
    for c in seq:
        chg = (Decimal(str(c.close)) - Decimal(str(base))) / Decimal(str(base))
        print(f"    {c.timestamp.strftime('%H:%M:%S'):>8} "
              f"{c.close:>10,.0f} {chg:>+9.2%} {c.volume:>12,.0f}")


def section_holding(candles, entry_idx, exit_idx, entry_price,
                    window_min: int = 30) -> None:
    print("\n[3] 보유 구간 추적 (peak/trough · TP·trail 후보)")
    hold = candles[entry_idx:exit_idx + 1] if exit_idx >= entry_idx else []
    if not hold:
        print("    보유 구간 캔들 없음")
        return

    peak_c = max(hold, key=lambda c: c.high)
    trough_c = min(hold, key=lambda c: c.low)
    pg = (Decimal(str(peak_c.high)) - entry_price) / entry_price
    tg = (Decimal(str(trough_c.low)) - entry_price) / entry_price
    print(f"    peak   {peak_c.timestamp:%H:%M:%S}  {peak_c.high:>10,.0f}  ({pg:+.2%})")
    print(f"    trough {trough_c.timestamp:%H:%M:%S}  {trough_c.low:>10,.0f}  ({tg:+.2%})")

    # TP·trail 후보 시점 수집
    events: list[str] = []
    peak = Decimal("-1e30")
    tp_hits: set[str] = set()
    trail_done = False
    for c in hold:
        high, close = Decimal(str(c.high)), Decimal(str(c.close))
        peak = max(peak, high)
        for tier in ("0.07", "0.05", "0.03"):
            if tier not in tp_hits and high >= entry_price * (1 + Decimal(tier)):
                tp_hits.add(tier)
                events.append(
                    f"    {c.timestamp:%H:%M:%S}  TP후보 +{Decimal(tier):.0%} 터치"
                )
        if not trail_done and peak > entry_price and close <= peak * Decimal("0.985"):
            trail_done = True
            events.append(f"    {c.timestamp:%H:%M:%S}  trail 후보 (peak −1.5% 이탈)")
    if events:
        print("    — 후보 시점 —")
        for e in sorted(events):
            print(e)
    else:
        print("    — TP·trail 후보 시점 없음 (진입 후 의미있는 상승 부재) —")

    print(f"    — 진입 후 {window_min}분 분봉 —")
    print(f"    {'시각':>8} {'종가':>10} {'손익':>9} {'거래량':>12}")
    for c in hold[:window_min]:
        gain = (Decimal(str(c.close)) - entry_price) / entry_price
        print(f"    {c.timestamp:%H:%M:%S} {c.close:>10,.0f} "
              f"{gain:>+8.2%} {c.volume:>12,.0f}")


def section_exit_compare(candles, symbol, entry_idx,
                         actual_exit_price, actual_exit_time) -> None:
    print("\n[4] 청산 비교 — 실제 vs IntradaySimulator")
    print(f"    실제 청산: {actual_exit_time}  가격 {actual_exit_price:,.0f}")
    if len(candles) < 31:
        print("    캔들 부족 — 시뮬 생략")
        return
    try:
        result = IntradaySimulator().run(candles, symbol=symbol, strategies=STRATEGIES)
    except Exception as exc:
        print(f"    시뮬 오류: {exc}")
        return
    sells = [t for t in result.trades if t.side == "sell"]
    if not sells:
        print("    시뮬 청산 없음 (해당 캔들에서 전략 미진입)")
        return
    for t in sells:
        print(f"    시뮬 청산: {t.timestamp.strftime('%H:%M:%S')}  "
              f"{t.strategy_id:<12} 가격 {t.price:,.0f}  reason={t.reason}")


# ─── 메인 ────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="손실 종목 단계별 진단 (Phase 1)")
    ap.add_argument("--symbol", required=True, help="종목코드 (예: 027360)")
    ap.add_argument("--date", required=True, help="영업일 YYYY-MM-DD")
    ap.add_argument("--import-dir", help="해제된 import 디렉터리 (생략 시 최신)")
    args = ap.parse_args()

    import_dir = (Path(args.import_dir) if args.import_dir
                  else IMPORTS_ROOT / args.date)
    if not import_dir.is_dir():
        import_dir = latest_import_dir()
    if import_dir is None or not import_dir.is_dir():
        raise SystemExit("import 디렉터리를 찾을 수 없음 — 먼저 daily pipeline 실행")
    print(f"=== 손실 진단: {args.symbol} ({args.date}) ===")
    print(f"import dir: {import_dir}")

    ohlcv_dir = find_subdir(import_dir, "ohlcv_cache")
    if ohlcv_dir is None:
        raise SystemExit("ohlcv_cache 디렉터리 없음")
    candles = load_candles(ohlcv_dir, args.symbol)
    if not candles:
        raise SystemExit(f"{args.symbol} 캔들 CSV 를 ohlcv_cache 에서 찾지 못함")
    print(f"캔들 {len(candles)} 봉 로드")

    fills = load_executions(import_dir, args.symbol)
    buys = [f for f in fills if f.get("side") == "buy"]
    sells = [f for f in fills if f.get("side") == "sell"]
    if not buys:
        raise SystemExit("executions.json 에 매수 체결 없음 — pipeline 먼저 실행")

    def _avg(rows):
        qv = sum(Decimal(str(r["qty"])) * Decimal(str(r["price"])) for r in rows)
        q = sum(Decimal(str(r["qty"])) for r in rows)
        return qv / q if q > 0 else Decimal(0)

    entry_price = _avg(buys)
    exit_price = _avg(sells) if sells else Decimal(str(candles[-1].close))
    entry_time = min(b.get("time", "") for b in buys) or "000000"
    exit_time = max((s.get("time", "") for s in sells), default="") or "장마감"

    entry_idx = _candle_index_at(candles, int(entry_time[:6] or 0), 0)
    exit_idx = (_candle_index_at(candles, int(exit_time[:6]), len(candles) - 1)
                if exit_time != "장마감" else len(candles) - 1)
    print(f"진입 {entry_time} @ {entry_price:,.0f} (idx {entry_idx})  /  "
          f"청산 {exit_time} @ {exit_price:,.0f} (idx {exit_idx})")

    section_entry_signal(candles, entry_idx, args.symbol)
    section_pre_entry(candles, entry_idx)
    section_holding(candles, entry_idx, exit_idx, entry_price)
    section_exit_compare(candles, args.symbol, entry_idx, exit_price, exit_time)

    print("\n[5] 진단 결론")
    for tag in diagnose(candles, entry_idx, exit_idx, entry_price, exit_price):
        print(f"    • {tag}")


if __name__ == "__main__":
    main()
