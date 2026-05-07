# BAR-69 — RLS + 컬럼 암호화 (Phase 5 세 번째)

- BAR-69a (worktree): ColumnEncryptor (Fernet) + RLSPolicy SQL 빌더 + 10 tests
- BAR-69b (운영): Vault/Secrets Manager 통합 + 실 RLS 적용 + 침투 테스트

## FR
- ColumnEncryptor: encrypt/decrypt + SecretStr key (CWE-798)
- RLSPolicy: enable_rls / policy_user_owns_row / admin_bypass SQL
- 회귀 ≥ 484
