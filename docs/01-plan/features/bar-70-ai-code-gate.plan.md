# BAR-70 — AI 코드 PR 게이트 (Phase 5 종료)

- BAR-70a (worktree): GitHub Actions workflow + code_gate 정책 모듈 + 10 tests
- BAR-70b (운영): 모의 침투 테스트 + Semgrep custom rules + Bandit 정책 튜닝

## FR
- .github/workflows/security-scan.yml — PR 라벨 ai-generated 시 Semgrep + Bandit + pytest
- backend/security/code_gate.py — 라벨 검증 + float-money 패턴 감지
- 10 tests + 회귀 ≥ 494
