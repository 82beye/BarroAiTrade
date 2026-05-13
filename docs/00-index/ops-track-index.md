---
tags: [index, ops, runbook]
---

# OPS 트랙 인덱스 — 운영 자동화 26 BAR

> Master Plan v2 (BAR-40~79) 완료 후, 실 운영 (mockapi.kiwoom.com) + 자기학습 자동화로 확장된 OPS 트랙.
> 작업 순서대로 정리. 각 BAR 의 완성도·실 검증·리포트 링크 포함.

---

## 진행 요약

| 지표 | 시작 | 현재 |
|------|------|------|
| BAR | 0 | **26** |
| Tests | 240 | **830 passed** (+590, 3.5×) |
| 키움 API | 0 | **11 TR-ID** |
| Telegram 명령 | 0 | **19 명령** |
| 회귀 fail | 0 | **0** (flaky 1건 단독 통과) |

---

## Layer 1 — 인증·기본 인프라 (OPS-01~07)

| BAR | Title | 핵심 산출 | Report |
|-----|-------|-----------|--------|
| OPS-01 | auth routes + 미들웨어 | JWT login/refresh + alerts.yaml | [[../04-report/features/BAR-OPS-01-report]] |
| OPS-02 | User + bcrypt + alembic 0007 | sha256 prehash + User 모델 | [[../04-report/features/BAR-OPS-02-report]] |
| OPS-03 | LiveTradingOrchestrator | 전략 다중 실행 dispatcher | [[../04-report/features/BAR-OPS-03-report]] |
| OPS-04 | OrderExecutor 정식 + RUNBOOK | KIS API 표준 매수/매도 | [[../04-report/features/BAR-OPS-04-report]] |
| OPS-05 | LiveTradingChecker + DEPLOYMENT | health check + 자가 진단 | [[../04-report/features/BAR-OPS-05-report]] |
| OPS-06 | KiwoomOAuth2 + LiveOrderExecutor | KIS 호환 OAuth2 + 주문 | [[../04-report/features/BAR-OPS-06-report]] |
| OPS-07 | Semgrep + Bandit + PenTestSuite | 모의 침투 자동화 | [[../04-report/features/BAR-OPS-07-report]] |

---

## Layer 2 — 시뮬·전략 (OPS-08~09)

| BAR | Title | 핵심 산출 | Report |
|-----|-------|-----------|--------|
| OPS-08 | IntradaySimulator | 슬라이딩 윈도우 5 전략 + ExitEngine | [[../04-report/features/BAR-OPS-08-report]] |
| OPS-09 | KiwoomCandleFetcher (KIS) | 일봉/분봉 KIS API | [[../04-report/features/BAR-OPS-09-report]] |
| OPS-09+ | 전략 시그널·청산 정책 분리 (2026-05-14) | scalping_consensus provider · LiveOrderGate qty 차단 · 전략별 exit_plan 분리 · f_zone 변동성 필터 · 명목 가치 진입 (S1) | [[../04-report/features/BAR-OPS-09-strategy-refactor-2026-05-14.report]] |

---

## Layer 3 — 키움 자체 OpenAPI (OPS-10~12)

> ⚠️ 핵심 발견: 사용자 키는 **키움 자체 OpenAPI** (api.kiwoom.com) 형식. KIS API 와 완전히 다른 스펙. 별도 어댑터 필요.

| BAR | Title | TR-ID | Report |
|-----|-------|-------|--------|
| OPS-10 | KiwoomNativeOAuth + Candles | oauth2/token, ka10081, ka10080 | [[../04-report/features/BAR-OPS-10-report]] |
| OPS-11 | LeaderPicker 2-factor | ka10032 (거래대금), ka10027 (등락률) | [[../04-report/features/BAR-OPS-11-report]] |
| OPS-12 | LeaderPicker 3-factor + min_score | + ka10030 (거래량) | [[../04-report/features/BAR-OPS-12-report]] |

---

## Layer 4 — 영속·정책·게이트 (OPS-13~17)

| BAR | Title | 핵심 산출 | Report |
|-----|-------|-----------|--------|
| OPS-13 | 시뮬 결과 CSV 영속 + history | simulation_log.csv + summarize | [[../04-report/features/BAR-OPS-13-report]] |
| OPS-14 | 키움 자체 주문 어댑터 | kt10000 (매수), kt10001 (매도) + DRY_RUN | [[../04-report/features/BAR-OPS-14-report]] |
| OPS-15 | 잔고/예수금 조회 | kt00018, kt00001 | [[../04-report/features/BAR-OPS-15-report]] |
| OPS-16 | 자금 한도 + 추천 매수 qty | balance_gate (per 30%, total 90%) | [[../04-report/features/BAR-OPS-16-report]] |
| OPS-17 | LiveOrderGate 4중 안전 | env flag + 일일 손실/거래수 + audit | [[../04-report/features/BAR-OPS-17-report]] |

---

## Layer 5 — End-to-End + 매도 (OPS-18~20)

| BAR | Title | 핵심 산출 | Report |
|-----|-------|-----------|--------|
| OPS-18 | simulate_leaders --execute E2E | 한 명령 시뮬→추천→실행 | [[../04-report/features/BAR-OPS-18-report]] |
| OPS-19 | 일일 markdown 리포트 | render_daily_report 5 섹션 | [[../04-report/features/BAR-OPS-19-report]] |
| OPS-20 | 매도 시그널 + auto-sell | ExitPolicy TP +5% / SL -2% | [[../04-report/features/BAR-OPS-20-report]] |

---

## Layer 6 — Telegram (OPS-21~25)

| BAR | Title | 핵심 산출 | Report |
|-----|-------|-----------|--------|
| OPS-21 | TelegramNotifier (5 alerts) | 시뮬·매수·매도·시뮬 요약 | [[../04-report/features/BAR-OPS-21-report]] |
| OPS-22 | LiveOrderGate 차단 알림 | BLOCKED/FAILED 즉시 텔레그램 | [[../04-report/features/BAR-OPS-22-report]] |
| OPS-23 | 일일 리포트 텔레그램 전송 | send_chunks (4096 char 분할) | [[../04-report/features/BAR-OPS-23-report]] |
| OPS-24 | 양방향 봇 (4 명령) | /help /ping /balance /history | [[../04-report/features/BAR-OPS-24-report]] |
| OPS-25 | 명령 확장 (3 추가) | /sim /eval /audit | [[../04-report/features/BAR-OPS-25-report]] |

---

## Layer 7 — Confirm 패턴 (OPS-26~27)

| BAR | Title | 핵심 산출 | Report |
|-----|-------|-----------|--------|
| OPS-26 | 매수 confirm 패턴 | OrderConfirmStore + /sim_execute → /confirm | [[../04-report/features/BAR-OPS-26-report]] |
| OPS-27 | 매도 confirm 패턴 | side="sell" 검증 + /sell_execute → /confirm_sell | [[../04-report/features/BAR-OPS-27-report]] |

→ 7중 보안 layer: whitelist + token + TTL + 일회용 + chat_id + LiveOrderGate + ENV flag

---

## Layer 8 — 학습 루프 (OPS-28~32)

| BAR | Title | 핵심 산출 | Report |
|-----|-------|-----------|--------|
| OPS-28 | 실현손익 (ka10073) + /pnl | RealizedPnLEntry + 30일 집계 | [[../04-report/features/BAR-OPS-28-report]] |
| OPS-29 | 시뮬 vs 실현 PnL 비교 (/diff) | bias_counts (양호/과대/과소/신호없음) | [[../04-report/features/BAR-OPS-29-report]] |
| OPS-30 | 정책 자동 튜닝 (/tune) | min_score / SL / max_per_position 추천 | [[../04-report/features/BAR-OPS-30-report]] |
| OPS-31 | policy.json 영속 + /tune apply | history 50건 + 자동 반영 | [[../04-report/features/BAR-OPS-31-report]] |
| OPS-32 | CLI PolicyConfig 자동 로드 | simulate_leaders 옵션 default 자동 | [[../04-report/features/BAR-OPS-32-report]] |

→ **학습 루프 완성**: `/diff` → `/tune apply` → `policy.json` → 다음 시뮬 자동 반영

---

## Layer 9 — 미체결 + 정확도 (OPS-33~35)

| BAR | Title | 핵심 산출 | Report |
|-----|-------|-----------|--------|
| OPS-33 | 미체결 (kt00004) + /orders | OpenOrder + side 자동 분류 | [[../04-report/features/BAR-OPS-33-report]] |
| OPS-34 | 미체결 취소 (kt10003) + /cancel_order | DRY_RUN 모드 | [[../04-report/features/BAR-OPS-34-report]] |
| OPS-35 | 트레이딩뷰 등급 시뮬 정확도 | next_open + intrabar + fee/tax/slippage | [[../04-report/features/BAR-OPS-35-report]] |

→ 현실 보정: 현대차 600 일봉 +7.4M → +1.9M (-74.8%, 정확도 ↑)

---

## 사용 키움 API (11 TR-ID)

| 카테고리 | TR-ID | 용도 |
|---------|-------|------|
| 인증 | oauth2/token | 토큰 발급 |
| 시세 | ka10081 | 일봉 |
| 시세 | ka10080 | 분봉 |
| 순위 | ka10032 | 거래대금 상위 |
| 순위 | ka10027 | 등락률 상위 |
| 순위 | ka10030 | 거래량 상위 |
| 계좌 | kt00018 | 계좌평가 |
| 계좌 | kt00001 | 예수금 |
| 계좌 | ka10073 | 실현손익 |
| 계좌 | kt00004 | 미체결 |
| 주문 | kt10000 | 매수 |
| 주문 | kt10001 | 매도 |
| 주문 | kt10003 | 취소 |

---

## Telegram 명령 (19개)

| 카테고리 | 명령 |
|----------|------|
| 메타 | `/help` `/ping` |
| 조회 | `/balance` `/history` `/sim` `/eval` `/audit` `/pnl` `/diff` `/tune` `/policy` `/orders` |
| 매수 | `/sim_execute` → `/confirm <TOKEN>` |
| 매도 | `/sell_execute` → `/confirm_sell <TOKEN>` |
| 정책 | `/tune apply` |
| 취소 | `/cancel_order <ORD_NO> <SYMBOL> [<QTY>]` |
| 공통 | `/cancel` |

---

## 다음 액션

1. **보안 회전**: 키움 키 + Telegram 토큰 ([[../05-paperclip/security-rotation]])
2. **운영 시작**: cron 4건 + 봇 데몬 ([[../05-paperclip/runbook-ops]])
3. **1~2주 모의 검증** → 실전 host 결정

→ 시스템 흐름도: [[system-flow]]
