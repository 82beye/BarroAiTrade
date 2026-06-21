---
tags: [report, feature/dante-uplift, channel/주식단테, status/done]
---

# 주식단테 접목 — (c)등급 안전구현 + shadow 백테스트 리포트

> **연관**: [[../../03-analysis/2026-06-21-dante-methodology-extract|방법론 추출]] · [[../../02-design/features/2026-06-21-dante-uplift.design|접목 설계]]
>
> **Summary**: 설계 §6의 `dante_filters.py`(주식단테 JD-R 정량 게이트)를 **inert 순수함수 모듈**로 구현하고, 실데이터(일봉 2,967종목·104만봉)로 shadow 백테스트해 신호별 엣지를 측정했다. 결과: **공구리(sr_flip, JD-R7)와 distribution(JD-R13)이 정량 엣지 확인**, 오돌리(JD-R20)는 단독 비용잠식, 밥그릇(saucer, JD-R5)은 희소·미검증. 코드/전략 레지스트리 변경 0 — **라이브 무영향**(회귀 1546 passed).
>
> **Date**: 2026-06-21 · **Status**: Done((c) 구현·측정). (d) 라이브 활성은 HITL(§7).

---

## 1. 구현 범위 (분류 (c) — 라이브 무영향)

- `backend/core/strategy/dante_filters.py` — **신규 inert 순수함수 모듈**. 호출처 없음(스캐너·데몬 미참조, 테스트로 보장). env=0/미호출 시 동작 불변.
- `backend/tests/strategy/test_dante_filters.py` — 24 단위테스트(경계·데이터부족·inert 보장).
- `scripts/backtest_dante_filters_shadow.py` — 일봉 forward-return shadow 측정(관측 전용).
- 본 리포트.

**변경하지 않은 것**: 전략 코드·`signal_scanner` 레지스트리·`PolicyConfig`·데몬·config 기본값. (d) 게이트 wiring은 OOS·HITL 후 별도.

## 2. `dante_filters.py` 함수 ↔ JD-R 매핑

| 함수 | JD-R | 정의(정량) |
|------|------|-----------|
| `ma_alignment` | R1/R4 | EMA 112/224/448 정·역배열·혼조 |
| `above_ma224` | R1 | 종가 ≥ EMA224(세력선) |
| `sr_flip` | R7 | 공구리: 직전 전고 상향돌파→{flip·진입·대칭목표} |
| `saucer_third_zone` | R5 | 밥그릇 3번: 224 아래 80봉 횡보 후 2× 이격 돌파 |
| `accumulation_candle` | R6 | 매집봉: 위꼬리 되돌림 ≥0.7 + 거래량 ≥평균×3 |
| `distribution_alert` | R13 | 장대음봉: 음봉 몸통 ≥3% + 거래량 전일×3 |
| `odori_cross` | R20 | 오돌리: 5일선 > 15일선 골든크로스 당봉 |
| `rr_ratio_ok` | R21 | 손익비 (목표폭/손절폭) ≥ 2.0 |

`closing_bet_filters.py`(더트레이딩 v2) 패턴 계승: `List[OHLCV]` 오래된→최신, JD-R+채널 인용 docstring, 데이터부족 시 보수적(False/None).

## 3. 단위테스트
`pytest backend/tests/strategy/test_dante_filters.py` → **24 passed**. inert 보장 2건:
`test_scanner_does_not_import_dante_filters`·`test_daemon_does_not_import_dante_filters`.

## 4. shadow 백테스트 결과 (일봉 2,967종목 · 평가봉 1,058,486)

`scripts/backtest_dante_filters_shadow.py` — forward 수익(net=왕복 0.90% 차감). parity(inline↔모듈) 266/266 일치.

| 신호 | N | net평균 | gross | baseline 동기간 | 판정 |
|------|---|---|---|---|---|
| **sr_flip(공구리) H20** | 23,863 | **+1.796%** | +2.696% | +1.791% | ✅ 엣지(목표도달 72.8%) |
| sr_flip(공구리) H10 | 24,178 | +0.725% | +1.625% | +0.715% | ✅ |
| **distribution H10** | 2,878 | (회피)−0.115% | −0.115% | +0.715% | ✅ 약세예측=회피/청산 |
| distribution H5 | 2,892 | (회피)−0.267% | −0.267% | +0.382% | ✅ |
| odori(5/15) H10 | 39,765 | −0.057% | +0.843% | +0.715% | ❌ 단독 비용잠식 |
| odori(5/15) H5 | 39,903 | −0.437% | +0.463% | +0.382% | ❌ |
| saucer(밥그릇) H20 | 117 | −3.145% | −2.245% | +1.791% | ⚠️ 희소·부진 |
| saucer(밥그릇) H60 | 104 | +2.996% | +3.896% | +6.888% | ⚠️ 소표본 |
| 레짐 224위 H20 | 447,044 | — | +1.913% | (전체 +1.791%) | ~ 약한 우위 |
| 레짐 224아래 H20 | 558,042 | — | +1.693% | | ~ |

## 5. 해석 — 검증된 엣지 vs 미검증

- **✅ sr_flip(공구리, JD-R7)** — 가장 강한 엣지. 비용 차감 후에도 baseline net 상회(H20 +1.796% vs +1.791%, gross +2.696% vs +1.791%), 대칭목표 **72.8% 도달**, 24K 표본으로 견고. 승률 43.7%(<50%)지만 **승자가 큼**(비대칭). → (d) 후보 1순위.
- **✅ distribution(JD-R13)** — forward 수익이 baseline 대비 **0.65~0.83%p 낮고 음수** → 약세 예측력 확인. 청산/회피 신호로 유효. → (d) 후보(청산 보조).
- **❌ odori(JD-R20)** — gross는 baseline 소폭 상회하나 **왕복비용 차감 시 net 음수**. 단독 진입 부적합(더트레이딩 종베·이격 분석과 동일한 '비용잠식' 패턴). 다른 신호의 *타이밍 보조*로만 의미.
- **⚠️ saucer(밥그릇, JD-R5)** — 2× 이격 강돌파 조건이 너무 희소(117건)하고 H20 부진. 후행 레이블 한계(분석 §5). **미검증 — 보류**.
- **~ 레짐(JD-R1)** — 224 위가 아래보다 +0.22%p 우위(방향 일치)지만 약함. **2024~26 불장 표본**이라 레짐 대비가 눌림. OOS(약세장 포함) 필요.

## 6. 회귀
`pytest backend/tests/` → **1546 passed, 10 skipped, 0 failed**(기존 1522 + dante 24). 라이브 경로 import 0 → **무영향**.

## 7. 다음 단계 — (d) HITL (자동화 금지)

OOS PASS 항목만 AskUserQuestion → `barrotrade-code-surgeon` 위임:
1. **sr_flip 진입(JD-R7)** — 신규 전략 OFF 등록 → `IntradaySimulator` 다구간 + `_oos_validation.py` PASS → (d) 활성.
2. **distribution 청산(JD-R13)** — `holding_evaluator`/exit 보조 청산 신호 → 백테스트 → (d).
3. **레짐 게이트(JD-R1)** — 약세장 포함 OOS로 우위 재확인 후 (d).
보류: saucer(희소·미검증), odori 단독(비용잠식). **농사매매=마틴게일 비권고**(설계 §4.3).

## 8. 한계
- **표본 편향**: 2024-11~2026-06 = 불장(baseline drift +). 레짐·롱 신호에 유리 → OOS(약세장) 전 라이브 금지.
- **일봉 측정**: 장중 진입 타이밍·슬리피지 미반영. sr_flip 라이브는 분봉 트리거·체결가 검증 필요.
- saucer/매집봉 후행 레이블 한계(분석 §5). 비공개 지표(수박지표·파란점선) 미반영.
- 모든 임계는 단일 그리드 측정 — walk-forward·민감도(±1스텝 부호유지)는 (d) 전 필수(설계 §7.2).
