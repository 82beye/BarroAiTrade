---
tags: [plan, feature/bar-52, status/in_progress, phase/2, area/data]
template: plan
version: 1.0
---

# BAR-52 Exchange/TradingSession + MarketSessionService Plan 🎯

> **Project**: BarroAiTrade / **Feature**: BAR-52 / **Phase**: 2 — **첫 티켓**
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v2#Phase 2]]
> **Date**: 2026-05-06 / **Status**: In Progress

---

## 1. Overview

### 1.1 Purpose

PRD 의 핵심 차별점인 **08:00–20:00 통합 거래 환경** 의 *시간 인지* 시스템을 시동한다.

- `Exchange` enum (KRX/NXT/COMPOSITE)
- `TradingSession` enum (8 단계)
- `MarketSessionService` — 시각·날짜·휴장일 보고 현재 세션·가용 거래소 판단
- `AnalysisContext.trading_session` 의 BAR-45 forward ref(`Any`) 를 *정식 type* 으로 해소

### 1.2 Background

- 마스터 플랜 v2 §2 Phase 2 첫 티켓
- BAR-45 design §1.4 placeholder 5건 중 1건 해소 (trading_session)
- 후속 BAR-53 (NxtGateway) 가 본 BAR-52 의 세션 정보 필요

### 1.3 Related

- [[../MASTER-EXECUTION-PLAN-v2]]
- BAR-45 placeholder: `AnalysisContext.trading_session: Any  # TODO(BAR-52)`
- 거래시간 표 (PRD §3): 08:00–20:00 통합 환경

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/models/market.py` 확장:
  - `Exchange(str, Enum)`: KRX, NXT, COMPOSITE
  - `TradingSession(str, Enum)`: NXT_PRE, KRX_PRE, REGULAR, KRX_CLOSING_AUCTION, INTERLUDE, KRX_AFTER, NXT_AFTER, CLOSED
- [ ] `backend/core/market_session/__init__.py`, `service.py` 신규
- [ ] `MarketSessionService` API:
  - `get_session(now: datetime) -> TradingSession`
  - `is_holiday(date) -> bool` (휴장일 캘린더, 초기 빈 set + 사용자 추가 가능)
  - `available_exchanges(session) -> list[Exchange]`
  - `available_orders(session) -> dict` (시장가/지정가/시간외 등)
- [ ] `AnalysisContext.trading_session` forward ref 해소 → `Optional[TradingSession]`
- [ ] `tests/market_session/` 신규 디렉터리 + 24+ 시나리오
- [ ] BAR-44 베이스라인 회귀 ±5%

### 2.2 Out of Scope

- ❌ NxtGateway 시세 수신 — BAR-53
- ❌ CompositeOrderBook — BAR-54
- ❌ SOR 라우팅 — BAR-55
- ❌ 휴장일 *자동 수집* (한국거래소 IR API 등) — 초기는 수동 등록만, 후속 BAR-61 캘린더 통합

---

## 3. Requirements

### 3.1 FR

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | Exchange enum (KRX/NXT/COMPOSITE) | High |
| FR-02 | TradingSession enum (8 단계) | High |
| FR-03 | get_session(now) — 시각·요일·휴장일 분기 | High |
| FR-04 | is_holiday(date) — set 기반 (초기 빈, add_holiday 메서드) | Medium |
| FR-05 | available_exchanges(session) — 세션 → list[Exchange] | High |
| FR-06 | available_orders(session) — 시장가/지정가/시간외 가용성 | Medium |
| FR-07 | AnalysisContext.trading_session 정식 type | High |
| FR-08 | BAR-44 베이스라인 회귀 ±5% | High |

### 3.2 NFR

| Category | 기준 |
|---|---|
| 회귀 | BAR-40~50 무영향 |
| 성능 | get_session ≤ 1ms |
| 커버리지 | service.py ≥ 80% |

---

## 4. Success Criteria

### 4.1 DoD

- [ ] enum 2종 + service 신규
- [ ] AnalysisContext placeholder 해소
- [ ] 24+ 테스트 통과
- [ ] BAR-44 베이스라인 회귀
- [ ] BAR-40~50 회귀 무영향

### 4.2 24+ 시나리오 매트릭스

| 시각 | 요일 | 휴장 | 기대 세션 | 가용 거래소 |
|---|---|:---:|---|---|
| 07:30 | 평일 | × | CLOSED | [] |
| 08:00 | 평일 | × | NXT_PRE | [NXT] |
| 08:30 | 평일 | × | KRX_PRE | [KRX, NXT] |
| 08:50 | 평일 | × | KRX_PRE | [KRX, NXT] |
| 09:00 | 평일 | × | REGULAR | [KRX, NXT] (NXT 09:00:30 시작) |
| 12:00 | 평일 | × | REGULAR | [KRX, NXT] |
| 15:20 | 평일 | × | KRX_CLOSING_AUCTION | [KRX] |
| 15:30 | 평일 | × | INTERLUDE | [] |
| 15:35 | 평일 | × | INTERLUDE | [] |
| 15:40 | 평일 | × | KRX_AFTER | [KRX, NXT] |
| 17:00 | 평일 | × | KRX_AFTER | [KRX, NXT] |
| 18:00 | 평일 | × | NXT_AFTER | [NXT] (KRX_AFTER 종료) |
| 19:00 | 평일 | × | NXT_AFTER | [NXT] |
| 20:00 | 평일 | × | CLOSED | [] |
| 22:00 | 평일 | × | CLOSED | [] |
| 토 12:00 | 토 | × | CLOSED | [] |
| 일 12:00 | 일 | × | CLOSED | [] |
| 휴장일 12:00 | 평일 | ✓ | CLOSED | [] |
| (+ 6 기타 경계) | | | | |

---

## 5. Architecture

### 5.1 TradingSession 시간표

```
07:00 ────────────────────────── CLOSED
08:00 ─┬─ NXT_PRE ────┐
08:30  │              KRX_PRE ──┐
08:50  └─ NXT_PRE 종료 │         │
09:00 ─────────────── REGULAR (KRX 09:00, NXT 09:00:30)
       │
15:20 ────────────────── KRX_CLOSING_AUCTION (단일가, NXT 종료)
15:30 ────────────────── INTERLUDE
15:40 ─┬─ KRX_AFTER ───┐
       │               NXT_AFTER (블루오션) ────┐
18:00  └─ KRX_AFTER 종료 │                       │
20:00 ──────────────────  └ NXT_AFTER 종료 ───── CLOSED
```

### 5.2 우선순위 정책 (겹침 시)

| 시간대 | 정책 |
|---|---|
| 08:30~08:50 | NXT_PRE + KRX_PRE 겹침 → **KRX_PRE 우선** (KRX 가 메인 시장) |
| 15:40~18:00 | KRX_AFTER + NXT_AFTER 겹침 → **KRX_AFTER 우선** (NXT 18:00 이후만 단독) |

### 5.3 Module Layout

```
backend/models/market.py             (확장: Exchange, TradingSession)
backend/core/market_session/
├── __init__.py                       (신규)
└── service.py                        (신규, MarketSessionService)
backend/models/strategy.py            (수정: trading_session forward ref → TradingSession)
backend/tests/market_session/
├── __init__.py
├── conftest.py                       (시각 fixture, 휴장일 fixture)
└── test_service.py                   (24+ 시나리오)
```

---

## 6. Risks

| Risk | Mitigation |
|------|------------|
| 시간대 우선순위 정책 부적절 | §5.2 명시. 후속 maintenance 시 조정 |
| 휴장일 자동 수집 부재 | set 수동 등록 + BAR-61 캘린더 통합 시 자동 |
| AnalysisContext 변경으로 BAR-45~50 회귀 | placeholder forward ref 해소만 — 기존 None 호환 유지 |
| 시각 timezone 정책 | KST (Asia/Seoul) 강제, datetime aware |

---

## 7. Convention Prerequisites

- ✅ Phase 0~1 인프라
- ❌ tests/market_session/ 부재 → 본 티켓에서 시동

---

## 8. Implementation Outline (D1~D6)

1. D1 — market.py 확장 (Exchange, TradingSession enum)
2. D2 — market_session/service.py 신규 (MarketSessionService)
3. D3 — AnalysisContext.trading_session 정식 type
4. D4 — tests/market_session/test_service.py 24+ 시나리오
5. D5 — V1~V6 (특히 BAR-44 베이스라인)
6. D6 — PR

---

## 9. Next

- BAR-53 NxtGateway 1차 (시세 read-only)

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 plan — Phase 2 첫 티켓, 8 세션 + 시간대 매트릭스 24+ |
