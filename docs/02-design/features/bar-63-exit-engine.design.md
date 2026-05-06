# BAR-63 — ExitEngine design

## §1 모델 (`backend/models/exit_order.py`)

```python
class ExitReason(str, Enum):
    TP1 = "tp1"; TP2 = "tp2"; TP3 = "tp3"
    STOP_LOSS = "stop_loss"
    TIME_EXIT = "time_exit"

class ExitOrder(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    qty: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    reason: ExitReason

class PositionState(BaseModel):
    """포지션 + ExitPlan 누적 상태 (frozen — ExitEngine 가 새 인스턴스 반환)."""
    model_config = ConfigDict(frozen=True)
    symbol: str
    entry_price: Decimal
    qty: Decimal       # 잔여 수량
    entry_time: datetime
    tp_filled: int = 0  # 0/1/2/3 — 발동된 TP 단계 수
    sl_at: Optional[Decimal] = None   # 현재 SL 가격 (breakeven 트리거 후 갱신)
```

## §2 ExitEngine (`backend/core/execution/exit_engine.py`)

```python
class ExitEngine:
    """ExitPlan + PositionState + current_price → 새 PositionState + list[ExitOrder]."""

    def evaluate(
        self,
        pos: PositionState,
        plan: ExitPlan,
        current_price: Decimal,
        now: datetime,
    ) -> tuple[PositionState, list[ExitOrder]]:
        """평가 순서:
        1. time_exit: now.time() >= plan.time_exit → 잔여 전량 청산 (ExitReason.TIME_EXIT)
        2. SL: current_price <= sl_at_effective → 잔여 전량 청산
        3. TP 단계: current_price >= tp.price 이고 tp_filled < idx+1 → tp.qty_pct * 초기 qty 청산
        4. breakeven_trigger: TP1 발동 후 sl_at = entry_price * (1 + breakeven_offset)
        """
```

`sl_at_effective`: pos.sl_at if not None else entry_price * (1 + plan.stop_loss.fixed_pct)

## §3 15 테스트
- ExitOrder + PositionState frozen + Decimal (3)
- TP1 단계 발동 (price + qty + reason) (1)
- TP1 + TP2 단계별 (1)
- 모든 TP 발동 (qty 누계 ≤ 초기 qty) (1)
- SL 발동 (전량 청산) (1)
- breakeven_trigger 후 SL 갱신 (1)
- time_exit 발동 (1)
- TP 와 SL 동시 조건 — TP 우선 (1)
- 미발동 (current_price 사이) (1)
- TP 가격 미달 — 발동 X (1)
- pos.sl_at None → fixed_pct 기반 계산 (1)
- empty take_profits (1)
- multiple time_exit 비교 (1)
