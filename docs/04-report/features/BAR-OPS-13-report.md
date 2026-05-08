# BAR-OPS-13 — 시뮬 결과 영속화 + history 분석

## 목적
매일 주도주 시뮬 → 결과 누적 → 시계열 추세 추적·전략 정확도 검증.

## 산출
- `backend/core/journal/simulation_log.py`:
  - `SimulationLogEntry` (frozen dataclass) — run_at·mode·symbol·strategy·pnl·win_rate·score·flu_rate
  - `SimulationLogger` — CSV append 기반, header 자동, round-trip 가능
  - `summarize_by_strategy(entries)` — 전략별 runs·trades·weighted win_rate·total_pnl
  - `summarize_by_run(entries)` — 실행(run_at)별 종목 수·trades·total_pnl 시계열
- `scripts/simulate_leaders.py` — `--log <path>` 옵션
- `scripts/show_simulation_history.py` — 누적 분석 CLI (`--by strategy/run/both`)
- `backend/tests/journal/test_simulation_log.py` — 7 cases

## 실 검증 (mockapi.kiwoom.com, 2026-05-08)

```bash
$ python scripts/simulate_leaders.py --top 3 --min-score 0.5 \
    --log data/simulation_log.csv

  319400 현대무벡스   PnL= -117,730
  001440 대한전선    PnL= +222,520
  005380 현대차     PnL=+7,400,500
  총 PnL: +7,505,290 / 15 entries → CSV 영속화
```

```bash
$ python scripts/show_simulation_history.py data/simulation_log.csv

== 전략별 누적 (5 전략) ==
  strategy                  runs  trades   win%      total_pnl
  swing_38                     3      66  72.7%     +7,505,290
  f_zone                       3       0   0.0%             +0
  ...
```

## 운영 시나리오

### 매일 cron 실행
```bash
# crontab -e
0 16 * * 1-5 cd /path/to/repo && set -a; . ./.env.local; set +a; \
  python scripts/simulate_leaders.py --top 5 --min-score 0.5 \
  --log data/simulation_log.csv
```

→ 매일 16시(장 마감 직후) 주도주 자동 선정 + 시뮬 + CSV append.

### 주말/분기별 분석
```bash
python scripts/show_simulation_history.py data/simulation_log.csv --by strategy
python scripts/show_simulation_history.py data/simulation_log.csv --by run
```

→ 전략별 승률·PnL 추세, 실행별 시계열로 어떤 시점에 시장이 강했는지 추적.

## 데이터 모델

```csv
run_at,mode,symbol,name,strategy,candle_count,trades,pnl,win_rate,score,flu_rate
2026-05-08T13:56:10+00:00,daily,319400,현대무벡스,swing_38,600,20,-117730.00,0.0000,0.935,21.61
2026-05-08T13:56:10+00:00,daily,319400,현대무벡스,gold_zone,600,0,0.00,0.0000,0.935,21.61
...
```

→ 스프레드시트 / pandas / SQL (CSV → Postgres COPY) 분석 즉시 가능.

## Tests
- 7 신규 / 회귀 **682 → 689 (+7)**, 0 fail

## 다음
- BAR-OPS-14: Postgres 영속화 (BAR-56 인프라 활용) — CSV → DB 마이그레이션
- BAR-OPS-15: 키움 자체 주문 API 어댑터 (실 거래 진입)
- BAR-OPS-16: GitHub Action 자동화 (PR 마다 시뮬 회귀)
