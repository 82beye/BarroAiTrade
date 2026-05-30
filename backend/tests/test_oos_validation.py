"""고도화 Phase 3 #10 — OOS 검증 관문 순수 로직 테스트 (I/O 없음)."""
from __future__ import annotations

from scripts._oos_validation import drop1_sign_stable, verdict


class TestDrop1SignStable:
    def test_stable_positive(self):
        # 여러 종목이 고르게 양수 → 1개 제거해도 양수 유지
        per = {"a": [3.0, 2.0], "b": [2.5, 1.5], "c": [3.0], "d": [2.0]}
        assert drop1_sign_stable(per) is True

    def test_unstable_outlier_driven(self):
        # 한 종목이 전체 양수를 좌우 → 제거 시 음수 반전
        per = {"a": [50.0], "b": [-2.0], "c": [-1.5], "d": [-2.0]}
        assert drop1_sign_stable(per) is False

    def test_empty(self):
        assert drop1_sign_stable({}) is False

    def test_robust_negative(self):
        # 고르게 음수 → 1개(최대 음수 기여) 제거해도 음수 유지
        per = {"a": [-3.0], "b": [-2.0], "c": [-2.5], "d": [-2.0]}
        assert drop1_sign_stable(per) is True


class TestVerdict:
    def test_pass_all_criteria(self):
        v, fails = verdict(active=20, trades=50, avg_ret=1.5, drop1_ok=True, holdout_avg=0.8)
        assert v == "PASS" and fails == []

    def test_fail_low_active(self):
        v, fails = verdict(active=10, trades=50, avg_ret=1.5, drop1_ok=True, holdout_avg=0.8)
        assert v == "FAIL" and any("active" in f for f in fails)

    def test_fail_negative_avg(self):
        v, fails = verdict(active=20, trades=50, avg_ret=-0.2, drop1_ok=True, holdout_avg=0.8)
        assert v == "FAIL" and any("avg_ret" in f for f in fails)

    def test_fail_drop1_unstable(self):
        v, fails = verdict(active=20, trades=50, avg_ret=1.5, drop1_ok=False, holdout_avg=0.8)
        assert v == "FAIL" and any("drop1" in f for f in fails)

    def test_fail_holdout_negative(self):
        v, fails = verdict(active=20, trades=50, avg_ret=1.5, drop1_ok=True, holdout_avg=-0.3)
        assert v == "FAIL" and any("holdout" in f for f in fails)

    def test_holdout_none_skipped(self):
        # holdout 표본 부족(None) 이면 그 기준은 미적용
        v, fails = verdict(active=20, trades=50, avg_ret=1.5, drop1_ok=True, holdout_avg=None)
        assert v == "PASS"
