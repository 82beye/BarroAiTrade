# BarroAiTrade 고도화 단계별 실행 계획 (Phase 0~6 전체 상세)

## Context

- 출발점: `docs/01-plan/analysis/BarroAiTrade_고도화_계획.md` 의 6 Phase 마스터 플랜은 "무엇을 / 왜" 까지만 정의됨. 실제로는 (1) 어떤 BAR 티켓으로 분해할지, (2) 어느 PDCA 사이클에 어떤 산출물이 떨어지는지, (3) 모의투자/백테스트 게이트가 어디에 박히는지가 비어있음.
- 현 상태(2026-05-06 기준):
  - `backend/core/strategy/base.py` 의 `Strategy` 추상은 `analyze()` 단일 메서드뿐 — `exit_plan`/`position_size`/`health_check` 미정의.
  - `backend/models/market.py` 는 `MarketType={STOCK, CRYPTO}` 만 — `Exchange`/`TradingSession` 없음.
  - `backend/core/gateway/` 에 `KiwoomGateway` 만 존재 — `NxtGateway`/`CompositeOrderBook` 없음.
  - `backend/legacy_scalping/`, `backend/security/`, `backend/core/{theme,news,scheduler,journal,market_session}/` **모두 미생성**.
  - ai-trade(`/Users/beye/workspace/ai-trade`) 16K 줄 자산 분리 운영. main.py 2,412줄, scalping_team 9에이전트, OHLCV 캐시 144MB.
  - PDCA: BAR-17(대시보드)~BAR-39(Docker)까지 완료, BAR-38 로 모의투자 모드 적용. **`tests/` 디렉터리 부재** — 회귀 게이트가 비어 있음.
  - DB: SQLite (audit_repo 구현). Phase 3 전에 Postgres 마이그레이션이 필요할 가능성 높음.
- 목표 산출물: 6 Phase × 39 BAR 티켓 × 검증 게이트 의 단일 실행 가능 매트릭스. 다음 1주 안에 시작할 첫 스프린트가 명확해야 한다.
- 가정: 단일 인원 + AI 서브에이전트 보조, 실거래 진입 금지(모의만), 자금흐름·보안·동시성 PR 은 사람 게이트키퍼.

---

## 0. 운영 원칙 (모든 Phase 공통)

| 원칙 | 적용 |
|---|---|
| PDCA 1 사이클 = 1 BAR 티켓 | `/pdca plan BAR-XX` → `01-plan/BAR-XX.md` → `design` → `do` → `analyze` → `report` |
| 회귀 게이트 | 모든 Phase 종료 시 `pytest backend/tests/` + 4대 전략 백테스트 + 모의 1주 무사고 |
| 자금흐름/보안 PR 은 라벨 `area:money` 또는 `area:security` 부착, 자동 머지 금지 — 사람 게이트키퍼 |
| AI 생성 코드 PR 은 라벨 `ai-generated` + Semgrep/Bandit 통과 의무 (Phase 5.4 이후) |
| main 직접 push 금지 — `BAR-XX` 브랜치 → PR → 본인 셀프 리뷰 |
| 각 Phase 의 마지막 산출물은 `docs/04-report/PHASE-N-*-report.md` |
| 실거래 진입은 Phase 4 완료 + 1주 소액(자산 5%) 라이브 검증 통과 후에만 허용 |

---

## Phase 0 — 기반 정비 (Week 1–2, 5 티켓: BAR-40~44)

**목표**: ai-trade 16K줄을 깨뜨리지 않은 채 main_repo 안으로 흡수, 회귀 베이스라인 확보.

### 티켓

| BAR | Title | 핵심 변경/신규 파일 | DoD |
|---|---|---|---|
| BAR-40 | sub_repo 모노레포 흡수 | `backend/legacy_scalping/**` (ai-trade 미러) + `Makefile` `legacy-scalping` 타겟 | `python -m backend.legacy_scalping.main --dry-run` 무에러 / CI green |
| BAR-41 | 모델 호환 어댑터 | `backend/legacy_scalping/_adapter.py` (dict 시그널 ↔ `models/signal.py:EntrySignal`) | `tests/legacy_scalping/test_adapter.py` 8 케이스 통과 |
| BAR-42 | 통합 환경변수 스키마 | `backend/config/settings.py` (NXT/뉴스/테마 placeholder), `.env.example` | `Settings()` 인스턴스화 성공, 누락 키는 `Optional` |
| BAR-43 | 표준 로깅·메트릭 통일 | `backend/legacy_scalping` 에 `core/monitoring/logger` import, `legacy_*` Prometheus counter | Grafana 기존 dashboard 에서 `legacy_*` 메트릭 가시화 |
| BAR-44 | 회귀 베이스라인 측정 | `docs/04-report/PHASE-0-baseline-2026-05.md` (4대 전략 5년 백테스트 승률·MDD·샤프) | report 머지 = Phase 0 종료 게이트 |

### 의존관계

```
BAR-40 ─┬─ BAR-41 ─ BAR-43 ─┐
        └─ BAR-42 ──────────┴─ BAR-44 (Phase 0 종료)
```

### 종료 DoD

- `pytest backend/tests/legacy_scalping/` green
- 베이스라인 리포트 main 머지 후 Phase 1 진입 허가

### 위험

- ai-trade import 충돌 → 모듈명 충돌 시 `legacy_scalping.<sub>` namespace 강제
- ai-trade의 `main.py`(2,412줄)·`kiwoom_api.py`(1,569줄)는 **이번 Phase 에서 분해하지 않는다**

---

## Phase 1 — 전략 엔진 통합 + 4대 매매기법 (Week 3–6, 7 티켓: BAR-45~51)

**목표**: 표준 `Strategy v2` 인터페이스 + 4대 전략(F존/SF존/골드존/38스윙) + 멀티에이전트 합의 1개. `analyze()` 단일 메서드를 `exit_plan`/`position_size`/`health_check` 까지 일급화.

### 티켓

| BAR | Title | 핵심 변경/신규 파일 | DoD |
|---|---|---|---|
| BAR-45 | Strategy v2 추상 + AnalysisContext | `backend/core/strategy/base.py` 확장, `backend/models/strategy.py` (`AnalysisContext`, `ExitPlan`, `Account`) | 기존 4 전략이 v2 시그니처로 컴파일됨, `pytest tests/strategy/test_base.py` |
| BAR-46 | F존 v2 리팩터 | `backend/core/strategy/f_zone.py` (408→ExitPlan 분리) | 백테스트 결과 BAR-44 베이스라인 ±5% 이내 |
| BAR-47 | SF존 별도 클래스 분리 | `backend/core/strategy/sf_zone.py` (신규) | 백테스트 + F존 대비 강도 가중치 명세 문서 |
| BAR-48 | 골드존 전략 신규 포팅 | `backend/core/strategy/gold_zone.py` (BB+Fib 0.382~0.618+RSI 회복) | 5년 백테스트 승률 ≥ 50%, MDD ≤ 20% |
| BAR-49 | 38스윙 전략 신규 포팅 | `backend/core/strategy/swing_38.py` (Fib 0.382 되돌림 + 임펄스 탐지) | 동상 |
| BAR-50 | ScalpingConsensusStrategy | `backend/core/strategy/scalping_consensus.py` (12 에이전트 가중합, threshold 0.65) | `tests/strategy/test_scalping_consensus.py`, 단독 모의 1주 무사고 |
| BAR-51 | 백테스터 v2 확장 | `backend/core/backtester.py` (852→~1100줄): walkforward + NXT 야간 시뮬 + 슬리피지/수수료/세금 모델 | 5 전략 동시 백테스트 리포트 자동 생성 |

### 의존관계

```
BAR-45 ──┬─ BAR-46 ─┐
         ├─ BAR-47 ─┤
         ├─ BAR-48 ─┼─ BAR-51 ─ Phase 1 종료 게이트 (모의 1주)
         ├─ BAR-49 ─┤
         └─ BAR-50 ─┘
```

### 종료 DoD

- 5 전략 동일 인터페이스 운용
- `docs/04-report/PHASE-1-strategies-report.md` (5 전략 5년 백테스트 + 모의 1주 결과)
- 회귀: BAR-44 대비 F존/블루라인 승률·MDD 5% 이상 후퇴 시 머지 차단

### 위험

- ScalpingConsensusStrategy 가중치 학습 데이터 부족 → 초기 weights 균등(1/12), 운용 후 grid search (Phase 3.3 의 대장주 알고리즘과 학습 잡 통합 가능)

---

## Phase 2 — NXT 통합 + 통합 호가창 + 거래시간 인지 (Week 5–10, Phase 1 과 일부 병렬, 4 티켓: BAR-52~55)

**목표**: 08:00–20:00 통합 거래 환경. NXT API 미공개 위험을 감안해 **2.A(시세 read-only) → 2.B(주문 라우팅)** 로 단계화.

### 티켓

| BAR | Title | 핵심 변경/신규 파일 | DoD |
|---|---|---|---|
| BAR-52 | Exchange/TradingSession enum + MarketSessionService | `backend/models/market.py` 확장, `backend/core/market_session/service.py` (신규), 휴장일 캘린더 데이터 | 08:00–20:00 임의 시각에서 정확한 세션·가용 거래소 반환, `tests/market_session/` 24 케이스 |
| BAR-53 | NxtGateway 1차 (시세 read-only) | `backend/core/gateway/nxt.py` (신규) — 키움 OpenAPI NXT 채널 우선 / KOSCOM CHECK fallback | NXT 야간 시간대 ticker/orderbook 7일 무중단 수신 |
| BAR-54 | CompositeOrderBook + UI | `backend/core/gateway/composite_orderbook.py`, `frontend/components/orderbook-composite.tsx` | KRX/NXT 잔량 가격별 색상 구분, `venue_breakdown(price)` 100% 정확 |
| BAR-55 | SOR v1 (가격/잔량 라우팅) | `backend/core/execution/router.py` (신규, OrderExecutor 통합), 강제 거래소 모드 옵션 | 모의 주문 100건 라우팅 100% 정확 |

### 의존관계

```
BAR-52 ─┬─ BAR-53 ─ BAR-54 ─┐
        │                   ├─ Phase 2 종료 게이트
        └─────────── BAR-55 ─┘
```

### 종료 DoD

- `docs/04-report/PHASE-2-nxt-integration-report.md`
- 모의: NXT 야간 매수→다음날 KRX 정규장 매도 1주 무사고 시나리오 캡처

### 위험

- BAR-53 시작 전 **1일 스파이크**: 키움 OpenAPI 의 NXT 시세 제공 여부 확인. 미제공 시 NXT 직접 API / KOSCOM CHECK 벤더 비용 평가 → BAR-53 일정 +1~2주 재추정.
- BAR-54 UI: lightweight-charts 한계 시 자체 React 컴포넌트로 fallback.

---

## Phase 3 — 테마 인텔리전스 엔진 (Week 9–14, Phase 2 일부 병렬, 7 티켓: BAR-56~62)

**목표**: 실시간 테마 클러스터링 + 대장주 판별 + 일정 연동.

### 사전 결정 사항

- **DB 마이그레이션**: 현재 SQLite → 테마/뉴스/벡터 검색에 부적합. Phase 3 시작 시 Postgres + pgvector 로 전환 결정 필요. 본 계획은 BAR-56 으로 분리.
- **임베딩**: 1차 `kiwipiepy` + `ko-sbert`, 2차 `claude-haiku` zero-shot 백업 (비용 절감용).

### 티켓

| BAR | Title | 핵심 변경/신규 파일 | DoD |
|---|---|---|---|
| BAR-56 | DB 마이그레이션: SQLite → Postgres + pgvector | `docker-compose.yml`(postgres 서비스), `backend/db/` 마이그레이션 스크립트, `alembic/` 도입 | 기존 데이터 100% 이전, 회귀 테스트 green, audit_repo 동작 검증 |
| BAR-57 | 뉴스/공시 수집 파이프라인 | `backend/core/news/collector.py` (RSS + DART, `httpx`+`apscheduler` 1분 polling), Redis Streams | 24시간 운용 시 수집 누락 ≤ 1%, 중복 제거 |
| BAR-58 | 형태소·임베딩 인프라 | `backend/core/news/nlp.py` (`kiwipiepy`+`ko-sbert`), 임베딩 캐시 | 100건 입력 시 P95 latency ≤ 500ms |
| BAR-59 | 테마 분류기 v1 | `backend/core/theme/classifier.py` (TF-IDF + LR → 임베딩 코사인 → claude-haiku zero-shot), `themes/theme_keywords/theme_stocks` 테이블 | 운영자 라벨링 1주 검증 정확도 ≥ 85% |
| BAR-60 | 대장주 점수 알고리즘 + 가중치 그리드 서치 | `backend/core/theme/leader_picker.py`, 월 1회 재학습 cron | 백테스트 환경에서 weights 수렴, 상위 4종목 추출 |
| BAR-61 | 일정 캘린더 + 이벤트→종목 연동 | `backend/core/scheduler/calendar.py`, `market_events` 테이블, IR/인포맥스/FnGuide 수집기, 사용자 수동 등록 API | D-1/D-Day 발생 시 스캐너 힌트 주입 동작, 캘린더 API 9 엔드포인트 |
| BAR-62 | 프론트 테마 박스 + 캘린더 + 뉴스 티커 | `frontend/app/themes/page.tsx`, `frontend/app/calendar/page.tsx`, `frontend/components/news-ticker.tsx` | 테마 박스 실시간 갱신, 일정→종목→테마→호가창 1-click 네비 |

### 의존관계

```
BAR-56 ─ BAR-57 ─ BAR-58 ─ BAR-59 ─┬─ BAR-60 ─┐
                                   │          ├─ BAR-62 ─ Phase 3 종료
                                   └─ BAR-61 ─┘
```

### 종료 DoD

- `docs/04-report/PHASE-3-theme-intel-report.md`
- 1주 운영 후 분류 정확도 ≥ 85% 검증 데이터
- 일정 → 종목 → 테마 → 호가창 1-click 네비게이션 영상 캡처

### 위험

- BAR-56 마이그레이션 실패 시 Phase 3 전체 블로킹 → 사전에 `db/migrations` 시나리오 dry-run 1일 확보
- 분류기 정확도 미달 시 BAR-59 v2(LLM 비중 확대) 추가 — Phase 3 종료 게이트 +1주

---

## Phase 4 — 자동매매 운영 엔진 + 매매 일지 (Week 13–18, 4 티켓: BAR-63~66)

**목표**: 인간 의사결정 없이 안전 자동매매 + 결과의 학습 자산화.

### 티켓

| BAR | Title | 핵심 변경/신규 파일 | DoD |
|---|---|---|---|
| BAR-63 | ExitPlan 일급화 + 분할 익절/손절 엔진 정착 | `backend/core/execution/exit_engine.py` (신규), `models/strategy.py:ExitPlan` 확장 (`take_profits`, `stop_loss`, `time_exit`, `breakeven_trigger`) | 5 전략 모두 ExitPlan 사용, 백테스트에서 TP/SL 정확 발동 |
| BAR-64 | Kill Switch + Circuit Breaker | `backend/core/risk/kill_switch.py`, `backend/core/risk/circuit_breaker.py`, RiskEngine 통합 | 시뮬: 일일 -3% / 슬리피지 5분 3회 / 시세 단절 시나리오 100% 발동, 신규 진입 차단 |
| BAR-65 | 매매 일지 + 감정 태그 + 자동 동기화 | `backend/core/journal/`, `trade_notes` 테이블, `frontend/app/journal/page.tsx`, 차트/호가창 우클릭 메모 | 매매 종료 시 자동 노트 생성, 감정 태그 3종, 월말 자동 분석 리포트 |
| BAR-66 | 비중 관리 (RiskEngine 정책) | `backend/core/risk/risk_engine.py` 확장 (동시 보유 ≤ 3종목, 종목당 ≤ 30%, 동일 테마 합산 한도) | 시뮬: 한도 초과 신규 진입 100% 거부 |

### 의존관계

```
BAR-63 ─┬─ BAR-64 ─┐
        ├─ BAR-65 ─┼─ Phase 4 종료 게이트 (모의 3주 인간개입 0회)
        └─ BAR-66 ─┘
```

### 종료 DoD

- `docs/04-report/PHASE-4-autotrading-report.md`
- 모의 3주 연속 자동매매, 인간 개입 0회
- Kill Switch 시뮬 시나리오 100% 발동 영상 캡처
- **실거래 진입 권한 부여**(자산 5% 이내, 1주 라이브 검증 의무)

### 위험

- ExitPlan 누수(특정 전략에서 과거 if-else 잔존) → 회귀 백테스트에서 동일 시그널/다른 청산 결과 비교, 5% 이상 차이 시 PR 차단

---

## Phase 5 — 보안 강화 (Week 11–20 지속, 4 티켓: BAR-67~70)

**목표**: 핀테크 보안 점검 가이드(LIAPP/금보원) 수준 방어선.

> **시동 정책**: BAR-67 (인증 골격) 은 Phase 1 시작 직후 즉시 *시동*만 (실거래 보호 선결). 정식 머지·운영은 본 Phase 시점.

### 티켓

| BAR | Title | 핵심 변경/신규 파일 | DoD |
|---|---|---|---|
| BAR-67 | JWT 골격 + RBAC 스캐폴딩 (시동, Phase 1 직후 시작) | `backend/security/auth.py` (JWT + Refresh, httpOnly Secure 쿠키), 미들웨어, RBAC role enum | `/login` 동작, viewer/trader/admin 라우트 가드 |
| BAR-68 | MFA + 감사 로그 무결성 + 실거래 강제 | `backend/security/mfa.py` (TOTP), `backend/db/audit_repo.py` 확장 (30일 해시 체인), 실거래 모드 진입 OTP 강제 | OTP 미입력 시 실거래 진입 차단 100%, 무결성 검증 스크립트 통과 |
| BAR-69 | RLS + 컬럼 암호화 + Vault | Postgres RLS 정책, Fernet 컬럼 암호화 (키움 자격증명/OAuth 토큰), `.env` → AWS Secrets Manager / Vault 마이그레이션 | 다른 user_id 의 데이터 접근 0% 가능, 암호화 컬럼 plaintext 유출 0건 |
| BAR-70 | AI 생성 코드 PR 게이트 | `.github/PULL_REQUEST_TEMPLATE.md` (AI 코드 체크리스트), `.github/workflows/security-scan.yml` (Semgrep + Bandit), `ai-generated` 라벨 자동 부착 hook | OWASP Top 10 자동 스캔 통과, 모의 침투 테스트 P0/P1 0건 |

### 의존관계

```
(Phase 1 시작) ─ BAR-67 시동 ─────────────┐
                                          ├─ BAR-68 ─ BAR-69 ─ BAR-70 ─ Phase 5 종료
                       Phase 4 종료 ──────┘
```

### 종료 DoD

- `docs/04-report/PHASE-5-security-report.md`
- OWASP Top 10 자동 스캔 통과
- 모의 침투 테스트(외부 또는 내부 공격팀) 1회 수행, P0/P1 0건
- 모든 거래 호출이 감사 로그에 무결성 검증 가능 형태로 기록

### 위험

- Vault/Secrets Manager 도입 비용 → 1차 도입은 AWS Parameter Store / 1Password Connect 등 저비용 옵션으로 대체 가능
- RLS 도입 시 기존 쿼리 회귀 위험 → BAR-69 PR 에 모든 라우트 통합 테스트 의무

---

## Phase 6 — 운영 고도화 + 확장 (Week 19–26+, 8 티켓: BAR-71~78)

**목표**: 단일 사용자 도구 → 멀티 사용자 SaaS 형 플랫폼 진화.

### 티켓

| BAR | Title | 핵심 변경/신규 파일 | DoD |
|---|---|---|---|
| BAR-71 | 멀티 사용자 격리 + 사용량 메트릭 | 사용자별 전략 인스턴스 (`backend/core/orchestrator.py` 멀티텐드), 자산 격리(RLS+Composite key), `usage_metrics` 테이블 | 2 user 동시 운용 시 데이터 누수 0건, 사용량 대시보드 |
| BAR-72 | 성능 — Redis 캐시 + WS 채널 샤딩 + Postgres 읽기 복제 | `backend/core/cache/redis.py`, WebSocket 채널 user_id 샤드 라우팅, RDS 읽기 복제 | P95 latency 50% 감소, WS concurrent 1000 → 5000 |
| BAR-73 | OpenTelemetry 추적 + 알림 IaC | OpenTelemetry SDK 적용, Grafana alert rules → `monitoring/alerts.yaml` (코드화) | 분산 trace 가시화, 알림 룰 git diff 가능 |
| BAR-74 | 어드민 백오피스 | `frontend/app/admin/`, 사용자/전략/감사로그 관리 페이지, admin role 전용 | 모든 어드민 액션 감사 로그 기록, 운영자 1주 사용 피드백 반영 |
| BAR-75 | 모바일 앱 (React Native) | `mobile/` 신규 monorepo (Expo 또는 RN bare), RASP 적용 (LIAPP), 화면 캡처 방지, 디버그 빌드 분리 | iOS TestFlight + Android internal testing 베타 배포, 핵심 기능(테마/호가/주문) 동작 |
| BAR-76 | 해외주식 게이트웨이 (미국/홍콩) | `backend/core/gateway/{us_stock,hk_stock}.py` (예: 키움증권 영웅문 / IBKR 평가), `Exchange` enum 확장 | 미국 시간대 1주 페이퍼 트레이딩 무사고 |
| BAR-77 | 코인 거래소 추가 | `backend/core/gateway/upbit.py`, `bithumb.py` (기존 `crypto_breakout` 전략 활용) | 24h 운영 1주 무사고 |
| BAR-78 | 회귀 자동화 GitHub Action | `.github/workflows/regression.yml` — pytest + 백테스트 회귀 + 모의 dry-run 자동 실행 | 모든 PR 자동 회귀, 베이스라인 -5% 후퇴 시 자동 차단 |

### 의존관계

```
BAR-71 ─ BAR-72 ─ BAR-73 ─ BAR-74 ─┐
                                   ├─ Phase 6 1차 종료 (SaaS β)
BAR-78 ────────────────────────────┘

(병렬 트랙)
BAR-75 / BAR-76 / BAR-77 — Phase 6 2차 종료 (확장)
```

### 종료 DoD

- `docs/04-report/PHASE-6-platform-report.md`
- 멀티 사용자 β 출시 가능 상태 (사용자 ≤ 10명 한정)
- 모바일 앱 베타 배포
- 회귀 자동화 GitHub Action 모든 PR 자동 발동

### 위험

- BAR-75 RN 진출 비용 — 핵심 운영자가 본인 1명이라면 Phase 6 2차 트랙은 후순위. 1차 종료(BAR-71~74, 78)만으로 SaaS β 출시 후 사용자 피드백으로 모바일 우선순위 재결정.

---

## 즉시 착수 스프린트 1 (이번 주 + 다음 주, Phase 0)

> 목적: BAR-44 (Phase 0 종료 게이트) 까지 도달.

| 일자 | 작업 | 산출물 |
|---|---|---|
| D+0 (오늘) | `/pdca plan BAR-40` | `docs/01-plan/BAR-40-monorepo-absorption.md` |
| D+1 | BAR-40 do — `cp -r ai-trade backend/legacy_scalping`, `__init__.py`, import path 정리 | 신규 디렉터리 + Makefile |
| D+2 | BAR-40 do — `python -m backend.legacy_scalping.main --dry-run` 통과 | dry-run 성공 로그 |
| D+3 | BAR-41 plan + design — adapter 시그니처 결정 | design 문서 |
| D+4 | BAR-41 do + `tests/legacy_scalping/test_adapter.py` 8 케이스 | 테스트 green |
| D+5 | BAR-42 do — settings 확장 + `.env.example` | 환경변수 스키마 |
| D+6 | BAR-43 do — logger/metric 일원화 | Grafana 대시보드 캡처 |
| D+7 | 회귀: 4 전략 모의 환경 1일 dry-run | dry-run 로그 |
| D+8~10 | BAR-44 — 5년 백테스트 4 전략 베이스라인 | `docs/04-report/PHASE-0-baseline-2026-05.md` |
| D+11~12 | Phase 0 회고 + Phase 1 plan (`/pdca plan BAR-45`) | Phase 1 진입 |

---

## 핵심 변경/신규 파일 (Phase 0~6 종합)

### 확장 대상 (기존 파일)
- `backend/core/strategy/base.py` (BAR-45)
- `backend/core/strategy/f_zone.py` (BAR-46)
- `backend/core/backtester.py` (BAR-51)
- `backend/models/market.py` (BAR-52)
- `backend/core/execution/order_executor.py` (BAR-55, BAR-63)
- `backend/core/risk/risk_engine.py` (BAR-66)
- `backend/db/audit_repo.py` (BAR-68)
- `backend/core/orchestrator.py` (BAR-71)
- `docker-compose.yml` (BAR-56)

### 신규 디렉터리
- `backend/legacy_scalping/**` (BAR-40)
- `backend/core/market_session/` (BAR-52)
- `backend/core/news/` (BAR-57, 58)
- `backend/core/theme/` (BAR-59, 60)
- `backend/core/scheduler/` (BAR-61)
- `backend/core/journal/` (BAR-65)
- `backend/security/` (BAR-67~70)
- `backend/core/cache/` (BAR-72)
- `mobile/` (BAR-75)
- `tests/{strategy,legacy_scalping,market_session,risk,journal}/` (각 BAR 단위)
- `docs/04-report/PHASE-{0..6}-*-report.md`
- `alembic/` (BAR-56)
- `.github/workflows/{security-scan,regression}.yml` (BAR-70, 78)
- `frontend/app/{themes,calendar,journal,admin}/` (BAR-62, 65, 74)
- `frontend/components/{orderbook-composite,news-ticker}.tsx` (BAR-54, 62)

### 신규 단일 파일 (전략·게이트웨이)
- `backend/core/strategy/{sf_zone,gold_zone,swing_38,scalping_consensus}.py` (BAR-47~50)
- `backend/core/gateway/{nxt,composite_orderbook,upbit,bithumb,us_stock,hk_stock}.py` (BAR-53, 54, 76, 77)
- `backend/core/execution/{router,exit_engine}.py` (BAR-55, 63)
- `backend/core/risk/{kill_switch,circuit_breaker}.py` (BAR-64)

---

## 검증 전략

| 게이트 | 도구 | 발동 조건 |
|---|---|---|
| 단위 테스트 | `pytest backend/tests/` | 모든 PR. 신규 코드 라인 커버리지 ≥ 70% |
| 백테스트 회귀 | `python -m backend.core.backtester --regression` (BAR-51 도입 후) | Phase 종료 / 전략 수정 PR. 베이스라인 ±5% 이내 |
| 모의투자 N주 검증 | TRADING_MODE=simulation (BAR-38 환경) | Phase 1: 1주, Phase 4: 3주 |
| Kill Switch 시뮬 | `tests/risk/test_kill_switch.py` (BAR-64) | BAR-64 머지 시 + Phase 4 종료 |
| 보안 스캔 | Semgrep + Bandit (BAR-70) | `ai-generated` 라벨 PR / 매주 cron |
| 모의 침투 테스트 | 외부 또는 내부 공격팀 | Phase 5 종료 게이트 |
| 회귀 자동화 | `.github/workflows/regression.yml` (BAR-78) | 모든 PR 자동 |

**중간 산출물 검증**: `gap-detector` 에이전트로 plan↔구현 매치율 ≥ 90% 확인 후 `/pdca report` 진입.

---

## 리스크 및 트리거

| 리스크 | 트리거 | 대응 |
|---|---|---|
| NXT API 미공개 | BAR-53 1일 스파이크 실패 | KOSCOM CHECK 등 벤더 평가, Phase 2 +2주 |
| ai-trade import 충돌 | BAR-40 dry-run 실패 | namespace 강제 / 충돌 모듈 rename |
| 백테스트 베이스라인 후퇴 | -5% 이상 하락 | 해당 PR revert, design 재검토 |
| 자금흐름 AI 코드 회계 오류 | Decimal 미사용 발견 | `area:money` 라벨 PR 차단, BAR-70 가속 |
| Postgres 마이그 실패 | BAR-56 dry-run 실패 | 1주 추가 일정, 데이터 export → fresh import 시나리오 |
| 분류기 정확도 < 85% | Phase 3 종료 게이트 미달 | BAR-59 v2 (LLM zero-shot 비중 확대), Phase 3 +1주 |
| 단일 인원 burnout | 스프린트 후반 PR 적체 | parallel-dev-team 서브에이전트 활용. 단 자금흐름·보안 PR 은 사람 게이트 유지 |
| 실거래 사고 | 1주 라이브 검증 중 손실 한도 초과 | Kill Switch 자동 발동, 즉시 simulation 복귀, 사고 보고서 |

---

## PDCA / 도구 통합

- 각 BAR 티켓: `/pdca plan BAR-XX` → `design` → `do` → `analyze` → `report` 5 단계 강제
- Phase 종료: `/pdca report PHASE-N` 으로 `docs/04-report/PHASE-N-*.md` 자동 통합
- Obsidian Vault (`obsidian-wiki` skill) 에 BAR 티켓별 노트 누적, 누적 wikilink 로 의존관계 시각화
- Phase 6 BAR-78 까지 도달 후 회귀 자동화가 모든 PR 에서 자동 발동
- AI 서브에이전트 활용 가이드: `Explore` (코드 탐색), `Plan` (설계 검증), `bkit:gap-detector` (매치율), `bkit:code-analyzer` (품질), `parallel-dev-team` (다중 트랙)

---

## 26주 마일스톤 캘린더

| Week | Phase | 주요 산출물 |
|---|---|---|
| 1–2 | Phase 0 | BAR-40~44 / 베이스라인 리포트 |
| 3–6 | Phase 1 | BAR-45~51 / 5 전략 + 백테스터 v2 |
| 5–10 | Phase 2 (1과 병렬) | BAR-52~55 / NXT + 통합호가 + SOR |
| 9–14 | Phase 3 (2와 병렬) | BAR-56~62 / 테마 인텔리전스 |
| 11+ | Phase 5 시동 | BAR-67 (JWT/RBAC) 시동 |
| 13–18 | Phase 4 | BAR-63~66 / 자동매매 + 매매일지 + 실거래 진입 권한 |
| 11–20 | Phase 5 | BAR-68~70 / 보안 정식 |
| 19–26+ | Phase 6 | BAR-71~78 / 멀티 사용자 + 모바일 + 확장 |

---

## 다음 액션

1. 본 plan 승인 시 `/pdca plan BAR-40` 실행으로 즉시 스프린트 1 시작.
2. 본 plan 자체를 `docs/01-plan/MASTER-EXECUTION-PLAN-v1.md` 로 복사해 git 추적 (현재는 worktree 의 `.claude/plans/` 에만 존재).
3. GitHub Issues 에 BAR-40~78 라벨링 (Phase 0~6, P0/P1/P2, area:strategy/security/data/ui).
4. `RUNBOOK.md` 골격 작성 (장애 시 Kill Switch 발동 절차 / 캐시 갱신 / 키 회전) — Phase 4 BAR-64 와 함께 채워나감.
