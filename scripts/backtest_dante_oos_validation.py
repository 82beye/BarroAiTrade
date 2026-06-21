#!/usr/bin/env python3
"""공구리(sr_flip)·distribution OOS 검증 — 선택편향·과최적화·종목쏠림 관문.

배경: shadow 백테스트(2026-06-21)에서 sr_flip·distribution 엣지를 측정했으나
in-sample이 2024~26 불장 편향. 본 스크립트는 _oos_validation.py 정신(trades≥30,
holdout>0, 부호안정)을 차용해 3축으로 OOS 검증한다:
  ① 기간 분할: IS(이른 절반) vs OOS(늦은 절반) + 분기별 breakdown
  ② 종목 분할: hash(symbol)%2 disjoint 두 세트 양쪽 유지 확인
  ③ 파라미터 민감도: 핵심 임계 ±1스텝에 net 부호 유지

한계: 224일 EMA warmup이 첫 ~1년을 소진 → 평가구간 ≈2024-10~2026-06(거의 불장).
**진정한 약세장 OOS는 본 데이터셋으로 불가** → (d) 전 라이브 dry-run/장기데이터 필수.

진입신호(sr_flip): net = forward 수익 − 왕복 0.90%. PASS = trades≥30 ∧ IS·OOS net>0
  ∧ OOS net > OOS baseline net ∧ 두 종목세트 net>0 ∧ 파라미터 변형 전부 net>0.
회피신호(distribution): forward 수익이 baseline보다 낮은가(약세 예측). PASS = IS·OOS
  모두 distribution fwd < baseline fwd ∧ 음수 ∧ 파라미터 변형 부호 유지.
"""
from __future__ import annotations

import argparse
import glob
import json
import os

COST = 0.009
DEFAULT_CACHE = "/Users/beye/workspace/BarroAiTrade/data/ohlcv_cache"
SPLIT_DATE = "20250901"  # IS < SPLIT ≤ OOS (평가구간 ~2024-10~2026-06 의 중간)


def _ema_series(closes, period):
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


class Bucket:
    __slots__ = ("n", "s", "w")
    def __init__(self):
        self.n = 0; self.s = 0.0; self.w = 0
    def add(self, x):
        self.n += 1; self.s += x; self.w += 1 if x > 0 else 0
    def mean(self):
        return self.s / self.n if self.n else 0.0
    def win(self):
        return self.w / self.n if self.n else 0.0


def quarter(date):
    y = date[:4]; m = int(date[4:6])
    return f"{y}Q{(m-1)//3 + 1}"


def load(files):
    out = []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8")).get("data", [])
        except Exception:
            continue
        if len(d) < 460:
            continue
        sym = os.path.basename(f)[:-5]
        out.append((sym, d))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.cache, "*.json")))
    data = load(files)
    print(f"cache={args.cache}  종목={len(data)}  split={SPLIT_DATE}  cost={COST:.2%}")

    H = 20   # sr_flip 평가 호라이즌
    HD = 10  # distribution 평가 호라이즌

    # 파라미터 민감도용 변형
    srflip_lookbacks = [15, 20, 25]
    distrib_volmults = [2.5, 3.0, 3.5]
    distrib_bodymins = [0.02, 0.03, 0.04]

    # 누적기: dim -> bucket
    base = {"IS": Bucket(), "OOS": Bucket()}           # baseline H20 (sr_flip 비교)
    baseD = {"IS": Bucket(), "OOS": Bucket()}          # baseline H10 (distribution 비교)
    srf = {"IS": Bucket(), "OOS": Bucket()}            # sr_flip net H20
    srf_sym = {0: Bucket(), 1: Bucket()}               # 종목 분할
    srf_q = {}                                         # 분기별 net
    dst = {"IS": Bucket(), "OOS": Bucket()}            # distribution fwd H10
    dst_sym = {0: Bucket(), 1: Bucket()}
    dst_q = {}
    base_q = {}                                        # 분기별 baseline H20
    baseD_q = {}                                       # 분기별 baseline H10
    srf_param = {lb: Bucket() for lb in srflip_lookbacks}
    dst_param_v = {vm: Bucket() for vm in distrib_volmults}
    dst_param_b = {bm: Bucket() for bm in distrib_bodymins}
    target_hit = [0, 0]

    for sym, d in data:
        o = [float(x["open"]) for x in d]
        h = [float(x["high"]) for x in d]
        l = [float(x["low"]) for x in d]
        c = [float(x["close"]) for x in d]
        v = [float(x["volume"]) for x in d]
        dt = [x["date"] for x in d]
        n = len(c)
        ema224 = _ema_series(c, 224)
        sma60 = _sma_series(c, 60)
        sset = sum(ord(ch) for ch in sym) % 2  # 결정적 종목 분할(재현성)

        for i in range(224, n):
            date = dt[i]
            half = "IS" if date < SPLIT_DATE else "OOS"
            q = quarter(date)

            # baseline
            if i + H < n:
                fr = c[i + H] / c[i] - 1
                base[half].add(fr); base_q.setdefault(q, Bucket()).add(fr)
            if i + HD < n:
                frd = c[i + HD] / c[i] - 1
                baseD[half].add(frd); baseD_q.setdefault(q, Bucket()).add(frd)

            # ── sr_flip (공구리) param sweep ──
            if i >= 26:
                prev_close = c[i - 1]
                for lb in srflip_lookbacks:
                    if i < lb + 2:
                        continue
                    window_hi = max(h[i - (lb + 1):i])
                    if prev_close <= window_hi < c[i]:
                        hi_rel = max(range(i - (lb + 1), i), key=lambda j: h[j])
                        if hi_rel + 1 <= i - 1:
                            low_after = min(l[hi_rel + 1:i])
                            if window_hi - low_after > 0 and i + H < n:
                                net = (c[i + H] / c[i] - 1) - COST
                                srf_param[lb].add(net)
                                if lb == 20:  # base 파라미터로 분할/기간 평가
                                    srf[half].add(net)
                                    srf_sym[sset].add(net)
                                    srf_q.setdefault(q, Bucket()).add(net)
                                    target = window_hi + (window_hi - low_after)
                                    target_hit[1] += 1
                                    if max(h[i + 1:i + 1 + H]) >= target:
                                        target_hit[0] += 1

            # ── distribution param sweep (정배열 proxy: close>sma60) ──
            if i >= 1 and o[i] > 0 and c[i] < o[i] and v[i - 1] > 0 and sma60[i] is not None and c[i] > sma60[i]:
                body = (o[i] - c[i]) / o[i]
                volr = v[i] / v[i - 1]
                if i + HD < n:
                    frd = c[i + HD] / c[i] - 1
                    for vm in distrib_volmults:
                        if body >= 0.03 and volr >= vm:
                            dst_param_v[vm].add(frd)
                    for bm in distrib_bodymins:
                        if body >= bm and volr >= 3.0:
                            dst_param_b[bm].add(frd)
                    if body >= 0.03 and volr >= 3.0:  # base 파라미터
                        dst[half].add(frd); dst_sym[sset].add(frd)
                        dst_q.setdefault(q, Bucket()).add(frd)

    def line(name, b, ref=None):
        extra = ""
        if ref is not None:
            extra = f"  (baseline {ref.mean():+.3%}, Δ {b.mean()-ref.mean():+.3%})"
        return f"{name:<22} N={b.n:>7,}  mean={b.mean():+.3%}  win={b.win():.1%}{extra}"

    print("\n" + "=" * 70)
    print("공구리 sr_flip — 진입 net(H20, 비용차감)")
    print("=" * 70)
    print(line("IS net", srf["IS"], base["IS"]))
    print(line("OOS net", srf["OOS"], base["OOS"]))
    print(line("종목세트0 net", srf_sym[0]))
    print(line("종목세트1 net", srf_sym[1]))
    if target_hit[1]:
        print(f"대칭목표 도달율(H20): {target_hit[0]}/{target_hit[1]} = {target_hit[0]/target_hit[1]:.1%}")
    print("분기별 net:")
    for q in sorted(srf_q):
        print(f"   {q}: " + line("", srf_q[q], base_q.get(q)).strip())
    print("파라미터 민감도(lookback):")
    for lb in srflip_lookbacks:
        print(f"   lookback={lb}: " + line("", srf_param[lb]).strip())

    print("\n" + "=" * 70)
    print("distribution — 회피신호(H10 forward, baseline 대비 약세여야 PASS)")
    print("=" * 70)
    print(line("IS fwd", dst["IS"], baseD["IS"]))
    print(line("OOS fwd", dst["OOS"], baseD["OOS"]))
    print(line("종목세트0 fwd", dst_sym[0]))
    print(line("종목세트1 fwd", dst_sym[1]))
    print("분기별 fwd:")
    for q in sorted(dst_q):
        print(f"   {q}: " + line("", dst_q[q], baseD_q.get(q)).strip())
    print("파라미터 민감도(vol_mult, body_min=3%):")
    for vm in distrib_volmults:
        print(f"   vol_mult={vm}: " + line("", dst_param_v[vm]).strip())
    print("파라미터 민감도(body_min, vol_mult=3.0):")
    for bm in distrib_bodymins:
        print(f"   body_min={bm}: " + line("", dst_param_b[bm]).strip())

    # ── 판정 ──
    print("\n" + "=" * 70 + "\n판정\n" + "=" * 70)
    srf_pass = (
        srf["IS"].n + srf["OOS"].n >= 30
        and srf["IS"].mean() > 0 and srf["OOS"].mean() > 0
        and srf["OOS"].mean() > base["OOS"].mean()
        and srf_sym[0].mean() > 0 and srf_sym[1].mean() > 0
        and all(srf_param[lb].mean() > 0 for lb in srflip_lookbacks)
    )
    srf_q_pos = sum(1 for q in srf_q if srf_q[q].mean() > 0)
    print(f"공구리 sr_flip: {'PASS' if srf_pass else 'FAIL'} "
          f"(IS·OOS net>0, OOS>baseline, 종목양쪽>0, 파라미터 전부>0; 분기 {srf_q_pos}/{len(srf_q)} 양수)")
    dst_pass = (
        dst["IS"].n + dst["OOS"].n >= 30
        and dst["IS"].mean() < baseD["IS"].mean() and dst["OOS"].mean() < baseD["OOS"].mean()
        and dst["IS"].mean() < 0 and dst["OOS"].mean() < 0
        and all(dst_param_v[vm].mean() < baseD["IS"].mean() for vm in distrib_volmults)
    )
    dst_q_weak = sum(1 for q in dst_q if dst_q[q].mean() < baseD_q.get(q, Bucket()).mean())
    print(f"distribution:   {'PASS' if dst_pass else 'FAIL'} "
          f"(IS·OOS 모두 baseline 하회·음수, 파라미터 부호유지; 분기 {dst_q_weak}/{len(dst_q)} baseline 하회)")
    print("\n⚠️ 한계: 평가구간 ≈2024-10~2026-06(불장 편향). 진정한 약세장 OOS 불가 → "
          "(d) 라이브 활성 전 약세장 포함 장기데이터/dry-run 필수.")


if __name__ == "__main__":
    main()
