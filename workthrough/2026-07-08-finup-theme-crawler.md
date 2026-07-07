# Finup Theme Crawler and Analysis

## Overview

Implemented a low-frequency collector for Finup theme-log data and documented the site's theme classification model. The collector creates a baseline dataset from public Finup endpoints covering top themes, related stocks, similar themes, news, and theme focus contents.

## Context

The requested source was `https://finance.finup.co.kr/lab/themelog`, with `https://finance.finup.co.kr/theme/3344` provided as a useful reference detail page. The page is client-rendered, so the implementation inspected Next.js static chunks to identify the public JSON and SSE endpoints used by the UI.

## Changes Made

### Collector

File: `scripts/finance/collect_finup_theme_data.py`

- Added a standard-library Python crawler.
- Uses `POST /api/radar/themelog/capture-chart` to collect the top 30 theme snapshot.
- For each theme, collects:
  - `GET /api/radar/theme/summary`
  - `GET /api/radar/theme/relation-stocks`
  - `GET /api/radar/theme/similarity`
  - `POST /api/radar/themelog/news`
  - `GET /api/finance/contents`
- Writes one integrated JSON file plus normalized CSVs.
- Adds inferred `type_stock_market_inferred` labels for `typeStock=1` and `typeStock=2`.
- Keeps requests sequential with a configurable sleep interval.

### Analysis Document

File: `docs/03-analysis/finup-theme-feature-analysis.md`

- Documented the Finup theme feature structure.
- Mapped UI sections to API endpoints.
- Explained the theme classification axes: ranking/treemap, realtime vs replay mode, detail summary, related stocks, similar themes, news, and focus content.
- Added a concrete `3344 시멘트/레미콘` example.
- Documented generated data files and quality caveats.

### Data Output

Latest generated files are indexed by:

```text
data/finup_theme/latest.json
```

The final snapshot timestamp is `20260708_003040`.

## Verification Results

Syntax check:

```bash
python3 -m py_compile scripts/finance/collect_finup_theme_data.py
```

Result: exit code 0.

Final data profile:

- Themes: 30
- Related stock rows: 1,908
- Similar theme rows: 120
- News rows: 150
- Focus content rows: 40
- Crawl errors: 0
- `3344 시멘트/레미콘`: 16 related stocks, 5 news rows, 2 focus content rows

## Notes

The generated `data/finup_theme/*` files are ignored by Git because `.gitignore` contains `data/*`. They remain available in the local workspace.

The current API responses return all stock volume/trade-value fields as `0`, so these fields should not be used directly as trading factors without another market data source.
