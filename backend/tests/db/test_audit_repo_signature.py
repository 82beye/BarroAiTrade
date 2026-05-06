"""BAR-56 — audit_repo 의 호출자 컨트랙트 (시그니처) 보존."""
from __future__ import annotations

import inspect

import pytest


def test_audit_repo_module_has_singleton():
    from backend.db.repositories import audit_repo as ar
    assert hasattr(ar, "audit_repo")
    assert ar.audit_repo.__class__.__name__ == "AuditRepository"


def test_insert_signature_unchanged():
    from backend.db.repositories.audit_repo import AuditRepository
    sig = inspect.signature(AuditRepository.insert)
    params = sig.parameters
    expected = {
        "self",
        "event_type",
        "symbol",
        "market_type",
        "quantity",
        "price",
        "pnl",
        "strategy_id",
        "metadata",
        "created_at",
    }
    assert set(params) == expected


def test_find_recent_signature_unchanged():
    from backend.db.repositories.audit_repo import AuditRepository
    sig = inspect.signature(AuditRepository.find_recent)
    assert set(sig.parameters) == {"self", "limit", "event_type"}


def test_audit_repo_methods_are_async():
    from backend.db.repositories.audit_repo import AuditRepository
    assert inspect.iscoroutinefunction(AuditRepository.insert)
    assert inspect.iscoroutinefunction(AuditRepository.find_recent)
