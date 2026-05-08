"""BAR-OPS-05 — LiveTradingChecker (10 cases)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.security.live_trading_checker import LiveTradingChecker


@pytest.fixture
def repo_root(tmp_path) -> Path:
    """가상 repo — 일부 파일만 존재."""
    (tmp_path / "monitoring").mkdir()
    (tmp_path / "monitoring" / "alerts.yaml").write_text("groups: []")
    (tmp_path / "RUNBOOK.md").write_text("# runbook")
    (tmp_path / "DEPLOYMENT.md").write_text("# deploy")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "security-scan.yml").write_text("name: x")
    return tmp_path


@pytest.fixture
def checklist_path(repo_root) -> str:
    """단순 체크리스트 yaml 작성."""
    cfg = {
        "gates": [
            {"id": "alerts_iac_deployed", "description": "alerts", "type": "file_exists", "path": "monitoring/alerts.yaml"},
            {"id": "runbook_exists", "description": "runbook", "type": "file_exists", "path": "RUNBOOK.md"},
            {"id": "deployment_doc_exists", "description": "deploy", "type": "file_exists", "path": "DEPLOYMENT.md"},
            {"id": "owasp_top10_passed", "description": "owasp", "type": "workflow", "workflow": ".github/workflows/security-scan.yml"},
            {"id": "regression_passed", "description": "tests", "type": "pytest", "command": "backend/tests/"},
            {"id": "simulation_3weeks_clean", "description": "sim", "type": "manual"},
            {"id": "missing_file_gate", "description": "missing", "type": "file_exists", "path": "MISSING.md"},
        ],
        "approval": {"capital_pct_initial": 0.05},
    }
    p = repo_root / "infra" / "live-checklist.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return "infra/live-checklist.yaml"


class TestLoadChecklist:
    def test_load_existing(self, repo_root, checklist_path):
        c = LiveTradingChecker(repo_root)
        cfg = c.load_checklist(checklist_path)
        assert "gates" in cfg
        assert len(cfg["gates"]) == 7

    def test_missing_raises(self, repo_root):
        c = LiveTradingChecker(repo_root)
        with pytest.raises(FileNotFoundError):
            c.load_checklist("nonexistent.yaml")


class TestVerify:
    def test_file_exists_pass_and_fail(self, repo_root, checklist_path):
        c = LiveTradingChecker(repo_root)
        summary = c.verify(checklist_path, skip_pytest=True)
        gate_results = {r.gate_id: r for r in summary.results}
        assert gate_results["alerts_iac_deployed"].passed is True
        assert gate_results["missing_file_gate"].passed is False
        assert "missing" in gate_results["missing_file_gate"].reason.lower()

    def test_workflow_passes_when_file_exists(self, repo_root, checklist_path):
        c = LiveTradingChecker(repo_root)
        summary = c.verify(checklist_path, skip_pytest=True)
        gate = next(r for r in summary.results if r.gate_id == "owasp_top10_passed")
        assert gate.passed is True

    def test_manual_default_fails_without_attestation(self, repo_root, checklist_path):
        c = LiveTradingChecker(repo_root)
        summary = c.verify(checklist_path, skip_pytest=True)
        gate = next(r for r in summary.results if r.gate_id == "simulation_3weeks_clean")
        assert gate.passed is False

    def test_manual_attestation_passes(self, repo_root, checklist_path):
        c = LiveTradingChecker(repo_root)
        summary = c.verify(
            checklist_path,
            skip_pytest=True,
            manual_attestations={"simulation_3weeks_clean": True},
        )
        gate = next(r for r in summary.results if r.gate_id == "simulation_3weeks_clean")
        assert gate.passed is True

    def test_pytest_gate_requires_attestation_when_not_skipped(
        self, repo_root, checklist_path
    ):
        c = LiveTradingChecker(repo_root)
        summary = c.verify(checklist_path, skip_pytest=False)
        gate = next(r for r in summary.results if r.gate_id == "regression_passed")
        assert gate.passed is False

    def test_capital_zero_when_any_fail(self, repo_root, checklist_path):
        c = LiveTradingChecker(repo_root)
        # missing_file_gate 가 실패 — 자본 0%
        summary = c.verify(checklist_path, skip_pytest=True)
        assert summary.all_passed is False
        assert summary.capital_pct_allowed == 0.0

    def test_all_pass_yields_capital_5pct(self, repo_root):
        """missing 게이트 제거한 체크리스트 → 모두 PASS → 5% 진입 허가."""
        cfg = {
            "gates": [
                {"id": "runbook_exists", "description": "x", "type": "file_exists", "path": "RUNBOOK.md"},
                {"id": "manual1", "description": "x", "type": "manual"},
            ],
            "approval": {"capital_pct_initial": 0.05},
        }
        path = "infra/passing.yaml"
        full = repo_root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        c = LiveTradingChecker(repo_root)
        summary = c.verify(
            path, manual_attestations={"manual1": True}, skip_pytest=True
        )
        assert summary.all_passed is True
        assert summary.failed_count == 0
        assert summary.capital_pct_allowed == 0.05

    def test_passed_count_correct(self, repo_root, checklist_path):
        c = LiveTradingChecker(repo_root)
        summary = c.verify(checklist_path, skip_pytest=True)
        # 5 file/workflow PASS + 1 missing FAIL + 1 manual FAIL + 0 pytest skip = 7 total
        assert summary.passed_count + summary.failed_count == 7
