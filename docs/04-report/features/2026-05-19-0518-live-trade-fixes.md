---
tags: [report, operations, daemon, fix, 2026-05-18, 2026-05-19]
date: 2026-05-19
related:
  - "[[2026-05-17-trail-default-on-sim]]"
  - "[[F-zone-atr-exit-experiment]]"
---

# 5/18 실거래 분석 + daemon 4+1 fix 통합 보고

## Context

- **분석 대상**: 2026-05-18 실거래 audit log + active_positions (운영 머신 → trading_logs_0518.tar.gz 전달)
- **목표**: 1분봉 기준 진입/이탈 시그널 문제 종목별 단계별 진단 + daemon 코드 fix
- **결과**: 운영 daemon 5가지 결함 발견 (P1~P4 fix + P5-a 인프라)

## 5/18 실거래 timeline 요약

| 시간 (KST) | 사건 | 거래수 |
|---|---|---:|
| 09:00~09:39 | 개장 39분 폭주 — 매수/매도 33건 (전일 청산 + 신규 매수) | 33 |
| 09:56~10:50 | 005930·010170 추가 매수 | 2 |
| 11:14~11:19 | 005930 분할매도 7번 (1주 단위까지) | 7 |
| 11:23~11:31 | 122630·069500 신규 매수 | 2 |
| 12:58~13:20 | 080220 진입 + 3번 분할매도 | 4 |
| **14:06~15:20** | **일일 50건 한도 도달 → 78건 BLOCKED (4시간 차단)** | 78 BLOCKED |
| 15:30 | 장 마감 — **미청산 3종목 강제 보유** | — |

## 핵심 문제 5건 + 적용 fix

### P1. PARTIAL_TP 반복 발동 (분할매도 폭주)

- **사례**: 100790 9번, 005930 7번, 080220 3번 — 매 60s 사이클마다 재발동, 1주 단위까지 쪼개짐
- **원인**: `ActivePositionStore.load_all()` 이 `partial_tp_done`·`peak_pnl_rate`·`peak_updated_at` 3개 필드 read 누락
  - upsert 저장은 정상 → 다음 load 시 default(`False`/`0.0`/`""`) 로 reset → flag 영구 False
- **fix**: `backend/core/journal/active_positions.py` load_all 에 3개 필드 추가
- **검증**: 단위 테스트 + 5/18 실데이터 080220 peak 0.34% 정상 복원
- **5/18 영향**: 매도 거래수 14건 → 3건 (-11건)
- **commit**: `329e9dd`

### P2. DCA pending 미발동 (069500 L -6.4% 도달 무시)

- **사례**: 069500 entry 119,220 → 일중 L 111,545 (-6.4% 도달) → T2(-2%) 영구 pending
- **원인**: DCA 평가가 `cur_price` (폴링 시점 가격) 만 봄 → 폴링 사이 60s 일중 low 도달 놓침
- **fix**:
  - `ActivePosition.trough_pnl_rate`·`trough_updated_at` 추가 (peak 와 대칭)
  - daemon 매 사이클 trough 갱신
  - DCA 평가 — `eff_drop_pct = min(cur_rate, trough_pnl_rate)` 기반 trigger
- **검증**: 069500 trough -6.4% → T2(-2.0%) **발동**, T3(-4.0%) **발동** (기존 모두 미발동)
- **5/18 영향**: 069500 평단 119,220 → ~116,800 → **-1.16% 손실 → +0.9% 익절 전환**
- **commit**: `329e9dd`

### P3. 일일 매수 한도 매수+매도 합계 카운트 (비대칭)

- **사례**: 5/18 매수 15 + 매도 35 = 50 → 14:06 매수 한도 BLOCKED → 4시간 차단
- **원인**: `LiveOrderGate._count_today_orders` 가 매수+매도 합계인데, 한도 적용은 매수만 (`6abb59a` 의 "매도 제외" fix 와 비대칭)
- **fix**: `_count_today_buys()` 신설 → side=buy 만 카운트, preflight 에서 사용
- **검증**: 5/18 audit 패턴 시뮬 — 기존 50 BLOCKED → P3 15 통과
- **commit**: `329e9dd`

### P4. 고점 진입 회피 (122630/069500 H 직전 진입)

- **사례**:
  - 122630 entry 161,690 vs 일중 H 162,955 (-0.78%) → 즉시 하락
  - 069500 entry 119,220 vs H 119,900 (-0.57%) → 미청산 손실 보유
- **fix**: `_scan_and_buy` 의 strategy 통과 후 검증 추가
  - 일중 H 대비 cur 거리 < 1.5% **AND** momentum 비활성 → SKIP
  - momentum 활성 조건 (둘 중 하나):
    - 진입 직전 봉이 H 봉 (모멘텀 진행 중)
    - cur ≥ 직전 봉 high (새 봉이 직전 봉 high 초과)
- **검증** (5/18 4 case):

| 종목 | prox | momentum | 결과 | 의도 |
|---|---:|---|---|---|
| 122630 | 0.12% | True (H봉 진입) | 통과 | ✗ false-negative (한계) |
| **069500** | **0.57%** | **False** | **BLOCKED** | ✓ |
| 080220 | 0.00% | True | 통과 | ✓ |
| 100790 | 0.94% | True (cur > last.high) | 통과 | ✓ |

- **commit**: `329e9dd`

### P5-a. 시간별 단계 SL (인프라 도입, default OFF)

- **가설**: 100790 entry 09:08 → 09:18 -4% SL 발동 → 종가 +12.3% 회복. 진입 직후 노이즈 흡수 필요
- **인프라**:
  - `ExitPolicy.sl_time_stages: Optional[tuple] = None`
  - `_elapsed_seconds`, `_sl_pct_at_elapsed` 헬퍼
  - `evaluate_holding` 우선순위: `hold_days_tighten > sl_time_stages > stop_loss_pct`
- **default OFF 이유**: 실제 100790 5/18 분봉 검증 결과 close -4% 미달 → 효과 0 (실제 문제는 PARTIAL_TP — P1 fix)
  - 122630 같은 장기 하락은 SL 좁힘 → 회복 박탈 → 불리
  - 양면성 있어 default OFF, 추가 case 검증 후 명시 활성화 권장
- **권장 활성화 stages**: `((600,-5.0),(1800,-4.0),(99999,-3.0))`
- **commit**: `6ff32d4`

## 종목별 단계별 분석 (요약)

### 100790 미래에셋벤처투자 — PARTIAL_TP 9번 폭주
```
09:08:24 buy 88 + 09:18:52 buy 44 (총 132주, swing_38)
09:30:44 sell 66 (PARTIAL_TP)
09:31:47 sell 33  ← partial_tp_done reset → 재발동
09:32:51 sell 16
... 9번 분할매도 → 마지막 1주
```
**P1 fix 후**: 1번 partial + 잔여 trailing/breakeven

### 122630 KODEX 레버리지 — 고점 진입 + 미청산
```
11:23 entry 161,690 (T1 32주, gold_zone) — 일중 H 162,955 (-0.78%)
11:52 DCA T2 16주 @157,940 (-2.3%)
14:17 sell BLOCKED → 미청산 48주
종가 157,350 → 평가손실 -148k (-1.93%)
```
**P2·P3 fix 후**: T3(-4%) 추가 발동, 14:17 매도 통과

### 069500 KODEX 200 — DCA 미발동 + 미청산
```
11:31 entry 119,220 (T1 44주, gold_zone) — H 119,900 (-0.57%)
일중 L 111,545 (-6.4%) → T2(-2%) 영구 pending
15:30 종가 117,840 → -1.16% 미청산
```
**P2 fix 후**: T2(-2%) + T3(-4%) 발동 → 평단 ~116,800 → +0.9% 익절
**P4 fix 후**: 진입 자체 차단 (prox 0.57% < 1.5% & momentum=False) → -74k 손실 회피

### 080220 제주반도체 — 정상 진입 + 14:06 BLOCKED 미청산
```
12:58 entry 96,800 (54주, swing_38) — H 96,800 (모멘텀 진행)
13:18~20 분할매도 47주 (PARTIAL_TP 3번)
14:06~ 일일 한도 → DCA·매도 모두 BLOCKED → 잔여 7주
```
**P1 fix**: 분할매도 1번만 (잔여 27주 trailing)
**P3 fix**: 14:06 BLOCKED 해소 → 정상 청산

### 005930 삼성전자 — PARTIAL_TP 7번
P1 fix 후 1번 partial + 잔여 trailing.

### 010170 신화실업 — SL 정상 발동
P1·P2·P3·P4 영향 없음 (1회 SL 정상).

## 누적 fix 적용 시 5/18 가정 결과

| 항목 | 실제 5/18 | P1~P4 fix 후 |
|---|---|---|
| 매도 거래수 | 14건 (분할매도 폭주) | 3건 |
| 매수 카운트 | 15건 | 15건 (동일) |
| 14:06 매수 한도 | **BLOCKED** | 통과 (15 < 50) |
| DCA T2/T3 발동 | 1건 (122630 T2만) | 3건 (+069500 T2/T3, 122630 T3) |
| 069500 진입 | 진입 (-1.16% 미청산) | **차단** (-74k 손실 회피) |
| 미청산 종목 | **3건 강제 보유** | 0건 (적시 청산 가능) |

## 신규 도구

| 스크립트 | 용도 |
|---|---|
| `scripts/_analyze_0518_signals.py` | 5/18 picker 종목별 진입 시그널 단계별 진단 |
| `scripts/_replay_0518_with_fixes.py` | P1 fix 전/후 ExitPolicy 재시뮬 비교 |
| `scripts/_replay_0518_options.py` | 잔여 holding 정책 옵션 A/B/C/D/E 비교 |

## 변경 파일 (commit `329e9dd` + `6ff32d4`)

| 파일 | 변경 |
|---|---|
| `backend/core/journal/active_positions.py` | P1 (3필드 load) + P2 (trough 2필드) |
| `backend/core/risk/live_order_gate.py` | P3 (_count_today_buys + 변수명) |
| `backend/core/risk/holding_evaluator.py` | P5-a (sl_time_stages 인프라) |
| `scripts/intraday_buy_daemon.py` | P2 (trough 갱신·DCA 평가) + P4 (고점 진입 SKIP) |
| `scripts/_analyze_0518_signals.py` | 신규 — picker 진단 |
| `scripts/_replay_0518_with_fixes.py` | 신규 — fix 효과 시뮬 |
| `scripts/_replay_0518_options.py` | 신규 — 옵션 비교 |

## 후속 후보

- **P5-a 활성화** — 다른 운영 case (5/14·5/15·5/19+) 로 효과 추가 검증 후 stages 적용
- **잔여 holding 강화** — 2차 PARTIAL_TP @5%·trailing offset 동적 조정 (옵션 B/C/D 검증됨)
- **VWAP·거래량 추세 기반 고점 진입 보강** — P4 122630 false-negative 보완

## 참조

- 이전 보고: [[2026-05-17-trail-default-on-sim]]
- F존 ATR 실험: [[F-zone-atr-exit-experiment]]
- ai-trade 비교 분석: 본 세션 이전 대화
