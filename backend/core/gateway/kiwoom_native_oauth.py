"""BAR-OPS-10 — 키움 자체 OpenAPI 네이티브 OAuth Manager.

KIS Open Trading API 와 별개로 키움증권이 운영하는 자체 REST OpenAPI.

엔드포인트 (공식): https://api.kiwoom.com/oauth2/token
요청 바디: {"grant_type": "client_credentials", "appkey": "...", "secretkey": "..."}
응답:
  - return_code: int (0=성공)
  - return_msg: str
  - token: str (Bearer token)
  - token_type: str ("bearer")
  - expires_dt: str (만료일시 YYYYMMDDHHMMSS)

BAR-OPS-31 — 공유 토큰 캐시:
키움 자체 OpenAPI 는 appkey 당 토큰 1개만 유효(새 발급 시 이전 토큰 무효화)하고
발급 빈도 제한이 있다. 여러 프로세스(API 서버 routes, ST 데몬, intraday 데몬,
telegram bot, ohlcv 캐시 스크립트)가 각자 발급하면 서로의 토큰을 무효화(rc=3, 8005)
하며 재발급을 폭주시켜 결국 발급이 거부(8001)된다. 이를 막기 위해 발급 토큰을
파일 캐시(파일락 보호)에 저장하여 모든 프로세스가 단일 토큰을 공유·재사용한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)

# 공유 토큰 캐시 기본 경로 — repo_root/data/.kiwoom_native_token.json
# (data/ 는 .gitignore 에 포함, 파일은 0600 권한으로 생성)
_DEFAULT_CACHE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / ".kiwoom_native_token.json"
)
_FILE_LOCK_TIMEOUT = 15.0   # 파일락 획득 대기 한도(초)
_FILE_LOCK_POLL = 0.1       # 파일락 폴링 간격(초)


class _FileLock:
    """fcntl.flock 기반 프로세스 간 배타 락 (이벤트 루프 비차단 폴링).

    Windows 등 fcntl 미지원 환경에서는 no-op 으로 동작(in-process 락만 적용).
    """

    def __init__(self, path: Path, timeout: float = _FILE_LOCK_TIMEOUT) -> None:
        self._path = path
        self._timeout = timeout
        self._fd: Optional[int] = None

    async def __aenter__(self) -> "_FileLock":
        try:
            import fcntl
        except ImportError:
            return self  # fcntl 미지원 — in-process asyncio.Lock 으로만 보호
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o600)
        waited = 0.0
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if waited >= self._timeout:
                    logger.warning("토큰 파일락 타임아웃(%.1fs) — 락 없이 진행", self._timeout)
                    return self
                await asyncio.sleep(_FILE_LOCK_POLL)
                waited += _FILE_LOCK_POLL

    async def __aexit__(self, *exc) -> None:
        if self._fd is not None:
            try:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            os.close(self._fd)
            self._fd = None


@dataclass(frozen=True)
class KiwoomNativeToken:
    access_token: SecretStr
    token_type: str
    expires_at: datetime


class KiwoomNativeOAuth:
    """키움 자체 OpenAPI OAuth (api.kiwoom.com)."""

    DEFAULT_BASE_URL = "https://api.kiwoom.com"

    def __init__(
        self,
        app_key: SecretStr,
        app_secret: SecretStr,
        base_url: str = DEFAULT_BASE_URL,
        http_client: Optional[httpx.AsyncClient] = None,
        refresh_margin_seconds: int = 1800,
        use_shared_cache: bool = True,
        token_cache_path: Optional[Path] = None,
    ) -> None:
        if not isinstance(app_key, SecretStr) or not isinstance(app_secret, SecretStr):
            raise TypeError("credentials must be SecretStr (CWE-798)")
        if not base_url.startswith("https://"):
            raise ValueError("base_url must be https-only (CWE-918)")
        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._http = http_client
        self._margin = refresh_margin_seconds
        self._token: Optional[KiwoomNativeToken] = None
        self._lock = asyncio.Lock()
        # 직전에 거부(rc=3)된 토큰 문자열 — 캐시 재채택 방지용
        self._rejected_token: Optional[str] = None

        # ── 공유 토큰 캐시 설정 ──────────────────────────────
        # 환경변수 KIWOOM_TOKEN_CACHE_DISABLED=1 로 전역 비활성 가능
        if os.environ.get("KIWOOM_TOKEN_CACHE_DISABLED", "") == "1":
            use_shared_cache = False
        self._use_cache = use_shared_cache
        if token_cache_path is not None:
            self._cache_path = Path(token_cache_path)
        else:
            env_path = os.environ.get("KIWOOM_TOKEN_CACHE_PATH", "")
            self._cache_path = Path(env_path) if env_path else _DEFAULT_CACHE_PATH
        # appkey+secret 지문 — 자격증명 변경 시 캐시 자동 무효화
        self._fingerprint = hashlib.sha256(
            (app_key.get_secret_value() + ":" + app_secret.get_secret_value()).encode()
        ).hexdigest()[:16]

    @property
    def base_url(self) -> str:
        return self._base_url

    def _is_valid(self, token: Optional[KiwoomNativeToken], now: datetime) -> bool:
        return bool(
            token and (token.expires_at - now).total_seconds() > self._margin
        )

    async def get_token(self) -> KiwoomNativeToken:
        async with self._lock:
            now = datetime.now()
            # 1) in-process 캐시 우선
            if self._is_valid(self._token, now):
                return self._token  # type: ignore[return-value]
            # 2) 공유 캐시 비활성 — 기존 동작(직접 발급)
            if not self._use_cache:
                return await self._issue_and_set(now)
            # 3) 공유 파일 캐시 — 파일락 하에 더블체크 후 1회만 발급
            async with _FileLock(self._cache_path.with_suffix(".lock")):
                cached = self._read_cache()
                if (
                    self._is_valid(cached, now)
                    and cached.access_token.get_secret_value() != self._rejected_token  # type: ignore[union-attr]
                ):
                    # 다른 프로세스가 이미 발급해둔 유효 토큰 채택 (발급 폭주 방지)
                    self._token = cached
                    self._rejected_token = None
                    logger.info("OAuth 공유 캐시 토큰 채택 (만료: %s)",
                                cached.expires_at.isoformat())  # type: ignore[union-attr]
                    return cached
                token = await self._issue_and_set(now)
                self._write_cache(token)
                return token

    async def _issue_and_set(self, now: datetime) -> KiwoomNativeToken:
        logger.info("OAuth 토큰 발급/갱신 시도 (기존 만료: %s)",
                    self._token.expires_at.isoformat() if self._token else "없음")
        self._token = await self._issue(now)
        self._rejected_token = None
        logger.info("OAuth 토큰 갱신 완료 (만료: %s)", self._token.expires_at.isoformat())
        return self._token

    def invalidate_token(self) -> None:
        """토큰 무효화 — API에서 인증 실패(rc=3) 시 호출.

        거부된 토큰 문자열을 기록해, 다음 get_token 시 공유 캐시에 남아있는
        동일 토큰을 다시 채택하지 않고 재발급하도록 한다(무효화 캐스케이드 방지).
        """
        if self._token is not None:
            self._rejected_token = self._token.access_token.get_secret_value()
        self._token = None
        logger.warning("OAuth 토큰 무효화됨 — 다음 호출 시 재발급")

    # ── 공유 캐시 I/O ──────────────────────────────────────────
    def _read_cache(self) -> Optional[KiwoomNativeToken]:
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        entry = data.get(self._fingerprint)
        if not entry:
            return None
        try:
            return KiwoomNativeToken(
                access_token=SecretStr(entry["token"]),
                token_type=entry.get("token_type", "Bearer"),
                expires_at=datetime.fromisoformat(entry["expires_at"]),
            )
        except (KeyError, ValueError, TypeError):
            return None

    def _write_cache(self, token: KiwoomNativeToken) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            # 기존 항목(다른 appkey) 보존하며 갱신
            data = {}
            try:
                with open(self._cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                data = {}
            data[self._fingerprint] = {
                "token": token.access_token.get_secret_value(),
                "token_type": token.token_type,
                "expires_at": token.expires_at.isoformat(),
                "issued_at": datetime.now().isoformat(),
            }
            # 0600 권한으로 원자적 교체 (CWE-732)
            tmp = self._cache_path.with_suffix(".tmp")
            fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            finally:
                os.replace(tmp, self._cache_path)
            try:
                os.chmod(self._cache_path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            logger.warning("토큰 캐시 쓰기 실패(무시): %s", type(exc).__name__)

    async def _issue(self, now: datetime) -> KiwoomNativeToken:
        url = f"{self._base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key.get_secret_value(),
            "secretkey": self._app_secret.get_secret_value(),
        }
        owns = self._http is None
        client = self._http or httpx.AsyncClient(timeout=15)
        _retries = 3
        _retry_delay = 2.0
        try:
            for attempt in range(_retries):
                try:
                    resp = await client.post(
                        url,
                        headers={"Content-Type": "application/json;charset=UTF-8"},
                        json=body,
                    )
                    if resp.status_code == 429 and attempt < _retries - 1:
                        wait = _retry_delay * (attempt + 1)
                        logger.warning(
                            "oauth 429 rate-limit — %.1fs 후 재시도 (%d/%d)",
                            wait, attempt + 1, _retries,
                        )
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429 and attempt < _retries - 1:
                        await asyncio.sleep(_retry_delay * (attempt + 1))
                        continue
                    logger.error(
                        "kiwoom-native token issue failed: status=%s url=%s",
                        exc.response.status_code, url,
                    )
                    raise
                except Exception as exc:
                    if attempt < _retries - 1:
                        await asyncio.sleep(_retry_delay * (attempt + 1))
                        continue
                    logger.error("kiwoom-native token issue error: %s", type(exc).__name__)
                    raise
        finally:
            if owns:
                await client.aclose()

        rc = data.get("return_code")
        if rc != 0:
            raise RuntimeError(
                f"kiwoom-native token error: rc={rc} msg={data.get('return_msg')}"
            )

        token = data.get("token")
        if not token:
            raise RuntimeError("kiwoom-native: token field missing in response")

        expires_dt_str = data.get("expires_dt", "")
        try:
            expires_at = datetime.strptime(expires_dt_str, "%Y%m%d%H%M%S")
        except (ValueError, TypeError):
            expires_at = now + timedelta(hours=24)

        return KiwoomNativeToken(
            access_token=SecretStr(token),
            token_type=data.get("token_type", "Bearer"),
            expires_at=expires_at,
        )


__all__ = ["KiwoomNativeOAuth", "KiwoomNativeToken"]
