# BarroAiTrade 매매전략 심층 리포트 — sf_zone (SF존/슈퍼존)

> 생성: 2026-06-22 · 진실원천: 코드 인용(file:line) · origin/main 기준 (HEAD `013d54b`)
> 상태: 🟢 활성 (default ON) · 분류: 1분봉 단타 · 컨셉: F존 강화 버전 — 같은 진입 엔진을 공유하되 **SF 라벨이 붙는 더 강한 셋업(강한 기준봉 + 거래량 재증가 + 고점수)만** 통과시키고, 청산을 더 타이트/공격적으로 운용한다.

---

## 1. 요약 (TL;DR)

- **SF존은 독립 전략 클래스가 아니라 F존 엔진의 "필터 + 청산 오버라이드"다.** `SFZoneStrategy` 는 내부에 `FZoneStrategy` 인스턴스(`self._inner`)를 보유하고(delegate, 옵션 A), F존 분석 결과 중 **`signal_type == "sf_zone"` 인 것만** 통과시킨 뒤 `strategy_id` 만 `sf_zone_v1` 로 재라벨한다. (`backend/core/strategy/sf_zone.py:42,46-50`)
- **진입 게이트는 100% F존 코드와 동일.** SF 여부는 F존 `_score_and_classify` 의 `is_sf_zone` 분기가 결정한다: `is_f_zone AND impulse_gain_pct ≥ 0.05 AND impulse_volume_ratio ≥ 3.0 AND score ≥ 7.0`. (`backend/core/strategy/f_zone.py:602-607`) → **F존 대비 추가로 요구되는 것은 "강한 기준봉(+5%·300%) + 7점 이상"** 이다.
- **청산(exit_plan)이 SF의 진짜 차별점.** F존(TP 2단 +3%/+5%, SL −2%, breakeven +1.5%) 대비 SF는 **TP 3단(+3%/+5%/+7%), SL −1.5%(더 타이트), breakeven +1.0%(더 빨리 본전 잠금)**. (`sf_zone.py:56-84` vs `f_zone.py:346-370`)
- **운영 우선순위 1순위.** dispatch 리스트에서 SF가 가장 먼저 평가되어 시그널이 나오면 그 자리에서 반환된다(`signal_scanner.py:156-182`). 단, 동점 정렬 tiebreaker(`STRATEGY_PRIORITY`)에서는 `sf_zone=4` 로 오히려 후순위 — dispatch 순서와 tiebreaker 우선순위가 **불일치**한다(§6 주의).
- **2차 안전망(HoldingEvaluator)은 sf 전용 프로파일 보유** — `STRATEGY_EXIT_PROFILES["sf_zone"]` 존재(`holding_evaluator.py:115-124`), F존과 다른 값(TP 7%, partial 0.33, trailing offset 1.5%, breakeven 2.0%). SL은 −4%로 F존과 동일(의도된 1차/2차 격차).

---

## 2. 전략 개요 (F존과의 관계 — 무엇을 강화/추가하는가)

SF존(슈퍼존)은 F존(급등 기준봉 → 눌림목 → 이평선 지지 → 반등)의 상위 등급 셋업이다. 코드상 구현은 BAR-47에서 "F존 클래스 내부의 SF 분기를 별도 클래스로 분리"한 것으로, **옵션 A(delegate)** 를 채택했다. (`sf_zone.py:3-5`)

핵심 구조 (`sf_zone.py:38-50`):

```
class SFZoneStrategy(Strategy):
    STRATEGY_ID = "sf_zone_v1"
    def __init__(self, params=None):
        self._inner = FZoneStrategy(params=params)   # F존 엔진 그대로 재사용

    def _analyze_v2(self, ctx):
        signal = self._inner._analyze_v2(ctx)
        if signal is None or signal.signal_type != "sf_zone":
            return None                              # SF 라벨만 통과
        return signal.model_copy(update={"strategy_id": self.STRATEGY_ID})
```

즉 **진입 판정 로직(기준봉/눌림목/이평선/반등/점수)은 한 줄도 SF가 새로 쓰지 않는다.** F존 엔진이 매긴 `signal_type` 이 `sf_zone` 인지 여부로 필터링할 뿐이다. SF가 F존에 대해 "추가/강화"하는 것은 정확히 두 곳이다:

| 구분 | F존(부모) | SF존(자식) | 강화 방향 |
|------|-----------|-----------|-----------|
| 진입 게이트 | `is_f_zone`(score ≥ 4.0) | `is_sf_zone` 추가 조건(아래 §3) | **더 엄격**(강한 기준봉+7점) |
| 청산 정책 | TP 2단/SL −2%/BE +1.5% | TP 3단/SL −1.5%/BE +1.0% | **더 공격적+타이트** |
| position_size | 균등(`even_position_size`) | 균등(동일) | 차이 없음 |

참고로 SF는 자체 `health_check` 도 가져, inner ready + `sf_impulse_min_gain_pct ≥ 0.05` 를 함께 검사한다(`sf_zone.py:91-100`).

---

## 3. 진입 로직 (F존 대비 차이 강조 · 조건·점수·게이트) ← 코드 인용

### 3.1 공유 파이프라인 (F존과 byte-identical)

SF는 inner F존을 그대로 호출하므로, 다음 모든 게이트가 동일하게 적용된다 (`f_zone.py:244-338`):

1. **데이터 충분성**: `len(candles) < p.min_candles` → None (`f_zone.py:257`)
2. **변동성 필터(F1)**: `p.min_atr_pct > 0` 일 때 `atr_pct < min_atr_pct` → 진입 거부 (`f_zone.py:262-269`). default 0.0(비활성). **운영/시뮬 진입점에서 `FZoneParams(min_atr_pct=0.035)` 명시 override → inner 로 전파**됨이 테스트로 확인됨(`test_sf_zone.py:149-152`).
3. **트랩 가드**: `trap_guard.py` 의 `evaluate_trap_guard` — 모든 임계 0(default)이면 no-op (`f_zone.py:271-277`, `trap_guard.py:83-84`). SF는 `FZoneParams` 상속이라 **트랩 가드가 자동 적용**된다(별도 코드 없음). 활성화 시 (a)과확장 (b)윗꼬리 (c)고갭 ATR화 룰이 작동.
4. **진입 시간 게이트(Phase 8e)**: `p.entry_time_cutoff` 도달 시 차단 (`f_zone.py:280-287`). default None.
5. **기준봉 → 눌림목 → 이평선 지지 → 반등 → 점수/분류** (`f_zone.py:293-316`).

### 3.2 SF 판정 — F존 대비 추가 게이트 (핵심 diff)

분류는 `_score_and_classify` 에서 일어난다 (`f_zone.py:598-607`):

```python
analysis.score = min(score, 10.0)
analysis.is_f_zone = analysis.score >= 4.0          # F존 기준선

analysis.is_sf_zone = (                              # SF존 추가 게이트
    analysis.is_f_zone
    and analysis.impulse_gain_pct >= p.sf_impulse_min_gain_pct   # ≥ 0.05 (5%)
    and analysis.impulse_volume_ratio >= p.sf_volume_ratio       # ≥ 3.0 (300%)
    and analysis.score >= 7.0                                    # 7점 이상
)
```

**F존 → SF존 승격에 필요한 추가 조건 (코드 diff 수준):**

| 항목 | F존 통과선 | SF존 추가 요구 | 출처 |
|------|-----------|----------------|------|
| 종합 점수 | `score ≥ 4.0` | `score ≥ 7.0` | `f_zone.py:599,606` |
| 기준봉 상승률 | `impulse_min_gain_pct`(default 3%, intraday 1%) 이상 | `≥ sf_impulse_min_gain_pct`(default **5%**, intraday **2%**) | `f_zone.py:51,80,604` |
| 기준봉 거래량 배율 | `impulse_volume_ratio`(default 2.0×) 이상 | `≥ sf_volume_ratio`(**3.0×**) | `f_zone.py:56,81,605` |

즉 SF는 "거래량이 평균 대비 3배 이상 터지면서 +5%(또는 intraday 2%) 이상 오른 강한 기준봉 + 종합 7점 이상"의 셋업이다. 점수 산식(0~10)은 §5에 정리. 기준봉 점수 항이 `sf_impulse_min_gain_pct`/`sf_volume_ratio` 로 정규화되어 있어(`f_zone.py:559-560`), **SF 조건을 만족하는 기준봉은 자동으로 gain/vol 만점(3점)에 근접** → 7점 게이트와 결이 맞는다.

> ⚠ 미검증 주의: SF 분류는 전적으로 F존 점수 산식에 의존하므로, `_analyze_v2` 반환의 `score` 는 `is_sf_zone=True` 인 경우 항상 ≥ 7.0 이다. 따라서 운영 동점 tiebreaker(점수 1차 정렬) 관점에서 SF 시그널은 통상 F존/gold존보다 높은 점수로 정렬될 가능성이 큼(정량 분포는 백테스트 데이터 필요 — 본 리포트 범위 밖).

### 3.3 진입가 / 신호 생성

`signal_type = "sf_zone" if is_sf_zone else "f_zone"`, `price = candles[-1].close`, `score = round(analysis.score, 2)` (`f_zone.py:316-338`). inner 가 `strategy_id="f_zone_v1"` 로 만든 시그널을 SF 래퍼가 `sf_zone_v1` 로 재라벨(`sf_zone.py:50`).

---

## 4. 청산 로직 (exit profile — sf 전용/공유 여부 명시)

청산은 **2계층 방어선** 구조다 (`holding_evaluator.py:86-99` 주석).

### 4.1 1차 방어선 — `SFZoneStrategy.exit_plan` (분봉 close 기반, ExitEngine)

`sf_zone.py:52-84` — **sf 전용 오버라이드** (F존 `exit_plan` 을 상속하지 않고 재정의):

| 요소 | SF존 (`sf_zone.py`) | F존 (`f_zone.py:342-370`) | 차이 |
|------|---------------------|---------------------------|------|
| TP1 | +3%, 33% (`:57-61`) | +3%, 50% (`:347-351`) | 분할 비율 ↓ |
| TP2 | +5%, 33% (`:62-66`) | +5%, 50% (`:352-356`) | 분할 비율 ↓ |
| **TP3** | **+7%, 34% (`:67-71`)** | 없음 | **SF 신규 — 상단 추가 익절** |
| Stop Loss | **fixed −1.5% (`:79-81`)** | fixed −2.0% (`:365-367`) | **SF 0.5%p 더 타이트** |
| time_exit | STOCK=14:50, crypto=None (`:74`) | 동일 (`:360`) | 동일 |
| breakeven_trigger | **+1.0% (`:83`)** | +1.5% (`:369`) | **SF 더 빨리 본전 잠금** |

SL은 둘 다 `resolve_sl_pct(...)` 로 라운드피겨 보정을 거치며(default OFF면 base 그대로), SF base는 `-0.015`, F존 base는 `-0.02` (`round_figure.py:187-220`). 테스트가 SF TP 3단·합계 1.00·SL −0.015·BE 0.01 을 고정(`test_sf_zone.py:53-69`).

> 해석: SF는 더 강한 셋업이라 (a) 상단 +7%까지 익절 사다리를 늘리고, (b) 셋업 신뢰도가 높은 만큼 손절을 −1.5%로 조여 손실을 빨리 끊으며, (c) +1.0%만 가도 본전을 잠가 "강한 진입이 어긋날 때"의 손실을 최소화하는 공격적·보수적 혼합 정책이다.

### 4.2 2차 안전망 — `HoldingEvaluator` `STRATEGY_EXIT_PROFILES["sf_zone"]` (브로커 pnl_rate 기반)

**sf 전용 프로파일 존재** (`holding_evaluator.py:115-124`):

```python
"sf_zone": {
    "stop_loss_pct": Decimal("-4.0"),        # F존과 동일 (의도된 1차/2차 격차)
    "take_profit_pct": Decimal("7.0"),       # F존 5.0 → SF 7.0 (exit_plan TP3 +7% 정렬)
    "partial_tp_pct": Decimal("3.0"),
    "partial_tp_ratio": Decimal("0.33"),     # F존 0.5 → SF 0.33 (3분할 정렬)
    "trailing_start_pct": Decimal("3.0"),    # F존 3.5 → SF 3.0
    "trailing_offset_pct": Decimal("1.5"),   # F존 1.0 → SF 1.5
    "breakeven_trigger_pct": Decimal("2.0"), # F존 2.5 → SF 2.0
    "tightened_sl_pct": Decimal("-2.5"),     # F존과 동일
}
```

`resolve_policy()` 는 `strategy_id` 에서 `_v1` 을 제거해 키 매칭하므로 `sf_zone_v1` → `sf_zone` 프로파일이 적용된다(`holding_evaluator.py:176-177`). 평가 우선순위: max/min_hold(sf는 미정의→intraday default) → distribution(default-OFF) → 단기고점 → 트레일링 → 브레이크이븐 → 분할익절 → 전량TP → 시간SL → SL (`holding_evaluator.py:296-444`).

> SL 격차 의도(`holding_evaluator.py:86-103`): intraday 단타(f/sf/gold)의 2차 SL(−4%)은 1차 exit_plan SL(SF −1.5%)보다 의도적으로 2.5%p 너그럽다. ExitEngine 누락(데몬 다운/분봉 fetch 실패) 시 HoldingEvaluator가 fallback 매도하고, 브로커 pnl_rate 노이즈를 흡수하는 폭을 둔 것. **즉 sf의 실효 SL은 정상 운영 시 −1.5%, 비상 시 −4%.**

---

## 5. 파라미터 표 (F존 상속분 + SF 오버라이드분 구분)

### 5.1 진입 파라미터 — 전부 `FZoneParams` 상속 (SF 고유 dataclass 없음)

SF는 `params` 를 받아 그대로 inner F존에 넘긴다(`sf_zone.py:42`). 따라서 진입 파라미터는 100% `FZoneParams`(`f_zone.py:46-118`). default 값:

| 파라미터 | default | for_intraday() (1분봉 운영값) | SF 판정 사용처 | 출처 |
|----------|---------|-------------------------------|----------------|------|
| `impulse_min_gain_pct` | 0.03 | 0.010 | F존 기준봉 하한 | `f_zone.py:51,179` |
| `impulse_max_gain_pct` | 1.0(무제한) | 1.0 | 과열 차단(LESSON: max 7% 적용 시 win 시그널까지 죽음) | `f_zone.py:52-55` |
| `impulse_volume_ratio` | 2.0 | 2.0 | F존 기준봉 거래량 | `f_zone.py:56` |
| `impulse_lookback` | 5 | 15 | 기준봉 탐색 봉 수 | `f_zone.py:57,181` |
| `pullback_min_pct` | -0.03 | -0.03 | 눌림 최대 하락(Phase D2.4: −0.05→−0.03) | `f_zone.py:66,182` |
| `pullback_max_pct` | -0.005 | -0.003 | 눌림 최소 하락 | `f_zone.py:67,183` |
| `pullback_volume_ratio` | 0.7 | 0.85 | 눌림 거래량 감소 | `f_zone.py:68,184` |
| `pullback_max_candles` | 10 | 30 | 눌림 허용 봉 | `f_zone.py:69,185` |
| `ma_periods` | [5,20,60] | [20,60,120] | 이평선 지지 | `f_zone.py:72,186` |
| `ma_support_tolerance` | 0.01 | 0.005 | 지지 근접 허용 | `f_zone.py:73,187` |
| `bounce_min_gain_pct` | 0.005 | 0.003 | 반등 최소 상승 | `f_zone.py:76,188` |
| `bounce_volume_ratio` | 1.2 | 1.1 | 반등 거래량 증가 | `f_zone.py:77,189` |
| **`sf_impulse_min_gain_pct`** | **0.05** | **0.020** | **SF 기준봉 하한(핵심)** | `f_zone.py:80,190` |
| **`sf_volume_ratio`** | **3.0** | **3.0** | **SF 거래량 배율(핵심)** | `f_zone.py:81,191` |
| `min_candles` | 60 | 120 | 데이터 최소 | `f_zone.py:84,192` |
| `min_atr_pct` | 0.0(OFF) | (운영 0.035 override) | 변동성 필터 | `f_zone.py:94`, `test_sf_zone.py:151` |
| `atr_n` | 14 | 14 | ATR 기간 | `f_zone.py:95`, `test_sf_zone.py:159-162` |
| `use_watermelon_bonus` | False | — | 점수 가산(OFF) | `f_zone.py:100` |
| `entry_time_cutoff` | None | (운영 14:00 override) | 장후반 진입 차단 | `f_zone.py:108` |
| `trap_*`(6개) | 0/비활성 | — | 트랩 가드(default-OFF) | `f_zone.py:113-118` |

> 운영에서 1분봉을 쓸 경우 SF 기준봉 하한이 **5% → 2%**, 거래량은 **3.0× 유지**, 점수는 여전히 ≥ 7.0 로 SF 승격이 결정된다(`FZoneParams.for_intraday`, `f_zone.py:178-193`).

### 5.2 점수 산식 (0~10) — F존 공유, SF 게이트가 참조

`_score_and_classify` (`f_zone.py:544-610`):
- 기준봉: `min(gain/sf_impulse_min_gain_pct,1)×2 + min(vol_ratio/sf_volume_ratio,1)×1` (최대 3점)
- 눌림목: `max(0, 1 − depth/0.05)×2` (최대 2점, 얕을수록 ↑)
- 이평선 지지: `2 − touch_pct/tolerance` (최대 2점)
- 반등: `min(gain/0.02,1)×1.5 + min(vol_ratio/2,1)×1.5` (최대 3점)
- (옵션) 수박지표 +bonus(default OFF)
→ F존 ≥ 4.0, SF존 ≥ 7.0.

### 5.3 청산 파라미터 (오버라이드 — §4 표 재정리)

| 계층 | TP | SL | BE | 출처 |
|------|----|----|----|------|
| 1차 exit_plan (sf 전용) | +3/+5/+7% (33/33/34%) | −1.5% | +1.0% | `sf_zone.py:56-84` |
| 2차 HoldingEvaluator (sf 전용) | 7.0% / partial 3.0%·0.33 | −4.0% (강화 −2.5%) | 2.0% | `holding_evaluator.py:115-124` |

### 5.4 position_size

`even_position_size` — score 무관 균등(default ratio 0.08 = 자본의 8%, 10슬롯) (`sf_zone.py:86-89`, `position_sizing.py:24,40-52`). F존과 동일(BAR-OPS-09 Phase 9, score 차등 제거). 테스트: 고/중/저 score 모두 동일 수량(`test_sf_zone.py:87-103`).

---

## 6. 활성·운영 상태

- **default 활성**: `_DEFAULT_ENABLED["sf_zone"] = True` (`signal_scanner.py:42-44`). 활성 단타 3종(sf/f/gold).
- **dispatch 1순위**: `_analyze_symbol` 의 intraday_dispatch 리스트에 **SF가 가장 먼저 append** 되고, 루프에서 첫 시그널을 즉시 반환 → 같은 종목에서 SF·F 둘 다 성립해도 **SF가 선점**(`signal_scanner.py:155-182`).
- **⚠ tiebreaker 우선순위는 후순위**: `STRATEGY_PRIORITY = {swing_38:1, gold_zone:2, f_zone:3, sf_zone:4, ...}` (`signal_scanner.py:59-62`). 정렬 키 `(-score, priority)` (`:137`). 즉 **dispatch 단계에서는 SF가 종목 내 최우선**이지만, 여러 종목 시그널을 점수 동률로 줄세울 때는 sf_zone이 f_zone/gold_zone보다 뒤다. dispatch 선점 덕에 한 종목에서 SF·F 동시 발생은 애초에 일어나지 않으므로(SF가 먼저 return), 이 tiebreaker는 **서로 다른 종목 간 동점 경합**에만 영향. 운영 의도(SF 최우선)와 tiebreaker 숫자가 어긋나 보이므로 점검 권장.
- **인스턴스 생성**: `self.sf_zone = SFZoneStrategy(f_zone_params)` — F존과 동일 params 객체를 공유(`signal_scanner.py:106-107`).
- **health_check**: `ready = inner_ready AND sf_impulse_min_gain_pct ≥ 0.05` (`sf_zone.py:91-100`, `test_sf_zone.py:114-120`).

---

## 7. 비용·손익분기 관점

거래비용 단일 진실원천 (`trading_costs.py:29-37`):
- 편도 수수료 `COMMISSION_RATE = 0.0035` (0.35%/leg)
- 매도 거래세 `TAX_RATE_SELL = 0.0020` (0.20%)
- 왕복 `ROUND_TRIP_COST_RATE = 0.0035×2 + 0.0020 = 0.0090` (**0.90%**)

SF TP 사다리(gross)를 net으로 환산하면(분할 비율 33/33/34, 왕복 0.90% 가정):

| 티어 | gross | net(−0.90%) | 비고 |
|------|-------|-------------|------|
| TP1 +3% | +3.0% | **+2.1%** | 손익분기(0.90%) 충분히 상회 |
| TP2 +5% | +5.0% | **+4.1%** | |
| TP3 +7% | +7.0% | **+6.1%** | SF 신규 상단 |
| BE +1.0% (본전 잠금) | +1.0% | **+0.1%** | gross +1.0%는 net 거의 본전 — 비용 차감 시 실질 이익 미미 |
| SL −1.5% | −1.5% | **−2.4%** | 비용 포함 실손 |

> 주의: HoldingEvaluator는 default가 gross 임계 비교다. `net_aware_tp`(default-OFF, `holding_evaluator.py:214-216,286-288`)를 켜야 TP/분할익절 임계에 왕복비용이 가산된다. 켜지 않으면 **breakeven_trigger(SF +1.0%/2차 +2.0%)는 비용 미반영** → +1.0% 본전 잠금이 실제로는 net 손익분기에 미달할 수 있음(정량 영향은 미검증). TP3(+7%)는 net +6.1%로 비용 영향이 작아 SF의 상단 사다리는 비용 측면에서 견고.

---

## 8. 백테스트·OOS 근거 / 한계·리스크

- **변동성 필터 운영 근거**: sf_zone 변동성 필터(inner ATR 게이트) — "누적 41 runs / 5 발동 (100% win), 발동 종목 모두 flu% ≥ 10.2%" (`test_sf_zone.py:139-147`). 단 표본이 작아 일반화 한계 명시.
- **min_atr_pct 권장값 0.035**: 운영 8종목 백테스트 +186k 검증(F1), LG전자(ATR% 2.94%, win 0%, −627k) 같은 저변동·고가 종목 제외 목적 (`f_zone.py:86-94`). default는 합성데이터 회귀 보존 위해 0.0.
- **SF 전용 OOS 수치 없음(한계)**: 코드/테스트에서 **SF존 단독 승률·손익비·MDD 백테스트 수치는 발견되지 않음.** SF는 F존 셋업의 상위 부분집합이라 별도 시뮬 트랙이 분리돼 있지 않은 것으로 보임(미검증 — 별도 백테스트 산출물 확인 필요).
- **baseline 회귀 테스트 skip 상태**: `test_c7_baseline_unchanged` 가 `@pytest.mark.skip`(main `ec9feab` 의 SyntheticDataLoader f_zone trades=0 회귀, 본 PR 외 잔재) (`test_sf_zone.py:126-136`). → SF 분리가 F존 baseline을 깨지 않는다는 검증이 현재 비활성.
- **리스크 1 — 진입 다양성 저하**: SF는 거래량 3배·+5%·7점이라 신호 빈도가 낮을 수 있음(F존보다 희소). 시그널 부족 시 슬롯 미충원 가능.
- **리스크 2 — 타이트 SL(−1.5%)의 노이즈 손절**: 1분봉에서 −1.5%는 변동성 큰 종목에서 쉽게 닿음 → min_atr_pct 필터와 결합되지 않으면(default OFF) whipsaw 손절 위험.
- **리스크 3 — dispatch vs tiebreaker 우선순위 불일치**(§6): 운영 의도와 코드 정렬값 괴리. 다종목 동점 시 SF가 의도와 달리 후순위로 밀릴 수 있음.
- **리스크 4 — net 미반영 BE**: §7대로 net_aware_tp OFF 시 BE +1.0%/+2.0%가 실비용 미반영.

---

## 9. 관련 파일·테스트

| 파일 | 역할 |
|------|------|
| `backend/core/strategy/sf_zone.py` | SF 래퍼(delegate) + 전용 exit_plan/health_check |
| `backend/core/strategy/f_zone.py` | 부모 엔진 — 진입·점수·SF 판정(`is_sf_zone`) 전부 여기 |
| `backend/core/strategy/trap_guard.py` | 진입 가드(상속 적용, default-OFF) |
| `backend/core/strategy/position_sizing.py` | `even_position_size` (균등 진입) |
| `backend/core/strategy/round_figure.py` | `resolve_sl_pct` (SL 라운드피겨 보정) |
| `backend/core/risk/holding_evaluator.py` | 2차 안전망 — `STRATEGY_EXIT_PROFILES["sf_zone"]` |
| `backend/core/scanner/signal_scanner.py` | dispatch(1순위) + `_DEFAULT_ENABLED`/`STRATEGY_PRIORITY` |
| `backend/core/trading_costs.py` | 거래비용 단일 진실원천(왕복 0.90%) |
| `backend/tests/strategy/test_sf_zone.py` | C1~C7 + 변동성 필터 테스트(상속/필터/exit/size/health) |

기타 sf_zone 참조 테스트: `test_signal_decision_audit.py`, `test_entry_revalidation.py`, `test_force_close_exempt.py`, `test_intraday_buy_daemon_strategies.py`, `scanner/test_signal_scanner_phase_c.py`, `backtester/test_intraday_simulator.py`, `backtester/test_portfolio_simulator.py`.

---

*진실원천 주석: 모든 수치·동작은 origin/main(HEAD `013d54b`, 2026-06-22) 코드 직접 인용. file:line 표기는 인용 시점 라인. SF존 단독 OOS 백테스트 수치, dispatch/tiebreaker 우선순위 불일치의 운영 영향, net 미반영 BE의 실손익 영향은 "미검증"으로 명시 — 별도 백테스트/운영 데이터 확인 필요.*
