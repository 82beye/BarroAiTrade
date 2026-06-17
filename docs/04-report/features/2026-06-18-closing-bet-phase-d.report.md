---
tags: [report, phase-d, strategy/closing_bet, feature/thetrading-uplift, status/step1]
---

# 종베 라이브 전환 Phase D — Step 1: 페이퍼 스캐너 (실주문 없음)

> **Project**: BarroAiTrade · **Date**: 2026-06-18 · **Branch**: `feat/thetrading-uplift-increment1`
> **상태**: Step 1 완료(페이퍼). **실자본 미투입.** 실주문 데몬 통합은 Step 2(페이퍼로 엣지 확인 후).

---

## 1. 왜 곧바로 실거래가 아니라 페이퍼인가 (두 가지 결정적 이유)

**① 검증의 진입 메커니즘 불일치.** 종베 OOS 3/3 PASS는 `entry_on_next_open`(**익일 시초 진입**) 설정이었다. 그러나 **종가베팅의 정의는 15:00 종가 진입**이고, 종가진입 ablation은 **브레이크이븐**이었다. 즉 **OOS가 검증한 건 종가진입 종베가 아니다.** 실자본 전에 "종가진입 종베가 실제로 엣지가 있는지"를 실신호로 직접 확인해야 한다.

**② 데몬 가드 충돌.** 종베의 전제(신고가 장대양봉 **종가=고점 근처** 매수)는 데몬의 고점추격 방지 가드와 정면 충돌한다:
- `_ZONE_ENTRY_CUTOFF`(14:30) — 종베 15:00 진입이 차단됨
- P4 고점인접 차단(일중 H 대비 1.5% 이내 + 모멘텀 종료) — 종베 종가가 걸림
- P10 시초가 폭등 차단(+20%) — 강세 종베 후보가 걸림

→ 기존 `_scan_and_buy`에 종베를 끼우면 (a)종베가 막히거나 (b)가드를 풀어 **다른 전략이 위험**해진다. 그래서 종베 실주문은 **격리된 별도 경로**로, 충분히 검증한 뒤 해야 한다.

(추가: 개발 머신은 키움 인증이 없어 라이브 데몬 코드를 여기서 테스트할 수 없다 → 실주문 코드를 미검증 상태로 올리는 건 위험.)

---

## 2. Step 1 산출물 — 페이퍼 스캐너

`scripts/closing_bet_paper_scan.py` — **주문을 전혀 내지 않고** 종베 신호만 수집:

- 15:00~15:20(KST) 주도주 선정 → 각 종목 **일봉+5분봉**으로 종베 분석(검증된 게이트: **money_flow ON·zone OFF**(악화 확인)·주도주컷) → 신호 종목을 `data/closing_bet_paper.csv`에 기록(진입가=종가, score, flow_grade, 0.618 손절가).
- 모드: 라이브(운영 머신, 키움 picker/fetcher) / `--from-cache`(개발 머신 테스트).

**운영 머신 실행** (cron 16:00 직전 또는 수동):
```bash
cd /Users/beye/BarroAiTrade && git pull origin main
venv/bin/python scripts/closing_bet_paper_scan.py --top 10
# → data/closing_bet_paper.csv (append)
```
캐시 테스트(개발 머신): `python scripts/closing_bet_paper_scan.py --from-cache --force --symbols 005930,000660`

---

## 3. 페이퍼로 무엇을 측정하나 (Step 2 진입 게이트)

1~2주 수집 후, 기록된 종베 신호의 **익일 아침(9~10시) 실제 수익률**을 집계(다음날 시·종가 캐시 또는 `_daily_strategy_audit`):
- **종가진입→익일청산 net 기대값이 양(+)인가** (비용 0.35% 차감 후)?
- 신호 빈도(일 몇 건), money_flow 게이트가 실제로 품질을 올리는가?

→ **양(+) 확인 시에만** Step 2(실주문 데몬 통합, 소액). 브레이크이븐/음(−)이면 종가진입 종베는 보류하고 진입방식(익일시초 등)·수수료 협의로 회귀.

---

## 4. Step 2 (페이퍼 통과 시) — 실주문 데몬 통합 설계 (미구현)

- 격리 함수 `_scan_and_buy_closing_bet()`(15:00~15:20 전용) — 기존 `_scan_and_buy`의 고점추격 가드를 **건드리지 않고** 종베 전용 진입. `LiveOrderGate`(dry_run 기본·일일손실·재시도) 재사용, `STRATEGY_EXIT_PROFILES["closing_bet"]`(min1/max3) 청산.
- 소액: `max_per_position` 대폭↓(예 2%), 동시 1~2종, 종베 전용 carry 한도.
- 점진: 기존 전략 유지 → 종베 소액 라이브로 OOS 가정(체결가) 재현 확인 → 비중 확대 → 그 다음 종베-only(`--strategies closing_bet` + supertrend/limit_up_chase 미기동).

---

## 5. 재현 / 테스트

```bash
# 캐시 모드 스모크(개발 머신)
python scripts/closing_bet_paper_scan.py --from-cache --force --symbols 005930,000660,373220
# 종베 단위/게이트 테스트
python -m pytest backend/tests/strategy/test_closing_bet.py -q   # 23 passed
```

> 한 줄: **종베 실자본은 아직 이르다.** 검증이 통과한 건 종가진입 종베가 아니라 익일시초 변형이고, 데몬 가드와도 충돌한다. 먼저 페이퍼로 종가진입 종베의 실엣지를 측정하고, 양(+)이면 소액 격리 통합으로 간다.
