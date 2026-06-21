---
tags: [report, feature/dante-uplift, channel/주식단테, governance/d-hitl, status/done]
---

# distribution 청산 게이트 — (d) config-gated 구현 리포트

> **연관**: [[2026-06-22-dante-oos-validation.report|OOS 검증]] · [[2026-06-21-dante-uplift.report|(c)구현+shadow]] · [[../../02-design/features/2026-06-21-dante-uplift.design|설계]]
>
> **Summary**: OOS에서 견고 PASS한 distribution(세력이탈 장대음봉, JD-R13)을 라이브 청산 경로(`holding_evaluator`)에 **config-gated default-OFF**로 배선. 사용자 확정(2026-06-22): **액션=전량 청산, 임계=거래량 3.0배·몸통 3%**. `distribution_exit_enabled=False`(default)라 **머지해도 라이브 청산 무변경(byte-identical)**. 활성화(enabled=True)는 약세장 dry-run 후 별도 HITL.
>
> **Date**: 2026-06-22 · **Status**: Done((d) 배선, default-OFF). 활성화는 미진행(HITL).

---

## 1. 거버넌스 경로
(d) 실거래 동작 변경 → **AskUserQuestion으로 값/방향 확정**(2026-06-22: 전량 청산·표준 임계) → **config-gated default-OFF 구현**(현행 보존) → 활성화는 dry-run 후 HITL. `barrotrade-code-surgeon`은 숫자 default 패치 전용이라 부적합(신규 청산 로직) → 직접 config-gated 구현(선례 `regime_exit.py` 미러링).

## 2. 변경 (default-OFF parity 보장)

| 파일 | 변경 | default 동작 |
|------|------|-------------|
| `backend/core/strategy/dante_filters.py` | `DistributionExitConfig`(frozen, enabled=False) + `fires()` + `from_policy_config()` | enabled=False → `fires()` 항상 False |
| `backend/core/risk/holding_evaluator.py` | `SellSignal.DISTRIBUTION`; `PositionContext.{daily_candles,distribution_exit}`; 평가 분기(min_hold 이후·short-term-high 이전) | ctx 미주입/disabled → 평가 skip(byte-identical) |
| `backend/core/journal/policy_config.py` | `distribution_exit_{enabled=False,vol_mult=3.0,body_min=0.03}` | 신규 PolicyConfig → 비활성 |
| `scripts/intraday_buy_daemon.py` | `_dist_cfg=from_policy_config(cfg)`; **enabled일 때만** 일봉 fetch + PositionContext 주입; `_SELL_SIGNALS`에 DISTRIBUTION | disabled → fetch X, 주입 X |

- **순환 회피**: holding_evaluator는 `DistributionExitConfig`를 TYPE_CHECKING만 import, 런타임은 duck-typing(`.fires()`) — strategy↔risk 순환 없음(선례 regime_exit 동일).
- **발동 조건**(enabled 시): 정배열 확장구간(종가>SMA60) ∧ 음봉 몸통≥3% ∧ 거래량 전일×3 → **전량 청산**(SellSignal.DISTRIBUTION). 우선순위: max_hold(강제)·min_hold(보호) 이후, 단기고점·트레일링 이전.

## 3. 테스트 (default-OFF parity 포함)
`backend/tests/strategy/test_distribution_exit.py` (10) + `test_dante_filters.py` 갱신:
- 설정: default OFF→fires False, enabled+정배열+신호→True, 비정배열/거래량약함/일봉없음→False.
- 통합: disabled→기존 평가와 동일(HOLD), enabled+발동→DISTRIBUTION 전량청산(sell_qty=qty), 발동 안 함→DISTRIBUTION 아님.
- 경계: 스캐너(매수경로) dante_filters 미참조 + **신규 PolicyConfig→게이트 default-OFF** 보장.
- **전체 회귀 1556 passed, 0 failed**(기존 1546 + 10).

## 4. 활성화 절차 (★HITL — 미진행)
1. `dry_run` 데몬으로 distribution 발동·청산을 **로그로 1~2주 관찰**(특히 약세 구간) → whipsaw/오청산율 점검.
2. `PolicyConfig.distribution_exit_enabled=True` 설정(또는 운영 env/policy.json) → **사용자 승인 후**.
3. `barro-trade-review` 매매복기로 활성 후 효과(회피 손익·청산 타이밍) 측정·환류.
- 롤백: `distribution_exit_enabled=False` 즉시 무력화(코드 변경 불필요).

## 5. 한계 (OOS 리포트 §6 계승)
- OOS가 **불장 편향**(약세장 OOS 불가) → 활성 전 dry-run 필수.
- 일봉 종가 기준 평가 — 장중 청산 타이밍/슬리피지는 라이브에서 별도. 데몬 사이클 주기에 1일 1회 발동(일봉 갱신 기준).
- 정배열 게이트(종가>SMA60)는 OOS proxy — 라이브 캘리브레이션 여지.
