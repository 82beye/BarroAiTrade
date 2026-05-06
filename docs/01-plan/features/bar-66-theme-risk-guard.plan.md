# BAR-66 — RiskEngine 비중 관리 (Phase 4 종료)

## §0 분리
- **BAR-66a (worktree)**: ThemeAwareRiskGuard 신규 + 단위 테스트
- **BAR-66b (운영)**: 모의 시뮬 한도 초과 신규 진입 100% 거부 검증

## §1 FR
- ThemeExposurePolicy: max_theme_exposure_pct=0.40 / max_concurrent_positions=3 / max_position_pct=0.30
- ThemeAwareRiskGuard: check_theme_exposure / check_concurrent_positions / check_position_size

## §2 NFR
회귀 ≥ 449.
