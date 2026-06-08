# BAR-OPS-37 — BAR-OPS-35/36 가드 env 토글 배선

_작성: 2026-06-09 / 브랜치: `feat/BAR-OPS-37-env-toggles`_

## 배경

BAR-OPS-35(가드)·36(러너)은 dataclass 플래그로 구현됐으나, **라이브 config 생성부에서 참조되지
않아 env로 켤 수 없는 상태**였다(기본값 OFF 그대로 = dormant). 본 작업으로 기존 컨벤션
(`os.environ.get("SUPERTREND_AUTO_*", default)`)대로 **env 토글 배선**을 추가 — 기본은 여전히 OFF라
**env 미설정 시 동작 불변**, 운영 머신에서 env 설정만으로 단계적 활성화(shadow→enforce) 가능.

## 배선 위치

- `scripts/intraday_buy_daemon.py` — 라이브 데몬(주 경로): `GatePolicy`(공용 gate) + `SupertrendAutoConfig`. `_env_truthy` 헬퍼 추가.
- `scripts/run_telegram_bot.py` — 텔레그램 봇 supertrend 경로: `GatePolicy` + `SupertrendAutoConfig`.

## env 토글 표 (전부 기본 OFF/현행 유지)

### SupertrendAutoConfig (가드/러너)
| env 변수 | 기본 | 매핑 플래그 | 의미 |
|---|---|---|---|
| `SUPERTREND_AUTO_HARD_STOP` | 0 | hard_stop_pct | catastrophic 하드손절 %(예 -6) |
| `SUPERTREND_AUTO_MAX_ENTRIES` | 0 | max_entries_per_symbol_day | 동일종목 당일 진입 상한(1=재진입 금지) |
| `SUPERTREND_AUTO_REENTRY_COOLDOWN` | 0 | reentry_cooldown_min | 청산 후 재진입 cooldown(분) |
| `SUPERTREND_AUTO_BLOCK_REENTRY_LOSS` | off | block_reentry_after_loss | 당일 손절종목 재진입 금지 |
| `SUPERTREND_AUTO_MAX_ATR_PCT` | 0 | max_atr_pct_for_entry | 고변동 종목 진입 차단(ATR/price, 예 0.05) |
| `SUPERTREND_AUTO_TP_TRAIL_ONLY` | off | take_profit_trail_only | 고정익절 끄고 트레일만 |
| `SUPERTREND_AUTO_VOL_HALVE_ATR` | 0 | vol_halve_atr_pct | 고변동 종목 사이징 절반(예 0.05) |
| `SUPERTREND_AUTO_SINGLE_TRANCHE` | off | single_tranche | DCA 분할 미사용(sync-loss 방지) |
| `SUPERTREND_AUTO_MAX_ENTRY_GAP` | 0 | max_entry_gap_pct | 추격매수 가드(직전봉比 갭 %, 예 3) |
| `SUPERTREND_AUTO_RUNNER` | off | runner_enabled | **러너 마스터 스위치** |
| `SUPERTREND_AUTO_RUNNER_LIMIT_UP` | 29 | runner_limit_up_pct | 상한가 판정 %(트리거+홀딩) |
| `SUPERTREND_AUTO_RUNNER_GAP_UP` | 0 | runner_gap_up_pct | 보유종목 시초갭 러너 트리거 %(예 5) |
| `SUPERTREND_AUTO_RUNNER_GIVEBACK` | 3 | runner_giveback_pct | 최고점 되돌림 청산 %(추세이탈) |
| `SUPERTREND_AUTO_RUNNER_GIVEBACK_ATR` | 0 | runner_giveback_atr_mult | >0 이면 peak−mult×ATR |
| `SUPERTREND_AUTO_RUNNER_LOCK` | 2 | runner_profit_lock_pct | 러너 수익잠금 floor % |
| `SUPERTREND_AUTO_GAP_PARTIAL` | 0 | runner_gap_partial_ratio | **익일 시가갭 부분익절 비율**(0.5=절반) |
| `SUPERTREND_AUTO_GAP_PARTIAL_MIN` | 3 | runner_gap_partial_min_pct | 부분익절 최소 갭 % |
| `SUPERTREND_AUTO_GAP_PARTIAL_WINDOW` | 6 | runner_gap_partial_window_bars | 개장 후 N봉(×5분) 이내만 |

### GatePolicy (회로차단기/주문)
| env 변수 | 기본 | 매핑 플래그 | 의미 |
|---|---|---|---|
| `SUPERTREND_AUTO_LOSS_LATCH` | off | daily_loss_latch | 일일손실 한도 sticky latch(회복해도 잠금) |
| `SUPERTREND_AUTO_ORDER_RETRY` | 0 | order_retry_count | 주문 실패 재시도 횟수 |
| `SUPERTREND_AUTO_ORDER_RETRY_BACKOFF` | 0 | order_retry_backoff_sec | 재시도 백오프(초) |
| `SUPERTREND_AUTO_RETRY_SELL_ONLY` | on(1) | retry_sell_only | 매도(청산)만 재시도 |

> 기존: `SUPERTREND_AUTO_DAILY_LOSS_LIMIT`(-3.0), `SUPERTREND_AUTO_TRAIL_ATR`(0), `SUPERTREND_AUTO_*` (RSI/ADX/FLIP 등)는 그대로.

## 설정 위치 (중요)

쉘 `export` 가 아니라 **`.env.local` 파일에 적는다** — 실거래 런처 `run_bot_with_env.sh` 가
`set -a; . ./.env.local; set +a` 로 source 후 `run_telegram_bot.py`(봇)를 띄우기 때문.
`SUPERTREND_AUTO_ENABLED` 가 truthy 면 데몬은 supertrend 를 봇에 양보(중복주문 방지)하므로
**실거래 supertrend = 봇 + .env.local** 경로. (템플릿: `.env.example` 의 슈퍼트렌드 섹션.)

## 권장 활성화 순서 (`.env.local` 에 추가, shadow 1~3거래일씩)

```dotenv
# 0) 봇이 supertrend 담당
SUPERTREND_AUTO_ENABLED=1
# 1단계 — 회로차단기 latch + 매도 재시도 (가장 안전·고효과)
SUPERTREND_AUTO_LOSS_LATCH=1
SUPERTREND_AUTO_ORDER_RETRY=2
# 2단계 — 재진입 차단 + 하드손절 (6/8 -509K 직접 차단, 시뮬 +836K)
SUPERTREND_AUTO_MAX_ENTRIES=1
SUPERTREND_AUTO_HARD_STOP=-6
SUPERTREND_AUTO_SINGLE_TRANCHE=1
# 3단계 — 러너(승자 보유) + 익일 시가갭 부분익절 (상한가 초과수익)
SUPERTREND_AUTO_RUNNER=1
SUPERTREND_AUTO_GAP_PARTIAL=0.5
SUPERTREND_AUTO_RUNNER_GAP_UP=5
```

> 변경 후 봇 재시작 필요(env 는 프로세스 시작 시 로드). 각 단계 후 `scripts/_daily_strategy_audit.py
> --date <D>` 로 진입건수·재진입·승률·고점매수율·부분익절 확정수익 측정. 과차단 시 임계 재튜닝.

## 검증

- 두 스크립트 `py_compile` OK. 스모크: env ON 시 전 신규 필드 정확 반영, env 미설정 시 기본(OFF) 확인.
- `test_supertrend_auto_trader.py` 67 passed (코어 미변경, 회귀 0).
- 기본 OFF라 env 미설정 라이브는 동작 불변.
