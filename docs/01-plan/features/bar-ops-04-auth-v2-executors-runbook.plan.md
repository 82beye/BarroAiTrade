# BAR-OPS-04 — auth_v2 (UserRepository 통합) + OrderExecutor 어댑터 + RUNBOOK

## 흡수 b 트랙
- BAR-67b 후속: auth ↔ UserRepository 통합 + register 엔드포인트 (OPS-01 _USER_DB stub 대체)
- BAR-63b 부분: OrderExecutor 운영 어댑터 stub (Kiwoom / IBKR / Upbit / Paper)
- 운영 문서: RUNBOOK.md (8 섹션 — KillSwitch, Gateway, Embedding lag, DB pool, 캐시, 키 회전, 실거래, 재해 복구)

## FR
- backend/api/routes/auth_v2.py — register / login / refresh / mfa/verify (UserRepository + bcrypt)
- backend/core/execution/order_executors.py — Paper / Kiwoom / IBKR / Upbit (Protocol + SecretStr 강제)
- RUNBOOK.md — 운영 장애 대응 절차서 (alerts.yaml 매핑)
- 18 신규 tests + 회귀 605 (587→605)

## DoD
- 회귀 0 fail / gap ≥ 90%
