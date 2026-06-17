---
tags: [report, validation, oos, strategy/closing_bet, feature/thetrading-uplift, status/complete]
---

# 종가베팅(종베) 검증 판정 리포트 — Phase C

> **대상**: `ClosingBetStrategy` (구체화 후: 분봉 자금유입·존 게이트 + 라이브 실행경로 통합)
> **Project**: BarroAiTrade · **Date**: 2026-06-18 · **Branch**: `feat/thetrading-uplift-increment1`
> **판정(요약)**: **일봉 OOS 관문 3/3 PASS(실측비용 반영)** — 프로젝트 공식 기준 통과. 단 **엔진/진입 메커니즘 민감도**와 **차별화 게이트 비효과**가 핵심 caveat → **조건부 GO**(소액·다음봉시가 진입·모니터링 전제).

---

## 1. 무엇을 검증했나

종베 구체화(Phase A·B) 후, 프로젝트의 **공식 OOS 관문**(`scripts/_oos_validation.py`, f_zone/gold_zone가 통과한 그 기준)에 종베를 편입해 검증. 관문 = **랜덤 유니버스(선택편향 제거) + train/holdout 40% 시간분할 + 실측비용**. PASS 조건 5개: active≥15·trades≥30·avg_ret>0·drop1 부호안정·holdout avg_ret>0.

병행: 5분봉 주입 **분봉 게이트 ablation**(자금유입·존이 per-trade 엣지를 더하는가).

실측비용 반영: `BARRO_COMMISSION_RATE=0.0035`(편도 0.35%, fill_audit 186건 역산).

---

## 2. 일봉 OOS 결과 — closing_bet **3/3 PASS**

| seed | active | trips | 승률 | avg_ret | holdout | drop1 | 판정 |
|------|--------|-------|------|---------|---------|-------|------|
| 42 | 50 | 248 | 43% | **+3.480%** | +4.710% | OK | **PASS** |
| 7 | 55 | 347 | 52% | **+3.889%** | +4.583% | OK | **PASS** |
| 123 | 50 | 246 | 52% | **+3.660%** | +3.927% | OK | **PASS** |

비교(동일 관문·동일 seed):

| 전략 | seed42 | seed7 | seed123 | 종합 |
|------|--------|-------|---------|------|
| **closing_bet** | +3.48 | +3.89 | +3.66 | **3/3 PASS (avg_ret 최고)** |
| f_zone | +1.90 | +1.99 | +3.08 | 3/3 PASS |
| gold_zone | +1.56 | +1.95 | +2.34 | 3/3 PASS |
| sf_zone | FAIL | FAIL | FAIL | 0/3 (희소, 기존 알려진 한계) |

→ **종베가 실측비용 반영 상태로 3 seed 전부 PASS, holdout(out-of-sample 40%)에서도 +3.9~4.7%로 견고.** drop1 부호안정(아웃라이어 의존 아님). 프로젝트 공식 기준으로는 **명확한 통과**.

---

## 3. 분봉 게이트 ablation (5분봉 주입, top 600, 19,670 stock-day)

| 변형 | 트립 | 승률 | gross | net(0.90) |
|------|------|------|-------|-----------|
| baseline (게이트 OFF) | 448 | 55.1% | +0.966% | +0.066% |
| +money_flow | 274 | 55.5% | +0.985% | +0.085% |
| +zone | 58 | 51.7% | +0.711% | **−0.189%** |
| +both | 38 | 47.4% | +0.355% | **−0.545%** |

- **money_flow 게이트**: 미세 개선(gross +0.02%, 트립 39%↓). 약하지만 양(+).
- **zone 게이트: 오히려 악화**. 신호 급감(448→58) + 기대값 하락. **장대양봉 신고가 종가(고점 근처)와 골드존 0.5~0.618 되돌림은 같은 날 양립 불가**(개념 충돌, 단위테스트로도 재현). 종베의 존은 "돌파 후 별도 눌림 진입" 셋업이지 돌파일 종가 게이트가 아니다 → **현 형태 zone 게이트는 폐기 또는 재설계**.

---

## 4. ⚠️ 핵심 caveat — 엔진/진입 메커니즘 민감도

같은 종베인데 백테스트 엔진에 따라 결과가 크게 다르다:

| 경로 | 진입 | 유니버스 | net 결과 |
|------|------|---------|---------|
| **일봉 OOS** (IntradaySimulator) | **익일 시가**(entry_on_next_open) | 랜덤60 + holdout | **+3.5%/트립 PASS** |
| 종가 ablation (custom) | 신호일 **종가** | 거래대금상위 600 | **브레이크이븐** |
| 일봉 스캐폴드(6/17) | 종가 | 상위500 | net ≈ 0 |

→ **종베 엣지의 상당 부분이 "장대양봉 익일 시초 연속성"에 있다**(익일 시가 진입이 종가 진입보다 유리). 이는 OOS 엔진이 모든 전략에 동일 적용하는 가정(f/gold도 같은 엔진서 +1.5~3%)이라 종베만의 artifact는 아니나, **라이브 진입 체결가가 이 가정과 일치해야** OOS 수익이 재현된다. 종가 동시호가 슬리피지로 나쁜 체결이 나면 브레이크이븐으로 회귀할 수 있다.

(추가 보수성: OOS 엔진은 intrabar에서 TP를 SL보다 먼저 평가 → 절대 수치는 모든 전략 공통으로 약간 낙관.)

---

## 5. 판정 — 조건부 GO

**공식 OOS 관문 3/3 PASS = 라이브 전환 게이트는 충족.** 단 아래 조건을 달아 **조건부 GO**:

1. **진입 체결가 정합**: 라이브 진입을 OOS 가정(익일 시초 또는 그에 준하는 유리 체결)에 맞춤. 종가 동시호가 불리체결 시 엣지 소멸 → 진입 방식 명시 + 슬리피지 모니터.
2. **zone 게이트 폐기**(net 악화). money_flow는 약한 양(+)이라 유지/추가 검증.
3. **소액 시작**: `max_per_position` 대폭↓(예 2%), 동시 1~2종, 종베 전용 carry 한도.
4. **점진 전환**: 기존 전략 **즉시 중지 금지**. 종베를 소액 라이브(또는 페이퍼)로 1~2주 가동해 OOS 수익이 실체결로 재현되는지 확인 후 비중 확대 → 그 다음 종베-only.
5. **모니터링**: `_daily_strategy_audit.py`(승률·자본가중·진입품질) + `verify_eod_data.py`. 종베 실측 승률·체결슬립이 OOS와 괴리되면 롤백.

> 한 줄: **종베는 공식 기준을 통과했다(예상보다 강함). 단 그 수익은 "익일 시초 연속성"에 의존하므로, 라이브 체결가가 그 가정을 지키는지가 전부다. 종가 불리체결이면 브레이크이븐.** zone 게이트는 빼고, 소액·점진 전환으로 실체결 검증부터.

---

## 6. 구현/검증 산출물 (Phase A·B·C)

- **게이트 실구현**: `closing_bet.py` `_money_flow_grade`(분봉 AM/PM 거래대금)·`_in_zone`(골드존 되돌림)·`AnalysisContext.intraday_candles`(멀티TF)·`LeaderCandidate.trade_value/is_new_high`.
- **실행경로 통합**: `intraday_simulator.py` `closing_bet` 분기(swing_38 멀티데이 패턴) + `_oos_validation.py` 편입(`BARRO_DATA_DIR` override).
- **백테스트**: `scripts/backtest_closing_bet_intraday.py`(분봉 ablation) + 원시결과 `2026-06-18-closing-bet-intraday-ablation.json`.
- **테스트**: `test_closing_bet.py` 23건(게이트 9건 추가). 전체 회귀 **1455 passed**.

## 7. 재현

```bash
# venv = /Users/beye/workspace/BarroAiTrade/venv/bin/python
# 일봉 OOS (실측비용, 3 seed)
for s in 42 7 123; do
  BARRO_DATA_DIR=.../data BARRO_COMMISSION_RATE=0.0035 \
    python scripts/_oos_validation.py --n 60 --seed $s
done
# 분봉 게이트 ablation
python scripts/backtest_closing_bet_intraday.py 600 250
```

## 8. 다음 단계 (Phase D — 사용자 승인 시)

조건부 GO 수용 시: D1 데몬 EOD 진입경로(`_scan_and_buy_closing_bet`, 15:00~15:20) + D2 스캐너 라이브 컨텍스트 주입 + D3 종베-only 스위치(소액·점진). **zone 게이트 제외, money_flow 유지.** 기존 전략은 종베 실체결 검증 후 단계적 중지.
