"""
BarroAiTrade Backend — FastAPI 앱 진입점
"""
from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.websocket import websocket_endpoint
from backend.core.monitoring.logger import setup_logging

setup_logging(json_format=os.getenv("LOG_JSON", "false").lower() == "true")

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from backend.core.monitoring.telegram_bot import telegram
    from backend.core.risk.risk_engine import RiskEngine
    from backend.core.risk.compliance import ComplianceService
    from backend.core.state import app_state
    from backend.models.risk import RiskLimits

    _log.info("BarroAiTrade 백엔드 시작")

    # DB 초기화
    try:
        from backend.db.database import init_db
        await init_db()
        _log.info("DB 초기화 완료")
    except Exception as e:
        _log.warning("DB 초기화 실패 (인메모리 모드로 동작): %s", e)

    # RiskEngine 및 ComplianceService 초기화
    app_state.risk_engine = RiskEngine(limits=RiskLimits())
    app_state.compliance = ComplianceService()
    _log.info("RiskEngine 초기화 완료 (기본 한도)")

    mode = os.getenv("TRADING_MODE", "simulation")
    market = os.getenv("TRADING_MARKET", "stock")
    await telegram.notify_system_start(mode, market)

    # 일일 리포트 스케줄러 시작 (매일 09:00 KST)
    try:
        from scripts.finance.telegram_integration.scheduler import start_scheduler, stop_scheduler
        start_scheduler()
        _log.info("일일 리포트 스케줄러 시작 완료")
    except Exception as e:
        _log.warning("스케줄러 시작 실패 (선택적 기능): %s", e)
        stop_scheduler = lambda: None  # noqa: E731

    yield  # 서버 실행 중

    stop_scheduler()


app = FastAPI(
    title="BarroAiTrade API",
    version="0.1.0",
    description="AI 기반 멀티마켓 자동매매 플랫폼",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REST 라우터 등록 ─────────────────────────────────────────────────────────
from backend.api.routes.signals import router as signals_router
from backend.api.routes.risk import router as risk_router
from backend.api.routes.market import router as market_router
from backend.api.routes.trading import router as trading_router
from backend.api.routes.positions import router as positions_router
from backend.api.routes.watchlist import router as watchlist_router
from backend.api.routes.config import router as config_router
from backend.api.routes.reports import router as reports_router
from backend.api.routes.metrics import router as metrics_router  # BAR-43
from backend.api.routes.logs import router as logs_router

app.include_router(signals_router, prefix="/api")
app.include_router(risk_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(trading_router, prefix="/api")
app.include_router(positions_router, prefix="/api")
app.include_router(watchlist_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(reports_router, prefix="/api")
app.include_router(logs_router, prefix="/api")
# BAR-43: /metrics (Prometheus exposition) — 단, prefix 없음 (Prometheus 표준 경로)
app.include_router(metrics_router)

# ── WebSocket ────────────────────────────────────────────────────────────────
app.add_api_websocket_route("/ws/realtime", websocket_endpoint)


@app.get("/api/status")
async def health():
    from backend.core.state import app_state
    return app_state.to_system_status()


