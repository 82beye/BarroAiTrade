# BAR-61 — 일정 캘린더 + 이벤트→종목 연동 (Phase 3 여섯 번째)

**선행**: BAR-59a (themes/theme_stocks) ✅ / BAR-60a (LeaderStockScorer) ✅
**후행**: BAR-62 (프론트 캘린더)

## §0 분리 정책

- **BAR-61a** (worktree): MarketEvent 모델 + EventCollector(IR/인포맥스/FnGuide stub) + EventLinker + alembic 0005 + 단위 테스트
- **BAR-61b** (운영): 실 IR/인포맥스/FnGuide API + 사용자 수동 등록 REST + 9 엔드포인트 + scanner 힌트 주입 검증

## §1 목적

D-1/D-Day 이벤트(실적발표 / IPO / 메가사이클 / 정책 발표) 발생 시 scanner 힌트 주입 + 프론트 캘린더 표시.

## §2 FR

- `MarketEvent` 모델 (frozen): event_type / symbol / event_date / title / source / metadata
- `EventCollector` Protocol: fetch() → list[MarketEvent] (IR/인포맥스/FnGuide stub)
- `EventCalendar` 서비스: get_events_by_date_range(start, end) / get_events_by_symbol
- `EventLinker`: event → theme → 관련 종목 자동 매핑
- alembic 0005: market_events (event_type, symbol, event_date, title, source, metadata JSONB)
- Settings 1 신규 (NEWS_CALENDAR_BACKEND=memory|kis)

## §3 NFR
- 회귀 ≥ 385 passed (370 + 15)
- coverage ≥ 70%

## §4 OOS
- 실 API (BAR-61b)
- REST 엔드포인트 9개 (BAR-61b)
- scanner 힌트 실제 주입 (BAR-61b)
- 프론트 (BAR-62)

## §5 DoD
- 15+ tests, 회귀 0 fail

## §6 다음
`/pdca design BAR-61` (단일 leader).
