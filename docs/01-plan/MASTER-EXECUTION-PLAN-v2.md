# BarroAiTrade 마스터 실행 계획 v2

> **Status**: 🟢 Active (v1 supersede)
> **Date**: 2026-05-06
> **Author**: beye (CTO-lead)
> **Supersedes**: [[MASTER-EXECUTION-PLAN-v1]]
> **Related**: [[features/bar-44-baseline.plan|BAR-44 Plan]] | [[../04-report/PHASE-0-baseline-2026-05.md|Phase 0 Baseline]]

---

## 0. v2 발행 사유

마스터 플랜 v1 (2026-05-06 발행) 은 BAR-40 plan 작성 시점부터 BAR-44 (Phase 0 종료 게이트) 까지의 *실측 PDCA 5 사이클* 진행 중에 다음과 같은 변경 사항이 누적되었다:

1. **BAR-51 번호 충돌 발견**: main 브랜치의 기존 `BAR-51` (서비스 복구 모니터링, commit `bb85bcf`) 와 v1 의 `BAR-51` (백테스터 v2 확장) 충돌
2. **명세 갱신 9건** (zero-modification 정의·LOC 한도·schema extra·fixture 패턴 등)
3. **신규 후순위 ticket** (BAR-44b 정식 5년 OHLCV 측정)

본 v2 는 위 변경사항을 통합하고 v1 을 *supersede* 한다. v1 파일은 *역사 추적용* 으로 보존된다.

---

## 1. v2 변경 매트릭스 (v1 → v2)

| # | 항목 | v1 | v2 | 출처 |
|---|---|---|---|---|
| 1 | BAR-51 (Phase 1) | 백테스터 v2 확장 | 🔁 **BAR-79** 로 재할당 (Phase 6 마지막 묶음) | main 의 기존 BAR-51 충돌 |
| 2 | zero-modification 정의 (BAR-40 §3.3) | "코드 무수정" | "외부 동작 보존, 진입점 격리만" | BAR-41/43 retro |
| 3 | `_adapter.py` LOC (BAR-41 Plan §4.3) | ≤ 200 | ≤ 250 | BAR-41 분석 M1 |
| 4 | `LegacySignalSchema.extra` (BAR-41 Design §3.1) | `forbid` | `ignore` | BAR-41 분석 A4 |
| 5 | metrics fixture (BAR-43 Design §3.3) | `importlib.reload` | Singleton (reload 제거) | BAR-43 do 실측 |
| 6 | fallback 검증 정책 | env 종속 skip | `PROM_FORCE_NOOP=1` 권고 | BAR-43 분석 M2 |
| 7 | NFR 성능 측정 위치 (BAR-42/43) | "선택" 표기 | BAR-44 베이스라인에 통합 명시 | BAR-44 plan 인계 |
| 8 | BAR-44b (선택, 정식 5년) | (부재) | 신규 후순위 ticket — Postgres 마이그·OHLCV 통합 후 | BAR-44 옵션 2 정의 |
| 9 | 운영 원칙 §0 docs PR 묶기 | (없음) | "단순 docs PR (plan/design/analyze/report) 은 BAR-78 회귀 자동화 시점에 묶기 검토" | BAR-40 §7.3 lessons |

---

## 2. v2 BAR 매트릭스 (v1 + 변경 + 신규)

### Phase 0 (BAR-40~44, 5 티켓) — ✅ 완료

| BAR | 상태 | Match | PR |
|-----|------|-------|-----|
| BAR-40 sub_repo 모노레포 흡수 | ✅ | 95% | #2~#6 |
| BAR-41 모델 호환 어댑터 | ✅ | 96% | #8~#12 |
| BAR-42 통합 환경변수 스키마 | ✅ | 98% | #13~#17 |
| BAR-43 표준 로깅·메트릭 통일 | ✅ | 97% | #18~#22 |
| BAR-44 회귀 베이스라인 (옵션 2) | 🚧 (본 PR) | (예상 95+%) | #23~ |

### Phase 1 (BAR-45~50, **6 티켓** — 기존 v1 의 BAR-51 제외)

| BAR | 제목 | 상태 |
|-----|------|------|
| BAR-45 | Strategy v2 추상 + AnalysisContext | 🔓 Phase 0 완료 후 진입 |
| BAR-46 | F존 v2 리팩터 | ⏳ |
| BAR-47 | SF존 별도 클래스 분리 | ⏳ |
| BAR-48 | 골드존 전략 신규 포팅 | ⏳ |
| BAR-49 | 38스윙 전략 신규 포팅 | ⏳ |
| BAR-50 | ScalpingConsensusStrategy | ⏳ |
| ~~BAR-51~~ | ~~백테스터 v2 확장~~ | 🔁 **BAR-79** 로 재할당 |

### Phase 2~5 — v1 매트릭스 그대로 유지

`MASTER-EXECUTION-PLAN-v1.md` 의 Phase 2 (BAR-52~55), Phase 3 (BAR-56~62), Phase 4 (BAR-63~66), Phase 5 (BAR-67~70) 정의 변경 없음.

### Phase 6 (BAR-71~78 + **BAR-79** 재할당)

| BAR | 제목 | 상태 |
|-----|------|------|
| BAR-71~78 | v1 정의 그대로 (멀티 사용자 / Redis / OpenTelemetry / 어드민 / 모바일 / 해외주식 / 코인 / 회귀 자동화) | ⏳ |
| **BAR-79** | **백테스터 v2 확장** (workforward + NXT 야간 시뮬 + 슬리피지/수수료/세금 모델) — v1 의 BAR-51 재할당 | ⏳ |

### 후순위 (Phase 미배정, 본 v2 신설)

| ID | 제목 | 시점 | 사유 |
|-----|------|------|------|
| BAR-44b | 정식 5년 OHLCV 백테스트 베이스라인 | Phase 3 BAR-56 (Postgres 마이그) 후 | BAR-44 옵션 2 의 후속 정식 측정 |

---

## 3. 운영 원칙 v2 (변경분만)

v1 §0 운영 원칙은 그대로 유지. 다음 한 항목 추가:

| 원칙 (신규) | 적용 |
|---|---|
| **단순 docs PR 묶기 검토** | plan/design/analyze/report 의 docs only PR 은 BAR-78 회귀 자동화 시점에 *묶기 옵션* 검토. 단 자금흐름·보안 PR (`area:money`/`area:security`) 은 묶기 금지 |

---

## 4. 명세 갱신 적용 (Phase 0 → Phase 1+ 인계)

### 4.1 zero-modification 정의 (변경 #2)

기존 BAR-40 §3.3 *"legacy 코드 무수정"* 은 다음과 같이 재해석:

> **"외부 동작 의미 변화 없음. 진입점 격리·dry-run 가드·import 경로 정정 같은 *방어적 보완* 은 본 정의에 부합한다."**

근거:
- BAR-40 §3.3 옵션 A (`main.py` 5줄 dry-run 가드 패치)
- BAR-41 4 `__init__.py` re-export 비활성화 (namespace 격리)
- BAR-43 `setup_logging()` 자동 통합 (legacy 표준 호출 활용)

3 BAR 모두 V3 (dry-run 회귀) 통과로 *동작 의미 변화 없음* 확증.

### 4.2 LOC 한도 (변경 #3)

`_adapter.py` 의 *분리 함수* (`_normalize_score`/`_coerce_to_dict`/`_derive_price`/`_build_metadata`/`_format_reason`) 는 *가독성 향상* 방향의 분리이므로 ≤ 250 LOC 로 상향. 향후 어댑터·도메인 모듈도 *읽기 쉬움 우선* 정책.

### 4.3 schema `extra="ignore"` (변경 #4)

`LegacySignalSchema` 의 `extra` 정책은 `ignore` 채택. 사유: legacy dict 의 *잡다 필드 흡수* 가 *호출자 보호* 정책과 일관 (silent default 보다 silent ignore 가 안전 강화). 안전망: `TestSchemaIsolated::test_schema_extra_ignored` 명시 검증.

### 4.4 metrics fixture Singleton (변경 #5)

prometheus_client 의 `REGISTRY` 가 *모듈 import 시 1회 등록* 모델이라 `importlib.reload` 가 *중복 등록* 을 트리거. 후속 BAR 의 메트릭 fixture 는 *Singleton 패턴* 으로 일관.

### 4.5 `PROM_FORCE_NOOP=1` (변경 #6)

prometheus_client 가 *항상 설치된* 환경에서 fallback no-op 검증 불가. 후속 maintenance 또는 BAR-78 회귀 자동화 시 `PROM_FORCE_NOOP=1` 환경변수 도입해 강제 fallback 분기 검증.

### 4.6 NFR 성능 측정 통합 (변경 #7)

BAR-42 `Settings()` ≤ 50ms, BAR-43 `/metrics` ≤ 100ms 등 *"선택"* 표기 NFR 들은 본 BAR-44 베이스라인 또는 BAR-78 회귀 자동화에 통합 측정.

---

## 5. v1 보존 정책

`docs/01-plan/MASTER-EXECUTION-PLAN-v1.md` 는 다음 사유로 *삭제하지 않고 보존*:

- 역사 추적 (PR #1 머지 시점의 정합 계약)
- BAR-40~44 의 plan/design/analysis/report wikilink 가 v1 을 참조
- v2 변경 매트릭스 (§1) 의 비교 기준
- BAR-44b 진입 시 *원본 BAR-51 명세* 참조용

`docs/01-plan/_index.md` 의 v1 표기는 ✅ "supersede" 로 갱신, v2 는 🟢 active 로.

---

## 6. 다음 액션 (Phase 1 진입)

1. BAR-44 report + Phase 0 종합 회고 머지
2. **BAR-45 plan 진입** — Strategy v2 추상 + AnalysisContext (`backend/core/strategy/base.py` 확장)
3. v2 의 §4 명세 갱신 항목들 (LOC 250, extra=ignore 등) 을 BAR-45 design 단계에서 일관 적용
4. BAR-67 시동 (JWT/RBAC) 은 Phase 1 시작 직후 *시동만* — v1 정책 그대로

---

## 7. Version History

| Version | Date | Changes | Author |
|---|---|---|---|
| 1.0 | 2026-05-06 | (v1) 초기 마스터 플랜, BAR-40~78 39 티켓 | beye |
| 2.0 | 2026-05-06 | (본 v2) BAR-51 → BAR-79 재할당 + 명세 갱신 9건 + BAR-44b 신설 | beye (CTO-lead) |
