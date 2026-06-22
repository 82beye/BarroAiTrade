# BarroAiTrade 매매전략 심층 리포트 — blue_line (블루라인)

> 생성: 2026-06-22 · 진실원천: 코드 인용(file:line) · origin/main 기준
> 상태: ⚪ 구현됨·비활성 (default OFF) · 분류: 1분봉 단타(이평 돌파) · 컨셉: 5EMA(블루라인)×20EMA 골든크로스 + 거래량 급증 추세추종

## 1. 요약 (TL;DR)

- **무엇**: 단기 5EMA("블루라인")가 중기 20EMA를 상향 돌파(골든크로스)하거나, 가격이 블루라인 위에서 지지받고 재상승할 때 진입하는 1분봉 추세추종 단타. (`backend/core/strategy/blue_line.py:1-9`, `:46`)
- **진입 게이트**: `(골든크로스 OR 블루라인 지지반등) AND 상승률 ≥ 0.5% AND 거래량 ≥ 평균×1.5` 의 AND 결합. 셋 중 하나라도 깨지면 미진입. (`blue_line.py:113`)
- **점수**: 기본 6.0 + 상승률 보너스(0~2.0) + 거래량 보너스(0~2.0), 상한 10.0. (`blue_line.py:115`, `:122`)
- **청산**: 전용 `exit_plan` override **없음** → base 기본값(SL=-2%, TP 없음) 사용. `STRATEGY_EXIT_PROFILES` 에 `blue_line` 항목 **없음** → 운영 적응형 매도는 default `ExitPolicy`(TP +5% / SL -4%) 로 fallback. (`base.py:71-76`, `holding_evaluator.py:104-170`, `:178-179`)
- **운영 상태**: `blue_line=False` (⚪ 구현됨·비활성). 단타 전용 모드(sf/f/gold/swing_38)로 전환되며 "단타 전략 완성 후 재개" 보류. 안정성 priority 6/8 로 낮음. (`signal_scanner.py:41-55`, `:59-62`)

---

## 2. 전략 개요 (블루라인 = 이평선 돌파 컨셉)

모듈 docstring 이 컨셉을 명시한다 (`blue_line.py:1-9`):

```python
"""
블루라인 전략 (Blue Line Strategy)

원리:
  단기 이동평균선(5일 EMA) + 중기 이동평균선(20일 EMA)이 골든크로스되고
  거래량이 증가할 때 진입하는 추세 추종 전략.

  "블루라인" = 5일 EMA. 주가가 블루라인 위에서 지지받고 재상승할 때 매수.
"""
```

- **블루라인 = 5EMA**, 중기선 = 20EMA. (`blue_line.py:29-30`, `:91-92`)
- 두 가지 트리거를 OR 로 묶는다: ① **골든크로스**(5EMA가 20EMA를 막 상향 돌파), ② **블루라인 지지 반등**(저가가 5EMA에 근접했으나 종가는 5EMA 위 마감). (`blue_line.py:99-108`, `:114`)
- docstring 은 "5일 EMA / 20일 EMA"라 적지만 운영 dispatch 는 **1분봉**(`timeframe="1m"`) 으로 호출되므로, 실제로는 "최근 5봉/20봉 지수이평"이다(일·분봉 무관 동일 공식). (`signal_scanner.py:87`, `:163`, `:169-176`)
- 지표는 외부 헬퍼가 아닌 **pandas `ewm(span=…, adjust=False)`** 로 EMA를 직접 계산. 변동성 필터용으로만 공통 `indicators.atr_pct` 를 wrapper 로 호출. (`blue_line.py:91-92`, `:147-151`, `indicators.py:21-51`)

---

## 3. 진입 로직 (조건·점수·게이트) ← 코드 인용 필수

진입 본체는 `_analyze_impl` (`blue_line.py:58-134`). 순서대로:

**0) 데이터 길이 게이트** — `len(candles) < min_candles(=60)` 이면 즉시 `None`. (`blue_line.py:66-67`, `:33`)

**1) 변동성 필터 (default OFF)** — `min_atr_pct > 0` 일 때만 동작. ATR% < 임계면 거부. default `min_atr_pct=0.0`(비활성) → 기존 회귀 보존. 운영 적용은 SignalScanner 명시 override(`BlueLineParams(min_atr_pct=0.035)`). (`blue_line.py:35-37`, `:70-77`)

**2) 진입 시간 게이트 (default OFF)** — `entry_time_cutoff` 가 None 이 아니고 마지막 candle 시각 ≥ cutoff 면 거부(장 후반 진입 손실 차단, Phase 8f). default None → 비활성. 운영은 `dtime(14, 0)` override 권장. (`blue_line.py:40-43`, `:79-87`)

**3) 지표 산출** (`blue_line.py:89-96`):
```python
ema_short = df["close"].ewm(span=p.short_period, adjust=False).mean()   # 5EMA
ema_long  = df["close"].ewm(span=p.long_period,  adjust=False).mean()   # 20EMA
avg_volume = df["volume"].mean()
current = df.iloc[-1]; prev = df.iloc[-2]
```
> 주의: `avg_volume` 은 `df["volume"].mean()` — **전체 candle 구간(최대 candle_limit=120봉) 평균**이다. `prev` 변수는 산출되나 진입 조건에서 직접 쓰이지 않는다. (`blue_line.py:93`, `:96`, `signal_scanner.py:88`)

**4) 트리거 A — 골든크로스** (`blue_line.py:99-102`):
```python
golden_cross = (
    ema_short.iloc[-2] <= ema_long.iloc[-2]      # 전봉: 5EMA ≤ 20EMA (데드)
    and ema_short.iloc[-1] > ema_long.iloc[-1]   # 현봉: 5EMA > 20EMA (골든)
)
```

**4) 트리거 B — 블루라인 지지 반등** (`blue_line.py:104-108`):
```python
on_blue_line = (
    current["low"] <= ema_short.iloc[-1] * 1.005   # 저가가 5EMA의 +0.5% 이내로 근접
    and current["close"] > ema_short.iloc[-1]       # 종가는 5EMA 위 마감
)
```

**5) 보조 게이트 — 상승률·거래량** (`blue_line.py:110-111`):
```python
gain_pct  = (current["close"] - current["open"]) / current["open"]   # 당봉 시가→종가 상승률
volume_ok = current["volume"] >= avg_volume * p.volume_ratio          # 거래량 ≥ 평균×1.5
```

**6) 최종 진입 판정 (AND 결합)** (`blue_line.py:113`):
```python
if (golden_cross or on_blue_line) and gain_pct >= p.min_gain_pct and volume_ok:
```
즉 **(트리거 A OR B) AND 상승률 ≥ 0.5% AND 거래량 1.5x** 를 모두 만족해야 진입.

**7) 점수 산정** (`blue_line.py:115`, `:122`):
```python
score = 6.0 + min(gain_pct/0.03, 1.0)*2.0 + min(current["volume"]/(avg_volume*1.5), 1.0)*2.0
# 최종: round(min(score, 10.0), 2)
```
- 베이스 6.0. 상승률 3%에서 만점(+2.0), 거래량이 (평균×1.5)에 도달하면 만점(+2.0).
- **최소 점수 ≈ 6.0**(게이트 통과 시점에 gain≥0.5%·vol≥1.5x 이므로 보너스가 0은 아님), **상한 10.0**.
- 점수 임계 컷(예: score≥7.0)은 본 전략 코드에 **없음** — 게이트 통과 즉시 시그널 발행. 슬롯 경합 시 SignalScanner 가 점수·priority 로 정렬. (`signal_scanner.py:136-137`)

**8) 시그널 산출** — `EntrySignal(signal_type="blue_line", strategy_id="blue_line_v1", price=종가, metadata={ema_short, ema_long, volume_ratio, trigger})`. trigger 는 골든크로스 우선 라벨링. (`blue_line.py:113-133`, `:49`, `:121`, `:125`)

진입점은 v2 `_analyze_v2(ctx)` → `_analyze_impl(...)`. SignalScanner 는 base `Strategy.analyze(symbol, name, candles, market_type)` legacy 시그니처로 호출(DeprecationWarning 경유 변환). (`blue_line.py:54-56`, `base.py:42-62`, `signal_scanner.py:176`)

---

## 4. 청산 로직 (전용 profile 유무 명시)

**전용 청산 로직이 두 계층 모두에 없다 — 둘 다 fallback 사용.**

**(a) ExitEngine 계층 (Strategy.exit_plan, 분봉 close 기반)**
- `BlueLineStrategy` 는 `exit_plan` / `exit_on_signal` 를 **override 하지 않는다** (`blue_line.py` 전체에 정의 없음 — `def exit_plan` 은 base.py·f_zone.py 에만 존재).
- 따라서 base 기본값 사용: **TP 없음(`take_profits=[]`), SL = 고정 -2%** (`base.py:71-76`):
```python
def exit_plan(self, position, ctx) -> ExitPlan:
    return ExitPlan(take_profits=[], stop_loss=StopLoss(fixed_pct=Decimal("-0.02")))
```

**(b) HoldingEvaluator 계층 (broker pnl_rate 기반 적응형 2차 안전망)**
- `STRATEGY_EXIT_PROFILES` 에 **`blue_line` 키 없음** (정의된 키: `f_zone`, `sf_zone`, `gold_zone`, `swing_38`, `closing_bet`). (`holding_evaluator.py:104-170`)
- `resolve_policy()` 는 매칭 실패 시 base `ExitPolicy` 를 **그대로 반환** → blue_line 포지션은 default 정책 적용. (`holding_evaluator.py:177-179`)
- 적용되는 default `ExitPolicy` (`holding_evaluator.py:56-66`):
  - TP +5.0% / SL -4.0% / 트레일링 시작 +3.0%·offset 1.5% / 브레이크이븐 트리거 +2.5% / 분할익절 +3.5%(50%) / 보유 5일 후 SL -2.0% 강화.
  - `min/max_hold_days = None` (intraday 회귀 보존 — 보유기간 게이트 미적용).
- 단기고점(SHORT_TERM_HIGH)·국면적응·net-aware·distribution 청산은 모두 default-OFF 또는 ctx 주입 의존이라 blue_line 에 특별 적용 없음. (`holding_evaluator.py:313-346`, `:198-221`)

> 정리: blue_line 은 1차 방어선 **-2%(ExitEngine base)**, 2차 안전망 **-4% / TP+5%(HoldingEvaluator default)** 로 청산된다. 다른 활성 단타(f/sf/gold)가 갖는 전략별 튜닝 profile 이 blue_line 엔 없다.

---

## 5. 파라미터 표

`BlueLineParams` (`blue_line.py:27-43`). 비용·게이트 임계 모두 코드 default 인용.

| 파라미터 | default | 의미 | 비고 (file:line) |
|---|---|---|---|
| `short_period` | `5` | 단기 EMA(블루라인) span | `blue_line.py:29` |
| `long_period` | `20` | 중기 EMA span | `blue_line.py:30` |
| `volume_ratio` | `1.5` | 거래량 배율 기준 (≥ 평균×1.5) | `blue_line.py:31` |
| `min_gain_pct` | `0.005` | 당봉 최소 상승률 0.5% | `blue_line.py:32` |
| `min_candles` | `60` | 최소 candle 수(미만이면 미분석) | `blue_line.py:33` |
| `min_atr_pct` | `0.0` | 변동성 필터(0=비활성). 운영 override 0.035 | `blue_line.py:37`, `:70` |
| `atr_n` | `14` | ATR 평균 봉 수 | `blue_line.py:38` |
| `entry_time_cutoff` | `None` | 진입 시간 cutoff(None=비활성). 운영 14:00 권장 | `blue_line.py:43`, `:80` |

파생 상수(코드 하드코딩, 파라미터 아님):
- 블루라인 근접 허용폭 `*1.005` (+0.5%) — `blue_line.py:106`
- 점수 상승률 만점 기준 `/0.03` (3%) — `blue_line.py:115`
- 점수 베이스 `6.0`, 보너스 각 `*2.0`, 상한 `10.0` — `blue_line.py:115`, `:122`
- `STRATEGY_ID = "blue_line_v1"` — `blue_line.py:49`

운영 청산 default (profile 부재 → 적용되는 `ExitPolicy`): TP `+5.0%` / SL `-4.0%` / trailing_start `+3.0%` / trailing_offset `1.5%` / breakeven `+2.5%` / partial_tp `+3.5%`(50%) / hold_days_tighten `5일`→SL `-2.0%`. (`holding_evaluator.py:56-66`)

---

## 6. 활성·운영 상태 (비활성 이유·토글 방법)

**상태**: `blue_line: False` (⚪ 구현됨·비활성). (`signal_scanner.py:46`)

**비활성 근거 (코드 주석 직접 인용)**:
- SignalScanner docstring (`signal_scanner.py:4-8`): "활성(default, 1분봉 intraday 단타): sf_zone, f_zone, gold_zone / 비활성(default): blue_line, crypto_breakout, swing_38 · **단타 전략 완성 이후 재개 예정** — `enabled_strategies` 인자로 override 가능".
- `_DEFAULT_ENABLED` 주석 (`signal_scanner.py:38-40`): "Phase D2.1(2026-05-28) — 단타 전용 모드 default. 사용자 결정: 1·2·6번(sf/f/gold) 만 활성, 나머지(blue/crypto/swing_38) **단타 전략 완성 후 재개**." → 즉 비활성 사유는 (i) 단타 라인업 정리(sf/f/gold 우선) + (ii) 재개 보류 결정.
- 안정성 우선순위가 낮음: `STRATEGY_PRIORITY["blue_line"] = 6` (8개 중 6위, 작을수록 우선). swing_38(1)·gold_zone(2)·f_zone(3)·sf_zone(4)·supertrend(5) 뒤. (`signal_scanner.py:59-62`)
- 분석 문서도 "비활성(참조)"로 분류, "5EMA×20EMA 골든크로스 + 거래량 1.5x"로만 요약. (`docs/04-report/features/2026-05-28-daytrading-strategies-analysis.md:6`, `:307-308`)

> 성능 미검증 측면: 실 OHLCV 백테스트가 아닌 합성 GBM 베이스라인만 존재(§8 참조). 재개 보류 사유는 명시적으로 "단타 전략 완성 후 재개"이며, swing_38 처럼 백테스트로 재활성된 사례(BAR-OPS-33)와 달리 blue_line 은 재검증·재활성 이력이 코드에 없다.

**토글 방법** (`signal_scanner.py:40`, `:99-102`, `:188-194` 테스트):
```python
scanner = SignalScanner(gateway, enabled_strategies={"blue_line": True})
# override 는 default 와 병합 — 지정 안 한 키(sf/f/gold/swing_38)는 default 유지.
# 활성 시 1분봉 dispatch 에 우선순위 SF > F > Gold > Blue > Crypto 순으로 합류.
```
인스턴스는 비활성이라도 생성되어 있어(`signal_scanner.py:104-109`) flag 토글만으로 재활성 가능. dispatch 합류 위치: `signal_scanner.py:162-163`.

---

## 7. 비용·손익분기 관점

공통 비용 가정: **편도 0.35% / 매도세 0.20% / 왕복 0.90%** (= 매수 0.35% + 매도 0.35% + 매도세 0.20%).

- **진입 게이트 vs 비용**: 진입 최소 상승률 `min_gain_pct=0.005`(0.5%)는 당봉 1개의 시가→종가 상승률 조건일 뿐, 진입 후 목표수익이 아니다. 왕복 비용 0.90%를 넘기려면 진입 후 추가로 +0.9%p 이상 올라야 본전. (`blue_line.py:32`, `:110`)
- **청산 임계 vs 비용 (default profile 기준)**:
  - SL -4% / 분할익절 +3.5% / TP +5% (gross 기준, `holding_evaluator.py:56-66`).
  - 왕복 0.90% 차감 시 net: 분할익절 +3.5% → **약 +2.6% net**, TP +5% → **약 +4.1% net**, SL -4% → **약 -4.9% net**.
  - ExitEngine 1차선 SL -2%(base)는 왕복 비용 차감 시 **약 -2.9% net** 손실로 청산.
- **net-aware TP 미적용**: `PositionContext.net_aware_tp` default False — blue_line 운영 경로에 명시 활성 코드 없음 → TP/익절은 gross 임계 그대로. (`holding_evaluator.py:214-216`, `:286-288`)
- **구조적 한계**: blue_line 전용 profile 이 없어 비용 대비 TP/SL 비율(손익비)이 전략 특성(1분봉 추세추종)에 맞춰 튜닝되지 않았다. f_zone/gold_zone 처럼 partial_tp·trailing 을 전략별로 조정한 값이 아닌 generic default 를 쓴다. (§4 비교)

> 위 net 환산은 본 리포트의 산술 계산이며 백테스트 실측이 아님(미검증).

---

## 8. 백테스트·OOS 근거(있으면)/한계·리스크

**근거가 있으나 합성 데이터 기반이며 실 OOS 아님.**

- PHASE-0 베이스라인(합성 GBM 1년 데이터) 결과 (`docs/04-report/PHASE-0-baseline-2026-05.md:39`, JSON `:107-113`):

| Strategy | 거래수 | 승률 | 누적수익 | MDD | Sharpe |
|---|---:|---:|---:|---:|---:|
| `blue_line_v1` | 12 | 58.3% | 1.82% | 0.62% | 5.38 |

- 문서 관찰: "blue_line_v1 가 가장 활발한 거래(12건) + 양의 수익. 합성 GBM 데이터의 마일드 트렌드를 잘 포착." (`PHASE-0-baseline-2026-05.md:44`)
- **명시된 한계 (인용)**: "합성 데이터 1년 결과로 전략 성과 절대 평가는 불가 / 본 표는 후속 PR 회귀 비교의 기준점 역할만 / 실 OHLCV 5년 백테스트는 BAR-44b 에서 수행 예정." (`PHASE-0-baseline-2026-05.md:48-51`)
- 이 수치는 **회귀 비교 기준점**일 뿐(±5% 임계, 예: win_rate ∈ [53.3%, 63.3%]) 실거래·실 OOS 근거가 아니다. (`PHASE-0-baseline-2026-05.md:55-68`)
- blue_line 에 대한 **실 OHLCV 그리드 백테스트나 OOS 검증 결과는 코드/docs 에서 확인되지 않음**(swing_38 의 Phase D 그리드 같은 산출물 부재) — 미검증.

**리스크**
- **전용 청산 profile 부재**: 1분봉 단타인데 청산은 generic default(TP+5/SL-4, ExitEngine base -2%)에 의존 → 전략-청산 정합성 미튜닝(§4).
- **거래량 기준의 약점**: `avg_volume` 이 전체 구간(최대 120봉) 단순평균이라, 장 초반/저거래 구간에서 1.5x 판정이 왜곡될 수 있음(코드 사실, 영향은 미검증). (`blue_line.py:93`)
- **운영 필터 default OFF**: `min_atr_pct`·`entry_time_cutoff` 가 default 비활성이라, 운영 재활성 시 명시 override 없이는 저변동·장후반 가짜 시그널에 노출. (`blue_line.py:37`, `:43`)
- **점수 컷 부재**: 게이트 통과 즉시 시그널(최소 ≈6.0점) → 슬롯 경합에서 우선순위는 SignalScanner 정렬에만 의존. (`blue_line.py:113`, `signal_scanner.py:136-137`)
- **성능 미검증 상태로 비활성**: 실데이터 검증 전이며 재활성 트리거(백테스트 통과 등)가 코드에 정의돼 있지 않음.

---

## 9. 관련 파일·테스트

**구현**
- `backend/core/strategy/blue_line.py` — `BlueLineParams`(`:27-43`) + `BlueLineStrategy._analyze_impl`(`:58-134`). 진입 판정 `:113`, 점수 `:115`.
- `backend/core/strategy/base.py` — `Strategy` ABC, default `exit_plan`(SL-2%/TP없음, `:71-76`), legacy analyze dispatch(`:42-62`).
- `backend/core/strategy/indicators.py` — `atr_pct`(`:21-51`, 변동성 필터용 wrapper 대상).
- `backend/core/scanner/signal_scanner.py` — `_DEFAULT_ENABLED["blue_line"]=False`(`:46`), `STRATEGY_PRIORITY["blue_line"]=6`(`:59-62`), dispatch 합류(`:162-163`), 토글 병합(`:99-102`).
- `backend/core/risk/holding_evaluator.py` — `STRATEGY_EXIT_PROFILES`(blue_line **부재**, `:104-170`), `resolve_policy` fallback(`:173-195`), default `ExitPolicy`(`:55-80`).

**테스트**
- `backend/tests/strategy/test_blue_line.py` — `TestBlueLineVolatilityFilter`(min_atr_pct 거부·default 0.0·atr_n 14) + `TestBlueLineEntryTimeGate`(cutoff None default·14:00 차단). 진입 핵심 로직(골든크로스/지지반등/점수) 직접 검증 테스트는 **부재** — 필터·게이트만 커버.
- `backend/tests/scanner/test_signal_scanner_phase_c.py` — `is_enabled("blue_line") is False`(`:112`), override 병합 시 blue_line True(`:188-194`), priority tiebreaker(`:199-218`).
- `backend/tests/strategy/test_baseline.py` — 합성 베이스라인 재현성(blue_line_v1 포함).

**문서**
- `docs/04-report/PHASE-0-baseline-2026-05.md` — blue_line_v1 합성 베이스라인 수치(`:39`, `:107-113`) + 한계 명시(`:48-51`).
- `docs/04-report/features/2026-05-28-daytrading-strategies-analysis.md` — 비활성 전략 참조(`:6`, `:307-308`).

---

*진실원천 주석*: 모든 수치·동작은 origin/main 기준 코드/docs 인용(file:line). §7 net 환산은 본 리포트 산술이며 백테스트 실측 아님. §8 baseline 은 합성 GBM 데이터 결과로 실 OOS·실거래 검증 아님(문서 자체 한계 명시). blue_line 실 OHLCV 백테스트·OOS·재활성 트리거는 코드/docs 에서 확인되지 않아 "미검증"으로 표기.
