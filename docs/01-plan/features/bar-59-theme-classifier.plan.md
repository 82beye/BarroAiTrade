# BAR-59 — 테마 분류기 v1 (TF-IDF + 임베딩 코사인 + zero-shot 3-tier, Phase 3 분류 게이트)

**Phase**: 3 (테마 인텔리전스) — **네 번째 BAR**
**선행**: BAR-56a (Postgres + pgvector 인프라) ✅ — 287 passed
        BAR-57a (뉴스/공시 수집 + Redis Streams `news_items`) ✅ — 299 passed
        BAR-58a (임베딩 인프라 + `embeddings` 테이블 + `search_similar`) ✅ — 327 passed
**후속 블로킹**: BAR-60 (대장주 점수 — theme tags + 뉴스 노출도 결합), BAR-61 (일정 캘린더 — 테마별 이벤트 묶음)

---

## 0. 분리 정책 — BAR-59a (worktree 정식 do) / BAR-59b (운영 정식)

worktree 환경 제약 (운영자 1주 라벨링 데이터 부재 / claude-haiku API 키 부재 / 실 정확도 ≥ 85% 측정 불가) 을 고려하여 BAR-54a/54b · BAR-56a/56b · BAR-57a/57b · BAR-58a/58b 와 동일한 a/b 분리 패턴을 적용한다. 본 plan 의 5단 PDCA 는 **BAR-59a 트랙** 만 다룬다. BAR-59b 는 별도 plan 없이 **운영 환경 진입 시 BAR-59a 산출물 위에서 라벨 수집 + 모델 재학습 + zero-shot 어댑터 활성화 사이클**로 수행한다.

| BAR | 트랙 | 산출물 | 본 사이클 |
|-----|------|--------|:---:|
| **BAR-59a** | worktree (Classifier Protocol + 4 어댑터 + 3-tier orchestrator + alembic 0004 + theme_repo + Settings + mock 단위 테스트) | `ThemeClassifier` Protocol, `TfidfLogRegClassifier` (sklearn TF-IDF + LR, fixture 기반 학습), `EmbeddingCosineClassifier` (BAR-58 `search_similar` 활용), `ClaudeHaikuClassifier` (NotImplementedError stub), `ThreeTierClassifier` (1차 high → 2차 mid → 3차 fallback), `ThemeRepository` (text() + dialect 분기), Settings 4 신규, alembic 0004 (themes / theme_keywords / theme_stocks), 25+ mock 단위 테스트 | ✅ 정식 do |
| **BAR-59b** | 운영 (운영자 라벨링 1주 ≥ 1000건 + 실 TF-IDF + LR 학습 + claude-haiku API 키 + 정확도 ≥ 85% 검증 + 월 1회 재학습 cron) | 라벨링 가이드라인 + 운영자 도구 / fixtures 가 아닌 실 라벨 데이터셋 / sklearn pipeline 직렬화 + 모델 아티팩트 (S3) / Anthropic API 키 secrets / haiku zero-shot 어댑터 활성화 / 정확도 측정 dashboard / 월 1회 재학습 cron + 모델 drift 알람 | deferred — 운영 진입 시 |

**왜 분리하는가**
- TF-IDF + LR 의 진짜 학습은 운영자 1주 라벨링 결과 (≥ 1000건, 테마별 ≥ 30건) 위에서만 의미. worktree 의 fixture (테마 5종 × 5건 = 25건) 는 알고리즘 동작 검증용이며, 정확도 ≥ 85% 게이트는 BAR-59b 책임.
- claude-haiku zero-shot 은 외부 Anthropic API 키 + 비용/latency 측정이 필요. worktree 는 인터페이스 stub (NotImplementedError) 만 두고 본 어댑터는 BAR-59b.
- 후속 BAR-60 가 요구하는 것은 "NewsItem.tags 자동 부여 + 종목별 테마 매핑 (theme_stocks)" 뿐. `TfidfLogRegClassifier` (fixture) + `EmbeddingCosineClassifier` (BAR-58 fake embedder) 만으로도 BAR-60 의 점수 알고리즘 / 임계치 / 단위 테스트는 진행 가능. 실 모델 정확도 검증은 BAR-59b 머지 후 BAR-60 의 통합 검증 시점에 합류.
- kiwipiepy 형태소 토크나이저는 BAR-58 plan 의 §4 에서 "BAR-58c 또는 BAR-59" 로 이관됨 — 본 BAR-59a 의 do 스코프에 포함하되 단위 테스트는 mock 으로 시그니처만 검증 (실 사전 다운로드 X). 실 토큰 품질 검증은 BAR-59b.

---

## 1. 목적 (Why)

Phase 3 의 핵심 분류 — **NewsItem 입력을 한국어 테마 tag set 으로 매핑하고, 종목별 테마 노출도 (theme_stocks) 를 누적한다**.

- BAR-60 (대장주 점수): 종목별 theme tags 빈도 + 뉴스 노출도 + 가격 모멘텀 결합 → 대장주 score. 본 BAR 의 `theme_stocks(theme_id, ticker, weight, last_updated)` 가 BAR-60 의 핵심 입력.
- BAR-61 (일정 캘린더): 테마별 실적/배당/주총 이벤트 묶음 — 본 BAR 의 `themes(theme_id, name, description)` 마스터 + DART 공시의 테마 매핑.
- BAR-62 (포트폴리오 권고): 테마 다변화 점수 — 본 BAR 의 `theme_stocks` 를 바탕으로 동일 테마 과집중 회피.

따라서 본 BAR 의 출력 (`themes` 마스터 + `theme_keywords` 키워드 매핑 + `theme_stocks` 종목 노출도 + `NewsItem.tags` 자동 부여) 이 Phase 3 후속 BAR 3건의 **공통 분류 면** 이다. 본 BAR 가 후행 3건의 테마 ID 체계 / 분류 인터페이스 / 신뢰도 임계치를 결정한다.

**왜 3-tier (TF-IDF / 임베딩 코사인 / claude-haiku) 인가**
- 1차 TF-IDF + LR: 한국어 키워드 기반 강한 시그널 (예: "2차전지", "AI 반도체"). 학습된 LR 의 `predict_proba` ≥ 0.7 → high confidence, 즉시 채택 (외부 API 호출 0건, 비용 0).
- 2차 임베딩 코사인: BAR-58 의 `search_similar` 로 사전 정의 테마 prototype 벡터와 cosine distance ≤ 임계 → mid confidence. TF-IDF 가 놓치는 의미적 유사 (예: 신조어 "초전도체") 흡수.
- 3차 claude-haiku zero-shot: 1·2차 모두 임계 미달 → fallback. 비용 발생하나 호출 빈도 ≤ 10% 로 억제 (1·2차 임계가 흡수).
- 비용 절감 가설: 1차 70% / 2차 20% / 3차 10% 분포 → 평균 분류당 API 비용 ≈ 1차+2차 (0원) × 0.9 + haiku × 0.1. 가설 검증은 BAR-59b 의 운영 측정.

**왜 한국어 토큰화에 kiwipiepy 인가**
- konlpy (Mecab) 는 OS 별 설치 복잡 + JVM 동반. kiwipiepy 는 pure Python wheel + 사전 내장 → CI / 컨테이너 호환성 우월.
- TF-IDF 의 입력 단위는 형태소 (어절이 아닌) — 한국어 조사 / 어미 분리가 필수. kiwipiepy 의 `analyze()` → 명사 (NNG/NNP) + 동사어간 (VV) + 형용사어간 (VA) 만 추출.
- 본 BAR-59a 의 do 단계는 kiwipiepy lazy import + mock 으로 토크나이저 인터페이스만 검증 (실 사전 다운로드는 첫 import 시 자동, 단위 테스트에서는 patch). 실 토큰 품질 검증은 BAR-59b.

---

## 2. 기능 요구사항 (FR)

### 2-1. ThemeClassifier Protocol

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-01 | `ThemeClassifier` (Protocol, async) — `async def classify(self, news_item: NewsItem) -> ClassificationResult`. ClassificationResult: `tags: list[str]`, `confidence: float ∈ [0, 1]`, `backend: str` (어떤 어댑터가 분류했는지), `latency_ms: float`. | `backend/core/themes/base.py` |
| FR-02 | `ClassificationResult` (Pydantic v2 frozen) — tags 는 정렬·중복 제거 후 immutable. confidence 는 어댑터별 의미 (TF-IDF: max predict_proba / Cosine: 1 - min cosine_distance / Haiku: 자체 score). | 동상 |
| FR-03 | `ThemeClassifier.backend_id: str` (property) — 운영 추적용 식별자 (예: `"tfidf_lr_v1"`, `"embedding_cosine_v1"`, `"claude_haiku_zero_shot_v1"`, `"three_tier_v1"`). | 동상 |

### 2-2. 구현체 4종

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-04 | `TfidfLogRegClassifier` — `sklearn.feature_extraction.text.TfidfVectorizer` (tokenizer=kiwipiepy 형태소, max_features=5000, ngram_range=(1, 2)) + `sklearn.linear_model.LogisticRegression` (multi_class='ovr', random_state=42 결정성 고정). 학습은 `fit(samples: list[(text, tag)])` — fixture 기반 (worktree) 또는 운영자 라벨 (BAR-59b). 추론: `classify(news_item)` → `predict_proba` → top-k tags + max prob. confidence = max prob. | `backend/core/themes/tfidf_classifier.py` |
| FR-05 | `EmbeddingCosineClassifier` — BAR-58 의 `EmbeddingRepository.search_similar(query, model, k=10)` 활용. theme prototype 벡터는 init 시 사전 정의 — 테마별 키워드 ("2차전지 LFP NCM 양극재") 를 BAR-58 embedder 로 인코딩 → 캐시. classify: news_item.body → embedder.encode → 모든 prototype 과 cosine distance 계산 → distance ≤ NEWS_THEME_THRESHOLD_COSINE 인 테마 채택. confidence = 1 - min_distance. | `backend/core/themes/embedding_classifier.py` |
| FR-06 | `ClaudeHaikuClassifier` — Anthropic API zero-shot 분류. 본 BAR-59a 에서는 `__init__` 에서 `NotImplementedError("BAR-59b: API key required")` raise. 인터페이스 (`async def classify(news_item) -> ClassificationResult`) 만 둠. BAR-59b 에서 `anthropic.AsyncClient` + system prompt + structured output 활성화. | `backend/core/themes/haiku_classifier.py` |
| FR-07 | `ThreeTierClassifier` — 1차 (`TfidfLogRegClassifier`) → confidence ≥ NEWS_THEME_THRESHOLD_TFIDF (기본 0.7) 면 즉시 반환. 미달 → 2차 (`EmbeddingCosineClassifier`) → confidence ≥ NEWS_THEME_THRESHOLD_COSINE 변환값 (1 - 0.5 = 0.5 기본) 면 반환. 미달 → 3차 (`ClaudeHaikuClassifier`) fallback. backend 필드에 어느 tier 가 채택되었는지 기록. | `backend/core/themes/three_tier.py` |
| FR-08 | `ClassifierFactory.from_settings(settings, embedder, repo) -> ThemeClassifier` — `NEWS_THEME_BACKEND` (`tfidf|cosine|haiku|three_tier`) 분기. `three_tier` 가 worktree 기본. | `backend/core/themes/factory.py` |

### 2-3. ThemeRepository (audit_repo / news_repo / embedding_repo 와 동일 패턴)

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-09 | `themes` 테이블 — Alembic revision 0004. 컬럼: `id BIGSERIAL`, `name TEXT NOT NULL`, `description TEXT`, `created_at TIMESTAMPTZ DEFAULT NOW()`. UNIQUE(name). | `alembic/versions/0004_themes.py` |
| FR-10 | `theme_keywords` 테이블 — 컬럼: `id BIGSERIAL`, `theme_id BIGINT NOT NULL` (FK → themes.id ON DELETE CASCADE), `keyword TEXT NOT NULL`, `weight FLOAT DEFAULT 1.0`. UNIQUE(theme_id, keyword). 인덱스: (theme_id). | 동상 |
| FR-11 | `theme_stocks` 테이블 — 컬럼: `id BIGSERIAL`, `theme_id BIGINT NOT NULL` (FK → themes.id ON DELETE CASCADE), `ticker TEXT NOT NULL`, `weight FLOAT DEFAULT 1.0`, `last_updated TIMESTAMPTZ DEFAULT NOW()`. UNIQUE(theme_id, ticker). 인덱스: (ticker), (theme_id, last_updated DESC). | 동상 |
| FR-12 | `ThemeRepository` — `async def upsert_theme(name, description) -> int`, `async def add_keyword(theme_id, keyword, weight) -> None`, `async def add_stock(theme_id, ticker, weight) -> None`, `async def find_themes_by_names(names) -> list[Theme]`, `async def find_stocks_by_theme(theme_id) -> list[ThemeStock]`. SQLAlchemy `text()` + named param + dialect 분기 (Postgres ON CONFLICT / SQLite INSERT OR REPLACE). | `backend/db/repositories/theme_repo.py` |
| FR-13 | UNIQUE(theme_id, keyword) / UNIQUE(theme_id, ticker) 충돌 시 `ON CONFLICT DO UPDATE SET weight = EXCLUDED.weight, last_updated = NOW()` (Postgres) / `INSERT OR REPLACE` (SQLite) — idempotent upsert. | 동상 |

### 2-4. Settings 신규 4종

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-14 | `NEWS_THEME_BACKEND: Literal["tfidf", "cosine", "haiku", "three_tier"] = "three_tier"` (worktree / 운영 공통 기본) | `backend/config/settings.py` |
| FR-15 | `NEWS_THEME_THRESHOLD_TFIDF: float = 0.7` (1차 LR predict_proba 임계 — 이상이면 즉시 채택) | 동상 |
| FR-16 | `NEWS_THEME_THRESHOLD_COSINE: float = 0.5` (2차 cosine distance 임계 — 이하이면 채택, 즉 confidence ≥ 0.5) | 동상 |
| FR-17 | `NEWS_THEME_LABELS_PATH: str = "backend/data/themes/labels.json"` (fixture 라벨 파일 경로 — 운영은 BAR-59b 의 라벨 DB 로 교체) | 동상 |

### 2-5. 의존성

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-18 | `pyproject.toml` 또는 `requirements.txt` 에 `scikit-learn>=1.5` (lazy import — TfidfLogRegClassifier 의 fit 호출 시점). | `pyproject.toml` |
| FR-19 | `kiwipiepy>=0.17` (BAR-58 plan 에서 이미 선언, 본 BAR 에서 첫 실 사용. lazy import — TfidfVectorizer 의 tokenizer 콜백에서 호출. 단위 테스트는 mock). | 동상 |
| FR-20 | `anthropic>=0.40` (선택 의존, BAR-59b 활성화 — 본 BAR 의 ClaudeHaikuClassifier stub 은 import X). `extras_require={"themes-haiku": ["anthropic"]}`. | 동상 |

### 2-6. fixture 데이터

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-21 | `backend/data/themes/labels.json` — 테마 5종 × 5건 = 25건 fixture. 테마 예시: "2차전지", "AI 반도체", "방산", "원자력", "바이오 신약". 각 건은 `{"text": "...", "tags": ["..."]}`. | 신규 |
| FR-22 | `backend/data/themes/prototypes.json` — 테마별 prototype 키워드 텍스트 (Cosine 어댑터의 init 시 embedder.encode 입력). 예: `{"2차전지": "2차전지 LFP NCM 양극재 음극재 분리막 전해질"}`. | 신규 |

---

## 3. 비기능 요구사항 (NFR)

| ID | 요구 | 측정 |
|----|------|------|
| NFR-01 | `TfidfLogRegClassifier` 결정성 — `random_state=42` 고정, 동일 fit 입력 → 동일 model. predict_proba 의 top-k 가 동일. | 단위 테스트 5회 fit 반복, np.array_equal |
| NFR-02 | `EmbeddingCosineClassifier` prototype 캐시 — init 시 1회 encode, classify 호출 시 추가 encode = 1회 (news_item 본인). 호출 횟수 = 1 + classify_count. | 단위 테스트, mock embedder spy |
| NFR-03 | `ThreeTierClassifier` 의 tier 분기 — TF-IDF confidence ≥ 0.7 시 2·3차 호출 X (mock spy assert_not_called). 0.7 미만 + Cosine ≥ 0.5 시 3차 호출 X. 둘 다 미달 시 3차 호출. | 단위 테스트 3 케이스 |
| NFR-04 | 모든 Classifier 의 출력 ClassificationResult — tags 는 정렬·중복 제거. confidence ∈ [0, 1] (clip). backend_id 가 ClassificationResult.backend 에 일치. | 단위 테스트 |
| NFR-05 | 회귀 327 passed 유지 (BAR-58a 누적) — BAR-59a do 머지 후 **회귀 ≥ 352 passed (327 + 25 신규)** | `pytest backend/tests/` exit 0 |
| NFR-06 | coverage ≥ 70% (`backend/core/themes/` + `backend/db/repositories/theme_repo.py`) | `pytest --cov=backend/core/themes --cov=backend/db/repositories/theme_repo` |
| NFR-07 | alembic 0004 의 up/down 양방향 PASS (Postgres / SQLite 양쪽) | 단위 테스트 |
| NFR-08 (BAR-59b) | 운영자 라벨링 1주 (≥ 1000건, 테마별 ≥ 30건) → 정확도 ≥ 85% (test split 20%) | 운영 환경, dashboard |
| NFR-09 (BAR-59b) | 3-tier 비용 분포 — 1차 채택 ≥ 70% / 2차 ≥ 20% / 3차 (haiku) ≤ 10%. 월별 측정. | 운영 환경, prometheus |

---

## 4. 비고려 (Out of Scope)

| 영역 | 이관 대상 | 사유 |
|------|----------|------|
| 운영자 라벨링 1주 (≥ 1000건) 수집 | **BAR-59b** | worktree 환경에서는 라벨링 불가. fixture 25건 (테마 5종 × 5건) 으로 알고리즘만 검증. |
| claude-haiku 실 API 호출 (zero-shot 분류) | **BAR-59b** | Anthropic API 키 + 비용 측정. 본 BAR 는 NotImplementedError stub 만. |
| 정확도 ≥ 85% 측정 게이트 | **BAR-59b** | 실 라벨 데이터셋 위에서만 의미. worktree 의 fixture 25건 위에서는 overfitting 위험. |
| 월 1회 재학습 cron + 모델 drift 알람 | **BAR-59b** | 운영 환경의 sklearn pipeline 직렬화 + S3 업로드 + cron. |
| kiwipiepy 형태소 토크나이저의 실 사전 다운로드 + 토큰 품질 검증 | BAR-59b 의 운영 사이클 (또는 BAR-58c 의 형태소 통합 BAR) | worktree 는 lazy import + mock 으로 시그니처만 검증. 실 토큰 품질은 운영 라벨 데이터 위에서 검증. |
| 대장주 점수 (theme tags 결합) | **BAR-60** | 본 BAR 는 theme_stocks 테이블 + 분류 결과 적재까지. 점수 알고리즘은 BAR-60. |
| 일정 캘린더 (테마별 이벤트 묶음) | **BAR-61** | 본 BAR 는 themes 마스터 + DART 공시의 테마 매핑까지 X (BAR-61 책임). |
| 검색·랭킹 REST API (테마별 최신 뉴스) | **BAR-60 또는 BAR-72** | 본 BAR 는 repo 메서드까지. HTTP endpoint 는 후속 BAR. |
| 테마 마스터의 운영자 관리 UI | BAR-72 (Phase 6 운영) | worktree 는 fixture json 으로 테마 5종 시드. 운영자 CRUD UI 는 운영 환경. |
| 다중 라벨 임계 학습 (multi-label calibration) | BAR-59c (성능 최적화 후속 BAR) | 1차 정책: top-k (k=3) + threshold 단순 cut. calibration 은 운영 라벨 충분 후. |
| 테마 클러스터링 (비지도 — 신규 테마 발견) | 추후 BAR | 본 BAR 는 사전 정의 테마 목록 (5종 fixture / 운영 ~50종) 위에서만 분류. |
| 실 prometheus metric 노출 | BAR-72 (Phase 6 운영) | worktree: in-process counter 만. |

---

## 5. DoD

### 5-1. BAR-59a (worktree 정식 do — 본 사이클)

- [ ] `backend/core/themes/__init__.py`
- [ ] `backend/core/themes/base.py` — `ThemeClassifier` Protocol + `ClassificationResult` (Pydantic v2 frozen) + property 명세
- [ ] `backend/core/themes/tfidf_classifier.py` — `TfidfLogRegClassifier` (sklearn lazy import, kiwipiepy tokenizer, random_state=42, fit/classify)
- [ ] `backend/core/themes/embedding_classifier.py` — `EmbeddingCosineClassifier` (BAR-58 embedder + repo 활용, prototype 캐시)
- [ ] `backend/core/themes/haiku_classifier.py` — `ClaudeHaikuClassifier` (NotImplementedError stub, 인터페이스 명세)
- [ ] `backend/core/themes/three_tier.py` — `ThreeTierClassifier` (1→2→3 tier flow, backend 필드 추적)
- [ ] `backend/core/themes/factory.py` — `ClassifierFactory.from_settings()` (4-way 분기)
- [ ] `backend/db/repositories/theme_repo.py` — `ThemeRepository` (upsert_theme / add_keyword / add_stock / find_themes_by_names / find_stocks_by_theme, dialect 분기)
- [ ] `alembic/versions/0004_themes.py` — themes / theme_keywords / theme_stocks 3 테이블 + UNIQUE + FK CASCADE + 인덱스. up/down 왕복 PASS
- [ ] `backend/config/settings.py` — 4 신규 설정 (`NEWS_THEME_BACKEND`, `NEWS_THEME_THRESHOLD_TFIDF`, `NEWS_THEME_THRESHOLD_COSINE`, `NEWS_THEME_LABELS_PATH`)
- [ ] `backend/data/themes/labels.json` — 테마 5종 × 5건 = 25건 fixture
- [ ] `backend/data/themes/prototypes.json` — 테마 5종 prototype 키워드 텍스트
- [ ] `pyproject.toml` — `scikit-learn>=1.5` 의존성 추가, `anthropic` 은 extras_require="themes-haiku"
- [ ] **25+ 단위 테스트 (mock kiwipiepy + mock embedder)** — `backend/tests/themes/`
  - ThemeClassifier Protocol contract — backend_id property + ClassificationResult shape (4 어댑터 × 1 = 4)
  - TfidfLogRegClassifier — 결정성 (random_state=42, 동일 입력 → 동일 model) / fit→classify round-trip / 빈 입력 안전 / kiwipiepy mock 시그니처 (4)
  - EmbeddingCosineClassifier — prototype 캐시 (init 1회 + classify 1회) / threshold 분기 / 빈 prototype 안전 / mock embedder spy (4)
  - ClaudeHaikuClassifier — NotImplementedError raise / 인터페이스 명세 (2)
  - ThreeTierClassifier — TF-IDF ≥ 0.7 시 2·3차 skip / TF-IDF 미달 + Cosine ≥ 0.5 시 3차 skip / 둘 다 미달 시 3차 호출 + NotImplementedError 흡수 (3)
  - ClassifierFactory — 4-way 분기 (4)
  - ThemeRepository — upsert_theme / add_keyword / add_stock / UNIQUE 충돌 → upsert / find round-trip (4)
- [ ] `Makefile` `test-themes` 타겟
- [ ] **회귀 ≥ 352 passed** (327 누적 + 25 신규)
- [ ] **coverage ≥ 70%** (`backend/core/themes/` + `theme_repo.py`)
- [ ] gap-detector 매치율 ≥ 90%

### 5-2. BAR-59b (운영 정식 — deferred)

- [ ] 운영자 라벨링 도구 (간단 CLI 또는 web form) + 라벨링 가이드라인 문서
- [ ] 운영자 1주 라벨링 ≥ 1000건 (테마별 ≥ 30건, 테마 ~50종)
- [ ] 실 TF-IDF + LR 학습 → test split 20% 정확도 ≥ 85%
- [ ] sklearn pipeline 직렬화 (`joblib.dump`) + S3 모델 아티팩트 저장 + 모델 ID 버저닝 (`tfidf_lr_v1_2026Q2`)
- [ ] Anthropic API 키 (`.env.production`) + `anthropic` 패키지 설치 + `ClaudeHaikuClassifier` 본 구현 활성화
- [ ] 정확도 측정 dashboard (테마별 precision/recall/F1)
- [ ] 3-tier 비용 분포 측정 — 1차 채택 ≥ 70% / 2차 ≥ 20% / 3차 ≤ 10%. 월별 dashboard
- [ ] 월 1회 재학습 cron (운영자 라벨 누적 + 재학습 + S3 업로드 + 모델 hot-swap)
- [ ] 모델 drift 알람 — 1주 단위 정확도 측정 + 5%p 하락 시 page

---

## 6. 알고리즘 의사코드

```python
# three_tier.py (요지)
class ThreeTierClassifier:
    backend_id = "three_tier_v1"

    def __init__(self, tfidf, cosine, haiku, settings):
        self.tfidf, self.cosine, self.haiku = tfidf, cosine, haiku
        self.t1 = settings.NEWS_THEME_THRESHOLD_TFIDF  # 0.7
        self.t2 = settings.NEWS_THEME_THRESHOLD_COSINE  # 0.5

    async def classify(self, news_item) -> ClassificationResult:
        # 1차: TF-IDF + LR
        r1 = await self.tfidf.classify(news_item)
        if r1.confidence >= self.t1:
            return r1.model_copy(update={"backend": "three_tier_v1:tier1_tfidf"})

        # 2차: 임베딩 코사인
        r2 = await self.cosine.classify(news_item)
        if r2.confidence >= self.t2:
            return r2.model_copy(update={"backend": "three_tier_v1:tier2_cosine"})

        # 3차: claude-haiku zero-shot (BAR-59b 활성)
        try:
            r3 = await self.haiku.classify(news_item)
            return r3.model_copy(update={"backend": "three_tier_v1:tier3_haiku"})
        except NotImplementedError:
            # BAR-59a 단계: tier3 미가용 → 1·2차 중 max confidence 반환 (best-effort)
            best = r1 if r1.confidence >= r2.confidence else r2
            return best.model_copy(
                update={"backend": "three_tier_v1:fallback_no_tier3", "confidence": max(best.confidence, 0.0)}
            )


# embedding_classifier.py (요지)
class EmbeddingCosineClassifier:
    backend_id = "embedding_cosine_v1"

    def __init__(self, embedder, prototypes: dict[str, str], settings):
        self.embedder = embedder
        self.theme_names = list(prototypes.keys())
        self.proto_texts = list(prototypes.values())
        self.threshold = settings.NEWS_THEME_THRESHOLD_COSINE
        self._proto_vectors: list[np.ndarray] | None = None  # lazy init

    async def _ensure_prototypes(self):
        if self._proto_vectors is None:
            self._proto_vectors = await self.embedder.encode(self.proto_texts)

    async def classify(self, news_item) -> ClassificationResult:
        await self._ensure_prototypes()
        [news_vec] = await self.embedder.encode([news_item.body])
        # cosine distance = 1 - cosine_similarity (vectors are L2-normalized)
        sims = [float(np.dot(news_vec, pv)) for pv in self._proto_vectors]
        dists = [1.0 - s for s in sims]
        # threshold: distance ≤ threshold ↔ similarity ≥ (1 - threshold)
        hits = [(name, 1.0 - d) for name, d in zip(self.theme_names, dists) if d <= self.threshold]
        hits.sort(key=lambda x: -x[1])  # confidence desc
        tags = sorted({name for name, _ in hits})
        confidence = hits[0][1] if hits else 0.0
        return ClassificationResult(tags=tags, confidence=confidence, backend=self.backend_id, latency_ms=0.0)


# tfidf_classifier.py (요지)
class TfidfLogRegClassifier:
    backend_id = "tfidf_lr_v1"

    def __init__(self, settings):
        self.settings = settings
        self._pipeline = None  # lazy: TfidfVectorizer + LogisticRegression
        self._classes_: list[str] | None = None

    def fit(self, samples: list[tuple[str, str]]):
        from sklearn.pipeline import Pipeline
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression

        texts, tags = zip(*samples)
        self._pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(tokenizer=self._kiwi_tokenize, max_features=5000, ngram_range=(1, 2))),
            ("lr", LogisticRegression(multi_class="ovr", random_state=42, max_iter=1000)),
        ])
        self._pipeline.fit(texts, tags)
        self._classes_ = list(self._pipeline.named_steps["lr"].classes_)

    @staticmethod
    def _kiwi_tokenize(text: str) -> list[str]:
        from kiwipiepy import Kiwi
        kiwi = Kiwi()  # lazy singleton in real impl
        return [tok.form for tok in kiwi.analyze(text)[0][0] if tok.tag.startswith(("NN", "VV", "VA"))]

    async def classify(self, news_item) -> ClassificationResult:
        if self._pipeline is None:
            return ClassificationResult(tags=[], confidence=0.0, backend=self.backend_id, latency_ms=0.0)
        text = f"{news_item.title} {news_item.body}"
        probs = self._pipeline.predict_proba([text])[0]
        top_idx = int(np.argmax(probs))
        return ClassificationResult(
            tags=[self._classes_[top_idx]],
            confidence=float(probs[top_idx]),
            backend=self.backend_id,
            latency_ms=0.0,
        )
```

---

## 7. 위험 / 완화

| 위험 | 트리거 | 완화 | 일정 영향 |
|------|--------|------|----------|
| 라벨 분포 불균형 (테마별 라벨 수 편차) | LR 의 minority class precision 급락 | `class_weight="balanced"` + 운영자 가이드라인 (테마별 ≥ 30건). 본 BAR-59a 의 fixture 는 테마별 5건 균등. 실 분포는 BAR-59b 검증. | BAR-59b 만 영향 |
| 차원 mismatch (Cosine 어댑터의 prototype 차원 ≠ news 임베딩 차원) | embedder 교체 시 prototype 캐시 stale | `_ensure_prototypes` 시점에 `assert vec.shape[0] == self.embedder.dim` + embedder.model_id 변경 감지 시 캐시 invalidate. 단위 테스트 fixture 1건. | +0 일 |
| 한국어 토큰화 품질 (kiwipiepy 미세조정 사전 부재) | "전고체배터리" 같은 신조어를 "전고체" + "배터리" 로 과분리 | 사용자 사전 추가 (`kiwi.add_user_word("전고체배터리", "NNP")`) — 본 BAR-59a 는 인터페이스만, 실 사전 등록은 BAR-59b. 단기 보완: `ngram_range=(1, 2)` 로 bigram 흡수. | BAR-59b 만 영향 |
| TF-IDF 임계 0.7 가 fixture 25건 위에서 너무 보수적/관대 | tier1 채택률이 0% 또는 100% | 임계는 settings 로 외부 주입 (`NEWS_THEME_THRESHOLD_TFIDF`) — 운영 측정 후 BAR-59b 에서 튜닝. fixture 위에서는 알고리즘 동작만 검증. | +0 일 |
| ClaudeHaikuClassifier 미구현 (NotImplementedError) 가 ThreeTierClassifier 의 tier3 호출에서 unhandled exception 으로 누수 | 통합 시 BAR-59a 테스트가 실패 | ThreeTierClassifier 가 NotImplementedError 를 명시적으로 catch + best-effort fallback (1·2차 max confidence 반환). 단위 테스트 1건으로 fallback 경로 검증. | +0 일 |
| sklearn 의 LR multi_class='ovr' deprecated 경고 (sklearn ≥ 1.5) | future deprecation | sklearn ≥ 1.7 에서 `multinomial` default 전환 예정. 본 BAR-59a 는 명시적으로 `ovr` 지정 (binary→OVR 결정성). 1.7 도달 시 BAR-59c 에서 재평가. | +0 일 |
| 운영자 라벨링 1주 일정 지연 (BAR-59b) | 운영자 가용성 부족 | BAR-59b 는 운영 사이클이며 본 BAR-59a 의 머지/배포는 차단하지 않음. 라벨 부재 시 worktree fixture 로 임시 운영 (정확도 낮음 알람). | BAR-59b 만 영향 |
| alembic 0004 의 3 테이블 + FK CASCADE 가 SQLite 에서 외래키 비활성 기본값과 충돌 | 단위 테스트에서 CASCADE 미작동 | SQLite 연결 시 `PRAGMA foreign_keys=ON` 주입 (BAR-56a 에서 이미 처리됨, 재확인). up/down 왕복 단위 테스트. | +0.5 일 |
| **트리거: 1주 추가 일정** | 위 위험 중 2개 이상 동시 발생 | BAR-59a 만 1주 연장 → BAR-60 시작 1주 지연 보고 | +5 일 |

---

## 8. 다음 단계 — design 단계 council 위임

design 단계는 **5인 council** 패턴으로 위임한다 (Enterprise level, Phase 3 의 분류 BAR — 4 어댑터 추상 / 3-tier orchestration / 한국어 토큰화 / DB 3 테이블 / 운영 게이트 분리 등 다각 시각 필요).

| 역할 | 시각 | 주요 산출물 |
|------|------|-----------|
| **enterprise-expert (architect)** | 전체 책임 분할 — Classifier Protocol / 4 어댑터 / ThreeTier orchestrator / Repo / Factory 의 모듈 경계 + DI 컨테이너 | 모듈 다이어그램 + 의존성 흐름 + 4 어댑터의 SRP 검증 |
| **bkend-expert** | Classifier Protocol 시그니처 / TF-IDF + LR pipeline 구성 / kiwipiepy lazy import / Repo dialect 처리 / ThreeTier 의 NotImplementedError fallback | `backend/core/themes/` 패키지 구조 + 테스트 fixture 명세 + sklearn pipeline 직렬화 정책 |
| **qa-strategist** | 25+ 테스트 시나리오 매트릭스 (결정성 / 빈 입력 / threshold 분기 / NotImplementedError 흡수 / UNIQUE 충돌 / dialect 분기 / mock embedder spy / mock kiwipiepy) — fixture json 25건 설계 | 시나리오 표 + fixture 명세 + coverage gate (70%) |
| **security-architect** | Anthropic API 키 secrets 처리 (BAR-59b) · NewsItem.body 의 PII (인명·주민번호) 가 외부 zero-shot 으로 노출되는 위협 · sklearn 모델 아티팩트의 무결성 (joblib pickle 공격) | 위협 모델 1쪽 + 모델 아티팩트 서명/검증 절차 + body PII 마스킹 정책 |
| **frontend-architect** | 운영자 라벨링 도구의 UX (BAR-59b) · 테마 마스터 관리 UI (BAR-72) · 분류 결과 신뢰도 표시 (대시보드 BAR-17) | 라벨링 UX wireframe 1쪽 + 신뢰도 표시 가이드 |

design 산출물: `docs/02-design/features/bar-59-theme-classifier.design.md`

PDCA 5단:
1. `/pdca design BAR-59` — council 5인 종합 → Classifier Protocol + 4 어댑터 + ThreeTier flow + alembic 0004 + 시나리오 매트릭스 확정
2. `/pdca do BAR-59` — 25+ 단위 테스트 + alembic 0004 + 회귀 ≥ 352 passed + coverage ≥ 70%
3. `/pdca analyze BAR-59` — gap-detector
4. `/pdca iterate BAR-59` (필요 시)
5. `/pdca report BAR-59` — BAR-59a 완료 보고 (BAR-59b 항목 deferred 명시)

본 BAR-59a 완료 → **BAR-60 (대장주 점수) 진입 가능** (`theme_stocks` 테이블 + ThreeTierClassifier 결과 위에서 score 알고리즘 검증, 실 정확도 ≥ 85% 합류는 BAR-59b 머지 후).

---

## 요약 (200단어)

BAR-59 는 Phase 3 의 네 번째 BAR 이자 BAR-60/61/62 가 공통으로 의존하는 **테마 분류기 v1** 이다. NewsItem 입력을 한국어 테마 tag set 으로 매핑하고 종목별 테마 노출도 (`theme_stocks`) 를 누적한다. 1차는 TF-IDF + LR (sklearn, kiwipiepy 형태소, predict_proba ≥ 0.7), 2차는 BAR-58 임베딩의 코사인 유사도 (사전 정의 테마 prototype 벡터, distance ≤ 0.5), 3차는 claude-haiku zero-shot (비용 절감 백업) — `ThreeTierClassifier` 가 1→2→3 순서로 임계 통과 즉시 반환한다. worktree 환경 제약 (운영자 1주 라벨링 부재 / Anthropic API 키 부재 / 정확도 ≥ 85% 측정 불가) 을 고려해 BAR-54a/54b · BAR-56a/56b · BAR-57a/57b · BAR-58a/58b 와 동일한 a/b 분리 정책을 채택한다. **BAR-59a (worktree 정식 do) — 본 사이클**: `ThemeClassifier` Protocol + `ClassificationResult` (Pydantic v2 frozen) + `TfidfLogRegClassifier` (sklearn lazy + kiwipiepy mock + random_state=42 결정성) + `EmbeddingCosineClassifier` (BAR-58 embedder + prototype 캐시) + `ClaudeHaikuClassifier` (NotImplementedError stub) + `ThreeTierClassifier` (1→2→3 + NotImplementedError fallback) + `ClassifierFactory` 4-way 분기 + `ThemeRepository` (text() + named param + dialect 분기) + alembic 0004 (themes / theme_keywords / theme_stocks 3 테이블 + UNIQUE + FK CASCADE) + Settings 4 신규 + fixture 25건 (테마 5종 × 5건) + 25+ mock 단위 테스트 + 회귀 ≥ 352 passed + coverage ≥ 70%. **BAR-59b (운영 정식)**: 운영자 라벨링 1주 ≥ 1000건 + 실 TF-IDF + LR 학습 + 정확도 ≥ 85% + Anthropic API 키 + haiku 활성화 + 월 1회 재학습 cron + 비용 분포 측정 (1차 ≥ 70% / 2차 ≥ 20% / 3차 ≤ 10%), deferred. 비고려: claude-haiku 실 호출 (59b) / 운영자 라벨링 (59b) / kiwipiepy 실 사전 다운로드 (59b 또는 58c) / 대장주 점수 (BAR-60) / 캘린더 (BAR-61) / 테마 마스터 UI (BAR-72). 위험은 라벨 분포 불균형 / 차원 mismatch / 한국어 토큰화 품질 / NotImplementedError 누수 — 모두 인터페이스 격리 + 임계 외부 주입 + ThreeTier fallback + a/b 분리 + BAR-60 통합 재검증으로 흡수. 다음은 enterprise-expert + bkend-expert + qa-strategist + security-architect + frontend-architect 5인 council 로 design 단계 위임.
