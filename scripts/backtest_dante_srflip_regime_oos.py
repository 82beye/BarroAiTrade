#!/usr/bin/env python3
"""sr_flip(공구리) × 레짐 필터 결합 OOS 재검증.

배경: 2026-06-22 OOS에서 sr_flip 은 종목·파라미터 견고·양의 기대값이나 net 대부분이
**롱-베타**(baseline 대비 알파 불안정). 리포트 §5 권고 = above_ma224·정배열 레짐 필터
결합 후 재측정. 본 스크립트가 그 후속.

측정: sr_flip ∩ 레짐필터 F 의 net(H20, 비용 0.90% 차감)을 두 기준과 비교
  ① 무조건 baseline(전체 봉 forward)
  ② **regime-matched baseline**(같은 레짐 F 를 만족하는 전체 봉 forward) ← 알파 분리 핵심.
필터 F: none(base) / above_ma224(JD-R1) / 정배열(112>224>448) / above_ma60(완화).
축: IS/OOS 기간 분할 + 분기별 + 종목 분할 + 파라미터(lookback) 민감도.

판정(레짐 결합 PASS): trades≥30 ∧ IS·OOS net>0 ∧ IS·OOS **Δ_regime>0**(레짐 내 알파)
  ∧ 종목 양쪽 net>0 ∧ 약세분기(2026Q2) baseline 대비 개선.

한계(OOS 리포트 계승): 224 warmup+불장 잔여 → 약세장 OOS 불가. 정배열은 448 warmup으로
  표본·구간 더 축소(후기 편중) — 결과 해석 시 N 병기.
"""
from __future__ import annotations

import argparse
import glob
import json
import os

COST = 0.009
DEFAULT_CACHE = "/Users/beye/workspace/BarroAiTrade/data/ohlcv_cache"
SPLIT_DATE = "20250901"
H = 20
LOOKBACKS = [15, 20, 25]
FILTERS = ["base", "a224", "jeong", "a60"]


def _ema_series(closes, period):
    k = 2.0 / (period + 1.0)
    out = [None] * len(closes)
    e = closes[0]
    for i, c in enumerate(closes):
        e = c * k + e * (1.0 - k) if i > 0 else c
        if i >= period - 1:
            out[i] = e
    return out


class B:
    __slots__ = ("n", "s", "w")
    def __init__(self):
        self.n = 0; self.s = 0.0; self.w = 0
    def add(self, x):
        self.n += 1; self.s += x; self.w += 1 if x > 0 else 0
    def mean(self):
        return self.s / self.n if self.n else 0.0
    def win(self):
        return self.w / self.n if self.n else 0.0


def quarter(d):
    return f"{d[:4]}Q{(int(d[4:6]) - 1) // 3 + 1}"


def regime_flags(i, close, e60, e112, e224, e448):
    """해당 봉의 레짐 필터 충족 여부 dict. None(데이터부족)이면 False."""
    f = {"base": True}
    f["a224"] = e224[i] is not None and close[i] > e224[i]
    f["a60"] = e60[i] is not None and close[i] > e60[i]
    f["jeong"] = (
        e112[i] is not None and e224[i] is not None and e448[i] is not None
        and e112[i] > e224[i] > e448[i]
    )
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.cache, "*.json")))
    print(f"cache={args.cache}  files={len(files)}  split={SPLIT_DATE}  H={H}  cost={COST:.2%}")

    # sr_flip 변형: srf[filter][split], 종목, 분기, 파라미터(lookback, filter=base)
    srf = {f: {"IS": B(), "OOS": B()} for f in FILTERS}
    srf_sym = {f: {0: B(), 1: B()} for f in FILTERS}
    srf_q = {f: {} for f in FILTERS}
    srf_lb = {lb: B() for lb in LOOKBACKS}  # 파라미터 민감도(filter=a224)
    # baseline: 무조건 + 레짐별, split·분기
    base = {"IS": B(), "OOS": B()}
    base_reg = {f: {"IS": B(), "OOS": B()} for f in FILTERS}
    base_q = {}
    base_reg_q = {f: {} for f in FILTERS}
    used = 0

    for fp in files:
        try:
            d = json.load(open(fp, encoding="utf-8")).get("data", [])
        except Exception:
            continue
        if len(d) < 480:
            continue
        used += 1
        o = [float(x["open"]) for x in d]
        h = [float(x["high"]) for x in d]
        lo = [float(x["low"]) for x in d]
        c = [float(x["close"]) for x in d]
        dt = [x["date"] for x in d]
        n = len(c)
        e60 = _ema_series(c, 60)
        e112 = _ema_series(c, 112)
        e224 = _ema_series(c, 224)
        e448 = _ema_series(c, 448)
        sym = os.path.basename(fp)[:-5]
        sset = sum(ord(ch) for ch in sym) % 2

        for i in range(224, n):
            if i + H >= n:
                continue
            fr = c[i + H] / c[i] - 1
            half = "IS" if dt[i] < SPLIT_DATE else "OOS"
            q = quarter(dt[i])
            flags = regime_flags(i, c, e60, e112, e224, e448)

            # baseline (무조건 + 레짐별)
            base[half].add(fr); base_q.setdefault(q, B()).add(fr)
            for f in FILTERS:
                if flags[f]:
                    base_reg[f][half].add(fr)
                    base_reg_q[f].setdefault(q, B()).add(fr)

            # sr_flip (lookback sweep)
            if i < 26:
                continue
            prev_close = c[i - 1]
            for lb in LOOKBACKS:
                if i < lb + 2:
                    continue
                whi = max(h[i - (lb + 1):i])
                if not (prev_close <= whi < c[i]):
                    continue
                hi_rel = max(range(i - (lb + 1), i), key=lambda j: h[j])
                if hi_rel + 1 > i - 1:
                    continue
                if whi - min(lo[hi_rel + 1:i]) <= 0:
                    continue
                net = fr - COST
                if lb == 20:
                    srf_lb_key = None
                    for f in FILTERS:           # filter 별 분배
                        if flags[f]:
                            srf[f][half].add(net)
                            srf_sym[f][sset].add(net)
                            srf_q[f].setdefault(q, B()).add(net)
                if flags["a224"]:
                    srf_lb[lb].add(net)         # 파라미터 민감도(a224 기준)

    print(f"평가 종목={used}")

    def fmt(label, b, ref=None, ref2=None):
        s = f"{label:<26} N={b.n:>7,} net={b.mean():+.3%} win={b.win():.1%}"
        if ref is not None:
            s += f" | uncond Δ{b.mean()-ref.mean():+.3%}"
        if ref2 is not None:
            s += f" | regimeΔ{b.mean()-ref2.mean():+.3%}"
        return s

    print("\n" + "=" * 78)
    print("sr_flip × 레짐필터 — net(H20, 비용차감) / uncond·regime baseline 대비")
    print("=" * 78)
    for f in FILTERS:
        allb = B()
        for s in ("IS", "OOS"):
            allb.n += srf[f][s].n; allb.s += srf[f][s].s; allb.w += srf[f][s].w
        ub = B(); rb = B()
        for s in ("IS", "OOS"):
            ub.n += base[s].n; ub.s += base[s].s
            rb.n += base_reg[f][s].n; rb.s += base_reg[f][s].s
        name = {"base": "공구리(필터없음)", "a224": "+above_ma224", "jeong": "+정배열(112>224>448)", "a60": "+above_ma60"}[f]
        print(fmt(name + " 전체", allb, ub, rb))
        print("   " + fmt("  IS", srf[f]["IS"], base["IS"], base_reg[f]["IS"]))
        print("   " + fmt("  OOS", srf[f]["OOS"], base["OOS"], base_reg[f]["OOS"]))
        print(f"     종목세트0 net={srf_sym[f][0].mean():+.3%}(N={srf_sym[f][0].n}) / "
              f"세트1 net={srf_sym[f][1].mean():+.3%}(N={srf_sym[f][1].n})")
        # 약세분기 2026Q2
        wq = srf_q[f].get("2026Q2", B()); wb = base_reg_q[f].get("2026Q2", B())
        print(f"     2026Q2(약세): net={wq.mean():+.3%}(N={wq.n}) vs regime-base {wb.mean():+.3%} "
              f"(Δ{wq.mean()-wb.mean():+.3%})")

    print("\n파라미터 민감도(+above_ma224, lookback):")
    for lb in LOOKBACKS:
        print(f"   lookback={lb}: net={srf_lb[lb].mean():+.3%} (N={srf_lb[lb].n:,})")

    print("\n분기별 net (+above_ma224 vs regime-base):")
    for q in sorted(srf_q["a224"]):
        a = srf_q["a224"][q]; rb = base_reg_q["a224"].get(q, B())
        print(f"   {q}: net={a.mean():+.3%}(N={a.n}) regimeΔ{a.mean()-rb.mean():+.3%}")

    # ── 판정 ──
    print("\n" + "=" * 78 + "\n판정 (레짐 결합이 sr_flip 알파를 개선하는가)\n" + "=" * 78)
    for f in ("a224", "jeong", "a60"):
        IS, OOS = srf[f]["IS"], srf[f]["OOS"]
        rIS, rOOS = base_reg[f]["IS"], base_reg[f]["OOS"]
        n = IS.n + OOS.n
        passed = (
            n >= 30 and IS.mean() > 0 and OOS.mean() > 0
            and IS.mean() > rIS.mean() and OOS.mean() > rOOS.mean()
            and srf_sym[f][0].mean() > 0 and srf_sym[f][1].mean() > 0
        )
        wq = srf_q[f].get("2026Q2", B()); wb = base_reg_q[f].get("2026Q2", B())
        weak_ok = wq.n == 0 or wq.mean() >= wb.mean()
        name = {"a224": "+above_ma224", "jeong": "+정배열", "a60": "+above_ma60"}[f]
        print(f"{name}: {'PASS' if passed else 'FAIL'} "
              f"(N={n}, IS·OOS net>0 & regimeΔ>0 & 종목양쪽>0; 약세분기 {'개선' if weak_ok else '악화'})")
    print("\n⚠️ 한계: 평가구간 ~2024-10~2026-06(불장). 정배열(448 warmup)은 표본·구간 축소(후기 편중).")


if __name__ == "__main__":
    main()
