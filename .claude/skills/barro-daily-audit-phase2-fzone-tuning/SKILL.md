---
name: barro-daily-audit-phase2-fzone-tuning
description: BarroAiTrade BAR-OPS-09 Phase 2 - f_zone 손실 패턴 진단 + 파라미터 튜닝. Phase 1 산출물(scripts/_daily_evening_pipeline.py 등)을 활용해 일일 운영/시뮬 결과 분석 → f_zone 손실 종목 drill-down → backend/core/strategy/f_zone.py 의 FZoneParams (min_atr_pct, impulse_min_gain_pct, bounce_min_gain_pct, ma_support_tolerance, pullback_min_pct) 또는 운영 진입점에서 명시 override 로 튜닝. 시뮬 검증(시뮬 데이터 비교) 통과 후 1커밋·푸시. 사용자가 "Phase 2 적용", "f_zone 튜닝", "X일 데이터 분석", "f_zone 손실 진단", "Daily audit Phase 2 실행", "LG전자 손실 진단", "저변동 종목 필터링" 같은 표현을 쓸 때 즉시 트리거. PRD `Daily 운영 audit 자동화 도구 (Phase 1)` §9 의 Phase 2 로드맵 항목 진행.
---

# BarroAiTrade BAR-OPS-09 Phase 2 — f_zone 튜닝 스킬

Phase 1 도구로 일일 운영 결과를 분석하고, f_zone 전략의 손실 패턴을 진단해 파라미터 1~2개를 튜닝한다. 시뮬 검증 통과 후 다음 영업일에 반영.

PRD `Daily 운영 audit 자동화 도구 (Phase 1)` 의 후속 — Phase 1 으로 자동화된 일별 사이클의 첫 번째 전략 튜닝 단계.

## 트리거 조건

- "Phase 2 적용", "f_zone 튜닝"
- "X일 데이터로 Phase 2 진행"
- "f_zone 손실 종목 진단해줘"
- "LG전자 같은 저변동주 필터링"
- "Daily audit Phase 2 실행"

## 사전 조건

- Phase 1 완료 (scripts/_daily_evening_pipeline.py 등 6개 파일 머지)
- 운영 zip (`~/Downloads/BarroAiTrade_*.zip`) 다운로드 완료
- (선택) kt00009 실거래 dump — 없으면 zip 안의 `data/simulation_log.csv` + `data/order_audit.csv` 로 대체

## 작업 흐름 (5 단계)

### Step 1 — 운영 데이터 ingest

```bash
WT=/Users/beye/workspace/BarroAiTrade/.claude/worktrees/strange-jackson-3c740a
ZIP="/Users/beye/Downloads/BarroAiTrade_m4 X.zip"  # 또는 최신
IMPORT_DIR="$WT/analysis/imports/YYYY-MM-DD"

mkdir -p "$IMPORT_DIR" && unzip -o -q "$ZIP" -d "$IMPORT_DIR"
```

**라이브 kt00009 호출 가능 시** (M4 + 환경변수):
```bash
"$WT/.venv/bin/python" scripts/_daily_evening_pipeline.py --date YYYY-MM-DD
```

**라이브 호출 불가 시 (로컬 분석)**:
- `data/simulation_log.csv` (daily 시뮬 결과: 종목 × 전략별 trades/pnl/win_rate)
- `data/order_audit.csv` (ORDERED/BLOCKED/DRY_RUN 매매 audit)
- `data/active_positions.json` (장 마감 시 보유 — 0 이어야 정상)

### Step 2 — 5/21 패턴 발견 (참고 케이스)

**시뮬 매트릭스 분석 패턴**:
```
종목 × 전략 → trades + pnl 매트릭스 생성
→ f_zone 진입한 종목 식별
→ f_zone 손실 종목 1개 이상 발견 시 Step 3 진입
```

**참고 결과 (5/21)**:
| 종목 | flu% | f_zone | gold_zone | swing_38 |
|---|---|---|---|---|
| 005930 (삼성) | 6.3% | 0 | 44/+826k | 2/+11k |
| 017900 (광전자) | 11.6% | 0 | 92/+11k | 4/+126k |
| **066570 (LG전자)** | 10.2% | **4/−384k** | **43/−626k** | 10/+1428k |
| 122630 (KODEX) | 10.7% | 0 | 54/+329k | 0 |
| 233740 (KODEX코스닥) | 11.0% | 0 | 59/+273k | 6/+95k |

→ f_zone 진입률 1/5, 그 1건에서 4 trades 모두 손실. LG전자에서 gold_zone 도 손실 → **저변동·고가주의 가짜 시그널 패턴**.

### Step 3 — f_zone 손실 패턴 진단

진단 태그별 fix 후보:

| 손실 패턴 | 의심 원인 | 변경 후보 |
|---|---|---|
| 저변동·고가주 손실 (LG전자形) | `min_atr_pct=0.0` 라 변동성 필터 없음 | 운영 진입점에서 `min_atr_pct=0.035` 명시 override (BAR-44 검증값) |
| 진입 시그널은 발생하나 즉시 하락 | `bounce_min_gain_pct=0.005` 너무 낮음 | 0.008~0.010 으로 상향 |
| 매물대 미인식, 진입 후 상승 부족 | `ma_support_tolerance=0.01` 너무 너그러움 | 0.005 로 강화 |
| 깊은 눌림에 진입해 회복 못함 | `pullback_min_pct=-0.05` 너무 깊음 | `-0.04` 또는 `-0.035` 로 제한 |
| `impulse_max_gain_pct=1.0` 무제한 — 과열 후 진입 | 사후 BAR-44 LESSON: max 7% 적용 시 winning 시그널 죽임 — **무제한 유지 권장** | 변경 금지 |
| 청산 늦음 (peak 대비 −5%+) | exit_plan tier 너무 너그러움 | `intraday_simulator._exit_plan_for_strategy` 의 f_zone trailing 강화 |
| 시초가 노이즈 (09:00~09:06 진입) | 진입 시간대 제한 부재 | f_zone.analyze 에 시간대 게이트 추가 |

**default vs override 원칙**:
- `f_zone.py` 의 `FZoneParams` default 는 **변경 금지** (BAR-44 baseline 회귀 보존)
- 변경은 **운영 진입점**에서 명시 override (예: `FZoneParams(min_atr_pct=0.035)`)
- 진입점 위치: `grep -rn "FZoneParams\|FZone(" backend/ scripts/ --include="*.py"` 로 확인

### Step 4 — 변경 + 시뮬 검증

```bash
# 진입점 변경 (예: scripts/simulate_leaders.py 또는 데몬)
# 변경 전후 동일 데이터로 IntradaySimulator 또는 daily run 실행해 결과 비교

# 단위 + 회귀 (반드시 통과)
"$WT/.venv/bin/pytest" backend/tests/strategy/test_f_zone.py -q
"$WT/.venv/bin/pytest" backend/tests/strategy/ backend/tests/risk/ -q

# 시뮬 (변경 전후 비교 — pnl/win_rate 개선 또는 손실 종목 진입 차단 확인)
# 예: ohlcv_cache 의 5/21 종목으로 IntradaySimulator 단독 실행
```

**검증 기준**:
- 회귀 영향 없음
- 변경 후 5/21 손실 종목(LG전자) f_zone 진입 차단 또는 win_rate 개선
- 다른 종목(005930 등)의 win 시그널 영향 없음

### Step 5 — 커밋 + 푸시 (사용자 승인 필수)

```bash
git add backend/core/strategy/f_zone.py <또는 진입점>
git commit -m "$(cat <<'EOF'
fix(BAR-OPS-09): f_zone <변경 요약> (Phase 2)

YYYY-MM-DD 손실 종목 XXXXXX (-XXX,XXXk, X trades) drill-down 결과 — <태그>.
<파라미터>를 <기존>→<신규>로 변경. 시뮬 검증 통과 (변경 전 net X → 변경 후 net Y),
회귀 영향 없음.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin BAR-OPS-09
```

## 핵심 사실 (반드시 숙지)

### f_zone.py FZoneParams default (2026-05-22 기준)

```python
impulse_min_gain_pct = 0.03         # 기준봉 최소 상승률 3%
impulse_max_gain_pct = 1.0          # 무제한 (BAR-44 LESSON_FZONE_MAX_GAIN — 변경 금지)
impulse_volume_ratio = 2.0
pullback_min_pct = -0.05            # 눌림 최대 -5%
pullback_max_pct = -0.005           # 눌림 최소 -0.5%
pullback_volume_ratio = 0.7
pullback_max_candles = 10
ma_periods = [5, 20, 60]
ma_support_tolerance = 0.01         # 이평선 ±1% 이내 접근을 지지로 간주
bounce_min_gain_pct = 0.005         # 반등 최소 0.5%
bounce_volume_ratio = 1.2
sf_impulse_min_gain_pct = 0.05      # SF존 5%
sf_volume_ratio = 3.0
min_candles = 60
min_atr_pct = 0.0                   # 변동성 필터 비활성 (BAR-44 baseline 회귀 보존)
                                    # 운영 진입점 권장 override: 0.035
atr_n = 14
```

### 운영 진입점

- `grep -rn "FZoneParams\|FZone(" backend/ scripts/`
- 결과에 따라 변경 위치 결정 (대체로 `simulate_leaders.py` 또는 strategy factory)

### 데이터 소스 우선순위

1. **라이브 kt00009** (M4 환경변수 필요) — 가장 정확한 실거래 net
2. **`data/simulation_log.csv`** — daily 시뮬 결과 (종목×전략 매트릭스)
3. **`data/order_audit.csv`** — ORDERED/BLOCKED 매매 audit (가격은 MKT 표기, 정확한 체결가 없음)
4. **`logs/closing.log`, `intraday.log`** — 평가·매도 로그 (보조)

### ohlcv_cache 형식

운영 zip 안의 `ohlcv_cache/` 는 **JSON** 형식 (`{"data": [...]}`).
`_loss_drill_down.py` 의 `load_csv_candles` 는 CSV 만 처리 → drill-down 시 JSON → CSV 변환 또는 별도 로더 필요.

## 함정

1. **f_zone.py default 변경 금지** — BAR-44 baseline 회귀 영향. 변경은 운영 진입점에서 override.
2. **`impulse_max_gain_pct` 무제한 유지** — BAR-44 LESSON: max 7% 적용 시 winning 시그널까지 죽임. 변경 금지.
3. **시뮬 vs 실거래 차이** — daily 시뮬은 600봉 전체 backtest, 실거래는 09:00~15:30. 진단 시 시간대 일치 확인.
4. **단일 일자만으로 결론 금지** — 5/21 한 날만 보지 말고 누적 (`reports/YYYY-MM-DD.md` 의 누적 시뮬) 도 함께 확인.

## 검증 / 수용 기준

1. `pytest backend/tests/strategy/test_f_zone.py -q` → 영향 없음
2. `pytest backend/tests/strategy/ backend/tests/risk/ -q` → 회귀 없음
3. 변경 후 손실 종목에서 f_zone 진입 차단 또는 win_rate 개선 (시뮬 비교)
4. 다른 종목의 win 시그널 영향 없음 (회귀 시뮬)

## 사용자 보고 패턴

```
[Step 1] zip 해제 완료 — N 종목 시뮬 + M 종목 실거래
[Step 2] f_zone 진입 X/N, pnl XXk
[Step 3] 진단: <태그> — 변경 후보 <파라미터> <기존>→<신규>
[Step 4] 시뮬 변경 전후 비교: <before> → <after>, 회귀 통과
[Step 5] 커밋·푸시 진행할까요?
```

Step 3 의 변경 후보는 **사용자와 합의 후 진행** (자동 결정 금지).
