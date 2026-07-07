# 코드베이스 사실 (BAR-OPS-09 Phase 1 적용 시 반드시 숙지)

원안 계획에서 일부 파일 참조가 실제와 달랐다. 아래는 원격 세션에서 탐색·검증으로 확정한 사실. Phase 1 코드 본문(`references/files/`) 은 이미 이 사실들을 반영해 작성돼 있으므로 그대로 적용하면 된다.

## 1. kt00009 (당일 체결 조회)

| 잘못된 가정 | 실제 |
|---|---|
| `core/gateway/kiwoom_native_*` 에 있음 | **`backend/legacy_scalping/execution/kiwoom_api.py`** 의 `KiwoomRestAPI.get_order_executions(sell_tp, qry_tp, ord_dt, stk_cd)` |
| native gateway 호출 | native gateway 는 kt00018(잔고)/kt00001(예수금)/kt00004(주문체결) 만 — kt00009 는 legacy 만 |

**Import 경로 (`01-daily-evening-pipeline.py` `fetch_executions_live` 에 이미 반영됨)**:

```python
legacy_root = _REPO_ROOT / "backend" / "legacy_scalping"
if str(legacy_root) not in sys.path:
    sys.path.insert(0, str(legacy_root))
from execution.kiwoom_api import KiwoomRestAPI  # 'backend.legacy_scalping.execution...' 아님
```

생성자 + 자격증명:
```python
api = KiwoomRestAPI({"mode": "real" or "simulation", "kiwoom": {}})
# env: KIWOOM_APP_KEY / KIWOOM_APP_SECRET / KIWOOM_ACCOUNT_NO
await api.get_order_executions(ord_dt="20260521", qry_tp="1")
```

## 2. order_audit.csv 한계

원안 계획에서는 `order_audit.csv` 로 전략별 net 추적 가능하다고 가정. 실제 컬럼:

```
ts, action, side, symbol, qty, price, order_no, return_code, blocked, reason
```

**전략·실현손익 컬럼 없음** — 그래서 `_strategy_perf_track.py` 의 입력은 신규 ledger CSV(`analysis/strategy_ledger.csv`) 다. `_daily_evening_pipeline.py` 가 그 ledger 를 매일 idempotent 하게 갱신한다.

## 3. IntradaySimulator

- 메서드: `IntradaySimulator().run(candles, symbol, strategies)` — 원안의 `best_pnl` 메서드는 없음
- 반환: `SimulationResult` — `.trades` (list), `.pnl_by_strategy` (dict), `.win_rate_by_strategy` (dict)
- `trade` 객체 필드: `side`, `strategy_id`, `timestamp`, `price`, `reason`
- 헬퍼: `backend.core.backtester._build_strategies(ids)` — 전략 인스턴스 lazy import (loss drill-down 의 진입 시그널 재검증에 사용)

## 4. 전략별 ExitPlan 위치

원안: `holding_evaluator.py` 의 `STRATEGY_EXIT_PROFILES` — **존재하지 않음**.

실제 위치: `backend/core/backtester/intraday_simulator.py`
- `_exit_plan_for_strategy(strategy_id)` — 표준 전략 (f_zone, swing_38, gold_zone 등)
- `_scaled_exit_plan(...)` — 단계별 분할 청산
- `_sfzone_atr_exit_plan(...)` — sf_zone 의 ATR 기반 청산

Phase 6 (청산 정교화) 에서 튜닝할 대상.

## 5. 부재 파일

원안 일부 참조 파일이 실제로 없음 — 후속 보정 필요:
- `short_term_high_exit.py` 없음
- `scripts/intraday_buy_daemon.py` 없음

Phase 1 에서는 이 파일들을 건드리지 않으므로 무관.

## 6. 재사용 자산 (신규 작성 금지)

| 자산 | 경로 | 용도 |
|---|---|---|
| `ActivePositionStore` | `backend/core/journal/active_positions.py` | `load_all()` → `dict[symbol, ActivePosition]`. `ActivePosition.strategy` 가 전략 귀속 1순위. 단, 청산된 종목은 `evaluate_holdings.py` 가 `remove()` 하므로 누락 가능 — 그래서 다단계 fallback (logs → IntradaySimulator → unknown) |
| `load_csv_candles` | `backend.core.backtester` export | CSV 컬럼 `timestamp,open,high,low,close,volume`, timestamp `%Y-%m-%d %H:%M:%S` |
| `IntradaySimulator` | `backend.core.backtester` export | drill-down 시뮬 청산 비교, Tier 3 전략 귀속 |
| `_build_strategies` | `backend/core/backtester/intraday_simulator.py` 내부 함수 | drill-down 진입 시그널 재검증 |
| `AnalysisContext` | `backend/models/strategy.py` | `(symbol, candles, market_type)` |
| `OHLCV` / `MarketType` | `backend/models/market.py` | 캔들 모델. `OHLCV.high/low/close/open` 은 **float** — 함정 §2 |
| `EntrySignal` | `backend.models.strategy` (strategy.analyze 반환) | `.score`, `.signal_type` |

## 7. `data/` 는 `.gitignore` 됨

- `active_positions.json`, `order_audit.csv` 등 런타임 파일은 운영 머신 zip 에 담겨 전달
- 본 스킬이 추가하는 `.gitignore` 4줄(`analysis/imports/`, `analysis/strategy_ledger.csv`, `analysis/strategy_perf.csv`, `analysis/strategy_perf.png`) 도 동일한 런타임 산출물 규칙

## 8. pytest 설정

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["backend/tests"]
pythonpath = ["."]
asyncio_mode = "auto"
```

새 테스트 파일은 `backend/tests/test_daily_pipeline.py` (`testpaths` 안). `pythonpath=["."]` 덕분에 `from scripts._daily_evening_pipeline import ...` 가능 — 단, `scripts/__init__.py` 가 있어야 함 (worktree 에 이미 존재).
