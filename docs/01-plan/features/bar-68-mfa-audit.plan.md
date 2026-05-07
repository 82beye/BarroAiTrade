# BAR-68 — MFA + 감사 로그 무결성

- BAR-68a (worktree): MFAService TOTP + LiveTradingGate + AuditChain (sha256) + 12 tests
- BAR-68b (운영): /mfa/setup + /mfa/verify + 30일 chain 검증 cron + 실거래 강제

## FR
- MFAService.generate_secret / verify / now_code / provisioning_uri (RFC 6238)
- LiveTradingGate: OTP 미입력 시 차단
- AuditChain: GENESIS + sha256(prev + canonical_json) — verify_chain
- 회귀 ≥ 474
