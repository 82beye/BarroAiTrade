# BarroAiTrade × ai-trade 통합 고도화 계획

> **목표**: 현재의 두 시스템(BarroAiTrade 풀스택 + ai-trade 멀티에이전트)을 PRD에서 정의한 "**대한민국 NXT/KRX 통합 실시간 테마 인텔리전스 + 자동매매 플랫폼**"으로 진화시킨다.

---

## 1. 현 시스템 진단 (As-Is)

### 1.1 BarroAiTrade (Main, 풀스택 플랫폼)

**구조**

```
backend/
├── api/routes/        # signals, market, trading, positions, watchlist, config, reports, risk
├── core/
│   ├── gateway/       # MarketGateway 추상 + KiwoomGateway (OAuth, RateLimiter, Health)
│   ├── strategy/      # FZone, BlueLine, CryptoBreakout, Backtester(852줄)
│   ├── scanner/       # DailyScreener, RealtimeScreener, IndicatorCalculator
│   ├── risk/          # RiskEngine, ComplianceService
│   ├── execution/     # OrderExecutor, PositionManager
│   ├── monitoring/    # Telegram, Logger, Reports, Alerts
│   └── orchestrator.py # asyncio.TaskGroup 기반 멀티 태스크 관리
├── db/                # SQLite + audit_repo
└── models/            # Pydantic v2 (market, signal, position, risk, config)

frontend/  # Next.js 15 + React 19 + TS5 + Tailwind + Zustand + lightweight-charts
├── app/    # /, /trading, /markets, /watchlist, /positions, /reports, /settings
└── hooks/useWebSocket.ts, useRealtimeConnection.ts
```

**강점**
- `Strategy` 추상 인터페이스가 깔끔히 정의되어 있어 전략 추가가 용이함
- `MarketGateway` 추상화로 거래소 추가가 가능한 구조
- Orchestrator가 `asyncio.TaskGroup`으로 supervised task를 운영해 **부분 실패에 강함**
- PDCA 문서 체계(`docs/01-plan ~ 04-report`)가 이미 정착됨
- Docker Compose로 backend/frontend/Prometheus/Grafana 일괄 배포 가능
- 백테스터(852줄)가 Multi-strategy 비교까지 지원

**약점 / 갭**
- `MarketType.STOCK`만 가정 — **NXT/KRX 분기 개념이 모델에 없음**
- 호가창(OrderBook) 모델은 있지만 **통합 호가창 로직 없음**
- 테마/뉴스/일정 도메인이 **완전히 부재**
- 실시간 시세는 키움 단일 소스에 의존 (NXT 시세는 미연결)
- 보안: JWT/MFA/RLS 미구현, `.env.local` 평문 의존
- 프론트 UI가 **PRD의 "테마 박스/통합 호가/캘린더"** 와 거리가 멈

### 1.2 ai-trade (Sub, 데이트레이딩 봇)

**구조**

```
main.py                # 2,412줄 모놀리식 진입점
scanner/
├── indicators.py
├── daily_screener.py, realtime_screener.py
├── leading_stocks.py  # 주도주 판별
├── bb_ichimoku.py     # 일목 + BB
├── market_condition.py
├── ohlcv_cache.py     # ~2,960 종목 일봉 JSON 캐시
└── agents/            # momentum, breakout, coordinator
strategy/
├── entry_signal.py, exit_signal.py, carryover_exit.py, intraday_filter.py
├── scalping_team/     # 12개 에이전트 (vwap, momentum_burst, breakout_confirm,
│                      #              spread_tape, golden_time, pullback,
│                      #              relative_strength, candle_pattern,
│                      #              volume_profile, risk_reward 등)
├── strategy_team/     # entry_timing, exit_optimizer, sizing, risk_reward, trade_pattern
└── verification_team/ # coordinator (956줄)
execution/
├── kiwoom_api.py      # 1,569줄 — REST 직접 구현
└── order_processor.py # 957줄
monitoring/
└── telegram_bot, daily_report, scalping_report, dashboard, notion_sync
```

**강점**
- **수박지표 + 파란점선** 등 주식단테 역매공파 이론을 코드화 — 차별화 자산
- **멀티에이전트 합의 모델**(scalping_team coordinator)이 단일 지표 의존을 탈피
- OHLCV 캐시 시스템 — 매일 35분 cron으로 전종목 일봉을 로컬에 보유
- 실전 운용 흐름(08:00 점검 → 08:25 시작 → 14:50 강제청산 → 15:10 리포트)이 cron으로 안정화됨

**약점 / 갭**
- **모놀리식 main.py(2,412줄)** — 테스트, 재사용, 개별 모듈 교체가 어려움
- BarroAiTrade의 표준 모델/Gateway/Risk와 **타입이 호환되지 않음**
- UI 없음 (Flask 미니 대시보드만)
- NXT 미지원 — 14:50 강제청산 정책은 KRX 정규장 종료 기준임

### 1.3 서희파더 전략 코드 (`seoheefather_strategy.py`)

**4가지 전략이 클래스 단위로 잘 분리되어 있음**: `FZoneStrategy`, `SFZoneStrategy`, `GoldZoneStrategy`, `Swing38Strategy`. 각 전략에 `detect_*()`, `backtest()` 메서드와 시각화까지 포함. **현재 BarroAiTrade에는 F존/SF존만 일부 반영**되어 있고 **골드존, 38스윙은 미반영**.

이 코드는 PoC 수준이지만 **이미 검증된 로직 4세트**로 봐야 하며, 표준 `Strategy` 인터페이스로 포팅하는 작업이 Phase 1의 핵심 산출물 중 하나가 됨.

---

## 2. 목표 시스템 정의 (To-Be)

### 2.1 한 줄 비전

> **NXT/KRX 통합 실시간 테마 인텔리전스 + 멀티 전략 자동매매 + 금융보안원 수준 보안을 갖춘 통합 트레이딩 플랫폼**

### 2.2 PRD 요구사항 → 시스템 책임 매핑

| PRD 요구 | 담당 모듈 | 신규/기존 |
|---|---|---|
| KRX/NXT 거래시간 인지 (08:00–20:00) | `core/market_session/` | **신규** |
| 통합 호가창 (KRX + NXT 병합) | `core/gateway/composite_orderbook.py` | **신규** |
| 테마 박스 / 대장주 판별 | `core/theme/` (engine, classifier, leader_picker) | **신규** |
| 실시간 특징주 슬라이더 | `core/news/news_stream.py` + WebSocket | **신규** |
| 일정/캘린더 + 일정-테마 연동 | `core/scheduler/` + 신규 DB 테이블 | **신규** |
| 4대 전략(F존/SF존/골드존/38스윙) | `core/strategy/` 확장 | **확장** |
| 멀티에이전트 합의 진입 | sub_repo의 `scalping_team` 흡수 | **이관** |
| 자동 주문 실행 + 분할 익절/손절 | `core/execution/` 확장 | **확장** |
| 매매 일지 + 메모 | `core/journal/` | **신규** |
| RASP/MFA/RLS 등 보안 | `backend/security/` 신규 + DB 정책 | **신규** |

### 2.3 격차(Gap) 요약

- **데이터 계층 격차 ~70%**: NXT 시세 미연동, 통합 호가, 뉴스/공시/일정 데이터 파이프라인 부재
- **인텔리전스 계층 격차 ~85%**: 테마 분류, 대장주 판별, 일정 연동 등 PRD의 차별화 요소가 거의 미구현
- **전략 계층 격차 ~40%**: 4대 전략 중 2개만 부분 구현, 멀티에이전트 합의 모델 미통합
- **보안 계층 격차 ~75%**: 인증/인가/감사 로그/RASP가 기본기 수준에 머무름
- **UI 격차 ~60%**: 대시보드 골격은 있으나 PRD의 "테마 박스 + 통합호가 + 캘린더" 화면 미구현

---

## 3. 고도화 로드맵 (6 Phase / 약 20–26주)

각 Phase는 PDCA 사이클을 기존 `docs/` 구조에 맞춰 진행하며, **모든 Phase 종료 시 회귀 백테스트 + 모의투자 1주 검증**을 통과해야 다음 단계로 넘어간다.

### Phase 0: 기반 정비 (Week 1–2) — "두 레포 통합과 기준선 정비"

**목표**: 분산된 자산을 하나의 작업 가능한 코드베이스로 모은다.

| Task | 산출물 | DoD |
|---|---|---|
| 0.1 sub_repo → main_repo 모노레포 흡수 | `backend/legacy_scalping/` 디렉터리로 ai-trade 보관 | main_repo CI 통과 |
| 0.2 모델 호환성 검증 | `OHLCV/Order/Position` 모델을 ai-trade가 import해 사용 가능하게 어댑터 작성 | 어댑터 단위테스트 |
| 0.3 통합 환경변수 스키마 | `pydantic-settings`의 `Settings`에 NXT/뉴스/테마 키 추가 | `.env.example` 업데이트 |
| 0.4 표준 로깅/메트릭 통일 | sub_repo도 `core/monitoring/logger`, Prometheus 카운터 사용 | Grafana에서 동일 대시보드로 관측 |
| 0.5 회귀 백테스트 베이스라인 측정 | F존/블루라인/수박/암호화폐 4개 전략의 현재 승률·MDD 기록 | `docs/04-report/baseline.md` |

**주의점**: ai-trade의 `main.py` 2,412줄을 **이번 단계에서 쪼개지 않는다**. 동작을 깨뜨리지 않은 채 모듈로 import만 가능하게 하는 것이 목표.

---

### Phase 1: 전략 엔진 통합 + 4대 매매기법 완성 (Week 3–6)

**목표**: 서희파더 4대 전략 + 멀티에이전트 합의 모델을 표준 `Strategy` 인터페이스로 통합한다.

#### 1.1 전략 추상화 확장

현재 `Strategy.analyze()`는 진입 신호만 반환한다. 청산 조건, 분할 매도, 포지션 사이징을 일급 객체로 끌어올린다.

```python
# backend/core/strategy/base.py 확장
class Strategy(ABC):
    STRATEGY_ID: str
    PARAMS_SCHEMA: type[BaseModel]      # Pydantic 파라미터 스키마

    @abstractmethod
    def analyze(self, ctx: AnalysisContext) -> Optional[EntrySignal]: ...

    @abstractmethod
    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan: ...

    @abstractmethod
    def position_size(self, signal: EntrySignal, account: Account) -> Decimal: ...

    def health_check(self) -> dict: ...   # 데이터 충분성, 파라미터 sanity
```

`AnalysisContext`는 KRX/NXT 통합 시세, 호가, 거래원, 테마, 뉴스, 시간대(프리/정규/애프터/블루오션)를 포함한다.

#### 1.2 4대 전략 구현/포팅

| 전략 | 현재 상태 | 작업 |
|---|---|---|
| F존 | 구현됨 (`f_zone.py` 408줄) | 신규 `Strategy` 인터페이스로 리팩터, ExitPlan 분리 |
| SF존 | F존 내 분기 처리 | **별도 클래스로 분리** + 강도 가중치 명시화 |
| 골드존 | **미구현** | `seoheefather_strategy.py`의 `GoldZoneStrategy`를 포팅 (BB + Fib 0.382~0.618 + RSI 회복) |
| 38스윙 | **미구현** | 동 코드의 `Swing38Strategy` 포팅 (피보나치 0.382 되돌림 + 임펄스 탐지) |

#### 1.3 멀티에이전트 합의 모델 흡수

ai-trade의 `scalping_team/coordinator.py`가 가진 12개 에이전트(VWAP, 모멘텀 버스트, 돌파 확인, 스프레드 테이프, 골든타임, 풀백, 상대강도, 캔들패턴, 볼륨 프로파일, 리스크리워드 등)를 **하나의 메타전략 `ScalpingConsensusStrategy`**로 감싼다. 각 에이전트의 점수를 가중합산해 진입 강도를 산출한다.

```python
class ScalpingConsensusStrategy(Strategy):
    STRATEGY_ID = "scalping_consensus_v1"
    agents: list[ScalpingAgent]
    weights: dict[str, float]
    threshold: float = 0.65   # 65% 이상 합의 시 진입
```

#### 1.4 백테스터 확장

기존 백테스터(852줄)를 `Strategy.exit_plan()`을 인지하도록 확장하고, **워크포워드 분석**, **NXT 야간장 시뮬레이션**, **슬리피지/수수료/세금** 모델을 추가한다.

**Phase 1 DoD**
- 4대 전략 + 합의 전략 5개가 동일 인터페이스로 동작
- 5종 전략의 5년 백테스트 리포트 자동 생성
- 모의투자 환경에서 5종 전략 동시 운용 1주 무사고

---

### Phase 2: NXT 통합 + 통합 호가창 + 거래시간 인지 (Week 5–10, Phase 1과 병렬)

**목표**: PRD의 핵심 차별점인 **08:00–20:00 통합 거래 환경**을 시스템에 정착시킨다.

#### 2.1 거래 시장 모델 확장

```python
# backend/models/market.py
class Exchange(str, Enum):
    KRX = "krx"
    NXT = "nxt"
    COMPOSITE = "composite"   # 두 거래소 병합 뷰

class TradingSession(str, Enum):
    NXT_PRE = "nxt_pre"          # 08:00–08:50
    KRX_PRE = "krx_pre"          # 08:30–09:00
    REGULAR = "regular"           # 09:00–15:20 (NXT 09:00:30 시작)
    KRX_CLOSING_AUCTION = "krx_closing_auction"  # 15:20–15:30
    INTERLUDE = "interlude"       # 15:30–15:40
    KRX_AFTER = "krx_after"       # 15:40–18:00
    NXT_AFTER = "nxt_after"       # 15:40–20:00 (블루오션)
```

`MarketSessionService`가 시각·날짜·휴장일 캘린더를 보고 현재 세션을 판단해 **세션별 가용 거래소/주문유형/가격 제한**을 주입한다.

#### 2.2 NXT 게이트웨이

`MarketGateway` 추상화를 활용해 `NxtGateway`를 추가한다. NXT는 자체 API가 별도로 제공되므로:

- **단기 전략**: 키움 OpenAPI가 NXT 시세를 제공하는지 확인 후, 미제공 시 NXT 직접 API(또는 KOSCOM CHECK 등 통합 시세 벤더) 연동
- **WebSocket 통합 시세 채널**: KRX/NXT 두 채널을 백엔드에서 머지해 클라이언트엔 단일 스트림 공급

#### 2.3 통합 호가창

```python
# backend/core/gateway/composite_orderbook.py
class CompositeOrderBook:
    """KRX + NXT 호가를 가격별로 병합한 가상 호가창"""
    def merge(self, krx_book: OrderBook, nxt_book: OrderBook) -> OrderBook: ...
    def best_bid_ask(self) -> tuple[Decimal, Decimal]: ...
    def venue_breakdown(self, price: Decimal) -> dict[Exchange, int]: ...   # 가격별 거래소 비중
```

UI에서는 가격 옆에 KRX/NXT 색상 인디케이터로 어느 거래소에서 들어온 잔량인지 시각화한다.

#### 2.4 SOR(Smart Order Routing) 1차

주문 라우팅 정책을 명시화한다:
- 호가가 좋은 거래소 우선 (가격 우선)
- 동일 가격 시 잔량이 많은 거래소
- 사용자가 특정 거래소를 강제할 수 있는 모드 제공

**Phase 2 DoD**
- 08:00–20:00 어느 시점에 호출해도 정확한 세션과 가용 거래소가 반환됨
- 모의투자에서 NXT 야간 시간대 매수/매도 1주 운용 무사고
- 통합 호가창 UI가 lightweight-charts 또는 자체 컴포넌트로 동작

---

### Phase 3: 테마 인텔리전스 엔진 (Week 9–14, Phase 2와 일부 병렬)

**목표**: PRD의 가장 차별화된 요구인 **실시간 테마 클러스터링 + 대장주 판별 + 일정 연동**을 구현한다.

#### 3.1 데이터 수집 파이프라인

```
[뉴스 RSS / 공시 DART / 종목토론 / 유튜브 자막]
        ↓
   normalize → stream(Redis Streams) → topic queue
        ↓
   [NewsClassifier] [ThemeMatcher] [SentimentScorer]
        ↓
   ThemeStore (Postgres + Vector DB)
```

| 컴포넌트 | 기술 선택 |
|---|---|
| RSS/DART 수집 | `httpx` + `apscheduler` (1분 polling) |
| 형태소 분석 | `kiwipiepy` 또는 `Mecab` |
| 임베딩 | `sentence-transformers` (한국어 ko-sbert) |
| 벡터 DB | **pgvector**(기존 Postgres 도입 시) 또는 Qdrant |
| 메시지 큐 | Redis Streams (기존 인프라 최소 변경) |

#### 3.2 테마 분류기

PRD에서 언급한 **나이브 베이즈 + KNN**은 1차 베이스라인으로 충분하다. 더 강건한 결과를 원하면:

- 1차: **TF-IDF + Logistic Regression** (수십 개 사전 정의 테마 라벨)
- 2차: **Sentence Embedding + 코사인 유사도** (테마 사전과 매칭)
- 3차: **LLM zero-shot 분류** (`claude-haiku` 등으로 비용 효율적 백업)

테마 사전은 운영자가 관리할 수 있게 DB 테이블화 (`themes`, `theme_keywords`, `theme_stocks`).

#### 3.3 대장주 판별 알고리즘

PRD가 명시한 가중합:
```
score = w1 · 등락률 + w2 · 거래대금 + w3 · 상한가도달시각(빠를수록 가점)
      + w4 · 거래원집중도 + w5 · 신규편입여부
```

**w 가중치는 백테스트 기반 그리드 서치로 학습**해 데이터 기반으로 결정한다. 실전 운용 후 매월 재학습 잡 추가.

#### 3.4 일정 시스템 + 일정-테마 연동

신규 테이블:

```sql
CREATE TABLE market_events (
  id, date, time, event_type,         -- 실적, IPO, 정기주총, 옵션만기, FOMC 등
  related_themes JSON,
  related_symbols JSON,
  importance INT,                      -- 1~5
  source_url, created_at
);
```

수집 소스: 한국거래소 IR 캘린더, 인포맥스, FnGuide RSS, 사용자 수동 등록. **이벤트 D-1 / D-Day 발생 시 관련 테마 종목들의 시세를 우선 모니터링**하도록 스캐너에 힌트를 주입한다.

#### 3.5 프론트엔드: 테마 박스 + 캘린더

- `app/themes/page.tsx`: 테마 그리드(별표 고정, 평균 등락률, 대장주 4종목, 상한가 노란 강조)
- `app/calendar/page.tsx`: 평일/주말 토글, 섹터 필터, 일정-테마 클릭 시 관련 종목으로 점프
- 하단 슬라이딩 특징주 띠 컴포넌트 (`components/news-ticker.tsx`)

**Phase 3 DoD**
- 실시간으로 테마 박스가 갱신되고 대장주 순위가 변동
- 1주일간 운영하며 분류 정확도 ≥ 85% (운영자 라벨링 검증)
- 일정 → 종목 → 테마 → 호가창 으로 1-click 네비게이션 가능

---

### Phase 4: 자동매매 운영 엔진 + 매매 일지 (Week 13–18)

**목표**: 인간 의사결정 없이 시스템이 안전하게 매매를 수행하고, 결과를 학습 자산으로 축적한다.

#### 4.1 신호→주문 파이프라인 강화

```
Signal → RiskFilter → ComplianceFilter → Sizer → SOR → OrderExecutor
                ↓                    ↓
            (거부 사유 로깅)    (시간외 거래 제한 등)
```

- **이중 키 인증**: 실거래 모드 진입 시 OTP 추가 입력 필수 (이미 settings에 토큰만 있으면 자동 진입하는 구조 개선)
- **Kill Switch**: 일일 손실 ≥ -3%, 5분 내 연속 3회 슬리피지 임계 초과, 외부 시세 단절 시 즉시 전 포지션 시장가 청산 + 신규 진입 차단
- **Circuit Breaker**: 변동성 급변 시 전략별 자동 일시정지

#### 4.2 분할 익절/손절 표준화

서희파더 코드 + ai-trade의 +3%/+5%/-2% 정책을 **전략별 ExitPlan 객체로 일급화**한다.

```python
@dataclass
class ExitPlan:
    take_profits: list[TakeProfitTier]   # [(price, qty_pct, condition)]
    stop_loss: StopLoss                   # 고정 + 트레일링
    time_exit: Optional[time]             # 14:50 강제청산 등
    breakeven_trigger: Optional[Decimal]  # +1.5% 도달 시 손절을 +0.5%로 이동
```

#### 4.3 매매 일지 (Trade Journal)

PRD가 명시한 "차트/호가창에서 즉시 메모, 가격대·날짜와 연결"을 구현:

```sql
CREATE TABLE trade_notes (
  id, user_id, symbol, price_anchor, date_anchor,
  entry_strategy, exit_reason,
  emotion_tag,           -- "확신/긴가민가/공포" — 서희파더 심리 원칙
  result_pnl,
  attached_chart_url,
  created_at
);
```

**연말/월말 자동 분석**: 강점 전략, 시간대별 승률, 감정 태그별 PnL 분포를 리포트로 생성.

#### 4.4 비중 관리 (Position Sizing)

서희파더 원칙의 코드화:
- 동시 보유 최대 3종목
- 종목당 자산의 30% 이하
- 신규 진입 시 잔여 한도 자동 계산
- 동일 테마 중복 진입 시 합산 한도 적용

이는 `RiskEngine`의 신규 정책으로 추가.

**Phase 4 DoD**
- 모의투자에서 3주 연속 자동매매, 인간 개입 0회
- Kill Switch가 의도한 시나리오에서 100% 발동
- 매매 일지가 매매와 자동 동기화

---

### Phase 5: 보안 강화 (Week 11–20, 지속) — "금융보안원 기준 진입"

**목표**: 핀테크 보안 점검 가이드(LIAPP/금보원) 수준의 방어선 구축.

#### 5.1 인증/인가

| 항목 | 현재 | 목표 |
|---|---|---|
| 사용자 인증 | 없음(단일 사용자 가정) | JWT + Refresh Token + httpOnly Secure 쿠키 |
| MFA | 없음 | TOTP(Google Authenticator) + 실거래 모드 강제 |
| 권한 | 없음 | RBAC: viewer / trader / admin |
| 감사 로그 | `audit_repo.py` 일부 | 모든 trading.* 호출 자동 감사, 30일 무결성 해시 체인 |

#### 5.2 데이터 보호

- **DB 행 단위 보안(RLS)**: Postgres RLS 정책으로 사용자별 격리. 다중 사용자 확장 대비 미리 도입.
- **민감 키 보호**: `.env` → AWS Secrets Manager / Vault 마이그레이션
- **TLS 1.3 + 인증서 핀닝**: Next.js fetch에 핀닝 미들웨어, 백엔드 호출 시 SSL 검증 강제
- **DB 암호화**: 키움 자격증명, OAuth 토큰은 컬럼 암호화(Fernet) 적용

#### 5.3 클라이언트 보호 (모바일 앱 진출 대비)

PRD가 언급한 RASP 항목은 데스크톱 웹에선 일부만 적용 가능하나, **모바일 React Native 진출을 가정**해 다음을 준비:

- 루팅/탈옥/Frida/Magisk 감지 라이브러리(LIAPP 등) 평가
- 화면 캡처 방지 + 원격 제어 감지 → 화면 블랙아웃
- 디버그 빌드/릴리즈 빌드 분리, 릴리즈에서 `console.log` 일괄 제거

#### 5.4 AI 코드 거버넌스

PRD의 "v0/Cursor가 만든 코드 위험" 우려에 대응:

- **금융 연산이 들어가는 PR(주문, 포지션, 자금 흐름, 가격 계산 등)은 시니어 1인 + 보안 1인 리뷰 필수**
- AI 생성 코드는 PR 라벨 `ai-generated` 부착 → 별도 정적 분석 파이프라인(Bandit, Semgrep) 통과 의무화
- **금지 지시어 리스트**: AI가 RLS 정책, 인증 체크, 자금 흐름 코드를 “단순화/주석처리/우회”하지 못하게 PR 템플릿에 체크리스트

**Phase 5 DoD**
- OWASP Top 10 자동 스캔 통과
- 모의 침투 테스트(외부 또는 내부 공격팀) 1회 수행 및 P0/P1 이슈 0건
- 감사 로그가 모든 거래에 대해 무결성 검증 가능

---

### Phase 6: 운영 고도화 + 확장 (Week 19–26+)

**목표**: 단일 사용자 도구에서 멀티 사용자 SaaS형 플랫폼으로 진화.

| 영역 | 내용 |
|---|---|
| 멀티 사용자 | 사용자별 전략 인스턴스, 자산 격리, 사용량 메트릭 |
| 성능 | Redis 캐시 도입, WebSocket 채널 샤딩, Postgres 읽기 복제 |
| 옵저버빌리티 | OpenTelemetry trace, 알림 규칙 Grafana로 코드화 |
| 백오피스 | 운영자용 어드민 대시보드 (사용자/전략/감사로그 관리) |
| 모바일 | React Native 앱 (아이폰/안드로이드) — RASP 적용 |
| 외부 확장 | 해외주식(미국/홍콩) 게이트웨이, 코인 거래소 추가 |

---

## 4. 구체적 신규/리팩터 티켓 (Top 24)

| # | 티켓 | Phase | 우선순위 | 예상공수 |
|---|---|---|---|---|
| T01 | sub_repo 모노레포 흡수 + CI 통합 | 0 | P0 | 3d |
| T02 | OHLCV/Order/Position 모델 호환 어댑터 | 0 | P0 | 2d |
| T03 | `Strategy` 인터페이스 v2 (exit_plan, position_size, health_check) | 1 | P0 | 5d |
| T04 | F존/SF존 신규 인터페이스로 리팩터 | 1 | P0 | 3d |
| T05 | 골드존 전략 포팅 + 백테스트 | 1 | P1 | 4d |
| T06 | 38스윙 전략 포팅 + 백테스트 | 1 | P1 | 4d |
| T07 | ScalpingConsensusStrategy (12 에이전트 합의) | 1 | P1 | 7d |
| T08 | 백테스터에 워크포워드 + NXT 시뮬 + 슬리피지 모델 추가 | 1 | P1 | 5d |
| T09 | `Exchange`/`TradingSession` 모델 도입 + 세션 서비스 | 2 | P0 | 4d |
| T10 | `NxtGateway` 1차 구현 (시세 read-only) | 2 | P0 | 7d |
| T11 | `CompositeOrderBook` + 통합 호가창 UI | 2 | P0 | 6d |
| T12 | SOR v1 (가격/잔량 기반 라우팅) | 2 | P1 | 4d |
| T13 | 뉴스/공시 수집 파이프라인 (RSS + DART) | 3 | P0 | 5d |
| T14 | 형태소 분석 + 임베딩 인프라 (kiwi + ko-sbert) | 3 | P0 | 4d |
| T15 | 테마 분류기 v1 (TF-IDF + 임베딩 매칭) | 3 | P1 | 6d |
| T16 | 대장주 점수 알고리즘 + 가중치 그리드 서치 | 3 | P1 | 5d |
| T17 | 일정 캘린더 + 이벤트 → 종목 연동 | 3 | P1 | 6d |
| T18 | 프론트 테마 박스 / 캘린더 / 뉴스 티커 페이지 | 3 | P1 | 8d |
| T19 | `ExitPlan` 일급화 + 분할 익절/손절 엔진 | 4 | P0 | 5d |
| T20 | Kill Switch + Circuit Breaker | 4 | P0 | 4d |
| T21 | 매매 일지 + 감정 태그 + 자동 동기화 | 4 | P2 | 5d |
| T22 | JWT + MFA + RBAC + 감사 로그 무결성 | 5 | P0 | 7d |
| T23 | RLS 정책 + 컬럼 암호화 + Vault 도입 | 5 | P1 | 5d |
| T24 | AI 생성 코드 PR 게이트 (Semgrep + 보안 리뷰 템플릿) | 5 | P1 | 2d |

**P0 합계 약 49일, P1 약 78일, P2 약 5일** — 1–2명 풀타임 기준 단일 인원으로는 약 6개월, 2명 병렬 시 3.5–4개월 추정.

---

## 5. 리스크 및 관리 방안

### 5.1 실거래 리스크

- **Phase 1–4가 끝날 때까지 실거래 진입 금지** — 모의투자/페이퍼 트레이딩만 사용
- 실거래 진입 전 **3주 모의 + 1주 소액(자산 5% 이내) 라이브 검증** 필수
- 모든 자동매매는 **일일 손실 한도(자산 -3%) Kill Switch** 강제

### 5.2 데이터 리스크

- 키움 API 레이트리밋·장애로 시세가 끊길 때를 대비해 **다중 시세 소스** 운용 (키움 + NXT + 백업 벤더)
- OHLCV 캐시는 매일 무결성 검증(checksum) 후 사용, 실패 시 캐시 무시하고 직접 호출

### 5.3 규제/컴플라이언스 리스크

- 자동매매 시스템은 **본인 계좌 한정 사용 원칙** — 타인 계좌 자동매매는 투자일임업 등록 없이는 불가
- "투자 자문/추천" 표현을 UI/문서에서 배제 (단순 정보 제공임을 명시)
- 사용자 데이터 처리 시 **개인정보보호법** 준수, GDPR/CCPA 대비 처리 정책 사전 작성

### 5.4 기술 부채

- ai-trade의 `main.py` 2,412줄, `kiwoom_api.py` 1,569줄은 **Phase 1 종료 후** 모듈 분해 별도 티켓으로 처리(섣부른 리팩터는 휴면 버그 노출 위험)
- 백테스터 852줄도 Phase 1.4에서 확장 시 **테스트 커버리지 80% 이상**을 동시에 확보

### 5.5 AI 개발 보조의 함정

PRD가 강조한 대로 v0/Cursor가 생성한 코드는 다음 영역에서 **추가 검토 의무화**:
1. 자금 흐름이 닿는 모든 함수 (Decimal 사용 여부, 반올림 정책)
2. 인증/인가 로직 (체크 우회 가능성)
3. 외부 입력 처리 (SQL injection, command injection)
4. 동시성 코드 (race condition, asyncio cancel handling)

---

## 6. 즉시 착수 Top 10 (이번 주)

1. **T01** sub_repo를 main_repo의 `legacy_scalping/` 으로 흡수, CI 그린 확보
2. **T02** OHLCV/Order/Position 어댑터 작성 + 단위테스트
3. **T05** 골드존 전략을 신규 `Strategy` 인터페이스로 포팅 (가장 작은 단위로 검증)
4. **T09** `Exchange`/`TradingSession` enum 추가, 모델 마이그레이션
5. **T22 (시동)** JWT 로그인 골격 + RBAC 스캐폴딩 (실거래 보호 선결)
6. 회귀 백테스트 베이스라인 측정 후 `docs/04-report/baseline-2026-05.md` 발행
7. 운영 문서 `RUNBOOK.md` 작성: 장애 시 Kill Switch 발동 절차, 캐시 갱신, 키 회전
8. PRD 요구사항을 GitHub Issues로 분해해 라벨링(Phase 0~6, P0~P2, area)
9. AI 생성 코드 PR 가이드라인 + 보안 리뷰 체크리스트 `.github/PULL_REQUEST_TEMPLATE.md` 작성
10. 모의투자 환경 격리 — 실거래 키와 모의 키를 **별도 secret 저장소**로 분리

---

## 7. 마무리 메모

현재 시스템은 **이미 상당한 자산**을 보유하고 있다. F존 전략 408줄, 백테스터 852줄, 키움 게이트웨이 1,569줄, 멀티에이전트 12종, OHLCV 캐시 ~2,960종목 — 이는 0에서 시작한 프로젝트와는 차원이 다른 출발선이다.

다만 **PRD가 그리는 "테마 인텔리전스 허브"는 현재 시스템의 자연 연장선이 아니라 새로운 도메인의 추가**이다. 따라서 기존 코드는 "전략·실행" 축으로 정제하고, 그 옆에 "**시장 데이터(NXT 통합)**", "**테마 인텔리전스**", "**보안**" 세 축을 새로 세우는 형태가 가장 자연스럽다.

**3개월 내 단기 목표**: 모의투자 환경에서 4대 전략 + NXT 야간장 + 통합 호가가 안정적으로 운용되는 상태.
**6개월 내 중기 목표**: 테마 엔진 + 일정 연동 + 보안 강화까지 완료해 단일 사용자 베타 출시.
**12개월 내 장기 목표**: 멀티 사용자 SaaS, 모바일 앱, 외부 시장 확장.

성공의 열쇠는 두 가지다: (1) **Phase별 모의 검증을 절대 건너뛰지 않는 규율**, (2) **AI 보조 개발을 활용하되 자금 흐름·보안·동시성 코드는 반드시 사람이 게이트키퍼로 서는 운영 원칙**.

---

*문서 버전 1.0 / 작성일 2026-05-06*
