"""개장 전 Telegram 브리핑의 포맷·산출물·중복 방지 공통 로직.

브리핑 데이터(전일 일봉 스캔, 일봉 기반 멀티에이전트 예측, 누적 체결 분석)는
장중 5분마다 다시 계산해도 의미 있게 바뀌지 않는다. 따라서 스케줄은 거래일 08:25
1회이며, 실패했을 때만 호출부가 5분 간격으로 제한 재시도한다.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable, Mapping

KST = timezone(timedelta(hours=9))
DELIVERY_KEYS = ("scan", "prediction", "strategy")


def _value(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _html(value: Any) -> str:
    return escape(str(value), quote=False)


def format_scan_message(
    watchlist: Iterable[Any], *, generated_at: datetime | None = None,
    display_limit: int = 10,
) -> str:
    """첨부 예시와 같은 종목 스캔 Telegram HTML 메시지."""
    rows = list(watchlist)
    now = (generated_at or datetime.now(KST)).astimezone(KST).strftime("%H:%M")
    lines = [
        f"📊 <b>종목 스캔 완료</b> ({now})",
        f"감시 종목: {len(rows)}개",
        "",
    ]
    for idx, stock in enumerate(rows[:display_limit], start=1):
        watermelon = "🍉 " if bool(_value(stock, "watermelon_signal", False)) else ""
        lines.append(
            f"{idx}. {watermelon}[{_html(_value(stock, 'code'))}] "
            f"{_html(_value(stock, 'name'))} | "
            f"{_html(_value(stock, 'blue_line_status'))} | "
            f"점수:{_html(_value(stock, 'score'))}"
        )
    remaining = len(rows) - display_limit
    if remaining > 0:
        lines.extend(["", f"... 외 {remaining}종목"])
    return "\n".join(lines)


_PRED_AGENT_ORDER = (
    ("momentum", "MOM"),
    ("volume", "VOL"),
    ("technical", "TEC"),
    ("breakout", "BRE"),
    ("timing", "TIM"),
)


def format_prediction_message(
    results: Iterable[Any], *, generated_at: datetime | None = None,
) -> str:
    """첨부 예시와 같은 팀 에이전트 상승 예측 Telegram HTML 메시지."""
    rows = list(results)
    now = (generated_at or datetime.now(KST)).astimezone(KST).strftime("%H:%M")
    lines = [
        f"<b>팀 에이전트 상승 예측</b> ({now})",
        f"예측 종목: {len(rows)}개",
        "",
    ]

    counts: dict[str, int] = {}
    for row in rows:
        label = str(_value(row, "consensus_level", ""))
        counts[label] = counts.get(label, 0) + 1
    lines.extend([" | ".join(f"{_html(k)}:{v}" for k, v in counts.items()), ""])

    for fallback_rank, row in enumerate(rows, start=1):
        scores = _value(row, "agent_scores", {}) or {}
        # 첨부의 [OOOO]는 timing을 제외한 진입판정 4개 에이전트를 나타낸다.
        agent_bar = "".join(
            "O" if name in scores else "-"
            for name in ("momentum", "volume", "technical", "breakout")
        )
        rank = _value(row, "rank", fallback_rank) or fallback_rank
        total_score = float(_value(row, "total_score", 0) or 0)
        confidence = float(_value(row, "confidence", 0) or 0)
        lines.append(
            f"{rank}. [{_html(_value(row, 'code'))}] {_html(_value(row, 'name'))} "
            f"<b>{total_score:.1f}점</b> [{agent_bar}] "
            f"{_html(_value(row, 'consensus_level'))}\n"
            f"   신뢰도:{confidence:.0%}"
        )
        score_parts = [
            f"{short}:{float(scores[name]):.0f}"
            for name, short in _PRED_AGENT_ORDER if name in scores
        ]
        if score_parts:
            lines.append(f"   {' | '.join(score_parts)}")
        for reason in list(_value(row, "top_reasons", []) or [])[:2]:
            lines.append(f"   {_html(reason)}")
    return "\n".join(lines)


def format_strategy_message(params: Any) -> str:
    """첨부 예시와 같은 전략 최적화 팀 Telegram HTML 메시지."""
    lines = [
        "<b>전략 최적화 팀 분석 결과</b>",
        f"종합 신뢰도: {float(_value(params, 'confidence', 0) or 0):.0%}",
        "",
        "<b>당일 파라미터</b>",
        f"  진입 시작: 09:{int(_value(params, 'entry_start_delay_minutes', 5)):02d}",
        f"  쿨다운: {int(_value(params, 'cooldown_minutes', 10))}분",
        f"  종목당 한도: {int(_value(params, 'max_entries_per_stock', 3))}회",
        f"  BB 과열: +{float(_value(params, 'max_bb_excess_pct', 8)):.1f}%",
        f"  돌파 상한: +{float(_value(params, 'max_breakout_pct', 7)):.1f}%",
        f"  손절: {_value(params, 'stop_loss_pct', -3.5)}%",
        f"  익절: +{_value(params, 'take_profit_1_pct', 5)}% / "
        f"+{_value(params, 'take_profit_2_pct', 8)}%",
        f"  BE 스톱: +{float(_value(params, 'breakeven_buffer_pct', .3)):.1f}%",
        f"  포지션 배율: {float(_value(params, 'position_size_multiplier', 1)):.0%}",
    ]
    blacklist = list(_value(params, "blacklist_codes", []) or [])
    if blacklist:
        lines.append(f"\n<b>블랙리스트</b>: {', '.join(_html(x) for x in blacklist)}")

    boosts = dict(_value(params, "stock_boost", {}) or {})
    if boosts:
        text = ", ".join(
            f"{_html(code)}(+{float(value):.0%})"
            for code, value in sorted(boosts.items(), key=lambda item: item[1], reverse=True)[:5]
        )
        lines.append(f"\n<b>부스트 종목</b>: {text}")

    penalties = dict(_value(params, "stock_penalty", {}) or {})
    if penalties:
        text = ", ".join(
            f"{_html(code)}(-{float(value):.0%})"
            for code, value in sorted(penalties.items(), key=lambda item: item[1], reverse=True)[:5]
        )
        lines.append(f"\n<b>페널티 종목</b>: {text}")

    reports = list(_value(params, "agent_reports", []) or [])
    if reports:
        lines.append("\n<b>에이전트 상세</b>")
        lines.extend(f"  {_html(report)}" for report in reports[:10])
    return "\n".join(lines)


def _normalize_json(value: Any) -> Any:
    """dataclass·numpy scalar·datetime을 표준 JSON 값으로 정규화."""
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _normalize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_json(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    # numpy scalar는 item()으로 Python 기본형을 반환한다.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _normalize_json(item())
        except (TypeError, ValueError):
            pass
    return value


def _jsonable(obj: Any) -> dict[str, Any]:
    if is_dataclass(obj):
        raw = asdict(obj)
    elif isinstance(obj, Mapping):
        raw = dict(obj)
    else:
        raw = dict(vars(obj))
    return _normalize_json(raw)


def build_watchlist_payload(
    watchlist: Iterable[Any], *, day: date, generated_at: datetime,
) -> dict[str, Any]:
    rows = [_jsonable(row) for row in watchlist]
    return {
        "date": day.isoformat(),
        "generated_at": generated_at.astimezone(KST).isoformat(),
        "count": len(rows),
        "stocks": rows,
    }


def build_predictions_payload(
    predictions: Iterable[Any], *, day: date, generated_at: datetime,
) -> dict[str, Any]:
    rows = [_jsonable(row) for row in predictions]
    return {
        "date": day.isoformat(),
        "generated_at": generated_at.astimezone(KST).isoformat(),
        "count": len(rows),
        "stocks": rows,
    }


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(_normalize_json(payload), handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


@dataclass(frozen=True)
class CacheReadiness:
    ready: bool
    reason: str
    file_count: int
    universe_count: int
    updated: str


def inspect_cache_readiness(
    cache_dir: Path, universe_count: int, *, today: date,
    minimum_coverage: float = 0.85, maximum_calendar_age_days: int = 7,
) -> CacheReadiness:
    """전종목 캐시가 실제 브리핑을 만들 만큼 채워졌는지 확인한다."""
    files = [p for p in cache_dir.glob("*.json") if p.name != "meta.json"]
    meta_path = cache_dir / "meta.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
        updated = str(meta.get("updated", ""))
        updated_day = date.fromisoformat(updated)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
        return CacheReadiness(False, "cache_meta_invalid", len(files), universe_count, "")

    required = max(1, int(universe_count * minimum_coverage))
    requested = int(meta.get("total_requested", 0) or 0)
    if len(files) < required or requested < required:
        return CacheReadiness(
            False, f"cache_coverage:{len(files)}/{universe_count}:requested={requested}",
            len(files), universe_count, updated,
        )
    age = (today - updated_day).days
    if age < 0 or age > maximum_calendar_age_days:
        return CacheReadiness(
            False, f"cache_age:{age}d", len(files), universe_count, updated,
        )
    return CacheReadiness(True, "", len(files), universe_count, updated)


class DeliveryState:
    """날짜·논리 메시지 단위의 Telegram 중복 방지 상태."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self, day: date) -> dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        if data.get("date") != day.isoformat():
            return {"date": day.isoformat(), "sent": []}
        sent = data.get("sent")
        data["sent"] = sent if isinstance(sent, list) else []
        return data

    def was_sent(self, day: date, key: str) -> bool:
        return key in self._load(day)["sent"]

    def mark_sent(self, day: date, key: str, *, at: datetime) -> None:
        data = self._load(day)
        if key not in data["sent"]:
            data["sent"].append(key)
        data["updated_at"] = at.astimezone(KST).isoformat()
        write_json_atomic(self.path, data)


def split_message(text: str, limit: int = 3900) -> list[str]:
    """Telegram 한도 안에서 줄 단위로 분할한다."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = line
    if current:
        chunks.append(current)
    return chunks


async def deliver_once(
    notifier: Any, messages: Mapping[str, str], *, day: date,
    state: DeliveryState, force: bool = False, now: datetime | None = None,
) -> dict[str, list[str]]:
    """세 논리 메시지를 순서대로 발송하고 성공 블록만 즉시 기록한다."""
    sent: list[str] = []
    skipped: list[str] = []
    at = now or datetime.now(KST)
    for key in DELIVERY_KEYS:
        text = messages.get(key, "").strip()
        if not text:
            raise ValueError(f"empty briefing block: {key}")
        if not force and state.was_sent(day, key):
            skipped.append(key)
            continue
        chunks = split_message(text)
        for index, chunk in enumerate(chunks, start=1):
            if len(chunks) > 1:
                chunk = f"({index}/{len(chunks)})\n{chunk}"
            await notifier.send(chunk)
        state.mark_sent(day, key, at=at)
        sent.append(key)
    return {"sent": sent, "skipped": skipped}


__all__ = [
    "KST", "DELIVERY_KEYS", "CacheReadiness", "DeliveryState",
    "format_scan_message", "format_prediction_message", "format_strategy_message",
    "build_watchlist_payload", "build_predictions_payload", "write_json_atomic",
    "inspect_cache_readiness", "split_message", "deliver_once",
]
