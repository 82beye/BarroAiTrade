"""
backend.legacy_scalping — ai-trade 흡수 모듈 (BAR-40)

zero-modification mirror 원칙 — legacy 코드 자체는 수정 금지.
BarroAiTrade 표준 시스템과의 호환은 _adapter (BAR-41) 를 통해.
"""

from backend.legacy_scalping._adapter import (
    LegacySignalSchema,
    to_entry_signal,
    to_legacy_dict,
)

__all__ = [
    "LegacySignalSchema",
    "to_entry_signal",
    "to_legacy_dict",
]
