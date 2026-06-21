---
tags: [report, feature/thetrading-uplift, corpus/301, status/complete]
---

# 더트레이딩 방법론 접목 — 풀코퍼스(301편) 증분 구현 리포트

> **연관 분석**: [[../../03-analysis/2026-06-21-thetrading-methodology-extract-v2-301편|추출 v2(301편)]]
> **연관 설계**: [[../features/2026-06-21-thetrading-uplift-301delta.design|301델타 분석·설계]]
>
> **Project**: BarroAiTrade · **Date**: 2026-06-21 · **Branch**: `feat/thetrading-uplift-301delta`
> **분류**: 전부 **(c) 안전**(신규 순수함수·inert·관측전용) — **라이브 매매 동작 무변경**
> **테스트**: 전체 **1519 passed, 10 skipped, 0 failed** (기존 1496 + 신규 23)

---

## 1. 요약

유튜브 '더트레이딩' **전체 301편**(기존 추출은 99편)을 5개 시기 배치로 재채굴해 R1~R13을 3배 데이터로 재검증·정제하고 신규 규칙(R14+)을 델타로 추가했다(분석 문서). 시그니처 **종가베팅을 실측 백테스트**로 검증한 결과 **현재 비용구조에서 net edge가 얇아 라이브 활성을 정당화하지 못함**을 확인했다. 코드 변경은 델타 정량게이트를 **관측 전용 순수함수**로 1개 모듈 신설한 것뿐이며, 호출처가 없어(inert) 라이브에 영향이 없다. 실거래 변경(d)은 근거와 함께 HITL 제안으로만 남겼다.

---

## 2. 핵심 측정 — 종가베팅 백테스트 (비용이 구속제약)

`scripts/backtest_closing_bet_intraday.py` (리더 600종목, 5분봉, 19,700 stock-day):

| 변형 | 트립 | 승률% | gross%/트립 | net@0.55% | **net@0.90%(정정비용)** |
|------|-----:|------:|-----------:|---------:|----------------------:|
| baseline | 432 | 55.8 | +1.007 | +0.457 | **+0.107** |
| +money_flow | 264 | 55.3 | +0.997 | +0.447 | +0.097 |
| +zone | 53 | 54.7 | +0.880 | +0.330 | **−0.020** |
| +both | 35 | 48.6 | +0.369 | −0.181 | **−0.531** |

→ 정정 비용(왕복 ~0.90%)에서 baseline net은 gross의 **~11%만 잔존**. 게이트를 더할수록 net 악화(표본 축소 > edge 개선). **비용이 종베 수익의 구속 제약**.

### 2.1 두 시뮬의 괴리 (경고 신호)
OOS 관문(`_oos_validation.py --n 60 --seed 42`, 일봉·3분할·실비용):

| 전략 | active | trades | win% | avg_ret% | holdout% | 판정 |
|------|-------:|-------:|-----:|---------:|---------:|------|
| f_zone | 33 | 54 | 39 | +1.904 | +3.903 | **PASS** |
| sf_zone | 14 | 18 | 44 | +0.353 | n/a | **FAIL** |
| gold_zone | 54 | 381 | 39 | +1.620 | +1.101 | **PASS** |
| closing_bet | 50 | 254 | 43 | +3.553 | +4.816 | **PASS** |

→ 일봉 OOS는 종베를 +3.553%로 후하게 보지만 현실적 5분봉 ablation은 +0.107%/트립. **차이 = 오버나잇 전략의 장중 슬리피지·체결·갭 미반영(낙관 편향)**. 단일 sim PASS를 신뢰하지 말 것. sf_zone OOS FAIL(표본 부족·outlier)도 주의.

---

## 3. 구현 (c) — 관측 전용 순수함수 모듈

| 파일 | 내용 | 성격 |
|------|------|------|
| `backend/core/strategy/closing_bet_filters.py` | 델타 정량게이트 순수함수 7종 | 신규, **inert** |
| `backend/tests/strategy/test_closing_bet_filters.py` | 단위테스트 23건(경계·데이터부족·inert 단언) | 신규 |

**함수**(반환만, 부작용 없음):
- `body_new_high`(R6 몸통 신고가)·`overheat_warning`(D-R24 5일전×1.6)·`liquidity_ok`(D-R29/30 1분봉≥15억∧거래량≥전일×3)·`remaining_upside_ratio`(D-R14 잔존 기대수익).
- **삼박자(추가)**: `envelope_upper_break`(D-R42 20MA±20% 상단돌파)·`disparity_5ma`/`disparity_yellow`(D-R43 5일선 이격 +14.25%)·`triple_factor_buy`(**D-R44** 엔벨∧이격∧거래대금≥1000억 AND).

**무영향 보장**: 어떤 전략/스캐너도 이 모듈을 import 하지 않음(`test_module_is_inert_not_imported_by_live_path`가 `signal_scanner.py`·`closing_bet.py` 소스에 `closing_bet_filters` 부재를 단언). env·config 토글조차 없음 → byte-identical.

---

## 4. 검증

- 신규 테스트 **23 passed** (0.60s).
- 전체 회귀 **1519 passed, 10 skipped, 0 failed** (5.47s). 기존 1496 + 신규 23, 회귀 0.
- 백테스트 산출: `docs/04-report/features/2026-06-18-closing-bet-intraday-ablation.json`(재실행 일치).

---

## 5. 자동 진행하지 않은 것 — (d) HITL 잔여 (근거 첨부)

| 항목 | 근거 | 권고 |
|------|------|------|
| **종베 라이브 활성** | net@0.90 +0.107%/트립, 게이트 추가 시 음전, sim 괴리 | **보류** — 요율 인하 또는 edge 상향·sim 정합 후 |
| 우대요율 협의(`BARRO_COMMISSION_RATE`) | 비용 ~89% 잠식 | ops 액션(코드 아님), net 1순위 레버 |
| R5 top5 hard-cut / R7 조 단위 임계 | "5위 밖=주도주 아님", 2026 조 단위 | 선정 시뮬·shadow 후 (d) |
| R9 종베 NXT 시간창 이원화 | 델타 R9 구조변화 | NXT 체결 데이터·dry_run 필요 |
| D-R36 음봉 종베 활성 | 하락장 토글 | OOS 후 (d) |

---

## 6. 다음 단계
1. `closing_bet_filters` 를 종베/선정 백테스트에 **shadow 비교축**으로 연결(차단 X) → 단기과열·유동성·몸통신고가 필터의 net edge 개선 효과 측정.
2. 요율 협의 결과 반영 후 종베 net 재평가 → 5분봉/일봉 sim 정합 → OOS → (d) 라이브 결정.
3. R5/R7/R9 (d) 항목은 각 AskUserQuestion + `barrotrade-code-surgeon` 위임으로.

> 브랜치 `feat/thetrading-uplift-301delta`. 커밋까지 자동, **push·머지·라이브 활성은 HITL**.
