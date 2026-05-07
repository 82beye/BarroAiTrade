"""BAR-68 — 감사 로그 해시 체인 무결성.

각 audit_log row 의 hash = sha256(prev_hash + canonical_payload).
30일 chain 검증 스크립트가 BAR-68b 운영에서 cron.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


GENESIS_HASH = "0" * 64


def canonical_serialize(payload: Mapping[str, Any]) -> str:
    """JSON 결정성 직렬화 — sort_keys + separators 고정."""
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def compute_hash(prev_hash: str, payload: Mapping[str, Any]) -> str:
    """hash = sha256(prev_hash + canonical_payload)."""
    if not isinstance(prev_hash, str) or len(prev_hash) != 64:
        raise ValueError("prev_hash must be 64-char hex (sha256)")
    canonical = canonical_serialize(payload)
    return hashlib.sha256(
        (prev_hash + canonical).encode("utf-8")
    ).hexdigest()


def verify_chain(rows: list[Mapping[str, Any]]) -> tuple[bool, int]:
    """rows 순서대로 hash 체인 검증. 실패 시 (False, 실패 idx)."""
    prev = GENESIS_HASH
    for idx, row in enumerate(rows):
        expected = compute_hash(
            prev, {k: v for k, v in row.items() if k != "row_hash"}
        )
        actual = row.get("row_hash")
        if expected != actual:
            return False, idx
        prev = actual
    return True, len(rows)


__all__ = [
    "GENESIS_HASH",
    "canonical_serialize",
    "compute_hash",
    "verify_chain",
]
