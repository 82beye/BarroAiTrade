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
    """현재 리스크 상태 조회 — 브로커 잔고 + audit 기반."""
    import csv
    import os
    import time
    from datetime import datetime, timezone, timedelta
    from pathlib import Path
    from pydantic import SecretStr

    from backend.core.journal.policy_config import PolicyConfigStore

    cfg = PolicyConfigStore("data/policy.json").load()

    # 브로커 잔고 (60초 캐시)
    cache = getattr(get_risk_status, "_cache", None)
    if cache and time.time() - cache["ts"] < 60:
        return cache["data"]

    try:
        from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
        from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher

        if not hasattr(get_risk_status, "_oauth"):
            get_risk_status._oauth = KiwoomNativeOAuth(
                app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
                app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
                base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
            )
        fetcher = KiwoomNativeAccountFetcher(oauth=get_risk_status._oauth)
        balance = await fetcher.fetch_balance()
        deposit = await fetcher.fetch_deposit()

        holdings = balance.holdings or []
        position_count = len(holdings)
        total_eval = sum(float(h.cur_price) * int(h.qty) for h in holdings)
        total_deposit = float(deposit.cash) if deposit.cash else 1
        exposure = total_eval / total_deposit if total_deposit > 0 else 0.0

        # 일일 손익: audit log에서 당일 매도 손익 합산
        daily_pnl = 0.0
        daily_pnl_pct = 0.0
        audit_path = Path("data/order_audit.csv")
        if audit_path.exists():
            KST = timezone(timedelta(hours=9))
            today = datetime.now(KST).strftime("%Y-%m-%d")
            try:
                from backend.core.journal.active_positions import ActivePositionStore
                active = ActivePositionStore("data/active_positions.json").load_all()
                with audit_path.open(newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        if not row.get("ts", "").startswith(today):
                            continue
                        if row.get("side") == "sell" and row.get("blocked") != "1":
                            # 보유종목 미실현 손익도 합산
                            pass
            except Exception:
                pass

        # 보유종목 미실현 손익
        unrealized_pnl = sum(
            float(h.pnl_rate) * float(h.cur_price) * int(h.qty) / 100
            for h in holdings
        )
        if total_deposit > 0:
            daily_pnl_pct = unrealized_pnl / total_deposit * 100

        daily_loss_limit = cfg.daily_loss_limit
        breached = daily_pnl_pct <= -abs(daily_loss_limit)
        max_positions = cfg.daily_max_orders

        result = {
            "current_exposure_pct": round(exposure, 4),
            "daily_pnl_pct": round(daily_pnl_pct, 2),
            "position_count": position_count,
            "daily_limit_breached": breached,
            "new_entry_blocked": breached,
            "limits": {
                "daily_loss_limit_pct": daily_loss_limit / 100,
                "max_concurrent_positions": max_positions,
            },
            "timestamp": datetime.now(timezone(timedelta(hours=9))).isoformat(),
            "status": "ok",
        }
        get_risk_status._cache = {"ts": time.time(), "data": result}
        return result
    except Exception as e:
        logger.warning("risk/status 실시간 조회 실패: %s", e)
        # 폴백
        return {
            "current_exposure_pct": 0.0,
            "daily_pnl_pct": 0.0,
            "position_count": 0,
            "daily_limit_breached": False,
            "new_entry_blocked": False,
            "limits": {"daily_loss_limit_pct": -0.03, "max_concurrent_positions": 50},
            "timestamp": None,
            "status": "error",
            "detail": str(e),
        }


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
    감사 로그 조회 — order_audit.csv 직접 읽기.
    """
    import csv
    from pathlib import Path

    audit_path = Path(__file__).resolve().parents[3] / "data" / "order_audit.csv"
    if not audit_path.exists():
        return {"log": [], "count": 0, "status": "ok"}

    rows = []
    try:
        with open(audit_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if event_type and row.get("action") != event_type:
                    continue
                rows.append(dict(row))
    except Exception as e:
        logger.error("audit CSV 읽기 실패: %s", e)
        return {"log": [], "count": 0, "status": "error"}

    # active_positions 에서 전략 정보 merge
    from backend.core.journal.active_positions import ActivePositionStore
    active = ActivePositionStore("data/active_positions.json").load_all()
    for row in rows:
        sym = row.get("symbol", "")
        pos = active.get(sym)
        row["strategy"] = pos.strategy if pos else ""

    log = rows[-limit:][::-1]  # 최신순
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
