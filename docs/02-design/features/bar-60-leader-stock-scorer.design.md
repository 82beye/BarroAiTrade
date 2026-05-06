# BAR-60 — 대장주 점수 알고리즘 Design

## §1 모델 (`backend/models/leader.py`)

```python
class StockMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    daily_volume: int = Field(ge=0)
    market_cap: Decimal = Field(ge=0)

class LeaderScore(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    theme_id: int
    score: float                       # [0, 1]
    components: dict[str, float]       # theme_match / embed_sim / volume_norm / cap_norm
```

## §2 LeaderStockScorer (`backend/core/themes/leader_picker.py`)

```python
class LeaderStockScorer:
    """
    가중합 점수 = theme_match * w_t + embed_sim * w_e + volume_norm * w_v + cap_norm * w_c
    기본: w_t=0.4, w_e=0.3, w_v=0.15, w_c=0.15 (sum=1.0)
    BAR-60b 그리드 서치로 갱신.
    """

    def __init__(self, weights: dict[str, float] | None = None):
        self._w = weights or {"theme": 0.4, "embed": 0.3, "volume": 0.15, "cap": 0.15}
        if abs(sum(self._w.values()) - 1.0) > 1e-6:
            raise ValueError("weights must sum to 1.0")

    def score(self, *, theme_match: float, embed_sim: float,
              volume_norm: float, cap_norm: float) -> float:
        w = self._w
        return (
            theme_match * w["theme"]
            + embed_sim * w["embed"]
            + volume_norm * w["volume"]
            + cap_norm * w["cap"]
        )

    async def select_leaders(
        self,
        theme_id: int,
        candidates: list[tuple[str, float, float, int, Decimal]],
            # (symbol, theme_match, embed_sim, daily_volume, market_cap)
        top_k: int = 5,
    ) -> list[LeaderScore]:
        """vol/cap min-max 정규화 후 score 계산. 상위 top_k."""
        if not candidates:
            return []
        vols = [c[3] for c in candidates]
        caps = [float(c[4]) for c in candidates]
        v_min, v_max = min(vols), max(vols)
        c_min, c_max = min(caps), max(caps)

        def norm(x, mn, mx):
            if mx == mn:
                return 1.0
            return (x - mn) / (mx - mn)

        results = []
        for sym, tm, es, vol, cap in candidates:
            vn = norm(vol, v_min, v_max)
            cn = norm(float(cap), c_min, c_max)
            s = self.score(theme_match=tm, embed_sim=es, volume_norm=vn, cap_norm=cn)
            results.append(LeaderScore(
                symbol=sym, theme_id=theme_id, score=s,
                components={
                    "theme_match": tm, "embed_sim": es,
                    "volume_norm": vn, "cap_norm": cn,
                },
            ))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]
```

## §3 12 테스트 매트릭스

- LeaderScore frozen + score [0,1] (2)
- Scorer 가중치 sum != 1.0 → ValueError (1)
- Scorer 기본 가중치 score 계산 (1)
- Scorer 커스텀 가중치 (1)
- select_leaders min-max 정규화 + 정렬 (3)
- select_leaders 빈 후보 (1)
- select_leaders top_k 제한 (1)
- StockMetrics frozen + Decimal market_cap (1)
- 가중치 fixture (테마별 다른 가중치) 결정성 (1)
