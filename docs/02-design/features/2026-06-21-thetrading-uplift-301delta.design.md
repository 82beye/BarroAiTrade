---
tags: [design, feature/thetrading-uplift, corpus/301, status/draft]
---

# 더트레이딩 방법론 접목 — 풀코퍼스(301편) 갭 분석·설계

> **연관 분석**: [[../../03-analysis/2026-06-21-thetrading-methodology-extract-v2-301편|방법론 추출 v2(301편 델타)]]
> **선행 설계**: [[2026-06-17-thetrading-methodology-uplift.design|99편 분석·설계]] · **선행 증분**: increment1(closing_bet 스캐폴드) + 증분2(비용정정·trap·regime·net-aware, main `8cbf83a`)
>
> **Summary**: 301편 풀코퍼스 델타를 **현재 main(2026-06-21)** 코드와 대조해 잔여 갭을 재정의하고, 종가베팅 **백테스트 실측**으로 라이브 활성 가부를 판정한다. 시그니처(종베·리더 선정·분봉유입) 중심으로 (c)안전·무영향 항목을 구현하고, 실거래 변경(d)은 근거와 함께 HITL로 제안한다.
> **Project**: BarroAiTrade · **Date**: 2026-06-21 · **Status**: Draft (구현은 §6 분류에 따름)
> **Scope note**: 실거래 숫자 파라미터 변경은 AskUserQuestion 승인 + `barrotrade-code-surgeon` 위임 후에만. 본 증분 코드는 전부 default-OFF/shadow/inert.

---

## 0. 배경 — 무엇이 이미 됐고, 무엇이 새로 열렸나

**선행으로 처리됨(2026-06-21 main)**: 비용모델 정정(`COMMISSION_RATE` 0.00175→**0.0035** 라이브, fill_audit 298행 재도출), trap_guard·regime_exit·net-aware TP(default-OFF), P0-1 진입가위치 **shadow** 갭가드, 측정도구 2종, 테스트 1496 통과.

**본 증분이 새로 여는 것**: ① 분석 표본을 99→**301편**으로 3배 확장해 임계를 시대에 맞게 갱신(R7 조 단위, R9 NXT, R6 몸통 신고가, R5 top5 강제컷). ② 시그니처 **종가베팅을 실측 백테스트로 검증**해 라이브 가부를 데이터로 판정. ③ 종베/선정의 잔여 게이트를 (c)shadow로 선설치.

---

## 1. 핵심 발견 — 종가베팅 백테스트 (실측, 비용이 구속제약)

`scripts/backtest_closing_bet_intraday.py` (리더 600종목, 5분봉 보유, 19,700 stock-day, 2026-06-21 재실행):

| 변형 | 트립 | 승률% | gross%/트립 | net@0.55% | **net@0.90%(정정비용)** |
|------|-----:|------:|-----------:|---------:|----------------------:|
| baseline (신고가+장대양봉) | 432 | 55.8 | +1.007 | +0.457 | **+0.107** |
| +money_flow (분봉유입) | 264 | 55.3 | +0.997 | +0.447 | +0.097 |
| +zone (골드존 0.5~0.618) | 53 | 54.7 | +0.880 | +0.330 | **−0.020** |
| +both | 35 | 48.6 | +0.369 | −0.181 | **−0.531** |

**해석(설계의 중심 결론)**:
1. **비용이 구속 제약**. 정정 비용(왕복 ~0.90%)에서 baseline net은 gross의 **약 11%만 잔존**(+0.107%/트립). 채널의 "+4~5% 슈팅" 화법은 베스트 케이스이고, 표본 평균 net edge는 매우 얇다.
2. **게이트를 더 걸수록 net이 악화**(zone −0.020, both −0.531). 표본 축소폭이 per-trade edge 개선을 초과 → "정교한 게이트 = 더 안전"이라는 직관이 **net 기준에서 실패**. 이는 델타 R7(비용·유동성)·D-R39(카지노 비중)와 정합.
3. → **종베 라이브 활성은 현재 요율에서 정당화되지 않음**. 전제조건: (a) 우대요율 협의로 왕복비용↓(`BARRO_COMMISSION_RATE`), 또는 (b) per-trade edge↑(예: 시황 게이트 D-P1·연기금 수급 D-R27로 표본을 줄이되 승률을 끌어올림 — 측정 필요).

### 1.1 두 시뮬의 괴리 — 핵심 경고 신호
OOS 관문(`_oos_validation.py --n 60 --seed 42`, 일봉·3분할·실비용)은 **closing_bet도 포함**하며 결과:

| 전략 | active | trades | win% | avg_ret% | holdout% | 판정 |
|------|-------:|-------:|-----:|---------:|---------:|------|
| f_zone | 33 | 54 | 39 | +1.904 | +3.903 | **PASS** |
| sf_zone | 14 | 18 | 44 | +0.353 | n/a | **FAIL**(active<15·trades<30·drop1 반전) |
| gold_zone | 54 | 381 | 39 | +1.620 | +1.101 | **PASS** |
| closing_bet | 50 | 254 | 43 | +3.553 | +4.816 | **PASS** |

→ **모순처럼 보이는 두 결과가 핵심**이다: 일봉·3분할 OOS는 종베를 **+3.553%(PASS)**로 후하게 보지만, 현실적 **5분봉 인트라데이 ablation은 net +0.107%/트립**으로 얇다. 차이의 원인 = 일봉 sim은 오버나잇 전략의 장중 슬리피지·체결·갭 가정을 못 잡아 **낙관 편향**(기존 문서의 "sim-live 괴리"와 동일). **결론: 단일 시뮬 PASS를 신뢰하지 말고 두 sim을 정합시킨 뒤 라이브 판단.** sf_zone OOS FAIL(표본 부족·outlier 의존)도 별도 주의.

---

## 2. 추적성 매트릭스 갱신 (델타 → 현재 코드)

선행 §3(R1~R13)에 풀코퍼스 임계와 신규 규칙을 반영. 상태는 2026-06-21 main 기준.

| # | 규칙(갱신) | 코드 매핑 | 상태 | 잔여 갭 |
|---|-----------|----------|------|---------|
| R5 | 거래대금 **5위 강제컷** | `kiwoom_native_rank.py` 점수 top_n | partial | rank≤5 hard-cut 인자 **(d)** |
| R6 | **몸통(종가) 신고가** + 3요소 | leader meta 신고가 필드 | **missing** | 종가기준 `is_new_high` 계산(무영향) **(c)** |
| R7 | 거래대금 **조 단위**/시총≥1000억 | 선정 임계·`closing_bet.min_trade_value` | partial(300억 가정) | 임계 갱신 **(d)** |
| R9 | 종베 **KRX+NXT 이원화** | `ClosingBetParams` 시간창 | scaffold OFF, NXT 미반영 | 시간창 확장 **(d)** |
| R10 | 종베 **3일차 한도** | `STRATEGY_EXIT_PROFILES["closing_bet"]` min1/max3 | inert 존재 | 정합(b) |
| R4 | 분봉유입 9~10매도/14:30~매수 | `closing_bet.require_money_flow` OFF | partial | shadow 측정 **(c)** |
| D-R14 | 잔존 기대수익 게이트 | P0-1 shadow(진입가 위치) | shadow 존재 | 지표 추가 **(c)** |
| D-R24 | 단기과열예고 + 투자경고 2종 | 없음 | **missing** | 종베 전 필터(관측) **(c)** |
| D-R29/30 | 1분봉≥15억 / 거래량≥300% | 없음 | **missing** | 유동성 shadow **(c)** |
| D-R25/26 | 프로그램비율·거래원 배제 | 없음 | **missing** | 데이터 플러밍 후 **(c)→(d)** |
| D-R36 | 음봉 종베(하락장) | `ClosingBetParams.enable_down_mode` OFF | 토글 존재 | 활성 **(d)** |

---

## 3. 본 증분 설계 — (c) 안전 구현 범위 (default-OFF/shadow/inert)

라이브 무영향(env=0 byte-identical). 각 항목 단위테스트 + 전체 회귀.

### 3.1 종베 사전필터 관측 모듈 — `closing_bet_filters.py` (신규, 순수함수)
델타의 정량 게이트를 **계산·판정만 하는 순수 함수**로 구현(차단 X). closing_bet/선정이 향후 (d)로 켤 때 재사용.
- `overheat_warning(daily_candles, prev5_close)` — D-R24: `종가 > 5일전 종가 × 1.6` 여부.
- `liquidity_ok(min1_value, day_value_vs_prev)` — D-R29/30: 1분봉 ≥15억 ∧ 당일거래량 ≥ 전일×3.
- `body_new_high(daily_candles, lookback=60)` — R6: **종가(몸통) 기준** 신고가(꼬리 제외).
- `remaining_upside_ratio(intraday)` — D-R14: `(목표고점-현재)/(목표고점-기준저점)`; 게이트 임계는 호출측 env.
- 전부 반환만 하고 부작용 없음. 기존 `indicators.py` 패턴 계승.

### 3.2 closing_bet 백테스트 하니스 정착 — (이미 실행, 산출 고정)
`backtest_closing_bet_intraday.py` 결과(§1)를 리포트에 고정 인용. 신규 코드 아님(측정).

### 3.3 (선택) leader 신고가 관측 — `kiwoom_native_rank` shadow 필드
LeaderCandidate에 `body_new_high`(R6) **계산 필드만** 추가(선정 점수·컷 무변경, 로그/메타로만 노출). env `BARRO_LEADER_NEWHIGH_SHADOW=0` 기본.

> 3.1·3.3 모두 "계산하고 기록"만 한다. 진입/청산/선정 동작은 한 글자도 바뀌지 않는다(테스트로 parity 단언).

---

## 4. (d) HITL 제안 — 실거래 변경 (구현 금지, 근거 첨부)

AskUserQuestion 승인 + code-surgeon 위임 후에만. 각 항목 백테스트 가부·OOS 게이트 명시.

| 제안 | 근거 | 백테스트 | 권고 |
|------|------|---------|------|
| **종베 라이브 활성 보류** | §1: net@0.90 +0.107%/트립, 게이트 추가 시 음전 | ✓(ablation) | **요율 인하 또는 edge 상향 전 비활성 유지** |
| 우대요율 협의 → `BARRO_COMMISSION_RATE` 하향 | net의 1순위 레버(비용 89% 잠식) | ✓ | ops 액션(코드 아님) |
| R7 선정 임계 조 단위/시총≥1000억 | 델타 R7, 2026 대형주 중심 | ⚠️(과거 순위 메타) | 선정 시뮬 후 |
| R5 top5 hard-cut | "5위 밖=주도주 아님" | ⚠️ | 유니버스 변경, shadow 후 |
| R9 종베 NXT 시간창 이원화 | 델타 R9 구조변화 | ⚠️(NXT 체결 데이터) | dry_run 필수 |
| D-R36 음봉 종베 활성 | 하락장 전용 토글 | ⚠️ | OOS 후 |

---

## 5. 검증 & HITL

- **회귀**: `venv/bin/python -m pytest backend/tests/ -q` → 기존 1496 + 신규 유지·0 failed.
- **무영향 parity**: 신규 필터 모듈은 호출처가 없으면 동작 0; shadow 필드는 env=0 시 미계산/미로그. parity 단위테스트 포함.
- **종베 근거**: §1 ablation json(`docs/04-report/features/2026-06-18-closing-bet-intraday-ablation.json`) + 재실행 일치.
- **HITL 게이트**: ①종베 라이브 ②선정 임계(R5/R7) ③NXT 시간창 ④음봉종베 — 전부 인간 승인 필수, 자동 금지.

## 6. 다음 단계
1. (c) `closing_bet_filters.py` + 테스트 → 커밋(푸시 HITL).
2. shadow 측정 N일 누적(단기과열·유동성·신고가 분포) → 종베 edge 개선 효과 측정.
3. 요율 협의 결과 반영 후 종베 net 재평가 → OOS → (d) 라이브 결정.

> 본 증분은 `feat/thetrading-uplift-301delta` 브랜치. 측정·환류는 `barro-trade-review` 매매복기 사이클로.
