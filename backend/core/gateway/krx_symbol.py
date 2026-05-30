"""KRX 종목코드 검증 — 고도화 Phase 1 #4 (2026-05-30).

KRX 6자리 코드는 '숫자만'이 아니다. 신주인수권증서/권리락 등 임시코드는
5번째 자리에 영문 대문자를 포함한다 (예: 0193W0, 00806K, 0154F0).
기존 `symbol.isdigit()` 가정은 이들을 거부해 주문 직전 ValueError 를 유발했다
(2026-05-29 0193W0 buy FAILED). 포맷 검증만 정확히 하고, 길이/문자집합은
엄격히 유지해 오타·깨진 코드의 실주문은 계속 차단한다.
"""
from __future__ import annotations

import re
from typing import Optional

# 6자리, 대문자 영숫자만 (소문자/특수문자/길이≠6 거부)
_KRX_SYMBOL_RE = re.compile(r"^[0-9A-Z]{6}$")


def is_valid_krx_symbol(symbol: Optional[str]) -> bool:
    """KRX 종목코드 포맷 유효성 — 6자리 [0-9A-Z]."""
    return bool(symbol) and _KRX_SYMBOL_RE.fullmatch(symbol) is not None
