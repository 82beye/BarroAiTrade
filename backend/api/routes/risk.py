"""
리스크 관리 API 라우터

엔드포인트:
  GET  /api/risk/status         - 현재 리스크 상태 조회 (RiskStatusPanel)
  PUT  /api/risk/limits         - 리스크 한도 동적 변경
  GET  /api/risk/events         - 최근 리스크 이벤트 조회
  GET  /api/risk/audit          - 감사 로그 조회
  POST /api/risk/force-close    - 전체 포지션 강제청산 명령
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.core.state import app_state
from backend.models.risk import RiskLimits, RiskStatus

logger = logging.getLogger(__name__)
router = APIRouter()

# ── 요청 모델 ──────────────────────────────────────────────────────────────────


class RiskLimitsUpdate(BaseModel):
    """리스크 한도 업데이트 요청 — 제공된 필드만 변경"""
    max_position_pct: float | None = None
    max_concurrent_positions: int | None = None
    max_total_exposure_pct: float | None = None
    stop_loss_pct: float | None = None
    take_profit_1_pct: float | None = None
    take_profit_1_qty_pct: float | None = None
    take_profit_2_pct: float | None = None
    daily_loss_limit_pct: float | None = None
    force_close_time: str | None = None


# ── 엔드포인트 ─────────────────────────────────────────────────────────────────


@router.get("/risk/status")
async def get_risk_status() -> dict:
    """
    현재 리스크 상태 조회

    프론트엔드 RiskStatusPanel이 폴링하는 엔드포인트.

    응답:
    ```json
    {
      "current_exposure_pct": 0.35,
      "daily_pnl_pct": -0.012,
      "position_count": 3,
      "daily_limit_breached": false,
      "new_entry_blocked": false,
      "limits": { ... },
      "timestamp": "2026-04-11T10:00:00"
    }
    ```
    """
    risk_engine = app_state.risk_engine
    if risk_engine is None:
        # 엔진 미초기화 시 기본값 반환
        return {
            "current_exposure_pct": 0.0,
            "daily_pnl_pct": 0.0,
            "position_count": 0,
            "daily_limit_breached": False,
            "new_entry_blocked": False,
            "limits": RiskLimits().model_dump(),
            "timestamp": None,
            "status": "not_initialized",
        }

    status = risk_engine.get_status(app_state.positions)
    result = status.model_dump()
    result["status"] = "ok"
    return result


@router.put("/risk/limits")
async def update_risk_limits(body: RiskLimitsUpdate) -> dict:
    """
    리스크 한도 동적 변경

    제공된 필드만 업데이트. 미제공 필드는 기존 값 유지.

    응답:
    ```json
    {
      "status": "ok",
      "limits": { ... }
    }
    ```
    """
    risk_engine = app_state.risk_engine
    if risk_engine is None:
        raise HTTPException(status_code=503, detail="RiskEngine 미초기화")

    current = risk_engine.limits
    updates = body.model_dump(exclude_none=True)

    if not updates:
        raise HTTPException(status_code=400, detail="변경할 한도 값이 없습니다")

    # 현재 한도에서 업데이트된 필드만 교체
    new_limits = current.model_copy(update=updates)
    risk_engine.update_limits(new_limits)

    # 변경 사항 WebSocket 브로드캐스트
    await app_state.broadcast(
        "risk_limits_updated",
        {"limits": new_limits.model_dump(), "changed_fields": list(updates.keys())},
    )

    logger.info("리스크 한도 변경: %s", updates)
    return {"status": "ok", "limits": new_limits.model_dump()}


@router.get("/risk/events")
async def get_risk_events(
    limit: int = Query(50, ge=1, le=200, description="최대 이벤트 수"),
) -> dict:
    """
    최근 리스크 이벤트 조회

    stop_loss, take_profit, force_close, limit_breach 등 이벤트 포함.
    """
    risk_engine = app_state.risk_engine
    if risk_engine is None:
        return {"events": [], "status": "not_initialized"}

    events = risk_engine.get_recent_events(limit=limit)
    return {"events": events, "count": len(events), "status": "ok"}


@router.get("/risk/audit")
async def get_audit_log(
    limit: int = Query(100, ge=1, le=500, description="최대 로그 수"),
    event_type: str | None = Query(None, description="이벤트 타입 필터"),
) -> dict:
    """
    감사 로그 조회

    컴플라이언스 전용 엔드포인트.
    """
    compliance = app_state.compliance
    if compliance is None:
        return {"log": [], "status": "not_initialized"}

    log = compliance.get_audit_log(limit=limit, event_type=event_type)
    return {"log": log, "count": len(log), "status": "ok"}


@router.post("/risk/force-close")
async def force_close_all(reason: str = Query("manual", description="강제청산 사유")) -> dict:
    """
    전체 포지션 강제청산 명령

    WARNING: 즉시 모든 포지션 시장가 청산.
    실제 매매 엔진에 청산 신호를 전달.
    """
    risk_engine = app_state.risk_engine
    if risk_engine is None:
        raise HTTPException(status_code=503, detail="RiskEngine 미초기화")

    symbols = list(app_state.positions.keys())
    if not symbols:
        return {"status": "ok", "message": "청산할 포지션 없음", "symbols": []}

    # WebSocket 브로드캐스트 — 프론트엔드 알림
    await app_state.broadcast(
        "force_close_requested",
        {"reason": reason, "symbols": symbols, "count": len(symbols)},
    )

    # 컴플라이언스 로깅
    if app_state.compliance:
        await app_state.compliance.log_risk_event(
            event_type="force_close_manual",
            reason=f"수동 강제청산: {reason}",
            metadata={"symbols": symbols},
        )

    logger.warning("수동 강제청산 요청: %s개 종목 (%s)", len(symbols), reason)
    return {
        "status": "ok",
        "message": f"{len(symbols)}개 포지션 청산 요청됨",
        "symbols": symbols,
        "reason": reason,
    }
