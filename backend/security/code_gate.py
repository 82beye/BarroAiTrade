"""BAR-70 — AI 생성 코드 PR 게이트 정책 (Python).

GitHub Actions workflow + 메타 정책. 운영 시 PR 라벨 검증 hook.
"""
from __future__ import annotations

import re
from typing import Iterable


REQUIRED_AI_LABEL = "ai-generated"
AREA_LABELS = frozenset(
    {
        "area:money",
        "area:security",
        "area:strategy",
        "area:data",
        "area:risk",
        "area:ui",
        "area:repo",
    }
)
PHASE_LABELS = frozenset({f"phase:{i}" for i in range(7)})
PRIORITY_LABELS = frozenset({"priority:p0", "priority:p1", "priority:p2"})


def is_ai_generated(labels: Iterable[str]) -> bool:
    return REQUIRED_AI_LABEL in set(labels)


def has_area_label(labels: Iterable[str]) -> bool:
    return bool(set(labels) & AREA_LABELS)


def has_phase_label(labels: Iterable[str]) -> bool:
    return bool(set(labels) & PHASE_LABELS)


def has_priority_label(labels: Iterable[str]) -> bool:
    return bool(set(labels) & PRIORITY_LABELS)


def validate_pr_labels(labels: Iterable[str]) -> tuple[bool, list[str]]:
    """PR 라벨 정책 검증."""
    errors: list[str] = []
    label_set = set(labels)
    if not (label_set & AREA_LABELS):
        errors.append("missing area:* label")
    if not (label_set & PHASE_LABELS):
        errors.append("missing phase:* label")
    if not (label_set & PRIORITY_LABELS):
        errors.append("missing priority:* label")
    return (not errors, errors)


# 자금흐름 코드 검사 — Decimal 미사용 의심 패턴
_FLOAT_MONEY_PATTERN = re.compile(
    r"(price|qty|quantity|balance|pnl|amount)\s*[:=]\s*float\b"
)


def detect_float_money(source: str) -> list[int]:
    """source 내 'price: float' 등 area:money 위반 의심 라인 번호 반환."""
    hits: list[int] = []
    for idx, line in enumerate(source.splitlines(), start=1):
        if _FLOAT_MONEY_PATTERN.search(line):
            hits.append(idx)
    return hits


__all__ = [
    "REQUIRED_AI_LABEL",
    "AREA_LABELS",
    "PHASE_LABELS",
    "PRIORITY_LABELS",
    "is_ai_generated",
    "has_area_label",
    "has_phase_label",
    "has_priority_label",
    "validate_pr_labels",
    "detect_float_money",
]
