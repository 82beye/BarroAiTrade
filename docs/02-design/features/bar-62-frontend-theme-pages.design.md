# BAR-62 design — 프론트 테마/캘린더/뉴스

## §1 REST 엔드포인트 (BAR-62a, FastAPI)

`backend/api/routes/themes.py`:
```python
@router.get("/api/themes")
async def list_themes() -> list[ThemeOut]: ...

@router.get("/api/themes/{theme_id}/stocks")
async def get_theme_stocks(theme_id: int) -> list[ThemeStockOut]: ...
```

`backend/api/routes/calendar.py`:
```python
@router.get("/api/calendar")
async def list_events(start: date, end: date) -> list[EventOut]: ...

@router.get("/api/calendar/symbol/{symbol}")
async def list_events_by_symbol(symbol: str) -> list[EventOut]: ...
```

`backend/api/routes/news.py`:
```python
@router.get("/api/news/recent")
async def recent_news(source: str | None = None, limit: int = 100) -> list[NewsOut]: ...
```

## §2 응답 스키마 (`backend/api/schemas/theme.py`, `event.py`, `news.py`)

```python
class ThemeOut(BaseModel):
    id: int; name: str; description: str

class ThemeStockOut(BaseModel):
    symbol: str; score: float; theme_id: int; theme_name: str

class EventOut(BaseModel):
    id: int; event_type: str; symbol: str | None
    event_date: str; title: str; source: str

class NewsOut(BaseModel):
    id: int; source: str; source_id: str; title: str
    url: str; published_at: str; tags: list[str]
```

## §3 BAR-62b 명세 (frontend, deferred)

- `frontend/app/themes/page.tsx`: 테마 카드 그리드 + 클릭 시 종목 리스트 페이지
- `frontend/app/calendar/page.tsx`: 월간 캘린더 + 이벤트 마커 + 종목 클릭 시 호가창 네비
- `frontend/components/news-ticker.tsx`: 헤더 슬라이드 (TanStack Query 1초 polling)
- 1-click 네비: 일정 → 종목 → 테마 → 호가창

## §4 10 테스트 (BAR-62a)
- `/api/themes` 200 / mock repo 빈 결과 (2)
- `/api/themes/{id}/stocks` 200 / 404 (2)
- `/api/calendar` start>end 422 / 정상 (2)
- `/api/calendar/symbol/{sym}` (1)
- `/api/news/recent` source filter / limit cap (2)
- schema validation (1)
