# BAR-OPS-02 — User CRUD + bcrypt Report

## 핵심
- User 도메인 모델 (frozen, regex user_id, email)
- PasswordHasher (bcrypt + sha256 prehash, 72-byte 제한 회피)
- UserRepository (text() + dialect 분기)
- alembic 0007 (users 테이블)

## 흡수 b 트랙
- BAR-67b 후속 (User 도메인 stub → 실 Repository 통합 가능)
- BAR-69b 부분 (bcrypt password)

## Tests
- 18 신규 / 회귀 577 passed (559→577)

## 다음 b 트랙
- BAR-OPS-03: auth 라우트 → UserRepository 통합 (현 _USER_DB 교체)
- BAR-OPS-04: Live Trading orchestrator 통합 (OrderExecutor + ExitEngine + KillSwitch + RiskGuard 와이어링)
