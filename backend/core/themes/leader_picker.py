"""BAR-60 — LeaderStockScorer.

가중합 점수 = theme_match * w_t + embed_sim * w_e + volume_norm * w_v + cap_norm * w_c
기본: 0.4 / 0.3 / 0.15 / 0.15 (BAR-60b 그리드 서치).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from backend.models.leader import LeaderScore


class LeaderStockScorer:
    """대장주 점수 계산기. weights sum == 1.0."""

    DEFAULT_WEIGHTS = {"theme": 0.4, "embed": 0.3, "volume": 0.15, "cap": 0.15}

    def __init__(self, weights: Optional[dict[str, float]] = None) -> None:
        w = weights or dict(self.DEFAULT_WEIGHTS)
        if set(w.keys()) != set(self.DEFAULT_WEIGHTS.keys()):
            raise ValueError(
                f"weights keys must be {set(self.DEFAULT_WEIGHTS.keys())}"
            )
        if abs(sum(w.values()) - 1.0) > 1e-6:
            raise ValueError("weights must sum to 1.0")
        self._w = w

    def score(
        self,
        *,
        theme_match: float,
        embed_sim: float,
        volume_norm: float,
        cap_norm: float,
    ) -> float:
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
        top_k: int = 5,
    ) -> list[LeaderScore]:
        """min-max 정규화 후 점수 계산 + 상위 top_k."""
        if not candidates:
            return []
        vols = [c[3] for c in candidates]
        caps = [float(c[4]) for c in candidates]
        v_min, v_max = min(vols), max(vols)
        c_min, c_max = min(caps), max(caps)

        def _norm(x: float, mn: float, mx: float) -> float:
            if mx == mn:
                return 1.0
            return (x - mn) / (mx - mn)

        results: list[LeaderScore] = []
        for sym, tm, es, vol, cap in candidates:
            vn = _norm(vol, v_min, v_max)
            cn = _norm(float(cap), c_min, c_max)
            s = self.score(
                theme_match=tm,
                embed_sim=es,
                volume_norm=vn,
                cap_norm=cn,
            )
            results.append(
                LeaderScore(
                    symbol=sym,
                    theme_id=theme_id,
                    score=s,
                    components={
                        "theme_match": tm,
                        "embed_sim": es,
                        "volume_norm": vn,
                        "cap_norm": cn,
                    },
                )
            )
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]


__all__ = ["LeaderStockScorer"]
