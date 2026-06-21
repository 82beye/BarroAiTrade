---
tags: [design, feature/dante-uplift, channel/주식단테, status/draft]
---

# 주식단테 방법론 기반 BarroAiTrade 고도화 — 분석·설계

> **연관 분석**: [[../../03-analysis/2026-06-21-dante-methodology-extract|주식단테 방법론 추출(1,010편)]]
> **선행 설계**: [[2026-06-17-thetrading-methodology-uplift.design|더트레이딩 uplift]] · [[2026-06-21-thetrading-uplift-301delta.design|더트레이딩 301델타]]
>
> **Summary**: 주식단테 채널에서 채굴한 정량 기법(JD-R1~25)을 현행 코드와 대조해 **신규/중복**을 가르고, 실거래 안전(HITL)을 보존하는 고도화 로드맵 + 신규 안전 필터 모듈(`dante_filters.py`) 스펙을 제시한다. 중심 발견: BarroAiTrade는 이미 더트레이딩(단기·연속형)을 깊게 코드화했고, 주식단테는 **224일 장기선 기반 바닥·반전 셋업**이라는 *직교적 보완 클래스*를 추가한다. 가장 높은 이식가치 3종 = **① 장기 EMA 레짐 게이트(JD-R1~4) ② S-R flip/distribution 디텍터(JD-R7/R13) ③ R:R·수급 운영 게이트(JD-R21/R18)**.
>
> **Project**: BarroAiTrade (개발 레포 `~/workspace/BarroAiTrade`)
> **Date**: 2026-06-21
> **Status**: Draft (문서 산출물 — **코드 변경 없음**. 구현/배포는 항목별 HITL 분류를 따른다.)
> **Scope note**: 이번 라운드 = **분석·설계 문서**. (c)등급 안전구현·백테스트는 본 설계 승인 후 별도 증분(§6·§8)으로 진행한다. 실거래 숫자 변경(d)은 `barrotrade-code-surgeon` 위임 + AskUserQuestion 후에만.

---

## 0. 배경과 근거

### 0.1 핵심 발견 — 직교적 보완 클래스
[[2026-06-17-thetrading-methodology-uplift.design|기존 설계 §0.1]]: BarroAiTrade 전략(F존/골드존/SF존/supertrend/closing_bet)은 **더트레이딩** 채널 방법론의 코드화다. 주식단테는 *별개 채널*이며, 그 시그니처는 방향이 반대다:

| 축 | 더트레이딩 / 현행 시스템 | 주식단테 (신규) |
|----|--------------------------|------------------|
| 추세 기준선 | 단기 5·20·60일선 | **장기 112·224·448일선** |
| 셋업 성격 | 상승추세 종목의 **돌파 후 눌림(연속형)** | 역배열 바닥종목의 **장기선 재돌파(반전형)** |
| 시간축 | 당일~수일(단타·스윙) | 수주~수개월(바닥 매집) |
| 진입 논리 | "불나방 손절 구간 역매수"(F존) | "224 아래 4개월 횡보 후 돌파 눌림"(밥그릇 3번) |

→ 두 방법론은 **경쟁이 아니라 보완**이다. 주식단테는 현 시스템이 *보지 않는* 종목군(장기 역배열 바닥)과 *없는* 신호(S-R flip·distribution·장기 레짐)를 추가한다.

### 0.2 두 근거의 수렴 (신뢰도)
독립 출처가 같은 결론을 가리키는 항목 = 우선순위 ↑:
- **고점추격 금지**: 주식단테 JD-R25 ↔ 더트레이딩 R11(진입가 위치 게이트) ↔ 운영 실측(고갭 추격 최대손실). **3중 수렴 → 최우선.**
- **신고가·거래대금·과열·이격**: 주식단테가 동일 개념을 재명명 → 이미 `closing_bet_filters.py`로 구현됨(중복 = 검증).
- **R:R 사전설정·칼손절·분할·몰빵금지**: 주식단테 JD-R21~24 ↔ 더트레이딩 ⑤ 리스크 규칙군. 운영 노하우 수렴.

---

## 1. 방법론 정수 (요약)
상세·인용은 [[../../03-analysis/2026-06-21-dante-methodology-extract|추출 문서]]. 그룹:
- **A 장기 EMA 골격**(112/224/448): 레짐 게이트(JD-R1), 돌파→눌림(JD-R2), 이평때리기 역배열 반등(JD-R3), 계단식 돌파(JD-R4).
- **B 바닥축적 패턴**: 밥그릇 원형바닥 4단계(JD-R5), 매집봉 흡수캔들(JD-R6), 공구리 S-R flip(JD-R7), 하이힐 낙폭과대 반등(JD-R8).
- **C 통합 셋업**: 역매공파(JD-R9, 손절3.5%/목표20%).
- **D 검색기**: 오돌이 시초가 단타 10조건(JD-R10), 기준봉 224돌파(JD-R11).
- **E 종목성격별**: 양봉/음봉 차별(JD-R12).
- **F 청산·회피**: distribution 장대음봉(JD-R13), 기준봉 시가 손절(JD-R14).
- **G 눌림목**(중복+α): confluence(JD-R15), 박스 리테스트 70%(JD-R16).
- **H 수급/주도주/국면**: 쇄빙선 순환매(JD-R17), 수급게이트(JD-R18), 장세 4단계(JD-R19), 오돌리 5/15 cross(JD-R20).
- **I 운영**: R:R≥1:2(JD-R21), 분할(JD-R22), 칼손절+시나리오(JD-R23), 몰빵금지(JD-R24), 고점추격금지(JD-R25), 메타원칙 JD-P1~7. (농사매매=마틴게일 **비권고**.)

---

## 2. 코드 현황 (As-Is)

### 2.1 전략 레지스트리 (`signal_scanner.py` origin/main)
| 전략 | TF | 활성 | MA/지표 | 셋업 |
|------|----|----|--------|------|
| `sf_zone` `f_zone` `gold_zone` | 1분 | ✓ | 5·20·60(또는 12·36·72) EMA, Fib, BB, RSI | 돌파후 눌림(연속) |
| `swing_38` | 1일 | ✓ | impulse+Fib0.382 | 연속 스윙 |
| `blue_line` | - | OFF | 5·20 EMA cross | 단기 추세 |
| `closing_bet` | EOD | OFF | `closing_bet_filters`(신고가·유동성·과열·엔벨·이격) | 종가베팅 |
| `supertrend`/`crypto_breakout` | - | (신호/OFF) | ATR | 추세 |

### 2.2 인프라 (재사용 자산)
- **신규 필터 idiom**: `backend/core/strategy/closing_bet_filters.py` — *순수함수 inert 모듈*. 각 함수 = 방법론 R번호 + 채널 인용 + "관측 전용·라이브 무영향" 주석. **주식단테 필터도 동일 패턴으로 작성**(§6).
- **지표**: `indicators.py`(atr_pct·RSI·HTF) + `closing_bet_filters._sma`. 장기 EMA 헬퍼는 없음(전략들이 inline `ewm`).
- **레짐**: `backtester/market_regime.py`(지수 BULL/BEAR…) + `risk/regime_exit.py`(default-OFF) — **종목 가격구조 레짐(224)과는 다른 차원**.
- **선정**: `gateway/kiwoom_native_rank.py`(거래대금·등락률·거래량 3-factor) + `leader-stock-scorer`/`theme-classifier`(bar-59/60).
- **리스크/비용**: `PolicyConfig`(stop_loss·daily_loss·max_per_position), `trading_costs.py`(편도 0.35%·매도세 0.20%, 왕복 ~0.90%).

### 2.3 부재 확인 (§분석 §4)
224/112/448 레짐 게이트 · S-R flip/원형바닥 디텍터 · distribution 청산 · 수급게이트(신용잔고·ETF) · R:R 게이트 = **모두 부재**.

---

## 3. 추적성 매트릭스 (JD-R ↔ 코드)

상태: **신규(missing) 13 · 중복(redundant/impl) 7 · 부분(partial) 5**. 신규 13 중 7건이 (c)안전 inert로 즉시 측정 착수 가능.

| # | 그룹 | 규칙 | 코드 매핑 | 상태 | 백테스트 | 거버넌스 |
|---|------|------|-----------|------|:--:|:--:|
| JD-R1 | A | 224 레짐 게이트 | 신규 `ma_regime` | **신규** | 부분 | c→d |
| JD-R2 | A | 224 돌파→눌림 | 신규 `ma224_breakout_pullback` | **신규** | 부분 | c→d |
| JD-R3 | A | 이평때리기(역배열 반등) | `blue_line` 확장/신규 | **신규** | 가능 | c→d |
| JD-R4 | A | 이평선 3개 계단식 | EMA(112/224/448) 파라미터 | 부분 | 가능 | c→d |
| JD-R5 | B | 밥그릇(원형바닥) | 신규 `saucer_bottom` | **신규** | 부분 | c→d |
| JD-R6 | B | 매집봉(흡수) | 신규 `accumulation_candle` | **신규** | 부분 | c→d |
| JD-R7 | B | 공구리(S-R flip) | 신규 `sr_flip` | **신규** | **가능** | c→d |
| JD-R8 | B | 하이힐(낙폭과대 반등) | 신규 `high_heel` | **신규** | 부분 | c→d |
| JD-R9 | C | 역매공파 | `swing_38` 확장/신규 | **신규** | 부분 | d |
| JD-R10 | D | 오돌이 검색기(10조건) | `signal_scanner` stack | **신규** | 가능* | c→d |
| JD-R11 | D | 기준봉 224돌파 검색기 | `signal_scanner` | 부분 | 부분 | c→d |
| JD-R12 | E | 양봉/음봉 차별(시총) | 신규 `candle_type_entry` | **신규** | 부분 | d |
| JD-R13 | F | distribution 장대음봉 | 신규 `distribution_alert` | **신규** | **가능** | c→d |
| JD-R14 | F | 기준봉 시가 손절 | `exit` 확장 | 부분 | 가능 | d |
| JD-R15 | G | confluence 눌림 | `f_zone`/`gold_zone` 가중 | 중복+α | 가능 | c→d |
| JD-R16 | G | 박스 리테스트 70% | `sf_zone` 변형 | 중복+α | 가능 | d |
| JD-R17 | H | 쇄빙선 순환매 | `leader-stock-scorer` | 부분 | 부분 | a/c |
| JD-R18 | H | 수급게이트(신용·ETF) | 신규 `supply_credit_gate` | **신규** | 가능 | a→c→d |
| JD-R19 | H | 장세 4단계 | `market_regime` 확장 | 부분 | 부분 | c→d |
| JD-R20 | H | 오돌리 5/15 cross | `blue_line` 변형 | 중복+α | 가능 | c→d |
| JD-R21 | I | R:R ≥ 1:2 게이트 | `risk_manager.min_rr_ratio` | **신규** | **가능** | c(shadow)→d |
| JD-R22 | I | 분할매수 비율 | `position_sizing.split_ratio` | 중복+α | 가능 | d |
| JD-R23 | I | 칼손절+시나리오 | `exit`/주문 필수필드 | 부분 | 가능 | c/d |
| JD-R24 | I | 몰빵 금지 | `position_sizing` 한도 | 중복 | 가능 | b/d |
| JD-R25 | I | 고점추격 금지 | 데몬 갭가드(R11 수렴) | 부분 | 가능 | (이미 진행) |
| — | I | 농사매매(마틴게일) | — | **비권고** | n/a | 거부 |

*JD-R10: OHLCV/이평 항목은 가능, 체결강도·호가잔량(⑥⑦)은 L2 실시간 → 라이브 전용.

---

## 4. 갭 / 중복 분석

### 4.1 신규 고가치 3선 (이식 우선)
1. **장기 EMA 레짐(JD-R1~R4)** — 현 시스템에 *시간 호라이즌 자체가 없음*. 224 위/아래로 단타 전략 활성/억제 = 직교적 안전 레이어. (예: 224 아래 역배열 종목엔 연속형 F존 진입을 보수화.)
2. **S-R flip(JD-R7) + distribution(JD-R13)** — 둘 다 **완전 정량화·OHLCV만으로 코딩**. 공구리=진입/지지 구조, distribution=청산/회피 구조. 현 시스템에 가격구조 pivot·분산 청산 신호 전무.
3. **운영 게이트 R:R(JD-R21) + 수급(JD-R18)** — 한 줄 게이트로 기대값 음수 진입 차단(R:R), 공개데이터로 과열 국면 매수 억제(수급). 알파 대비 구현비용 최저.

### 4.2 중복 = 교차검증 (신규코드 불필요)
- 눌림목(JD-R15/16) ≈ `f_zone`/`gold_zone` Fib 눌림. 신규 가치는 **confluence count 가중**(2개+ 근거 겹침)·**박스 70% 목표** 뿐.
- 신고가(몸통)·유동성·과열·엔벨로프·이격 = 이미 `closing_bet_filters.py`(더트레이딩 v2). 주식단테가 동일 개념 재명명 → **두 채널 독립 수렴 = 신뢰도 보강**.
- 오돌리(5/15 cross) ≈ `blue_line`(5/20). 224 게이트만 추가.

### 4.3 전이 불가 / 거부
- 비공개 지표(수박지표·파란점선)·재량 레이블(밥그릇 자리·굽 높이)·세력 음모론 → 정량 대리(볼린저 squeeze·OBV·zigzag)로만 근사, 동일성 미보장.
- **농사매매(마틴게일)** → 자본고갈 리스크, 거버넌스 거부. 신저가 손절·섹터분산·현금≥30%만 *원칙* 참고.
- 단정화법·실시간 종목추천 = 채널 규제리스크지 코드 전이 대상 아님.

---

## 5. 고도화 로드맵 (P0/P1/P2 · 거버넌스)

> **거버넌스 4분류**: (a)운영/데이터 (b)이미구현=배포 (c)안전=자동가능(inert·shadow·default-OFF, env=0 byte-identical) (d)실거래 파라미터=AskUserQuestion 후 `barrotrade-code-surgeon`. push/머지/라이브 활성=HITL.

### P0 — 즉시 안전·고수렴 (이번 설계 직후 (c) 증분 후보)
- **P0-1 `dante_filters.py` inert 모듈 신설**(§6) — JD-R7(sr_flip)·JD-R13(distribution)·JD-R1(ma_regime)·JD-R21(rr_ratio)을 *순수함수·관측전용*으로. 분류 **(c)**. 라이브 무영향. 백테스트/shadow 비교축.
- **P0-2 고점추격 금지(JD-R25)** — 더트레이딩 R11·운영실측과 3중 수렴. 이미 더트레이딩 트랙(진입가 위치 결합 갭가드)에서 진행 중 → **중복 작업 회피**, 주식단테 근거를 그 트랙에 보강 인용만.

### P1 — 신규 신호 (shadow → 백테스트 → (d))
- **P1-1 장기 EMA 레짐 게이트(JD-R1~R4)** — 종목별 224/112/448 정·역배열 + 위치. shadow 측정(전략 활성/억제 추천만, 차단 X) → OOS 후 (d).
- **P1-2 공구리 S-R flip 진입(JD-R7)** — `sr_flip` 신호 → 백테스트(IntradaySimulator/일봉 grid) → (d) 신규 전략 OFF 등록.
- **P1-3 수급게이트(JD-R18)** — (a) 신용잔고비율·ETF상장·코스피 쏠림 데이터 소싱 → (c) inert 게이트 → (d) 실차단.
- **P1-4 R:R 게이트(JD-R21)** — (c) shadow(NO-GO 추천 로깅) → (d) `risk_manager.min_rr_ratio` 실차단.

### P2 — 패턴 디텍터(난이도 중~고)·데이터 의존
- 밥그릇/매집봉/하이힐 디텍터(JD-R5/6/8, 부분) · 역매공파 통합(JD-R9) · 양봉/음봉 차별(JD-R12) · 장세 4단계(JD-R19) · 오돌이 검색기 라이브 항목(JD-R10 ⑥⑦, L2 의존).

---

## 6. 신규 (c) 안전 모듈 스펙 — `dante_filters.py`

> `closing_bet_filters.py` 패턴 계승: 순수함수, `List[OHLCV]` 오래된→최신, **inert(호출처 없음)·관측 전용·라이브 무영향**. env=0/미호출 시 동작 불변. 각 함수에 JD-R번호+채널 인용 docstring.

```python
# backend/core/strategy/dante_filters.py  (스펙 — 구현은 승인 후 별도 증분)
from typing import List, Optional
from backend.models.market import OHLCV

def _ema(candles: List[OHLCV], period: int) -> Optional[float]: ...   # 장기 EMA 헬퍼(112/224/448)

# JD-R1/R4 — 장기 EMA 정/역배열 + 224 위치 레짐
def ma_alignment(candles, periods=(112, 224, 448)) -> Optional[str]:
    """'정배열'(p1>p2>p3) | '역배열'(p1<p2<p3) | '혼조' | None(데이터부족)."""

def above_ma224(candles, period: int = 224) -> Optional[bool]:
    """종가 ≥ EMA224 → True(상승레짐). 데이터부족 None."""

# JD-R7 — 공구리(S-R flip): 하락파동 직전 전고 상향돌파 + flip선
def sr_flip(candles, pivot_lookback: int = 20) -> Optional[dict]:
    """직전 swing-high 상향 돌파 시 {'flip_price','break_open','target'} 반환.
    target = break_price + (직전 하락폭)  # 대칭. 손절=flip_price 이탈. 없으면 None."""

# JD-R5 — 밥그릇(원형바닥 3번 자리) 근사: 224 아래 N개월 횡보 후 돌파
def saucer_third_zone(candles, base_min_days: int = 80, breakout_mult: float = 2.0) -> bool:
    """224 아래 base_min_days+ 횡보 → 상방 이격 ≥ 직전이격×breakout_mult 돌파 → True."""

# JD-R6 — 매집봉(흡수): 고가대비 종가 되돌림율 + 거래량 스파이크
def accumulation_candle(candles, retrace_min: float = 0.7, vol_mult: float = 3.0) -> bool:
    """(high-close)/(high-low) ≥ retrace_min AND vol ≥ 평균×vol_mult → True."""

# JD-R13 — distribution 경보: 정배열 확장구간 거래량 300% 장대음봉
def distribution_alert(candles, vol_mult: float = 3.0, body_min: float = 0.03) -> bool:
    """정배열 + 음봉 + 몸통 ≥ body_min + vol ≥ 전일×vol_mult → True(청산/회피)."""

# JD-R20 — 오돌리: 5일선이 15일선 상향 크로스(당봉)
def odori_cross(candles, short: int = 5, long: int = 15) -> bool: ...

# JD-R21 — 최소 손익비 게이트(관측): 목표폭/손절폭 ≥ min_rr
def rr_ratio_ok(entry: float, stop: float, target: float, min_rr: float = 2.0) -> Optional[bool]:
    """(target-entry)/(entry-stop) ≥ min_rr → True. 분모≤0 None."""
```
- **단위테스트**: 각 함수 경계값 + `test_dante_filters_not_imported_by_live_path`(signal_scanner가 직접 import 안 함 = inert 보장).
- **shadow 배선**: 데몬/백테스트에서 *로그만*(env `BARRO_DANTE_SHADOW=0` 기본). 차단·진입 변경 없음.
- **(d) 활성 경로**: OOS PASS 후 AskUserQuestion → `barrotrade-code-surgeon`로 게이트 wiring(default-OFF env).

---

## 7. 검증 & HITL

### 7.1 백테스트 가능 / 불가
| 가능(일봉 grid + OOS) | 제한/불가(데이터·재량) |
|---|---|
| sr_flip(JD-R7), distribution(JD-R13), 오돌리(R20), R:R(R21), 224레짐(R1~R4), 수급게이트(R18, KRX 공개데이터), 검색기 OHLCV 항목(R10 일부) | 매집봉 거래량 정량(R6, OBV 대리), 밥그릇/하이힐 자리 레이블(R5/8), 체결강도·호가잔량(R10 ⑥⑦, L2), 비공개 지표(수박·파란점선) |

### 7.2 과최적화 방지 (3중)
1. **OOS/holdout** — `scripts/_oos_validation.py` 관문(active≥15 & trades≥30 & avg_ret>0 & drop1 부호안정 & holdout>0). ★PASS 전 라이브 자본 금지.
2. **walk-forward** — 그리드 최적치 인접구간 유지.
3. **파라미터 민감도** — 임계 ±1스텝 손익 부호 유지(절벽형 거부). (224 lookback·distribution vol_mult·R:R 임계 필수.)

### 7.3 단계적 배포 순서
1. (c) `dante_filters.py` inert + 단위테스트 + shadow 로그 — 라이브 무영향.
2. shadow N일 누적 → 임계 캘리브레이션(특히 "224 vs 20" 혼동 해소, distribution vol_mult).
3. 백테스트(sr_flip·distribution·R:R 우선=정량 완결) → OOS PASS.
4. AskUserQuestion(d) → `barrotrade-code-surgeon` → 게이트/신규전략 default-OFF env 활성.

### 7.4 HITL 게이트 (자동화 금지)
①장기 EMA 레짐으로 전략 on/off ②공구리/distribution 실진입·청산 ③R:R 실차단 ④수급게이트 실차단 ⑤신규 전략(sr_flip/역매공파) 라이브 ⑥분할비율·몰빵한도 변경 — 모두 AskUserQuestion + code-surgeon. **농사매매(마틴게일)는 제안 자체 비권고.**

### 7.5 롤백·관측
- 모든 게이트 env 기반 → `env=0` 즉시 무력화. param 변경은 git revert.
- 관측: `_daily_strategy_audit.py`(§A 승률·자본가중, §B 진입품질, §C 청산슬립) + shadow 분포로 224레짐/공구리/distribution 효과 사전 해석.

---

## 8. 다음 단계 (이 문서 이후)

본 문서는 **분석·설계**다. 사용자 승인 시 다음 증분:
1. **(c) `dante_filters.py` inert 모듈 + 단위테스트 + shadow** — 라이브 무영향, 즉시 가능(더트레이딩 increment 패턴).
2. **백테스트** — sr_flip(JD-R7)·distribution(JD-R13)·R:R(JD-R21)부터(정량 완결) → `scripts/backtest_*` + `_oos_validation.py`.
3. **(d) HITL** — OOS PASS 항목만 AskUserQuestion → code-surgeon 위임.

> 각 증분은 `barro-trade-review` 매매복기 사이클로 효과를 측정·환류한다. 우선순위 추천: **JD-R21(R:R) + JD-R13(distribution) + JD-R7(공구리)** — 정량 완결·구현비용 최저·현 시스템 부재 영역.
