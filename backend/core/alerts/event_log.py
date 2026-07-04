"""알림 이벤트 로그 — JSONL append/read 순수 유틸 (티마 앱 벤치마킹 P1).

파일: data/alert_events.jsonl (한 줄 = 알림 이벤트 1건).

운영 데몬·스캐너가 나중에 `append_alert_event(...)` 를 호출해 전략 시그널
(예: `[SF존] SF존 위메이드 B1도달`)을 적재하고, 대시보드 알림내역 화면이
`read_alert_events(...)` 로 조회한다. IO 외 부수효과 없는 순수 함수로 구성.

⚠️ 파일이 없으면 read 는 빈 리스트를 반환한다 (no_data graceful).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# 알림 설정에서 지원하는 전략 키 (환경설정 Push 토글 4종 — PRD §4.5)
ALERT_STRATEGIES = ("f_zone", "sf_zone", "gold_zone", "swing_38")

# 전략 키 → 표시 라벨 (스크리너 탭과 동일 — screener.py _STRATEGY_TABS).
# 데몬은 supertrend 신호도 낼 수 있어 라벨만 추가로 매핑(미지원 키는 그대로 표기).
STRATEGY_LABELS = {
    "f_zone": "F존",
    "sf_zone": "SF존",
    "gold_zone": "골드존",
    "swing_38": "38스윙",
    "supertrend": "슈퍼트렌드",
}


def strategy_label(strategy: str) -> str:
    """전략 키 → 표시 라벨 (미등록 키는 원문 그대로)."""
    return STRATEGY_LABELS.get(strategy, strategy or "")


def _data_dir() -> Path:
    """repo_root/data (event_log.py → parents[3] == repo root)."""
    return Path(__file__).resolve().parents[3] / "data"


def _events_path() -> Path:
    return _data_dir() / "alert_events.jsonl"


def append_alert_event(
    strategy: str,
    symbol: str,
    name: Optional[str] = None,
    message: str = "",
    level_label: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> dict:
    """알림 이벤트 1건을 data/alert_events.jsonl 에 append 한다.

    Args:
        strategy: 전략 키 ("f_zone" | "sf_zone" | "gold_zone" | "swing_38").
        symbol: 종목 코드.
        name: 종목명 (없으면 symbol 로 대체).
        message: 표시 메시지 (예: "[SF존] SF존 위메이드 B1도달").
        level_label: 도달 기준선 라벨 (B1/G2/J3 …). 없으면 None.
        occurred_at: 발생 시각 ISO8601. 미지정 시 현재 UTC.

    Returns:
        적재된 이벤트 dict (occurred_at 채워진 최종본).
    """
    event = {
        "strategy": strategy,
        "symbol": symbol,
        "name": name or symbol,
        "message": message,
        "level_label": level_label,
        "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
    }
    path = _events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_alert_events(
    strategy: Optional[str] = None,
    limit: int = 100,
    since: Optional[str] = None,
) -> List[dict]:
    """알림 이벤트를 최신순으로 조회한다 (파일 없으면 빈 리스트).

    Args:
        strategy: 지정 시 해당 전략만 필터 (None = 전체).
        limit: 최대 반환 개수.
        since: ISO8601 — 이 시각 이후(초과) 이벤트만 (파싱 실패 시 무시).

    Returns:
        [{id, strategy, symbol, name, message, level_label, occurred_at}, ...]
        최신(occurred_at 큰 값)순. id 는 파일 행번호(1-based).
    """
    path = _events_path()
    if not path.exists():
        return []

    since_dt = _parse_dt(since) if since else None

    events: List[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("alert_events.jsonl 파싱 실패 (line %d)", lineno)
                continue
            if strategy is not None and raw.get("strategy") != strategy:
                continue
            if since_dt is not None:
                occ = _parse_dt(raw.get("occurred_at"))
                if occ is not None and occ <= since_dt:
                    continue
            raw["id"] = lineno
            events.append(raw)

    # 최신순 (occurred_at desc, 동률은 행번호 desc)
    events.sort(
        key=lambda e: (e.get("occurred_at") or "", e.get("id", 0)), reverse=True
    )
    if limit is not None and limit >= 0:
        events = events[:limit]
    return events


def record_signal_capture_events(
    signals,
    occurred_at: Optional[str] = None,
) -> List[dict]:
    """정제 시그널 목록에 대해 '포착' 알림 이벤트를 append 한다.

    운영 데몬(scripts/intraday_buy_daemon.py)이 refined_signals.json 저장 직후
    호출한다. 시그널 1건 → "[전략라벨] 종목명 포착" 메시지로 append_alert_event.

    ⚠️ 기준선(B1/B2/B3 등) '도달' 이벤트는 실시간 가격 감시가 필요해 이번 범위 밖 —
    여기서는 '포착' 이벤트만 기록한다(level_label=None).

    Args:
        signals: dict 반복자. 각 항목은 {"strategy", "symbol", "name"} 키 사용
                 (name 없으면 symbol 로 대체, symbol 없으면 건너뜀).
        occurred_at: 공통 발생 시각 ISO8601. 미지정 시 각 이벤트가 현재 UTC.

    Returns:
        append 된 이벤트 dict 리스트. 개별 시그널 실패는 건너뛰고 로깅만 한다
        (호출측 메인 흐름 무영향).
    """
    written: List[dict] = []
    for sig in signals or []:
        try:
            symbol = (sig.get("symbol") or "").strip()
            if not symbol:
                continue
            strategy = sig.get("strategy") or ""
            name = sig.get("name") or symbol
            label = strategy_label(strategy)
            event = append_alert_event(
                strategy=strategy,
                symbol=symbol,
                name=name,
                message=f"[{label}] {name} 포착",
                level_label=None,
                occurred_at=occurred_at,
            )
            written.append(event)
        except Exception:
            logger.warning("알림 이벤트 기록 실패 (skip): %r", sig, exc_info=True)
    return written


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """ISO8601 문자열 → datetime (실패 시 None)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


__all__ = [
    "append_alert_event",
    "read_alert_events",
    "record_signal_capture_events",
    "strategy_label",
    "ALERT_STRATEGIES",
    "STRATEGY_LABELS",
]
