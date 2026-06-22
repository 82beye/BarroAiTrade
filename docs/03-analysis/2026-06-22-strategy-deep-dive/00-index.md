# BarroAiTrade 매매전략 전체 심층 분석 — 인덱스

> 생성: 2026-06-22 · 진실원천: **코드**(origin/main HEAD `013d54b`) · 모든 수치는 인용된 `file:line` 기준
> 범위: 현재 구현된 매매전략 **전체**. 활성 4 · 비활성(구현됨) 3 · 별도경로(opt-in) 2 = **9개 전용 리포트** + 보조/연구 모듈 정리.

이 폴더는 BarroAiTrade에 구현된 모든 매매전략을 코드 기준으로 1전략=1리포트로 분해한 것이다. 각 리포트는 동일 9섹션 템플릿(요약·개요·진입·청산·파라미터·운영상태·비용·검증/한계·관련파일)을 따른다. 손익/임계 수치는 **재계산하지 않고 코드에서 인용**했으며, 코드에 근거가 없는 백테스트/OOS 수치는 "(미검증)"으로 명시했다.

---

## 1. 전략 카탈로그 (상태 매트릭스)

| 전략                  | 한글      | 상태         | 시간프레임            | 컨셉 한 줄                             | priority¹ | 리포트                                      |
| ------------------- | ------- | ---------- | ---------------- | ---------------------------------- | :-------: | ---------------------------------------- |
| **sf_zone**         | SF존/슈퍼존 | 🟢 활성      | 1분봉 단타           | F존 강화 — 강한 기준봉+거래량+고점수만 통과, 타이트 청산 |     4     | [sf_zone.md](sf_zone.md)                 |
| **f_zone**          | F존      | 🟢 활성      | 1분봉 단타           | 급등 후 눌림목 이평지지+반등 5단계 추세추종          |     3     | [f_zone.md](f_zone.md)                   |
| **gold_zone**       | 골드존     | 🟢 활성      | 1분봉 단타(되돌림)      | BB하단+Fib되돌림+RSI과매도 회복 mean-revert  |     2     | [gold_zone.md](gold_zone.md)             |
| **swing_38**        | 38스윙    | 🟢 활성      | 일봉 스윙(3~20일)     | 임펄스 후 Fib 0.382 되돌림 반등 멀티데이 스윙     |     1     | [swing_38.md](swing_38.md)               |
| **closing_bet**     | 종가베팅    | ⚪ 비활성²     | 일봉진입+오버나잇(D1~D3) | 막판 신고가 장대양봉 주도주를 종가에 잡아 익일 슈팅에 매도  |     8     | [closing_bet.md](closing_bet.md)         |
| **blue_line**       | 블루라인    | ⚪ 비활성      | 1분봉 단타           | 5EMA×20EMA 골든크로스+거래량 급증 돌파         |     6     | [blue_line.md](blue_line.md)             |
| **crypto_breakout** | 암호화폐 돌파 | ⚪ 비활성³     | intraday 돌파      | 박스권 상단 거래량 동반 돌파 (crypto 전용)       |     7     | [crypto_breakout.md](crypto_breakout.md) |
| **supertrend**      | 슈퍼트렌드   | 🟡 opt-in⁴ | 추세추종(5분봉)        | ATR 밴드 추세전환을 진입·청산 대칭 이벤트로 매매      |     5     | [supertrend.md](supertrend.md)           |
| **limit_up_chase**  | 상한가 추격  | 🟡 별도경로⁴   | intraday~오버나잇    | 등락률 +20~27% 모멘텀+호가 매수벽으로 상한가 직전 진입 |     –     | [limit_up_chase.md](limit_up_chase.md)   |

¹ `STRATEGY_PRIORITY` (`signal_scanner.py:59-62`) — 다종목 슬롯/자본 경합 시 점수 동률이면 작은 값 우선(tiebreaker). 신호 *발행* 순서가 아님.
² closing_bet: 스캐너 default OFF. 자동매수 executor는 `BARRO_CB_AUTOEXEC`(default 0=OFF) opt-in, 라이브는 HITL.
³ crypto_breakout: flag OFF + crypto 게이트웨이 부재(stub) → **토글을 켜도 신호 0건**.
⁴ supertrend·limit_up_chase: `SignalScanner._DEFAULT_ENABLED`에 미등록. 각각 별도 스캐너/트레이더 경로 + env/플래그 opt-in.

### 활성/비활성 한눈에
- **🟢 라이브 default 활성 (4)**: `sf_zone · f_zone · gold_zone`(1분봉 단타) + `swing_38`(일봉 스윙)
- **⚪ 구현됐으나 default 비활성 (3)**: `closing_bet · blue_line · crypto_breakout`
- **🟡 별도경로 opt-in (2)**: `supertrend`(--supertrend) · `limit_up_chase`(LIMIT_UP_CHASE_ENABLED)

---

## 2. 공통 인프라 (전략이 공유하는 골격)

### 2.1 신호 스캐너 dispatch (`backend/core/scanner/signal_scanner.py`)
- `_DEFAULT_ENABLED`(`:41-55`)로 전략별 활성/비활성 토글. 비활성도 인스턴스는 생성되어 flag만 켜면 재활성(코드변경 불요).
- 종목별 평가 순서(`_analyze_symbol`, `:141-211`): **1분봉 그룹**(SF → F → Gold → Blue → Crypto, 활성 중 *첫 신호*가 그 종목 대표) → **일봉 그룹**(swing_38 → closing_bet). 1분봉 활성 전략이 하나도 없으면 1m fetch skip(비용 절약), swing/종베 비활성이면 일봉 fetch skip.
- 다종목 신호는 `점수 내림차순, STRATEGY_PRIORITY 오름차순`으로 정렬(`:137`).

### 2.2 청산 메커니즘 (2계층)
1. **전략 자체 ExitPlan** — 각 전략 `analyze`가 진입과 함께 반환하는 1차 TP/SL 그리드(전략 파일 내).
2. **HoldingEvaluator 안전망** — `backend/core/risk/holding_evaluator.py`의 `STRATEGY_EXIT_PROFILES`. 전략별 SL/TP/부분익절/트레일링/본전/보유기간을 *별도로* 정의(대개 ExitPlan보다 너그러운 2차 백스톱).
   - 전용 프로파일 보유: `f_zone · sf_zone · gold_zone · swing_38 · closing_bet`.
   - **blue_line · crypto_breakout 은 전용 프로파일 없음** → 전략무관 default `ExitPolicy`(TP +5%/SL −4%)로 fallback (미튜닝).
   - supertrend 은 가격 TP/SL이 아닌 **전용 `SupertrendExitWatcher`**(지표 전환) 청산.

### 2.3 거래비용 (`backend/core/trading_costs.py:29-33`)
| 항목 | 값 | env override |
|---|---|---|
| 편도 수수료 `COMMISSION_RATE` | **0.35%** (0.0035) | `BARRO_COMMISSION_RATE` |
| 매도 거래세 `TAX_RATE_SELL` | **0.20%** (0.0020) | `BARRO_TAX_RATE_SELL` |
| **왕복 총비용** `ROUND_TRIP_COST_RATE` | **0.90%** | (자동 = 0.35%×2 + 0.20%) |

> ⚠️ `trading_costs.py:32`의 "손익분기 ≈0.55%" 주석은 **stale**(구 모델). 실제 왕복은 **0.90%**. 다수 백테스트/그리드가 구 저비용(왕복 ~0.21~0.55%) 가정으로 산출돼, 현 0.90% 기준 단타 손익은 재검증 필요(아래 §4).

### 2.4 데몬 운영 게이트 (`scripts/intraday_buy_daemon.py`)
| 게이트 | 적용 전략 | 효과 |
|---|---|---|
| `_MEANREV_STRATEGIES` (`:627`) | `{gold_zone}` | DCA(물타기) gate 대상(`--dca-strategy-gate` 시) |
| `_NO_DCA_STRATEGIES` (`:634`) | `{swing_38, supertrend}` | 자체 분할매수 전략 → 데몬 DCA 비활성 |
| `_GAP_GUARD_STRATEGIES` (`:658`) | default `{gold_zone, f_zone}` | 시초 갭 상한 가드 (env `BARRO_GAP_GUARD_STRATEGIES`로 편입) |
| `_CUTOFF_EXEMPT_STRATEGIES` (`:666`) | `{swing_38}` | 14:30 진입 컷오프 면제(일봉 스윙) |
| `_FORCE_CLOSE_EXEMPT_STRATEGIES` (`:672`) | `{swing_38}` | 장마감 강제청산(이월한도 트림) 면제 |
| `trap_guard` (`:702`) | f_zone(+sf 상속) | 고점/함정봉 진입 가드 |
| `DistributionExitConfig` | (전 전략, default OFF) | 세력이탈 장대음봉 청산 게이트 — `distribution_exit_enabled=False` |
| `regime_exit` / `net_aware_tp` | (default OFF) | 레짐 청산·net 인지 TP — 전부 default OFF |

---

## 3. 전략별 한 줄 진단 (요약)

- **f_zone** 🟢 — 5단계(기준봉+3%/vol×2 → 눌림 → 이평지지 → 반등) 종합점수 ≥4.0. **비용 후 흑자는 일봉 백테스트에서만 확인**, 운영 1분봉은 (구 비용 기준에서도) 적자 셋업 — OOS 미검증.
- **sf_zone** 🟢 — F존 엔진의 **delegate 래퍼**(진입 판정 동일, `score≥7 & gain≥5% & vol≥3x`만 SF 승격 후 재라벨). 차별점은 더 타이트/공격적 전용 청산. *priority 모순*(아래 §4) 주의.
- **gold_zone** 🟢 — mean-revert 바닥 매수(BB하단+Fib+RSI). DCA·gap-guard 특수처리 대상. TP+2% 부분익절은 비용 후 순 +1.1%로 마진 얇음.
- **swing_38** 🟢 — 유일한 일봉/멀티데이 활성. **OOS 5/5 PASS**(holdout>full, 과최적화 없음), 4~6월 안정성 1위. 멀티데이라 왕복비용 1회만 → 비용 감도 최저.
- **closing_bet** ⚪ — 완성된 비활성 스캐폴딩. 자동매수 `BARRO_CB_AUTOEXEC` default OFF. **종가 진입 시 net 브레이크이븐**, 이격도 게이트 ON 시 +0.4~0.5% 개선(robust). 라이브는 HITL.
- **blue_line** ⚪ — 5/20 EMA 골든크로스 돌파. 전용 청산 없음(default fallback). Phase D2.1 단타 전환으로 보류, 실 OOS 미검증.
- **crypto_breakout** ⚪ — crypto 전용 하드 게이트. 한국주식엔 신호 0 + crypto 게이트웨이 부재로 **사실상 dead**. 전용 청산·비용모델 부재.
- **supertrend** 🟡 — standalone(scanner+exit_watcher+auto_trader), `--supertrend` opt-in. 지표전환 청산. **코드 주석이 "OOS 기대값 음수"라 자인** → 비권고, DCA 제외·비중 억제.
- **limit_up_chase** 🟡 — SupertrendAutoTrader 상속, 진입만 상따 전용(등락률 밴드+호가 매수벽), 청산은 부모 RUNNER 재사용. env 미설정 시 비가동, 호가 L2 부재로 백테스트 구조적 불가.

---

## 4. 횡단 발견 · 점검 권고 (코드 근거)

1. **비용 가정 불일치(전 전략 영향)** — `trading_costs.py:32` "≈0.55%" 주석 stale, 실제 왕복 0.90%. 단타 전략들의 흑자 백테스트가 구 저비용 가정 → **현 0.90% 기준 단타 OOS 재검증 필요**.
2. **단타 1분봉 흑자 미검증** — f_zone 그리드상 흑자는 *일봉*뿐, 1분봉 default는 (구 비용에서도) 적자(-0.45%/trade). 현재 라이브 핵심이 1분봉 단타임을 감안하면 가장 큰 미검증 리스크.
3. **sf_zone 우선순위 모순** — dispatch에선 SF가 종목 내 최우선 선점이나, 다종목 tiebreaker `STRATEGY_PRIORITY`에선 `sf_zone=4`로 `f_zone(3)·gold_zone(2)`보다 후순위. 운영 의도(강한 셋업 우선)와 어긋남 → 점검 권장.
4. **gold_zone docstring vs default 불일치** — 모듈 docstring(BB1%/Fib0.382~0.618/RSI30→40)이 실제 default(3%/0.236~0.786/35→38)와 다름. default가 진실원천.
5. **blue_line·crypto_breakout 전용 청산 부재** — default ExitPolicy fallback. 재활성 전 청산 튜닝 필요.
6. **closing_bet 진입가 가정 민감** — OOS 흑자(+3.5%)는 "익일 시초 진입" 가정 의존. 실제 *종가* 진입이면 브레이크이븐 회귀가 핵심 caveat.

---

## 5. 보조 · 연구 · 레거시 모듈 (전용 리포트 없음)

매매 *전략*이 아니거나(필터·청산 헬퍼·인프라), 연구/inert/레거시라 전용 리포트를 만들지 않은 모듈:

| 모듈 | 성격 |
|---|---|
| `dante_filters.py` | 주식단테 방법론 inert 연구 필터(공구리 sr_flip·매집봉·224레짐·distribution) + `DistributionExitConfig`(청산게이트 default OFF). 라이브 호출처 거의 없음(연구/관측). |
| `trap_guard.py` | 진입 함정 가드(`TrapGuardConfig`). f_zone/sf_zone에 적용되는 **필터**(전략 아님). |
| `short_term_high_exit.py` | 단기 고점 청산 헬퍼(청산 보조). |
| `closing_bet_filters.py` | closing_bet 보조 필터(이격도·과열·유동성). |
| `round_figure.py` | 호가/라운드피겨 근접 헬퍼(이격도 게이트 등에서 사용). |
| `scalping_consensus.py` | 백테스트 `intraday_simulator` 컨센서스 모델 + legacy_scalping. 라이브 진입전략 아님. |
| `ob_scalp.py` | 호가(orderbook) 스캘프 **shadow 연구**(`scripts/_ob_scalp_shadow.py`). 라이브 미편입. |
| `stock_strategy.py` | 파란점선+수박 통합(StockStrategy). `run_baseline.py` **베이스라인 벤치마크** 용도. |
| `_watermelon.py` | 수박지표(비공개 재현) 레거시. 라이브 미인스턴스화. |
| `indicators.py · base.py · position_sizing.py · backtester.py` | 공통 인프라(지표·베이스클래스·사이징·백테스터). |

---

## 6. 한계 · 면책

- 본 분석은 **코드 정적 분석**이다. 손익/승률 수치는 코드·기존 백테스트 산출물 인용이며, 본 작업에서 백테스트를 재실행하지 않았다(재현은 별도 OOS 도구의 몫).
- 활성/비활성 상태는 `_DEFAULT_ENABLED` 코드 기준이다. **운영 머신의 실제 기동 커맨드/`policy.json`/env가 이를 override할 수 있으므로**, 실제 라이브 활성 전략은 운영 머신에서 별도 확인해야 한다.
- 모든 임계는 사후 선별 편향 가능성이 있어 OOS 검증이 전제다(swing_38만 OOS 5/5 PASS 보유).

---

*진실원천: `signal_scanner.py` · `holding_evaluator.py` · `trading_costs.py` · `intraday_buy_daemon.py` + 각 전략 파일. 상세 인용은 개별 리포트의 file:line 참조.*
