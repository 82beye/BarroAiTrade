#!/usr/bin/env python3
"""주식단테 게이트(dante_filters) shadow 백테스트 — 일봉 forward-return 측정.

목적: docs/02-design/features/2026-06-21-dante-uplift.design.md §6 신규 신호의
'엣지 유무'를 실데이터(ohlcv_cache 일봉)로 측정. 라이브 무영향(관측 전용).

방법:
  - ohlcv_cache 일봉(오래된→최신)에서 종목별 EMA112/224/448·SMA5/15·평균거래량 series 선계산.
  - 각 신호 발생봉 i 에서 forward H일 수익 close[i+H]/close[i]-1 기록.
  - 진입신호(odori/sr_flip/saucer)는 왕복비용 0.90% 차감한 net 도 산출.
  - 회피신호(distribution)는 forward 수익이 baseline보다 낮은지(=약세 예측) 확인.
  - 레짐(JD-R1)은 above_ma224 True/False 별 baseline forward 수익 비교.
  - parity: inline 검출을 dante_filters 모듈과 표본 대조(재구현 일치 보장).

사용: python scripts/backtest_dante_filters_shadow.py [--cache DIR] [--max-symbols N]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone

from backend.core.strategy import dante_filters as df
from backend.models.market import MarketType, OHLCV

COST = 0.009  # 왕복비용(편도 0.35%×2 + 매도세 0.20%)
DEFAULT_CACHE = "/Users/beye/workspace/BarroAiTrade/data/ohlcv_cache"
_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ema_series(closes, period):
    """adjust=False EMA series (dante_filters._ema 와 동일 정의). 유효구간 [period-1:]."""
    k = 2.0 / (period + 1.0)
    out = [None] * len(closes)
    e = closes[0]
    for i, c in enumerate(closes):
        e = c * k + e * (1.0 - k) if i > 0 else c
        if i >= period - 1:
            out[i] = e
    return out


def _sma_series(closes, period):
    out = [None] * len(closes)
    run = 0.0
    for i, c in enumerate(closes):
        run += c
        if i >= period:
            run -= closes[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def _to_ohlcv(o, h, l, c, v):
    return OHLCV(symbol="X", timestamp=_TS, open=o, high=h, low=l, close=c,
                 volume=v, market_type=MarketType.STOCK)


class Acc:
    """forward 수익 누적기."""
    def __init__(self):
        self.gross = []
    def add(self, r):
        self.gross.append(r)
    def stats(self, cost=0.0):
        n = len(self.gross)
        if n == 0:
            return (0, 0.0, 0.0, 0.0)
        net = [g - cost for g in self.gross]
        mean = sum(net) / n
        win = sum(1 for x in net if x > 0) / n
        return (n, mean, win, sum(self.gross) / n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--max-symbols", type=int, default=0, help="0=전체")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.cache, "*.json")))
    if args.max_symbols:
        files = files[: args.max_symbols]
    print(f"cache={args.cache}  symbols={len(files)}  cost(왕복)={COST:.3%}")

    H1, H2, H3 = 5, 10, 20
    acc = {
        "odori_H5": Acc(), "odori_H10": Acc(),
        "srflip_H10": Acc(), "srflip_H20": Acc(),
        "saucer_H20": Acc(), "saucer_H60": Acc(),
        "distrib_H5": Acc(), "distrib_H10": Acc(),
        "base_H5": Acc(), "base_H10": Acc(), "base_H20": Acc(), "base_H60": Acc(),
        "regime_above_H20": Acc(), "regime_below_H20": Acc(),
    }
    srflip_target_hit = [0, 0]  # [hit, total] within H20
    parity_checks = 0
    parity_fail = 0
    parity_stride = 7919  # 결정적 표본(소수)

    bars_seen = 0
    for fpath in files:
        try:
            data = json.load(open(fpath, encoding="utf-8")).get("data", [])
        except Exception:
            continue
        if len(data) < 460:  # 448 EMA + 여유
            continue
        o = [float(d["open"]) for d in data]
        h = [float(d["high"]) for d in data]
        l = [float(d["low"]) for d in data]
        c = [float(d["close"]) for d in data]
        v = [float(d["volume"]) for d in data]
        n = len(c)
        ema224 = _ema_series(c, 224)
        sma5 = _sma_series(c, 5)
        sma15 = _sma_series(c, 15)
        sma60 = _sma_series(c, 60)
        # 평균거래량(직전 20)
        avgvol = [None] * n
        run = 0.0
        for i in range(n):
            run += v[i]
            if i >= 20:
                run -= v[i - 20]
            if i >= 20:
                avgvol[i] = run / 20  # 직전 20봉(i 포함 21개 중 i 제외 근사 — inline용)

        for i in range(224, n):
            bars_seen += 1
            # baseline forward
            if i + H1 < n:
                acc["base_H5"].add(c[i + H1] / c[i] - 1)
            if i + H2 < n:
                acc["base_H10"].add(c[i + H2] / c[i] - 1)
            if i + H3 < n:
                acc["base_H20"].add(c[i + H3] / c[i] - 1)
            if i + 60 < n:
                acc["base_H60"].add(c[i + 60] / c[i] - 1)

            # JD-R1 레짐: above_ma224
            if ema224[i] is not None and i + H3 < n:
                fr = c[i + H3] / c[i] - 1
                (acc["regime_above_H20"] if c[i] >= ema224[i] else acc["regime_below_H20"]).add(fr)

            # JD-R20 오돌리: 5/15 골든크로스 당봉
            if sma5[i] is not None and sma15[i] is not None and sma5[i-1] is not None \
               and sma15[i-1] is not None:
                if sma5[i-1] <= sma15[i-1] and sma5[i] > sma15[i]:
                    if i + H1 < n:
                        acc["odori_H5"].add(c[i + H1] / c[i] - 1)
                    if i + H2 < n:
                        acc["odori_H10"].add(c[i + H2] / c[i] - 1)

            # JD-R13 distribution: 음봉+몸통3%+거래량 전일×3, 정배열 확장 proxy(종가>SMA60)
            if i >= 1 and o[i] > 0 and c[i] < o[i] and v[i-1] > 0:
                body = (o[i] - c[i]) / o[i]
                if body >= 0.03 and v[i] >= v[i-1] * 3.0 and sma60[i] is not None and c[i] > sma60[i]:
                    if i + H1 < n:
                        acc["distrib_H5"].add(c[i + H1] / c[i] - 1)
                    if i + H2 < n:
                        acc["distrib_H10"].add(c[i + H2] / c[i] - 1)

            # JD-R7 공구리(sr_flip): 직전 20봉 전고 상향 돌파
            if i >= 22:
                window_hi = max(h[i-21:i])  # 최신봉 제외 직전 21봉 중 high (모듈 윈도우와 정합)
                if c[i-1] <= window_hi < c[i]:
                    # 전고 형성 후 저점
                    hi_rel = max(range(i-21, i), key=lambda j: h[j])
                    if hi_rel + 1 <= i - 1:
                        low_after = min(l[hi_rel+1:i])
                        if window_hi - low_after > 0:
                            target = window_hi + (window_hi - low_after)
                            if i + H2 < n:
                                acc["srflip_H10"].add(c[i + H2] / c[i] - 1)
                            if i + H3 < n:
                                acc["srflip_H20"].add(c[i + H3] / c[i] - 1)
                                srflip_target_hit[1] += 1
                                if max(h[i+1:i+1+H3]) >= target:
                                    srflip_target_hit[0] += 1

            # JD-R5 밥그릇 3번(saucer): 224 아래 80봉 횡보 후 강한 돌파 (cross-up 에서만 평가)
            if i >= 224 + 80 and ema224[i] is not None and ema224[i] > 0:
                if c[i-1] <= ema224[i] < c[i]:
                    base = slice(i-80, i)
                    below = sum(1 for j in range(i-80, i) if c[j] < ema224[i])
                    if below >= 0.9 * 80:
                        base_low = min(l[i-80:i])
                        below_dist = ema224[i] - base_low
                        if below_dist > 0 and c[i] >= base_low + below_dist * 2.0:
                            if i + H3 < n:
                                acc["saucer_H20"].add(c[i + H3] / c[i] - 1)
                            if i + 60 < n:
                                acc["saucer_H60"].add(c[i + 60] / c[i] - 1)

            # parity: 표본에서 inline vs 모듈 대조 (distribution·odori)
            if bars_seen % parity_stride == 0 and i >= 25:
                window = [_to_ohlcv(o[j], h[j], l[j], c[j], v[j]) for j in range(i-24, i+1)]
                m_dist = df.distribution_alert(window, vol_mult=3.0, body_min=0.03)
                inline_dist = (o[i] > 0 and c[i] < o[i] and v[i-1] > 0
                               and (o[i]-c[i])/o[i] >= 0.03 and v[i] >= v[i-1]*3.0)
                parity_checks += 1
                if m_dist != inline_dist:
                    parity_fail += 1
                m_odori = df.odori_cross(window, short=5, long=15)
                inline_odori = (sma5[i] is not None and sma15[i] is not None
                                and sma5[i-1] <= sma15[i-1] and sma5[i] > sma15[i])
                parity_checks += 1
                if m_odori != inline_odori:
                    parity_fail += 1

    print(f"\n총 평가봉(224+): {bars_seen:,}")
    print(f"parity(inline vs 모듈): {parity_checks}건 중 불일치 {parity_fail}건")
    print("\n=== 신호별 forward 수익 (net=왕복비용 차감) ===")
    print(f"{'신호':<18}{'N':>9}{'net평균':>10}{'승률':>8}{'gross평균':>11}")
    for key, cost in [
        ("base_H5", 0.0), ("odori_H5", COST), ("distrib_H5", 0.0),
        ("base_H10", 0.0), ("odori_H10", COST), ("distrib_H10", 0.0),
        ("srflip_H10", COST),
        ("base_H20", 0.0), ("srflip_H20", COST), ("saucer_H20", COST),
        ("base_H60", 0.0), ("saucer_H60", COST),
    ]:
        nn, mean, win, gross = acc[key].stats(cost)
        print(f"{key:<18}{nn:>9,}{mean:>10.3%}{win:>8.1%}{gross:>11.3%}")

    print("\n=== JD-R1 레짐(224선 위/아래) H20 baseline ===")
    for key in ("regime_above_H20", "regime_below_H20"):
        nn, mean, win, gross = acc[key].stats(0.0)
        print(f"{key:<18}{nn:>9,}{gross:>10.3%}{win:>8.1%}")

    if srflip_target_hit[1]:
        print(f"\nsr_flip 대칭목표 도달율(H20 내): "
              f"{srflip_target_hit[0]}/{srflip_target_hit[1]} "
              f"= {srflip_target_hit[0]/srflip_target_hit[1]:.1%}")


if __name__ == "__main__":
    main()
