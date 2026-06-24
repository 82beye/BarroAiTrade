---
name: barrotrade
description: BarroTrade 멀티에이전트 트레이딩 사이클 오케스트레이션. mode(cycle/analyze/debate/consensus/risk/order/reflect/doctor/init)를 받아 controller가 Stage I~VII로 17 에이전트를 dispatch. ★실거래 송출 절대 금지(mock/HITL)★. BarroAiTrade(현 라이브)에 통합 스캐폴드.
---

# BarroTrade 오케스트레이션 스킬

`/barrotrade <mode> [args]` 호출 시 **barrotrade-controller** 에이전트를 Task로 띄워 사이클을 오케스트레이션한다.

## 사용
- `/barrotrade analyze 005930` — Stage I~III(거시·전략·신호) 분석만 (보유 에이전트로 동작)
- `/barrotrade debate 005930` — Bull/Bear 4라운드 토론(debate-moderator)
- `/barrotrade quick <signal>` — quick-decider 10초 GO/WAIT/NO-GO
- `/barrotrade reflect <cycle_id>` — self-reflector 사후 성찰
- `/barrotrade cycle 005930` — 전체 7-stage (★보유 11/17 에이전트라 일부 stage 미완 — 아래 갭 참조)
- `/barrotrade doctor` — pre-flight 무결성 점검

## 동작 (controller 위임)
호출 → barrotrade-controller(Task) → mode별 stage 시퀀스로 에이전트 dispatch →
산출물 검증·audit chain·circuit breaker → 보고. 상세는 .claude/agents/barrotrade-controller.md.

## ★안전 불변식★
- 실거래 주문 엔드포인트(/uapi/.../order-*) **절대 호출 금지** — 산출은 시뮬(70_order.simulated.json)·자문뿐.
- 현 시스템은 **mock(mockapi.kiwoom.com)·HITL** — 실제 매매는 기존 LiveOrderGate/사람 승인만.
- in-flight lock(workspace/.in-flight.json), 회로 차단기(일일손실), HITL 24h expire.

## ★현재 갭(zip 11/17 에이전트)★
- 보유(11): controller·macro-specialist·trend-expert·risk-manager·debate-moderator·quick-decider·portfolio-pm·signal-watcher·intraday-reporter·self-reflector·code-surgeon
- 누락(전체 cycle 차단): **data-preprocessor·fundamental-specialist·rag-analyst** (+ consensus/order/compliance 계열). 이들 추가 전엔 cycle의 Stage I(data)·II(fundamental/rag)·VI(consensus/order)가 미완 → analyze(부분)·debate·quick·reflect 모드 우선 사용.
- config/*.json 은 스캐폴드(아래) — 운영 임계는 튜닝 필요.
