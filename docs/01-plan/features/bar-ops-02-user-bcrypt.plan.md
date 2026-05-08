# BAR-OPS-02 — User CRUD + bcrypt (운영 b 트랙 통합 #2)

## 흡수 b 트랙
- BAR-67b 후속 — User 도메인 모델 + Repository (BAR-OPS-01 의 _USER_DB stub 대체)
- BAR-69b 부분 — bcrypt 비밀번호 해시 + mfa_secret 컬럼 (Fernet 암호화는 후속)

## FR
- backend/models/user.py — User (frozen, user_id pattern, email validate)
- backend/security/password.py — PasswordHasher (bcrypt + sha256 prehash for 72-byte 제한 회피)
- backend/db/repositories/user_repo.py — insert / find_by_user_id / update_password / update_mfa_secret
- alembic/versions/0007_users.py — users 테이블 (UNIQUE user_id + email + idx)
- 18 단위 테스트 (PasswordHasher 8 + UserRepo 8 + Model 3 + alembic 2 — 일부 통합)
- 회귀 ≥ 577

## DoD
- 회귀 0 fail / gap ≥ 90%
