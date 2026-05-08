# BAR-OPS-03 — Live Trading Orchestrator (운영 b 트랙 #3)

## 흡수 b 트랙
- BAR-63b 부분 — OrderExecutor Protocol + 통합 사이클
- BAR-64b 부분 — KillSwitch 진입 차단 검증
- BAR-66b 부분 — RiskGuard 한도 거부 통합

## FR
- LiveTradingOrchestrator: attempt_entry / evaluate_position
- KillSwitch + RiskGuard + SOR 게이트 순서 (kill → risk → routing)
- KillSwitch active 시: 신규 진입만 차단, 보유 청산은 정상 발동
- OrderExecutor Protocol (BAR-63b 운영 어댑터로 교체)
- TradeOutcome enum (APPROVED / BLOCKED_KILL_SWITCH / BLOCKED_RISK_GUARD / BLOCKED_ROUTING)
- 10 통합 tests + 회귀 587

## DoD
- 회귀 0 fail / gap ≥ 90%
