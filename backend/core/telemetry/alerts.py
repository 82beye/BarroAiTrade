"""BAR-73 — Alert 정책 (Grafana alert rules YAML 빌더 + IaC 검증)."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertPolicy(BaseModel):
    """단일 alert 정책 — Grafana 변환용."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    severity: AlertSeverity
    expr: str = Field(min_length=1)              # PromQL
    for_duration: str = Field(default="5m")
    summary: str = ""
    runbook_url: str = ""

    def to_yaml_dict(self) -> dict[str, Any]:
        return {
            "alert": self.name,
            "expr": self.expr,
            "for": self.for_duration,
            "labels": {"severity": self.severity.value},
            "annotations": {
                "summary": self.summary or self.name,
                "runbook_url": self.runbook_url,
            },
        }


def build_alerts_yaml(policies: list[AlertPolicy]) -> dict[str, Any]:
    """Grafana / Prometheus 호환 alert 그룹 dict."""
    return {
        "groups": [
            {
                "name": "barro_ai_trade",
                "rules": [p.to_yaml_dict() for p in policies],
            }
        ]
    }


__all__ = ["AlertSeverity", "AlertPolicy", "build_alerts_yaml"]
