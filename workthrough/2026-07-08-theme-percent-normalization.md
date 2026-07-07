# Theme Percent Normalization

## Overview

Updated the TIMA theme detail screen so displayed stock move percentages can use the daily limit scale. Raw `change_pct` values remain unchanged for sorting and calculations, and the theme list remains on the original raw percentage display.

## Context

The requested UI behavior was clarified after the initial change: the theme list should keep raw market change percentages, while the daily-limit scale remains available for views that explicitly need `+30% -> +100%` and `-30% -> -100%`. The theme card stock bar itself should follow the TIMA reference image: a centered `-30% | 0% | +30%` track, not a one-sided progress bar. The solid range lines must represent each stock's own intraday high/low move, not the theme-wide highest/lowest stock.

## Changes Made

### 1. Shared Formatter

File: `frontend/lib/change-percent.ts`

- Added `toDailyLimitScalePct`.
- Added `formatDailyLimitScalePct`.
- Clamps normalized display values to `-100%` through `+100%`.

### 2. Theme Card Display

File: `frontend/components/themes/theme-card.tsx`

- Theme header percentage remains the original raw `change_pct`-based percentage.
- Theme header total traded value and stock row traded value now display as integer `억` units without decimal places.
- Theme card header now uses a single row for `테마명 / 등락률 / 거래대금` instead of putting the metrics on a second row.
- Stock percentage rows inside theme cards remain the original raw `change_pct` display.
- Stock bar rows now use a centered daily-limit scale where `-30% = 0%`, `0% = 50%`, and `+30% = 100%` on the track.
- Positive stocks render a red bar from the center line to the right.
- Negative stocks render a blue bar from the center line to the left.
- The center black vertical line marks `0%`.
- Thin horizontal solid lines now start from the `0%` center line and extend to the stock-local intraday high/low move positions on the same centered scale.
- The highest-gain line uses the 상승 red color and the deepest-loss line uses the 하락 blue color.
- Intraday high/low change rates are derived from `price`, `change_pct`, `day_high`, and `day_low` by reversing the previous close from the current price and current change percentage.
- Stock row highlight background now applies only near daily limit moves (`>= +29%` or `<= -29%`) instead of any `>= +8%` gain.
- Sorting, surge detection, and raw data handling remain based on original `change_pct`.

### 3. Theme Detail Table

File: `frontend/app/(tima)/themes/[id]/page.tsx`

- Detail table `등락률` cells now use the normalized daily-limit scale.
- Detail table `대금(억)` cells now display integer `억` units without decimal places.

### 4. Signals Table

File: `frontend/app/(tima)/signals/page.tsx`

- Screener table traded value now displays integer `억` units without decimal places.

## Verification Results

```bash
npx tsc --noEmit
```

Result: exit code 0.

The running Next dev server on `http://localhost:3001/themes` recompiled and responded normally.

Additional Playwright verification:

- `반도체 > 402340` displayed `↑ 6.52%`.
- API source for `402340` was `change_pct=6.52`.
- Rendered centered bar coordinates were `left=50.00`, `width=10.87`, `end=60.87`, matching `50 + (6.52 / 30 * 50)`.
- `원익IPS` displayed `↑ 0.93%`.
- `원익IPS` derived previous close was approximately `161,696.23`, with intraday high move `+5.63%` and intraday low move `-1.05%`.
- `원익IPS` rendered centered bar coordinates were `left=50.00`, `width=1.55`, `end=51.55`.
- `원익IPS` high solid line ended at `59.38` with `9.38` width from the `0%` center line; the low solid line started at `48.25` with `1.75` width to the `0%` center line.
- Playwright confirmed the positive solid line rendered as `rgb(208, 0, 16)` and the negative solid line rendered as `rgb(32, 96, 192)`.
- `원전 > LS ELECTRIC` displayed `↓ 5.10%`.
- Rendered centered bar coordinates were `left=41.50`, `width=8.50`, `end=41.50`, matching `50 - (5.10 / 30 * 50)`.
- The centered `0%` line remained `50.00`.
- `LS ELECTRIC` high solid line ended at `56.53` from intraday high `+3.92%`; low solid line started at `40.85` from intraday low `-5.49%`.
- Screenshot saved to `frontend/test-results/theme-stock-intraday-range-lines.png`.

Additional integer `억` verification:

- Theme card showed `293,078억`, `79억`, `19,818억`, and `2,546억`.
- Theme detail table showed `79`, `19,818`, and `2,546` in `대금(억)` cells.
- Playwright confirmed no `\d+\.\d+억` text remained on the theme card.
- Screenshots saved to `frontend/test-results/theme-eok-integer-card.png` and `frontend/test-results/theme-eok-integer-detail.png`.

Additional single-row theme header verification:

- `반도체` header rendered as `반도체 / +7.88% / 293,078억` on one row.
- Playwright confirmed all three header children shared the same vertical center.
- Playwright confirmed `반도체` was not visually truncated (`clientWidth=63`, `scrollWidth=63`).
- Screenshot saved to `frontend/test-results/theme-header-single-row-fit.png`.

Additional limit-highlight verification:

- `삼성전기 +8.27%`, `프리티 +12.32%`, and `아이에스티이 +2.46%` rendered without highlight background or corner flag.
- `강동씨앤엘 +30.00%` and `서산 +29.96%` rendered with the 상한가 highlight background and corner flag.
- Screenshot saved to `frontend/test-results/theme-limit-highlight-only.png`.

Additional market clock update:

- `frontend/components/tima/tima-shell.tsx` now renders the top clock as a Korean regular-market clock.
- During regular market hours (`09:00 <= now < 15:30`, KST weekdays), it displays the current KST time.
- After market close, weekends, and pre-open periods, it displays the most recent regular-session close time (`15:30`) on the latest weekday.
- Playwright verified:
  - Current off-hours display: `07-07(화) 15:30`.
  - Mocked market-open time: `07-08(수) 10:12`.
  - Mocked after-close time: `07-08(수) 15:30`.
  - Mocked Monday pre-open time: `07-03(금) 15:30`.
- Screenshot saved to `frontend/test-results/tima-market-clock-current.png`.
