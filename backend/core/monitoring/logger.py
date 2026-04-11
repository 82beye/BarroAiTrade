"""
중앙집중식 로깅 설정 — JSON 구조화 로그 + 파일 로테이션

사용:
  from backend.core.monitoring.logger import setup_logging
  setup_logging()
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))


class _JsonFormatter(logging.Formatter):
    """JSON 형식 로그 포매터"""

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime, timezone

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(json_format: bool = False) -> None:
    """로깅 초기화 — 호출은 main.py startup에서 1회"""

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = []

    # 콘솔 핸들러
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        _JsonFormatter() if json_format
        else logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    handlers.append(console)

    # 파일 핸들러 (일별 로테이션, 7일 보관)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "barro.log",
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(_JsonFormatter())
    handlers.append(file_handler)

    # 매매 이벤트 전용 로그
    trade_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "trades.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    trade_handler.setFormatter(_JsonFormatter())
    trade_handler.addFilter(lambda r: r.name.startswith("barro.trade"))
    handlers.append(trade_handler)

    logging.basicConfig(
        level=LOG_LEVEL,
        handlers=handlers,
        force=True,
    )

    # 서드파티 노이즈 억제
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("로깅 초기화 완료 (level=%s)", LOG_LEVEL)


def get_trade_logger() -> logging.Logger:
    """매매 이벤트 전용 로거"""
    return logging.getLogger("barro.trade")
