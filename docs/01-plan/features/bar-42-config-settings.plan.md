---
tags: [plan, feature/bar-42, status/in_progress, phase/0, area/repo]
template: plan
version: 1.0
---

# BAR-42 통합 환경변수 스키마 Plan

> **Project**: BarroAiTrade
> **Feature**: BAR-42
> **Phase**: 0 (기반 정비) — 세 번째 티켓
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v1#Phase 0 — 기반 정비 (Week 1–2, 5 티켓: BAR-40~44)]]
> **Author**: beye (CTO-lead)
> **Date**: 2026-05-06
> **Status**: In Progress
> **Gate**: BAR-43 (Logger·Prometheus 통일) 의 선결, BAR-44 (베이스라인) 까지의 의존

---

## 1. Overview

### 1.1 Purpose

`backend/config/settings.py` 의 `Settings` 클래스에 **Phase 1~5 에서 사용할 환경변수 placeholder** 를 *지금* 미리 추가한다. 누락 키는 모두 `Optional` 처리해 *현 시점에서는 동작 변화 없이* 후속 BAR (NXT 게이트웨이·뉴스 수집·테마 분류·JWT 인증·Postgres 마이그레이션) 가 환경변수만 주입하면 즉시 동작 가능하게 한다.

### 1.2 Background

- 현재 `Settings` 는 키움/DB/Telegram/로깅/서버 5 그룹만 정의 (45 LOC). NXT, 뉴스(DART/RSS), 테마(임베딩 모델), Postgres, Redis, JWT/MFA 모두 부재.
- 마스터 플랜 v1 의 BAR-42: **"NXT/뉴스/테마 키 placeholder 추가, `Settings()` 인스턴스화 성공, 누락 키는 `Optional`"**.
- Phase 5 시동 BAR-67 (JWT/RBAC) 가 Phase 1 시작 직후 *시동* — 본 BAR-42 에 JWT 키 placeholder 도 함께 두면 BAR-67 진입이 부드럽다.
- BAR-41 §M3 / BAR-44 인계: Postgres + pgvector 마이그레이션은 Phase 3 BAR-56 — 그 전에도 환경변수만은 미리.

### 1.3 Related Documents

- 마스터 플랜: [[../MASTER-EXECUTION-PLAN-v1]]
- BAR-40 (선결, 완료): [[../../04-report/bar-40-monorepo-absorption.report]]
- BAR-41 (병행, 완료): [[../../04-report/bar-41-model-adapter.report]]
- 후속 BAR-43 Logger 통일: 본 settings 의 `log_*` 필드를 활용
- 후속 BAR-53 NxtGateway: 본 settings 의 `nxt_*` 필드 사용
- 후속 BAR-67 JWT: 본 settings 의 `jwt_*` 필드 사용

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/config/settings.py` 의 `Settings` 클래스 확장 (Phase 1~5 placeholder)
- [ ] 환경변수 그룹 6종 신규: NXT / 뉴스(DART+RSS) / 테마(임베딩+벡터DB) / Postgres / Redis / JWT/MFA
- [ ] 모든 신규 필드 `Optional[...] = None` (BAR-42 의 *동작 변화 없음* 원칙)
- [ ] `.env.example` 갱신 (그룹별 주석 + placeholder 값)
- [ ] `tests/config/test_settings.py` 신규 — 인스턴스화 + 누락 키 동작 단위 테스트 (5+ 케이스)
- [ ] `Makefile` `test-config` 타겟 (또는 `test-legacy` 와 통합한 `test` 타겟)
- [ ] BAR-40 dry-run 회귀 무영향 — `make legacy-scalping` 여전히 통과
- [ ] BAR-41 회귀 무영향 — `make test-legacy` 여전히 통과

### 2.2 Out of Scope

- ❌ 환경변수의 *실제 값* 주입 (각 후속 BAR 의 책임)
- ❌ NXT/뉴스/테마 모듈 자체 구현 (BAR-53/57/59)
- ❌ JWT/RBAC 미들웨어 구현 (BAR-67)
- ❌ Postgres 마이그레이션 (BAR-56)
- ❌ Vault/Secrets Manager 도입 (BAR-69) — `.env` 기반 유지

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | NXT placeholder: `nxt_base_url`, `nxt_app_key`, `nxt_app_secret`, `nxt_enabled: bool = False` | High | Pending |
| FR-02 | 뉴스 placeholder: `dart_api_key`, `rss_feed_urls: list[str] = []`, `news_polling_interval_sec: int = 60` | High | Pending |
| FR-03 | 테마 placeholder: `theme_embedding_model: str = "ko-sbert"`, `theme_vector_db_url`, `theme_classifier_threshold: float = 0.65` | Medium | Pending |
| FR-04 | Postgres placeholder: `postgres_url`, `postgres_pool_size: int = 5`, `pgvector_enabled: bool = False` | Medium | Pending |
| FR-05 | Redis placeholder: `redis_url`, `redis_streams_enabled: bool = False` | Medium | Pending |
| FR-06 | JWT/MFA placeholder: `jwt_secret`, `jwt_access_ttl_sec: int = 3600`, `jwt_refresh_ttl_sec: int = 604800`, `mfa_issuer: str = "BarroAiTrade"` | High | Pending |
| FR-07 | 모든 신규 필드 `Optional[...] = None` 또는 `default` 값 — `Settings()` 호출 시 *환경변수 미주입 상태* 인스턴스화 성공 | High | Pending |

### 3.2 Non-Functional Requirements

| Category | 기준 | 측정 |
|---|---|---|
| 호환성 | 기존 5 그룹 필드 동작 무영향 (snapshot test) | pytest |
| 성능 | `Settings()` 인스턴스화 ≤ 50ms | benchmark (선택) |
| 보안 | `jwt_secret`, `kiwoom_app_secret`, `dart_api_key` 등 민감 정보 `repr()` 시 마스킹 | `__str__` / `pydantic.SecretStr` |
| 테스트 커버리지 | `settings.py` ≥ 80% | pytest --cov |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] `settings.py` 확장 완료 (≤ 200 LOC, 현재 75 LOC)
- [ ] `Settings()` 호출 시 환경변수 미주입 상태에서 *무에러 인스턴스화*
- [ ] `tests/config/test_settings.py` 5+ 케이스 통과
- [ ] BAR-40 / BAR-41 회귀 무영향
- [ ] 라인 커버리지 ≥ 80%
- [ ] PR 셀프 리뷰 + 머지

### 4.2 5+ 테스트 케이스 시나리오

| # | 케이스 |
|---|--------|
| C1 | 환경변수 미주입 → `Settings()` 무에러 (모든 신규 필드 default 또는 None) |
| C2 | `KIWOOM_APP_KEY="abc"` 환경변수 주입 → `settings.kiwoom_app_key == "abc"` (기존 동작 유지) |
| C3 | `NXT_ENABLED=true` 주입 → `settings.nxt_enabled is True` (신규 필드) |
| C4 | `RSS_FEED_URLS='["https://a.com/rss","https://b.com/rss"]'` 주입 → list 정확 파싱 |
| C5 | `JWT_SECRET="..."` 시 `repr(settings)` 출력에 secret 노출 없음 (마스킹) |
| C6 | (보강) `Settings(_env_file=None)` 호출 시 .env 파일 없이도 동작 |

### 4.3 Quality Criteria

- [ ] settings.py ≤ 200 LOC
- [ ] 그룹별 주석 (`# === NXT ===` 등 6 그룹 + 기존 5 그룹)
- [ ] Pydantic v2 `SecretStr` 적용 (민감 정보 5건 — kiwoom_app_secret, telegram_bot_token, dart_api_key, jwt_secret, postgres_url)
- [ ] `.env.example` 그룹별 주석 + 사용 시점 BAR 번호 표기

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| `SecretStr` 도입으로 기존 코드(`settings.kiwoom_app_secret` 사용처) 회귀 | High | Medium | sed grep 으로 기존 사용처 색출 후 `.get_secret_value()` 호출 일괄 변경 또는 컬럼별 적용 보류 |
| 신규 필드 default 값이 후속 BAR 의 의도와 어긋남 | Medium | Low | placeholder 만 두고 *값* 은 후속 BAR 가 결정. 본 티켓에서는 *형태(타입)* 만 확정 |
| `.env.example` 과 실제 `.env` 의 키 이름 불일치 | Low | Medium | 본 티켓에서 `.env.example` 와 `Settings` 필드명 1:1 검증 테스트 케이스 추가 |
| pydantic-settings 의 `list[str]` 환경변수 파싱 복잡도 | Medium | Medium | `RSS_FEED_URLS='["a","b"]'` JSON 문자열 형식 명시. 또는 `comma-separated` decoder 추가 |
| Postgres URL 노출 (Phase 3 마이그 후) | High | Low | `SecretStr` 적용 + `.env` git ignore 강제 |

---

## 6. Architecture Considerations

### 6.1 Project Level
- **Enterprise**

### 6.2 그룹화 전략

```python
# === Trading 기본 (기존, 변경 없음) ===
trading_mode, trading_market, scan_interval_sec

# === Kiwoom API (기존) ===
kiwoom_base_url, kiwoom_app_key, kiwoom_app_secret*, kiwoom_account_no, kiwoom_mock

# === NXT (신규, BAR-53 사용) ===
nxt_enabled: bool = False
nxt_base_url: Optional[str] = None
nxt_app_key: Optional[str] = None
nxt_app_secret*: Optional[SecretStr] = None

# === DB (기존 + 신규) ===
db_path: str = "data/barro_trade.db"          # SQLite (현재)
postgres_url*: Optional[SecretStr] = None      # 신규, BAR-56 마이그
postgres_pool_size: int = 5
pgvector_enabled: bool = False

# === Redis (신규, BAR-57 Streams) ===
redis_url: Optional[str] = None
redis_streams_enabled: bool = False

# === 뉴스/공시 (신규, BAR-57) ===
dart_api_key*: Optional[SecretStr] = None
rss_feed_urls: list[str] = Field(default_factory=list)
news_polling_interval_sec: int = 60

# === 테마 (신규, BAR-58/59) ===
theme_embedding_model: str = "ko-sbert"
theme_vector_db_url: Optional[str] = None
theme_classifier_threshold: float = 0.65

# === 보안 (신규, BAR-67/68) ===
jwt_secret*: Optional[SecretStr] = None
jwt_access_ttl_sec: int = 3600
jwt_refresh_ttl_sec: int = 604800
mfa_issuer: str = "BarroAiTrade"

# === Telegram (기존) ===
telegram_bot_token*: str = ""

# === 로깅 (기존) ===
log_json, log_level

# === 서버 (기존) ===
host, port

* = SecretStr 적용
```

### 6.3 SecretStr 적용 결정

| 옵션 | 장점 | 단점 | 채택 |
|---|---|---|:---:|
| A. 신규 5 secret 만 SecretStr | 회귀 위험 0 | 기존 secrets (`kiwoom_app_secret`, `telegram_bot_token`) 비대칭 | — |
| B. 기존+신규 모든 secret SecretStr | 일관성, 보안 강화 | 기존 사용처 `.get_secret_value()` 변경 필요 (회귀 위험) | — |
| C. **신규 5 secret SecretStr 적용 + 기존 비대칭 정당화 주석 + 후속 BAR-67 에서 일괄 정리 약속** | 회귀 위험 최소 + 보안 강화 + 인계 명확 | 코드 일관성 약간 손상 | ⭐ |

→ **C 채택**. 후속 BAR-67 에서 기존 secret 도 일괄 SecretStr 화 (인계 사항).

---

## 7. Convention Prerequisites

### 7.1 기존 컨벤션

- ✅ Pydantic v2 (`BaseSettings`, `SettingsConfigDict`) — `pydantic-settings>=2.2.0` 의존성 존재
- ✅ `Optional` 사용 일반적 (한국어 docstring + type hint)
- ✅ `tests/legacy_scalping/` 디렉터리 존재 (BAR-41 시동) — `tests/config/` 도 같은 패턴

### 7.2 본 티켓에서 정의할 컨벤션

| 항목 | 결정 |
|---|---|
| 환경변수 그룹 주석 | `# === <그룹명> (BAR-XX 사용) ===` 형식 통일 |
| Optional vs default | 값 모름이면 `Optional[...] = None`, 알면 default 값 |
| SecretStr 정책 | 본 티켓 §6.3 옵션 C |
| 테스트 위치 | `backend/tests/config/test_settings.py` |
| `.env.example` 형식 | `# BAR-XX (사용 시점)\nKEY=value` |

---

## 8. 작업 단계 (Implementation Outline)

> 본 plan 승인 후 design 문서에서 상세화. 여기는 개략적 단계.

1. **D1 사전 점검**: 기존 `Settings` 클래스 사용처 grep (`from backend.config.settings import settings`) — secret 필드 변경 위험도 측정
2. **D2 `Settings` 확장**: 6 신규 그룹 추가 (§6.2 명세)
3. **D3 `SecretStr` 적용**: 신규 5 secret 만 (§6.3 옵션 C)
4. **D4 `.env.example` 갱신**: 그룹별 주석 + placeholder
5. **D5 `tests/config/__init__.py`, `tests/config/test_settings.py` 신규 (5+ 케이스)**
6. **D6 `Makefile` 갱신**: `test-config` 또는 `test` 통합
7. **D7 V1~V6 검증** (design 에서 정의)
8. **D8 회귀**: BAR-40 dry-run + BAR-41 pytest 무영향 확인
9. **D9 PR 생성** (라벨: `area:repo` `phase:0` `priority:p0`)

---

## 9. Next Steps

1. [ ] Design 문서 작성 (`/pdca design BAR-42`) — `docs/02-design/features/bar-42-config-settings.design.md`
2. [ ] Do 단계 진입 + 5+ 테스트 케이스
3. [ ] Analyze (gap-detector + 회귀)
4. [ ] Report

---

## 10. 비고

- `area:repo` `phase:0` `priority:p0` 라벨 권장.
- `area:security` 미부착 — 본 티켓은 *placeholder 만* 추가하므로 보안 영향 없음. JWT/MFA 실제 구현은 BAR-67 시점에 `area:security` 라벨.
- 자금흐름 영향 0건 (값 변경 없음).

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 plan — Phase 0 세 번째 티켓, 6 신규 그룹 placeholder 정의, SecretStr 옵션 C | beye (CTO-lead) |
