# BAR-OPS-01 — 운영 b 트랙 통합 (인증 + middleware + 알림 IaC)

마스터 플랜 a 트랙 종료 후 운영 진입 통합 사이클.

## 흡수 b 트랙
- BAR-67b: /login + /refresh + httpOnly Secure cookie
- BAR-68b: /mfa/setup + /mfa/verify
- BAR-71b: TenantContextMiddleware (FastAPI)
- BAR-73b: monitoring/alerts.yaml IaC

## FR
- backend/api/routes/auth.py — 4 엔드포인트
- backend/api/middleware.py — TenantContextMiddleware
- monitoring/alerts.yaml — 6 alert 정책 (DailyLoss / GatewayDisconnect / Slippage / NewsLag / APIError / DBPool)
- 12 통합 테스트

## DoD
- 회귀 ≥ 559
- gap ≥ 90%
