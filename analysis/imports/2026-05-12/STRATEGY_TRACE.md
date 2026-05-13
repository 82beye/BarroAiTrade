# 비활성 전략 추적 — 2026-05-12

**대상:** `f_zone`, `sf_zone`, `gold_zone`, `scalping_consensus` (모두 5종목 × 4 runs = 25 (symbol,run) 단위에서 trade=0)

## TL;DR

| 전략 | 0건 원인 | 카테고리 | 상태 |
|------|----------|----------|------|
| **f_zone** | 후보 종목이 모두 강한 상승 중(+11.89%~+20.03%) → 눌림목 -5%~-0.5% 미발생 | 종목 선정 ↔ 전략 미스매치 | 정책 검토 필요 |
| **sf_zone** | f_zone 상위 조건이 0이면 자동 0 + 추가 임계(기준봉 +5%·거래량 3x) | 종속 0 | 정책 검토 필요 |
| **gold_zone** | RSI ≤30 oversold 진입 자체가 없음 (상승 중인 주도주에서 구조적 미발생) | 종목 선정 ↔ 전략 미스매치 | 정책 검토 필요 |
| **scalping_consensus** | **provider 미주입 → `analyze()` 첫 라인에서 None 반환** | 🐛 시뮬 측 누락 (코드 버그) | ✅ **인터페이스 수정 완료** — 운영 provider는 별도 티켓 |

핵심 결론 두 가지:
1. **3-factor 주도주 선정과 f_zone/gold_zone은 구조적으로 비호환** — 거래대금·등락률 상위 종목은 정의상 "이미 강하게 오른" 종목이라 눌림목/oversold 조건이 발생하지 않음.
2. **scalping_consensus는 단순 미연결** — 운영 측 버그/누락이며, 데이터/시장과 무관.

---

## 1. f_zone — 눌림목 미발생

`backend/core/strategy/f_zone.py` 진입 조건 (AND):

| 단계 | 조건 | 라인 |
|------|------|------|
| 기준봉 | gain≥3% AND 거래량≥2.0x (최근 5봉) | l.277 |
| **눌림목** | **-5% ~ -0.5% AND 거래량≤0.7x (기준봉 후 10봉)** | l.316, 321 |
| 이평선 | 5/20/60 EMA ±1% 근접 | l.349 |
| 반등봉 | 양봉 AND 거래량≥1.2x | l.388 |
| 최종 점수 | ≥4.0 | l.437 |

**2026-05-12 후보 종목 등락률:**
- 319400 현대무벡스 **+20.03% → +11.89%** (4 runs 누적, 강한 상승)
- 066570 LG전자 **+17.61% / +17.23%**
- 090710 휴림로봇 **+14.19%**
- 003280 흥아해운 **+13.86%**
- 010170 대한광통신 +6.98% → +1.97%

→ 모두 일중 강한 상승. 눌림이 와도 보통 -5%보다 얕거나(되돌림 부족) 더 깊게(범위 이탈) 떨어짐. **-5%~-0.5%의 좁은 밴드 + 거래량 감소 동시 충족이 거의 불가.**

## 2. sf_zone — f_zone 종속 + 강화 임계

`backend/core/strategy/sf_zone.py:442-444`:
- f_zone 전체 조건 통과 **AND**
- 기준봉 gain≥**5%**, 거래량≥**3.0x**
- 종합 점수 ≥**7.0**

f_zone이 0이면 자동 0. 통과해도 임계가 1.67배 빡빡함.

## 3. gold_zone — RSI 2단계 조건

`backend/core/strategy/gold_zone.py`:

| 단계 | 조건 | 라인 |
|------|------|------|
| BB | 하단 1% 이내 진입 | l.113-115 |
| Fib | 최근 30봉 고저 0.382~0.618 | l.127 |
| **RSI(14)** | **최근 10봉 중 min ≤30 AND 현재 ≥40** | l.150-158 |
| 최종 점수 | ≥0.3 | l.74 |

문제: 강한 상승 추세인 주도주는 RSI가 보통 50+에서 시작해 60~80을 횡보. **최근 10봉 안에 RSI≤30 진입이 없음** → 조건 자체 미발생.

볼린저 하단 진입(BB) 역시 상승 추세에서는 잘 안 닿음.

## 4. scalping_consensus — 🐛 provider 미주입 → ✅ 인터페이스 정비됨

### 진단

`backend/core/strategy/scalping_consensus.py:66-67`:
```python
def analyze(self, ctx):
    if self._provider is None:
        return None
    ...
```

`set_analysis_provider(provider)` (l.56-62)로 외부에서 주입돼야 동작. 그런데 **시뮬레이터/실행 스크립트 어디서도 호출하지 않음** → 데이터/시장과 무관하게 항상 0건.

### 수정 (이번 변경)

진짜 12-에이전트 `ScalpingCoordinator`는 `List[StockSnapshot]` + intraday 틱데이터가 필요해서 시뮬의 `AnalysisContext(candles만)`와 직접 호환되지 않음(legacy module은 `zero-modification mirror` 원칙으로 import 경로 수정 불가). 따라서 **시뮬 측 인터페이스를 먼저 정비**하고, 운영 단계 진짜 provider 작성은 별도 작업으로 분리:

1. `IntradaySimulator.__init__(..., scalping_provider=None)` 옵션 추가
   - 파일: `backend/core/backtester/intraday_simulator.py`
2. `_build_strategies(strategy_ids, scalping_provider=None)`이 인스턴스에 provider 주입
3. `scalping_consensus` 포함하면서 provider 미주입 시 **명시적 warning 로그** (조용한 0건 방지)
4. 5개 단위 테스트 추가 (`backend/tests/backtester/test_intraday_simulator.py`):
   - `test_build_injects_provider` ✅
   - `test_build_without_provider_warns` ✅
   - `test_simulator_propagates_provider` ✅ (provider 주입 시 실제 trade 발생)
   - `test_low_score_provider_no_entry` ✅ (threshold 차단 검증)
   - `test_default_simulator_no_provider_backward_compat` ✅ (기존 호출 동작 보존)

### 사용 예 (운영 진입 시)

```python
from backend.core.backtester import IntradaySimulator
# 운영 시 작성할 진짜 provider — ScalpingCoordinator wrapper
def my_scalping_provider(ctx):
    # snapshot/intraday 변환 + ScalpingCoordinator.analyze() 호출
    # → ScalpingAnalysis 1건 또는 dict 반환
    ...

sim = IntradaySimulator(scalping_provider=my_scalping_provider)
result = sim.run(candles, symbol="005930")
```

### 후속 작업 (별도 티켓 권장)

- 운영용 `ScalpingCoordinatorProvider` 작성: AnalysisContext → StockSnapshot/intraday 변환 + ScalpingCoordinator 호출 wrapper
- legacy `from strategy.scalping_team...` import 경로 sys.path 부트스트랩 (또는 backend/legacy_scalping 패키지 import 어댑터)
- 시뮬 vs 운영 provider 결과 회귀 비교 (BAR-78에서 예고됨)

## 5. swing_38이 작동한 이유 (대조)

`backend/core/strategy/swing_38.py`:
- 임펄스: 최근 30봉 내 gain≥5% AND 거래량≥2x — 강한 상승 종목이면 거의 항상 충족
- Fib 0.382 되돌림 **±7.5% tolerance** — 넓은 허용 범위
- 직전봉 양봉만 확인
- 점수 ≥0.3 (느슨)

→ **조건 자체가 "오르는 종목"에 호의적**. 3-factor 주도주 선정과 궁합이 맞음.

## 5개 전략이 같은 데이터를 받는지

`backend/core/simulator/intraday_simulator.py:207-259`:
```python
for strategy, sid in zip(strategies_obj, strategy_ids):
    for i in range(warmup, len(candles)):
        window = candles[:i+1]  # 정확히 동일 슬라이딩 윈도우
        ctx = AnalysisContext(symbol=symbol, candles=window, ...)
        signal = strategy.analyze(ctx)
```

→ 5개 전략이 동일 candles, 동일 window를 받음. **데이터 차이 아님.**

## 정책 파일

`data/policy.json`: 전략별 enable/disable 필드 없음. 모든 전략 활성. min_score=0.6, TP=+5%, SL=-2%.

→ **비활성화 아님.**

## 권장 조치

| # | 조치 | 우선순위 |
|---|------|----------|
| 1 | **scalping_consensus provider 주입 라인 추가** (시뮬레이터 또는 실행 스크립트) — 단순 미연결 버그 | 🔴 즉시 |
| 2 | **3-factor 종목 선정과 전략 호환성 정책 결정** — 옵션 A: 약세장 종목도 후보에 포함, 옵션 B: f_zone/gold_zone을 다른 종목 풀(시총 상위·기관 매수 등)에서 별도 시뮬, 옵션 C: 해당 전략들을 일단 비활성화 표시 | 🟡 정책 |
| 3 | **전략별 거부 reason 로깅** — `analyze()` None 반환 직전에 어느 단계에서 탈락했는지 디버그 로그 남기면 본 보고서를 매일 자동 생성 가능 | 🟢 개선 |
| 4 | swing_38 단독 의존 리스크 — 횡보/하락장에서 swing_38도 0이 되는 시나리오 대비 | 🟢 모니터링 |

## 검증 명령

```bash
# scalping_consensus provider 미주입 재현
cd /Users/beye/workspace/BarroAiTrade
grep -rn "set_analysis_provider" backend/ scripts/

# f_zone/gold_zone이 약세 종목에서는 발화하는지 확인
# (별도 백테스트 필요 — 본 보고서 범위 밖)
```
