#!/usr/bin/env python3
"""ai_swing 백테스트 — 기존 _oos_validation 기계 재사용 + SL×trailing 그리드 (2026-07-30).

`scripts/oos_validation_swing38.py` 와 동일한 재사용 원칙(진실원천 1개, 재구현 0)에
그리드 스윕을 더한 것이다. 로더·시뮬레이터·판정 게이트는 전부 `_oos_validation` 것을
쓰고, 이 스크립트는 **유니버스 선택 + 파라미터 격자 주입 + 결과 표** 만 담당한다.

★ 반복 3 선행 필수 ★
  ai_swing 은 `IntradaySimulator._exit_plan_for_strategy` 의 ai_swing 분기를 통해
  라이브와 동일한 청산(TP +20/+50%·SL -15%·BE +10%·min_hold 3·max_hold 20)으로 돈다.
  그 분기가 없으면 시뮬은 고정 +3/+5/+7%·-1.5% 로 돌아 **그리드 결과가 무의미**해진다.
  (기존 swing_38 의 시뮬↔라이브 괴리가 정확히 그 문제였다.)

그리드 주입 방식
  `_oos_validation.backtest_universe` 는 `IntradaySimulator(...)` 를 함수 안에서
  지역 import 로 생성하며 `params_override` 를 넘기지 않는다. 그래서 모듈 속성
  `intraday_simulator.IntradaySimulator` 를 서브클래스로 **일시 교체**해 override 를
  주입한다(지역 import 라 매 호출 시 교체본이 잡힌다). 원본은 finally 로 반드시 복원한다.

유니버스 3택
  --random N --seed S       비변동성 랜덤 (선택편향 대조군, 기본)
  --universe-from-ai-trade  BARRO_AI_TRADE_DIR 의 오늘 스캔∩예측 교집합
  --universe-file PATH      개행/CSV 구분 종목코드 목록

판정 (§8 — 표본 부족을 PASS 로 쓰지 않는다)
  active≥15 & trades≥30 & avg_ret>0 & drop1 부호안정 & holdout>0 → PASS
  active/trades 미달 → **INSUFFICIENT** (FAIL 도 PASS 도 아닌 '판정 불가')

사용:
  python scripts/backtest_ai_swing.py --random 120 --seed 42 --grid "sl=-8,-10,-15,-20"
  python scripts/backtest_ai_swing.py --random 120 --seeds 42,7,123 --grid "sl=-15 tp1=15,20,25"
  python scripts/backtest_ai_swing.py --universe-from-ai-trade --grid "sl=-15"

⚠️ 주문 송출 없음 — 일봉 캐시만 읽는 순수 시뮬이다 (§2 S2).
⚠️ 캐시 신선도: 실행 시 meta.json 의 updated 를 헤더에 찍는다. 낙후 시
   `python scripts/update_ohlcv_cache.py`(키움 API 키 필요)로 갱신 후 재실행할 것.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import statistics
import sys
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

oos = importlib.import_module("_oos_validation")

# worktree 의 data/ 는 비어 있을 수 있음 → 메인레포 일봉 캐시로 오버라이드.
# (oos_validation_swing38.py 와 동일 패턴)
_MAIN_CACHE = Path("/Users/beye/workspace/BarroAiTrade/data/ohlcv_cache")
if _MAIN_CACHE.exists():
    oos.DAILY_CACHE = _MAIN_CACHE

SID = "ai_swing"

# 그리드 키 → AiSwingParams 필드 (percent 입력 → fraction 저장)
_PCT_FIELDS = {
    "sl": "sl_pct",
    "tp1": "tp1_pct",
    "tp2": "tp2_pct",
    "be": "be_pct",
    # 추적 수익화 축 — trail_start=0 이면 트레일링 비활성(효과 격리용 대조군)
    "trail_start": "trail_start_pct",
    "trail_off": "trail_offset_pct",
}
# 그리드 키 → 그대로 쓰는 필드 (정수/실수)
_RAW_FIELDS = {
    "min_hold": ("min_hold_days", int),
    "max_hold": ("max_hold_days", int),
    "min_score": ("min_score", float),
    "tp1_qty": ("tp1_qty", Decimal),
}


# ─── 순수 로직 (테스트 대상) ──────────────────────────────────────────────
def parse_grid(spec: str) -> list[dict]:
    """`"sl=-8,-15 max_hold=10,20"` → params_override 후보 dict 들의 데카르트 곱.

    - percent 필드(sl/tp1/tp2/be)는 `-15` → Decimal("-0.15") 로 변환한다.
    - 빈 문자열이면 `[{}]`(기본 파라미터 1회 실행)을 돌려준다.
    - 알 수 없는 키는 ValueError — 조용히 무시하면 스윕이 안 돈 걸 모르게 된다.
    """
    spec = (spec or "").strip()
    if not spec:
        return [{}]
    axes: list[tuple[str, list]] = []
    for token in spec.split():
        if "=" not in token:
            raise ValueError(f"그리드 토큰에 '=' 없음: {token!r}")
        key, raw = token.split("=", 1)
        key = key.strip()
        values: list = []
        for v in raw.split(","):
            v = v.strip()
            if not v:
                continue
            if key in _PCT_FIELDS:
                values.append(Decimal(v) / Decimal("100"))
            elif key in _RAW_FIELDS:
                caster = _RAW_FIELDS[key][1]
                values.append(caster(v) if caster is not Decimal else Decimal(v))
            else:
                raise ValueError(
                    f"알 수 없는 그리드 키: {key!r} "
                    f"(허용: {sorted(_PCT_FIELDS) + sorted(_RAW_FIELDS)})"
                )
        if not values:
            raise ValueError(f"그리드 값 없음: {token!r}")
        field = _PCT_FIELDS.get(key) or _RAW_FIELDS[key][0]
        axes.append((field, values))

    combos: list[dict] = [{}]
    for field, values in axes:
        combos = [{**c, field: v} for c in combos for v in values]
    return combos


def combo_label(combo: dict) -> str:
    """그리드 조합을 표에 쓸 짧은 라벨로. 빈 조합은 'default'."""
    if not combo:
        return "default"
    parts = []
    for k, v in combo.items():
        if isinstance(v, Decimal) and k.endswith("_pct"):
            parts.append(f"{k.replace('_pct', '')}={v * 100:+.0f}%")
        else:
            parts.append(f"{k.replace('_days', '')}={v}")
    return " ".join(parts)


def classify(active: int, trades: int, avg_ret: float, drop1_ok: bool,
             holdout_avg: float | None) -> tuple[str, list[str]]:
    """oos.verdict 결과를 INSUFFICIENT 로 강등 가능하게 감싼다.

    표본이 게이트 하한(active≥15·trades≥30)에 미달하면 성과 수치가 유의하지 않다 —
    FAIL(전략이 나쁨) 과 구별해 **INSUFFICIENT(판정 불가)** 로 표기한다 (§8).
    """
    v, fails = oos.verdict(active, trades, avg_ret, drop1_ok, holdout_avg)
    if active < oos.MIN_ACTIVE_SYMBOLS or trades < oos.MIN_TRADES:
        return "INSUFFICIENT", fails
    return v, fails


def load_universe_file(path: str) -> list[str]:
    """개행/콤마 구분 종목코드 파일 → 6자리 숫자 코드 목록(중복 제거, 순서 보존)."""
    raw = Path(path).read_text(encoding="utf-8")
    out: list[str] = []
    seen: set[str] = set()
    for chunk in raw.replace(",", "\n").split("\n"):
        s = chunk.strip()
        if len(s) == 6 and s.isdigit() and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def cache_as_of() -> str:
    """일봉 캐시 meta.json 의 updated — 리포트 헤더 라벨용 (§8 지연 데이터 라벨)."""
    try:
        meta = json.loads((oos.DAILY_CACHE / "meta.json").read_text(encoding="utf-8"))
        return str(meta.get("updated") or "unknown")
    except Exception:
        return "unknown"


# ─── I/O ──────────────────────────────────────────────────────────────────
def universe_from_ai_trade() -> list[str]:
    """BARRO_AI_TRADE_DIR 의 오늘 스캔∩예측 교집합 종목코드.

    status 가 ok/stale 이 아니면(no_data/partial) 빈 목록 — 날조하지 않는다 (§0-2).
    """
    from backend.core.scanner.ai_trade_universe import load_ai_trade_universe

    uni = load_ai_trade_universe()
    print(f"  ai-trade universe: status={uni.status} reason={uni.reason or '-'} "
          f"scan={uni.scan_count} pred={uni.pred_count} 교집합={uni.intersect_count} "
          f"(as_of {uni.as_of})")
    if uni.status not in ("ok", "stale"):
        return []
    return [it.symbol for it in uni.items]


def run_combo(symbols: list[str], combo: dict) -> dict:
    """한 그리드 조합으로 유니버스 전체를 백테스트하고 요약 + 판정을 돌려준다.

    `_oos_validation.backtest_universe` 는 params_override 를 받지 않으므로
    IntradaySimulator 를 서브클래스로 일시 교체해 주입한다(원본은 finally 복원).
    """
    import backend.core.backtester.intraday_simulator as sim_mod

    original = sim_mod.IntradaySimulator
    override = {SID: dict(combo)} if combo else None

    class _WithOverride(original):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            if override is not None:
                kwargs.setdefault("params_override", override)
            super().__init__(*args, **kwargs)

    oos.STRATEGIES = [SID]      # 기계가 참조하는 전략 목록 오버라이드
    sim_mod.IntradaySimulator = _WithOverride
    try:
        full, hold = oos.backtest_universe(symbols)
    finally:
        sim_mod.IntradaySimulator = original

    s = oos.summarize(full[SID])
    h = oos.summarize(hold[SID])
    d1 = oos.drop1_sign_stable(full[SID])
    v, fails = classify(s["active"], s["trades"], s["avg_ret"], d1, h["avg_ret"])
    return {
        "label": combo_label(combo), **s,
        "holdout_avg": h["avg_ret"], "holdout_trades": h["trades"],
        "drop1": d1, "verdict": v, "fails": fails,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ai_swing 백테스트 + SL×trailing 그리드")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--random", type=int, default=120, help="랜덤 유니버스 종목 수")
    src.add_argument("--universe-from-ai-trade", action="store_true",
                     help="BARRO_AI_TRADE_DIR 의 오늘 스캔∩예측 교집합 사용")
    src.add_argument("--universe-file", help="종목코드 목록 파일")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--seeds", help="멀티 seed csv (지정 시 --seed 무시, 랜덤 유니버스만)")
    ap.add_argument("--grid", default="", help='예: "sl=-8,-15 max_hold=10,20"')
    args = ap.parse_args()

    try:
        combos = parse_grid(args.grid)
    except ValueError as exc:
        print(f"그리드 파싱 실패: {exc}", file=sys.stderr)
        return 2

    print(f"=== ai_swing 백테스트 — 일봉·실비용·OOS 게이트 ===")
    print(f"일봉 캐시 as_of: {cache_as_of()}  (낙후 시 update_ohlcv_cache.py 로 갱신)")
    print(f"게이트: active≥{oos.MIN_ACTIVE_SYMBOLS} & trades≥{oos.MIN_TRADES} "
          f"& avg_ret>0 & drop1 안정 & holdout>0  "
          f"(표본 미달 → INSUFFICIENT, PASS 로 쓰지 않음)")
    print(f"그리드 조합 수: {len(combos)}")

    # ─ 유니버스 결정 ─
    seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else [args.seed]
    universes: list[tuple[str, list[str]]] = []
    if args.universe_from_ai_trade:
        universes.append(("ai-trade 교집합", universe_from_ai_trade()))
    elif args.universe_file:
        universes.append((f"file:{Path(args.universe_file).name}",
                          load_universe_file(args.universe_file)))
    else:
        for sd in seeds:
            universes.append((f"random seed={sd}", oos.select_random_universe(args.random, sd)))

    rows: list[dict] = []
    for uni_label, symbols in universes:
        print(f"\n── 유니버스: {uni_label} — {len(symbols)}종목 ──")
        if not symbols:
            print("  종목 0 — 백테스트 생략 (원천 없음/교집합 공집합).")
            continue
        print(f"{'조합':28}{'active':>7}{'trades':>7}{'win%':>7}"
              f"{'avg_ret%':>10}{'holdout%':>10}{'drop1':>7}  판정/사유")
        for combo in combos:
            r = run_combo(symbols, combo)
            r["universe"] = uni_label
            rows.append(r)
            print(f"{r['label']:28}{r['active']:>7}{r['trades']:>7}{r['win_rate']:>7.1f}"
                  f"{r['avg_ret']:>10.3f}{r['holdout_avg']:>10.3f}{str(r['drop1']):>7}  "
                  f"{r['verdict']}" + (f" ({'; '.join(r['fails'])})" if r["fails"] else ""))

    if not rows:
        print("\n결과 없음 — 유니버스가 비었다. 캐시/교집합 원천을 확인할 것.")
        return 1

    passes = [r for r in rows if r["verdict"] == "PASS"]
    insuf = [r for r in rows if r["verdict"] == "INSUFFICIENT"]
    scored = [r for r in rows if r["trades"] > 0]
    best = max(scored, key=lambda r: r["avg_ret"]) if scored else None
    print(f"\n종합: {len(passes)}/{len(rows)} 조합 PASS, {len(insuf)} INSUFFICIENT(표본 미달)")
    if best:
        print(f"최고 avg_ret: {best['label']} @ {best['universe']} "
              f"{best['avg_ret']:+.3f}% (holdout {best['holdout_avg']:+.3f}%, "
              f"trades {best['trades']}, {best['verdict']})")
    if scored:
        print(f"전체 avg_ret 평균: {statistics.fmean(r['avg_ret'] for r in scored):+.3f}%")
    print("⚠️ 비용=브로커 실측(보수). INSUFFICIENT 는 성과 판정이 아니라 표본 부족이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
