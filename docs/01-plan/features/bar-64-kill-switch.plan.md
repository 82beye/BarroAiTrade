# BAR-64 — Kill Switch + Circuit Breaker (Phase 4 두 번째)

## §0 분리 정책

- **BAR-64a (worktree)**: KillSwitch + CircuitBreaker 신규 + RiskEngine 통합 + 단위 테스트
- **BAR-64b (운영)**: 시뮬 시나리오 100% 발동 + 신규 진입 차단 검증

## §1 목적

치명 위험 발생 시 자동 매매 즉시 중단. 다음 시나리오:
- 일일 -3% 누적 손실
- 슬리피지 5분 3회 (체결가 ↔ 호가 괴리)
- 시세 단절 (gateway disconnect 30초)

발동 시 신규 진입 차단 + 운영자 알림.

## §2 FR

- `KillSwitchState` (frozen): is_active / triggered_at / reason / cooldown_until
- `KillSwitch` 서비스:
  - record_loss(amount) — 일일 누적 손실 갱신
  - record_slippage_event(deviation) — 슬리피지 이벤트
  - record_gateway_event(connected) — 게이트웨이 상태
  - check() → KillSwitchState — 발동 여부 평가
  - trip(reason) / reset(now) — 수동 발동 / cooldown 후 리셋
- `CircuitBreaker`: 단일 카운터 (이벤트 N회/M분 → 발동)
- 단위 테스트 ≥ 12

## §3 NFR
- 회귀 ≥ 423 (411 + 12)
- coverage ≥ 70%

## §4 OOS
- 실제 OrderExecutor 차단 (BAR-64b)
- Prometheus alert 실 발송 (BAR-64b)
- 시뮬 시나리오 100% 발동 영상 (BAR-64b)
