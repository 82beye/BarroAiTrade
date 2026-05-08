# BAR-OPS-07 — 모의 침투 자동화 (Semgrep custom rules + Bandit + PenTestSuite)

## 흡수 b 트랙
- BAR-70b 정식 — Semgrep custom rules + Bandit 정책 + 자동 침투 단위 테스트

## FR
- infra/semgrep/barro-rules.yml — 7 custom rules:
  - barro-decimal-required-for-money (자금흐름 area:money)
  - barro-secretstr-required-for-keys (CWE-798)
  - barro-no-raw-sql-fstring (CWE-89)
  - barro-frozen-required-for-domain-models (BAR-45 정책)
  - barro-no-secret-print (CWE-532)
  - barro-https-only (CWE-319)
  - barro-no-pickle-load (CWE-502)
- infra/semgrep/.bandit — Bandit 정책 (Python AST 보안)
- backend/security/pen_test.py — PenTestSuite (8 attack vector)
  - SQL_INJECTION / JWT_TAMPERING / JWT_NONE_ALG / RBAC_BYPASS / SSRF / PII_LEAK / TIMING / REPLAY
- 12 신규 tests + 회귀 642 (630→642)

## DoD
- 모든 critical attack vector 차단 검증 (보안 회귀 게이트)
