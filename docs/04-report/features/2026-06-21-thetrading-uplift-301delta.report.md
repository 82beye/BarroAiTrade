---
tags: [report, feature/thetrading-uplift, corpus/301, status/complete]
---

# 더트레이딩 방법론 접목 — 풀코퍼스(301편) 증분 구현 리포트

> **연관 분석**: [[../../03-analysis/2026-06-21-thetrading-methodology-extract-v2-301편|추출 v2(301편)]]
> **연관 설계**: [[../features/2026-06-21-thetrading-uplift-301delta.design|301델타 분석·설계]]
>
> **Project**: BarroAiTrade · **Date**: 2026-06-21 · **Branch**: `feat/thetrading-uplift-301delta`
> **분류**: 전부 **(c) 안전**(신규 순수함수·inert·관측전용) — **라이브 매매 동작 무변경**
> **테스트**: 전체 **1522 passed, 10 skipped, 0 failed** (기존 1496 + 신규 26)

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

## 6. 삼박자(D-R44) shadow 측정 결과 — 필터가 net edge 기여 (양성)

`scripts/backtest_closing_bet_triple_shadow.py` (baseline 종베 432트립을 삼박자로 분할, 동일 유니버스/청산):

| 버킷 | 트립 | 승률% | gross% | net@0.55 | **net@0.90** |
|------|-----:|------:|-------:|--------:|------------:|
| baseline_all (대조) | 432 | 55.8 | +1.007 | +0.457 | **+0.107** |
| **triple_pass (삼박자 충족)** | 158 | 60.1 | +1.186 | +0.636 | **+0.286** |
| triple_fail (미충족) | 274 | 53.3 | +0.904 | +0.354 | +0.004 |
| env_pass (D-R42 단독) | 359 | 56.5 | +1.044 | +0.494 | +0.144 |
| **disp_pass (D-R43 단독)** | 238 | 61.8 | +1.305 | +0.755 | **+0.405** |

**해석**:
1. **삼박자 충족 시 net@0.90 +0.286%** (baseline +0.107% 대비 **Δ+0.179%p, ≈2.7배**), 승률 55.8→60.1%, 표본 37%(432→158) 유지. **미충족군은 +0.004%(손익분기)** → 필터가 엣지를 정확히 분리. zone/money_flow 게이트(net 악화)와 **정반대** — 표본 축소가 per-trade 개선에 의해 상쇄됨.
2. **이격도 노란불(D-R43) 단독이 최강**(net@0.90 **+0.405%**, 승률 61.8%, 238트립). 엔벨로프(D-R42)·거래대금 제약이 full triple을 다소 과필터 → **이격도가 엣지의 핵심 동인**.
3. 다만 정정비용(0.90%) 기준 절대 net은 여전히 작다(+0.286%/트립). 우대요율 협의 시 net@0.55(+0.636%)로 크게 개선 → **요율이 여전히 1순위 레버**.
- 산출: `docs/04-report/features/2026-06-21-closing-bet-triple-shadow.json`.

## 7. 이격도 노란불 게이트 (d) — config-gated 구현 (HITL 승인 후)

사용자 HITL 승인(AskUserQuestion: config-gated 옵션 구현 + 임계 +14.25%)에 따라 `ClosingBetParams`에 게이트 추가:
- `require_disparity_yellow: bool = False`(**default OFF = 현행 스캐폴드 byte-identical 보존**), `disparity_yellow_threshold: float = 0.1425`.
- `_analyze_v2` ④-b: `require_disparity_yellow` 시 `disparity_yellow(candles, threshold)` 미충족이면 진입 거부. closing_bet 자체가 `_DEFAULT_ENABLED=False`라 **라이브 무영향**.
- 단위테스트 +3(default-OFF parity / ON 저이격 거부 / ON 노란불 허용). 회귀 **1522 passed, 0 failed**.

**end-to-end 정합 검증**(구현 게이트를 백테스트로 재실행):

| 변형 | 트립 | 승률% | net@0.90 |
|------|-----:|------:|---------:|
| baseline | 432 | 55.8 | +0.107 |
| **+disparity_gate (구현)** | 238 | 61.8 | **+0.405** |

→ 구현 게이트가 §6 shadow의 `disp_pass`(238/61.8%/+0.405%)와 **정확히 일치** → 구현 정합성 확인.

> ⚠️ 게이트는 **옵션으로만 추가**됨(default OFF). 종베 자체의 **라이브 활성**은 여전히 별도 (d) HITL(요율 협의·sim 정합·OOS 후).

## 8. 이격도 게이트 robustness 검증 (seed·기간·임계) — ROBUST

`scripts/backtest_closing_bet_disparity_robustness.py`. 이격도 게이트는 일봉(5MA) 기반이라 5분봉 제약 없이 **전체 일봉 250봉**으로 평가 → 표본 대폭 확대(**148,211 stock-day / baseline 3,427 신호** vs 5분봉 432).

**[A] 전체(장기간)** — 게이트가 손익분기 baseline을 net 양수로 전환:

| 변형 | 트립 | 승률% | net@0.90 |
|------|-----:|------:|---------:|
| baseline | 3,427 | 54.2 | **+0.008** (손익분기) |
| **+gate@0.1425** | 1,586 | 62.3 | **+0.520** |

**[B] 임계 sweep — 절벽형 최적 아님(단조·전구간 양수)**:

| thr | 0.10 | 0.1225 | 0.1425 | 0.1625 | 0.18 |
|-----|-----:|------:|------:|------:|-----:|
| net@0.90 | +0.224 | +0.342 | +0.520 | +0.680 | +0.815 |
| 승률% | 57.5 | 59.4 | 62.3 | 65.1 | 67.7 |

→ net·승률이 임계와 함께 **단조 증가**, ±스텝 부호 안정 → 과최적화 아님. (+14.25%는 보수적 중간; 더 선택적 임계는 net↑·표본↓ trade-off.)

**[C] 5 seed(종목 50% 서브샘플)** — 전 seed 게이트 우위:

| seed | 1 | 2 | 3 | 4 | 5 |
|------|--:|--:|--:|--:|--:|
| baseline net90 | −0.081 | −0.106 | +0.068 | −0.044 | +0.026 |
| **gate net90** | +0.417 | +0.447 | +0.609 | +0.480 | +0.543 |
| Δ | +0.498 | +0.553 | +0.541 | +0.524 | +0.517 |

**[D] 기간 early/late(시간 OOS, 분할 2026-02-04)**:

| 구간 | baseline net90 | gate net90 |
|------|---------------:|-----------:|
| early | −0.054 | **+0.515** |
| late | +0.070 | **+0.524** |

**판정: ROBUST** (seeds OK · period OK · sweep net>0 OK). 게이트 net edge가 유니버스 서브샘플·시간 구간·임계 선택 전반에서 안정. baseline은 장기 손익분기 → **게이트가 종베를 net 양수로 만드는 핵심**.
- 산출: `docs/04-report/features/2026-06-21-disparity-gate-robustness.json`.

## 9. 다음 단계
1. ✅ 삼박자 shadow 측정 → D-R43 이격도 최대 기여.
2. ✅ 이격도 게이트 config-gated 구현(default-OFF) + 백테스트 정합 검증.
3. ✅ robustness 검증(seed·기간·임계) → ROBUST.
4. 요율 협의 결과 반영 → 5분봉/일봉 sim 정합 → OOS → **종베 라이브 + 게이트 ON** (d) 결정(잔여 HITL).

> 브랜치 `feat/thetrading-uplift-301delta`. 커밋까지 자동, **push·머지·라이브 활성은 HITL**.
