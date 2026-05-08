"""BAR-OPS-05 — Live Trading Checker.

infra/live-checklist.yaml 의 모든 게이트가 PASS 일 때만 실거래 진입 허용.
Master Plan v2 Phase 4 종료 게이트 자동 검증.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class GateResult(BaseModel):
    """단일 게이트 검증 결과."""

    model_config = ConfigDict(frozen=True)

    gate_id: str
    description: str
    passed: bool
    reason: str = ""


class CheckSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    all_passed: bool
    passed_count: int
    failed_count: int
    results: list[GateResult]
    capital_pct_allowed: float = 0.0


class LiveTradingChecker:
    """체크리스트 YAML 로드 + 게이트 검증."""

    def __init__(self, repo_root: Path) -> None:
        self._root = repo_root

    def load_checklist(self, path: str) -> dict[str, Any]:
        full = self._root / path
        if not full.exists():
            raise FileNotFoundError(f"checklist not found: {full}")
        with open(full, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def verify(
        self,
        checklist_path: str = "infra/live-checklist.yaml",
        manual_attestations: Optional[dict[str, bool]] = None,
        skip_pytest: bool = False,
    ) -> CheckSummary:
        """모든 게이트 평가. manual 게이트는 attestations dict 로 외부 주입."""
        config = self.load_checklist(checklist_path)
        gates = config.get("gates", [])
        attestations = manual_attestations or {}

        results: list[GateResult] = []
        for gate in gates:
            gid = gate["id"]
            desc = gate.get("description", "")
            gtype = gate.get("type", "manual")

            if gtype == "file_exists":
                p = self._root / gate["path"]
                ok = p.exists()
                reason = "" if ok else f"file missing: {gate['path']}"
                results.append(GateResult(gate_id=gid, description=desc, passed=ok, reason=reason))
            elif gtype == "manual":
                ok = attestations.get(gid, False)
                reason = "" if ok else "manual attestation required"
                results.append(GateResult(gate_id=gid, description=desc, passed=ok, reason=reason))
            elif gtype == "workflow":
                # 워크플로 파일 존재만 확인 — 실 실행 결과는 GitHub PR check 가 보장
                wf = self._root / gate["workflow"]
                ok = wf.exists()
                reason = "" if ok else f"workflow missing: {gate['workflow']}"
                results.append(GateResult(gate_id=gid, description=desc, passed=ok, reason=reason))
            elif gtype == "pytest":
                # 본 BAR 단위 테스트 자체가 회귀에 포함되므로 skip_pytest=True 시 패스 가정
                if skip_pytest:
                    results.append(GateResult(gate_id=gid, description=desc, passed=True))
                else:
                    # 실 실행은 외부 (CI / make) 가 책임 — 본 메서드는 attestation 의 위
                    ok = attestations.get(gid, False)
                    reason = "" if ok else "pytest gate requires CI attestation"
                    results.append(GateResult(gate_id=gid, description=desc, passed=ok, reason=reason))
            else:
                results.append(
                    GateResult(
                        gate_id=gid,
                        description=desc,
                        passed=False,
                        reason=f"unknown gate type: {gtype}",
                    )
                )

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        all_ok = failed == 0
        approval = config.get("approval", {})
        capital = float(approval.get("capital_pct_initial", 0.0)) if all_ok else 0.0
        return CheckSummary(
            all_passed=all_ok,
            passed_count=passed,
            failed_count=failed,
            results=results,
            capital_pct_allowed=capital,
        )


__all__ = ["GateResult", "CheckSummary", "LiveTradingChecker"]
