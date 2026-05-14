"""BAR-OPS-31 — 정책 config JSON 영속.

simulate_leaders / evaluate_holdings 의 기본값을 파일에서 로드.
/tune apply 명령으로 추천값 자동 반영.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class PolicyConfig:
    min_score: float = 0.5
    stop_loss_pct: float = -4.0
    take_profit_pct: float = 5.0
    max_per_position: float = 0.30
    max_total_position: float = 0.90
    daily_loss_limit: float = -3.0
    daily_max_orders: int = 50
    # 적응형 매도 정책
    trailing_start_pct: float = 3.0       # 트레일링 시작 수익률 (%)
    trailing_offset_pct: float = 1.5      # 고점 대비 하락 허용폭 (%)
    breakeven_trigger_pct: float = 2.5    # 브레이크이븐 전환 수익률 (%)
    partial_tp_pct: float = 3.5           # 1차 분할 익절 기준 (%)
    partial_tp_ratio: float = 0.5         # 1차 익절 매도 비율
    hold_days_tighten: int = 5            # N일 이상 보유 시 SL 강화
    tightened_sl_pct: float = -2.0        # 장기 보유 시 강화 SL (%)
    history: list[dict] = field(default_factory=list)         # 변경 이력

    def as_dict(self) -> dict:
        return asdict(self)


class PolicyConfigStore:
    """JSON 파일 기반 정책 영속."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> PolicyConfig:
        if not self.path.exists():
            return PolicyConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return PolicyConfig()
        # known fields only — forward compat
        cfg = PolicyConfig()
        for k in cfg.__dataclass_fields__:
            if k in data:
                setattr(cfg, k, data[k])
        return cfg

    def save(self, cfg: PolicyConfig) -> None:
        self.path.write_text(
            json.dumps(cfg.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def apply(
        self, recommendations: list, *, source: str = "tune",
    ) -> tuple[PolicyConfig, list[dict]]:
        """추천 list[PolicyRecommendation] 반영 + history append.

        반환: (new_cfg, applied_changes).
        """
        cfg = self.load()
        applied: list[dict] = []
        for r in recommendations:
            if not hasattr(cfg, r.field):
                continue
            old = getattr(cfg, r.field)
            new = r.recommended
            if old == new:
                continue
            setattr(cfg, r.field, new)
            applied.append({
                "field": r.field, "old": old, "new": new,
                "reason": r.reason, "severity": r.severity,
            })
        if applied:
            cfg.history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": source,
                "changes": applied,
            })
            # history 최근 50건만 유지
            cfg.history = cfg.history[-50:]
            self.save(cfg)
        return cfg, applied


__all__ = ["PolicyConfig", "PolicyConfigStore"]
