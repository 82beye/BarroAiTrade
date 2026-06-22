# BarroAiTrade 매매전략 심층 리포트 — crypto_breakout (암호화폐 돌파)

> 생성: 2026-06-22 · 진실원천: 코드 인용(file:line) · origin/main 기준(013d54b)
> 상태: ⚪ 구현됨·비활성 (default OFF) · 분류: intraday(1분봉) 돌파 · 적용시장: crypto 전용 · 컨셉: 박스권 횡보 상단을 거래량 동반으로 돌파 시 진입

## 1. 요약 (TL;DR)

- **무엇**: 최근 N봉 박스권(횡보) 상단 저항선을 거래량 급증과 함께 돌파할 때 매수 신호를 내는 단순 돌파 전략. 진입 로직만 122줄로 구현돼 있고 청산 로직은 전략 자체에 없음(`crypto_breakout.py:1-122`).
- **시장 제약(하드 게이트)**: `market_type != MarketType.CRYPTO` 면 즉시 `None` 반환 — **암호화폐 전용**(`crypto_breakout.py:57-58`). 한국주식(`MarketType.STOCK`)에는 절대 신호를 내지 않음.
- **현 상태**: `_DEFAULT_ENABLED["crypto_breakout"] = False` (`signal_scanner.py:46`). default 비활성. `STRATEGY_PRIORITY=7`(8개 중 7위, `signal_scanner.py:61`).
- **이중 비활성**: flag 가 False 일 뿐 아니라, 현재 모든 라이브 게이트웨이가 `MarketType.STOCK` 만 발행 — crypto OHLCV 를 공급하는 라이브 게이트웨이가 없음(아래 §6). 즉 **토글을 켜도** 한국주식 환경에선 신호가 0건.
- **검증 한계**: 전용 단위 테스트·백테스트·OOS 근거 **없음**. 기존 테스트는 상속/통합 스모크 수준뿐(`test_base.py:159-163`, `test_baseline.py:77`). 진입 점수·청산 정책 모두 **미검증**.

## 2. 전략 개요 (돌파 컨셉·적용 시장)

모듈 docstring(`crypto_breakout.py:1-11`)이 명시하는 3단계 원리:

1. **박스권 탐지**: 최근 N봉의 고점/저점 범위가 좁을 때(횡보 구간).
2. **돌파 신호**: 현재 가격이 박스권 고점 +버퍼% 초과.
3. **거래량 확인**: 돌파 봉의 거래량이 평균 대비 크게 증가.

docstring 은 "암호화폐 시장의 높은 변동성에 맞게 파라미터 조정"이라 밝히며(`crypto_breakout.py:4-6`), 이 의도는 코드의 시장 하드 게이트로 강제된다:

```python
# crypto_breakout.py:57-58
if market_type != MarketType.CRYPTO:
    return None
```

`MarketType` 은 `STOCK`/`CRYPTO` 2종(`backend/models/market.py:14-16`). 따라서 본 전략은 **crypto 전용** 이며, 스캐너 timeframe 매트릭스상 1분봉(intraday) 전략으로 분류된다(`signal_scanner.py:12-13`, intraday dispatch 경로 `signal_scanner.py:164-165`).

## 3. 진입 로직 (조건·점수·게이트)

진입 본체는 `_analyze_impl`(`crypto_breakout.py:50-111`). 순차 게이트는 다음과 같다.

**G0. 시장 게이트** — crypto 아니면 즉시 탈락:
```python
# crypto_breakout.py:57-58
if market_type != MarketType.CRYPTO:
    return None
```

**G1. 데이터 충분성** — `min_candles`(=40) 미만이면 탈락(`crypto_breakout.py:61-62`).

**G2. 박스권 횡보 판정** — 현재 봉을 제외한 직전 `box_period`(=20)봉의 고/저 범위가 `box_max_range_pct`(=8%) 이내여야 횡보로 인정:
```python
# crypto_breakout.py:68-76
box = df.iloc[-(p.box_period + 1):-1]
box_high = box["high"].max()
box_low = box["low"].min()
box_range_pct = (box_high - box_low) / box_low if box_low > 0 else 999
is_ranging = box_range_pct <= p.box_max_range_pct
if not is_ranging:
    return None
```
주: `box_low <= 0` 인 비정상 데이터는 `999` 로 처리돼 횡보 판정에서 탈락(방어 코드).

**G3. 돌파 확인** — 현재 종가가 박스 고점 ×(1+버퍼) 미만이면 탈락:
```python
# crypto_breakout.py:78-81
breakout_level = box_high * (1 + p.breakout_buffer_pct)   # 버퍼 1%
if current["close"] < breakout_level:
    return None
```

**G4. 거래량 게이트** — 현재 봉 거래량이 (현재 봉 제외) 평균 대비 `volume_ratio`(=2.5배=250%) 미만이면 탈락:
```python
# crypto_breakout.py:66, 83-86
avg_volume = df["volume"].iloc[:-1].mean()
...
vol_ratio = current["volume"] / avg_volume if avg_volume > 0 else 0
if vol_ratio < p.volume_ratio:
    return None
```

**점수 산식** — 0~10 클램프, 기본 5.0 + 돌파폭 가산(최대 +2.5) + 거래량 가산(최대 +2.5):
```python
# crypto_breakout.py:88-89
breakout_pct = (current["close"] - box_high) / box_high
score = 5.0 + min(breakout_pct / 0.05, 1.0) * 2.5 + min(vol_ratio / (p.volume_ratio * 2), 1.0) * 2.5
```
- 돌파폭 5%(0.05)에서 가산 만점(+2.5), 거래량은 `volume_ratio*2`(=5.0배)에서 가산 만점(+2.5).
- 모든 게이트 통과 + 돌파폭 ≥5% + 거래량 ≥5배면 이론상 만점 10.0.
- 최소 통과 점수(게이트 4개 모두 막 통과 시점)는 5.0 근처 — **별도 최소 점수 컷오프 게이트는 없음**(게이트 통과 = 신호 발생). 점수는 스캐너 정렬·슬롯 경합용으로만 쓰임(`signal_scanner.py:137`).

**산출물** — `EntrySignal(signal_type="crypto_breakout", strategy_id="crypto_breakout_v1", ...)` 에 `metadata` 로 box_high/box_low/box_range_pct/volume_ratio/breakout_pct 기록(`crypto_breakout.py:91-111`). `STRATEGY_ID="crypto_breakout_v1"`(`crypto_breakout.py:41`).

> 주(지표): 이 전략은 `indicators.py` 의 ATR·RSI·HTF 리샘플 헬퍼를 **사용하지 않는다**(import 없음, `crypto_breakout.py:12-24`). 변동성 필터(`atr_pct`)·RSI 확인·상위 타임프레임 게이트가 전혀 없는 순수 가격/거래량 돌파 로직이다 — 다른 단타 전략(f_zone/sf_zone 등) 대비 필터가 단순.

## 4. 청산 로직

**전략 자체에 청산 로직 없음.** `CryptoBreakoutStrategy` 는 `exit_plan()` 을 override 하지 않으므로(`crypto_breakout.py` 전체에 정의 없음) 베이스 클래스 default 가 적용된다:
```python
# backend/core/strategy/base.py:71-76
def exit_plan(self, position, ctx) -> ExitPlan:
    """청산 계획 — 기본은 SL=-2%, TP 없음."""
    return ExitPlan(take_profits=[], stop_loss=StopLoss(fixed_pct=Decimal("-0.02")))
```
즉 ExitEngine 경로에서는 **고정 SL -2%, TP 미설정**.

**운영 적응형 매도(HoldingEvaluator)에도 전용 프로파일 없음**. `STRATEGY_EXIT_PROFILES`(`holding_evaluator.py:104-170`)에는 f_zone/sf_zone/gold_zone/swing_38/closing_bet 만 정의돼 있고 **crypto_breakout 키가 없다**. `resolve_policy()`(`holding_evaluator.py:173-179`)는 매칭 실패 시 base `ExitPolicy` 를 그대로 반환하므로, crypto_breakout 포지션은 기본 적응형 정책으로 평가된다:
- TP +5.0%, SL -4.0%, 트레일링 시작 +3.0%/오프셋 1.5%, 브레이크이븐 +2.5%, 분할익절 +3.5%(50%) 등(`holding_evaluator.py:56-66` 기본값).

요약: 청산은 (a) ExitEngine = 베이스 SL -2%, (b) HoldingEvaluator = 전략 무관 기본 ExitPolicy. **돌파 실패(되돌림) 전용 청산·박스 하단 재진입 손절 등 컨셉 맞춤 청산이 전혀 없다** — 설계 미완.

## 5. 파라미터 표

`CryptoBreakoutParams`(`crypto_breakout.py:29-35`):

| 파라미터 | 기본값 | 의미 | 코드 위치 |
|---|---|---|---|
| `box_period` | 20 | 박스권 탐지 기간(봉 수) | crypto_breakout.py:31 |
| `box_max_range_pct` | 0.08 (8%) | 박스권 최대 범위(이내=횡보) | crypto_breakout.py:32 |
| `breakout_buffer_pct` | 0.01 (1%) | 돌파 버퍼 | crypto_breakout.py:33 |
| `volume_ratio` | 2.5 (250%) | 거래량 배율(평균 대비) | crypto_breakout.py:34 |
| `min_candles` | 40 | 최소 캔들 수 | crypto_breakout.py:35 |

점수 산식 내 상수(`crypto_breakout.py:89`): 돌파폭 만점 기준 `0.05`(5%), 거래량 만점 기준 `volume_ratio*2`(=5.0배). 베이스 점수 5.0.

청산 측 기본값(전략 전용 아님): SL -2%(ExitEngine base, `base.py:75`) / 기본 ExitPolicy TP +5%·SL -4%(`holding_evaluator.py:57-58`).

> 주: 위 파라미터는 코드 default 값이며, 라이브에서 검증·튜닝된 값이라는 근거는 코드/테스트에 없음(미검증).

## 6. 활성·운영 상태 (비활성 이유·적용시장 제약·토글)

**Default 비활성**:
```python
# signal_scanner.py:41-46
_DEFAULT_ENABLED = {
    "sf_zone": True, "f_zone": True, "gold_zone": True,
    "blue_line": False,
    "crypto_breakout": False,  # 4번 비활성
    ...
}
```
스캐너 docstring(`signal_scanner.py:4-8`)이 밝힌 이유: "BAR-OPS-09 Phase D2.1(2026-05-28) 단타 전용 모드" — sf/f/gold 만 활성, **blue_line·crypto_breakout·swing_38 은 단타 전략 완성 이후 재개 예정**(swing_38 은 이후 BAR-OPS-33 으로 재활성, `signal_scanner.py:47-50`). crypto_breakout 은 비활성 유지.

**이중 비활성(더 근본적 제약)** — 시장 미연결:
- 본 전략은 `MarketType.CRYPTO` 봉에서만 작동(`crypto_breakout.py:57`).
- 그러나 라이브 게이트웨이(kiwoom 계열)는 모두 `MarketType.STOCK` OHLCV 만 발행(`kiwoom.py:75` `market_type = MarketType.STOCK`, `kiwoom_native_candles.py:242,255` 등).
- crypto 게이트웨이는 `StubUpbitGateway` 등 **stub 뿐**이며 `market_type = "crypto"`(문자열, `MarketType.CRYPTO` enum 도 아님)에 `fetch_ticker` 가 가격 0 을 반환하는 미구현 상태(`extensions.py:58-71`). docstring 도 "운영 진입 시 Upbit/Bithumb 어댑터로 교체, worktree 단계는 인터페이스+stub"(`extensions.py:1-5`)라 명시.
- 결론: **flag 를 켜도(`enabled_strategies={"crypto_breakout": True}`) 현재 시스템에선 신호 0건** — crypto OHLCV 공급원이 없기 때문. 현 시스템은 한국주식 중심.

**토글 방법**(코드 변경 없이):
```python
SignalScanner(gateway, enabled_strategies={"crypto_breakout": True})
```
override 키만 덮어쓰며 나머지는 default 유지(`signal_scanner.py:99-102`). 단 위 시장 제약 때문에 실효성은 crypto 게이트웨이 구현 이후에야 발생. 전략 인스턴스 자체는 비활성이라도 항상 생성됨(`signal_scanner.py:104-110`).

## 7. 비용·손익분기 관점

- **공통 비용 모델은 한국주식 전용**: `trading_costs.py` 의 `COMMISSION_RATE=0.0035`(편도 0.35%), `TAX_RATE_SELL=0.0020`(매도세 0.20%), `ROUND_TRIP_COST_RATE = 0.0035*2 + 0.0020 = 0.0090`(왕복 0.90%)(`trading_costs.py:29-33`). 근거는 키움 fill_audit 실측(`trading_costs.py:1-11`).
- **crypto 전용 비용 모델 없음**: 코드베이스에 암호화폐 거래소(Upbit/Bithumb 등) 수수료·매도세 구조를 반영한 별도 cost 모델이 **존재하지 않는다**(검색 결과 `trading_costs.py` 단일 파일, crypto 분기 없음). 따라서 crypto_breakout 의 손익분기를 정확히 산정할 코드 근거가 현재 없음(미검증).
- **참고(주의)**: 만약 한국주식 비용(왕복 0.90%)을 그대로 적용하면, 진입 점수 만점 조건의 돌파폭 5%·박스폭 8% 대비 비용 비중은 작아 보이나 — 실제 crypto 거래소 비용(예: 일반적으로 거래소별 0.04~0.25%/leg + 호가 슬리피지)은 코드에 반영돼 있지 않아 **정량 결론 불가**. crypto 라이브 진입 전 전용 cost 모델 도입이 선결 과제.
- 청산이 SL -2%(ExitEngine base)로 좁아, 잦은 false breakout 되돌림 시 비용+손절 중첩으로 기대값이 음(-)이 될 위험 — 단, 이는 검증 데이터가 아닌 구조적 추론(미검증).

## 8. 백테스트·OOS 근거(있으면)/한계·리스크

**백테스트·OOS 근거 없음(코드/테스트 기준).**
- swing_38(BAR-OPS-33 백테스트 4~6월) 같은 정량 근거 주석이 crypto_breakout 에는 전혀 없음.
- baseline 테스트는 "거래 0건 전략도 무에러(수박/crypto_breakout 가능)"라 명시(`test_baseline.py:77`) — 즉 합성 데이터에서 신호가 거의/전혀 안 나오는 게 정상으로 취급됨. 성능 검증이 아님.

**관련 리스크/한계**:
1. **청산 미설계**: 돌파 컨셉 전용 청산(되돌림 손절·박스 재진입 청산) 부재. 베이스 SL -2% + 기본 ExitPolicy 에 의존(§4).
2. **필터 단순**: ATR 변동성 필터·RSI·HTF 확인 없음(§3 주). 저변동/가짜 돌파 차단 장치가 약함.
3. **시장 미연결**: crypto 라이브 게이트웨이·전용 비용 모델 부재로 즉시 운영 불가(§6,§7).
4. **거래량 평균의 노이즈**: `avg_volume` 이 박스 외 전체 직전 봉 평균(`df["volume"].iloc[:-1].mean()`, `crypto_breakout.py:66`)이라 box_period 와 일치하지 않음 — 의도/검증 불명(미검증).
5. **검증 부재**: 전용 단위 테스트 없음 — 점수 산식·게이트 경계값 회귀 테스트 미존재.

## 9. 관련 파일·테스트

**소스**
- `backend/core/strategy/crypto_breakout.py` (메인 122줄 — `CryptoBreakoutParams`:29-35, `_analyze_impl`:50-111)
- `backend/core/strategy/base.py` (Strategy ABC, default `exit_plan` SL -2%:71-76)
- `backend/core/scanner/signal_scanner.py` (default OFF:46, priority 7:61, intraday dispatch:164-165, 토글:99-102)
- `backend/core/risk/holding_evaluator.py` (`STRATEGY_EXIT_PROFILES`:104-170 — crypto_breakout **미등록**; `resolve_policy`:173-195)
- `backend/core/trading_costs.py` (한국주식 비용 단일 진실원천:29-33; crypto 분기 없음)
- `backend/core/strategy/indicators.py` (ATR/RSI/HTF 헬퍼 — 본 전략 **미사용**)
- `backend/core/gateway/extensions.py` (`StubUpbitGateway` crypto stub:58-71)
- `backend/models/market.py` (`MarketType.STOCK`/`CRYPTO`:14-16)
- `backend/models/signal.py` (`EntrySignal` signal_type literal 에 "crypto_breakout":18)

**테스트(전용 성능 테스트 없음 — 스모크/통합/매핑 수준)**
- `backend/tests/strategy/test_base.py:159-163` — 상속·`_analyze_v2` 존재 확인
- `backend/tests/strategy/test_baseline.py:77` — 거래 0건 무에러
- `backend/tests/scanner/test_signal_scanner_phase_c.py:98,113,174,194` — default 비활성·toggle 회귀
- `backend/tests/legacy_scalping/test_adapter.py:132` — `(즉시, crypto) → crypto_breakout` signal_type 매핑

---
*진실원천 주석: 본 리포트의 모든 수치·동작은 origin/main(013d54b) 코드 직접 인용(file:line)에 근거한다. 백테스트·OOS 성능, crypto 전용 거래비용, 라이브 crypto 게이트웨이는 코드/테스트에 존재하지 않아 "미검증"으로 명시했다. 추측 결론은 배제했고, 구조적 추론은 그 출처(코드 근거 vs 추론)를 본문에 구분 표기했다.*
