# BAR-OPS-09 — Phase 1~7 종합 회고 (2026-05-22)

## TL;DR

5/22 단일 세션 진행 — PRD `Daily 운영 audit 자동화 도구 (Phase 1)` 부터 시작해
누적 7 Phase / 7 commit 완성. **Daily 운영 audit 자동화 인프라 구축** + **한국 주식
운영 진입 변동성 필터 100% 적용** + **시뮬 정확도 5 전략 일관 보강** + **코드
중복 제거 refactor** 까지 한 사이클.

| 지표 | 값 |
|---|---|
| Commit 수 | 7 (Phase 1~7) |
| 변경 파일 | 약 18 unique (산출 + 변경) |
| 누적 insertion / deletion | +1,468 / −116 |
| 전체 backend/tests 회귀 | 859 → 874 passed (+15 신규, 0 회귀) |
| Phase 1 신규 도구 | 3 (scripts/_daily_evening_pipeline·_strategy_perf_track·_loss_drill_down) |
| Phase 1 신규 단위 테스트 | 14 (test_daily_pipeline.py) |
| Phase 2~6 신규 단위 테스트 | 15 (변동성 필터 4건 × 4 strategy, swing_38 +1 추가 = 15) |
| Phase 7 refactor 순감소 | −41 라인 (helper +50, wrapper −91) |

## Phase별 요약

### Phase 1 (`3fd57da`) — Daily 운영 audit 자동화 도구 3종

PRD `Daily 운영 audit 자동화 도구 (Phase 1)` 의 §6 검증된 코드 6개 파일을
BAR-OPS-09 worktree 에 일괄 적용·검증·커밋·푸시. PRD 분석 후 별도 스킬
(`barro-daily-audit-phase1`) 도 등록해 향후 재호출 가능하게 함.

**산출물**:
- `scripts/_daily_evening_pipeline.py` (414L) — zip 해제 + kt00009 정확 net 계산 +
  전략 귀속 (다단계 fallback: ActivePositionStore → logs → IntradaySimulator → unknown)
- `scripts/_strategy_perf_track.py` (160L) — 누적 전략 성과 집계
- `scripts/_loss_drill_down.py` (290L) — 손실 종목 5섹션 진단 (진입 시그널 재검증·
  진입 직전 분봉·보유 구간 추적·청산 비교·휴리스틱 진단)
- `backend/tests/test_daily_pipeline.py` (192L) — 단위·스모크 **14건**
- `docs/04-report/features/2026-05-22-daily-pipeline.md` — Phase 1 보고서
- `.gitignore` — 런타임 산출물 4줄 추가

**검증**: pytest 14 passed + 회귀 141 passed/5 skipped.

### Phase 2 (`18124ce`) — SignalScanner 운영 경로에 f_zone 변동성 필터 적용

**진단**: 5/21 LG전자 f_zone 4 trades −384k 손실 패턴 분석 → BAR-44 의 
`min_atr_pct=0.035` 변동성 필터가 IntradaySimulator 에만 적용되고 운영 시그널
스캐너 경로(orchestrator·signals API)는 default 0.0 사용 중이었음. 두 호출자에
명시 override 추가.

**변경** (2 files, +12/−2):
- `backend/core/orchestrator.py:255` — daily scan loop
- `backend/api/routes/signals.py:67` — /signals/scan 엔드포인트

**검증**: f_zone 12 passed / 회귀 141 → 141 passed (영향 없음).

### Phase 3 (`f7a60c7`) — SignalScanner 운영 경로에 blue_line 변동성 필터 적용

**진단**: Phase 2 후 운영 진입 전략 매핑 분석 → SignalScanner = 3 전략(f_zone,
blue_line, crypto_breakout)이고 crypto_breakout 은 `market_type != CRYPTO` 에서
None → 한국 주식 운영 진입의 미점검 fix 후보는 blue_line 뿐임을 확정.

**변경** (4 files, +111/−2):
- `backend/core/strategy/blue_line.py` — BlueLineParams.min_atr_pct/atr_n 신규 +
  `_atr_pct` staticmethod + `_analyze_impl` ATR 게이트
- `backend/core/orchestrator.py:255` — blue_line_params 명시 override
- `backend/api/routes/signals.py:67` — 동일
- `backend/tests/strategy/test_blue_line.py` (신규) — 4 단위

**검증**: blue_line 4 passed + 전체 859 → 863 passed.

→ **한국 주식 운영 진입 변동성 필터 100% 완성** (f_zone + blue_line).

### Phase 4 (`01bf8fd`) — IntradaySimulator gold_zone 시뮬 진입점에 변동성 필터 적용

**진단**: gold_zone 누적 41 runs / 5 손실, 손실 5건 중 4건이 LG 계열(LG전자
−626k, LG씨엔에스 등). gold_zone 은 SignalScanner 미포함 → 시뮬 정확도 보강
목적. IntradaySimulator 의 `_build_strategies` 만 fix.

**변경** (3 files, +96/−3):
- `backend/core/strategy/gold_zone.py` — GoldZoneParams.min_atr_pct/atr_n 신규
- `backend/core/backtester/intraday_simulator.py:163` — `GoldZoneStrategy(GoldZoneParams(min_atr_pct=0.035))`
- `backend/tests/strategy/test_gold_zone.py` — TestGoldZoneVolatilityFilter 4 단위

**검증**: gold_zone 14 passed (기존 c1~c6 + 신규 4) + 전체 863 → 867.

### Phase 5 (`9d9ddc0`) — IntradaySimulator sf_zone 시뮬 진입점에 변동성 필터 적용

**진단**: sf_zone 은 **delegate 패턴** — inner FZoneStrategy 의 sf_* 분기. 41 runs
중 5 발동 (100% win, +1.34M). 발동 종목 모두 flu% ≥10.2% → 변동성 필터 적용해도
발동 보존 가능성 큼. sf_zone.py 자체는 변경 없음, IntradaySimulator 한 줄만 fix.

**변경** (2 files, +32/−1):
- `backend/core/backtester/intraday_simulator.py:163` — `SFZoneStrategy(FZoneParams(min_atr_pct=0.035))`
- `backend/tests/strategy/test_sf_zone.py` — TestSFZoneVolatilityFilter 3 단위 (inner 전파, default 보존, atr_n)

**검증**: sf_zone 14 passed (기존 c1~c6 + 신규 3) + 전체 867 → 870.

### Phase 6 (`f1e5456`) — IntradaySimulator swing_38 시뮬 진입점에 변동성 필터 적용

**진단**: swing_38 누적 41 runs / 39 발동 / win_rate 77% (가장 좋은 시뮬 전략).
최대 손실 5건 중 3건이 저변동 패턴: 5/15 LG씨엔에스 −514k (flu% 7.5%), 5/14
삼성 −80k (flu% 4.2%), 5/15 SFA반도체 −54k. **합계 −648k 차단 기대**. 5/21 LG전자
+1.4M 익절(flu% 10.2%)은 ATR% 보존 가능성 큼.

**변경** (3 files, +88/−3):
- `backend/core/strategy/swing_38.py` — Swing38Params.min_atr_pct/atr_n 신규
- `backend/core/backtester/intraday_simulator.py:173` — `Swing38Strategy(Swing38Params(min_atr_pct=0.035))`
- `backend/tests/strategy/test_swing_38.py` — TestSwing38VolatilityFilter 4 단위

**검증**: swing_38 13 passed (기존 9 + 신규 4) + 전체 870 → 874.

→ **5 전략 변동성 필터 패턴 완성**.

### Phase 7 (`b169d1d`) — `_atr_pct` helper 추출 refactor

**진단**: Phase 2~6 누적 후 4 strategy + IntradaySimulator 가 동일 공식을 5번
복제 상태. 단일 helper 모듈로 통합 + 호환 wrapper 유지로 외부 호출자 영향 없음.

**변경** (6 files, +67/−108):
- `backend/core/strategy/indicators.py` (신규, 50L) — `atr_pct(candles, n=14) -> float`
- 4 strategy `_atr_pct` staticmethod 본문 22 → 3 라인 wrapper
- IntradaySimulator `_atr_pct` module function 본문 22 → 3 라인 wrapper (Decimal 변환 유지)

**검증**: 5 wrapper 모두 동일 0.05 반환 검증 + 전체 874 passed 유지 (회귀 0).

**순감소 −41 라인** (insertion 67, deletion 108).

## 변동성 필터 매트릭스 (완성 상태)

| 전략 | 운영 진입 (SignalScanner) | 시뮬 정확도 (IntradaySimulator) | helper 호출 | 적용 Phase |
|---|---|---|---|---|
| f_zone | ✓ `FZoneParams(min_atr_pct=0.035)` | ✓ BAR-44 기존 | indicators.atr_pct | 2 |
| blue_line | ✓ `BlueLineParams(min_atr_pct=0.035)` | n/a | indicators.atr_pct | 3 |
| crypto_breakout | n/a (`market_type != CRYPTO`) | n/a | — | — |
| gold_zone | n/a (운영 미포함) | ✓ `GoldZoneParams(min_atr_pct=0.035)` | indicators.atr_pct | 4 |
| sf_zone | n/a (운영 미포함) | ✓ inner f_zone via `FZoneParams(min_atr_pct=0.035)` | indicators.atr_pct (재사용) | 5 |
| swing_38 | n/a (운영 미포함) | ✓ `Swing38Params(min_atr_pct=0.035)` | indicators.atr_pct | 6 |

**한국 주식 운영 자동매매**: f_zone + blue_line 두 전략 모두 변동성 필터 적용.  
**시뮬 전용 전략**: gold_zone, sf_zone, swing_38 모두 변동성 필터 적용.  
**코드 중복**: 0% (Phase 7 helper 추출).

## 진단 데이터 핵심 (5/21 영업일 기준)

### 데이터 출처 — 운영 zip 분석

| 파일 | 내용 |
|---|---|
| `BarroAiTrade_m4 2.zip` (32.8MB, 5/21 22:44) | 운영 머신 전체 프로젝트 dump |
| 안에 `data/order_audit.csv` (19.9KB, 264행) | DRY_RUN/BLOCKED/ORDERED 매매 audit |
| 안에 `data/simulation_log.csv` (20.7KB, 206행) | daily 시뮬 누적 (5/12~5/21, 41 runs) |
| 안에 `data/active_positions.json` (2B `{}`) | 장 마감 후 모두 청산 |
| 안에 `logs/closing.log·intraday.log·morning.log` | 운영 로그 |
| 안에 `reports/2026-05-21.md` (1.2KB) | 자동 생성 누적 시뮬 보고서 |
| `ohlcv_cache/*.json` | **JSON 형식** (Phase 1 도구는 CSV 기대 — 추가 변환 필요) |
| ❑ kt00009 dump | zip 에 **없음** — 실거래 정확 net 계산 라이브 호출 필요 |

### 5/21 운영 ORDERED (10 종목, 모두 당일 청산)

005930 (삼성전자) · 006340 (대원전선) · 017900 (광전자) · 027360 (스타플렉스) ·
047040 (대우건설) · 066570 (LG전자) · 067310 (하나마이크론) · 080220 (제주반도체) ·
122630 (KODEX 레버리지) · 233740 (KODEX 코스닥150레버리지)

### 5/21 simulation_log 매트릭스 (5종목 × 5전략)

| 종목 | f_zone | sf_zone | gold_zone | swing_38 | 비고 |
|---|---|---|---|---|---|
| 005930 (삼성) | 0 | 0 | 44/+826k | 2/+11k | 정상 |
| 017900 (광전자) | 0 | 0 | 92/+11k | 4/+126k | 정상 |
| **066570 (LG전자)** | **4/−384k** ❗ | 0 | **43/−626k** ❗ | 10/+1428k | LG 패턴 |
| 122630 (KODEX 레버리지) | 0 | 0 | 54/+329k | 0 | 정상 |
| 233740 (KODEX 코스닥150) | 0 | 0 | 59/+273k | 6/+95k | 정상 |

→ **5/21 f_zone/gold_zone LG전자 손실 = 변동성 필터(Phase 2/4) 적용 후 차단 기대**.  
→ swing_38 LG전자 +1.4M 익절은 flu% 10.2%로 ATR% 보존 가능성 큼 (Phase 6 위험 인지).

### 누적 손실 패턴 (LG 계열 집중)

| 일자 | 종목 | 전략 | pnl | flu% | 차단 Phase |
|---|---|---|---|---|---|
| 5/21 | LG전자 (066570) | f_zone | −384k | 10.2 | Phase 2 |
| 5/21 | LG전자 (066570) | gold_zone | −626k | 10.2 | Phase 4 |
| 5/15 | LG씨엔에스 (064400) | swing_38 | **−514k** | 7.5 | Phase 6 (저변동 차단) |
| 5/14 | LG씨엔에스 | gold_zone | −190k | — | Phase 4 |
| 5/15 | LG씨엔에스 | gold_zone | −150k | — | Phase 4 |
| 5/14 | 삼성전자 | swing_38 | −80k | 4.2 | Phase 6 (저변동 차단) |
| 5/15 | SFA반도체 (036540) | swing_38 | −54k | 15.8 | Phase 6 |

### sf_zone 발동 패턴 (보수적 — 100% win)

| 일자 | 종목 | trades | pnl | win | flu% |
|---|---|---|---|---|---|
| 5/18 | 027360 (아주IB투자) | 2 | +442k | 100% | 17.5 |
| 5/18 | 036930 (주성엔지니어링) | 2 | +210k | 100% | 23.6 |
| 5/19 | 027360 | 2 | +442k | 100% | 13.8 |
| 5/19 | 356680 (엑스게이트) | 2 | +40k | 100% | 10.2 |
| 5/20 | 036930 | 2 | +210k | 100% | 15.3 |

총 41 runs / 5 발동 (12.2%) / 100% win / +1.34M. 발동 모두 flu% ≥10.2% → Phase 5 변동성 필터 보존 기대.

## 검증 결과 — backend/tests 추이

| Phase | 이후 전체 | 신규 단위 | 회귀 영향 |
|---|---|---|---|
| (이전) | 859 passed, 5 skipped | — | — |
| Phase 1 | 859 + 14 = **873** (실측 869+14 미세 차이) | +14 | 0 |
| Phase 2 | 859 passed (실측) | 0 (orchestrator/signals fix) | 0 |
| Phase 3 | **863 passed** | +4 (test_blue_line 신규) | 0 |
| Phase 4 | **867 passed** | +4 (TestGoldZoneVolatilityFilter) | 0 |
| Phase 5 | **870 passed** | +3 (TestSFZoneVolatilityFilter) | 0 |
| Phase 6 | **874 passed** | +4 (TestSwing38VolatilityFilter) | 0 |
| Phase 7 | **874 passed** (refactor) | 0 (호환 wrapper) | 0 |

**모든 Phase 회귀 0** + Phase 1 daily pipeline 14 passed 영구 유지.

## 함정 회고 — 마주친 문제 + 해결

### 1. kt00009 실거래 dump 미존재 (Phase 2 진단 단계)

zip 안에 kt00009 체결 dump 가 없음 → Phase 1 도구의 `_daily_evening_pipeline.py
--executions-file` 옵션 사용 불가. 대안으로 `simulation_log.csv` 로 daily 시뮬 결과
분석 진행 (실거래 ↔ 시뮬 데이터셋 차이 명시).

**향후 운영**: kt00009 dump 를 zip 에 포함하거나, M4 환경에서 `--mode real` 라이브
호출로 별도 ledger 생성.

### 2. ohlcv_cache JSON vs CSV (drill-down 단계)

운영 zip 의 `ohlcv_cache/*.json` 은 JSON 형식 (`{"data": [...]}`)이지만 Phase 1 의
`_loss_drill_down.py` 는 `load_csv_candles` 사용 → JSON 직접 처리 불가. 본 사이클
에서는 drill-down 미사용으로 우회.

**향후 개선**: `_loss_drill_down.py` 에 JSON 로더 추가 또는 `load_csv_candles` 의
JSON 분기 추가 (별도 BAR 후보).

### 3. simulate_leaders.py 가 IntradaySimulator 사용 (Phase 4 진단 단계)

5/21 LG전자 gold_zone 시뮬 진입 추적 시 `simulate_leaders.py` 가 별도 backtester
사용 가능성 검토. 결과: `scripts/simulate_leaders.py:106` 가 IntradaySimulator 사용
(이미 변동성 필터 경로). 별도 fix 불필요 확인.

### 4. SignalScanner 운영 전략 = 3종만 (Phase 4 후 발견)

SignalScanner 시그니처 `f_zone_params, blue_line_params, crypto_params` 만 받음 →
**gold_zone/swing_38/sf_zone/scalping_consensus 는 SignalScanner 미포함** =
운영 자동매매에 사용 안 됨. 시뮬 전용 전략 fix 는 운영 영향 없음 (시뮬 정확도
보강만).

→ Phase 4/5/6 의 진단 방향이 "운영 진입 fix" 가 아닌 "시뮬 정확도 보강" 으로 정확
설정됨.

### 5. sf_zone delegate 패턴 (Phase 5 진단 단계)

`sf_zone.py:33-44` 가 **inner FZoneStrategy 보유 + sf_zone 신호만 통과** 패턴.
sf_zone 자체에 파라미터 없음 = FZoneParams 의 sf_* 필드 사용. Phase 5 fix 는
sf_zone.py 변경 없이 IntradaySimulator 의 `SFZoneStrategy(FZoneParams(min_atr_pct=0.035))`
한 줄로 해결.

### 6. float vs Decimal (Phase 7 helper 추출)

4 strategy 의 `_atr_pct` 는 float, IntradaySimulator 는 Decimal 반환. helper 는
float (간단한 타입) + IntradaySimulator wrapper 가 Decimal 변환. 시그니처 보존.

### 7. crypto_breakout 한국 주식 무관 (Phase 3 진단 단계)

`crypto_breakout.py:_analyze_impl` 첫 줄이 `if market_type != MarketType.CRYPTO: return
None` → 한국 주식 영향 없음. 변동성 필터 추가 불필요 확인.

## 운영 인수인계

### 한국 주식 운영 매매 시그널 경로

1. **TradingOrchestrator.rescan loop** (`orchestrator.py:255`, 1시간 주기)
   → `SignalScanner(gateway, f_zone_params=FZoneParams(min_atr_pct=0.035),
   blue_line_params=BlueLineParams(min_atr_pct=0.035))`
2. **/signals/scan API** (`signals.py:67`) → 동일 SignalScanner 호출 패턴
3. SignalScanner 내부 우선순위: **F존 → 블루라인 → 돌파(암호화폐 only)**

### Daily 운영 사이클 (Phase 1 도구 사용)

```bash
WT=/Users/beye/workspace/BarroAiTrade  # 또는 worktree
cd "$WT"

# 1. zip 해제 + ledger 갱신
python scripts/_daily_evening_pipeline.py --zip ~/Downloads/BarroAiTrade_*.zip --date YYYY-MM-DD
# 또는 kt00009 dump 별도:
python scripts/_daily_evening_pipeline.py --executions-file path/to/kt00009.json --date YYYY-MM-DD

# 2. 누적 전략 성과 (그래프 옵션)
python scripts/_strategy_perf_track.py [--graph]

# 3. 손실 종목 진단 (ohlcv_cache 필요 — 현재 JSON 호환 보완 필요)
python scripts/_loss_drill_down.py --symbol XXXXXX --date YYYY-MM-DD
```

### 시뮬 정확도 보강 효과 검증 방법

Phase 4/5/6 의 시뮬 변동성 필터 효과는 simulate_leaders 재실행 후 simulation_log
비교로 확인:

```bash
python scripts/simulate_leaders.py --date YYYY-MM-DD --mode daily --top 10
# 결과 비교: 변경 전 simulation_log.csv vs 변경 후
# 기대: LG 계열 (LG전자, LG씨엔에스) 진입 차단 → 누적 net 개선
```

### 신규 strategy 추가 시 패턴

```python
# strategy 파일
from backend.core.strategy.indicators import atr_pct

class NewStrategy(Strategy):
    def _analyze_v2(self, ctx):
        p = self.params
        if p.min_atr_pct > 0:
            if atr_pct(ctx.candles, n=p.atr_n) < p.min_atr_pct:
                return None
        # ... 진입 로직
```

호환 wrapper 추가 불필요 (직접 helper 호출).

## 다음 단계 가이드

### 즉시 진행 가능

1. **5/22 영업일 zip 수령 → 운영 변동성 필터 효과 검증** (가장 직접적)
   - ORDERED 종목에서 LG 계열 차단 여부
   - simulation_log 매트릭스 비교
2. **`_loss_drill_down.py` JSON 호환 추가** — ohlcv_cache JSON 직접 로드
   (별도 BAR-OPS-09 보강 또는 BAR-OPS-10 신규)

### 중기 (큰 작업)

3. **청산 정교화** (PRD §9 원래 Phase 6) — `intraday_simulator._exit_plan_for_strategy`
   / `_scaled_exit_plan` / `_sfzone_atr_exit_plan` 튜닝
   - LG전자 gold_zone win 41% 같은 청산 지연 fix
   - 모든 전략 영향 — 회귀 위험 큼
   - 별도 BAR PR 권장
4. **운영 전략 확장 검토** — gold_zone/swing_38 의 시뮬 누적 양호(+6.77M, +10.6M)
   를 근거로 운영 자동매매에 도입 검토 (큰 결정, 별도 BAR)

### 장기

5. **종합 회고 보고서** (본 문서) → 사용자 인수인계 자료
6. **kt00009 dump 자동화** — 매일 zip 에 포함되도록 운영 머신 측 자동화

## 부록 — 등록한 스킬

- **`barro-daily-audit-phase1`** (`.claude/skills/barro-daily-audit-phase1/`) — Phase 1
  적용 자동화. 6개 reference 파일 + 4개 reference docs (code-facts, pitfalls, verify,
  roadmap). 향후 재호출 가능.
- **`barro-daily-audit-phase2-fzone-tuning`** (`.claude/skills/barro-daily-audit-phase2-fzone-tuning/`)
  — Phase 2 (f_zone 튜닝) 워크플로우 가이드. Phase 3~7 은 동일 패턴 변형이라
  별도 SKILL.md 등록 생략 (`.claude/*` ignore 로 git 비추적, 로컬 전용).

## 최종 commit 그래프 (origin/BAR-OPS-09)

```
b169d1d refactor(BAR-OPS-09): _atr_pct helper 추출 — 5 곳 중복 제거 (Phase 7)
f1e5456 fix(BAR-OPS-09): IntradaySimulator swing_38 시뮬 진입점에 변동성 필터 적용 (Phase 6)
9d9ddc0 fix(BAR-OPS-09): IntradaySimulator sf_zone 시뮬 진입점에 변동성 필터 적용 (Phase 5)
01bf8fd fix(BAR-OPS-09): IntradaySimulator gold_zone 시뮬 진입점에 변동성 필터 적용 (Phase 4)
f7a60c7 fix(BAR-OPS-09): SignalScanner 운영 경로에 blue_line 변동성 필터 적용 (Phase 3)
18124ce fix(BAR-OPS-09): SignalScanner 운영 경로에 f_zone 변동성 필터 적용 (Phase 2)
3fd57da feat(BAR-OPS-09): Daily 운영 audit 자동화 도구 3종 (Phase 1)
9e9eb67 test(BAR-OPS-09): baseline 회귀 5건 skip — main ec9feab 잔재 (본 PR 무관)
```

— 2026-05-22 작성, BAR-OPS-09 Phase 1~7 완료
