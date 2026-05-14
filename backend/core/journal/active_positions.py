"""활성 포지션 메타 정보 영속화.

매수 주문 시 전략·진입가·분할 정보를 JSON 파일로 저장.
evaluate_holdings 에서 로드 → DCA 트리거 판단 + 전략 기반 손절 기준 적용.

구조:
  data/active_positions.json
  {
    "319400": {
      "symbol": "319400",
      "name": "현대무벡스",
      "strategy": "swing_38",
      "entry_price": 43300,
      "entry_time": "...",
      "total_recommended_qty": 339,
      "sl_pct": -4.0,
      "flu_rate": 11.89,
      "score": 0.865,
      "tranches": [
        {"tranche": 1, "ratio": 0.5, "qty": 170, "trigger_drop_pct": 0.0,
         "status": "filled", "order_no": "0128139", "filled_price": 43300, "filled_at": "..."},
        {"tranche": 2, "ratio": 0.25, "qty": 85, "trigger_drop_pct": -2.0,
         "status": "pending", "order_no": "", "filled_price": 0.0, "filled_at": ""},
        {"tranche": 3, "ratio": 0.25, "qty": 84, "trigger_drop_pct": -4.0,
         "status": "pending", "order_no": "", "filled_price": 0.0, "filled_at": ""}
      ]
    }
  }
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class Tranche:
    tranche: int              # 1, 2, 3
    ratio: float              # 0.5 / 0.25 / 0.25
    qty: int
    trigger_drop_pct: float   # 0.0(즉시), -2.0(2% 하락 시), -4.0(4% 하락 시)
    status: str               # pending / filled / skipped
    order_no: str = ""
    filled_price: float = 0.0
    filled_at: str = ""


@dataclass
class ActivePosition:
    symbol: str
    name: str
    strategy: str             # swing_38 등 시뮬에서 채택된 전략
    entry_price: float        # 1분할 진입가
    entry_time: str           # ISO 8601
    total_recommended_qty: int
    sl_pct: float = -4.0
    flu_rate: float = 0.0
    score: float = 0.0
    tranches: list[Tranche] = field(default_factory=list)
    # 적응형 매도 정책용 추적 필드
    peak_pnl_rate: float = 0.0            # 보유 기간 중 최고 수익률
    peak_updated_at: str = ""             # peak 갱신 시점
    partial_tp_done: bool = False         # 1차 분할 익절 완료 여부

    # ── 계산 헬퍼 ───────────────────────────────────────────────

    def avg_filled_price(self) -> float:
        filled = [t for t in self.tranches if t.status == "filled" and t.filled_price > 0]
        if not filled:
            return self.entry_price
        total_value = sum(t.filled_price * t.qty for t in filled)
        total_qty = sum(t.qty for t in filled)
        return total_value / total_qty if total_qty > 0 else self.entry_price

    def filled_qty(self) -> int:
        return sum(t.qty for t in self.tranches if t.status == "filled")

    def pending_tranches(self) -> list[Tranche]:
        return [t for t in self.tranches if t.status == "pending"]

    def sl_price(self) -> float:
        """평균 매수가 기준 SL 가격."""
        return self.avg_filled_price() * (1 + self.sl_pct / 100)

    def best_strategy(self) -> str:
        return self.strategy


def _build_tranches(total_qty: int, entry_price: float, order_no: str, filled_at: str) -> list[Tranche]:
    """3분할 트랜치 생성 — 1분할 50%, 2분할 25%, 3분할 25%."""
    q1 = round(total_qty * 0.5)
    q2 = round(total_qty * 0.25)
    q3 = total_qty - q1 - q2   # 나머지 전부 3분할로

    now = filled_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [
        Tranche(
            tranche=1, ratio=0.5, qty=q1,
            trigger_drop_pct=0.0, status="filled",
            order_no=order_no, filled_price=entry_price, filled_at=now,
        ),
        Tranche(
            tranche=2, ratio=0.25, qty=q2,
            trigger_drop_pct=-2.0, status="pending",
        ),
        Tranche(
            tranche=3, ratio=0.25, qty=q3,
            trigger_drop_pct=-4.0, status="pending",
        ),
    ]


class ActivePositionStore:
    """JSON 파일 기반 활성 포지션 영속."""

    def __init__(self, path: str | Path = "data/active_positions.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict[str, ActivePosition]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        result: dict[str, ActivePosition] = {}
        for symbol, d in data.items():
            tranches = [Tranche(**t) for t in d.get("tranches", [])]
            pos = ActivePosition(
                symbol=d["symbol"],
                name=d.get("name", ""),
                strategy=d.get("strategy", ""),
                entry_price=float(d.get("entry_price", 0)),
                entry_time=d.get("entry_time", ""),
                total_recommended_qty=int(d.get("total_recommended_qty", 0)),
                sl_pct=float(d.get("sl_pct", -4.0)),
                flu_rate=float(d.get("flu_rate", 0)),
                score=float(d.get("score", 0)),
                tranches=tranches,
            )
            result[symbol] = pos
        return result

    def save_all(self, positions: dict[str, ActivePosition]) -> None:
        data = {symbol: asdict(pos) for symbol, pos in positions.items()}
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def upsert(self, pos: ActivePosition) -> None:
        all_pos = self.load_all()
        all_pos[pos.symbol] = pos
        self.save_all(all_pos)

    def remove(self, symbol: str) -> None:
        all_pos = self.load_all()
        all_pos.pop(symbol, None)
        self.save_all(all_pos)

    def get(self, symbol: str) -> Optional[ActivePosition]:
        return self.load_all().get(symbol)

    def create_from_order(
        self,
        symbol: str,
        name: str,
        strategy: str,
        entry_price: float,
        total_recommended_qty: int,
        order_no: str,
        sl_pct: float = -4.0,
        flu_rate: float = 0.0,
        score: float = 0.0,
    ) -> ActivePosition:
        """매수 주문 직후 호출 — 3분할 트랜치 생성 후 저장."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tranches = _build_tranches(total_recommended_qty, entry_price, order_no, now)
        pos = ActivePosition(
            symbol=symbol, name=name, strategy=strategy,
            entry_price=entry_price, entry_time=now,
            total_recommended_qty=total_recommended_qty,
            sl_pct=sl_pct, flu_rate=flu_rate, score=score,
            tranches=tranches,
        )
        self.upsert(pos)
        return pos


__all__ = ["ActivePosition", "ActivePositionStore", "Tranche"]
