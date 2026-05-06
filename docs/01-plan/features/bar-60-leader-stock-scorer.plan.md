# BAR-60 — 대장주 점수 알고리즘 (Phase 3 다섯 번째)

**선행**: BAR-58a (Embedder + search_similar) ✅ / BAR-59a (theme_stocks) ✅
**후행**: BAR-61 (일정 캘린더), BAR-62 (프론트)

## §0 분리 정책

| BAR | 트랙 | 산출물 |
|-----|------|--------|
| **BAR-60a** | worktree | LeaderStockScorer + theme 단위 점수 계산 + 단위 테스트 |
| **BAR-60b** | 운영 | 월 1회 그리드 서치 cron + 실 거래량/시총 데이터 + 백테스트 정확도 ≥ 60% |

## §1 목적

theme_stocks (BAR-59) + embeddings (BAR-58) + 거래량/시총 fixture 결합 → 테마별 상위 종목 선정.

## §2 FR

- `LeaderStockScorer`: 가중합 점수 = `theme_match * w_theme + embed_sim * w_embed + volume_norm * w_vol + market_cap_norm * w_cap`
- 기본 가중치: 0.4 / 0.3 / 0.15 / 0.15 (운영 그리드 서치)
- `select_leaders(theme_id, top_k=5)` — 상위 N 종목 반환
- StockMetricsRepository — 거래량/시총 fixture (운영 시 KIS API)
- 단위 테스트 ≥ 12

## §3 NFR
- 회귀 ≥ 369 passed (357 + 12), coverage ≥ 70%
- 결정성 (가중치 fixture)

## §4 OOS
- 그리드 서치 (BAR-60b)
- 실 KIS API (BAR-60b)
- 백테스트 (BAR-60b)

## §5 DoD
- 12+ tests, 회귀 0 fail, gap ≥ 90%

## §6 다음
`/pdca design BAR-60` (단일 leader 패턴 — 단순 BAR 이므로 council 생략).
