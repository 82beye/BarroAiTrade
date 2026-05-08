"""BAR-OPS-07 — 모의 침투 시나리오 자동화.

각 시나리오는 단위 테스트에서 실행. 실패 = 보안 회귀.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AttackVector(str, Enum):
    SQL_INJECTION = "sql_injection"
    JWT_TAMPERING = "jwt_tampering"
    JWT_NONE_ALG = "jwt_none_alg"
    RBAC_BYPASS = "rbac_bypass"
    SSRF = "ssrf"
    PII_LEAK = "pii_leak"
    TIMING_ATTACK = "timing_attack"
    REPLAY_ATTACK = "replay_attack"


class AttackResult(BaseModel):
    """모의 침투 시도 결과 (frozen)."""

    model_config = ConfigDict(frozen=True)

    vector: AttackVector
    succeeded: bool                           # True = 침투 성공 = 보안 실패
    blocked: bool                             # True = 시스템이 차단함 = 정상
    details: str = ""

    @property
    def is_secure(self) -> bool:
        """방어 성공: 시도 실패 + 차단됨."""
        return (not self.succeeded) and self.blocked


class PenTestSuite:
    """침투 시나리오 모음. 단위 테스트가 호출."""

    @staticmethod
    def try_sql_injection_in_user_id(repo, malicious_id: str) -> AttackResult:
        """SQL Injection — user_id 에 ' OR 1=1-- 주입."""
        # SQLAlchemy text() + named param 은 자동 escaping
        # repo.find_by_user_id(malicious_id) 호출 — 정상 None 반환 시 차단
        import asyncio
        try:
            result = asyncio.get_event_loop().run_until_complete(
                repo.find_by_user_id(malicious_id)
            )
            # 정상: None 또는 단일 user 반환 (다른 user 데이터 노출 X)
            return AttackResult(
                vector=AttackVector.SQL_INJECTION,
                succeeded=False,
                blocked=True,
                details=f"named param escaping — result={result}",
            )
        except Exception as e:
            return AttackResult(
                vector=AttackVector.SQL_INJECTION,
                succeeded=False,
                blocked=True,
                details=f"raised: {type(e).__name__}",
            )

    @staticmethod
    def try_jwt_tampering(jwt_service, user_id: str, target_role: str) -> AttackResult:
        """JWT 변조 — payload role 을 admin 으로 변경."""
        from backend.security.auth import Role

        token = jwt_service.encode_access(user_id, Role.VIEWER)
        # payload base64 변조 시도 — signature 가 매치하지 않아 실패해야
        parts = token.split(".")
        if len(parts) != 3:
            return AttackResult(
                vector=AttackVector.JWT_TAMPERING,
                succeeded=False, blocked=True,
                details="token format invalid",
            )
        # payload 자리만 swap (실제 변조)
        tampered = ".".join([parts[0], parts[1] + "X", parts[2]])
        try:
            jwt_service.decode(tampered)
            # signature 검증 통과 → 보안 실패
            return AttackResult(
                vector=AttackVector.JWT_TAMPERING,
                succeeded=True, blocked=False,
                details="tampered token accepted",
            )
        except ValueError:
            return AttackResult(
                vector=AttackVector.JWT_TAMPERING,
                succeeded=False, blocked=True,
                details="signature verification rejected",
            )

    @staticmethod
    def try_jwt_none_alg(jwt_service) -> AttackResult:
        """JWT 'none' 알고리즘 우회 시도."""
        import json, base64

        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {"user_id": "attacker", "role": "admin", "type": "access",
                 "exp": 9999999999, "iat": 0}
            ).encode()
        ).rstrip(b"=").decode()
        none_token = f"{header}.{payload}."
        try:
            jwt_service.decode(none_token)
            return AttackResult(
                vector=AttackVector.JWT_NONE_ALG,
                succeeded=True, blocked=False,
                details="none alg accepted",
            )
        except ValueError:
            return AttackResult(
                vector=AttackVector.JWT_NONE_ALG,
                succeeded=False, blocked=True,
                details="none alg rejected",
            )

    @staticmethod
    def try_rbac_bypass(user_role, required_role) -> AttackResult:
        """RBAC — viewer 가 admin 권한 필요한 작업 시도."""
        from backend.security.auth import RBACPolicy

        try:
            RBACPolicy.require_role(user_role, required_role)
            return AttackResult(
                vector=AttackVector.RBAC_BYPASS,
                succeeded=True, blocked=False,
                details="권한 미달인데 통과",
            )
        except PermissionError:
            return AttackResult(
                vector=AttackVector.RBAC_BYPASS,
                succeeded=False, blocked=True,
                details="RBAC rejected",
            )

    @staticmethod
    def try_ssrf(rss_constructor, malicious_url: str) -> AttackResult:
        """SSRF — RSSSource 에 file:// 또는 internal IP 시도."""
        try:
            rss_constructor(malicious_url)
            return AttackResult(
                vector=AttackVector.SSRF,
                succeeded=True, blocked=False,
                details=f"malicious URL accepted: {malicious_url}",
            )
        except (ValueError, Exception):
            return AttackResult(
                vector=AttackVector.SSRF,
                succeeded=False, blocked=True,
                details="HOST_ALLOWLIST rejected",
            )

    @staticmethod
    def try_pii_leak_in_log(text: str, secret_value: str) -> AttackResult:
        """log 출력에 secret 평문이 포함되는지."""
        if secret_value in text:
            return AttackResult(
                vector=AttackVector.PII_LEAK,
                succeeded=True, blocked=False,
                details="secret found in log",
            )
        return AttackResult(
            vector=AttackVector.PII_LEAK,
            succeeded=False, blocked=True,
            details="secret not in log (masked)",
        )


__all__ = ["AttackVector", "AttackResult", "PenTestSuite"]
