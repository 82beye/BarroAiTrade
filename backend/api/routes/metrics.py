"""
BAR-43: Prometheus 메트릭 노출 엔드포인트.

GET /metrics → Prometheus exposition format text.

TODO(BAR-69): admin-only 인증 추가 (Phase 5 보안 강화).
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from backend.core.monitoring.metrics import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)

router = APIRouter(tags=["monitoring"])


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint() -> Response:
    """Prometheus exposition format.

    prometheus_client 미설치 시 fallback stub (`# prometheus_client not installed`).
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
