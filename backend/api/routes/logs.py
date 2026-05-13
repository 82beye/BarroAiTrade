"""로그 파일 상태 조회 API — 모니터링 대시보드용."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["monitoring"])

_LOG_ROOT = Path(__file__).resolve().parents[3] / "logs"

# 로그별 정상 기준: 마지막 수정 후 허용 경과 시간(초)
_LOG_CONFIGS = {
    "morning":  {"file": "morning.log",  "label": "매수 로그",  "stale_sec": 3600},
    "eval":     {"file": "eval.log",     "label": "평가 로그",  "stale_sec": 3600},
    "closing":  {"file": "closing.log",  "label": "청산 로그",  "stale_sec": 86400},
    "launchd":  {"file": "launchd.log",  "label": "서버 로그",  "stale_sec": 300},
    "report":   {"file": "report.log",   "label": "리포트 로그", "stale_sec": 86400},
}


class LogFileStatus(BaseModel):
    key: str
    label: str
    file: str
    exists: bool
    healthy: bool
    size_bytes: int
    last_modified: float   # unix timestamp
    age_sec: int           # 마지막 수정 후 경과 초
    last_line: str


class LogStatusResponse(BaseModel):
    logs: list[LogFileStatus]
    timestamp: float


@router.get("/logs/status", response_model=LogStatusResponse)
async def logs_status() -> LogStatusResponse:
    now = time.time()
    results: list[LogFileStatus] = []

    for key, cfg in _LOG_CONFIGS.items():
        path = _LOG_ROOT / cfg["file"]
        if not path.exists():
            results.append(LogFileStatus(
                key=key, label=cfg["label"], file=cfg["file"],
                exists=False, healthy=False,
                size_bytes=0, last_modified=0, age_sec=999999,
                last_line="파일 없음",
            ))
            continue

        stat = path.stat()
        age_sec = int(now - stat.st_mtime)
        healthy = age_sec <= cfg["stale_sec"]

        # 마지막 줄 읽기 (최대 512바이트)
        last_line = ""
        try:
            with open(path, "rb") as f:
                f.seek(max(0, stat.st_size - 512))
                tail = f.read().decode("utf-8", errors="replace")
                lines = [l.strip() for l in tail.splitlines() if l.strip()]
                last_line = lines[-1] if lines else ""
        except Exception:
            last_line = ""

        results.append(LogFileStatus(
            key=key, label=cfg["label"], file=cfg["file"],
            exists=True, healthy=healthy,
            size_bytes=stat.st_size,
            last_modified=stat.st_mtime,
            age_sec=age_sec,
            last_line=last_line[:120],
        ))

    return LogStatusResponse(logs=results, timestamp=now)
