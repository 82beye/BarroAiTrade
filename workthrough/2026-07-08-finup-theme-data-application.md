# Finup Theme Data Application

## Overview

Applied the previously crawled Finup theme data to the running TIMA theme board. The crawler had produced JSON/CSV files under `data/finup_theme/`, but the backend was still reading `themes` and `theme_stocks` from the old curated `theme_map.json` seed.

## Context

- Expected: `/themes` should show Finup themes such as `D램`, `DDR5`, `3D 낸드`, `방산`, and `비메모리 반도체`.
- Actual before fix: `/api/themes` returned old curated seed themes such as `반도체`, `HBM`, `2차전지`, `바이오`, and descriptions ending in `큐레이션 시드 기반`.
- Additional runtime issue: the active backend on port `8000` was running from `.claude/worktrees/tima-dashboard-p0` with `DB_PATH=/tmp/theme_live_test.db`, while frontend work was being done in the main repository.

## Changes Made

### 1. Finup Snapshot Importer

File: `backend/core/themes/finup_importer.py`

- Added `import_finup_theme_snapshot`.
- Loads `data/finup_theme/latest.json` by default.
- Parses each snapshot theme and its `relation_stocks`.
- Replaces existing `themes`, `theme_keywords`, and `theme_stocks` when `replace=True`.
- Stores Finup stock `diff` as `theme_stocks.score`.
- Merges Finup stock names into `data/stock_names.json` so theme rows can display Korean names instead of only symbols.

### 2. API Integration

File: `backend/api/routes/themes_calendar_news.py`

- Added `POST /api/themes/import-finup`.
- Clears the in-memory theme stock cache after import.
- Sets `ThemeStockOut.change_pct` from DB `score` as a fallback before live quote enrichment succeeds.

### 3. Data Application

Command:

```bash
DB_PATH=data/barro_trade.db venv/bin/python - <<'PY'
import asyncio
from backend.db.database import init_db
from backend.core.themes.finup_importer import import_finup_theme_snapshot

async def main():
    await init_db()
    result = await import_finup_theme_snapshot(replace=True)
    print(result)

asyncio.run(main())
PY
```

Result:

```text
theme_count=30
stock_count=1908
stock_names_changed=1218
source=https://finance.finup.co.kr/lab/themelog
collected_at=2026-07-08T00:30:40+09:00
```

### 4. Runtime Backend

- Stopped the old backend process that was serving `.claude/worktrees/tima-dashboard-p0`.
- Started the main repository backend on `http://127.0.0.1:8000` with `DB_PATH=data/barro_trade.db`.
- Running tmux session: `barro-backend-main-8000`.
- Frontend remains running on `http://localhost:3001/themes`.

## Verification Results

### Compile

```bash
venv/bin/python -m compileall backend/core/themes/finup_importer.py backend/api/routes/themes_calendar_news.py
```

Result: exit code 0.

### Import Endpoint

```bash
curl -s -X POST 'http://localhost:8000/api/themes/import-finup?replace=true'
```

Result:

```text
status=ok
theme_count=30
stock_count=1908
stock_names_changed=0
```

### API Verification

```bash
curl -s http://localhost:8000/api/themes
```

Confirmed:

```text
30 themes
2차전지, 3D 낸드, AI(인공지능), AI반도체, DDR5, D램, LED 장비...
```

Database verification:

```text
themes=30
theme_stocks=1908
first inserted themes: D램, DDR5, 3D 낸드, 방산, 비메모리 반도체
```

Theme stock verification:

```text
/api/themes/31/stocks
D램: HLB이노베이션, LB세미콘, ISC, 원익머트리얼즈, 티에스이...
```

### Playwright Verification

Opened `http://localhost:3001/themes`.

Confirmed:

- Finup themes visible: `D램`, `DDR5`, `3D 낸드`, `방산`, `비메모리 반도체`.
- Old `큐레이션 시드 기반` descriptions were no longer present.
- Screenshot saved to `frontend/test-results/theme-finup-data-applied.png`.

### Test Note

Command:

```bash
venv/bin/python -m pytest backend/tests/test_theme_snapshots.py backend/tests/test_screener_api.py::TestThemeStockBackwardCompat -q
```

Result: 7 passed, 2 failed.

Failures were in existing expectations that `_enrich_theme_stocks` is a no-op without a gateway. The current route already falls back to ReadOnly/cache quote enrichment, so prices are populated where tests expect `None`. This is separate from the Finup importer path and should be handled as a test expectation/update task.
