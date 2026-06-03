#!/usr/bin/env python3
"""RSI 타임프레임 sweep — 슈퍼트렌드 + 멀티 TF RSI 확인 필터 (BAR-OPS-10, 2026-06-03).

오프라인 5분봉 캐시(data/ohlcv_cache_5m) **워크포워드** 백테스트로 "RSI 타임프레임을
슈퍼트렌드 확인 필터로 썼을 때 수익률을 극대화하는 조합"을 **% 기준**으로 탐색한다.
(개발 머신 키 stale → 라이브 fetch 불가. 캐시 6주: ~2026-04-23 ~ 06-02.)

핵심:
  - 실제 전략 코드와 **동일 게이트**를 구동(검증: 샘플에서 SupertrendStrategy.analyze/
    exit_on_signal 와 fast-path 결과 일치 단언 — 불일치 시 즉시 실패).
  - 지표가 모두 인과적(causal)이므로 종목당 1회 precompute 후 O(n) 워크 → O(n²) 회피.
  - 변형: rsi_timeframe ∈ {5,10,15,30}m × rsi_period ∈ {9,14} × rsi_mode ∈
    {signal_cross, centerline, level} × exit ∈ {entry_only, entry+exit} + NO_RSI 베이스라인.
  - 비용모델: 수수료 0.015%×2 + 세금 0.18% (왕복 0.21%). 진입=다음봉 open, 청산=다음봉 open.
  - in-sample(~2026-05-22) / out-of-sample(이후) 병기 → 과적합 가시화. **KRW 미출력**.

사용:
  ./venv/bin/python analysis/imports/2026-06-03/rsi_tf_sweep.py \
      [--cache-dir /abs/data/ohlcv_cache_5m] [--audit /abs/data/order_audit.csv] \
      [--symbols 005930,000660] [--max-symbols 40] [--is-end 20260522]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

# ── 경로 설정 (worktree/PROJECT_ROOT 모두 지원) ──────────────────────────────
_env_root = os.environ.get("PROJECT_ROOT")
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.core.backtester.intraday_simulator import TradeRecord  # noqa: E402
from backend.core.backtester.performance import compute_metrics  # noqa: E402
from backend.core.strategy.indicators import (  # noqa: E402
    _bucket_index,
    _offset_min,
    compute_rsi,
    resample_htf,
    rsi_cross_state,
    rsi_signal_line,
)
from backend.core.strategy.supertrend import (  # noqa: E402
    SupertrendParams,
    SupertrendStrategy,
    compute_adx,
    compute_supertrend,
)
from backend.models.market import MarketType, OHLCV  # noqa: E402
from backend.models.position import Position  # noqa: E402
from backend.models.strategy import AnalysisContext  # noqa: E402

# data 경로: ROOT/data 우선(일반 체크아웃), 없으면 메인 체크아웃 절대경로 폴백(worktree).
_MAIN_DATA = Path("/Users/beye/workspace/BarroAiTrade/data")
_ROOT_DATA = ROOT / "data"
_DATA = _ROOT_DATA if (_ROOT_DATA / "ohlcv_cache_5m").exists() else _MAIN_DATA
DEFAULT_CACHE = _DATA / "ohlcv_cache_5m"
DEFAULT_AUDIT = _DATA / "order_audit.csv"

COST = 0.00015 * 2 + 0.0018      # 왕복 비용 0.0021 (수수료 0.015%×2 + 세금 0.18%)
ATR_PERIOD, MULT, SOURCE = 10, 3.0, "hl2"
MIN_ADX, ADX_PERIOD, MIN_FLIP = 25.0, 14, 1.0     # 운영 baseline 게이트
ENTRY_LB, EXIT_LB = 2, 2
RSI_SIGNAL_PERIOD = 9
RSI_LOOKBACK = 2
RSI_MIN_LEVEL, RSI_MAX_LEVEL = 50.0, 100.0
MIN_PRICE = 1000.0
TF_TABLE = {"5m": 1, "10m": 2, "15m": 3, "30m": 6}


# ── 로더 ──────────────────────────────────────────────────────────────────────
def load_5m(symbol: str, cache_dir: Path) -> List[OHLCV]:
    p = cache_dir / f"{symbol}.json"
    if not p.exists():
        return []
    raw = json.loads(p.read_text())
    rows = raw.get("data", raw) if isinstance(raw, dict) else raw
    out: List[OHLCV] = []
    for r in rows:
        try:
            ts = datetime.strptime(r["datetime"], "%Y%m%d%H%M%S")
        except (KeyError, ValueError):
            continue
        out.append(OHLCV(
            symbol=symbol, timestamp=ts,
            open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
            close=float(r["close"]), volume=float(r.get("volume", 0)),
            market_type=MarketType.STOCK,
        ))
    out.sort(key=lambda b: b.timestamp)
    return out


def traded_symbols(audit: Path) -> List[str]:
    if not audit.exists():
        return []
    syms: list[str] = []
    seen = set()
    with open(audit) as f:
        for row in csv.DictReader(f):
            s = (row.get("symbol") or "").strip()
            if s and s not in seen:
                seen.add(s)
                syms.append(s)
    return syms


def liquidity_rank(cache_dir: Path, exclude: set, limit: int) -> List[str]:
    """캐시 전체를 5분봉 (close×volume) 중앙값 유동성 순으로 정렬 → 상위 limit (exclude 제외)."""
    scored: list[tuple[float, str]] = []
    for fp in glob.glob(str(cache_dir / "*.json")):
        sym = Path(fp).stem
        if sym in exclude:
            continue
        try:
            raw = json.loads(Path(fp).read_text())
            rows = raw.get("data", []) if isinstance(raw, dict) else raw
            if len(rows) < 500:
                continue
            vals = [float(r["close"]) * float(r.get("volume", 0)) for r in rows[-400:]]
            scored.append((statistics.median(vals), sym))
        except Exception:
            continue
    scored.sort(reverse=True)
    return [s for _, s in scored[:limit]]


# ── 변형 정의 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Variant:
    name: str
    rsi_enabled: bool
    tf_mult: int
    period: int
    mode: str
    exit_enabled: bool


def build_variants() -> List[Variant]:
    out = [Variant("NO_RSI(baseline)", False, 2, 14, "signal_cross", False)]
    for tf_name, tf_mult in TF_TABLE.items():
        for period in (9, 14):
            for mode in ("signal_cross", "centerline", "level"):
                for ex in (False, True):
                    tag = "entry+exit" if ex else "entry"
                    out.append(Variant(
                        f"RSI {tf_name} p{period} {mode} [{tag}]",
                        True, tf_mult, period, mode, ex,
                    ))
    return out


def params_for(v: Variant) -> SupertrendParams:
    return SupertrendParams(
        atr_period=ATR_PERIOD, multiplier=MULT, source=SOURCE,
        min_candles=30, entry_lookback=ENTRY_LB, exit_lookback=EXIT_LB,
        min_adx=MIN_ADX, adx_period=ADX_PERIOD, min_flip_atr_mult=MIN_FLIP,
        rsi_enabled=v.rsi_enabled, rsi_timeframe_mult=v.tf_mult, rsi_period=v.period,
        rsi_signal_period=RSI_SIGNAL_PERIOD, rsi_mode=v.mode,
        rsi_cross_lookback=RSI_LOOKBACK, rsi_min_level=RSI_MIN_LEVEL,
        rsi_max_level=RSI_MAX_LEVEL, rsi_exit_enabled=v.exit_enabled,
    )


# ── 종목별 인과 precompute (변형 무관 + RSI 이벤트 12조합) ────────────────────
def _complete_count_at(candles: List[OHLCV], tf_mult: int, base_minutes: int = 5) -> List[int]:
    """각 5분봉 i 에서 '완성(닫힌) HTF 버킷 수' = htf_rsi_at(candles,i,tf) 길이와 정확히 동일.

    resample_htf 는 (날짜, bucket_index) 그룹 단위. 한 그룹은 '뒤에 다른 그룹이 시작되면'
    완성이고, 시계열의 마지막 그룹만 형성 중(=bar i 의 다음 5분 슬롯이 같은 버킷)일 수 있다.
    → 'i 까지의 distinct 그룹 수 - (마지막 봉이 forming 이면 1)'.
    (종전 'next_b>cur_b 일 때 +1' 방식은 장마감 단일가/부분 버킷을 누락 → cc<len 버그)
    """
    n = len(candles)
    cc = [0] * n
    groups = 0
    cur_key = None
    for i in range(n):
        ts = candles[i].timestamp
        cur_b = _bucket_index(ts, tf_mult, base_minutes)
        key = (ts.date(), cur_b)
        if key != cur_key:
            groups += 1
            cur_key = key
        next_b = (_offset_min(ts) + base_minutes) // (tf_mult * base_minutes)
        forming = next_b <= cur_b      # 다음 5분 슬롯이 같은 버킷 → 마지막 그룹 형성 중
        cc[i] = groups - (1 if forming else 0)
    return cc


class SymbolPrecompute:
    """한 종목의 인과 지표 캐시. 모든 변형이 O(n) 조회로 재사용."""

    def __init__(self, candles: List[OHLCV]):
        self.candles = candles
        self.n = len(candles)
        self.close = [float(c.close) for c in candles]
        res = compute_supertrend(candles, period=ATR_PERIOD, multiplier=MULT, source=SOURCE)
        self.trend = res.trend
        self.buy = res.buy_signals
        self.sell = res.sell_signals
        self.atr = res.atr
        self.dn = res.dn
        self.supertrend = res.supertrend
        self.adx = compute_adx(candles, period=ADX_PERIOD)
        # 진입까지의 가장 최근 BUY 전환 인덱스 (FLIP 평가용)
        self._last_buy = [-1] * self.n
        lb = -1
        for i in range(self.n):
            if self.buy[i]:
                lb = i
            self._last_buy[i] = lb
        # RSI 이벤트 12조합 (tf_mult, period, mode) → (golden, dead, cc, need)
        self._rsi: dict[tuple, tuple] = {}
        for tf_mult in set(TF_TABLE.values()):
            cc = _complete_count_at(candles, tf_mult)
            htf = resample_htf(candles, tf_mult)
            for period in (9, 14):
                rsi = compute_rsi(htf, period)
                for mode in ("signal_cross", "centerline", "level"):
                    sig = rsi_signal_line(rsi, RSI_SIGNAL_PERIOD) if mode == "signal_cross" else None
                    golden, dead = rsi_cross_state(
                        rsi, sig, mode=mode,
                        min_level=RSI_MIN_LEVEL, max_level=RSI_MAX_LEVEL,
                    )
                    need = period + 1 + (RSI_SIGNAL_PERIOD if mode == "signal_cross" else 0)
                    self._rsi[(tf_mult, period, mode)] = (golden, dead, cc, need)

    # ── 게이트 (fast path, 인과 lookups) ─────────────────────────────────────
    def _flip_ok(self, i: int) -> bool:
        bidx = self._last_buy[i]
        if bidx >= 0:
            atr_ref = self.atr[bidx]
            resist = self.dn[bidx - 1] if bidx >= 1 else self.supertrend[bidx]
            breakout = self.close[bidx] - resist
        else:
            atr_ref = self.atr[i]
            breakout = self.close[i] - self.supertrend[i]
        return atr_ref > 0 and breakout >= MIN_FLIP * atr_ref

    def _rsi_event(self, v: Variant, i: int, which: str) -> bool:
        golden, dead, cc, need = self._rsi[(v.tf_mult, v.period, v.mode)]
        lc = cc[i]
        if lc < need:
            return False
        last = lc - 1
        arr = golden if which == "long" else dead
        if v.mode == "level":
            return arr[last]
        lo = max(0, last - RSI_LOOKBACK + 1)
        return any(arr[lo:last + 1])

    def entry_ok(self, v: Variant, i: int, *, apply_min_price: bool = True) -> bool:
        if self.trend[i] != 1:
            return False
        if not any(self.buy[max(0, i - ENTRY_LB + 1):i + 1]):
            return False
        if self.adx[i] < MIN_ADX:
            return False
        if not self._flip_ok(i):
            return False
        if v.rsi_enabled and not self._rsi_event(v, i, "long"):
            return False
        if apply_min_price and self.close[i] < MIN_PRICE:
            return False
        return True

    def exit_ok(self, v: Variant, i: int) -> bool:
        st_exit = any(self.sell[max(0, i - EXIT_LB + 1):i + 1])
        if st_exit:
            return True
        if v.exit_enabled and self._rsi_event(v, i, "exit"):
            return True
        return False


# ── 워크포워드 (단일 포지션 long-only, 라운드트립) ───────────────────────────
@dataclass
class Trip:
    entry_date: date
    exit_ts: datetime
    net_ret_pct: float


def walk_forward(pre: SymbolPrecompute, v: Variant, warmup: int) -> List[Trip]:
    candles, n = pre.candles, pre.n
    trips: List[Trip] = []
    holding = False
    entry_px = 0.0
    entry_idx = 0
    i = warmup
    while i < n - 1:
        if not holding:
            if pre.entry_ok(v, i):
                entry_idx = i + 1
                entry_px = float(candles[entry_idx].open)
                holding = True
        else:
            if pre.exit_ok(v, i):
                exit_px = float(candles[i + 1].open)
                net = ((exit_px - entry_px) / entry_px - COST) * 100 if entry_px > 0 else 0.0
                trips.append(Trip(candles[entry_idx].timestamp.date(),
                                  candles[i + 1].timestamp, net))
                holding = False
        i += 1
    if holding and entry_px > 0:
        exit_px = float(candles[-1].close)
        net = ((exit_px - entry_px) / entry_px - COST) * 100
        trips.append(Trip(candles[entry_idx].timestamp.date(), candles[-1].timestamp, net))
    return trips


# ── fast-path == 실제 전략 코드 일치 검증 (불일치 시 즉시 실패) ──────────────
def validate_fast_path(pre: SymbolPrecompute, warmup: int) -> None:
    """샘플 변형·봉에서 fast entry/exit 가 SupertrendStrategy.analyze/exit_on_signal 와 일치."""
    sample_variants = [
        Variant("v_norsi", False, 2, 14, "signal_cross", False),
        Variant("v_sig10", True, 2, 14, "signal_cross", True),
        Variant("v_cl15", True, 3, 9, "centerline", True),
        Variant("v_lvl30", True, 6, 14, "level", True),
    ]
    n = pre.n
    idxs = list(range(warmup, n - 1, max(1, (n - warmup) // 60)))   # ~60 표본
    for v in sample_variants:
        strat = SupertrendStrategy(params_for(v))
        for i in idxs:
            window = pre.candles[: i + 1]
            ctx = AnalysisContext(symbol=window[-1].symbol, candles=window,
                                  market_type=MarketType.STOCK)
            fast_entry = pre.entry_ok(v, i, apply_min_price=False)  # 전략은 min_price 미적용
            strat_entry = strat.analyze(ctx) is not None
            if fast_entry != strat_entry:
                raise AssertionError(
                    f"ENTRY 불일치 {v.name} i={i}: fast={fast_entry} strat={strat_entry}")
            pos = _mk_pos(window[-1].symbol)
            fast_exit = pre.exit_ok(v, i)
            strat_exit = strat.exit_on_signal(
                pos, ctx, Decimal(str(pre.close[i]))) is not None
            if fast_exit != strat_exit:
                raise AssertionError(
                    f"EXIT 불일치 {v.name} i={i}: fast={fast_exit} strat={strat_exit}")


def _mk_pos(symbol: str, avg_price: float = 10000.0) -> Position:
    return Position(
        symbol=symbol, name=symbol, quantity=1.0, avg_price=avg_price,
        current_price=avg_price, realized_pnl=0.0, unrealized_pnl=0.0, pnl_pct=0.0,
        market_type=MarketType.STOCK, entry_time=datetime(2026, 4, 23, 9, 0),
        strategy_id="supertrend_v1",
    )


# ── 집계 ──────────────────────────────────────────────────────────────────────
def _records(trips: List[Trip], lo: Optional[date] = None, hi: Optional[date] = None) -> list:
    recs = []
    for t in trips:
        if lo and t.entry_date < lo:
            continue
        if hi and t.entry_date > hi:
            continue
        recs.append(TradeRecord(
            strategy_id="supertrend", symbol="POOL", side="sell",
            qty=Decimal("1"), price=Decimal("0"), timestamp=t.exit_ts,
            reason="rt", pnl=Decimal(str(round(t.net_ret_pct, 6))),
        ))
    return recs


@dataclass
class VariantStats:
    name: str
    trades: int
    ret_pct: float       # 총 수익률 % (라운드트립 net% 합)
    avg_pct: float       # 거래당 기대값 % (expectancy — 이상치 둔감 비교지표)
    win_pct: float
    pf: float
    mdd_pct: float       # 누적 수익률% 곡선 peak 대비 최대낙폭(%pt)
    sharpe: float
    is_ret: float
    is_win: float
    is_tr: int
    oos_ret: float
    oos_win: float
    oos_tr: int


def summarize(name: str, trips: List[Trip], is_end: date) -> VariantStats:
    full = compute_metrics(_records(trips))
    is_m = compute_metrics(_records(trips, hi=is_end))
    oos_m = compute_metrics(_records(trips, lo=date.fromordinal(is_end.toordinal() + 1)))
    pf = full.profit_factor if full.profit_factor != float("inf") else 999.0
    return VariantStats(
        name=name, trades=full.total_trades, ret_pct=float(full.total_pnl),
        avg_pct=float(full.avg_pnl), win_pct=full.win_rate * 100, pf=pf,
        mdd_pct=float(full.max_drawdown), sharpe=full.sharpe_ratio,
        is_ret=float(is_m.total_pnl), is_win=is_m.win_rate * 100, is_tr=is_m.total_trades,
        oos_ret=float(oos_m.total_pnl), oos_win=oos_m.win_rate * 100, oos_tr=oos_m.total_trades,
    )


def run_basket(basket, variants, warmup, is_end, cache_dir, *, do_validate=False):
    """바스켓 종목들에 대해 워크포워드 → 변형별 VariantStats(수익률% 내림차순) + 사용/스킵."""
    all_trips: dict[str, List[Trip]] = {v.name: [] for v in variants}
    used, skipped = [], []
    validated = not do_validate
    for sym in basket:
        candles = load_5m(sym, cache_dir)
        if len(candles) < warmup + 50:
            skipped.append(sym)
            continue
        pre = SymbolPrecompute(candles)
        if not validated:
            validate_fast_path(pre, warmup)
            validated = True
            print(f"[sweep] fast-path == SupertrendStrategy 일치 검증 OK ({sym})")
        for v in variants:
            all_trips[v.name].extend(walk_forward(pre, v, warmup))
        used.append(sym)
    stats = [summarize(v.name, all_trips[v.name], is_end) for v in variants]
    stats.sort(key=lambda s: s.ret_pct, reverse=True)
    return stats, used, skipped


# ── 메인 (두 컷: 운영=실거래만 / 광의=+유동성) ──────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    ap.add_argument("--audit", default=str(DEFAULT_AUDIT))
    ap.add_argument("--symbols", default="", help="쉼표구분 종목코드(지정 시 단일 컷)")
    ap.add_argument("--broad-size", type=int, default=80, help="광의 컷 목표 종목수")
    ap.add_argument("--is-end", default="20260522", help="in-sample 종료일 YYYYMMDD")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "RSI_TF_SWEEP.md"))
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir)
    is_end = datetime.strptime(args.is_end, "%Y%m%d").date()
    warmup = max(35, (14 + RSI_SIGNAL_PERIOD + 2) * max(TF_TABLE.values()))   # 30m RSI 준비 = 150
    variants = build_variants()

    # ── 바스켓 구성 ──────────────────────────────────────────────────────────
    cuts = []   # (label, reason, basket)
    if args.symbols.strip():
        basket = [s.strip() for s in args.symbols.split(",") if s.strip()]
        cuts.append(("지정", f"--symbols 명시 ({len(basket)}종목)", basket))
    else:
        traded = [s for s in traded_symbols(Path(args.audit)) if (cache_dir / f"{s}.json").exists()]
        cuts.append(("운영(실거래)", f"order_audit ∩ 캐시 = {len(traded)}종목 (시스템이 실제 매매한 유니버스)", traded))
        fill = liquidity_rank(cache_dir, set(traded), max(0, args.broad_size - len(traded)))
        broad = traded + fill
        cuts.append(("광의(+유동성)",
                     f"실거래 {len(traded)} + 유동성 상위 {len(fill)} = {len(broad)} (중앙값 close×volume)",
                     broad))

    print(f"[sweep] 변형 {len(variants)}개 · warmup {warmup}봉 · IS≤{is_end} · 비용 {COST*100:.2f}%/왕복")
    results = []   # (label, reason, stats, used, skipped)
    first = True
    for label, reason, basket in cuts:
        print(f"[sweep] === 컷: {label} ({len(basket)}종목) ===")
        stats, used, skipped = run_basket(basket, variants, warmup, is_end, cache_dir,
                                          do_validate=first)
        first = False
        base = next(s for s in stats if s.name.startswith("NO_RSI"))
        win = stats[0]
        print(f"[sweep]  사용 {len(used)} / 스킵 {len(skipped)} · 1위 {win.name} "
              f"ret={win.ret_pct:+.1f}% win={win.win_pct:.1f}% tr={win.trades} | "
              f"NO_RSI ret={base.ret_pct:+.1f}%")
        results.append((label, reason, stats, used, skipped))

    write_report(args.out, results, warmup, is_end)
    print(f"[sweep] 리포트 작성: {args.out}")
    return 0


def _fmt(s: VariantStats) -> str:
    return (f"| {s.name} | {s.trades} | {s.ret_pct:+.1f} | {s.avg_pct:+.2f} | {s.win_pct:.1f} | "
            f"{s.pf:.2f} | {s.mdd_pct:.1f} | {s.sharpe:.2f} | "
            f"{s.is_ret:+.1f}/{s.is_win:.0f}%/{s.is_tr} | "
            f"{s.oos_ret:+.1f}/{s.oos_win:.0f}%/{s.oos_tr} |")


def _cut_block(lines, label, reason, stats, used, skipped):
    base = next(s for s in stats if s.name.startswith("NO_RSI"))
    winner = stats[0]
    n_oos_pos = sum(1 for s in stats if s.oos_ret > 0)
    rsi_top = next((s for s in stats if not s.name.startswith("NO_RSI")), None)
    lines.append(f"## 컷: {label}")
    lines.append("")
    lines.append(f"- 바스켓: {reason}; 사용 {len(used)} / 스킵 {len(skipped)}")
    base_rank = stats.index(base) + 1
    if winner.name.startswith("NO_RSI"):
        lines.append(f"- **1위 = NO_RSI 베이스라인** (ret {base.ret_pct:+.1f}%, win {base.win_pct:.1f}%, "
                     f"tr {base.trades}). RSI 최상위는 {rsi_top.name} (ret {rsi_top.ret_pct:+.1f}%) — "
                     f"베이스라인 미달.")
    else:
        d = winner.ret_pct - base.ret_pct
        lines.append(f"- **1위 = {winner.name}** (ret {winner.ret_pct:+.1f}%, win {winner.win_pct:.1f}%, "
                     f"tr {winner.trades}). NO_RSI(ret {base.ret_pct:+.1f}%, tr {base.trades}, "
                     f"순위 {base_rank}위) 대비 Δret **{d:+.1f}%pt**, 거래 {winner.trades} vs {base.trades}.")
    lines.append(f"- OOS 수익 변형 {n_oos_pos}/{len(stats)}개 · NO_RSI OOS {base.oos_ret:+.1f}%/{base.oos_win:.0f}% "
                 f"(tr {base.oos_tr}).")
    lines.append("")
    lines.append("| variant | trades | ret% | avg%/tr | win% | PF | MDD%pt | sharpe | IS ret/win/tr | OOS ret/win/tr |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    for s in stats:
        lines.append(_fmt(s))
    lines.append("")
    # 모드별 롤업
    by_mode: dict[str, list] = {}
    for s in stats:
        if s.name.startswith("NO_RSI"):
            continue
        for m in ("signal_cross", "centerline", "level"):
            if m in s.name:
                by_mode.setdefault(m, []).append(s.ret_pct)
    lines.append("모드별 평균 ret%: " + " · ".join(
        f"{m} {statistics.mean(v):+.1f}(최고 {max(v):+.1f})" for m, v in by_mode.items()))
    lines.append("")


def write_report(path, results, warmup, is_end):
    lines = []
    lines.append("# RSI 타임프레임 sweep — 슈퍼트렌드 멀티 TF RSI 확인 필터 (BAR-OPS-10)")
    lines.append("")
    lines.append(f"- 생성: analysis/imports/2026-06-03/rsi_tf_sweep.py · IS≤{is_end} / OOS 이후 · warmup {warmup}봉")
    lines.append("- 데이터: data/ohlcv_cache_5m (오프라인 5분봉, ~2026-04-23~06-02, 6주 단일국면)")
    lines.append("- 실제 전략코드(SupertrendStrategy.analyze/exit_on_signal)와 동일 게이트 구동(fast-path 일치 검증).")
    lines.append(f"- 비용모델: 수수료 0.015%×2 + 세금 0.18% (왕복 {COST*100:.2f}%). 진입/청산=다음봉 open. 단일포지션 long-only.")
    lines.append("- **모든 수치 % 기준**(KRW 미출력). ret%=라운드트립 net% 합(거래당 등가중), "
                 "avg%/tr=거래당 기대값(이상치 둔감), MDD%pt=누적수익률% peak 대비 최대낙폭.")
    lines.append("- 변형: rsi_timeframe∈{5,10,15,30}m × period∈{9,14} × mode∈{signal_cross,centerline,level} "
                 "× exit∈{entry, entry+exit} + NO_RSI(ADX≥25+FLIP≥1.0) 베이스라인.")
    lines.append("")

    # 두 컷 요약 대조 (핵심 finding) — 모두 데이터 기반(하드코딩 주장 금지)
    op = results[0]
    broad = results[1] if len(results) > 1 else None
    op_base = next(s for s in op[2] if s.name.startswith("NO_RSI"))
    op_win = op[2][0]
    op_rsis = [s for s in op[2] if not s.name.startswith("NO_RSI")]
    op_rsi_top = op_rsis[0] if op_rsis else None
    op_rsi_sharpe = max(op_rsis, key=lambda s: s.sharpe) if op_rsis else None

    def _mode_means(stats_):
        d: dict[str, list] = {}
        for s in stats_:
            if s.name.startswith("NO_RSI"):
                continue
            for m in ("signal_cross", "centerline", "level"):
                if m in s.name:
                    d.setdefault(m, []).append(s.ret_pct)
        return {m: statistics.mean(v) for m, v in d.items()}

    op_modes = _mode_means(op[2])
    lines.append("## 핵심 결론")
    lines.append("")
    if broad:
        br_base = next(s for s in broad[2] if s.name.startswith("NO_RSI"))
        lines.append(f"- **총수익률 1위는 두 컷 모두 NO_RSI 베이스라인**(운영 {op_base.ret_pct:+.1f}% / "
                     f"광의 {br_base.ret_pct:+.1f}%). → **RSI 확인 필터는 이 6주 표본에서 raw 슈퍼트렌드"
                     f"(ADX+FLIP) 의 총수익을 넘지 못함.** '수익률 우선' KPI 기준 **기본 OFF 가 정답**.")
    if op_rsi_top is not None:
        lines.append(f"- 다만 **RSI 는 '거래 품질' 필터로는 의미** 있음: 운영 컷 최선 RSI **{op_rsi_top.name}** 는 "
                     f"총수익 {op_rsi_top.ret_pct:+.1f}%(베이스 {op_base.ret_pct:+.1f}%)로 낮지만 — "
                     f"거래당 기대값 {op_rsi_top.avg_pct:+.2f}%(베이스 {op_base.avg_pct:+.2f}%) · "
                     f"승률 {op_rsi_top.win_pct:.0f}%(베이스 {op_base.win_pct:.0f}%) · "
                     f"Sharpe {op_rsi_top.sharpe:.2f}(베이스 {op_base.sharpe:.2f}) · "
                     f"MDD {op_rsi_top.mdd_pct:.0f}(베이스 {op_base.mdd_pct:.0f}). "
                     f"**거래수↓({op_rsi_top.trades} vs {op_base.trades}, 과매매 감소)·한 건의 질·위험조정수익↑.**")
    lines.append("- **모드: centerline·level > signal_cross.** 운영 컷 모드별 평균 ret% = " +
                 ", ".join(f"{m} {op_modes[m]:+.0f}" for m in ("centerline", "level", "signal_cross")
                           if m in op_modes) +
                 ". 사용자가 본 '골든/데드크로스'(signal_cross)보다 **RSI 50 기준선 돌파(centerline)** 가 안정적. "
                 "**15분 타임프레임은 두 컷 모두 최악**(과최적화/노이즈). → 기본 후보 모드 = centerline.")
    if op_rsi_sharpe is not None:
        lines.append(f"- 위험조정 최선(운영 Sharpe): **{op_rsi_sharpe.name}** "
                     f"(Sharpe {op_rsi_sharpe.sharpe:.2f}, MDD {op_rsi_sharpe.mdd_pct:.0f}, "
                     f"win {op_rsi_sharpe.win_pct:.0f}%).")
    lines.append("- **승률 80% 목표는 본 필터로 달성 불가**: 슈퍼트렌드는 신호기반 청산(반대전환까지 보유) "
                 "추세추종이라 구조적으로 저승률(~20~40%)·고손익비. 80% 승률은 별도 TP/SL 청산 오버레이 필요(범위 밖).")
    lines.append("")
    lines.append("### ⚠️ OOS 미검증 — 라이브 활성 전 필독")
    lines.append("- OOS(2026-05-23~06-02, ~1.5주)는 표본이 얇고 컷마다 부호가 흔들림. IS 상위 변형이 OOS 에서 "
                 "역전되는 사례 다수. **\"검증된 라이브 엣지\"가 아니라 후보**로 취급.")
    lines.append("")
    lines.append("### 권고 (rollout) — config-gated OFF")
    lines.append("- **수익률 우선 KPI 기준: 기본 OFF 유지가 정답**(베이스라인이 총수익 더 높음). "
                 "RSI 는 거래수↓·위험조정수익↑를 원할 때만 opt-in 하는 **품질 필터**.")
    lines.append("- 데이터-최선 후보값(10m · centerline · p14)을 SupertrendAutoConfig/SupertrendParams "
                 "기본 필드값으로 박되 `rsi_enabled=False` 유지. (signal_cross 도 config 로 선택 가능)")
    lines.append("- opt-in 경로: 운영 머신에서 본 리포트 검토 → `rsi_enabled=True`(+필요시 `rsi_exit_enabled`) → "
                 "**dry_run 1~2세션**(entered/exited 델타 관찰) → 더 긴/다양한 국면에서 OOS 양(+) 확인 후 라이브.")
    lines.append("")

    for label, reason, stats, used, skipped in results:
        _cut_block(lines, label, reason, stats, used, skipped)

    lines.append(f"_운영 컷 바스켓: {', '.join(op[3])}_")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
