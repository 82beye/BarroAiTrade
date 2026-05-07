# BAR-71 — 멀티 사용자 격리 + 사용량 메트릭 (Phase 6 시동)

- BAR-71a (worktree): TenantContext (contextvar) + UsageMetricsRecorder + 10 tests
- BAR-71b (운영): orchestrator 멀티텐드 + RLS 통합 + usage_metrics 테이블 + 어드민 대시보드

## FR
- TenantContext (set_user / current_user / require_user) — RLS app.user_id 와 연동
- UsageMetricsRecorder (record / time_call ctx manager / 사용자별 격리)
- 회귀 ≥ 504
