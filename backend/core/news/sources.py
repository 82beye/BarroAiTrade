"""
BAR-57 — News source adapters.

- NewsSourceAdapter Protocol (mock 친화)
- RSSSource: HOST_ALLOWLIST 4 도메인 강제 (SSRF CWE-918 차단)
- DARTSource: SecretStr params dict 분리 (CWE-532 로그 노출 차단)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

import feedparser
import httpx
from pydantic import SecretStr

from backend.models.news import NewsItem, NewsSource

logger = logging.getLogger(__name__)


@runtime_checkable
class NewsSourceAdapter(Protocol):
    """단일 source 의 fetch 인터페이스. mock 친화."""

    name: NewsSource

    async def fetch(self) -> list[NewsItem]: ...


# ─────────────────────────────────────────────
# RSSSource — HOST_ALLOWLIST + https-only
# ─────────────────────────────────────────────


class RSSSource:
    """RSS 피드 어댑터. SSRF 방지 — host allowlist + https only."""

    HOST_ALLOWLIST: frozenset[str] = frozenset(
        {
            "rss.hankyung.com",
            "rss.mk.co.kr",
            "www.yna.co.kr",
            "rss.edaily.co.kr",
        }
    )

    def __init__(
        self,
        name: NewsSource,
        feed_url: str,
        http: httpx.AsyncClient,
    ) -> None:
        if not feed_url.startswith("https://"):
            raise ValueError(f"non-https feed_url blocked: {feed_url}")
        host = urlparse(feed_url).hostname or ""
        if host not in self.HOST_ALLOWLIST:
            raise ValueError(
                f"host not in allowlist: {host} (allowed: {sorted(self.HOST_ALLOWLIST)})"
            )
        self.name = name
        self.feed_url = feed_url
        self._http = http

    async def fetch(self) -> list[NewsItem]:
        resp = await self._http.get(self.feed_url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        items: list[NewsItem] = []
        now = datetime.now(timezone.utc)
        for entry in parsed.entries[:50]:
            try:
                source_id = self._extract_source_id(entry)
                published_at = self._extract_published(entry, now)
                items.append(
                    NewsItem(
                        source=self.name,
                        source_id=source_id,
                        title=getattr(entry, "title", "")[:512] or "(no title)",
                        body=getattr(entry, "summary", "")[:20_000],
                        url=getattr(entry, "link", "") or self.feed_url,
                        published_at=published_at,
                        fetched_at=now,
                        tags=(),
                    )
                )
            except Exception as exc:  # parse fail-soft
                logger.warning("RSSSource %s entry skipped: %s", self.name.value, exc)
                continue
        return items

    @staticmethod
    def _extract_source_id(entry) -> str:
        # guid → id → link 순. SourceIdStr 패턴 외 문자는 제거.
        raw = (
            getattr(entry, "id", None)
            or getattr(entry, "guid", None)
            or getattr(entry, "link", None)
            or ""
        )
        # https://x/y?z 같은 URL 도 SourceIdStr 패턴 (`^[\w\-/.]+$`) 에 안 맞으므로 hash
        import hashlib

        if raw and not all(c.isalnum() or c in "-_/." for c in raw):
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return raw[:256] or "unknown"

    @staticmethod
    def _extract_published(entry, fallback: datetime) -> datetime:
        ts = getattr(entry, "published_parsed", None) or getattr(
            entry, "updated_parsed", None
        )
        if ts is None:
            return fallback
        # struct_time → datetime (UTC 가정)
        return datetime(*ts[:6], tzinfo=timezone.utc)


# ─────────────────────────────────────────────
# DARTSource — SecretStr params dict 분리
# ─────────────────────────────────────────────


class DARTSource:
    """OpenDART 공시 목록 어댑터. crtfc_key 는 SecretStr."""

    name: NewsSource = NewsSource.DART
    base_url: str = "https://opendart.fss.or.kr/api/list.json"

    def __init__(self, api_key: SecretStr, http: httpx.AsyncClient) -> None:
        if not isinstance(api_key, SecretStr):
            raise TypeError("api_key must be SecretStr (CWE-532)")
        self._api_key = api_key
        self._http = http

    async def fetch(self) -> list[NewsItem]:
        try:
            resp = await self._http.get(
                self.base_url,
                params={
                    "crtfc_key": self._api_key.get_secret_value(),
                    "page_count": 50,
                },
            )
        except Exception as exc:
            # 예외 메시지에서 query string 마스킹 — log 노출 차단
            masked = self._mask(str(exc))
            logger.warning("DARTSource fetch failed: %s", masked)
            raise

        if resp.status_code == 401:
            logger.error("DARTSource auth failed (401) — key 회전 필요")
            return []
        if resp.status_code == 429:
            logger.warning("DARTSource rate-limited (429) — backoff")
            return []
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "000":
            logger.warning("DARTSource non-OK status: %s", data.get("message"))
            return []

        now = datetime.now(timezone.utc)
        items: list[NewsItem] = []
        for entry in (data.get("list") or [])[:50]:
            try:
                rcept_no = entry.get("rcept_no", "")
                if not rcept_no:
                    continue
                title = (entry.get("report_nm") or "").strip()[:512] or "(no title)"
                corp = entry.get("corp_name") or ""
                # corp_name 결합 — title 에 회사명 prefix 노출
                if corp and not title.startswith(corp):
                    title = f"[{corp}] {title}"[:512]
                items.append(
                    NewsItem(
                        source=self.name,
                        source_id=rcept_no,
                        title=title,
                        body="",
                        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
                        published_at=self._parse_dart_date(
                            entry.get("rcept_dt"), now
                        ),
                        fetched_at=now,
                        tags=(),
                    )
                )
            except Exception as exc:
                logger.warning("DARTSource entry skipped: %s", self._mask(str(exc)))
                continue
        return items

    @staticmethod
    def _mask(text: str) -> str:
        import re

        return re.sub(r"crtfc_key=[^&\s]+", "crtfc_key=***", text)

    @staticmethod
    def _parse_dart_date(raw: Optional[str], fallback: datetime) -> datetime:
        if not raw or len(raw) != 8:
            return fallback
        try:
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return fallback


__all__ = ["NewsSourceAdapter", "RSSSource", "DARTSource"]
