"""고도화 Phase 1 #4 — KRX 종목코드 검증 (0193W0 FAILED 재발 방지)."""
from __future__ import annotations

import pytest

from backend.core.gateway.krx_symbol import is_valid_krx_symbol


class TestValidKrxSymbol:
    @pytest.mark.parametrize("sym", [
        "005930",  # 삼성전자
        "0193W0",  # 5/29 FAILED — 영문 포함 임시코드
        "00806K",  # 신주인수권 류
        "0154F0",
        "373220",
        "ABCDEF",  # 형식상 6 대문자영숫자 (포맷만 검증)
    ])
    def test_accepts_valid(self, sym):
        assert is_valid_krx_symbol(sym) is True

    @pytest.mark.parametrize("sym", [
        None, "", "12345", "1234567", "abc123",  # 소문자
        "0193w0",  # 소문자 영문
        "01_3W0",  # 특수문자
        "005930 ", " 005930",  # 공백
        "00593",
    ])
    def test_rejects_invalid(self, sym):
        assert is_valid_krx_symbol(sym) is False

    def test_0193W0_no_longer_rejected_by_isdigit(self):
        # 회귀: 기존 isdigit() 는 0193W0 거부 → 본 헬퍼는 허용
        assert "0193W0".isdigit() is False
        assert is_valid_krx_symbol("0193W0") is True
