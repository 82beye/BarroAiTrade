# BAR-65 — 매매 일지 + 감정 태그 (Phase 4 세 번째)

## §0 분리

- **BAR-65a (worktree)**: TradeNote 모델 + JournalRepository + alembic 0006 + REST 4 + 단위 테스트
- **BAR-65b (운영 frontend)**: app/journal/page.tsx + 차트 우클릭 메모 + 월말 자동 분석 리포트 cron

## §1 FR
- TradeNote (frozen): trade_id / entry_time / exit_time / symbol / side / qty / pnl(Decimal) / emotion (PROUD/REGRET/NEUTRAL) / note / tags
- JournalRepository: insert / find_by_date_range / find_by_symbol / update_emotion
- alembic 0006: trade_notes (UNIQUE trade_id + idx 2)
- REST: POST /api/journal, GET /api/journal?start=&end=, GET /api/journal/symbol/{}, PATCH /api/journal/{id}/emotion
- 단위 테스트 ≥ 12

## §2 NFR
회귀 ≥ 436 (424 + 12), coverage ≥ 70%.
