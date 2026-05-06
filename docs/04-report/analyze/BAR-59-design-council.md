# Team Agent Combined Output

- BAR: BAR-59
- Stage: design
- Generated: 2026-05-07 02:16:04 KST
- Roles: architect developer qa reviewer security


---

## architect (exit=0)

## Architect 검토 — BAR BAR-59 design

### 결과
**WARN** — 모듈 경계·의존성 그래프·기존 추상과의 충돌은 모두 통과. 다만 design → do 진입 전 3건의 명세 정합성 보정이 필요.

### 핵심 발견 (5)
- **FR-05 ↔ §6 의사코드 불일치**: FR-05 는 `EmbeddingRepository.search_similar` 활용을 명시하나 `embedding_classifier.py` 의사코드는 `embedder.encode + np.dot` in-memory 계산만 사용 (Repo 의존성 0). prototype 5종은 in-process 배열이 적합하므로 의사코드가 옳고 FR-05 가 정정 대상. 의존성 그래프에서 `EmbeddingCosineClassifier → EmbeddingRepository` 간선 제거 필요.
- **TfidfLogRegClassifier bootstrap 미명시**: `__init__` 은 빈 pipeline, `fit(samples)` 는 별도 호출. `ClassifierFactory.from_settings()` 가 worktree 모드에서 `NEWS_THEME_LABELS_PATH` 의 fixture 25건을 자동 fit 하는 책임을 명시하지 않으면 tier1 의 `_pipeline is None` 분기로 confidence=0 이 고정 → ThreeTier 가 항상 tier2/3 fallback 으로 빠져 plan §2 의 비용 분포 가설(1차 70%) 검증 불가.
- **fallback 시 backend 추적 정보 손실**: `best.model_copy(update={"backend": "three_tier_v1:fallback_no_tier3"})` 가 원래 tier1/tier2 출처를 덮어씀. 운영 prometheus 측정(NFR-09) 시 어느 tier 의 best 인지 식별 불능. `f"...:fallback_no_tier3:from_{best.backend}"` 형태로 보존하거나 `attempted: tuple[str, ...]` 신규 필드 검토.
- **Protocol 일관성**: `backend_id` 가 의사코드에서 class attr 로 선언되나 FR-03 은 property 로 명세. ABC/Protocol 혼용 회피 측면에서 `ClassVar[str]` 로 통일 권고. (Strategy v2 ABC / INxtGateway Protocol 과 별개 도메인이라 충돌 없음.)
- **alembic 0004 SQLite FK CASCADE**: BAR-56a 의 `PRAGMA foreign_keys=ON` 적용 범위가 connection-level 인지 engine-level 인지 design.md 에서 명시 필요. `theme_keywords/theme_stocks` 의 ON DELETE CASCADE 가 상위 themes 삭제 단위 테스트로 양방향 검증되어야 NFR-07 충족.

### 권고
1. design.md 의 FR-05 본문에서 `search_similar` 활용 문구를 "in-memory cosine (prototype 5종은 init 시 1회 encode 후 캐시)" 로 정정하고, ClassifierFactory 시그니처에서 `repo` 파라미터를 EmbeddingCosineClassifier 경로에서 제거. theme_repo 만 ThemeRepository 주입에 사용.
2. ClassifierFactory 의 책임에 "settings.NEWS_THEME_LABELS_PATH 로딩 → TfidfLogRegClassifier.fit(samples) 자동 호출" 을 명시하고, FR-21 fixture 부재 시 명시적 ValueError raise. 단위 테스트 1건 (factory.from_settings 후 tier1 confidence > 0 검증) 을 NFR-03 매트릭스에 추가.
3. ClassificationResult 에 `attempted: tuple[str, ...] = ()` (frozen, 정렬 X) 추가하고, ThreeTier 가 tier 호출마다 누적 → fallback 경로에서 `("tfidf_lr_v1", "embedding_cosine_v1")` 가 보존되도록. tuple 사용으로 Pydantic v2 frozen 호환.

### blocker
없음. 위 3건은 design.md 본문 정정 (≤ 0.5 일) 으로 흡수, do 진입 차단 사유 아님. 모듈 경계 (4 어댑터 SRP) / 기존 추상과의 충돌 (Strategy v2 / INxtGateway / MarketSessionService 와 도메인 분리 명확) / Pydantic v2 frozen 정책 / Decimal 자금흐름 N/A — 모두 PASS.

---

## developer (exit=0)

## Developer 작업 — BAR BAR-59 design

본 단계는 design 검토. 본 pane 에서 직접 수정 X, 리뷰 노트만 산출.

### 검토 결과 — **WARN** (구현 가능, but 3건 사전 정합 필요)

#### W1. `ClaudeHaikuClassifier` NotImplementedError 위치 모순 (BLOCK 위험)
- FR-06: "`__init__` 에서 raise NotImplementedError" 명세
- §6 의사코드: `await self.haiku.classify(...)` 후 `except NotImplementedError` catch
- → init 에서 raise 시 `ClassifierFactory.from_settings(... three_tier ...)` 가 즉시 폭발. ThreeTier orchestrator 가 haiku 인스턴스를 들고 있어야 하므로 **`__init__` 정상 구성 + `classify()` 진입 시 raise** 로 통일 필요. 단위 테스트 (FR DoD §5-1, "NotImplementedError raise") 도 classify 호출 시점으로 명세 변경.

#### W2. pyproject.toml 에 `[project]` 선언 자체 부재
- 현재 `pyproject.toml` 은 pytest/coverage 설정만 (25줄). FR-18/19/20 의 "scikit-learn>=1.5 / kiwipiepy>=0.17 / anthropic extras_require" 추가 위치가 부정확 — `[project.dependencies]` / `[project.optional-dependencies]` 섹션을 신설하거나, 별도 `requirements.txt` 패턴인지 architect 와 합의 후 do 진입. 머지 후 CI 가 sklearn 미설치로 실패할 위험.

#### W3. `ClassificationResult.tags` 가 `list[str]` — frozen 불호환
- FR-02: `tags: list[str]` 정렬·중복 제거 후 immutable
- 그러나 `NewsItem` 은 frozen + `tags: tuple[str, ...]` (backend/models/news.py:49). Pydantic v2 frozen 모델이 mutable list 필드를 들면 hash/equality + 외부 변조 차단 모두 깨짐. `tuple[str, ...]` 로 통일 권고 (NewsItem 패턴 답습).

### 추가 노트 (미차단)
- `_kiwi_tokenize` 를 `@staticmethod` + 매 호출 `Kiwi()` 생성하면 5000-feature TF-IDF fit 시 25 × 평균 토큰화당 사전 reload 발생. **module-level singleton + lazy** 권고. BAR-59b joblib 직렬화 호환성 (top-level callable) 도 동일 이유 충족.
- coverage 정책 (`pyproject.toml:22`) 이 `raise NotImplementedError` 라인 자동 exclude — haiku stub coverage 70% 게이트 영향 0.
- alembic 0004 FK CASCADE + SQLite `PRAGMA foreign_keys=ON` 은 BAR-56a 에서 처리됨 — up/down 왕복 테스트만 추가하면 PASS 예상.

### 산출 파일
- (design 단계: 본 pane 직접 수정 X — architect 의 `bar-59-theme-classifier.design.md` 에 W1~W3 반영 권고)

### 테스트 결과
- (do 단계 X)

### 위험·노트
- **변경 범위 보고**: 본 pane 코드 수정 0. design.md 작성은 architect 책임.
- **충돌 가능 영역**: bkend-expert 가 sklearn pipeline 직렬화 절차 명세 시 `_kiwi_tokenize` singleton 패턴 동시 정의 권고. security-architect 의 joblib pickle 무결성 절차와 직접 연동.

---
─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read, Grep, Bash (worktree 코드/plan 정합성 검토)
⏭️ Not Used: TodoWrite (단일 리뷰 산출, 진행 추적 불필요), /pdca design (본 pane 은 council 분담 중 developer 시각만 담당), AskUserQuestion (auto mode + 명세 명확)
💡 Recommended: architect 가 W1~W3 반영 후 `/pdca do BAR-59` 진입 시 본 pane 이 NotImplementedError 위치 수정 + tuple tags + pyproject deps 3건 선구현 담당
─────────────────────────────────────────────────

---

## qa (exit=0)

베이스라인 확인 완료 (327 passed, 1 skipped — plan의 BAR-58a 누적 327과 일치, 회귀 게이트 ≥ 352 = 327 + 25 정확). design 파일은 아직 없으며 plan 위에서 council 단계 진입 직전. QA 시각으로 plan-as-design-input 검토합니다.

## QA 검증 — BAR BAR-59 design

### 결과
**WARN**

### 테스트 메트릭 (예상)
- 신규: 25 cases (4+4+4+2+3+4+4 = 25, plan §5-1 분류표 합산 일치)
- 회귀: ≥ 352 passed / 0 failed (baseline 327 + 25 신규)
- coverage 목표: ≥ 70% (`backend/core/themes/` + `theme_repo.py`) — NFR-06 기재

### 누락·위험

1. **alembic 0004 round-trip 시나리오가 25건 분류표에 없음** — NFR-07 (up/down 왕복 PASS) + DoD 5-1 의 "up/down 왕복 PASS" 는 명시되어 있으나 25건 시나리오 매트릭스 (themes/keywords/stocks 3 테이블 + FK CASCADE + UNIQUE) 에는 ThemeRepository 4건만 배정. **alembic round-trip + SQLite `PRAGMA foreign_keys=ON` 재확인 1~2건 추가 → 26~27건 권고**.

2. **차원 mismatch + Factory invalid backend 케이스 누락** — 위험 표 #2 (Cosine prototype 차원 ≠ news 차원, "단위 테스트 fixture 1건" 명시) 와 `ClassifierFactory` 의 unknown backend ValueError 분기가 25건 분류표에 없음. EmbeddingCosineClassifier 4건 / Factory 4건이 모두 정상 경로만 다룸. **음의 경로 2건 보강 필수**.

3. **결정성 증빙 + 외부 누수 위협 design 명세 미확정** — NFR-01 ("5회 fit 반복 np.array_equal") 의 결정성은 sklearn LR `solver` 에 의존 (ovr + 기본 lbfgs 는 비결정성 가능). `solver='liblinear'` 명시 + 다중 OS/스레드 환경 검증이 design 단계에서 확정 필요. 추가로 위험 표 #5 (NotImplementedError 누수) 는 ThreeTierClassifier 단위 테스트로 흡수되지만, **NewsItem.body 의 PII 가 BAR-59b haiku 호출에서 외부로 누수되는 정책** (security-architect 위임 영역) 이 BAR-59a 에서 입력 sanitize 인터페이스로 들어갈지 BAR-59b 이관할지 design 명시 필요.

### 권고
- alembic 0004 round-trip + dim mismatch + Factory invalid backend 3건 보강 → **28+ cases 로 매트릭스 확장**
- TfidfLogRegClassifier `solver='liblinear'` 명시 (결정성 보장) + 5회 반복 게이트 명문화
- PII 마스킹 인터페이스 — BAR-59a 의 ClassificationResult input contract 에 hook 만 두고 실 마스킹은 BAR-59b 로 이관할지 design council 결정 (security-architect)

회귀 게이트 (≥ 352) 와 coverage 게이트 (≥ 70%) 는 적정. 상기 3건 보강 시 PASS 전환 가능.

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read (plan 검토), Bash (회귀 baseline 327 verify), Glob (design/plan 디렉토리 점검)
⏭️ Not Used: AskUserQuestion (Auto mode + 본 QA 역할은 단일 산출), /pdca skills (council 단계 외부 dispatcher 가 관리), TodoWrite (단일 검토 산출), gap-detector/code-analyzer (design 산출물 부재 — analyze 단계 진입 후)
💡 Recommended: design 산출물 생성 후 `bkit:design-validator` 로 council 5인 통합 검증 → `/pdca do BAR-59` 진입
─────────────────────────────────────────────────

---

## reviewer (exit=0)

## Reviewer 리뷰 — BAR BAR-59 design

### 종합
**WARN** (design 문서 미작성 단계 — plan→design 전환을 위한 사전 검토)

### 발견 (7개)

- **[차단] ClaudeHaikuClassifier `__init__` 시점 NotImplementedError 와 ThreeTier fallback 모순**
  Plan FR-06 은 `__init__`에서 raise 하도록 명세되어 있으나, §6 의사코드는 `await self.haiku.classify(...)` 호출 시점에서 except — 그 이전 `ThreeTierClassifier.__init__` 주입 단계에서 인스턴스화 자체가 폭발한다. design 에서 **lazy stub mode** (`stub=True` 분기 또는 `factory.from_settings` 가 stub 인스턴스만 반환) 로 정정 필요.

- **[차단] `EmbeddingRepository.search_similar` 시그니처 불일치**
  실제 `search_similar(query_vec, model, top_k=10)` 인데 plan FR-05 는 `search_similar(query, model, k=10)`. 또한 의사코드는 search_similar 를 호출하지 않고 in-memory `np.dot` 만 사용 — plan 명세와 의사코드 자체 모순. 5종 prototype 만 있을 때 in-memory 가 합당하므로 plan 의 "search_similar 활용" 문구를 design 에서 "참조용, 실제는 prototype 캐시 in-memory dot" 으로 정정.

- **[중요] `ClassificationResult.tags` 타입 — frozen 모델에 `list[str]`**
  Plan FR-02 는 `list[str]` 이나 NewsItem.tags 는 `tuple[str, ...]`. Pydantic v2 frozen 일관성 위해 design 에서 **tuple** 로 통일 권장 (NFR-04 정렬·중복 제거 + 불변).

- **[중요] Embedder L2 normalize 가정의 명시 누락**
  의사코드 `np.dot(news_vec, pv) == cosine_similarity` 는 양쪽 L2-normalized 전제. FakeDeterministicEmbedder 는 normalize 하나, 운영 실 모델 보장 없음. design 에서 **EmbeddingCosineClassifier 입구에 normalize assert** + 위협 모델 명시.

- **[중요] fixture 25건 위 LR `predict_proba` 의미성**
  테마 5종 × 5건 + ngram(1,2) + max_features=5000 → train 너무 작아 OVR LR 의 predict_proba 가 거의 0/1 으로 saturate. NFR-01 결정성은 OK 이나 임계 0.7 분기 단위 테스트가 trivially pass — design 시나리오 매트릭스에 **"saturated proba 케이스" 분리** 필요.

- **[경미] SQLite `PRAGMA foreign_keys=ON` 재확인 위임**
  Plan §7 위험 표는 "BAR-56a 처리됨, 재확인" 으로만 기술. design 의 alembic 0004 섹션에서 grep 결과 인용 권장.

- **[경미] `LogisticRegression(multi_class='ovr')` sklearn 1.7+ deprecated 경고**
  Plan §7 에서 인지하나 design 의사코드는 그대로 명시. design 에서 `# noqa: deprecated-multiclass` 주석 또는 `OneVsRestClassifier(LogisticRegression(...))` 명시적 wrap 권장 (deprecated 누수 방지).

### 표준 일관성

- **커밋 메시지 prefix**: plan PR #98 `docs(BAR-59):` — **일치**
- **5 PR pattern**: `/pdca plan/design/do/analyze/report` 명시 §8 — **일치**
- **a/b 분리 패턴**: BAR-54/56/57/58 와 동일 — **일치**

차단 2건 (ClaudeHaikuClassifier 모순 / search_similar 시그니처) 정정 후 design 진행 권장.

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read, Bash, Grep (design 사전 검토용 plan/code 확인)
⏭️ Not Used: TodoWrite (단일 리뷰 task), pdca/agent skill (reviewer 역할은 의견만 — code-analyzer/gap-detector 는 do/analyze 단계 책임)
💡 Recommended: 5인 council 의 design 산출 후 `bkit:design-validator` 로 명세 일관성 자동 검증, 이어서 `/pdca do BAR-59`
─────────────────────────────────────────────────

---

## security (exit=0)

## Security 검토 — BAR BAR-59 design

### 종합
**WARN** — BAR-59a 산출물 자체에는 자격증명 평문/하드코딩 없음 (Haiku 는 stub, fixture 만 사용). 그러나 BAR-59b 진입 시 **확정될 인터페이스 3건** 이 BAR-59a Protocol 단계에 미리 자리를 잡지 않으면 후행 누수 위험. 현재 PR 게이트는 통과하되, design 산출물에 보안 인터페이스 hook 을 명시할 것.

### 발견 (CWE / OWASP 매핑)

- **[HIGH] CWE-200 / OWASP A01 — 외부 LLM 으로 NewsItem.body PII 노출 위험 (BAR-59b 트리거)**: `ClaudeHaikuClassifier.classify(news_item)` 가 활성화되면 본문 전체 (인명·종목·잠재 주민번호 등) 가 Anthropic API 로 송출. plan §8 에 security-architect 책임 "body PII 마스킹 정책" 이 명시되어 있으나, BAR-59a 의 Protocol/DoD 에 sanitize hook 이 부재 → BAR-59b 에서 후행 추가 시 인터페이스 break.

- **[HIGH] CWE-502 — joblib pickle 모델 아티팩트 무결성 (BAR-59b)**: BAR-59b DoD 의 `joblib.dump` + S3 업로드 + 월 1회 hot-swap. S3 권한 침해 또는 MITM 시 임의 코드 실행 (joblib = pickle). 서명/HMAC 검증 절차가 BAR-59b 단계에 명시되어 있으나, **모델 로더 인터페이스** (`load_model(path, expected_sha256)`) 가 BAR-59a 에 자리잡지 않으면 hot-swap 구현 시 검증 우회 위험.

- **[MEDIUM] CWE-532 — Anthropic API key 로그 노출 가능성**: FR-14 의 `NEWS_THEME_BACKEND` 외에 BAR-59b 가 추가할 `ANTHROPIC_API_KEY` 의 settings 타입이 미명시. `pydantic.SecretStr` 미사용 시 settings dump / health check / 예외 traceback 으로 누출.

- **[LOW] CWE-1325 — kiwipiepy 사전 자동 다운로드 supply chain**: `Kiwi()` 첫 호출 시 외부 사전 다운로드 — hash pinning 부재.

- **[LOW] FK ON DELETE CASCADE on themes**: `theme_stocks` 가 CASCADE 삭제됨 (FR-10/11). 자금흐름 area 는 아니나 운영자 실수로 노출도 누적 데이터 영구 소실 가능. soft-delete + RESTRICT 권장.

### 권고

1. **BAR-59a Protocol 에 `_redact(text: str) -> str` hook 추가** — `ClaudeHaikuClassifier.classify` 입력 가공 단계에서 호출되도록 stub 단계부터 자리잡기. BAR-59b 에서 정규식 (주민번호 6-7 / 전화번호 / 이메일) + presidio 등 채택 시 인터페이스 break 없음.
2. **BAR-59b DoD 에 모델 무결성 검증 명시** — `joblib.load(path)` 전 SHA256 + HMAC-SHA256 (env secret) 검증, 실패 시 `RuntimeError` + 운영 알람. 모델 ID 버저닝 (`tfidf_lr_v1_2026Q2`) 옆에 `sha256` 필드 동반.
3. **Settings 의 시크릿 필드는 `pydantic.SecretStr`** — `ANTHROPIC_API_KEY` (BAR-59b), 향후 S3 자격증명. logger filter + `__repr__` 마스킹. 본 BAR-59a 단계에서 Settings 클래스 컨벤션을 미리 design 문서에 못 박을 것.

후속 보안 BAR 트리거: BAR-67 (시크릿 관리 통합) 에서 `ANTHROPIC_API_KEY` + 모델 서명 키 통합. PR label `area:security` 부착 권장 (BAR-59b 진입 시).

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read (plan 문서 확인), security-architect 역할 산출물
⏭️ Not Used: /pdca design (Council 1인 분담 — orchestrator 가 별도 호출), gap-detector (design 단계는 plan vs design 비교 — analyze 단계에서 호출), AskUserQuestion (auto mode + 선행 컨텍스트 충분)
💡 Recommended: design 산출물 통합 후 `/pdca do BAR-59` — 본 검토의 권고 3건이 design.md 에 반영되었는지 확인 후 구현 진입
─────────────────────────────────────────────────
