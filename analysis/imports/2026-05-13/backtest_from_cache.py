#!/usr/bin/env python3
"""data/ohlcv_cache 기반 600봉 백테스트 — 5 전략 × N 종목.

5/12 단일 일자 분석(STRATEGY_TRACE.md)에서 세운 가설을 600봉(약 2.5년)으로
재검증한다:
  - swing_38 만 발화 vs 다른 4개 0건? → 600봉에서도?
  - f_zone/gold_zone 이 약세 종목 + 다른 일자엔 발화?
  - scalping_consensus 가 auto-load 후 실제 trade 만드는지?

입력: data/ohlcv_cache/<symbol>.json (일봉, date YYYYMMDD)
출력: analysis/imports/2026-05-13/REPORT_600BARS.md + stdout 매트릭스

사용:
    ./venv/bin/python analysis/imports/2026-05-13/backtest_from_cache.py
    # 사용자 정의 종목 리스트
    ./venv/bin/python analysis/imports/2026-05-13/backtest_from_cache.py --symbols 005930,000660
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

# PROJECT_ROOT 환경변수로 override 가능 — worktree 에서 실행 시 worktree 코드 import.
_env_root = os.environ.get("PROJECT_ROOT")
if _env_root:
    ROOT = Path(_env_root).resolve()
else:
    ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# legacy_scalping 의 DeprecationWarning 등 노이즈 차단
warnings.filterwarnings("ignore", category=DeprecationWarning)

from backend.core.backtester import IntradaySimulator  # noqa: E402
from backend.models.market import MarketType, OHLCV  # noqa: E402

CACHE_DIR = ROOT / "data" / "ohlcv_cache"
OUT_DIR = ROOT / "analysis" / "imports" / "2026-05-13"
REPORT_MD = OUT_DIR / "REPORT_600BARS.md"

# 5/12·5/13 운영 종목 (캐시 있는 8개만 — 439960·252670 제외)
DEFAULT_SYMBOLS = {
    "319400": "현대무벡스",
    "066570": "LG전자",
    "090710": "휴림로봇",
    "010170": "대한광통신",
    "003280": "흥아해운",
    "012200": "계양전기",
    "356680": "엑스게이트",
    "012860": "모베이스전자",
}

STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"]


def load_candles_from_cache(symbol: str, name: str) -> list[OHLCV]:
    """data/ohlcv_cache/<symbol>.json → list[OHLCV] (일봉, date 오름차순)."""
    p = CACHE_DIR / f"{symbol}.json"
    if not p.exists():
        raise FileNotFoundError(p)
    raw = json.loads(p.read_text())
    candles: list[OHLCV] = []
    for row in sorted(raw["data"], key=lambda r: r["date"]):
        ts = datetime.strptime(row["date"], "%Y%m%d")
        candles.append(
            OHLCV(
                symbol=symbol,
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0)),
                market_type=MarketType.STOCK,
            )
        )
    return candles


def backtest_one(symbol: str, name: str, position_value: float | None = None) -> dict:
    """한 종목 × 5 전략 백테스트."""
    candles = load_candles_from_cache(symbol, name)
    kwargs: dict = dict(
        warmup_candles=31,
        position_qty=Decimal("100"),
        entry_on_next_open=True,
        exit_on_intrabar=True,
        commission_pct=0.015,
        tax_pct_on_sell=0.18,
        slippage_pct=0.0,
    )
    if position_value is not None:
        kwargs["position_value"] = Decimal(str(position_value))
    sim = IntradaySimulator(**kwargs)
    result = sim.run(candles, symbol=symbol, strategies=STRATEGIES)
    return {
        "symbol": symbol,
        "name": name,
        "n_candles": len(candles),
        "pnl_by_strategy": dict(result.pnl_by_strategy),
        "win_rate_by_strategy": dict(result.win_rate_by_strategy),
        "trades_by_strategy": {
            sid: sum(1 for t in result.trades if t.strategy_id == sid and t.side == "buy")
            for sid in STRATEGIES
        },
    }


def fmt_money(v) -> str:
    try:
        return f"{int(round(float(v))):+,}"
    except Exception:
        return "—"


def render_per_symbol_table(rows: list[dict]) -> str:
    out = ["| symbol | name | candles | strategy | trades | win% | PnL |",
           "|--------|------|--------:|----------|-------:|-----:|----:|"]
    for r in rows:
        for sid in STRATEGIES:
            t = r["trades_by_strategy"][sid]
            pnl = r["pnl_by_strategy"].get(sid, 0)
            wr = r["win_rate_by_strategy"].get(sid, 0.0)
            out.append(
                f"| {r['symbol']} | {r['name']} | {r['n_candles']} | "
                f"{sid} | {t} | {wr*100:.1f}% | {fmt_money(pnl)} |"
            )
    return "\n".join(out)


def render_per_strategy_table(rows: list[dict]) -> str:
    """전략별 8종목 합계."""
    out = ["| strategy | active_symbols | total_trades | mean_win% | total_pnl | pnl/trade |",
           "|----------|---------------:|-------------:|----------:|----------:|----------:|"]
    for sid in STRATEGIES:
        active = sum(1 for r in rows if r["trades_by_strategy"][sid] > 0)
        total_trades = sum(r["trades_by_strategy"][sid] for r in rows)
        total_pnl = sum(float(r["pnl_by_strategy"].get(sid, 0)) for r in rows)
        wrs = [r["win_rate_by_strategy"].get(sid, 0.0) for r in rows
               if r["trades_by_strategy"][sid] > 0]
        mean_wr = (sum(wrs) / len(wrs) * 100) if wrs else 0.0
        pnl_per_trade = (total_pnl / total_trades) if total_trades else 0.0
        out.append(
            f"| {sid} | {active}/{len(rows)} | {total_trades} | "
            f"{mean_wr:.1f}% | {fmt_money(total_pnl)} | {fmt_money(pnl_per_trade)} |"
        )
    return "\n".join(out)


def build_report(rows: list[dict]) -> str:
    buf = StringIO()
    buf.write("# 600봉 백테스트 리포트 — 2026-05-13\n\n")
    buf.write(
        f"_입력: `data/ohlcv_cache/` ({len(rows)} 종목, 600봉/종목 ~2.5년 일봉)._  \n"
        f"_시뮬: IntradaySimulator(commission=0.015%/leg, tax=0.18% on sell, "
        f"warmup=31, position_qty=100)._\n\n"
    )

    buf.write("## A. 전략별 종합 (8 종목 합산)\n\n")
    buf.write(render_per_strategy_table(rows))
    buf.write("\n\n")

    # 자동 인사이트
    strat_summary = {sid: {
        "active": sum(1 for r in rows if r["trades_by_strategy"][sid] > 0),
        "total_trades": sum(r["trades_by_strategy"][sid] for r in rows),
        "total_pnl": sum(float(r["pnl_by_strategy"].get(sid, 0)) for r in rows),
    } for sid in STRATEGIES}

    active_count = {sid: s["active"] for sid, s in strat_summary.items()}
    inactive = [sid for sid, c in active_count.items() if c == 0]
    if inactive:
        buf.write(f"- **0건 전략 (600봉에서도)**: {', '.join(inactive)}\n")
    activeish = [sid for sid, c in active_count.items() if c > 0]
    if activeish:
        buf.write(f"- **활성 전략**: {', '.join(activeish)}\n")
    buf.write("\n")

    buf.write("## B. 종목 × 전략 매트릭스\n\n")
    buf.write(render_per_symbol_table(rows))
    buf.write("\n\n")

    buf.write("## C. STRATEGY_TRACE.md 가설 검증\n\n")
    buf.write("5/12 단일 일자 보고서에서 세운 가설:\n\n")
    # 1) swing_38 외 4개 0건?
    sw_act = active_count.get("swing_38", 0)
    fz_act = active_count.get("f_zone", 0)
    sfz_act = active_count.get("sf_zone", 0)
    gz_act = active_count.get("gold_zone", 0)
    sc_act = active_count.get("scalping_consensus", 0)
    buf.write(
        f"1. **swing_38 만 발화** — 5/12 결론: 4개 0건. 600봉에서:  \n"
        f"   - swing_38: **{sw_act}/{len(rows)}** active\n"
        f"   - f_zone: {fz_act}, sf_zone: {sfz_act}, gold_zone: {gz_act}, "
        f"scalping_consensus: {sc_act}\n"
        f"   → "
    )
    if fz_act + sfz_act + gz_act == 0:
        buf.write("**가설 유지** — 600봉에서도 f_zone/sf_zone/gold_zone 0건. "
                  "전략 진입 조건 자체가 강세 종목에 부적합 (구조적 미스매치 재확인).\n\n")
    else:
        buf.write("**가설 부분 기각** — 다일자에 걸쳐 보면 일부 전략 발화 케이스 존재.\n\n")

    buf.write(
        f"2. **scalping_consensus auto-load (BAR-OPS-09)** — 5/12 보고서 작성 시점엔 "
        f"provider 미주입. 600봉 + auto-load 후:  \n"
        f"   - scalping_consensus active: **{sc_act}/{len(rows)}**\n"
        f"   → "
    )
    if sc_act > 0:
        buf.write("**provider 연결 효과 확인** — 실제 trade 발생.\n\n")
    else:
        buf.write("provider 연결됐으나 일봉 + threshold 0.65 기준에서 진입 신호 없음. "
                  "일봉이 ScalpingCoordinator 의 분봉/틱 가정과 안 맞을 가능성.\n\n")

    # 3) swing_38 PnL이 압도적인지
    sw_pnl = strat_summary["swing_38"]["total_pnl"]
    buf.write(
        f"3. **swing_38 PnL 압도성** — 5/12: 단일 일자 +1,229,219원 (5종목 합계).  \n"
        f"   - 600봉 합계: **{fmt_money(sw_pnl)}원** ({strat_summary['swing_38']['total_trades']} trades)\n\n"
    )

    buf.write("## D. 종목별 swing_38 효율 (TOP)\n\n")
    swing_rows = sorted(rows, key=lambda r: float(r["pnl_by_strategy"].get("swing_38", 0)),
                        reverse=True)
    buf.write("| symbol | name | trades | win% | swing_38 PnL |\n")
    buf.write("|--------|------|-------:|-----:|-------------:|\n")
    for r in swing_rows:
        t = r["trades_by_strategy"]["swing_38"]
        pnl = r["pnl_by_strategy"].get("swing_38", 0)
        wr = r["win_rate_by_strategy"].get("swing_38", 0.0)
        buf.write(f"| {r['symbol']} | {r['name']} | {t} | {wr*100:.1f}% | {fmt_money(pnl)} |\n")

    buf.write("\n## Caveats\n\n")
    buf.write(
        "- 입력은 **일봉**(600 ≈ 2.5년) — IntradaySimulator 의 TP/SL ±1.5~7% 가 일봉 기준에서는 "
        "더 빨리 발동 (분봉에 비해 단순 백테스트 의미).\n"
        "- 시뮬이 다음 캔들 open 진입 + bar high/low 터치 청산 적용 (OPS-35).\n"
        "- 수수료 0.015%/leg, 매도세 0.18% 차감.\n"
        "- scalping_consensus 의 결과는 ScalpingCoordinator 가 일봉을 어떻게 처리하는지에 따라 달라짐.\n"
    )
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description="OHLCV 캐시 기반 600봉 백테스트")
    ap.add_argument("--symbols", help="콤마 구분 종목코드 (생략 시 운영 종목 8개)")
    ap.add_argument("--position-value", type=float, default=None,
                    help="S1: 종목당 명목 가치 고정 (예: 1000000). None 이면 100주 고정.")
    ap.add_argument("--report-suffix", default="",
                    help="REPORT_600BARS{suffix}.md 파일명 접미사 (모드별 분리 저장)")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.symbols:
        sym_map = {s.strip(): s.strip() for s in args.symbols.split(",")}
    else:
        sym_map = DEFAULT_SYMBOLS

    rows = []
    for sym, name in sym_map.items():
        try:
            r = backtest_one(sym, name, position_value=args.position_value)
            rows.append(r)
            sw_t = r["trades_by_strategy"]["swing_38"]
            sw_pnl = float(r["pnl_by_strategy"].get("swing_38", 0))
            print(f"[OK] {sym} {name:<10} candles={r['n_candles']} "
                  f"swing_38: trades={sw_t} pnl={fmt_money(sw_pnl)}")
        except FileNotFoundError:
            print(f"[SKIP] {sym} 캐시 누락")
        except Exception as e:
            print(f"[ERR ] {sym}: {type(e).__name__}: {e}")

    if not rows:
        print("결과 없음")
        return 2

    print("\n" + "=" * 60)
    print("전략별 종합 (8 종목)")
    print("=" * 60)
    print(render_per_strategy_table(rows))

    report = build_report(rows)
    out_path = OUT_DIR / f"REPORT_600BARS{args.report_suffix}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n리포트 저장: {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
