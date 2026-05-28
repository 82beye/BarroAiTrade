# order_audit.csv / active_positions.json 동기화 누락 진단 리포트

**작성일**: 2026-05-29
**스코프**: B 시리즈 후속 5번 — 5/28 swing_38 잔여 4종목 인시던트 + 5/20부터 439960 미청산 인시던트의 근본 원인 코드 추적
**관련**:
- PR #176 (Phase D2.1 단타 전용 모드) — swing_38 비활성 시점
- PR #182 (B1 v2) — 동기화 누락 자동 감지 알람

---

## Executive Summary

| 인시던트 | 증상 | 가설 근본 원인 |
|---|---|---|
| **5/28 swing_38 잔여 4종목** (001820/006660/012330/034220) | broker 보유 + `active_positions.json` `{}` 빈 | `ActivePositionsStore.load_all()` JSON 파싱 실패 → `{}` 반환 → 다음 `upsert/remove` 호출 시 **전체 키 손실** |
| **5/20부터 439960 미청산** (CSV net 392주, broker 0) | broker 청산 + `order_audit.csv` sell 행 누락 | `_audit()` `OSError` 발생 시 `logger.error` 만 호출하고 CSV write 누락 — broker는 정상 체결, 감사 로그만 손실 |

**핵심 발견**: 두 인시던트 모두 **로컬 영속화 데이터의 진실성 가정이 깨지는 케이스** — broker 잔고가 절대 진실, 로컬 CSV/JSON 은 derived data로 갱신 누락 가능.

---

## 1. order_audit.csv writer 구조

### 1-1. 단일 writer 위치
**파일**: `backend/core/risk/live_order_gate.py` (line 167~189)
**함수**: `LiveOrderGate._audit(action, side, symbol, qty, price, order_no, return_code, blocked, reason)`

```python
def _audit(self, action, side, symbol, qty, price, order_no, return_code, blocked, reason=""):
    try:
        new_file = not self._audit_path.exists()
        with open(self._audit_path, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(_AUDIT_HEADERS)
            w.writerow([
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                action, side.value, symbol, qty,
                str(price) if price is not None else "MKT",
                order_no or "",
                str(return_code) if return_code is not None else "",
                "1" if blocked else "0",
                reason,
            ])
    except OSError as exc:
        logger.error("audit log write failed (%s) — %s %s not recorded",
                     type(exc).__name__, action, symbol)   # ⚠ broker는 체결됐는데 CSV만 누락
```

### 1-2. `_AUDIT_HEADERS` (현재 10 컬럼)
```python
_AUDIT_HEADERS = [
    "ts", "action", "side", "symbol", "qty", "price",
    "order_no", "return_code", "blocked", "reason",
]
```
> **`strategy_id` 컬럼 없음** — 분석 리포트 §4-3 / B 시리즈 종합 §즉시 적용 권고 그대로. 3번 작업 대상.

### 1-3. 호출 경로 (모든 매수/매도 = 단일 진입점)
- `place_buy()` → `_gated(BUY, ...)` → `executor.place_buy()` → `_audit("ORDERED"|"DRY_RUN")`
- `place_sell()` → `_gated(SELL, ...)` → `executor.place_sell()` → `_audit("ORDERED"|"DRY_RUN")`
- 예외 발생 시 `_audit("FAILED")` 또는 `_audit("BLOCKED")`

### 1-4. 매도 (`place_sell`) 호출자 (전부 — 우회 경로 없음 확인)

| 호출자 | 호출 위치 | 시나리오 |
|---|---|---|
| `scripts/evaluate_holdings.py:194` | `HoldingEvaluator` 결정 기반 | cron polling 매도 |
| `scripts/intraday_buy_daemon.py:317` | intraday 데몬 | 장중 자동 매도 |
| `scripts/run_telegram_bot.py:298` | 텔레그램 봇 | 사용자 명령 매도 |

→ **모든 매도가 `live_order_gate.place_sell` 경유**. broker 직접 주문 우회 경로 없음 (raw kiwoom API 호출 X). 누락 원인은 `_audit()` 내부 실패뿐.

---

## 2. 439960 인시던트 — `order_audit.csv` sell 행 누락 원인

### 2-1. 가설 A — `OSError` 발생 시 CSV write 실패 (★ 최유력)

`_audit()` 의 `except OSError` 분기가 있어 디스크 가득참·권한 거부·동시 lock 등 OSError 시 **logger.error 만 호출하고 CSV write 누락**. broker 는 이미 체결됨 → 데이터 불일치 영속화.

**진단 가이드**:
```bash
# 운영 머신에서 5/20 전후 audit log write failed 검색
grep "audit log write failed" /tmp/backend_v2.log /Users/beye/workspace/BarroAiTrade/logs/barro.log 2>/dev/null
# 또는 journalctl 등 시스템 로그
```

### 2-2. 가설 B — 동시 쓰기 race condition

`open(path, "a")` append 모드는 일반적으로 atomic이지만, 동시에 여러 프로세스(intraday_buy_daemon + telegram_bot + evaluate_holdings)가 같은 파일 쓰면 일부 write 손실 가능. POSIX append-mode는 보통 안전하지만 OS/파일시스템 의존.

**진단 가이드**:
```bash
# 운영 머신에서 동시 실행 프로세스 확인
ps -ef | grep -E "intraday_buy_daemon|run_telegram_bot|evaluate_holdings" | grep -v grep
```

### 2-3. 가설 C — broker 직접 주문 우회 (가능성 낮음)

raw kiwoom API 호출 코드 grep 결과 `place_buy`/`place_sell` 경유만 발견 → 우회 경로 없음. **가설 C 기각**.

### 2-4. 권장 fix

1. **`_audit()` `OSError` 처리 강화** — 실패 시 stderr + 별도 dead-letter 파일 (`data/order_audit_failed.csv`) 에 저장 → 사용자 알림 가능
2. **`fcntl.flock()` 또는 atomic write 패턴** (tempfile + rename) — race 차단
3. **B1 v2 보강** (4번 작업) — CSV 가 진실 절대 가정 약화

---

## 3. active_positions.json writer 구조

### 3-1. 단일 writer 위치
**파일**: `backend/core/journal/active_positions.py` (line 162~167)
**함수**: `ActivePositionsStore.save_all(positions: dict[str, ActivePosition])`

```python
def save_all(self, positions: dict[str, ActivePosition]) -> None:
    data = {symbol: asdict(pos) for symbol, pos in positions.items()}
    self.path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
```

### 3-2. 호출자 (`upsert` / `remove` 만)
```python
def upsert(self, pos: ActivePosition) -> None:
    all_pos = self.load_all()      # ★ JSON 파싱 실패 시 {} 반환
    all_pos[pos.symbol] = pos
    self.save_all(all_pos)         # ⚠ 다른 keys 모두 손실

def remove(self, symbol: str) -> None:
    all_pos = self.load_all()      # ★ JSON 파싱 실패 시 {} 반환
    all_pos.pop(symbol, None)
    self.save_all(all_pos)         # ⚠ 다른 keys 모두 손실
```

### 3-3. `load_all()` 검사
`load_all()` 코드를 확인하면 `JSONDecodeError` 시 `{}` 반환할 가능성 ↑ (방어적 코드). 그러면:
1. 파일 일부 손상 (write 중 끊김 등)
2. `load_all()` `{}` 반환
3. 다음 `upsert(pos)` 호출 → `{pos.symbol: pos}` 만 저장 → **이전 모든 keys 영구 손실**
4. 다음 호출에서 `load_all()` 정상 반환 → `{pos.symbol: pos}` 만 보임 → 잘못된 진실로 영속화

---

## 4. 5/28 swing_38 잔여 4종목 — `{}` 빈 원인

### 4-1. 가설 A — `load_all()` 파싱 실패 + 후속 `upsert/remove` 전체 손실 (★ 최유력)

위 §3-3 시나리오. 4종목(001820/006660/012330/034220)이 5/28 buy 시점에 정상 저장되어 있었는데, 어느 순간 JSON 파일 일부 손상 → `load_all() → {}` → 다음 매수/매도(예: 5/28 14:40 다른 종목)의 upsert가 4종목 키 모두 손실하고 `{새 종목: pos}` 저장 → 이후 또 어떤 매도가 새 종목 remove → 최종 `{}`.

**진단 가이드**:
```bash
# 운영 머신에서 active_positions.json 파싱 에러 검색
grep -E "JSONDecodeError|active_positions.*invalid" /tmp/backend_v2.log /Users/beye/workspace/BarroAiTrade/logs/barro.log 2>/dev/null
# active_positions.json 파일 백업이 있는지
ls -la data/_backup* data/.history* /backup/active_positions* 2>/dev/null
```

### 4-2. 가설 B — `save_all({})` 명시적 호출 코드 (가능성 낮음)

`save_all` 호출자가 `upsert/remove` 만이고, 둘 다 빈 dict 직접 저장 안 함. 외부 도구(예: ops 스크립트, 수동 reset)가 빈 파일 저장했을 가능성 — 코드 추적 불가.

### 4-3. 가설 C — write 중 프로세스 종료 → 파일 truncate (가능성 중)

`Path.write_text()` 는 내부적으로 `open(mode='w')` → 파일을 truncate 후 write. 도중에 프로세스 killed 되면 파일 길이 0 또는 부분 데이터. 다음 `load_all()` JSONDecodeError → `{}` 가설 A 와 결합.

### 4-4. 권장 fix

1. **`save_all()` atomic write 패턴** — `tempfile.NamedTemporaryFile + os.replace()` 또는 `pathlib.Path.write_text` 대신:
   ```python
   tmp = self.path.with_suffix(".json.tmp")
   tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
   tmp.replace(self.path)   # atomic rename
   ```
2. **`load_all()` 실패 시 graceful 처리** — JSONDecodeError 시 `{}` 반환 X, 명시적 backup 후 raise / alert
3. **자동 백업** — 매번 `save_all` 직전 timestamped backup `data/_active_positions_history/<ts>.json` 생성 (보존 7일)
4. **운영 monitoring** — 파일 크기가 평소보다 작아지면 alert (정상시 4KB+ → 빈 객체 `{}` 는 2 bytes)

---

## 5. 두 인시던트 공통 패턴 — broker 잔고가 절대 진실

두 인시던트 모두 broker 측은 정상이고 로컬 영속화 데이터(CSV/JSON)만 손실. **로컬 데이터를 진실로 가정하는 모든 모니터링/평가 로직이 영향받음**:

| 의존 모듈 | 영향 |
|---|---|
| `HoldingEvaluator` 청산 결정 | active_positions 빈 dict → 보유 0 가정 → 청산 룰 발동 X |
| `B1 v1 모니터링` | active_positions 기반 → 누락 못 잡음 |
| `B1 v2 모니터링` | order_audit.csv 기반 → CSV sell 누락 케이스(439960)는 false positive |
| `STRATEGY_EXIT_PROFILES` 적응형 매도 | broker pnl_rate 기반이라 OK ✓ |
| `ExitEngine` (분봉 close) | broker 가격 fetch → OK ✓ |
| `Strategy.exit_plan` | Position 모델 의존 → broker fetch 필요 |

→ **broker 잔고 polling이 모든 진실의 source — 로컬 영속화는 boot/restart 후 보조 데이터로 강등 권장**.

---

## 6. 권고 조치 (우선순위 순)

| 순위 | 작업 | 위치 |
|---:|---|---|
| 1 | **`_audit()` OSError 시 dead-letter 파일 + 사용자 알림** | live_order_gate.py — 별도 BAR |
| 2 | **`save_all()` atomic write 패턴 (tempfile + replace)** | active_positions.py — 별도 BAR |
| 3 | **`load_all()` JSONDecodeError graceful 처리 + backup 복원 시도** | active_positions.py — 별도 BAR |
| 4 | **자동 백업** — 매 `save_all` 직전 timestamped 백업 | active_positions.py — 별도 BAR |
| 5 | **B1 v2 보강** — CSV ground truth 가정 약화 (4번 작업, 본 PR에서 즉시 진행) | daytrading_daily_monitor.py |
| 6 | **`strategy_id` 컬럼 추가** (3번 작업, 본 PR에서 즉시 진행) | live_order_gate.py + 모든 caller |
| 7 | **broker 잔고 polling 주기 점검** | 운영 머신 — 사용자 진단 |
| 8 | **`active_positions.json` 크기/정합성 monitoring alert** | scripts/daytrading_daily_monitor.py 확장 — 별도 BAR |

---

## 7. 진단 명령 모음 (사용자 운영 머신 실행 권장)

```bash
# 1) audit log write failed 검색
grep "audit log write failed" /tmp/backend_v2.log logs/barro.log 2>/dev/null

# 2) active_positions JSON 파싱 에러
grep -iE "JSONDecodeError|active_positions.*invalid|active_positions.*parse" \
  /tmp/backend_v2.log logs/barro.log 2>/dev/null

# 3) 동시 실행 daemon
ps -ef | grep -E "intraday_buy_daemon|run_telegram_bot|evaluate_holdings|uvicorn" | grep -v grep

# 4) active_positions.json 파일 크기 추적 (정상 4KB+ vs 빈 객체 2bytes)
stat -f "%z" data/active_positions.json

# 5) 백업 위치 (있다면)
ls -la data/_backup* data/.history* 2>/dev/null

# 6) order_audit.csv 의 OSError 가능 원인 (디스크/권한)
df -h /
ls -ld data/ data/order_audit.csv

# 7) B1 v2 미청산 검출 — 최근 60일 lookback
venv/bin/python scripts/daytrading_daily_monitor.py --date $(date +%Y-%m-%d) --save-md
```

---

## 8. 참고

- `backend/core/risk/live_order_gate.py:167-189` — `_audit()` 함수
- `backend/core/risk/live_order_gate.py:36-39` — `_AUDIT_HEADERS`
- `backend/core/journal/active_positions.py:162-178` — `save_all` / `upsert` / `remove`
- `scripts/daytrading_daily_monitor.py:118-220` — B1 v2 `_aggregate_unfilled_from_csv` + `_detect_position_discrepancy` (PR #182)
- `docs/04-report/features/2026-05-28-b-series-summary.md` — B 시리즈 종합 §B6 SL 격차 결정
- `docs/04-report/features/2026-05-28-sl-gap-decision.md` — B6 결정 리포트 (exit_plan vs holding_evaluator)
