# BAR-17 Gap Analysis Report

- **Match Rate**: 77%
- **Date**: 2026-04-11
- **Status**: Below 90% — Iteration Required

## Phase Scores

| Phase | Rate |
|-------|:----:|
| Phase 1: Framework | 100% |
| Phase 2: ShadCN UI | 56% |
| Phase 3: Layout | 67% |
| Phase 4: Zustand Store | 100% |
| Phase 5: Dashboard Widgets | 100% |
| Phase 6: Chart | 100% |
| Phase 7: Page APIs | 56% |
| Phase 8: Polish | 50% |

## Missing Items (11)

1. `components/layout/app-sidebar.tsx` — 별도 파일로 추출
2. `components/layout/status-bar.tsx` — 재사용 가능한 StatusBar
3. `components/ui/skeleton.tsx` — 로딩 Skeleton
4. `components/markets/market-table.tsx` — MarketTable 추출
5. `components/positions/position-summary.tsx` — PositionSummary 추출
6. `components/positions/position-table.tsx` — PositionTable 추출
7. react-hook-form + zod in OrderForm
8. Sonner toast 에러 처리 (alert 제거)
9. Architecture violation: pages에서 직접 api import 제거
