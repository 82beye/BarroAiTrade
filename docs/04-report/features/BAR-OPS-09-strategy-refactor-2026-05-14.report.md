---
tags: [report, ops, bar-ops-09, strategy-refactor, 2026-05-14]
---

# BAR-OPS-09 후속 — 전략 시그널·청산 정책 분리 + 명목 가치 정규화 (2026-05-14)

> 운영 머신(인텔 맥북) 로그 임포트 → 600봉 백테스트 → 전략 구조 리팩토링 →
> 결론 정정 누적. BAR-OPS-09 브랜치에서 진행 (5/13~14 세션).

---

## 1. 작업 누적 (커밋 5건)

| 커밋 | 제목 | 핵심 |
|------|------|------|
| 5d8d4b2 | fix: scalping_consensus provider injection (silent 0건 버그 해소) | provider 미주입 인터페이스 정비 + 단위 테스트 5건 |
| 01e8d88 | Merge origin/main — scalping_consensus provider 결합 | main의 auto-load 코드와 결합. 명시·자동 양립 |
| dda381a | fix: LiveOrderGate qty<=0 사전 차단 (DCA ValueError 회귀 방지) | 2026-05-13 운영 DCA qty=0 ValueError 게이트 차단 |
| ec48b2d | feat: 전략별 exit_plan 분리 + f_zone 변동성 필터 | `_exit_plan_for_strategy` 분기 + f_zone `min_atr_pct=0.035` |
| dd609ea | feat: IntradaySimulator 명목 가치 진입 (S1) + 고가주 정책 | `position_value` + 1주당 100만원 임계 200만원 한도 |
| 57c6522 | docs: 2026-05-12~13 운영 로그·캐시·전략 시뮬 분석 산출물 | analysis/imports/ 37 파일 통합 커밋 |

---

## 2. 운영 로그 임포트 (2026-05-12~13)

### 입력

- `barro_logs_20260513.zip` (5/12 로그, 7 파일, 17KB)
- `barro-logs-2026-05-13.tar.gz` (5/13 로그, 11 파일, 931KB)
- `ohlcv_cache_20260513.tar.gz` (153.9 MB / **2,967 종목** 일봉 캐시)

### 발견된 운영 이슈

| # | 증상 | 원인 | 상태 |
|---|------|------|------|
| 1 | DCA 추가매수 `ValueError: qty must be > 0` 2건 | 보유 1주 × 25% 분할 = 0주 | ✅ main 9d04656 호출자 가드 + 본 PR LiveOrderGate 차단 |
| 2 | `order_audit.csv` HTTPStatusError 3건 | 키움 API 429 rate limit | 🟡 main b4d2ad5 429 retry 적용 |
| 3 | `server.log` KIWOOM_APP_KEY KeyError 반복 | backend.api 시작 시 `.env` 미로드 | 🟡 별도 작업 |
| 4 | 012200 계양전기 SL -4.20% | DCA 1차 진입 직후 하락 | 🟢 정상 동작 |
| 5 | telegram_bot.err 5,345줄 poll cycle 실패 | 외부 의존성 장애 | 🟡 관찰 |

상세: [[../../../analysis/imports/2026-05-13/BUG_TRACE]]

---

## 3. 핵심 구조 변경 (전략별 분리)

### 3.1 IntradaySimulator — `_exit_plan_for_strategy` 분기

종전: 모든 전략에 `_scaled_exit_plan(entry_price)` 고정 적용 (TP +3/+5/+7%, SL -1.5%).

신규:
```python
def _exit_plan_for_strategy(sid, entry_price, candles_window):
    if sid == "sf_zone":
        return _sfzone_atr_exit_plan(...)   # ATR 기반 동적
    return _scaled_exit_plan(entry_price)   # 그 외 (f_zone/gold_zone/swing_38/scalping_consensus)
```

### 3.2 f_zone — 변동성 필터 (F1)

```python
class FZoneParams:
    min_atr_pct: float = 0.035   # ATR% < 3.5% 종목 거부 (저변동 부적합)
```

LG전자(ATR% 2.94%) 같은 저변동 종목 거부. 정상 일중 변동 절반 이하인 SL -1.5%가 노이즈에 발동되는 패턴 차단.

### 3.3 LiveOrderGate — qty 사전 검증

```python
class InvalidOrderQty(ValueError):
    """qty ≤ 0 진입 시 BLOCKED + 명시적 reason + Telegram 알림."""

async def _gated(self, ...):
    if qty <= 0:
        err = InvalidOrderQty(f"qty must be > 0, got {qty}")
        self._audit("BLOCKED", ..., reason=str(err))
        await self._notify_blocked(...)
        raise err
```

DCA 분할 비율 × 보유 1주 floor = 0주 같은 케이스 → audit `FAILED` 가 아닌 `BLOCKED` 로 명시 차단 + 알림.

### 3.4 IntradaySimulator — 명목 가치 진입 (S1) + 고가주 정책

```python
def __init__(..., position_value=None, high_price_threshold=Decimal("1000000"),
             high_price_budget=Decimal("2000000")):
```

| 1주 가격 | qty 결정 | 명목 |
|---------|---------|------|
| ≤ 100만원 | floor(`position_value` / price) | ~position_value |
| > 100만원 ~ 200만원 | floor(2M / price) | 1~2M (1주) |
| > 200만원 | 0 (진입 거부) | — |

---

## 4. 백테스트 결과 — 8 운영 종목 × 600봉 (S1 1M 기준)

### 4.1 전략별 합계

| 전략 | total_pnl | active | trades | win% | pnl/trade |
|------|----------:|:------:|------:|-----:|----------:|
| **f_zone** (F1) | **+704,081** | 8/8 | 32 | 56.5% | **+22,003** |
| swing_38 | +541,920 | 8/8 | 72 | 56.1% | +7,527 |
| sf_zone (ATR) | +164,198 | 4/8 | 9 | 71.9% | +18,244 |
| gold_zone | +653,898 | 8/8 | 200 | 61.3% | +3,269 |
| scalping_consensus | 0 | 0/8 | 0 | — | — |
| **합** | **+2,064,097** | | | | |

### 4.2 종목 × 전략 매트릭스 (S1)

| 종목 | f_zone | sf_zone | gold_zone | swing_38 | **합** |
|------|-------:|--------:|----------:|---------:|------:|
| 319400 현대무벡스 | +75k | +196k | **+379k** | +140k | **+790k** ✅ |
| 010170 대한광통신 | **+347k** | +181k | +162k | +61k | **+750k** ✅ |
| 090710 휴림로봇 | +272k | **-250k** | +50k | +300k | +372k |
| 003280 흥아해운 | -17k | 0 | +230k | +28k | +241k |
| 356680 엑스게이트 | +48k | +38k | +121k | +30k | +236k |
| 066570 LG전자 | -30k | 0 | +12k | +38k | +21k |
| 012860 모베이스전자 | 0 | 0 | **-161k** | -3k | **-164k** ❌ |
| 012200 계양전기 | +9k | 0 | **-139k** | -51k | **-181k** ❌ |

---

## 5. 학습 — 종전 결론 정정

### 5.1 자본 정규화로 인한 4건 정정 (LESSON_S1_NORMALIZATION)

| # | 종전 결론 | S1 검증 | 정정 |
|---|-----------|--------|------|
| 1 | "swing_38 LG +490k = LG 적합" | S1: LG +38k | **자본 효과 착시** (LG 11M 자본 사용) |
| 2 | "swing_38 최강" | per-trade S1: f_zone +22k vs swing_38 +7.5k | **f_zone 1위** (per-trade 명목 PnL) |
| 3 | "f_zone LG -627k 손실" | S1: LG -30k | 80%가 자본 크기 효과 |
| 4 | "sf_zone win% 89.9% 우수" | S1: 휴림로봇 -250k | 표본 작음(N=9), 신중 해석 |

### 5.2 실패한 가설 (롤백)

- **"+7% 과열 임펄스 거부"** → 단일 종목(LG) 패턴을 일반화한 결과 sf_zone winning 시그널 죽임. [[../../../analysis/imports/2026-05-13/LESSON_FZONE_MAX_GAIN]]
- **전역 ATR SL multiplier** → TP 고정 + SL만 ATR로 R:R 깨짐. 롤백 후 sf_zone 단독 ATR(TP+SL 모두) 적용.

---

## 6. 미해결 + 다음 단계

### 미해결

- **scalping_consensus** 일봉 600봉 0건 — ScalpingCoordinator 가 분봉/틱 가정. 분봉 캐시로 별도 검증 필요.
- **계양전기·모베이스전자** 누적 손실 — gold_zone 잦은 진입 + 작은 winning. 종목 풀 제한 필요.

### 다음 단계 후보

| # | 작업 | 우선순위 |
|---|------|---------|
| H1 | gold_zone 종목 필터 (저변동 + 저가주 제외) | 🔴 |
| H2 | 종목 × 전략 적합도 매핑 정책 (한 종목에 적합 전략만) | 🔴 |
| H3 | sf_zone 휴림로봇 -250k trace | 🟡 |
| H4 | scalping_consensus 분봉 백테스트 | 🟢 |
| H5 | 14M 한도 + 자본 회전 시뮬 (BAR-OPS-16 정책 반영) | 🟢 |

---

## 7. 산출물

### 코드
- `backend/core/backtester/intraday_simulator.py`: `_exit_plan_for_strategy`, `_sfzone_atr_exit_plan`, `_atr_pct`, `position_value`, 고가주 정책
- `backend/core/strategy/f_zone.py`: `min_atr_pct`, `_atr_pct`
- `backend/core/strategy/sf_zone.py`: params 인터페이스 정리
- `backend/core/risk/live_order_gate.py`: `InvalidOrderQty` + qty 사전 검증

### 테스트 (누적 50+ 신규)
- `backend/tests/backtester/test_intraday_simulator.py`: 30/30 통과
- `backend/tests/strategy/test_f_zone.py`: 12/13 통과 (1건 환경 의존)
- `backend/tests/risk/test_live_order_gate.py`: 11/11 통과

### 분석 (37 파일, [[../../../analysis/imports]])

#### 2026-05-12
- [[../../../analysis/imports/2026-05-12/REPORT|단일 일자 시뮬 분석]]
- [[../../../analysis/imports/2026-05-12/STRATEGY_TRACE|4 전략 0건 추적]]

#### 2026-05-13
- [[../../../analysis/imports/2026-05-13/BUG_TRACE|DCA qty=0 ValueError 추적]]
- [[../../../analysis/imports/2026-05-13/CACHE_SYNC|OHLCV 캐시 동기화]]
- [[../../../analysis/imports/2026-05-13/REPORT_600BARS_S1|600봉 S1 통합 백테스트]]
- [[../../../analysis/imports/2026-05-13/F_ZONE_ANALYSIS_S1|f_zone S1 정밀 분석]]
- [[../../../analysis/imports/2026-05-13/SF_ZONE_ANALYSIS_S1|sf_zone S1]]
- [[../../../analysis/imports/2026-05-13/GOLD_ZONE_ANALYSIS_S1|gold_zone S1]]
- [[../../../analysis/imports/2026-05-13/SWING_38_ANALYSIS_S1|swing_38 S1]]
- [[../../../analysis/imports/2026-05-13/FZONE_LG_TRACE|LG전자 f_zone 손실 trace]]
- [[../../../analysis/imports/2026-05-13/LESSON_FZONE_MAX_GAIN|실패한 +7% 가설 롤백 기록]]
- [[../../../analysis/imports/2026-05-13/LESSON_S1_NORMALIZATION|자본 정규화 정정 보고서]]

### 재사용 스크립트
- `analyze_strategy.py --strategy=XXX --position-value=N --suffix=XXX`
- `backtest_from_cache.py --position-value=N --report-suffix=XXX`
- `trace_fzone_lg.py` (종목 trace 패턴 재사용)

---

## 8. 핵심 메시지

> 절대 PnL 비교는 **자본 사용량을 통일한 후에만** 의미 있다. 종전 분석에서 swing_38 LG +490k 같은 결과가 강한 전략적 우위로 보였으나 S1 정규화 후 단순 자본 크기 효과로 판명.
>
> **per-trade 명목 PnL 기준 f_zone(+22k) 이 1위**. 단 swing_38 은 72 trades 회전으로 누적 수익 +542k 확보 — 빈도 × 효율 양 차원 고려.
>
> 전략별 시그널·청산 정책은 **각자의 정의에 맞게 분리**해야 한다 (sf_zone ATR, f_zone 변동성 필터). 한 곳에 전역 옵션을 두면 한 전략에 맞춘 변경이 다른 전략을 깨뜨린다.

→ [[../../00-index/ops-track-index|OPS 트랙 인덱스]]
→ [[BAR-OPS-09-report|BAR-OPS-09 원본 (KiwoomCandleFetcher)]]
