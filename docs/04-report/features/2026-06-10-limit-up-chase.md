# 상따(상한가 따라잡기) + 캡상승 수익극대화 매매전략

_작성: 2026-06-10 / 브랜치: `worktree-limit-up-chase` → `main`(머지 완료) / 커밋 `f61dcec`_

> **상태: 구현·검증 완료, 기본 비활성(OFF).** 운영 반영은 `.env.local` 토글 + dry_run 검증 후.

---

## 1. 배경 / 목적

운영 시스템은 그동안 **급등 추격 손실**을 겪어, 데몬·봇에 급등 진입을 막는 글로벌 가드를
겹겹이 쌓아왔다(`_MAX_FLU_RATE=30`, P10 시초가폭등 차단 `20%`, 고점인접 차단 `1.5%`).
이는 보수 전략(f/sf/gold/swing)에는 옳지만, **상한가 모멘텀을 의도적으로 추격**하는 전략과는
정반대다.

한편 "캡상승 수익극대화"(상한가 홀딩 → 수익잠금 → 고점되돌림 청산, 익일 시가갭 부분익절)는
이미 `supertrend_auto_trader.py`의 **RUNNER 기능(BAR-OPS-36)으로 구현돼 있으나 비활성** 상태였다.
즉 부족한 것은 **상따 '진입' 로직**이다.

**목표**: 보수 전략·글로벌 가드를 전혀 건드리지 않으면서, RUNNER 청산을 재사용하는
**분리된·env토글·기본 OFF·dry_run 우선** 상따 트레이더를 추가한다.

---

## 2. 설계 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 구현 형태 | `LimitUpChaseTrader(SupertrendAutoTrader)` **상속** | `run_cycle`만 override, 청산 헬퍼(self·bars 기반 순수 메서드)는 상속만으로 재사용 |
| 부모 파일 | `supertrend_auto_trader.py` **무수정** | supertrend·보수전략 무영향 보장 |
| 진입 | 모멘텀 밴드 + **호가 매수벽**(ka10004) | 상한가 직접 플래그·체결강도 TR 없음 → 등락률 근사 + 호가 잔량으로 가짜 급등 거름 |
| 청산 | 당일 RUNNER + **오버나잇 모드 전환** | 캡상승 = 기존 RUNNER 재사용, 점상한가는 익일 갭매도 |
| 실행 위치 | 봇 프로세스 내 **별도 async task** | 신규 서비스 미신설, OAuth 공유로 429 경합 최소화 |
| 포지션 | `strategy="limit_up_chase"` 태깅 | supertrend/zone과 상호 배타(더블셀 방지) |
| 리스크 | 기본 OFF · dry_run 우선 · 보수한도 | 가장 공격적 전략 → 단계적 검증 |

---

## 3. 진입 로직 (AND 조건)

상따 후보는 데몬 글로벌 가드와 **무관한 독립 경로**(상따 전용 picker)로 소싱한다.

### (A) 후보 소싱
- `KiwoomNativeLeaderPicker(min_flu_rate=LIMIT_UP_MIN_FLU)` — 등락률 하한을 상따용으로 높여
  급등 주도주만 수집. 3-factor(거래대금·등락률·거래량) 랭킹 + `min_score`.

### (B) 모멘텀 밴드 — `_momentum_band_pass`
- `entry_flu_min ≤ 등락률 ≤ entry_flu_max` (기본 **20~27%**).
- 상단(27%)으로 **이미 +30% 락을 찍은 종목의 추격은 차단**(고점 추격 손실 회피).
- 거래대금/거래량 급증은 picker 랭킹·min_score가 보장 → 추가 TR 호출 없음(429 절감).

### (C) 호가 매수벽 — `_passes_orderbook_wall` (핵심)
모멘텀 통과 후보만 `ka10004` 호가를 1회 온디맨드 조회. 다음 **모두** 충족 시 진입:

1. **상한가 근접 매수벽**: 매수1호가 ≥ `상한가가격 × (1 − wall_near_pct%)`
   (상한가가격 = 전일종가 × (1 + `runner_limit_up_pct`/100))
2. **매수1호가 절대 잔량**: ≥ `wall_min_top_qty` (기본 5만 주) — 두꺼운 벽
3. **매수/매도 잔량 비율**: top-N 매수합 / 매도합 ≥ `wall_bid_ask_ratio` (기본 3.0)
   — 매도 잔량 전무(상한가 락 임박) 시 통과

호가 미가용/조회 실패 시 **보수적으로 진입 보류(False)**.

### (D) 시간 게이트
- `entry_start_time`(기본 09:05) ~ `entry_end_time`(기본 14:00). 장막판 상한가 추격(익일갭
  리스크)을 차단.

---

## 4. 청산 로직 — RUNNER 재사용 + 오버나잇

부모 `_runner_*`/`_is_limit_up`/`_maybe_gap_partial`을 재사용한다(strategy_id만 limit_up_chase로 귀속).

### 우선순위 (매 사이클)
1. **익일 시가갭 부분익절**(오버나잇 보유 한정) → 일부 확정 후 잔량 런
2. **EOD 강제청산**(daily 모드, `eod_close_time` 이후)
3. **트레일청산**(샹들리에): 고점종가 − `trail_atr_mult`×ATR 이탈
4. **하드손절**: 진입가 대비 ≤ `hard_stop_pct` (상한가 깨짐 빠른 탈출)
5. **RUNNER 청산**: 상한가권이면 **홀딩**(안 판다), 수익잠금 floor 이탈 시 청산, 고점 되돌림 시 청산
6. **고정 익절**: 아직 runner 트리거 전(미상한가·미TP) 구간 보완

### 모드 (`LIMIT_UP_OVERNIGHT_MODE`)
- **`daily`**(기본): 상한가 락이라도 `eod_close_time`(기본 15:15)에 자체 강제청산 → 당일 정리.
- **`overnight`**: 상한가 락 종목을 익일로 보유 → 다음날 개장 초 시가갭에서 `gap_partial`
  비율만큼 확정, 잔량은 RUNNER로 런(점상한가 연속 노림).

---

## 5. 포지션 소유권 / 충돌 방지

| 지점 | 메커니즘 |
|---|---|
| 데몬 신규매수 회피 | 상따가 `active_positions.json`에 등록 → 데몬 `excluded`에 포함되어 같은 종목 미매수 |
| 상호 배타 청산 | 상따 청산은 `strategy.startswith("limit_up_chase")`만, supertrend는 `"supertrend"`만 → 더블셀 불가 |
| EOD 강제청산 방어 | `evaluate_holdings.py --exclude-strategy` 기본값에 `limit_up_chase` 추가(오버나잇 파괴·더블셀 방지) |

---

## 6. 환경 토글 (`.env.local`, 접두사 `LIMIT_UP_`)

기본 OFF — 키가 없으면 트레이더 미생성(보수전략·supertrend 완전 무영향).

| env | 기본 | 의미 |
|---|---|---|
| `LIMIT_UP_CHASE_ENABLED` | (unset=OFF) | 마스터 토글 |
| `LIMIT_UP_CHASE_DRYRUN` | 1(ON) | dry_run 선검증. 0 명시해야 실송출 |
| `LIMIT_UP_INTERVAL_SEC` | 90 | 사이클 주기 |
| `LIMIT_UP_UNIVERSE_TOP` | 15 | picker top_n |
| `LIMIT_UP_MIN_FLU` | 15 | picker 후보 등락률 하한 |
| `LIMIT_UP_ENTRY_FLU_MIN` / `_MAX` | 20 / 27 | 모멘텀 밴드(%) |
| `LIMIT_UP_WALL_NEAR_PCT` | 1.0 | 매수1호가 상한가 근접(%) |
| `LIMIT_UP_WALL_MIN_TOP_QTY` | 50000 | 매수1호가 절대 잔량(주) |
| `LIMIT_UP_WALL_BID_ASK_RATIO` | 3.0 | top-N 매수/매도 비율 |
| `LIMIT_UP_ENTRY_START` / `_END` | 09:05 / 14:00 | 진입 시간창 |
| `LIMIT_UP_MAX_POS` | 1 | 동시 보유 상한 |
| `LIMIT_UP_MAX_PER_POS_RATIO` | 0.03 | 종목당 비중(예수금 3%) |
| `LIMIT_UP_MIN_PRICE` | 2000 | 최소 진입가(동전주 회피) |
| `LIMIT_UP_DAILY_LOSS_LIMIT` | -2.0 | 일일손실 한도(%) |
| `LIMIT_UP_MAX_ORDERS` | 6 | 일일 최대 주문수 |
| `LIMIT_UP_HARD_STOP` | -4.0 | 상한가 깨짐 빠른 손절(%) |
| `LIMIT_UP_TRAIL_ATR` | 2.0 | ATR 트레일 배수 |
| `LIMIT_UP_TAKE_PROFIT` | 5.0 | 고정 익절(%) |
| `LIMIT_UP_MAX_ENTRIES` | 1 | 동일종목 당일 진입 상한 |
| `LIMIT_UP_RUNNER_LIMIT_UP` | 29 | 상한가 판정(%) |
| `LIMIT_UP_RUNNER_LOCK` | 2 | 수익잠금 floor(%) |
| `LIMIT_UP_RUNNER_GIVEBACK` | 3 | 고점 되돌림 청산(%) |
| `LIMIT_UP_OVERNIGHT_MODE` | daily | daily \| overnight |
| `LIMIT_UP_EOD_CLOSE` | 15:15 | daily 모드 강제청산 시각 |
| `LIMIT_UP_GAP_PARTIAL` | 0.5 | 익일 갭 부분익절 비율 |
| `LIMIT_UP_GAP_PARTIAL_MIN` | 3.0 | 부분익절 최소 갭(%) |
| `LIMIT_UP_GAP_PARTIAL_WINDOW` | 6 | 개장 후 N봉(×5분) 이내 |

---

## 7. 리스크 컨트롤

- 기본 **OFF** · **dry_run 우선**(`DRYRUN=1`)
- 동시 보유 **1종**(검증 후 2), 종목당 **3%**, `_cap_qty`/`max_order_value` 하드캡
- `GatePolicy(daily_loss_limit=-2.0, daily_max_orders=6)`
- `min_price` 상향(동전주 상한가 변동성 회피)
- 상한가 깨짐 시 `hard_stop=-4%` + `trail=2×ATR`이 RUNNER보다 우선 → 빠른 탈출
- `max_entries_per_symbol_day=1`(깨진 상한가 재추격 금지)

---

## 8. 데이터 한계

| 요구 | 가용 여부 |
|---|---|
| 상한가 직접 플래그 TR | ❌ 없음 → 등락률 +30% 근사 |
| 체결강도(cntr_str) | ❌ TR 미반환 |
| 상한가 '락' 틱 포착 | ❌ 60초 스냅샷·5분봉 → 근접 모멘텀만 |
| 호가 잔량(ka10004) | ✅ 가용(매수벽 확인) |
| 오버나잇 보유 | ✅ 가능(`EOD_FORCE_CLOSE_DISABLED=1`) |

→ 호가 L2 이력이 없어 **백테스트 불가**. 진입 핵심(매수벽)은 **라이브 dry_run 검증 위주**.

---

## 9. 검증

`backend/tests/test_limit_up_chase_trader.py` **18 케이스 전부 통과**:

- 호가벽 pass + 4 reject(잔량·근접·비율·미가용)
- 모멘텀 밴드 in/out
- 진입 통합(strategy_id 태깅) / 호가벽·모멘텀 거절
- `_is_limit_up` 상속 재사용
- 상한가 락 → 오버나잇 홀딩(미청산)
- 하드손절 청산
- **전략 격리(supertrend 포지션 미간섭 = 더블셀 없음)**
- 익일 갭 부분익절(1회·멱등)
- 진입 시간창 / EOD 모드 게이트
- `evaluate_holdings` 제외 default 검증

(참고: 운영 venv에 `pytest-asyncio` 미설치로 기존 비동기 테스트도 동일하게 안 돌아감 → 로직은
`asyncio.run` 직접 구동으로 18/18 검증.)

---

## 10. 단계 롤아웃

1. **Phase 0 — dry_run 관찰**: `.env.local`에 `LIMIT_UP_CHASE_ENABLED=1` `LIMIT_UP_CHASE_DRYRUN=1`
   → 봇 재시작(launchd) → 텔레그램으로 `entered/exited` DRY_RUN 로그 며칠 관찰(호가벽 판정·진입 빈도).
2. **Phase 1 — 소액 라이브**: `LIMIT_UP_CHASE_DRYRUN=0`, `MAX_POS=1`, per-position 3%.
3. **Phase 2 — 오버나잇**: `LIMIT_UP_OVERNIGHT_MODE=overnight`, 익일 `gap_partial` 동작 확인.

각 단계 `data/order_audit.csv`의 `strategy_id="limit_up_chase"`로 PnL 분리 집계.

---

## 11. 파일 맵

| 파일 | 역할 |
|---|---|
| `backend/core/limit_up_chase_trader.py` | **신규** — `LimitUpChaseTrader` + `LimitUpChaseConfig` + 호가벽 게이트 |
| `backend/core/supertrend_auto_trader.py` | **무수정** — RUNNER/`_is_limit_up`/청산 헬퍼 상속 원본 |
| `scripts/run_telegram_bot.py` | `_build_limit_up_chase_trader` 빌더 + `_run_all` 태스크 |
| `scripts/evaluate_holdings.py` | `--exclude-strategy` 기본값에 `limit_up_chase` 추가(EOD 방어) |
| `backend/core/gateway/kiwoom_native_orderbook.py` | ka10004 호가 fetch(재사용) |
| `backend/core/gateway/kiwoom_native_rank.py` | 주도주 picker(재사용) |
| `backend/tests/test_limit_up_chase_trader.py` | **신규** — 18 케이스 |
