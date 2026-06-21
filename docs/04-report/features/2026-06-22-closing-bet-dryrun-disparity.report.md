---
tags: [report, strategy/closing_bet, governance/d-hitl, status/done]
---

# 종가베팅 dry-run 활성 — 이격도 게이트 ON (config-gated)

> **연관**: [[2026-06-17-thetrading-methodology-uplift.design|더트레이딩 설계 §6 ClosingBet]] · [[2026-06-21-uplift-summary.report|더트레이딩 요약]]
>
> **Summary**: 사용자 요청(종베를 매매전략에 편입)에 대해 **dry-run 먼저 + 이격도 게이트 ON**(2026-06-22 확정)으로 진행. 종베는 이미 **알림/페이퍼 인프라**(`closing_bet_alert_daemon.py`·`closing_bet_paper_scan.py`, 실주문 0)를 보유 → 새 자동매매 배선 없이, 두 스크립트에 **이격도 게이트(disparity_yellow)를 env 토글(default-OFF)**로 추가만 함. 라이브 무변경(byte-identical). 자동매매 편입은 dry-run 통과 후 별도 (d)(EOD dispatch 배선).
>
> **Date**: 2026-06-22 · **Status**: Done(dry-run 준비). 자동매매 편입=미진행(후속 HITL).

---

## 1. 배경·결정
- 요청: "종가베팅도 매매전략에서 실행되게 설정". 종베 진입창 = **15:00~15:20**(`ClosingBetParams.entry_window_start/end`, require_eod_window).
- 현 상태: `_DEFAULT_ENABLED["closing_bet"]=False` + **2026-06-18 사용자 결정 "종베=수동관리 전용"**(알림만, 자동매매 X). 데몬 진입 cutoff 14:30이라 종베(15:00~) 자동진입은 별도 EOD dispatch 배선 필요(설계 §6.5).
- 근거: 종베는 비용 잠식으로 baseline net +0.107%(왕복 0.90%) → **이격도 게이트 ON 시 net +0.405%**. 오버나잇 갭 리스크 실증(설계 §6.6).
- **확정(AskUserQuestion 2026-06-22): ① dry-run 먼저 ② 이격도 게이트 ON.**

## 2. 변경 (최소·config-gated)
종베 dry-run 인프라가 이미 존재(실주문 0):
- `scripts/closing_bet_alert_daemon.py` — 알림 전용(텔레그램 시그널, 주문 X).
- `scripts/closing_bet_paper_scan.py` — 페이퍼 스캐너(CSV 기록, 주문 X, "라이브 통합 전 측정" 전용).

→ 두 스크립트에 **이격도 게이트 env 토글** 추가:
```python
_CB_DISPARITY = os.environ.get("BARRO_CB_DISPARITY_YELLOW", "0") in ("1","true","yes","on")
PARAMS = ClosingBetParams(..., require_disparity_yellow=_CB_DISPARITY, disparity_yellow_threshold=0.1425)
```
- **default "0"(OFF) → 현행 byte-identical**. 사용자 dry-run은 `BARRO_CB_DISPARITY_YELLOW=1`로 ON.
- 코어 `closing_bet.py`·`_DEFAULT_ENABLED`·자동매매 데몬 **무변경**. 검증: AST OK, env 토글 OK, 회귀 1556 passed.

## 3. dry-run 실행 절차 (★트레이딩 머신, 정규장 15:00~15:20)
종베는 주도주 picker(Kiwoom 인증 필요)라 dev 머신 불가 → 트레이딩 머신에서:

**A. 페이퍼 스캐너**(CSV 수집, 권장):
```
BARRO_CB_DISPARITY_YELLOW=1 python scripts/closing_bet_paper_scan.py --top 10
# → data/closing_bet_paper.csv 에 15:00~15:20 종베 신호(이격도 게이트 적용) append
```
**B. 알림 데몬**(실시간 텔레그램):
```
BARRO_CB_DISPARITY_YELLOW=1 python scripts/closing_bet_alert_daemon.py --mode loop --interval 60
```
- 1~2주 관찰: 신호 빈도(게이트로 감소), 익일 슈팅 적중, 오버나잇 갭. `_daily_strategy_audit`로 익일 결과 집계.
- env 미설정/0 → 게이트 OFF(현행). 끄려면 env 제거.

## 4. 자동매매 편입 (★dry-run 통과 후 별도 HITL — 미진행)
실제 "매매전략 편입"(자동 진입)은 본 dry-run 통과 후:
1. 데몬에 **종베 EOD dispatch 블록**(15:00~15:20 전용 경로) + `_CUTOFF_EXEMPT_STRATEGIES`에 closing_bet 추가(14:30 cutoff 면제) 배선 — 설계 §6.5.
2. 종베 전용 **오버나잇 carry 한도(계좌 10%)·동시 1~2종**(설계 §6.6) 필수.
3. `enabled_strategies={"closing_bet": True}` + 실주문 — AskUserQuestion 최종 승인 후.
- 2026-06-18 "수동관리 전용" 결정과 충돌하므로 자동매매 전환은 명시 승인 필요.

## 5. 한계
- 종베 OOS는 '익일시초 진입' 변형 PASS였고 '종가진입'은 브레이크이븐 — 본 dry-run(페이퍼)이 실신호 직접 측정 목적.
- 불장 편향·오버나잇 갭·동시호가 분할 미체결(평단 왜곡) 리스크 → 실자본 전 dry-run 필수.
