---
tags: [design, feature/bar-42, status/in_progress, phase/0, area/repo]
template: design
version: 1.0
---

# BAR-42 통합 환경변수 스키마 Design Document

> **관련 문서**: [[../../01-plan/features/bar-42-config-settings.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v1|Master Plan v1]]
>
> **Summary**: `Settings` 클래스에 6 신규 그룹 placeholder 추가. SecretStr 옵션 C 적용. 5+ 테스트 시나리오 + V1~V6 검증 + D1~D9 체크리스트
>
> **Project**: BarroAiTrade
> **Feature**: BAR-42
> **Phase**: 0
> **Author**: beye (CTO-lead)
> **Date**: 2026-05-06
> **Status**: Draft
> **Planning Doc**: [bar-42-config-settings.plan.md](../../01-plan/features/bar-42-config-settings.plan.md)

---

## 1. Overview

### 1.1 Design Goals

- 6 신규 그룹 placeholder (NXT/뉴스/테마/Postgres/Redis/JWT) — *동작 변화 없이* 후속 BAR 의 환경변수 의존만 선해소
- 기존 5 그룹 (Trading/Kiwoom/DB/Telegram/로깅/서버) 무영향
- SecretStr 옵션 C 적용 — 신규 5 secret 만, 기존은 BAR-67 위임
- `tests/config/` 디렉터리 신규 — BAR-41 의 `tests/legacy_scalping/` 패턴 일관

### 1.2 Design Principles

- **Placeholder Only**: 본 티켓은 *형태(타입·default)* 만. 값 결정은 후속 BAR
- **Backward Compatibility**: 기존 5 그룹 동작 0 변경
- **Secret Asymmetry Documented**: 옵션 C 의 비대칭을 코드 주석으로 명시 — BAR-67 인계
- **`Settings()` Always Succeeds**: 환경변수 미주입 상태에서도 무에러 인스턴스화

---

## 2. Architecture

### 2.1 그룹 배치

```
backend/config/settings.py (현 75 LOC → 목표 ≤ 200 LOC)

class Settings(BaseSettings):
    # === Trading 기본 (기존) ===
    trading_mode, trading_market, scan_interval_sec

    # === Kiwoom API (기존) ===
    kiwoom_base_url, kiwoom_app_key, kiwoom_app_secret, kiwoom_account_no, kiwoom_mock

    # === NXT (신규, BAR-53) ===
    nxt_enabled: bool = False
    nxt_base_url: Optional[str] = None
    nxt_app_key: Optional[str] = None
    nxt_app_secret: Optional[SecretStr] = None      # 🔒

    # === DB (기존 + Postgres 신규, BAR-56) ===
    db_path: str = "data/barro_trade.db"             # SQLite (현재)
    postgres_url: Optional[SecretStr] = None         # 🔒, 신규
    postgres_pool_size: int = 5
    pgvector_enabled: bool = False

    # === Redis (신규, BAR-57) ===
    redis_url: Optional[str] = None
    redis_streams_enabled: bool = False

    # === 뉴스/공시 (신규, BAR-57) ===
    dart_api_key: Optional[SecretStr] = None         # 🔒
    rss_feed_urls: list[str] = Field(default_factory=list)
    news_polling_interval_sec: int = 60

    # === 테마 (신규, BAR-58/59) ===
    theme_embedding_model: str = "ko-sbert"
    theme_vector_db_url: Optional[str] = None
    theme_classifier_threshold: float = 0.65

    # === 보안 (신규, BAR-67/68) ===
    jwt_secret: Optional[SecretStr] = None           # 🔒
    jwt_access_ttl_sec: int = 3600
    jwt_refresh_ttl_sec: int = 604800
    mfa_issuer: str = "BarroAiTrade"

    # === Telegram (기존) ===
    telegram_bot_token, telegram_chat_id
    # NOTE: telegram_bot_token 도 SecretStr 권장이나 옵션 C — BAR-67 일괄

    # === 로깅 (기존) ===
    log_json, log_level

    # === 서버 (기존) ===
    host, port
```

### 2.2 SecretStr 비대칭 정당화 (옵션 C)

```python
# 신규 secret (5건) — SecretStr
nxt_app_secret: Optional[SecretStr] = None
postgres_url: Optional[SecretStr] = None
dart_api_key: Optional[SecretStr] = None
jwt_secret: Optional[SecretStr] = None
# (note: telegram_chat_id 는 secret 아님)

# 기존 secret (2건) — str (BAR-67 에서 SecretStr 일괄 변환 예정)
kiwoom_app_secret: str = ""        # TODO(BAR-67): SecretStr
telegram_bot_token: str = ""        # TODO(BAR-67): SecretStr
```

기존 사용처가 `settings.kiwoom_app_secret` 직접 참조하는 경우가 다수 있어 본 티켓 범위 밖. BAR-67 에서 일괄 `.get_secret_value()` 호출 변환.

### 2.3 list[str] 환경변수 파싱

`pydantic-settings` 는 환경변수에서 `list[str]` 을 두 형식으로 해석:

```bash
# 형식 1: JSON 문자열 (권장)
RSS_FEED_URLS='["https://news.naver.com/rss","https://kr.investing.com/rss"]'

# 형식 2: comma-separated (custom decoder 필요)
RSS_FEED_URLS=https://news.naver.com/rss,https://kr.investing.com/rss
```

**채택**: 형식 1 (JSON). pydantic-settings 의 기본 파서 그대로 사용. `.env.example` 에 형식 명시.

### 2.4 Module Layout

```
backend/config/settings.py             ← 확장 (75 LOC → ~150 LOC)
.env.example                            ← 갱신 (그룹별 주석)

backend/tests/config/
├── __init__.py                         ← 신규
└── test_settings.py                    ← 신규 (5+ 케이스)

Makefile                                ← test-config 또는 test 통합
```

---

## 3. Implementation Spec

### 3.1 신규 필드 type 정책 표

| 필드 | 타입 | Default | 사용 BAR |
|---|---|---|---|
| `nxt_enabled` | `bool` | `False` | BAR-53 |
| `nxt_base_url` | `Optional[str]` | `None` | BAR-53 |
| `nxt_app_key` | `Optional[str]` | `None` | BAR-53 |
| `nxt_app_secret` | `Optional[SecretStr]` | `None` | BAR-53 |
| `postgres_url` | `Optional[SecretStr]` | `None` | BAR-56 |
| `postgres_pool_size` | `int` | `5` | BAR-56 |
| `pgvector_enabled` | `bool` | `False` | BAR-56 |
| `redis_url` | `Optional[str]` | `None` | BAR-57 |
| `redis_streams_enabled` | `bool` | `False` | BAR-57 |
| `dart_api_key` | `Optional[SecretStr]` | `None` | BAR-57 |
| `rss_feed_urls` | `list[str]` | `[]` (Field default_factory) | BAR-57 |
| `news_polling_interval_sec` | `int` | `60` | BAR-57 |
| `theme_embedding_model` | `str` | `"ko-sbert"` | BAR-58 |
| `theme_vector_db_url` | `Optional[str]` | `None` | BAR-59 |
| `theme_classifier_threshold` | `float` | `0.65` | BAR-59 |
| `jwt_secret` | `Optional[SecretStr]` | `None` | BAR-67 |
| `jwt_access_ttl_sec` | `int` | `3600` | BAR-67 |
| `jwt_refresh_ttl_sec` | `int` | `604800` | BAR-67 |
| `mfa_issuer` | `str` | `"BarroAiTrade"` | BAR-68 |

**총 19 신규 필드** (NXT 4 + Postgres 3 + Redis 2 + 뉴스 3 + 테마 3 + JWT/MFA 4)

### 3.2 SecretStr fallback (pydantic 버전 호환)

```python
try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from pydantic import Field, SecretStr
    _PYDANTIC_SETTINGS = True
except ImportError:
    from pydantic import BaseModel as BaseSettings
    from pydantic import Field
    SecretStr = str  # type: ignore  # fallback when pydantic-settings 미설치
    _PYDANTIC_SETTINGS = False
```

### 3.3 .env.example 형식

```bash
# === NXT (BAR-53) ===
# 미사용 시 NXT_ENABLED=false 유지
NXT_ENABLED=false
# NXT_BASE_URL=https://nxt.example.com
# NXT_APP_KEY=your-app-key
# NXT_APP_SECRET=your-app-secret

# === Postgres (BAR-56, 마이그레이션 후) ===
# POSTGRES_URL=postgresql://user:pass@localhost:5432/barro
# POSTGRES_POOL_SIZE=5
# PGVECTOR_ENABLED=false

# === Redis (BAR-57) ===
# REDIS_URL=redis://localhost:6379/0
# REDIS_STREAMS_ENABLED=false

# === 뉴스/공시 (BAR-57) ===
# DART_API_KEY=your-dart-key
# RSS_FEED_URLS=["https://news.naver.com/rss"]
# NEWS_POLLING_INTERVAL_SEC=60

# === 테마 (BAR-58/59) ===
# THEME_EMBEDDING_MODEL=ko-sbert
# THEME_VECTOR_DB_URL=...
# THEME_CLASSIFIER_THRESHOLD=0.65

# === 보안 (BAR-67/68) ===
# JWT_SECRET=your-jwt-secret
# JWT_ACCESS_TTL_SEC=3600
# JWT_REFRESH_TTL_SEC=604800
# MFA_ISSUER=BarroAiTrade
```

---

## 4. 5+ Test Cases

```python
# backend/tests/config/test_settings.py
import os
import pytest
from backend.config.settings import Settings


class TestSettings:
    """BAR-42 5+ 케이스 (Plan §4.2 / Design §4)."""

    def test_c1_no_env_vars_succeeds(self, monkeypatch):
        """C1: 환경변수 미주입 → Settings() 무에러."""
        # 모든 BAR_42 신규 환경변수 제거
        for key in ["NXT_ENABLED", "NXT_BASE_URL", "POSTGRES_URL",
                    "REDIS_URL", "DART_API_KEY", "RSS_FEED_URLS",
                    "JWT_SECRET", "THEME_EMBEDDING_MODEL"]:
            monkeypatch.delenv(key, raising=False)
        s = Settings(_env_file=None)
        assert s.nxt_enabled is False
        assert s.nxt_base_url is None
        assert s.postgres_url is None
        assert s.theme_embedding_model == "ko-sbert"
        assert s.theme_classifier_threshold == 0.65
        assert s.jwt_access_ttl_sec == 3600

    def test_c2_kiwoom_app_key_injection(self, monkeypatch):
        """C2: 기존 동작 회귀 — KIWOOM_APP_KEY 주입."""
        monkeypatch.setenv("KIWOOM_APP_KEY", "test-key")
        s = Settings(_env_file=None)
        assert s.kiwoom_app_key == "test-key"

    def test_c3_nxt_enabled_bool_parsing(self, monkeypatch):
        """C3: NXT_ENABLED=true → bool 정확 파싱."""
        monkeypatch.setenv("NXT_ENABLED", "true")
        s = Settings(_env_file=None)
        assert s.nxt_enabled is True

    def test_c4_rss_feed_urls_list_parsing(self, monkeypatch):
        """C4: RSS_FEED_URLS JSON 주입 → list 파싱."""
        monkeypatch.setenv("RSS_FEED_URLS", '["https://a.com/rss","https://b.com/rss"]')
        s = Settings(_env_file=None)
        assert s.rss_feed_urls == ["https://a.com/rss", "https://b.com/rss"]

    def test_c5_jwt_secret_repr_masked(self, monkeypatch):
        """C5: JWT_SECRET → repr() 마스킹."""
        monkeypatch.setenv("JWT_SECRET", "super-secret-value")
        s = Settings(_env_file=None)
        assert "super-secret-value" not in repr(s)
        assert s.jwt_secret is not None
        assert s.jwt_secret.get_secret_value() == "super-secret-value"

    def test_c6_env_file_none(self, monkeypatch):
        """C6: _env_file=None → .env 없이도 동작."""
        s = Settings(_env_file=None)
        assert isinstance(s, Settings)


class TestEnvExampleConsistency:
    """env.example 의 키와 Settings 필드 1:1 검증 (보강)."""

    def test_env_example_keys_match_settings_fields(self):
        """`.env.example` 의 모든 KEY 가 Settings 필드명과 일치."""
        from pathlib import Path
        repo_root = Path(__file__).parent.parent.parent.parent
        env_example = repo_root / ".env.example"
        if not env_example.exists():
            pytest.skip(".env.example not found")

        # 주석/빈 줄 제거 후 KEY=VALUE 추출
        keys = set()
        for line in env_example.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip().lower()
                keys.add(key)

        settings_fields = set(Settings.model_fields.keys())
        unknown = keys - settings_fields
        assert not unknown, f".env.example 의 알 수 없는 키: {unknown}"
```

---

## 5. Verification Scenarios (V1 ~ V6)

| # | 시나리오 | 명령 | 기대 |
|---|---|---|---|
| V1 | pytest 6+ 케이스 통과 | `make test-config` | exit 0, 6+ passed |
| V2 | 라인 커버리지 ≥ 80% | `pytest --cov=backend.config.settings` | ≥ 80% |
| V3 | BAR-40 dry-run 회귀 | `make legacy-scalping` | exit 0 |
| V4 | BAR-41 pytest 회귀 | `make test-legacy` | 19 passed |
| V5 | `Settings()` 즉시 인스턴스화 | `python3 -c "from backend.config.settings import settings; print(settings.nxt_enabled)"` | `False` 출력 |
| V6 | `.env.example` 와 필드명 일치 | `TestEnvExampleConsistency` | 통과 |

---

## 6. Risk Mitigation Detail

| Risk (Plan §5) | Detection | Action |
|---|---|---|
| SecretStr 회귀 | V3/V4 실패 | 옵션 C 로 신규 5개만 적용 — 기존 `.kiwoom_app_secret` 사용처 변경 없음 |
| placeholder 의도 어긋남 | 후속 BAR 진입 시 default 값 변경 | 본 티켓에서 default 는 *최소 안전값* (e.g., NXT_ENABLED=False) |
| .env.example 불일치 | V6 실패 | 1:1 검증 테스트 추가 — TestEnvExampleConsistency |
| list[str] 파싱 | V1 의 C4 실패 | JSON 형식 명시 (`.env.example` 주석) |
| Postgres URL 노출 | git push 시 .env 포함 | `.gitignore` 의 `.env` 패턴 이미 존재 |

---

## 7. Out-of-Scope (재확인)

- ❌ 환경변수 *값* 주입 (각 후속 BAR)
- ❌ NXT/뉴스/테마/JWT 모듈 자체 (BAR-53/57/59/67)
- ❌ Postgres 마이그레이션 (BAR-56)
- ❌ 기존 secret 일괄 SecretStr (BAR-67)

---

## 8. Implementation Checklist (D1~D9)

- [ ] D1 — `Settings` 사용처 grep (`from backend.config.settings`) 으로 회귀 위험 확인
- [ ] D2 — `Settings` 클래스에 19 신규 필드 추가 (§3.1 표)
- [ ] D3 — SecretStr fallback (§3.2)
- [ ] D4 — `.env.example` 갱신 (§3.3)
- [ ] D5 — `backend/tests/config/__init__.py`, `test_settings.py` 6+ 케이스
- [ ] D6 — `Makefile` `test-config` 타겟 또는 `test` 통합
- [ ] D7 — V1~V6 검증 실행
- [ ] D8 — BAR-40/41 회귀 무영향 확인
- [ ] D9 — PR 생성 (라벨: `area:repo` `phase:0` `priority:p0`)

---

## 9. Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 design — 19 신규 필드 표, 5+ 테스트 시나리오, V1~V6 | beye (CTO-lead) |
