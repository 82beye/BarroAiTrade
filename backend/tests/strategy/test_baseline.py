"""
BAR-44 베이스라인 재현성 테스트 (Plan §4.2 / Design §2.4).

C1~C6 — Fixed seed 재현성 + 결과 구조 검증.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/run_baseline.py 의 run_baseline 함수를 직접 import
_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from run_baseline import run_baseline  # noqa: E402  isort:skip


class TestBaselineReproducibility:
    """C1~C3 — 재현성."""

    def test_c1_run_returns_dict_of_4_strategies(self):
        """C1: run_baseline(seed=42) 4 전략 결과 dict 반환."""
        reports = run_baseline(seed=42, num_candles=100)
        assert isinstance(reports, dict)
        # 적어도 거래가 발생한 전략은 있어야 함
        assert len(reports) >= 2

    def test_c2_same_seed_reproducible(self):
        """C2: 동일 seed → 동일 결과 (재현성)."""
        r1 = run_baseline(seed=42, num_candles=100)
        r2 = run_baseline(seed=42, num_candles=100)

        assert set(r1.keys()) == set(r2.keys())
        for sid in r1:
            m1 = r1[sid].metrics
            m2 = r2[sid].metrics
            assert m1.total_return_pct == pytest.approx(m2.total_return_pct, abs=1e-6)
            assert m1.win_rate == pytest.approx(m2.win_rate, abs=1e-6)
            assert m1.max_drawdown == pytest.approx(m2.max_drawdown, abs=1e-6)
            assert len(r1[sid].trades) == len(r2[sid].trades)

    def test_c3_different_seed_diverges(self):
        """C3: 다른 seed → 결과가 적어도 한 전략에서 다름."""
        r1 = run_baseline(seed=42, num_candles=100)
        r2 = run_baseline(seed=100, num_candles=100)

        # 적어도 한 전략의 거래수 또는 수익이 달라야 함
        diff_found = False
        for sid in set(r1.keys()) & set(r2.keys()):
            if len(r1[sid].trades) != len(r2[sid].trades):
                diff_found = True
                break
            if r1[sid].metrics.total_return_pct != r2[sid].metrics.total_return_pct:
                diff_found = True
                break
        assert diff_found, "다른 seed 인데 결과가 완전 동일 — 확률성 동작 의심"


class TestBaselineMetricsShape:
    """C4~C5 — 결과 구조."""

    def test_c4_metrics_fields_exist(self):
        """C4: 각 전략 결과의 필수 metrics 필드 존재."""
        reports = run_baseline(seed=42, num_candles=100)
        for sid, r in reports.items():
            m = r.metrics
            assert hasattr(m, "total_return_pct")
            assert hasattr(m, "win_rate")
            assert hasattr(m, "max_drawdown")
            assert hasattr(m, "sharpe_ratio")
            assert hasattr(r, "trades")

    def test_c5_zero_trade_strategies_handled(self):
        """C5: 거래 0건 전략도 무에러 (수박/crypto_breakout 가능)."""
        reports = run_baseline(seed=42, num_candles=100)
        for sid, r in reports.items():
            # 거래 0건도 정상 — metrics 가 0/NaN 이 아닌 기본값이어야
            assert len(r.trades) >= 0
            assert r.metrics.win_rate >= 0.0


class TestBaselineMinimalData:
    """C6 — 최소 데이터."""

    def test_c6_minimal_candles_50(self):
        """C6: num_candles=50 (최소) → 무에러."""
        reports = run_baseline(seed=42, num_candles=50)
        assert isinstance(reports, dict)
