"""
BarroAiTrade Backend — FastAPI 앱 진입점
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.websocket import websocket_endpoint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="BarroAiTrade API",
    version="0.1.0",
    description="AI 기반 멀티마켓 자동매매 플랫폼",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REST 라우터 등록 ─────────────────────────────────────────────────────────
# TODO: Backend Engineer가 각 라우터 구현 후 등록
# from backend.api.routes.trading import router as trading_router
# from backend.api.routes.positions import router as positions_router
# from backend.api.routes.watchlist import router as watchlist_router
# from backend.api.routes.market import router as market_router
# from backend.api.routes.reports import router as reports_router
# from backend.api.routes.config import router as config_router
# from backend.api.routes.risk import router as risk_router
#
# app.include_router(trading_router, prefix="/api")
# app.include_router(positions_router, prefix="/api")
# ... 등

# ── WebSocket ────────────────────────────────────────────────────────────────
app.add_api_websocket_route("/ws/realtime", websocket_endpoint)


@app.get("/api/status")
async def health():
    from backend.core.state import app_state
    return app_state.to_system_status()


@app.on_event("startup")
async def startup() -> None:
    logging.getLogger(__name__).info("BarroAiTrade 백엔드 시작")
