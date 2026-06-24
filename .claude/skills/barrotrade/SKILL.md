---
name: barrotrade
description: BarroTrade 멀티에이전트 트레이딩 사이클 오케스트레이션. mode(cycle/analyze/debate/consensus/risk/order/reflect/doctor/init)를 받아 controller가 Stage I~VII로 17 에이전트를 dispatch. ★실거래 송출 절대 금지(mock/HITL)★. BarroAiTrade(현 라이브)에 통합 스캐폴드.
---

# BarroTrade 오케스트레이션 스킬

`/barrotrade <mode> [args]` 호출 시 **barrotrade-controller** 에이전트를 Task로 띄워 사이클을 오케스트레이션한다.

## 사용
- `/barrotrade analyze 005930` — Stage I~III: data(10)→macro/fundamental/rag(20/21/15)→trend(30) 분석
- `/barrotrade debate 005930` — bull/bear brief(40/41) → debate-moderator 4라운드 토론(50)
- `/barrotrade quick <signal>` — quick-decider 10초 GO/WAIT/NO-GO
- `/barrotrade reflect <cycle_id>` — self-reflector 사후 성찰(99 + semantic memory)
- `/barrotrade cycle 005930` — 전체 7-stage (17 에이전트 가동)
- `/barrotrade doctor` — pre-flight 무결성 점검

## 동작 (controller 위임)
호출 → barrotrade-controller(Task) → mode별 stage 시퀀스로 에이전트 dispatch →
산출물 검증·audit chain·circuit breaker → 보고. 상세는 .claude/agents/barrotrade-controller.md.

## ★안전 불변식★
- 실거래 주문 엔드포인트(/uapi/.../order-*) **절대 호출 금지** — 산출은 시뮬(70_order.simulated.json)·자문뿐.
- 현 시스템은 **mock(mockapi.kiwoom.com)·HITL** — 실제 매매는 기존 LiveOrderGate/사람 승인만.
- in-flight lock(workspace/.in-flight.json), 회로 차단기(일일손실), HITL 24h expire.

## 에이전트 구성 (17 가동) + 산출물 파이프라인
| Stage | Layer | 에이전트 | 산출물 |
|-------|-------|----------|--------|
| 0 | controller | controller | 사이클 상태·audit |
| 1 | data | **data-preprocessor** | `10_market_snapshot.md` |
| 2 | analysis | macro-specialist | `20_macro_report.md` |
| 2 | analysis | **fundamental-specialist** | `21_fundamental_report.md` |
| 2 | analysis | **rag-analyst** | `15_news_rag.json` |
| 3 | strategy | trend-expert | `30_trend_signal.md` |
| 4 | consensus | **bull-researcher** | `40_bull_brief.md` |
| 4 | consensus | **bear-researcher** | `41_bear_brief.md` |
| 4 | consensus | debate-moderator | `50_debate_log.md` |
| 5 | risk | risk-manager | `60_risk_check.md` |
| 6 | order | portfolio-pm | `70_order.simulated.json` |
| 7 | report | intraday-reporter | `recap.md` |
| 8 | reflect | self-reflector | `99_reflection.md` + semantic memory |
| 9 | monitor | signal-watcher | `_intraday/<date>/*.jsonl` |
| 10 | control | **compliance-officer** | `80_compliance_audit.md` |
| 11 | decide | quick-decider | `decisions/<signal>.md` |
| 12 | evolve | code-surgeon | `_evolve/<id>/proposal.md` |

(★ 굵게 = 이번에 추가된 6종. daytrading-quant 는 사이클 외 standalone 장중 모니터링.)

## mode별 stage 시퀀스
- **cycle**: 1→2(병렬: macro/fundamental/rag)→3→4(bull·bear 병렬→moderator)→5→6→10(compliance 게이트)→(종료 후 7/8)
- **analyze**: 1→2→3
- **debate**: 2→3→4(bull·bear→moderator)
- **consensus**: 4 (기존 brief 재사용)
- **risk**: 5 / **order**: 6→10 / **reflect**: 8
- **doctor**: pre-flight only / **init**: scaffold init

## 비고
- config/*.json 운영 임계는 라이브 .env.local/policy.json 과 정렬·튜닝 필요(스캐폴드 기본값).
- 첫 사이클은 `workspace/_memory/semantic/` 가 비어 있음 — rag-analyst 는 "0 patterns" graceful 처리.
- PER/PBR 등 정량 재무비율 모듈은 부재 — fundamental-specialist 는 정성 감사만(수치 날조 금지).
