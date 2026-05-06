# BAR-67a JWT + RBAC — Completion Report

**Phase 5 진척**: 1/4 (BAR-67 완료)
**Tests**: 13 신규 / 회귀 462 (449→462)

## 핵심
- Role enum (VIEWER/TRADER/ADMIN) + 계층
- JWTService HS256 (access 1h / refresh 7d) + SecretStr 강제
- RBACPolicy.require_role / has_permission

## BAR-67b (운영 deferred)
- /login 엔드포인트 + httpOnly Secure 쿠키
- Refresh 회전
- 라우트 가드 통합 (FastAPI Depends)

## 다음
`/pdca plan BAR-68` (MFA + 감사 로그 무결성).
