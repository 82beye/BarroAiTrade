# BAR-OPS-05 — Live Trading Checker + DEPLOYMENT

## 흡수
- Master Plan v2 Phase 4 종료 게이트 자동화 — "실거래 진입 권한 (자산 5%, 1주 라이브)"

## FR
- infra/live-checklist.yaml — 10 게이트 (regression / baseline / sim 3주 / kill switch / OWASP / pen-test / audit chain / alerts / runbook / deployment) + approval 단계
- backend/security/live_trading_checker.py — LiveTradingChecker (load_checklist + verify) + GateResult + CheckSummary
- DEPLOYMENT.md — 7 섹션 배포 절차 (사전 조건 / 토폴로지 / 시크릿 / DB / 모니터링 / 단계적 진입 / 롤백)
- 10 신규 tests + 회귀 615 (605→615)
