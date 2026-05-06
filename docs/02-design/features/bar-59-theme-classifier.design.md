# BAR-59 — 테마 분류기 v1 Design

**Plan**: `docs/01-plan/features/bar-59-theme-classifier.plan.md` (PR #98 머지)
**Phase**: 3 — 네 번째 BAR / Phase 3 분류 게이트
**Status**: Draft (council: architect + developer + qa + reviewer + security)
**Date**: 2026-05-07

> **요약**: BAR-58a 임베딩 인프라 위 3-tier 테마 분류기 (TF-IDF + 임베딩 cosine + claude-haiku stub). 5 council 권고 모두 흡수. BAR-59a (worktree) / BAR-59b (운영 라벨링 + claude-haiku) 분리.

---

## §0. 분리 정책

| BAR | 트랙 | 산출물 |
|-----|------|--------|
| **BAR-59a** | worktree | Classifier Protocol + 4 어댑터 + ThreeTier orchestrator + theme_repo + alembic 0004 + 28+ tests |
| **BAR-59b** | 운영 | 라벨링 1주 + 실 LR 학습 + claude-haiku 활성화 + 정확도 ≥ 85% + 모델 SHA256+HMAC |

### 0.1 5 council 합의

- **architect**: search_similar 미사용 → in-memory prototype 캐시 / Factory auto-fit / `attempted` 필드
- **developer**: ClaudeHaiku `__init__` 정상 + `classify()` raise / tags `tuple[str,...]` 통일 / `_kiwi_tokenize` module-level singleton
- **qa**: 매트릭스 25 → 28+ (alembic round-trip / dim mismatch / invalid backend) / `solver='liblinear'` / saturated proba 분리
- **reviewer**: search_similar 시그니처 정정 / Embedder L2 normalize 전제 명시 / saturated proba 케이스 분리
- **security**: `_redact()` hook 자리잡기 (CWE-200) / 모델 SHA256+HMAC 인터페이스 (CWE-502) / `SecretStr` 컨벤션 (CWE-532)

---

## §1. 데이터 모델 (`backend/models/theme.py`)

```python
from pydantic import BaseModel, ConfigDict, Field

class ClassificationResult(BaseModel):
    """frozen + tuple tags + attempted 추적."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    tags: tuple[str, ...] = ()                  # 정렬·중복 제거
    scores: dict[str, float] = Field(default_factory=dict)
    backend: str = ""                            # "tfidf_lr_v1" / "three_tier_v1:fallback_no_tier3:from_..."
    confidence: float = 0.0
    attempted: tuple[str, ...] = ()              # tier1→tier2→tier3 누적
```

---

## §2. ThemeClassifier Protocol

```python
from typing import Protocol, runtime_checkable, ClassVar

@runtime_checkable
class ThemeClassifier(Protocol):
    backend_id: ClassVar[str]
    async def classify(self, news_item) -> ClassificationResult: ...
    async def _redact(self, text: str) -> str: ...   # security CWE-200
```

기본 `_redact` 는 no-op (BAR-59b 에서 정규식+presidio 교체).

---

## §3. TfidfLogRegClassifier (1차)

```python
class TfidfLogRegClassifier:
    backend_id: ClassVar[str] = "tfidf_lr_v1"

    def __init__(self, threshold: float = 0.7) -> None:
        self._pipeline = None
        self._threshold = threshold

    def fit(self, samples: list[tuple[str, str]]) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.multiclass import OneVsRestClassifier
        from sklearn.pipeline import Pipeline

        clf = OneVsRestClassifier(
            LogisticRegression(solver="liblinear", random_state=42)  # qa: 결정성
        )
        self._pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(
                tokenizer=_kiwi_tokenize,
                ngram_range=(1, 2),
                max_features=5000,
                lowercase=False,
            )),
            ("lr", clf),
        ])
        texts, themes = zip(*samples)
        self._pipeline.fit(list(texts), list(themes))

    async def classify(self, news_item) -> ClassificationResult:
        if self._pipeline is None:
            return ClassificationResult(
                backend=self.backend_id, attempted=(self.backend_id,)
            )
        text = await self._redact(f"{news_item.title} {news_item.body}")
        proba = self._pipeline.predict_proba([text])[0]
        classes = self._pipeline.classes_
        scores = {c: float(p) for c, p in zip(classes, proba)}
        tags = tuple(sorted({c for c, p in scores.items() if p >= self._threshold}))
        confidence = max(scores.values()) if scores else 0.0
        return ClassificationResult(
            tags=tags, scores=scores, backend=self.backend_id,
            confidence=confidence, attempted=(self.backend_id,),
        )

    async def _redact(self, text: str) -> str:
        return text


# module-level singleton (developer 권고 — joblib pickle 호환)
_KIWI = None

def _kiwi_tokenize(text: str) -> list[str]:
    global _KIWI
    if _KIWI is None:
        from kiwipiepy import Kiwi
        _KIWI = Kiwi()
    return [
        token.form
        for token in _KIWI.tokenize(text)
        if token.tag.startswith(("NN", "VV", "VA"))
    ]
```

---

## §4. EmbeddingCosineClassifier (2차)

```python
class EmbeddingCosineClassifier:
    """prototype 5종 in-memory 캐시. search_similar 미사용 (architect 권고).

    Embedder L2 normalize 가정 → np.dot == cosine_similarity (reviewer 권고).
    """

    backend_id: ClassVar[str] = "embedding_cosine_v1"

    def __init__(self, embedder, theme_prototypes: dict[str, str], threshold: float = 0.5):
        self._embedder = embedder
        self._theme_prototypes = theme_prototypes
        self._threshold = threshold
        self._proto_vecs = None

    async def _ensure_prototypes(self):
        if self._proto_vecs is not None:
            return
        themes = list(self._theme_prototypes.keys())
        texts = list(self._theme_prototypes.values())
        vecs = await self._embedder.encode(texts)
        self._proto_vecs = dict(zip(themes, vecs))

    async def classify(self, news_item) -> ClassificationResult:
        await self._ensure_prototypes()
        text = await self._redact(f"{news_item.title} {news_item.body}")
        [news_vec] = await self._embedder.encode([text])
        scores = {}
        for theme, pv in self._proto_vecs.items():
            sim = float(np.dot(news_vec, pv))    # L2 normalized 가정
            scores[theme] = 1.0 - sim             # cosine distance
        tags = tuple(sorted({c for c, d in scores.items() if d <= self._threshold}))
        confidence = 1.0 - (min(scores.values()) if scores else 1.0)
        return ClassificationResult(
            tags=tags, scores=scores, backend=self.backend_id,
            confidence=confidence, attempted=(self.backend_id,),
        )

    async def _redact(self, text: str) -> str:
        return text
```

---

## §5. ClaudeHaikuClassifier (3차 stub)

```python
class ClaudeHaikuClassifier:
    """council (architect/developer/reviewer) 합의: __init__ 정상 + classify() 진입 시 raise."""

    backend_id: ClassVar[str] = "claude_haiku_v1"

    def __init__(self, api_key=None) -> None:
        self._api_key = api_key  # Optional[SecretStr] — BAR-59b 활성화

    async def classify(self, news_item) -> ClassificationResult:
        # security: BAR-59b 진입 시 _redact() 적용 후 외부 송출
        raise NotImplementedError("ClaudeHaikuClassifier — BAR-59b")

    async def _redact(self, text: str) -> str:
        return text  # BAR-59b 정규식+presidio
```

---

## §6. ThreeTierClassifier orchestrator

```python
class ThreeTierClassifier:
    backend_id: ClassVar[str] = "three_tier_v1"

    def __init__(self, tier1, tier2, tier3) -> None:
        self._tier1 = tier1
        self._tier2 = tier2
        self._tier3 = tier3

    async def classify(self, news_item) -> ClassificationResult:
        attempted: list[str] = []

        r1 = await self._tier1.classify(news_item)
        attempted.append(self._tier1.backend_id)
        if r1.tags and r1.confidence >= 0.7:
            return r1.model_copy(update={"attempted": tuple(attempted)})

        r2 = await self._tier2.classify(news_item)
        attempted.append(self._tier2.backend_id)
        if r2.tags and r2.confidence >= 0.5:
            return r2.model_copy(update={"attempted": tuple(attempted)})

        try:
            r3 = await self._tier3.classify(news_item)
            attempted.append(self._tier3.backend_id)
            return r3.model_copy(update={"attempted": tuple(attempted)})
        except NotImplementedError:
            attempted.append(self._tier3.backend_id)
            best = r1 if r1.confidence >= r2.confidence else r2
            from_marker = (
                f"{self.backend_id}:fallback_no_tier3:from_{best.backend}"
            )
            return best.model_copy(update={
                "backend": from_marker,
                "attempted": tuple(attempted),
            })
```

---

## §7. ClassifierFactory

```python
class ClassifierFactory:
    @staticmethod
    def from_settings(settings, embedder=None) -> ThemeClassifier:
        backend = settings.news_theme_backend
        if backend == "tfidf":
            cls = TfidfLogRegClassifier(threshold=settings.news_theme_threshold_tfidf)
            samples = ClassifierFactory._load_samples(settings.news_theme_labels_path)
            cls.fit(samples)
            return cls
        if backend == "cosine":
            if embedder is None:
                raise ValueError("embedder required for cosine backend")
            return EmbeddingCosineClassifier(
                embedder=embedder,
                theme_prototypes=ClassifierFactory._load_prototypes(
                    settings.news_theme_labels_path
                ),
                threshold=settings.news_theme_threshold_cosine,
            )
        if backend == "haiku":
            return ClaudeHaikuClassifier(api_key=settings.anthropic_api_key)
        if backend == "three_tier":
            tier1 = TfidfLogRegClassifier(threshold=settings.news_theme_threshold_tfidf)
            samples = ClassifierFactory._load_samples(settings.news_theme_labels_path)
            tier1.fit(samples)
            tier2 = EmbeddingCosineClassifier(
                embedder=embedder,
                theme_prototypes=ClassifierFactory._load_prototypes(
                    settings.news_theme_labels_path
                ),
                threshold=settings.news_theme_threshold_cosine,
            )
            tier3 = ClaudeHaikuClassifier(api_key=settings.anthropic_api_key)
            return ThreeTierClassifier(tier1, tier2, tier3)
        raise ValueError(f"unknown theme backend: {backend}")

    @staticmethod
    def _load_samples(path):
        if not path or not os.path.exists(path):
            raise ValueError(f"news_theme_labels_path missing: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [(t, theme) for theme, texts in data.items() for t in texts]

    @staticmethod
    def _load_prototypes(path):
        if not path or not os.path.exists(path):
            raise ValueError(f"news_theme_labels_path missing: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {theme: texts[0] for theme, texts in data.items()}
```

---

## §8. ThemeRepository (`backend/db/repositories/theme_repo.py`)

```python
class ThemeRepository:
    """themes / theme_keywords / theme_stocks CRUD. dialect 분기 (BAR-56/57/58 패턴)."""

    async def upsert_theme(self, name: str, description: str = "") -> int: ...
    async def add_keyword(self, theme_id: int, keyword: str) -> bool: ...
    async def link_stock(self, theme_id: int, symbol: str, score: float) -> bool: ...
    async def find_themes_by_stock(self, symbol: str) -> list[dict]: ...
```

---

## §9. Alembic 0004

```python
def upgrade():
    op.create_table(
        "themes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", ts_type, nullable=False, server_default=...),
    )
    op.create_table(
        "theme_keywords",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("theme_id", sa.BigInteger,
                  sa.ForeignKey("themes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keyword", sa.Text, nullable=False),
        sa.UniqueConstraint("theme_id", "keyword"),
    )
    op.create_table(
        "theme_stocks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("theme_id", sa.BigInteger,
                  sa.ForeignKey("themes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("score", sa.Float(precision=53), nullable=False),
        sa.UniqueConstraint("theme_id", "symbol"),
    )
    op.create_index("idx_theme_stocks_symbol", "theme_stocks", ["symbol"])
```

---

## §10. Settings

```python
news_theme_backend: Literal["tfidf", "cosine", "haiku", "three_tier"] = "three_tier"
news_theme_threshold_tfidf: float = Field(default=0.7, ge=0.0, le=1.0)
news_theme_threshold_cosine: float = Field(default=0.5, ge=0.0, le=2.0)
news_theme_labels_path: Optional[str] = None    # JSON {"테마": ["text", ...]}
```

`anthropic_api_key: Optional[SecretStr]` 는 BAR-58 에서 이미 추가됨.

---

## §11. fixture (`backend/tests/fixtures/theme_labels.json`)

테마 5종 × 5건 = 25 샘플 (전기차 / 반도체 / 바이오 / 원전 / AI).

---

## §12. 테스트 매트릭스 (≥ 28 cases)

| 그룹 | 케이스 |
|------|:------:|
| `test_tfidf_lr.py` | 4 (fit / threshold 분기 / saturated proba / kiwipiepy fallback) |
| `test_embedding_cosine.py` | 4 (prototype 캐시 / threshold / dim mismatch / L2 가정) |
| `test_claude_haiku.py` | 2 (init / classify NotImplementedError) |
| `test_three_tier.py` | 5 (tier1 hit / tier2 / tier3 NotImplementedError catch + best-effort / attempted 누적 / backend marker) |
| `test_factory.py` | 4 (각 backend / unknown backend ValueError / 자동 fit / labels_path missing ValueError) |
| `test_theme_repo.py` | 4 (upsert / link_stock / find / FK CASCADE) |
| `test_alembic_0004.py` | 3 (revision id / upgrade 3 table + UNIQUE / downgrade reverse) |
| `test_classification_result.py` | 2 (frozen + tuple / attempted 보존) |
| **합계** | **28** |

`--cov-fail-under=70`.

---

## §13. 회귀 게이트

- baseline: 327 passed (BAR-58a 후)
- 신규: 28 → **≥ 355 passed**

---

## §14. 보안 요약

| CWE | 시그니처 |
|-----|----------|
| CWE-200 외부 LLM PII | `_redact()` hook 자리잡기 (BAR-59a no-op) |
| CWE-502 joblib pickle | BAR-59b SHA256+HMAC 무결성 검증 (인터페이스만 design) |
| CWE-532 API 키 로그 | `anthropic_api_key: SecretStr` (BAR-58 에서 이미 추가) |
| CWE-1325 supply chain | kiwipiepy 사전 다운로드 — BAR-59b hash pinning |

---

## §15. 후속

- **BAR-59b**: 라벨링 1주 + 실 LR 학습 + claude-haiku 활성화 + SHA256/HMAC
- **BAR-60**: 대장주 점수 — theme_stocks + embeddings 결합

---

## §16. 다음 단계

`/pdca do BAR-59` — §1~§11 구현 + 28 tests + 회귀 ≥ 355 + coverage ≥ 70%.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-07 | Initial draft (5 council 종합) | bkit-cto-lead |
