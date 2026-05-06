# BAR-60a Gap Analysis

**매치율**: 100% (8/8) PASS

| # | 항목 | 결과 |
|---|------|:---:|
| 1 | StockMetrics + LeaderScore (frozen + Decimal) | ✅ |
| 2 | LeaderStockScorer DEFAULT_WEIGHTS sum=1.0 | ✅ |
| 3 | weights 키 검증 (theme/embed/volume/cap) | ✅ |
| 4 | sum != 1.0 ValueError | ✅ |
| 5 | score 가중합 계산 | ✅ |
| 6 | select_leaders min-max 정규화 | ✅ |
| 7 | 정렬 + top_k | ✅ |
| 8 | 13 tests + 회귀 370 (≥369) + coverage | ✅ |

iterator 트리거 불필요. report 진행.
