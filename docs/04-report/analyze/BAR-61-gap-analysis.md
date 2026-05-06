# BAR-61a Gap Analysis — 100% PASS

| # | 항목 | 결과 |
|---|------|:---:|
| 1 | EventType + MarketEvent (frozen) | ✅ |
| 2 | EventCollector Protocol + StubEventCollector | ✅ |
| 3 | EventCalendar (refresh) | ✅ |
| 4 | EventLinker (symbol 직접 / metadata fallback) | ✅ |
| 5 | EventRepository (insert/find_by_date_range/find_by_symbol + dialect 분기) | ✅ |
| 6 | alembic 0005 (UNIQUE + idx 2) | ✅ |
| 7 | 16 tests + 회귀 386 | ✅ |

iterator 불필요. report 진행.
