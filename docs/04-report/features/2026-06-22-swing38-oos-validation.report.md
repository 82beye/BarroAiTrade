---
tags: [report, strategy/swing_38, validation/oos, status/done]
---

# swing_38 OOS 검증 리포트

> **Summary**: 현재 활성 중인 다일 스윙 전략 `swing_38`(임펄스→Fib0.382→반등, max_hold 20)을 기존 OOS 관문(`scripts/_oos_validation.py`)을 그대로 재사용해 멀티 seed 검증. **5/5 seed PASS** — active 106~114·trades 649~789·승률 42.7~44.4%·avg_ret +2.36~+2.78%/라운드트립(브로커 실측비용 차감)·**holdout +2.93~+3.75%(full 상회 → 과최적화 없음)**·drop1 전부 안정. 기존 튜닝(SL−15%×D+20)이 OOS에서도 유지됨을 확인. 라이브 무변경(관측).
>
> **Date**: 2026-06-22 · **Status**: Done(검증). swing_38 은 이미 활성 — 본 검증은 사후 정당성 확인.

---

## 1. 방법 (진실원천 재사용 — 재구현 0)
- `scripts/oos_validation_swing38.py` = `_oos_validation.py`의 로더·시뮬레이터·게이트를 import 하고 `STRATEGIES=["swing_38"]`만 오버라이드.
- 실제 `Swing38Strategy`(IntradaySimulator `_build_strategies` 가 `require_daily_candles=True·max_hold_days=20`으로 인스턴스화, exit_plan 보유기간 게이트 작동) + **브로커 실측 비용**(COMMISSION_PCT·TAX) + 실일봉(ohlcv_cache).
- seed별 랜덤 유니버스 n=120(유동성 하한), full vs **holdout(최근 40%)**, 게이트: active≥15 & trades≥30 & avg_ret>0 & drop1 부호안정 & holdout avg>0.

## 2. 결과 (5 seed)

| seed | uni | active | trades | win% | avg_ret% | holdout% | drop1 | 판정 |
|------|----:|-------:|-------:|-----:|---------:|---------:|:-----:|:----:|
| 42 | 120 | 106 | 649 | 43.5 | +2.429 | +3.189 | ✓ | PASS |
| 7 | 120 | 114 | 789 | 44.4 | +2.779 | +3.747 | ✓ | PASS |
| 123 | 120 | 108 | 659 | 44.2 | +2.557 | +3.264 | ✓ | PASS |
| 2024 | 120 | 111 | 701 | 44.2 | +2.684 | +3.724 | ✓ | PASS |
| 99 | 120 | 112 | 754 | 42.7 | +2.360 | +2.931 | ✓ | PASS |

**종합: 5/5 PASS · 전체 avg_ret 평균 +2.562% · holdout 평균 +3.371%.**

## 3. 해석
- **견고한 PASS.** 5개 독립 seed 전부 통과, 종목 수백·거래 수백건 표본. avg_ret(라운드트립 net) 양수, **holdout이 full보다 높음 → 시간 OOS에서 열화 없음(과최적화 아님)**. drop1(최대기여 종목 제거) 부호 안정 → outlier 의존 아님.
- 승률 43% < 50%이나 **비대칭 큰 승자**(TP +20/+50% × SL −10/−15%)로 기대값 양수 — 스윙 전략 정상 프로파일.
- 기존 튜닝(`max_hold_days=20`, SL=−15%; S6 그리드 자본가중 +1.808%)이 **OOS·실측비용에서도 유지**됨을 독립 확인.

## 4. 한계
- **불장 편향**: holdout(최근 40%) ≈ 2025~2026 강세 구간 → 약세장 OOS 아님(dante 트랙 동일 제약). holdout>full 은 안심 신호이나 진정한 베어 검증은 아님.
- **랜덤 유니버스**: 라이브는 거래대금 주도주 선정(`kiwoom_native_rank`) → 실제 유니버스와 다름. 본 검증은 전략 엣지의 *범용성*(임의 종목 다일 스윙) 확인.
- 비용=브로커 실측(보수). 슬리피지·동시보유 한도·자본곡선 MDD는 평균기준 밖.

## 5. 결론
swing_38(활성 유지)은 **OOS 견고 통과** — 현 활성 상태 정당. 추가 (d) 변경 불필요. 약세장 데이터 확보 시 재확인 권장.
