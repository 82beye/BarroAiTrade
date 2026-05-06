# BAR-61a 일정 캘린더 — Completion Report

**Phase 3 진척**: 6/7 (BAR-56~61 완료)
**PR Trail**: plan #108 / design #109 / do #110 / analyze #111 / report (this)
**Tests**: 16 신규 / 회귀 386 (370→386, 0 fail)

## 핵심
- MarketEvent (frozen) + EventType (5종)
- StubEventCollector (BAR-61b 운영 어댑터로 교체)
- EventCalendar.refresh + EventLinker (symbol 직접 / metadata fallback)
- alembic 0005 (UNIQUE(symbol,date,type) + idx 2)

## BAR-61b (운영 deferred)
- 실 IR/인포맥스/FnGuide API 어댑터
- REST 9 엔드포인트 (CRUD + 검색)
- scanner 힌트 실 주입 검증

## 다음
`/pdca plan BAR-62` (프론트 — 테마 박스 + 캘린더 + 뉴스 티커).
