# 2026-05-13 운영 이상 신호 추적

**입력:** `logs/imports/2026-05-13/` (931KB tar.gz, 5/12+5/13 누적)
**작성:** 2026-05-13

## 요약

| # | 증상 | 근본 원인 | 상태 |
|---|------|----------|------|
| 1 | DCA 추가매수 시 `ValueError: qty must be > 0` 2건 | DCA 분할 비율 × 보유 1주 floor = 0주 | ✅ **본 커밋에서 게이트 레이어 추가 차단** + main 9d04656 호출자 가드 |
| 2 | `order_audit.csv` FAILED HTTPStatusError 3건 | 키움 OpenAPI 일시 오류·rate limit | 🟡 관찰 (이미 main b4d2ad5에서 429 retry/backoff 추가) |
| 3 | `server.log` `KIWOOM_APP_KEY` KeyError 반복 | backend.api 시작 시 `.env` 미로드 | 🟡 별도 작업 (운영 wrapper 점검) |
| 4 | 012200 계양전기 SL -4.20% 발동 | DCA 1차 진입 직후 하락 | 🟢 정상 동작 (SL 매도) — 분할 첫 비중 정책 회고 가치 |
| 5 | telegram_bot.err 5,345줄 `poll cycle failed: HTTPStatusError` | 외부 의존성 장애 | 🟡 관찰 |

---

## 1. DCA qty=0 ValueError (메인 이슈) 🔴→✅

### 증상 (`data/order_audit.csv` 27, 29행)

```
2026-05-13T01:00:03+00:00, FAILED, buy, 012860, 0, MKT, ,, 0, ValueError
2026-05-13T01:00:05+00:00, FAILED, buy, 356680, 0, MKT, ,, 0, ValueError
```

### 추적: 발생 지점 → 호출 경로

1. **executor 단**: `backend/core/gateway/kiwoom_native_orders.py:175-176`
   ```python
   if qty <= 0:
       raise ValueError(f"qty must be > 0, got {qty}")
   ```
2. **gate 단**: `backend/core/risk/live_order_gate.py:_gated()` — 종전에는 qty 검증 없이 executor 호출 → 위 ValueError 가 catch 되어 audit 에 `FAILED, ..., reason=ValueError` 기록.
3. **호출자 단**: `scripts/evaluate_holdings.py` DCA 분할 — pending tranche.qty 를 그대로 전달.

### 근본 원인

`data/active_positions.json` 엑스게이트(356680):
```json
"tranches": [
  {"tranche": 1, "ratio": 0.5,  "qty": 1, "status": "filled"},
  {"tranche": 2, "ratio": 0.25, "qty": 0, ...},   ← qty=0 (DCA 비율 × 보유 1주 = 0.25주 → floor=0)
  {"tranche": 3, "ratio": 0.25, "qty": 1, ...}    ← floor 처리로 1주
]
```

총 추천 2주를 3분할(50/25/25)하면 1·0·1 주로 떨어짐. **두 번째 분할이 qty=0 그대로 저장**됨.

### main 의 9d04656 (KST 10:10) 가 한 일

- `scripts/evaluate_holdings.py:104-107` 에 가드 추가:
  ```python
  for tranche in pending:
      if tranche.qty <= 0:
          continue
      ...
  ```
- 운영 머신은 5/13 KST 10:00 시점에 fix 적용 전 상태 → ValueError 발생.

### 본 커밋 (BAR-OPS-09) 이 추가하는 것 — 게이트 레이어 차단

호출자 측 가드만으로는 **새 호출자(다른 스크립트/봇 명령)가 같은 실수를 반복할 위험**. layered defense 차원에서 `LiveOrderGate._gated()` 입구에 사전 검증을 추가했다:

`backend/core/risk/live_order_gate.py`:
```python
# 신규 예외 — TradingDisabled 류와 같은 BLOCKED 카테고리
class InvalidOrderQty(ValueError):
    """qty ≤ 0 으로 주문 시도. executor 단의 ValueError 가 audit 에 'FAILED' 로
    남는 것을 방지하기 위해 게이트에서 'BLOCKED' 로 사전 차단한다."""

async def _gated(self, side, symbol, qty, price, daily_pnl_pct):
    if qty <= 0:
        err = InvalidOrderQty(f"qty must be > 0, got {qty}")
        self._audit("BLOCKED", side, symbol, qty, price, None, None,
                    blocked=True, reason=str(err))
        await self._notify_blocked(side, symbol, str(err))
        raise err
    ...
```

### 효과

| 시나리오 | 종전 | 본 커밋 |
|----------|------|---------|
| 호출자가 qty=0 전달 (현재 evaluate_holdings) | 가드로 skip → 0건 | 가드로 skip (변화 없음) |
| 호출자 가드 누락 (가상 신규 스크립트) | executor ValueError → **audit FAILED** + telegram **blocked 알림 누락** | gate BLOCKED + 명시적 reason + telegram 알림 |
| 의도적 qty=0 테스트 호출 | executor 도달 후 실패 | 즉시 차단 |

### 테스트 추가 (`backend/tests/risk/test_live_order_gate.py`)

3개 케이스 — 11/11 통과:
- `test_qty_zero_buy_blocked_before_executor` — qty=0 매수 → BLOCKED + executor 미호출
- `test_qty_negative_sell_blocked` — qty=-1 매도 → BLOCKED
- `test_qty_positive_passes` — qty=1 정상 통과 회귀 방지

---

## 2. HTTPStatusError 패턴 🟡

`order_audit.csv` 5/13 FAILED 3건:
- 01:00:02 buy 012200 273주 — DCA 2차 매수 직후
- 01:09:42 sell 356680 1주 — 매도 시도
- (telegram_bot.err 에는 같은 메시지 5,345번 반복)

main `b4d2ad5 fix: 매매 프로세서 안정화 — ... 429 retry, backoff` 가 이미 들어와 있어 BAR-OPS-09 머지된 코드에서는 재시도 적용 중. 추가 작업 불필요. 단 telegram_bot 의 poll cycle 은 retry/backoff 가 들어가도 외부 의존성 장애일 가능성이 커서 별도 관찰.

---

## 3. KIWOOM_APP_KEY KeyError (`server.log`) 🟡

```
2026-05-12 11:50:21 [ERROR] backend.api.routes.positions: 포지션 조회 실패: 'KIWOOM_APP_KEY'
... 반복
```

`scripts/run_bot_with_env.sh` 가 main 에 추가됐는데 (5/13 deploy), backend.api 서버 시작 시점에는 `.env.local` 이 로드되지 않은 상태로 보임. **본 커밋 범위 밖** — 운영 머신의 systemd/launchd 또는 docker-compose 시작 스크립트가 `.env` 를 export 하는지 점검 필요.

---

## 4. 계양전기 SL -4.20% 발동 🟢

- 13,470 매수 → 12,680 매도 (-4.20%)
- DCA 1/3 분할 + 추가 매수 → 1시간 내 SL 발동
- 코드는 의도대로 동작 — `policy.json` 의 `stop_loss_pct: -4.0` 임계 정상 적용
- 회고 가치: **DCA 첫 분할 50% 가 너무 크면 진입 직후 하락에서 SL 손실 증폭**. 첫 분할을 30%로 줄이고 트리거 폭을 좁히는 정책 검토 후보.

---

## 5. telegram_bot poll cycle 장애 🟡

5,345줄 모두 `poll cycle failed: HTTPStatusError — retrying in 60s`. 단일 메시지 반복 → exponential backoff 가 안 걸리거나 60s 고정 retry. 외부 API(텔레그램 측) 장애일 가능성 + 로깅 시 빈도 조절 부재.

후보 작업: `scripts/run_telegram_bot.py` 또는 polling 루프에 `consecutive_failures` 카운터 + 1시간 cool-down 도입. 본 커밋 범위 밖.

---

## 변경 파일 (본 커밋)

| 파일 | 변경 |
|------|------|
| `backend/core/risk/live_order_gate.py` | `InvalidOrderQty(ValueError)` 추가 + `_gated()` 진입 시 qty≤0 BLOCKED 처리 |
| `backend/tests/risk/test_live_order_gate.py` | 회귀 테스트 3건 추가 |
| `analysis/imports/2026-05-13/BUG_TRACE.md` | 본 분석 보고서 |

## 검증

```bash
cd /Users/beye/workspace/BarroAiTrade/.claude/worktrees/strange-jackson-3c740a
./.venv/bin/pytest backend/tests/risk/test_live_order_gate.py -v
# 11/11 통과
```
