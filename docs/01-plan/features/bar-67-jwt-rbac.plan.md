# BAR-67 — JWT + RBAC 골격 (Phase 5 첫 BAR, 시동)

## §0 분리
- **BAR-67a (worktree)**: JWT 토큰 발행/검증 + Role enum + RBAC 미들웨어 시그니처 + 단위 테스트
- **BAR-67b (운영)**: 실 /login 엔드포인트 + httpOnly Secure 쿠키 + Refresh 회전 + 라우트 가드 통합

## §1 FR
- `Role` enum: VIEWER / TRADER / ADMIN
- `JWTService`: encode_access(user_id, role) / encode_refresh / decode (signature + exp 검증)
- `RBACPolicy`: require_role(role) 체크 함수 (라우트 가드 진입점)
- 키 관리: settings.jwt_secret SecretStr (CWE-798)
- 단위 테스트 ≥ 12

## §2 NFR
- 회귀 ≥ 461 (449 + 12)
- TTL: access 1h / refresh 7d
- 알고리즘: HS256

## §3 OOS
- /login 엔드포인트 (BAR-67b)
- httpOnly 쿠키 통합 (BAR-67b)
- BAR-68 MFA
