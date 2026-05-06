# BAR-62 — 프론트 (테마/캘린더/뉴스 티커, Phase 3 종료 게이트)

## §0 분리 정책

- **BAR-62a (worktree backend)**: REST 엔드포인트 (FastAPI 라우트) + 단위 테스트
- **BAR-62b (운영 frontend)**: Next.js `app/themes`, `app/calendar`, `components/news-ticker` + Storybook + Playwright

## §1 목적

Phase 3 의 모든 backend 산출 (theme_repo / event_repo / news_repo) 을 frontend 가 소비할 수 있는 REST API 노출 + frontend 컴포넌트 명세.

## §2 FR (BAR-62a)

- `GET /api/themes` — 테마 목록 (id, name, description)
- `GET /api/themes/{id}/stocks` — 테마별 종목 (LeaderStockScorer 결과 활용)
- `GET /api/calendar?start=&end=` — 기간별 이벤트
- `GET /api/calendar/symbol/{symbol}` — 종목별 이벤트
- `GET /api/news/recent?source=&limit=` — 최근 뉴스 (티커용)
- 단위 테스트 ≥ 10 (라우트 mock + repo mock)

## §3 NFR
- 회귀 ≥ 396 passed (386 + 10)
- coverage ≥ 70%

## §4 OOS
- Next.js 빌드 (BAR-62b)
- WebSocket 실시간 갱신 (BAR-72)
- 1-click 네비 영상 (BAR-62b)

## §5 DoD
- 5 REST 엔드포인트 + 10+ tests, 회귀 0 fail
